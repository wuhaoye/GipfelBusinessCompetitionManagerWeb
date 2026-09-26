"""
Django 设置模块。

环境变量启动校验（fail-fast）：JWT_SECRET 未配置时直接抛异常拒绝启动，
与原 NestJS main.ts 的 zod 校验保持一致。
"""
from __future__ import annotations

import hashlib
import hmac
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

# ==================== 快照与回退（apps.snapshots） ====================
# 归档目录：每份快照一个子目录（manifest.json + tables/*.jsonl.gz + files/）
SNAPSHOT_DIR = Path(os.environ.get("SNAPSHOT_DIR", str(BASE_DIR / "snapshots")))
SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
# 门禁状态进程内缓存秒数（多进程部署时其它进程的变更最多滞后这么久）
SNAPSHOT_GATE_CACHE_SECONDS = float(os.environ.get("SNAPSHOT_GATE_CACHE_SECONDS", "1"))
# 强制暂停时等待「在途写请求」排空的上限（秒），超时则暂停操作失败（数据不变）
SNAPSHOT_DRAIN_TIMEOUT = float(os.environ.get("SNAPSHOT_DRAIN_TIMEOUT", "10"))
# 回退中状态的兜底 TTL（秒）：进程崩溃时不至于永久停在「回退中」
SNAPSHOT_RESTORE_TTL_SECONDS = int(os.environ.get("SNAPSHOT_RESTORE_TTL_SECONDS", "900"))
# 手动暂停的默认 TTL（0 = 不自动恢复，需管理员显式恢复）
SNAPSHOT_PAUSE_TTL_SECONDS = int(os.environ.get("SNAPSHOT_PAUSE_TTL_SECONDS", "0"))
# 上传文件归档总量上限（字节），超过则跳过剩余文件并在快照里记录提示
SNAPSHOT_MAX_FILE_BYTES = int(
    os.environ.get("SNAPSHOT_MAX_FILE_BYTES", str(2 * 1024 * 1024 * 1024))
)
# 单次下载打包上限（字节）
SNAPSHOT_DOWNLOAD_MAX_BYTES = int(
    os.environ.get("SNAPSHOT_DOWNLOAD_MAX_BYTES", str(256 * 1024 * 1024))
)

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
    "apps.snapshots",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",  # 必须在 Common 之前
    "apps.common.cors.DynamicCorsMiddleware",  # 自定义 CORS 反射（接管 corsheaders 动态判断）
    "apps.common.middleware.SecurityHeadersMiddleware",
    "apps.common.middleware.OperatorContextMiddleware",
    "apps.common.middleware.LoginRateLimitMiddleware",
    # 快照门禁：处于「强制暂停 / 回退中」时拒绝业务写入（HTTP 423），
    # 把在途写请求计入计数器供排空使用。白名单端点见 apps/snapshots/middleware.py。
    "apps.snapshots.middleware.SnapshotGateMiddleware",
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


# ==================== 数据库（C2 阶段 1：SQLite 调优） ====================
# 背景（见《架构性运维约束整改简报.md》C2）：默认回滚日志（journal_mode=delete）+
# sqlite3 默认 busy_timeout=5000ms，写并发一高就抛 "database is locked"。
#
# 改法分两层（Django 5.0 的 sqlite3 后端**只认** sqlite3.connect() 的形参，
# 没有 init_command/transaction_mode：见 django/db/backends/sqlite3/base.py
# get_connection_params()，OPTIONS 会被原样 **kwargs 传给 sqlite3.connect）：
#   ① OPTIONS["timeout"]      → sqlite3 的锁等待秒数（即 PRAGMA busy_timeout = timeout*1000）
#   ② connection_created 信号 → 对**每条**新连接执行 PRAGMA（WAL / synchronous / busy_timeout）
#      实现见 apps/common/db_pragmas.py（由 apps/common/apps.py 的 ready() 接线）。
# 单靠 OPTIONS 无法开 WAL，单靠信号也要每个连接都跑一遍——两者缺一不可。
#
# ⚠️ WAL 依赖共享内存与文件锁，**数据库必须位于本地磁盘**（网络盘/NFS/容器挂载卷上
#    可能不可用甚至损坏）。部署前确认；如需退回，设 SQLITE_JOURNAL_MODE=DELETE 即可。
_ENV_TRUE = {"1", "true", "yes", "on"}

