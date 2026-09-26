"""认证与健康检查视图。

响应经 apps.common.response.JSONRenderer 自动包装为 {code,message,data}：
视图返回 Response(data)，其中 data 为 dict/list/None 时渲染器自动包装为
{code:0, message:"成功", data}；如需自定义 message 用 success() 包成含 code 键的 dict。
"""
from __future__ import annotations

import hashlib
import json
import os
import re

import logging

from django.conf import settings
from django.core.signing import TimestampSigner
from django.http import HttpResponseNotModified
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.exceptions import BusinessError
from apps.common.helpers import client_ip as _client_ip
from apps.common.helpers import truthy as _truthy
from apps.common.middleware import (
    normalize_login_username,
    record_login_failure,
    record_login_success,
)

from .authentication import create_jwt

logger = logging.getLogger("gipfel")


def _read_version() -> str:
    """从项目根目录 VERSION.json 读取版本号（单一真源）。"""
    import json
    from pathlib import Path

    vfile = Path(__file__).resolve().parent.parent.parent.parent / "VERSION.json"
    try:
        return json.loads(vfile.read_text(encoding="utf-8"))["version"]
    except Exception:
        return "0.0.0"


VERSION = _read_version()


# ==================== 用户资料序列化 ====================
def serialize_user(user) -> dict:
    """构造前端所需用户资料（与原 auth.controller login/me 返回结构一致）。"""
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "displayName": user.display_name,
        "mustChangePassword": getattr(user, "must_change_password", False),
        "isActive": getattr(user, "is_active", True),
        "permissions": user.permissions_list,
        "companyScopes": user.company_scopes_list,
        "viewCompanyScopes": user.view_company_scopes_list,
        "contractViewCompanyScopes": user.contract_view_company_scopes_list,
        "stockCompanyScopes": user.stock_company_scopes_list,
        "competitionId": getattr(user, "competition_id", None),
    }


# ==================== 健康检查 / 版本 ====================
class HealthView(APIView):
    """GET /api/health → {code:0, message:"成功", data:{status:"ok"}}"""

    permission_classes = (AllowAny,)

    def get(self, request):
        return Response({"status": "ok"})


class VersionView(APIView):
    """GET /api/version → {code:0, message:"成功", data:{version, port, log_viewer_port, log_viewer_url}}

    port 来自 settings.PORT（即 .env 的 PORT），log_viewer_port 来自 settings.LOG_VIEWER_PORT
    （即 .env 的 LOG_VIEWER_PORT），供前端「后端管理」与「日志查看器」跳转按钮动态拼地址，
    避免后端改端口后按钮仍硬编码旧端口。

    安全：未认证用户仅返回版本号（用于版本硬封锁校验）；端口和日志查看器地址仅对已认证用户可见。
    """

    permission_classes = (AllowAny,)

    def get(self, request):
        # 基础响应：版本号（未认证用户也可获取，用于版本硬封锁校验）
        data = {"version": VERSION}

        # 以下敏感信息仅对已认证用户返回（端口、日志查看器地址）
        user = request.user
        if user and user.is_authenticated:
            data["port"] = settings.PORT
            data["log_viewer_port"] = settings.LOG_VIEWER_PORT

            # 日志查看器公网地址：优先 LOG_VIEWER_PUBLIC_URL 显式覆盖；
            # 否则由当前请求 Host 派生子域 log.<host>（部署需配套 DNS A 记录 + certbot -d log.<host>）；
            # 均无则回退本地 127.0.0.1（开发）。前端「日志查看器」按钮据此拼跳转地址。
            log_viewer_url = os.environ.get("LOG_VIEWER_PUBLIC_URL", "").strip()
            if not log_viewer_url:
                host = (request.get_host().split(":") or [""])[0]
                if host:
                    # 纯 IP 部署：日志查看器与前端同主机、走 8120 端口，用 http://<ip>:8120/；
                    # 域名部署：用 https://log.<domain>/（需配套 DNS A 记录 + certbot 覆盖子域）。
                    # 注意：Host 是客户端实际访问地址（经公网即公网 IP），故纯 IP 形态能正确推导公网地址。
                    if re.match(r"^\d{1,3}(\.\d{1,3}){3}$", host) or host.startswith("["):
                        log_viewer_url = f"http://{host}:{settings.LOG_VIEWER_PORT}/"
                    else:
                        log_viewer_url = f"https://log.{host}/"
                else:
                    log_viewer_url = f"http://127.0.0.1:{settings.LOG_VIEWER_PORT}/"
            data["log_viewer_url"] = log_viewer_url

        return Response(data)


