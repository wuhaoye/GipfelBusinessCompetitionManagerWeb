"""全局工作门禁（强制暂停 / 回退中 / 恢复正常）。

三个模式：
    RUNNING    —— 正常。所有读写放行。
    PAUSED     —— 强制暂停。所有写请求被 `SnapshotGateMiddleware` 拒绝（HTTP 423），
                  读请求放行；在线客户端收到 `system:paused` 后弹出全屏遮罩、停止一切操作。
    RESTORING  —— 回退中。读写全部被拒（仅白名单端点可用），因为此刻库内容正在被整表
                  替换，任何读都可能拿到半截状态。

「强制暂停」如何做到真正同步：
1. 先把模式写成 PAUSED 并落库（跨进程可见）+ 向所有房间广播；
2. 再等待「在途写请求」计数器归零（`drain`）。计数器由中间件在放行写请求时 +1/-1，
   由于模式已经翻转，新模式下的写请求会直接 423，不会再有新进入者；
3. 归零之后（默认最多等 10 秒）才开始读库/写库，因此快照与回退都建立在
   「没有任何写入在途」的静止点上；排空超时会明确报错而不是带病继续。

`data_version` 在每次成功回退后 +1。客户端把它与本地记录比对，不一致即丢弃本地缓存
并整体重载，从而保证全场所有客户端看到的是同一代数据。
"""
from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from datetime import timedelta
from typing import Any, Iterator

from django.conf import settings
from django.db import OperationalError, ProgrammingError
from django.utils import timezone

from .models import SystemGate

logger = logging.getLogger("gipfel")

MODE_RUNNING = SystemGate.MODE_RUNNING
MODE_PAUSED = SystemGate.MODE_PAUSED
MODE_RESTORING = SystemGate.MODE_RESTORING

#: 门禁状态进程内缓存时长（秒）。多进程部署下其它进程的变更最多滞后这么久。
_CACHE_TTL = float(getattr(settings, "SNAPSHOT_GATE_CACHE_SECONDS", 1.0))
#: 等待在途写请求排空的默认上限（秒）
DRAIN_TIMEOUT = float(getattr(settings, "SNAPSHOT_DRAIN_TIMEOUT", 10.0))

_state_lock = threading.Lock()
_cached_payload: dict | None = None
_cached_at: float = 0.0

_writer_cond = threading.Condition()
_active_writers = 0
_writer_total = 0
#: 是否有独占操作（快照创建 / 回退）正在执行；TTL 自动恢复必须避开它
_exclusive_busy = False


class GateBusy(RuntimeError):
    """在途写请求未能在超时内排空。"""


# ====================================================================
# 状态读取
# ====================================================================
def _empty_state() -> dict:
    return {
        "mode": MODE_RUNNING,
        "reason": "",
        "message": "",
        "operatorId": None,
        "operatorName": "",
        "since": None,
        "ttlSeconds": 0,
        "expiresAt": None,
        "activeSnapshotId": None,
        "progress": "",
        "dataVersion": 0,
        "updatedAt": None,
    }


def _row_to_payload(row: SystemGate) -> dict:
    return {
        "mode": row.mode,
        "reason": row.reason or "",
        "message": row.message or "",
        "operatorId": row.operator_id,
        "operatorName": row.operator_name or "",
        "since": row.since.isoformat() if row.since else None,
        "ttlSeconds": row.ttl_seconds or 0,
        "expiresAt": row.expires_at.isoformat() if row.expires_at else None,
        "activeSnapshotId": row.active_snapshot_id,
        "progress": row.progress or "",
        "dataVersion": row.data_version or 0,
        "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
    }