#: 是否启用 SQLite PRAGMA 调优（关闭后行为与改造前完全一致，便于现场快速回退）
SQLITE_TUNING_ENABLED = (
    os.environ.get("SQLITE_TUNING_ENABLED", "true").strip().lower() in _ENV_TRUE
)
#: 日志模式：WAL（推荐）| DELETE | TRUNCATE | PERSIST | MEMORY | OFF
SQLITE_JOURNAL_MODE = os.environ.get("SQLITE_JOURNAL_MODE", "WAL").strip().upper()
#: 同步级别：NORMAL（WAL 下的推荐值）| FULL | OFF | EXTRA
SQLITE_SYNCHRONOUS = os.environ.get("SQLITE_SYNCHRONOUS", "NORMAL").strip().upper()
#: 锁等待毫秒数（改造前为 sqlite3 默认 5000ms，高并发下频繁抛锁错误）
SQLITE_BUSY_TIMEOUT_MS = int(os.environ.get("SQLITE_BUSY_TIMEOUT_MS", "20000"))
SQLITE_WAL_AUTOCHECKPOINT = int(os.environ.get("SQLITE_WAL_AUTOCHECKPOINT", "1000"))

# 白名单校验：这几个值会被拼进 PRAGMA 语句，不接受任意字符串（防配置注入 / 打错字静默失效）
if SQLITE_JOURNAL_MODE not in {"WAL", "DELETE", "TRUNCATE", "PERSIST", "MEMORY", "OFF"}:
    raise RuntimeError(
        f"环境变量校验失败:\n  SQLITE_JOURNAL_MODE: 非法值 {SQLITE_JOURNAL_MODE!r}"
        "（允许 WAL/DELETE/TRUNCATE/PERSIST/MEMORY/OFF）"
    )
if SQLITE_SYNCHRONOUS not in {"OFF", "NORMAL", "FULL", "EXTRA"}:
    raise RuntimeError(
        f"环境变量校验失败:\n  SQLITE_SYNCHRONOUS: 非法值 {SQLITE_SYNCHRONOUS!r}"
        "（允许 OFF/NORMAL/FULL/EXTRA）"
    )
if SQLITE_BUSY_TIMEOUT_MS <= 0:
    raise RuntimeError("环境变量校验失败:\n  SQLITE_BUSY_TIMEOUT_MS: 必须为正整数（毫秒）")

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
        # timeout → sqlite3.connect(timeout=...) → PRAGMA busy_timeout=timeout*1000
        #
        # ⚠️ 与 `SQLITE_TUNING_ENABLED` 联动：关掉调优开关时这里**连 OPTIONS 一起撤掉**，
        # 于是锁等待回到 sqlite3 默认 5s、WAL 也不再下发 —— 现场「一键回退到改造前」只需
        # 一个开关（AUDIT_HTTP_ERROR_MODE=all 负责审计那一半），不必再去动 OPTIONS。
        "OPTIONS": (
            {"timeout": SQLITE_BUSY_TIMEOUT_MS / 1000.0}
            if SQLITE_TUNING_ENABLED
            else {}
        ),
    }
}

#: 启动时实际下发的 PRAGMA 列表（apps/common/db_pragmas.py 逐条执行并记录日志）
SQLITE_PRAGMA_STATEMENTS = (
    f"PRAGMA journal_mode={SQLITE_JOURNAL_MODE}",
    f"PRAGMA synchronous={SQLITE_SYNCHRONOUS}",
    f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}",
    f"PRAGMA wal_autocheckpoint={SQLITE_WAL_AUTOCHECKPOINT}",
)


# ==================== 审计噪声治理（C2 阶段 1） ====================
# 背景（见简报 C2.1 写放大来源）：改造前**每个 4xx/5xx 都写一行审计**，
# 100 客户端重连/IP 抖动时 401/403 会淹没 AuditLog 并把 SQLite 写串行放大。
# log_write（业务写审计）**不受本开关影响**，永远落库。
#   all     = 与改造前一致（全部落库）
#   sampled = 5xx 全量落库；4xx 仅按采样率落库 + 始终写文件日志（默认，赛后排查仍可查日志）
#   off     = 4xx/5xx 都只写文件日志，不落库
AUDIT_HTTP_ERROR_MODE = os.environ.get("AUDIT_HTTP_ERROR_MODE", "sampled").strip().lower()
if AUDIT_HTTP_ERROR_MODE not in {"all", "sampled", "off"}:
    raise RuntimeError(
        f"环境变量校验失败:\n  AUDIT_HTTP_ERROR_MODE: 非法值 {AUDIT_HTTP_ERROR_MODE!r}"
        "（允许 all/sampled/off）"
    )