# ==================== 登录 ====================
class LoginView(APIView):
    """POST /api/auth/login {username, password} → {token, user}"""

    permission_classes = (AllowAny,)

    def post(self, request):
        # 与限流中间件共用同一归一化口径（strip + 类型/长度约束）：两边键必须一致，
        # 否则「admin 」这类尾随空格变体可绕过锁定（审计 C-01/I-05）；
        # 非字符串入参（列表/数字）也不会再因 .strip()/.encode() 抛异常 → 500。
        username = normalize_login_username(request.data.get("username"))
        raw_password = request.data.get("password")
        password = raw_password if isinstance(raw_password, str) else ""
        if not username or not password:
            raise BusinessError("用户名和密码不能为空", code=400, status_code=400)

        ip = _client_ip(request)

        from apps.users.models import User

        user = User.objects.filter(username=username).first()

        # 用户不存在或密码错误：统一提示，避免枚举用户
        if user is None or not user.check_password(password):
            record_login_failure(ip, username)
            # 诊断日志：日志行的 [xxx] 前缀取自 Authorization 头里的（可能已失效的）token，
            # 并非本次请求体里的用户名——真正尝试的用户名以此处为准。
            # 真实环境曾复现「改密 200 后 20ms 用新密码重登 401、10ms 即返回（未跑 bcrypt）」的竞态，
            # 依据 user_found/hash_len 可区分「查无此人」与「哈希异常」。
            logger.warning(
                "登录失败: username=%r user_found=%s hash_len=%s",
                username[:64],
                user is not None,
                len(user.password_hash) if user else "-",
            )
            raise BusinessError("用户名或密码错误", code=401, status_code=401)

        # 账号已被禁用
        if not getattr(user, "is_active", True):
            raise BusinessError("该账号已被禁用，请联系管理员", code=403, status_code=403)

        # 顶号下线：递增 token_version，旧 token 立即失效
        user.token_version = (user.token_version or 0) + 1
        user.save(update_fields=["token_version", "updated_at"])

        # 立即踢掉旧设备：通知 + **真正断开**旧连接（不等新设备建立 socket 连接）。
        # 只发 auth:required 的话，不响应的客户端仍留在 user-/comp- 房间继续收广播（审计 I-01）。
        from apps.realtime.emit import kick_user_sessions
        kick_user_sessions(user.id, reason="token_version_mismatch")

        record_login_success(ip, username)

        token = create_jwt(user)
        return Response({"token": token, "user": serialize_user(user)})


# ==================== 日志查看器防直连令牌 ====================
class LogViewerTokenView(APIView):
    """POST /api/auth/logviewer-token → {token}

    签发一次性/短时（默认 120s）防直连令牌，供前端「系统设置 → 日志查看器」按钮点击后拼入跳转 URL。
    日志查看器 index 视图校验该令牌，缺失/无效/过期则拒绝访问，从而实现
    「仅按钮点击可跳转、直接输入网址无法跳转」。

    仅 SUPER_ADMIN 可获取（与前端按钮 v-if="isSuperAdmin" 一致）；令牌本身不替代日志查看器
    自身的超级管理员登录，仅作为「来源合法性」网关。
    """

    permission_classes = (IsAuthenticated,)

    def post(self, request):
        if getattr(request.user, "role", None) != "SUPER_ADMIN":
            raise BusinessError("仅超级管理员可生成日志查看器访问令牌", code=403, status_code=403)
        # 用与主后端共用的 LOGVIEWER_SECRET_KEY 签名；盐固定以便日志查看器侧一致校验。
        signer = TimestampSigner(key=settings.LOGVIEWER_SECRET_KEY, salt="logviewer-gate")
        token = signer.sign(f"lv:{request.user.id}")
        return Response({"token": token})