def load_state(force: bool = False) -> dict:
    """读取当前门禁状态（带 1 秒进程内缓存；表不存在时按 RUNNING 处理）。"""
    global _cached_payload, _cached_at
    now = time.monotonic()
    with _state_lock:
        if (
            not force
            and _cached_payload is not None
            and (now - _cached_at) < _CACHE_TTL
        ):
            return dict(_cached_payload)
    try:
        row = SystemGate.objects.filter(pk=SystemGate.SENTINEL_ID).first()
        if row is None:
            payload = _empty_state()
        else:
            payload = _row_to_payload(row)
    except (OperationalError, ProgrammingError):
        # 迁移尚未执行（首次 migrate / 建库脚本）：视为未启用门禁
        payload = _empty_state()

    # TTL 到期自动恢复（独占操作进行中时不自动恢复，避免打断回退）
    if (
        payload["mode"] != MODE_RUNNING
        and payload.get("expiresAt")
        and not _exclusive_busy
    ):
        try:
            from django.utils.dateparse import parse_datetime

            expires = parse_datetime(payload["expiresAt"])
            if expires is not None and timezone.now() >= expires:
                logger.info("门禁 TTL 到期，自动恢复运行（原模式 %s）", payload["mode"])
                payload = set_mode(
                    MODE_RUNNING,
                    reason="暂停时限到期，系统自动恢复",
                    operator_name="system",
                )
                return payload
        except Exception:  # noqa: BLE001
            logger.debug("解析门禁过期时间失败", exc_info=True)

    with _state_lock:
        _cached_payload = dict(payload)
        _cached_at = time.monotonic()
    return dict(payload)


def is_blocking_reads() -> bool:
    """回退中：连读请求也要拒绝（库内容正在被替换）。"""
    return load_state()["mode"] == MODE_RESTORING


def is_paused() -> bool:
    return load_state()["mode"] != MODE_RUNNING


def data_version() -> int:
    return int(load_state().get("dataVersion") or 0)


def _invalidate_cache() -> None:
    global _cached_payload, _cached_at
    with _state_lock:
        _cached_payload = None
        _cached_at = 0.0


# ====================================================================
# 状态写入
# ====================================================================
def set_mode(
    mode: str,
    *,
    reason: str = "",
    message: str = "",
    ttl_seconds: int = 0,
    active_snapshot_id: int | None = None,
    progress: str = "",
    operator_id: int | None = None,
    operator_name: str = "",
    broadcast: bool = True,
) -> dict:
    """写入门禁状态（落库 + 失效缓存 + 广播），返回最新状态。"""
    if mode not in (MODE_RUNNING, MODE_PAUSED, MODE_RESTORING):
        raise ValueError(f"未知门禁模式：{mode}")

    now = timezone.now()
    row, _created = SystemGate.objects.get_or_create(pk=SystemGate.SENTINEL_ID)
    previous_mode = row.mode
    row.mode = mode
    row.reason = (reason or "")[:255]
    row.message = message or ""
    row.progress = progress or ""
    if mode == MODE_RUNNING:
        row.since = None
        row.expires_at = None
        row.ttl_seconds = 0
        row.operator_id = operator_id
        row.operator_name = (operator_name or "")[:128]
        row.active_snapshot_id = None
    elif previous_mode == mode and row.since is not None:
        # 同模式内更新（如回退进度刷新）：保留原开始时间
        row.ttl_seconds = int(ttl_seconds or 0)
        row.expires_at = row.since + timedelta(seconds=int(ttl_seconds)) if ttl_seconds else None
        if operator_id is not None:
            row.operator_id = operator_id
        if operator_name:
            row.operator_name = operator_name[:128]
        if active_snapshot_id is not None:
            row.active_snapshot_id = active_snapshot_id
    else:
        row.since = now
        row.ttl_seconds = int(ttl_seconds or 0)
        row.expires_at = (
            now + timedelta(seconds=int(ttl_seconds)) if ttl_seconds else None
        )
        if operator_id is not None:
            row.operator_id = operator_id
        if operator_name:
            row.operator_name = operator_name[:128]
        row.active_snapshot_id = active_snapshot_id
    row.save(
        update_fields=[
            "mode",
            "reason",
            "message",
            "progress",
            "since",
            "expires_at",
            "ttl_seconds",
            "operator_id",
            "operator_name",
            "active_snapshot_id",
            "updated_at",
        ]
    )
    _invalidate_cache()
    payload = _row_to_payload(row)
    with _state_lock:
        global _cached_payload, _cached_at
        _cached_payload = dict(payload)
        _cached_at = time.monotonic()

    if broadcast:
        _broadcast_state(payload)
    return dict(payload)


def set_progress(progress: str, *, snapshot_id: int | None = None) -> None:
    """更新回退/快照进度并广播（仅在非 RUNNING 时广播，避免正常期噪声）。"""
    try:
        row = SystemGate.objects.filter(pk=SystemGate.SENTINEL_ID).first()
        if row is None:
            return
        row.progress = (progress or "")[:255]
        if snapshot_id is not None:
            row.active_snapshot_id = snapshot_id
        row.save(update_fields=["progress", "active_snapshot_id", "updated_at"])
        _invalidate_cache()
        if row.mode != MODE_RUNNING:
            payload = _row_to_payload(row)
            _emit("system:progress", payload)
    except Exception:  # noqa: BLE001
        logger.debug("更新门禁进度失败", exc_info=True)


