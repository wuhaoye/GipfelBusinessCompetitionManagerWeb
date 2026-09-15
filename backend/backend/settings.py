"""
Django 设置模块。

环境变量启动校验（fail-fast）：JWT_SECRET 未配置时直接抛异常拒绝启动，
与原 NestJS main.ts 的 zod 校验保持一致。
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# 加载 .env（与原 NestJS dotenv/config 等价）
load_dotenv()


# ==================== 路径常量 ====================
BASE_DIR = Path(__file__).resolve().parent.parent

# ==================== 环境变量启动校验（fail-fast） ====================
# 对应原 server/src/main.ts 顶部的 zod 校验：JWT_SECRET 必填
JWT_SECRET = os.environ.get("JWT_SECRET", "").strip()
if not JWT_SECRET:
    raise RuntimeError(
        "环境变量校验失败:\n  JWT_SECRET: JWT_SECRET is required\n"
        "  请在 .env 中配置强随机值，如：openssl rand -hex 32"
    )

# 已知弱密钥黑名单（防止部署时忘记修改）
_WEAK_JWT_SECRETS = {
    "your-dev-secret-change-in-production",
    "secret",
    "jwt-secret",
    "change-me",
    "password",
    "123456",
}
if JWT_SECRET.lower() in _WEAK_JWT_SECRETS:
    raise RuntimeError(
        "环境变量校验失败:\n  JWT_SECRET: 检测到已知弱密钥，请使用强随机值\n"
        "  生成命令：openssl rand -hex 32"
    )

# 日志查看器防直连令牌共享密钥：主后端用它签发一次性令牌，日志查看器用它校验。
# 与主后端共用同一 .env；缺失时回退 JWT_SECRET（保证两服务能协商一致即可）。
LOGVIEWER_SECRET_KEY = (os.environ.get("LOGVIEWER_SECRET_KEY") or JWT_SECRET).strip()

# 后端管理后台防直连网关：一次性令牌有效期（秒）。与日志查看器网关同构，仅作用 /admin/ 入口。
# 令牌由主后端用 LOGVIEWER_SECRET_KEY + salt="backend-gate" 签发，BackendGateMiddleware 用同密钥同盐校验。
BACKEND_GATE_MAX_AGE = int(os.environ.get("BACKEND_GATE_MAX_AGE", "120"))


# ==================== 通用配置 ====================
# Django 自身 SECRET_KEY：优先读 DJANGO_SECRET_KEY（生产建议配置独立值，与 JWT 密钥分离，
# 缩小单密钥泄露的影响面）；未配置时回退 JWT_SECRET（迁移期兼容，行为与旧版一致）。
# 注意：更换 SECRET_KEY 会使现有 session / CSRF cookie 失效（用户需重新登录），换钥需择机进行。
_django_secret = (os.environ.get("DJANGO_SECRET_KEY") or "").strip()
if not _django_secret:
    import logging
    logging.getLogger("gipfel").warning(
        "DJANGO_SECRET_KEY 未配置，回退使用 JWT_SECRET（生产环境建议独立配置以缩小密钥泄露影响面）"
    )
SECRET_KEY = _django_secret or JWT_SECRET

DEBUG = os.environ.get("DEBUG", "false").lower() == "true"

# 生产环境 DEBUG 模式警告（在 ALLOWED_HOSTS 解析后检查）
_debug_warn_logged = False


def _resolve_allowed_hosts() -> list:
    """Django ALLOWED_HOSTS：默认回环地址，可通过 DJANGO_ALLOWED_HOSTS 追加公网域名/IP。

    生产必须显式配置 DJANGO_ALLOWED_HOSTS（逗号分隔），否则仅回环可达，公网 Host 会被拒（400）。
    另自动纳入 LOG_VIEWER_PUBLIC_URL 的主机（若已配置日志查看器公网地址）。
    收紧后可消除 Host 投毒（VersionView 的 log_viewer_url 不再反射任意 Host）。

    注意：urlparse(...).netloc 含端口（host:port），但 Django 的 get_host() 用
    split_domain_port 拆出 domain 再 validate_host，仅校验 domain 与 ALLOWED_HOSTS，
    端口被忽略——故这里剥掉端口，仅放 host。
    """
    hosts = ["127.0.0.1", "localhost", "::1"]
    extra = os.environ.get("DJANGO_ALLOWED_HOSTS", "").strip()
    if extra:
        hosts += [h.strip() for h in extra.split(",") if h.strip()]
    lv = os.environ.get("LOG_VIEWER_PUBLIC_URL", "").strip()
    if lv:
        from urllib.parse import urlparse

        p = urlparse(lv)
        if p.hostname and p.hostname not in hosts:
            hosts.append(p.hostname)
    return hosts


ALLOWED_HOSTS = _resolve_allowed_hosts()

# 生产环境 DEBUG 模式安全检查
if DEBUG:
    _non_localhost_hosts = [
        h for h in ALLOWED_HOSTS
        if h not in ("127.0.0.1", "localhost", "::1", "[::1]")
    ]
    if _non_localhost_hosts:
        import logging
        logging.getLogger("gipfel").warning(
            "⚠️  检测到 DEBUG=true 且 ALLOWED_HOSTS 包含非回环主机 %s ——"
            " 生产环境务必设置 DEBUG=false，否则会暴露详细错误信息和堆栈！",
            _non_localhost_hosts
        )

def _resolve_csrf_trusted_origins() -> list:
    """Django 4+ CSRF Origin 校验白名单（scheme://host[:port] 显式格式）。

    POST 请求带 Origin 头时必须命中本列表，否则 403（Origin checking failed）。
    实证场景：本地经前端 :5173 按钮跳转 /admin 后提交登录表单，浏览器携带的
    Origin 为 http://127.0.0.1:5173，未在白名单即被拒（403 禁止登录）。
    生产：DJANGO_CSRF_TRUSTED_ORIGINS（逗号分隔，如 https://your-domain.com）
    追加；并按 ALLOWED_HOSTS 的非回环主机自动派生 http/https origin。
    """
    origins = [
        "http://127.0.0.1:8000",
        "http://localhost:8000",
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ]
    for h in ALLOWED_HOSTS:
        if h in ("127.0.0.1", "localhost", "::1"):
            continue
        origins += [f"http://{h}", f"https://{h}"]
    extra = os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").strip()
    if extra:
        origins += [o.strip() for o in extra.split(",") if o.strip()]
    return origins


CSRF_TRUSTED_ORIGINS = _resolve_csrf_trusted_origins()

PORT = int(os.environ.get("PORT", "8000"))

# 日志查看器端口（与主后端共用同一 .env；默认 8120，经 /api/version 下发给前端「日志查看器」跳转按钮）
LOG_VIEWER_PORT = int(os.environ.get("LOG_VIEWER_PORT", "8120"))

# JWT 配置（与原 main.ts / jwt.strategy.ts 一致）
JWT_ISSUER = os.environ.get("JWT_ISSUER", "gipfel-competition")
JWT_AUDIENCE = os.environ.get("JWT_AUDIENCE", "gipfel-competition-client")
JWT_EXPIRES_IN = os.environ.get("JWT_EXPIRES_IN", "24h")
# 默认超级管理员（首次 migrate 自动写入；可经 .env 覆盖）
# · 业务超管：apps.users.User（前端 JWT 登录用，role=SUPER_ADMIN）
# · 后台超管：django.contrib.auth.User（/admin 登录用，is_staff/is_superuser）
# · 首次登录强制改密（must_change_password=true）
SEED_ADMIN_USERNAME = os.environ.get("SEED_ADMIN_USERNAME", "admin")
SEED_ADMIN_EMAIL = os.environ.get("SEED_ADMIN_EMAIL", "admin@example.com")
# 未配置时自动生成强随机密码（deploy-linux.sh 也会自动生成）
_raw_admin_pw = os.environ.get("SEED_ADMIN_PASSWORD", "").strip()
if not _raw_admin_pw:
    import secrets as _secrets
    _raw_admin_pw = _secrets.token_urlsafe(16)
    import logging
    logging.getLogger("gipfel").info(
        "SEED_ADMIN_PASSWORD 未配置，已自动生成随机密码（首次登录后强制修改）"
    )
SEED_ADMIN_PASSWORD = _raw_admin_pw

# 日志
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
LOG_DIR = Path(os.environ.get("LOG_DIR", "./logs"))
LOG_DIR.mkdir(parents=True, exist_ok=True)

# 上传
UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", "./uploads"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# CORS（未配置时仅本地/私网反射并带凭据，公网须白名单）
CORS_ORIGIN = os.environ.get("CORS_ORIGIN", "").strip()


# ==================== CORS 配置 ====================
# 对应原 main.ts 的 enableCors：本地/私网反射并带凭据，公网须白名单
def _resolve_cors_origins() -> list:
    if not CORS_ORIGIN:
        return []
    return [s.strip() for s in CORS_ORIGIN.split(",") if s.strip()]


# 显式白名单来源（来自 CORS_ORIGIN 环境变量）；命中时带凭据，否则仅反射不带凭据
CORS_ALLOWED_ORIGINS = _resolve_cors_origins()
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOW_ALL_ORIGINS = False


def cors_origin_validator(request_origin: str) -> bool:
    """自定义 CORS origin 校验器（在 apps.common.middleware 中接入）。

    返回 True 表示允许。
    """
    from apps.common.middleware import is_local_or_private_origin

    # 无 Origin：同源/本地调用，允许
    if not request_origin:
        return True
    origins = _resolve_cors_origins()
    allow_all = "*" in origins
    if origins and not allow_all:
        return request_origin in origins
    # 未配置或配置 *：仅本地/私网反射
    return is_local_or_private_origin(request_origin)


# 暴露为大写设置项，供 DynamicCorsMiddleware 通过 settings.CORS_ORIGIN_VALIDATOR 调用。
# Django 的 LazySettings 仅将「大写模块属性」识别为 settings 配置项，
# 模块级小写函数 cors_origin_validator 不会被自动暴露，必须通过大写别名显式导出。
CORS_ORIGIN_VALIDATOR = cors_origin_validator


# 用信号在 corsheaders 之前接入自定义校验：通过中间件自行处理 OPTIONS 与响应头
# （corsheaders 的 CORS_ORIGIN_WHITELIST 走静态判断，无法动态反射，故自定义中间件接管）


# ==================== 应用注册 ====================
INSTALLED_APPS = [
    "daphne",  # ASGI server（置于 django.contrib.staticfiles 之前以接管 runserver）
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # 第三方
    "rest_framework",
    "corsheaders",
    # 本项目
    "apps.common",
    "apps.users",
    "apps.auth",
    "apps.competitions",
    "apps.materials",
    "apps.parts",
    "apps.products",
    "apps.tech_tree",
    "apps.maps",
    "apps.infrastructures",
    "apps.fuels",
    "apps.vehicles",
    "apps.warehouses",
    "apps.production_lines",
    "apps.industry_types",
    "apps.companies",
    "apps.company_fields",
    "apps.contracts",
    "apps.regions",
    "apps.consumer_demands",
    "apps.messages",
    "apps.stock",
    "apps.files",
    "apps.realtime",
    "apps.audit",
    "apps.announcements",
    "apps.widget_packages",
    "apps.preparation",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",  # 必须在 Common 之前
    "apps.common.cors.DynamicCorsMiddleware",  # 自定义 CORS 反射（接管 corsheaders 动态判断）
    "apps.common.middleware.SecurityHeadersMiddleware",
    "apps.common.middleware.OperatorContextMiddleware",
    "apps.common.middleware.LoginRateLimitMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    # 后端管理后台防直连网关：仅 /admin/* 受控，缺失/无效令牌则 302 重定向回前端 SPA。
    # 必须位于 SessionMiddleware 之后，以便使用 request.session 写入网关标记。
    "apps.common.backend_gate.BackendGateMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "backend.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "backend.wsgi.application"
ASGI_APPLICATION = "backend.asgi.application"


# ==================== 数据库 ====================
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}


# ==================== 密码哈希 ====================
# 与原 bcryptjs cost=12 兼容（业务 users 表为自定义 User，自行用 bcrypt 校验 password_hash，
# 不走本列表，故下方顺序/增删不影响业务登录）。
#
# 注意：必须同时保留 Django 默认算法。/admin 后台使用 django.contrib.auth.User，
# 其密码由 createsuperuser / migrate 播种为 pbkdf2_sha256；若此处只留 bcrypt，
# identify_hasher() 会因找不到 pbkdf2 算法使 check_password 恒返回 False，
# 表现为「后台账号密码正确却永远登录失败」。保留默认算法不影响 bcrypt 已有的哈希。
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.BCryptSHA256PasswordHasher",
    "django.contrib.auth.hashers.BCryptPasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.ScryptPasswordHasher",
]


# ==================== DRF ====================
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "apps.auth.authentication.JWTAuthentication",  # 自定义 JWT（tokenVersion + issuer/audience）
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
        # 比赛域全局兜底：非超管的写操作必须有比赛上下文（body 的 competitionId
        # 或自身归属比赛）。显式设置 permission_classes 的视图不受影响；
        # 粗校验无副作用——各视图的 create_competition_id / apply_competition_scope
        # 仍负责精确的归属强制与 queryset 过滤（见 apps/common/guards.py）。
        "apps.common.guards.CompetitionScopePermission",
    ),
    "DEFAULT_PAGINATION_CLASS": None,  # 自定义分页（parsePagination 等价）
    "PAGE_SIZE": 50,
    "DEFAULT_RENDERER_CLASSES": (
        "apps.common.response.JSONRenderer",  # 统一 {code,message,data} 包装
    ),
    "EXCEPTION_HANDLER": "apps.common.exceptions.exception_handler",
    "DEFAULT_FILTER_BACKENDS": (),
}

# 分页采用自定义 parsePagination（等价原 NestJS），DEFAULT_PAGINATION_CLASS=None 为有意为之，
# 静默 DRF 的 W001 检查（PAGE_SIZE 仅作为后备默认值）。
SILENCED_SYSTEM_CHECKS = ["rest_framework.W001"]

# ==================== SimpleJWT 配置（被自定义覆盖，此处仅设默认值） ====================
from datetime import timedelta  # noqa: E402


def _parse_expires(value: str) -> timedelta:
    """解析 '24h' / '30m' / '7d' 等 ms 库格式为 timedelta。"""
    import re

    m = re.fullmatch(r"\s*(\d+)\s*([smhd])\s*", value)
    if not m:
        return timedelta(hours=24)
    n = int(m.group(1))
    unit = m.group(2)
    return {  # noqa: E402
        "s": timedelta(seconds=n),
        "m": timedelta(minutes=n),
        "h": timedelta(hours=n),
        "d": timedelta(days=n),
    }[unit]


SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": _parse_expires(JWT_EXPIRES_IN),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=30),
    "ROTATE_REFRESH_TOKENS": False,
    "ALGORITHM": "HS256",
    "SIGNING_KEY": JWT_SECRET,
    "AUTH_HEADER_TYPES": ("Bearer",),
    "TOKEN_TYPE": "access",
    "ISSUER": JWT_ISSUER,
    "AUDIENCE": JWT_AUDIENCE,
}


# ==================== 国际化 ====================
LANGUAGE_CODE = "zh-hans"
TIME_ZONE = "Asia/Shanghai"
USE_I18N = True
USE_TZ = True  # 保持时区感知（与原 Prisma DateTime 一致）


# ==================== 静态文件与上传 ====================
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# /uploads 由自定义 URL 路由 + 中间件托管（CORP cross-origin）
MEDIA_URL = "/uploads/"
MEDIA_ROOT = UPLOAD_DIR

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# ==================== 日志 ====================
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            # operator 来自 OperatorFilter：当前请求的 JWT 用户（无上下文时为 '-'）
            # client_ip 来自 OperatorFilter：客户端 IP（无上下文时为 '-'）
            # device 来自 OperatorFilter：设备信息精简标识（无上下文时为 '-'）
            "format": "[{asctime}] {levelname} {name} [{operator}] [{client_ip}] [{device}] {message}",
            "style": "{",
        },
    },
    "filters": {
        "operator": {
            "()": "apps.common.logfilter.OperatorFilter",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
            "filters": ["operator"],
        },
        "file_rotate": {
            "class": "logging.handlers.TimedRotatingFileHandler",
            "filename": str(LOG_DIR / "gipfel.log"),
            "when": "midnight",
            "backupCount": 14,
            "formatter": "verbose",
            "filters": ["operator"],
            "encoding": "utf-8",
        },
    },
    "root": {
        "handlers": ["console", "file_rotate"],
        "level": LOG_LEVEL,
    },
    "loggers": {
        "django": {
            "handlers": ["console", "file_rotate"],
            "level": "INFO",
            "propagate": False,
        },
        "gipfel": {
            "handlers": ["console", "file_rotate"],
            "level": LOG_LEVEL,
            "propagate": False,
        },
    },
}


# ==================== 反向代理 / HTTPS ====================
# nginx 终止 TLS 后以 http 反代到 daphne(127.0.0.1:8000)，并转发
# X-Forwarded-Proto（见 deploy/nginx-gipfel.conf 各 proxy_set_header）。
# 不声明此项时 Django 的 request.is_secure() 恒为 False：admin 会话与 CSRF
# cookie 不会被标记 Secure，request.build_absolute_uri() 也会生成 http:// 链接。
# 与日志查看器站点保持一致（backend/logviewer/logviewer/settings.py 同名设置）。
#
# 安全性：daphne 仅绑回环地址，公网无法直接投递该头，故信任它是安全的。
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# ==================== Session/Cookie ====================
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
# SESSION_COOKIE_SECURE / CSRF_COOKIE_SECURE 暂不在此无条件开启：
# 纯 HTTP 部署下标记 Secure 会导致 cookie 无法回传、admin 登录失败。
# 如需开启，请参照日志查看器按「外网地址是否为 https」条件设置
# （LOGVIEWER_SECURE_COOKIES 的同类做法），而不是硬编码 True。
