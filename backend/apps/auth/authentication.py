"""JWT 认证。

- HS256 签名 + issuer/audience 校验（与原 jwt.module 配置一致）
- 校验 tokenVersion（顶号下线：payload.tv ≠ user.token_version → 401）
- 强制改密拦截：must_change_password=true 时除「改密」与「读自身资料」外全部拒绝
- 暴露 decode_jwt_payload（供 OperatorContextMiddleware 注入上下文，失败不阻断）
- 暴露 create_jwt（供 LoginView 签发）
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import jwt
from django.conf import settings
from rest_framework import authentication, exceptions

logger = logging.getLogger("gipfel")

_ALGORITHM = "HS256"

# 强制改密放行路径：must_change_password=true 时仅以下接口允许通过。
# · change-password：改密本身。
# · me：前端「修改初始密码」弹窗与 20s 会话心跳都要读自己的资料（只读自身信息，
#   不放大任何业务权限）。改前未放行 → 心跳打 /api/auth/me 收到 401，被前端全局 401
#   拦截器当成「会话过期」清掉 token，用户随后提交改密必然报「登录已过期」
#   （真机事故：新部署的超管永远改不了初始密码，等于无法登录）。
_CHANGE_PASSWORD_PATHS = (
    "/api/auth/change-password",
    "/api/auth/me",
)


# ==================== Token 编解码 ====================
def _expires_in() -> datetime:
    """根据 JWT_EXPIRES_IN（如 '24h'）计算过期时间。"""
    value = getattr(settings, "JWT_EXPIRES_IN", "24h")
    import re

    m = re.fullmatch(r"\s*(\d+)\s*([smhd])\s*", str(value))
    if not m:
        return datetime.now(timezone.utc) + timedelta(hours=24)
    n = int(m.group(1))
    unit = m.group(2)
    delta = {
        "s": timedelta(seconds=n),
        "m": timedelta(minutes=n),
        "h": timedelta(hours=n),
        "d": timedelta(days=n),
    }[unit]
    return datetime.now(timezone.utc) + delta


def create_jwt(user) -> str:
    """签发 JWT，payload：{sub, username, role, tv, cid}。

    tv = token_version，用于顶号下线判定；cid = competitionId。
    """
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user.pk),
        "username": user.username,
        "role": user.role,
        "tv": user.token_version,
        "cid": getattr(user, "competition_id", None),
        "iss": settings.JWT_ISSUER,
        "aud": settings.JWT_AUDIENCE,
        "iat": now,
        "exp": _expires_in(),
    }
    token = jwt.encode(payload, settings.JWT_SECRET, algorithm=_ALGORITHM)
    # PyJWT >=2 返回 str；兼容旧版返回 bytes
    if isinstance(token, bytes):
        token = token.decode("utf-8")
    return token


def decode_jwt_payload(token: str) -> dict | None:
    """解码并校验 JWT，失败返回 None（不抛异常，供上下文中间件安全调用）。"""
    try:
        return jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[_ALGORITHM],
            issuer=settings.JWT_ISSUER,
            audience=settings.JWT_AUDIENCE,
        )
    except jwt.PyJWTError:
        return None
    except Exception:  # noqa: BLE001
        return None


# ==================== DRF 认证类 ====================
class JWTAuthentication(authentication.BaseAuthentication):
    """自定义 JWT 认证：复用 SimpleJWT 的 ALGORITHM/ISSUER/AUDIENCE 配置。"""

    keyword = "Bearer"

    def authenticate(self, request):
        token = self._extract_token(request)
        if token is None:
            # 无凭据：交给 IsAuthenticated 权限层处理（最终 401）
            return None

        payload = decode_jwt_payload(token)
        if payload is None:
            raise exceptions.AuthenticationFailed(
                "登录已过期，请重新登录", code="expired"
            )

        user = self._get_user(payload)
        if user is None:
            raise exceptions.AuthenticationFailed(
                "登录已过期，请重新登录", code="invalid_user"
            )

        # 账号已被禁用：立即失效。禁用是管理动作（PATCH /api/users/:id {"isActive": false}），
        # 若只在登录时校验，被禁用账号仍可用旧 token 访问全部接口直至 token 过期（审计 I-02）。
        if not getattr(user, "is_active", True):
            raise exceptions.AuthenticationFailed(
                "该账号已被禁用，请联系管理员", code="inactive"
            )

        # 顶号下线：token 中 tv 与用户当前 token_version 不一致
        if payload.get("tv") != user.token_version:
            raise exceptions.AuthenticationFailed(
                "账号已在其他设备登录", code="token_version_mismatch"
            )

        # 强制改密：除改密接口外全部拦截
        if getattr(user, "must_change_password", False) and not _is_change_password_endpoint(
            request
        ):
            raise exceptions.AuthenticationFailed(
                "账号需先修改初始密码", code="must_change_password"
            )

        return (user, token)

    def authenticate_header(self, request):
        return self.keyword

    # ---------- 辅助 ----------
    def _extract_token(self, request) -> str | None:
        header = request.headers.get("Authorization", "")
        if not header:
            return None
        parts = header.split()
        if len(parts) == 2 and parts[0].lower() == self.keyword.lower():
            return parts[1].strip()
        return None

    def _get_user(self, payload: dict):
        sub = payload.get("sub")
        if not sub:
            return None
        from apps.users.models import User

        try:
            return User.objects.get(pk=sub)
        except User.DoesNotExist:
            return None
        except Exception:  # noqa: BLE001
            return None


def _is_change_password_endpoint(request) -> bool:
    """判断当前请求是否落在「强制改密期间仍需放行」的路径上（尾匹配，兼容 include 前缀）。"""
    path = request.path or ""
    return path.endswith(_CHANGE_PASSWORD_PATHS)
