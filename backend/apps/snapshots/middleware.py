"""写请求冻结中间件：门禁处于暂停/回退中时拒绝一切业务写入。

- `PAUSED`    → 写请求（POST/PUT/PATCH/DELETE）返回 `423 Locked`，读请求放行。
- `RESTORING` → 连读请求也返回 `423 Locked`（此刻库内容正在被整表替换，
                任何读都可能拿到半截状态）。

放行的白名单：
- 静态资源 / Socket.IO / 上传文件：与业务数据无关，必须保持可用，
  否则暂停遮罩、心跳、重连都拿不到资源。
- `/api/version`、`/api/health`：版本校验与健康检查。
- `/api/auth/`：登录 / 改密 / 顶号等账号动作（与业务数据无关；暂停期间仍需允许登录，
  否则被强制暂停的客户端连「发生了什么」都看不到）。
- `/api/snapshots`：快照系统自身的端点（查看门禁状态、解除暂停、执行回退）。
  这些端点各自有 `snapshot:view/manage/restore` 权限校验，非超管无法调用。

在途写请求会计入 `gate` 的计数器，供「强制暂停」等待排空使用 —— 这正是
「先翻转模式、再等计数器归零」能够拿到静止点的原因。
"""
from __future__ import annotations

import logging

from django.http import JsonResponse

from . import gate

logger = logging.getLogger("gipfel")

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

EXEMPT_PREFIXES = (
    "/api/snapshots",
    "/api/auth/",
    "/api/version",
    "/api/health",
    "/socket.io/",
    "/static/",
    "/uploads/",
    "/_internal/",  # C1-a 内部实时转发端点：暂停/回退期间自身广播不能被 423 拦掉
    "/favicon.ico",
)


class SnapshotGateMiddleware:
    """全局写请求闸门。"""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path or ""
        if any(path.startswith(prefix) for prefix in EXEMPT_PREFIXES):
            return self.get_response(request)

        mode = gate.load_state()["mode"]
        if mode != gate.MODE_RUNNING:
            method = (request.method or "GET").upper()
            if mode == gate.MODE_RESTORING or method not in SAFE_METHODS:
                return self._locked_response(request, mode)

        method = (request.method or "GET").upper()
        if method not in SAFE_METHODS:
            # 在途写请求计数：强制暂停时据此等待「静止点」
            with gate.write_permit():
                return self.get_response(request)
        return self.get_response(request)

    # ------------------------------------------------------------------
    def _locked_response(self, request, mode: str):
        state = gate.load_state(force=True)
        if mode == gate.MODE_RESTORING:
            message = (
                "系统正在回退数据，期间禁止访问与操作，请稍候；"
                "回退完成后页面会自动同步到回退后的数据。"
            )
            error_code = "system_restoring"
            retry_after = 5
        else:
            reason = state.get("reason") or ""
            message = (
                "系统已被管理员强制暂停"
                + (f"（{reason}）" if reason else "")
                + "，所有写入已冻结；请暂停一切操作，等待系统恢复后继续。"
            )
            error_code = "system_paused"
            retry_after = 10
        payload = {
            "code": 423,
            "message": message,
            "data": None,
            "errorCode": error_code,
            "gate": state,
        }
        response = JsonResponse(
            payload, status=423, json_dumps_params={"ensure_ascii": False}
        )
        response["Retry-After"] = str(retry_after)
        response["X-System-Gate"] = mode
        response["Cache-Control"] = "no-store"
        logger.info(
            "门禁拦截请求：%s %s（mode=%s）", request.method, request.path, mode
        )
        return response
