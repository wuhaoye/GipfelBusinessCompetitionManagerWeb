# -*- coding: utf-8 -*-
"""内部实时转发端点（C1-a）：hub 进程受理 forward 进程转发来的实时事件。

契约（`docs/运维约束整改设计说明.md` §2，路径固定为 settings.REALTIME_INTERNAL_PATH）：

- `POST {"event": str, "payload": obj, "room": str|null}`，成功返回 `{"ok": true, "seq": <int>}`；
- 请求头 `X-Gipfel-Internal-Token: <settings.REALTIME_INTERNAL_TOKEN>`，用
  `secrets.compare_digest` 常数时间比较；
- **仅接受回环来源**（`REMOTE_ADDR` ∈ {127.0.0.1, ::1}），非回环 / 无令牌 / 令牌错 → 403；
- 收到转发后**不做再转发**（`bus.handle_inbound(..., allow_forward=False)`），防自环；
- 必须 `csrf_exempt`（服务间调用没有 CSRF cookie），且 `/_internal/` 已加入
  `apps/snapshots/middleware.py::EXEMPT_PREFIXES`——否则暂停/回退期间
  `system:paused` 自身的转发会被 423 拦掉 = 广播断链。

两处**纯增量**可选字段（不带时行为完全等于上面的契约原文）：
- `"replay": bool`（缺省 `true`）：发送方表达「该事件在本地模式下是否入环」，
  让 hub 侧环形缓冲成员与单进程 `local` 模式逐事件一致；
- `"action": "emit" | "kick"`（缺省 `"emit"`）：`kick` 表示请 hub 代 forward 进程
  断开某账号（`userId` / `reason`）的全部 socket 会话（I-01 顶号 / I-03 管理动作）。
"""
from __future__ import annotations

import json
import logging
import secrets

from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import bus

logger = logging.getLogger("gipfel")


def _is_loopback(remote_addr: str) -> bool:
    """REMOTE_ADDR 是否回环（兼容 `::ffff:127.0.0.1` 这类 IPv4-mapped 写法）。"""
    addr = (remote_addr or "").strip().strip("[]")
    return addr in bus.LOOPBACK_ADDRS


def _forbidden(message: str) -> JsonResponse:
    return JsonResponse({"ok": False, "message": message}, status=403)


def _token_ok(provided: str, expected: str) -> bool:
    """常数时间比较令牌（空令牌直接拒绝；字节比较避免非 ASCII 触发 TypeError）。"""
    if not provided or not expected:
        return False
    try:
        return secrets.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))
    except Exception:  # noqa: BLE001
        return False


@csrf_exempt
@require_POST
def internal_emit_view(request: HttpRequest) -> JsonResponse:
    """内部转发入口（仅回环 + 共享令牌）。"""
    remote = (request.META.get("REMOTE_ADDR") or "").strip()
    if not _is_loopback(remote):
        logger.warning("内部转发端点拒绝非回环来源：%s", remote or "<empty>")
        return _forbidden("loopback_only")

    provided = request.headers.get(bus.INTERNAL_TOKEN_HEADER, "") or ""
    if not _token_ok(provided, bus.internal_token()):
        logger.warning("内部转发端点令牌校验失败（来源 %s）", remote)
        return _forbidden("invalid_token")

    try:
        body = json.loads(request.body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, ValueError):
        return JsonResponse({"ok": False, "message": "invalid_json"}, status=400)
    if not isinstance(body, dict):
        return JsonResponse({"ok": False, "message": "invalid_body"}, status=400)

    action = str(body.get("action") or bus.ACTION_EMIT)
    if action == bus.ACTION_KICK:
        try:
            user_id = int(body.get("userId"))
        except (TypeError, ValueError):
            return JsonResponse({"ok": False, "message": "invalid_userId"}, status=400)
        reason = str(body.get("reason") or "token_version_mismatch")
        seq = bus.handle_inbound_kick(user_id, reason)
        return JsonResponse({"ok": True, "seq": int(seq)})

    event = body.get("event")
    if not isinstance(event, str) or not event:
        return JsonResponse({"ok": False, "message": "invalid_event"}, status=400)
    room = body.get("room")
    if room is not None and not isinstance(room, str):
        return JsonResponse({"ok": False, "message": "invalid_room"}, status=400)

    seq = bus.handle_inbound(
        event,
        body.get("payload"),
        room,
        replay=bool(body.get("replay", True)),
    )
    return JsonResponse({"ok": True, "seq": int(seq)})
