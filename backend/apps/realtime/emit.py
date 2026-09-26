# -*- coding: utf-8 -*-
"""实时广播辅助。

【关键集成点】python-socketio AsyncServer(async_mode="asgi") 的 emit 必须在 ASGI
事件循环内执行（await sio.emit）；Django HTTP 视图/信号运行在同步线程中，直接
调用 sio.emit 会因「当前线程无正在运行的 event loop」而静默失败或抛异常。

修复方案（基于 experience 563698）：
1. gateway 保存其 ASGI server 的 running loop（在 connect 事件中记录）
2. 同步侧通过 `asyncio.run_coroutine_threadsafe(coro, loop)` 把协程投递到那个 loop
3. 结果用 Future 等待，但有 1s 超时；超时不阻断 HTTP 主流程
4. loop 未就绪时（如脚本/migrate/测试）降级为静默跳过

【C1-a 改造】「投递出口」换成跨进程总线 `apps.realtime.bus`（HTTP 与 WebSocket
进程分离后，WSGI 进程里的 emit 推不到 daphne 进程里的 socket）：
- **本模块公开函数签名与语义一律不变**（调用方：apps/common/signals.py、
  apps/snapshots/*、各视图，均未改动）；
- seq 分配 / 环形缓冲 / 实际投递分别委托 `bus.allocate_seq` / `bus.push_ring` /
  `bus.deliver`，按 settings.REALTIME_BUS = auto/local/hub/forward/redis 分派；
- `_after_commit` 延迟提交语义不变（见下），因此 seq 仍在提交后才分配。

契约对齐前端 realtime/resource-changed.ts 的 ResourceChangedEvent：
    { resource, ids[], action ("created"|"updated"|"deleted"|"bulk"),
      competitionId, seq, ts }
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time

from . import bus

logger = logging.getLogger("gipfel")

EVENT_RESOURCE_CHANGED = "resource:changed"
EVENT_PERMISSIONS_CHANGED = "permissions:changed"
#: 通知客户端「需要重新认证」（顶号 / 被禁用 / 密码被重置后由前端清登录态并跳登录页）
EVENT_AUTH_REQUIRED = "auth:required"

# ---------- 系统门禁（快照 / 强制暂停 / 回退）事件 ----------
#: 全量状态快照（连接握手、状态变更时下发）：{mode, reason, message, since, expiresAt,
#: dataVersion, activeSnapshotId, progress, operatorName, seq, ts}
EVENT_SYSTEM_STATE = "system:state"
#: 强制暂停（写请求全部被冻结）
EVENT_SYSTEM_PAUSED = "system:paused"
#: 回退进行中（读写全部被冻结）
EVENT_SYSTEM_RESTORING = "system:restoring"
#: 恢复运行（客户端应比对 dataVersion 决定是否整体重载）
EVENT_SYSTEM_RESUMED = "system:resumed"
#: 回退完成后的一次性通知：{snapshotId, label, dataVersion, rows, tables, ...}
EVENT_SYSTEM_RESTORED = "system:restored"
#: 操作进度（快照创建 / 回退阶段）
EVENT_SYSTEM_PROGRESS = "system:progress"

# ASGI 事件循环引用（由 gateway.connect 首次触发时赋值；仅写一次）
_loop_lock = threading.Lock()
_loop: asyncio.AbstractEventLoop | None = None


def register_loop(loop: asyncio.AbstractEventLoop) -> None:
    """由 gateway 在 connect 事件中调用，注册 ASGI 事件循环供同步侧投递 emit。"""
    global _loop
    with _loop_lock:
        # 覆盖重注册：ASGI worker 重启 / reload 后旧 loop 可能已关闭，
        # 必须刷新，否则 _run_coro_on_loop 会因 loop.is_running() 为 False 静默丢弃所有实时广播（#P3）
        if _loop is None or _loop.is_closed():
            _loop = loop


def _get_loop_safe() -> asyncio.AbstractEventLoop | None:
    """线程安全获取已注册的 ASGI loop。"""
    with _loop_lock:
        return _loop


# ====================================================================
# 序号 / 环形缓冲（委托跨进程总线 bus：local/hub=进程内，redis=INCR+ZSET）
# ====================================================================
# 语义与改造前逐行一致，仅存储介质随 settings.REALTIME_BUS 变化：
#   local / hub → 进程内 deque + 进程内 seq（= 改造前）
#   forward     → 不入环、seq 由 hub 权威分配（本进程计数不权威）
#   redis       → Redis INCR 序号 + ZSET 环形缓冲


def _next_seq() -> int:
    """分配下一个序号（模式语义见 bus.allocate_seq）。

    广播路径不要单独调用它再 `_push_ring`：请用 `bus.assign_seq_and_ring` /
    `bus.setdefault_seq_and_ring`，它们把取号与入环放在同一临界区（修复并发广播时
    环内 seq 可能非单调的既有缺陷）。
    """
    return bus.allocate_seq()


def _current_seq() -> int:
    return bus.current_seq()


def _push_ring(event, data: dict, room: str | None) -> None:
    """写入重放环形缓冲（forward 进程不入环：权威环在 hub 进程）。

    保留为低层入口；广播路径已改用 `bus.assign_seq_and_ring` /
    `bus.setdefault_seq_and_ring`（取号 + 入环原子完成）。
    """
    bus.push_ring(event, data, room)


def replay_since(last_seq: int) -> list[dict]:
    """补发 seq > last_seq 的事件（local/hub=进程内环，redis=ZSET）。

    `sync:replay` 只在持有 socket 的进程里被调用（hub / 单进程），forward 进程
    不服务重放，因此这里直接委托总线。
    """
    return bus.replay_since(last_seq)


def server_seq() -> int:
    return bus.current_seq()


# ====================================================================
# MODEL → resource 映射（MODEL_TO_RESOURCE、GLOBAL_RESOURCES、辅助函数）
# ====================================================================
MODEL_TO_RESOURCE: dict[str, str] = {
    # P0
    "Competition": "competitions",
    "FiscalYear": "fiscalYears",
    "User": "users",
    "AuditLog": None,
    # P1 生产链与地图
    "Material": "materials",
    "Part": "parts",
    "PartMaterial": None,
    "PartTechRequirement": None,
    "Product": "products",
    "ProductPart": None,
    "ProductTechRequirement": None,
    "TechNode": "tech-nodes",
    "TechPrerequisite": None,
    "MapNodeType": "map-node-types",
    "PathType": "path-types",
    "MapNode": "map-nodes",
    "MapEdge": "map-edges",
    "Infrastructure": "infrastructures",
    "Fuel": "fuels",
    "Vehicle": "vehicles",
    "VehiclePathType": None,
    "Warehouse": "warehouses",
    "ProductionLine": "production-lines",
    # P2
    "IndustryType": "industry-types",
    "IndustryField": "industry-fields",
    "Company": "companies",
    "CompanyFieldValue": None,
    "ContractType": "contract-types",
    "Contract": "contracts",
    "ContractFieldEffect": None,
    "Region": "region",
    "ConsumerDemand": "consumer-demand",
    # P3
    "Message": "message",
    "MessageRecipient": None,
    "Stock": "stocks",
    "StockFundsAccount": "stock-accounts",
    "StockHolding": "stock-holdings",
    "StockOrder": "stock-orders",
    "StockCandle": "stock-candles",
}

GLOBAL_RESOURCES = {"industry-types", "industry-fields"}


def resolve_model_info(model_class) -> tuple[str | None, bool]:
    name = getattr(model_class, "__name__", "")
    resource = MODEL_TO_RESOURCE.get(name)
    if not resource:
        return None, False
    return resource, resource in GLOBAL_RESOURCES


def extract_competition_id(instance) -> int | None:
    for attr in ("competition_id", "competitionId"):
        v = getattr(instance, attr, None)
        if isinstance(v, int):
            return v
    comp_ref = getattr(instance, "competition", None)
    if comp_ref is not None:
        cid = getattr(comp_ref, "id", None)
        if isinstance(cid, int):
            return cid
    return None


# ====================================================================
# 底层：投递到 ASGI 事件循环的线程安全 emit
# ====================================================================
def _run_coro_on_loop(coro) -> bool:
    """安全地把协程投递到已注册的 ASGI loop，返回是否「已尝试投递」。

    - loop 未注册 → 返回 False，调用方应静默降级
    - 投递成功 → 返回 True；注意结果不等待、不阻塞调用线程超过 10ms
    - 任何异常 → logger.debug 记录、不抛
    """
    loop = _get_loop_safe()
    if loop is None or not loop.is_running():
        return False
    try:
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        # 给调度器最多 10ms 把任务挂上；不等待 emit 完成（WebSocket 发报是 IO）
        future.result(timeout=0.01)
    except asyncio.TimeoutError:
        # 调度成功但未立即完成 → 正常（后台会继续）
        pass
    except Exception as exc:  # noqa: BLE001
        logger.debug("ASGI 投递协程失败: %s", exc, exc_info=True)
    return True


def _deliver_local(event: str, payload, room: str | None = None) -> None:
    """本进程投递原语：把事件投到已注册的 ASGI loop（= 改造前的 _emit_sio 本体）。

    由 `bus` 在 local/hub（以及「受理转发」）分支调用；loop 未就绪或失败静默。
    """
    try:
        from .gateway import sio
    except Exception:  # noqa: BLE001
        return

    async def _do_emit():
        try:
            if room:
                await sio.emit(event, payload, room=room)
            else:
                await sio.emit(event, payload)
        except Exception:  # noqa: BLE001
            logger.debug("emit_sio await 失败 event=%s", event, exc_info=True)

    # 如果当前就在 ASGI 循环内（Socket.IO 处理器本身），直接 await
    try:
        cur_loop = asyncio.get_running_loop()
        loop_ref = _get_loop_safe()
        if cur_loop is not None and (loop_ref is cur_loop):
            cur_loop.create_task(_do_emit())
            return
    except RuntimeError:
        pass  # 无运行中 loop → 走 run_coroutine_threadsafe

    # 无可用 ASGI loop（管理命令 / migrate / 单元测试）：直接返回，避免创建出
    # 永不 await 的协程而触发 RuntimeWarning（行为与原先的静默降级一致）。
    if _get_loop_safe() is None:
        return
    _run_coro_on_loop(_do_emit())


def _emit_sio(event: str, payload, room: str | None = None) -> None:
    """兼容入口（widget_packages 等直接调用）：交给总线按模式分派投递。

    与改造前一致：**不含 seq、不入环**（`replay=False`，纯即时通知）。
    local/hub 下行为与改造前的 `sio.emit` 投递完全相同；forward/redis 下由总线
    转发/发布，修掉「多进程后这类即时通知静默丢失」的问题。
    """
    bus.deliver(event, payload, room, replay=False)


# ====================================================================
# 事务提交后投递
# ====================================================================
def _after_commit(fn) -> None:
    """把广播延迟到数据库事务提交之后执行。

    为什么必须延迟：post_save 信号在 `save()` 那一刻就触发，显式 emit 也常写在
    `transaction.atomic()` 内部（合同落账 / 复原、股票推进轮次、财年定时器、
    产业字段级联重算）。若此时立即广播，客户端会马上回拉数据，但它走的是另一个
    数据库连接、读不到尚未提交的写入 —— 于是拿到旧值，而事件已经消耗掉、不会再来，
    表现就是「刷新不及时」（数据要等下一次事件或手动刷新才对）。

    行为说明：
    - 不在事务中时，on_commit 会同步立即执行，与原先行为完全一致；
    - 事务回滚时回调被丢弃，顺带消除了「回滚了却已广播」的假事件；
    - seq 在回调内才分配，保证重放环形缓冲的 seq 顺序与实际提交顺序一致。
    """
    from django.db import transaction

    def _run() -> None:
        try:
            fn()
        except Exception:  # noqa: BLE001
            logger.debug("事务提交后广播执行失败", exc_info=True)

    try:
        transaction.on_commit(_run)
    except Exception:  # noqa: BLE001
        # 无可用数据库连接等极端场景（管理脚本 / 启动期）→ 退化为立即执行
        _run()


# ====================================================================
# 广播 API
# ====================================================================
def emit_resource_changed(
    resource: str,
    record_id: int | None,
    competition_id: int | None,
    action: str,
    *,
    ids: list[int] | None = None,
) -> None:
    if ids is None:
        ids = [record_id] if isinstance(record_id, int) else []
    resolved_ids = list(ids)

    is_global = resource in GLOBAL_RESOURCES
    room: str | None = None
    if not is_global and competition_id is not None:
        room = f"comp-{competition_id}"

    def _deliver() -> None:
        # seq / ts 在提交后才取，确保客户端按 seq 重放的顺序与落库顺序一致；
        # 「取号 + 入环」由 bus.assign_seq_and_ring 在同一临界区内原子完成
        # （修复并发广播时环内 seq 可能非单调的既有缺陷；事件字段集合与取值不变）
        payload = {
            "resource": resource,
            "id": (
                record_id
                if isinstance(record_id, int)
                else (resolved_ids[0] if resolved_ids else None)
            ),
            "ids": resolved_ids,
            "action": action,
            "competitionId": competition_id,
            "ts": int(time.time() * 1000),
        }
        bus.assign_seq_and_ring(EVENT_RESOURCE_CHANGED, payload, room)
        # replay=True：该事件在本地模式下入环，转发给 hub 时同样要入环（断线可补发）
        bus.deliver(EVENT_RESOURCE_CHANGED, payload, room, replay=True)

    _after_commit(_deliver)


def emit_resource_changed_to_users(
    resource: str,
    record_id: int,
    user_ids,
    action: str,
    competition_id: int | None = None,
    ids: list[int] | None = None,
) -> None:
    """向指定用户房间广播资源变更（不进入环形重放：每用户独立房间）。"""
    if ids is None:
        ids = [record_id] if isinstance(record_id, int) else []
    resolved_ids = list(ids)
    targets = list(user_ids or [])

    def _deliver() -> None:
        payload = {
            "resource": resource,
            "id": (
                record_id
                if isinstance(record_id, int)
                else (resolved_ids[0] if resolved_ids else None)
            ),
            "ids": resolved_ids,
            "action": action,
            "competitionId": competition_id,
            "ts": int(time.time() * 1000),
        }
        # 只取号（统一覆盖），**不入环**（= 改造前语义：每用户独立房间不参与重放）
        bus.assign_seq(payload)
        for uid in targets:
            # replay=False：与改造前一致，此类事件从不入环（否则重连会重复消费）
            bus.deliver(EVENT_RESOURCE_CHANGED, payload, f"user-{uid}", replay=False)

    _after_commit(_deliver)


def emit_to_users(user_ids, event: str, data) -> None:
    for uid in user_ids or []:
        bus.deliver(event, data, f"user-{uid}", replay=False)


async def kick_user_sessions_async(user_id: int, reason: str) -> None:
    """异步实现：通知并**真正断开**某账号的全部 Socket.IO 会话。

    只发 `auth:required` 不够——客户端若不响应（或恶意客户端根本不理会），
    连接仍然留在 `user-{id}` / `comp-{id}` 房间里继续收广播。因此先通知、再逐个
    `sio.disconnect(sid)`（审计 I-01 顶号 / I-03 管理动作）。
    """
    from .gateway import sio

    room = f"user-{user_id}"
    try:
        await sio.emit(EVENT_AUTH_REQUIRED, {"reason": reason}, room=room)
    except Exception:  # noqa: BLE001
        logger.debug("auth:required 通知失败 user=%s", user_id, exc_info=True)
    try:
        # get_participants 是同步生成器，产出 (sid, eio_sid)
        for sid, _eio_sid in list(sio.manager.get_participants("/", room)):
            try:
                await sio.disconnect(sid)
            except Exception:  # noqa: BLE001
                logger.debug("断开 socket 失败 sid=%s", sid, exc_info=True)
    except Exception:  # noqa: BLE001
        logger.debug("读取房间成员失败 user=%s", user_id, exc_info=True)


def kick_user_sessions(user_id: int, reason: str = "token_version_mismatch") -> None:
    """同步入口：把某账号的所有在线会话踢下线（loop 未就绪时静默跳过）。

    调用场景：登录顶号、管理员重置密码、管理员禁用账号。
    实际投递由 `bus.kick_user_sessions` 按模式分派：local/hub 本进程执行（= 改造前）；
    forward，或 redis 且配了 REALTIME_FORWARD_URL 时，由 hub 代执行断开（否则进程分离
    或 Redis 多进程下只发通知、不断开，I-01/I-03 失效）；纯 redis 无 forward URL 时
    只能跨进程送达 auth:required（已知限制，见 bus.py 模块 docstring）。
    """
    bus.kick_user_sessions(user_id, reason)


def emit_to_competition(competition_id: int | None, event: str, data) -> None:
    """向某比赛房间（comp-<id>）广播自定义事件。

    用于 resource:changed 之外的业务事件（如 fiscal-year:changed / competition:changed）。competition_id 为空
    时静默跳过，避免误广播到全局。
    """
    if competition_id is None:
        return
    # 同样延迟到提交后：财年推进等事件常与业务写入同处一个事务
    # replay=False：改造前该事件不入环，保持逐事件一致
    _after_commit(
        lambda: bus.deliver(event, data, f"comp-{competition_id}", replay=False)
    )


# ====================================================================
# 系统门禁事件（快照 / 强制暂停 / 回退）
# ====================================================================
def emit_system_event(
    event: str,
    payload: dict | None = None,
    *,
    replay: bool = True,
    room: str | None = None,
) -> None:
    """广播系统级事件（默认全局房间，进入环形缓冲以便断线重连补发）。

    - 全局事件会进入 `replay_since` 的环形缓冲，客户端断线重连后 `sync:replay`
      仍能补到「刚才被强制暂停 / 已回退完成」这类关键事件；
    - seq / ts 在事务提交后才分配，保证补发顺序与实际提交顺序一致。
    """
    base = dict(payload or {})

    def _deliver() -> None:
        data = dict(base)
        data.setdefault("ts", int(time.time() * 1000))
        if replay:
            # 取号（已有则沿用）+ 入环，同一临界区内原子完成
            bus.setdefault_seq_and_ring(event, data, room)
        else:
            # 只补 seq、不入环（与改造前 replay=False 的行为一致）
            bus.setdefault_seq(data)
        bus.deliver(event, data, room, replay=replay)

    _after_commit(_deliver)


def emit_system_restored(restored: dict) -> None:
    """回退成功后的一次性通知（客户端据此清缓存并整体重载）。"""
    emit_system_event(EVENT_SYSTEM_RESTORED, restored)


# ====================================================================
# permissions:changed
# ====================================================================
def emit_permissions_changed(user_id: int, permission_version: int) -> None:
    ts_ms = int(time.time() * 1000)
    payload = {
        "userId": user_id,
        "version": permission_version,
        "ts": ts_ms,
    }
    room = f"user-{user_id}"
    # 取号 + 入环原子完成（并发下环内 seq 严格单调）
    bus.assign_seq_and_ring(EVENT_PERMISSIONS_CHANGED, payload, room)
    bus.deliver(EVENT_PERMISSIONS_CHANGED, payload, room, replay=True)