# ==================== 后端管理后台防直连令牌 ====================
class BackendTokenView(APIView):
    """POST /api/auth/backend-token → {token}

    签发一次性/短时（默认 120s）防直连令牌，供前端「系统设置 → 后端管理界面」按钮点击后拼入
    /admin/?token=...。后端 BackendGateMiddleware 校验该令牌，缺失/无效/过期则 302 重定向回前端 SPA，
    从而实现「仅按钮点击可跳转、直接输入网址无法跳转」。

    仅 SUPER_ADMIN 可获取（与前端按钮 v-if="isSuperAdmin" 一致）；令牌本身不替代 Django 后台
    自身的超级管理员登录，仅作为「来源合法性」网关。
    """

    permission_classes = (IsAuthenticated,)

    def post(self, request):
        if getattr(request.user, "role", None) != "SUPER_ADMIN":
            raise BusinessError("仅超级管理员可生成后端管理访问令牌", code=403, status_code=403)
        # 用与主后端/日志查看器共用的 LOGVIEWER_SECRET_KEY 签名；盐固定以便 BackendGateMiddleware 一致校验。
        signer = TimestampSigner(key=settings.LOGVIEWER_SECRET_KEY, salt="backend-gate")
        token = signer.sign(f"bk:{request.user.id}")
        return Response({"token": token})


# ==================== 当前用户 ====================
def _canonical_me_payload(data: dict) -> str:
    """把 /auth/me 的响应 data 规范化为稳定字符串（列表先排序），供 ETag 取哈希。

    permissions / companyScopes / viewCompanyScopes 等字段是**集合**语义（顺序无意义），
    且来源是 role 模板与 JSON 列，顺序不保证跨请求稳定。若不排序就哈希，ETag 会随顺序
    抖动而每次请求都变 —— 心跳永远拿不到 304，等于没做优化（C3.4 验收项）。
    """
    canonical = {
        k: (sorted(v, key=str) if isinstance(v, list) else v) for k, v in data.items()
    }
    return json.dumps(canonical, ensure_ascii=False, sort_keys=True, default=str)


def _me_etag(data: dict, variant: str) -> str:
    """弱 ETag：同一份 data（集合字段排序后）恒定。

    variant 区分两种**表示**（`f`=完整资料 / `l`=light 轻响应）：HTTP 要求不同表示的
    ETag 必须不同，否则客户端拿轻响应的 ETag 去请求完整资料会被误判 304 而丢字段。
    """
    digest = hashlib.sha256(_canonical_me_payload(data).encode("utf-8")).hexdigest()[:32]
    return f'W/"{variant}-{digest}"'


def _strip_weak(tag: str) -> str:
    """去掉弱校验前缀 `W/`（比较时容忍客户端回传弱/强形态差异）。"""
    tag = tag.strip()
    return tag[2:] if tag.startswith("W/") else tag


def _if_none_match_hits(request, etag: str) -> bool:
    """If-None-Match 是否命中当前 ETag：支持 `*`、逗号分隔多值，容忍弱校验前缀。"""
    raw = request.headers.get("If-None-Match")
    if not raw:
        return False
    target = _strip_weak(etag)
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if part == "*":
            return True
        if _strip_weak(part) == target:
            return True
    return False