def bump_data_version() -> int:
    """数据版本 +1（每次成功回退后调用），返回新版本号。"""
    row, _created = SystemGate.objects.get_or_create(pk=SystemGate.SENTINEL_ID)
    row.data_version = (row.data_version or 0) + 1
    row.save(update_fields=["data_version", "updated_at"])
    _invalidate_cache()
    logger.info("数据版本号已递增至 %s", row.data_version)
    return row.data_version


# ====================================================================
# 广播
# ====================================================================
def _emit(event: str, payload: dict) -> None:
    try:
        from apps.realtime.emit import emit_system_event

        emit_system_event(event, payload)
    except Exception:  # noqa: BLE001
        logger.debug("门禁事件广播失败 event=%s", event, exc_info=True)


def _broadcast_state(payload: dict) -> None:
    """广播 system:state，并在进入/退出暂停时补发语义事件。"""
    _emit("system:state", payload)
    mode = payload.get("mode")
    if mode == MODE_PAUSED:
        _emit("system:paused", payload)
    elif mode == MODE_RESTORING:
        _emit("system:restoring", payload)
    else:
        _emit("system:resumed", payload)


# ====================================================================
# 在途写请求计数（强制暂停的「排空」依据）
# ====================================================================
def begin_write() -> None:
    global _active_writers, _writer_total
    with _writer_cond:
        _active_writers += 1
        _writer_total += 1


def end_write() -> None:
    global _active_writers
    with _writer_cond:
        if _active_writers > 0:
            _active_writers -= 1
        _writer_cond.notify_all()


def active_writers() -> int:
    with _writer_cond:
        return _active_writers


def writer_stats() -> dict:
    with _writer_cond:
        return {"active": _active_writers, "total": _writer_total}


@contextmanager
def write_permit() -> Iterator[None]:
    """标记一段「在途写操作」（由中间件包裹写请求）。"""
    begin_write()
    try:
        yield
    finally:
        end_write()


def drain(timeout: float | None = None) -> bool:
    """等待在途写请求归零。返回是否在超时前排空。"""
    limit = DRAIN_TIMEOUT if timeout is None else float(timeout)
    deadline = time.monotonic() + limit
    with _writer_cond:
        while _active_writers > 0:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                logger.warning(
                    "强制暂停：仍有 %d 个写请求在途，排空等待超时（%.1fs）",
                    _active_writers,
                    limit,
                )
                return False
            _writer_cond.wait(timeout=min(remaining, 0.5))
    return True


@contextmanager
def exclusive_window(
    *,
    mode: str = MODE_PAUSED,
    reason: str = "",
    message: str = "",
    ttl_seconds: int = 0,
    snapshot_id: int | None = None,
    operator_id: int | None = None,
    operator_name: str = "",
    drain_timeout: float | None = None,
    resume_reason: str = "操作完成，系统已恢复",
) -> Iterator[dict]:
    """「暂停 → 排空 → 执行独占操作 → 恢复」的完整窗口。

    进入时把门禁切到 mode（默认 PAUSED）并广播；排空失败会抛 GateBusy，
    同时把门禁恢复为 RUNNING 后再抛出（避免系统被卡在暂停态）。
    """
    global _exclusive_busy
    if _exclusive_busy:
        raise GateBusy("已有快照/回退操作正在执行，请等待其完成后再试")
    state = set_mode(
        mode,
        reason=reason,
        message=message,
        ttl_seconds=ttl_seconds,
        active_snapshot_id=snapshot_id,
        operator_id=operator_id,
        operator_name=operator_name,
    )
    _exclusive_busy = True
    try:
        if not drain(drain_timeout):
            raise GateBusy(
                f"仍有 {active_writers()} 个写请求未结束，系统未进入静止状态；"
                "请稍后重试（数据未被修改）"
            )
        yield state
    finally:
        _exclusive_busy = False
        set_mode(
            MODE_RUNNING,
            reason=resume_reason,
            operator_id=operator_id,
            operator_name=operator_name,
        )


def is_exclusive_busy() -> bool:
    return _exclusive_busy


def state_payload(extra: dict[str, Any] | None = None) -> dict:
    payload = load_state(force=True)
    if extra:
        payload = {**payload, **extra}
    return payload