AUDIT_HTTP_ERROR_SAMPLE_RATE = float(
    os.environ.get("AUDIT_HTTP_ERROR_SAMPLE_RATE", "0.05")
)
if not 0.0 <= AUDIT_HTTP_ERROR_SAMPLE_RATE <= 1.0:
    raise RuntimeError(
        "环境变量校验失败:\n  AUDIT_HTTP_ERROR_SAMPLE_RATE: 必须在 [0, 1] 区间"
    )
#: 审计历史保留天数（manage.py audit_archive 的默认值）
AUDIT_RETENTION_DAYS = int(os.environ.get("AUDIT_RETENTION_DAYS", "7"))


# ==================== 实时事件总线（C1-a：HTTP 与 WebSocket 进程分离） ====================
# 背景（见简报 C1.4）：/api/* 改由多 worker 的 WSGI 承载后，REST 进程里的
# emit_resource_changed **推不到 daphne 进程里的 socket 连接**（改造前靠「同进程 +
# run_coroutine_threadsafe」才成立）。因此必须引入跨进程事件总线：
#
#   REALTIME_BUS=local   （默认 auto 且无其它配置时的落点）单进程老行为，进程内 deque + loop 投递
#   REALTIME_BUS=hub     daphne 进程：拥有 socket / 序号 / 环形缓冲，接收并本地投递内部转发
#   REALTIME_BUS=forward WSGI 进程：把 emit **转发**给 hub（内部回环 HTTP + 共享密钥）
#   REALTIME_BUS=redis   两端都用 Redis（python-socketio 的 AsyncRedisManager + Redis 序号/环形缓冲）
#   REALTIME_BUS=auto    有 REALTIME_REDIS_URL → redis；否则 local（零配置 = 与改造前完全一致）
REALTIME_BUS = os.environ.get("REALTIME_BUS", "auto").strip().lower()
if REALTIME_BUS not in {"auto", "local", "hub", "forward", "redis"}:
    raise RuntimeError(
        f"环境变量校验失败:\n  REALTIME_BUS: 非法值 {REALTIME_BUS!r}"
        "（允许 auto/local/hub/forward/redis）"
    )
#: Redis 连接串（如 redis://127.0.0.1:6379/0）；为空则不用 Redis
REALTIME_REDIS_URL = os.environ.get("REALTIME_REDIS_URL", "").strip()
#: hub 的内部转发地址（forward 模式必填，如 http://127.0.0.1:8000）；双端可共用同一份 .env
REALTIME_FORWARD_URL = os.environ.get("REALTIME_FORWARD_URL", "").strip().rstrip("/")
#: 单次内部转发的超时（秒）：回环 HTTP，超时只丢弃该事件、绝不阻断业务请求
REALTIME_FORWARD_TIMEOUT = float(os.environ.get("REALTIME_FORWARD_TIMEOUT", "0.5"))
#: 重放环形缓冲长度（seq 单调递增，超出即淘汰最旧）
REALTIME_RING_MAX_LEN = int(os.environ.get("REALTIME_RING_MAX_LEN", "5000"))
#: 内部转发共享密钥：由 LOGVIEWER_SECRET_KEY 派生（两进程读同一 .env，无需额外配置）
REALTIME_INTERNAL_TOKEN = hmac.new(
    LOGVIEWER_SECRET_KEY.encode("utf-8"),
    b"gipfel-internal-realtime-emit",
    hashlib.sha256,
).hexdigest()
#: 内部转发端点的路径（非 /api 前缀：nginx 不会代理它，公网不可达）
REALTIME_INTERNAL_PATH = "/_internal/realtime/emit"


# ==================== 会话心跳条件请求（C3） ====================
# /api/auth/me 支持 ETag/If-None-Match（命中即 304 空体）与 ?light=1 轻响应，
# 用于把 100 客户端 × 20s 的稳态心跳流量降下来（见简报 C3.3）。
# 关闭后行为与改造前完全一致（始终返回完整资料 + 不校验 If-None-Match）。
AUTH_ME_CONDITIONAL_ENABLED = (
    os.environ.get("AUTH_ME_CONDITIONAL_ENABLED", "true").strip().lower() in _ENV_TRUE
)


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