class MeView(APIView):
    """GET /api/auth/me → 当前登录用户资料（C3：条件请求 + 轻响应，契约见设计说明 §4.1）。

    - 无参：响应体与改造前**逐字段一致**，仅新增 `ETag` 响应头（状态未变则值不变）；
    - `If-None-Match` 命中 → **304 空体 + 同一 ETag**（省掉响应体与序列化开销）；
    - `?light=1` → data 仅 `{id, tokenVersion, isActive, mustChangePassword}`，
      全局信封 `{code,message,data}` 不变；
    - 开关 `AUTH_ME_CONDITIONAL_ENABLED=false` → 以上三项全部退化为改造前行为
      （不写 ETag、不校验 If-None-Match、忽略 light，始终返回完整资料）；
    - **401 语义一个字都不变**：失效 / 被顶号（token_version 不一致）/ 强制改密门禁
      都由 JWTAuthentication 在进入本视图前抛出，本视图既不参与也不改写任何 401 分支
      —— 心跳正是靠它感知顶号。
    """

    permission_classes = (IsAuthenticated,)

    def get(self, request):
        user = request.user

        # 开关关闭：完全回到改造前行为（不写 ETag / 不看 If-None-Match / 忽略 light）
        if not getattr(settings, "AUTH_ME_CONDITIONAL_ENABLED", True):
            return Response(serialize_user(user))

        if _truthy(request.query_params.get("light")):
            data = {
                "id": user.id,
                "tokenVersion": getattr(user, "token_version", 0),
                "isActive": getattr(user, "is_active", True),
                "mustChangePassword": getattr(user, "must_change_password", False),
            }
            etag = _me_etag(data, "l")
        else:
            data = serialize_user(user)
            etag = _me_etag(data, "f")

        if _if_none_match_hits(request, etag):
            # 304 必须空体：直接用 Django 的 HttpResponseNotModified，不经 DRF 渲染器
            # （经渲染器会把 {code,message,data} 塞进 304 响应体，违反 HTTP 语义）。
            not_modified = HttpResponseNotModified()
            not_modified["ETag"] = etag
            return not_modified

        response = Response(data)
        response["ETag"] = etag
        return response


# ==================== 修改密码 ====================
class ChangePasswordView(APIView):
    """POST /api/auth/change-password {oldPassword, newPassword}

    标记 _allow_must_change_password=True：强制改密场景下放行（认证层据此豁免）。
    """

    permission_classes = (IsAuthenticated,)
    _allow_must_change_password = True

    def post(self, request):
        old_password = request.data.get("oldPassword") or ""
        new_password = request.data.get("newPassword") or ""
        if not old_password or not new_password:
            raise BusinessError("原密码和新密码不能为空", code=400, status_code=400)
        if len(new_password) < 8:
            raise BusinessError("新密码长度不能少于 8 位", code=400, status_code=400)

        user = request.user
        if not user.check_password(old_password):
            raise BusinessError("原密码不正确", code=400, status_code=400)
        if old_password == new_password:
            raise BusinessError("新密码不能与原密码相同", code=400, status_code=400)

        user.set_password(new_password)
        user.must_change_password = False
        # 改密后吊销所有已签发 token：递增 token_version，旧 JWT 立即失效
        user.token_version = (user.token_version or 0) + 1
        user.save(
            update_fields=[
                "password_hash",
                "must_change_password",
                "token_version",
                "updated_at",
            ]
        )

        # 直接签发新 token 返回，前端用其续接会话——避免让前端再次 /login 触发的
        # SQLite 写后读竞态（曾复现 200 后 20ms 用新密码重登 401、10ms 即返回
        # 「未跑 bcrypt」的场景，本质是 login 路径上 User 实例偶发读到旧/空
        # password_hash）。改密、吊销旧 token、签发新 token 三步在同一请求同一
        # ORM 实例上原子完成，读时已能见到新 hash。
        new_token = create_jwt(user)
        return Response({"ok": True, "token": new_token, "user": serialize_user(user)})
