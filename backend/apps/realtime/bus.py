# -*- coding: utf-8 -*-
"""跨进程实时事件总线（C1-a）。

背景（《架构性运维约束整改简报》C1.4 + `docs/运维约束整改设计说明.md` §2）：
改造前的实时广播是**纯进程内**的——同步视图用
`asyncio.run_coroutine_threadsafe(sio.emit(...), loop)` 把事件投到 daphne 那个
唯一的 event loop（`emit.py` 顶部注释）。一旦把 `/api/*` 拆到多 worker 的 WSGI
进程，WSGI 里的 emit 就推不到 daphne 进程里的 socket 连接，因此必须有跨进程总线。

本模块是 `emit` 的**唯一投递出口**，按 `settings.REALTIME_BUS` 分派：

| 模式 | 谁用 | seq / 环形缓冲 | 投递 |
| --- | --- | --- | --- |
| `local` | 单进程（默认落点） | 进程内 deque（= 改造前） | 本进程 ASGI loop（= 改造前） |
| `hub` | daphne | 进程内（权威） | 本进程 ASGI loop；受理内部转发端点 |
| `forward` | WSGI/gunicorn | 无（由 hub 权威分配） | 独立后台线程 + 有界队列，POST 给 hub |
| `redis` | 两端 | Redis `INCR` + `ZSET` | `sio`（`AsyncRedisManager`）发布到 Redis |

设计约定（与设计说明 §2 的冻结契约一致）：

- 内部端点 `POST settings.REALTIME_INTERNAL_PATH`，请求体
  `{"event": str, "payload": obj, "room": str|null}`，请求头
  `X-Gipfel-Internal-Token`，成功返回 `{"ok": true, "seq": <int>}`；
  收到转发后**绝不再次转发**（防自环，见 `_transport(..., allow_forward=False)`）。
- forward 的发送在独立后台线程完成：**业务请求不等待**；hub 不可达/超时 → warning + 丢弃。
- redis 包缺失 / 未配置 URL / 连不上 → warning + 降级为 `forward`（配了
  `REALTIME_FORWARD_URL`）或 `local`，**绝不因缺依赖而崩**。

与冻结契约的两处**纯增量**可选字段（不改名、不加必填字段；不带这些字段时行为
完全等于契约原文，即「分配 seq + 入环 + 投递房间」）：
- 可选 `"replay": bool`（缺省 `true`）：由发送方表达「该事件在本地模式下是否入环」。
  没有它，hub 只能对所有转发事件一律入环，`emit_to_users` /
  `emit_resource_changed_to_users` / `emit_to_competition` / `widget-package:changed`
  这类「本地模式下不入环」的即时通知也会被写进环形缓冲，客户端重连 `sync:replay`
  时会重放并重复消费。带上该字段后 hub 侧环形缓冲成员与单进程 `local` 模式**逐事件一致**。
- 可选 `"action": "emit"|"kick"`（缺省 `"emit"`）：forward 模式下
  `kick_user_sessions()` 所在进程没有 socket 可断，必须由 hub 代执行
  （否则 I-01 顶号 / I-03 管理动作在进程分离部署下只发通知、不断开，安全修复失效）。

相对基线的行为改进（**唯一一处有意改动语义**，已获 Lead 批准）：
「取号 + 入环」改为同一临界区内的原子组合（`assign_seq_and_ring` /
`setdefault_seq_and_ring`），消除改造前 `_next_seq()` 与 `_push_ring()` 分处
`_seq_lock` / `_ring_lock` 的竞态——并发广播时环内 seq 可能非单调，使
`replay_since` 的二分定位偏移（重复/漏发）。详见证 in `_state_lock` 处注释与
`code_audit/U02-backend-identity.md`；环容量、事件结构、投递语义均未改动。

已知限制（不在本任务边界内，已上报 Lead）：
纯 `redis` 且**未配置** `REALTIME_FORWARD_URL` 的部署下，「强制断开 socket」需要 hub 侧
协助——python-socketio 的 pub-sub manager 只能按 `sid` 跨进程断开，而 WSGI 进程拿不到
daphne 侧的 sid（`AsyncRedisManager` 无跨进程 room participants）。因此 `kick_user_sessions()`
的收口是：**只要 `REALTIME_FORWARD_URL` 非空（redis 配置下 gipfel-wsgi unit 仍会设置它），
就把 `action="kick"` 经内部转发通道交给 hub 在本进程执行断开**；仅当该 URL 为空时才退化为
「跨进程送达 `auth:required` + 一次 warning」。
"""
from __future__ import annotations

import asyncio
import json
import logging
import queue
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from itertools import islice

logger = logging.getLogger("gipfel")

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
MODE_AUTO = "auto"
MODE_LOCAL = "local"
MODE_HUB = "hub"
MODE_FORWARD = "forward"
MODE_REDIS = "redis"
VALID_MODES = (MODE_AUTO, MODE_LOCAL, MODE_HUB, MODE_FORWARD, MODE_REDIS)

#: 内部转发请求头（值 = settings.REALTIME_INTERNAL_TOKEN）
INTERNAL_TOKEN_HEADER = "X-Gipfel-Internal-Token"
#: 内部端点只接受回环来源；`::ffff:127.0.0.1` 是 IPv4-mapped IPv6 的等价写法
LOOPBACK_ADDRS = frozenset({"127.0.0.1", "::1", "::ffff:127.0.0.1"})
#: settings.REALTIME_INTERNAL_PATH 缺失时的兜底（设计说明 §2 固定值）
DEFAULT_INTERNAL_PATH = "/_internal/realtime/emit"

DEFAULT_RING_MAX_LEN = 5000
#: forward 模式有界队列长度：满则丢弃该事件 + warning（绝不阻塞业务请求）
FORWARD_QUEUE_MAXSIZE = 10000
#: redis 键：序号（INCR）与环形缓冲（ZSET，score=seq）
REDIS_SEQ_KEY = "gipfel:realtime:seq"
REDIS_RING_KEY = "gipfel:realtime:ring"

#: 内部端点 action（纯增量可选字段，缺省 emit = 契约原文行为）
ACTION_EMIT = "emit"
ACTION_KICK = "kick"
#: 与 emit.EVENT_AUTH_REQUIRED 同值；此处写字面量避免模块级循环导入（emit → bus → emit）
EVENT_AUTH_REQUIRED = "auth:required"


# ---------------------------------------------------------------------------
# settings 读取（全部**每次调用时**读取：测试用 override_settings 即可切模式）
# ---------------------------------------------------------------------------
def _conf(name: str, default):
    from django.conf import settings

    value = getattr(settings, name, default)
    return default if value is None else value


def redis_url() -> str:
    return str(_conf("REALTIME_REDIS_URL", "") or "").strip()


def forward_url() -> str:
    return str(_conf("REALTIME_FORWARD_URL", "") or "").strip().rstrip("/")


def internal_path() -> str:
    path = str(_conf("REALTIME_INTERNAL_PATH", DEFAULT_INTERNAL_PATH) or "").strip()
    return path or DEFAULT_INTERNAL_PATH


def internal_token() -> str:
    return str(_conf("REALTIME_INTERNAL_TOKEN", "") or "")


def forward_timeout() -> float:
    try:
        value = float(_conf("REALTIME_FORWARD_TIMEOUT", 0.5))
    except (TypeError, ValueError):
        value = 0.5
    return value if value > 0 else 0.5


def ring_max_len() -> int:
    try:
        value = int(_conf("REALTIME_RING_MAX_LEN", DEFAULT_RING_MAX_LEN))
    except (TypeError, ValueError):
        value = DEFAULT_RING_MAX_LEN
    return value if value > 0 else DEFAULT_RING_MAX_LEN


# ---------------------------------------------------------------------------
# 进程内状态：seq + 环形缓冲（local/hub；redis 走 Redis）
# ---------------------------------------------------------------------------
#: **单把锁**同时保护「序号计数器 + 进程内环形缓冲」。
#:
#: 【对既有缺陷的修复】改造前 `emit._next_seq()` 与 `emit._push_ring()` 分处两把锁
#: （`_seq_lock` / `_ring_lock`）：线程 A 拿到 41、线程 B 拿到 42，若 B 先 append，
#: 环内 seq 变成 [.., 42, 41] 而**不再单调递增**，`replay_since` 的二分定位随之偏移
#: （客户端重连补发会重复或漏发）。详见 code_audit/U02-backend-identity.md。
#: 现在「取号 + 写入 data['seq'] + 入环」在同一临界区内完成（见 assign_seq_and_ring），
#: 因此环内 seq 严格单调、与取号顺序一致；redis 模式由 ZSET score=seq 天然有序。
_state_lock = threading.Lock()
_seq_value = 0
_event_ring: deque = deque()
_ring_seqs: deque = deque()


def _next_local_seq() -> int:
    global _seq_value
    with _state_lock:
        _seq_value += 1
        return _seq_value


def _local_current_seq() -> int:
    with _state_lock:
        return _seq_value


def _seq_of(value) -> int:
    """环形缓冲里 seq 的防御性归一：非 int（None/字符串）按 0 处理（避免 sync:replay 500）。"""
    return value if isinstance(value, int) else 0


def _ring_entry(event: str, data: dict, room: str | None) -> dict:
    """环形缓冲条目（结构与改造前逐字段一致）。"""
    return {
        "event": event,
        "data": data,
        "room": room,
        "seq": data.get("seq"),
        "ts_ms": data.get("ts"),
    }


def _push_local_ring_locked(entry: dict, max_len: int) -> None:
    """把条目写入进程内环（**调用方必须持 _state_lock**）。"""
    _event_ring.append(entry)
    _ring_seqs.append(entry.get("seq"))
    # 手动裁剪等价于改造前的 deque(maxlen=...)，但长度可随 settings 动态生效
    while len(_event_ring) > max_len:
        _event_ring.popleft()
        _ring_seqs.popleft()


def _push_local_ring(entry: dict) -> None:
    max_len = ring_max_len()
    with _state_lock:
        _push_local_ring_locked(entry, max_len)


def _bisect_right_seqs(target: int) -> int:
    """在单调递增的 _ring_seqs 上二分，返回首个 seq > target 的下标（调用方持 _state_lock）。"""
    lo, hi = 0, len(_ring_seqs)
    while lo < hi:
        mid = (lo + hi) // 2
        if _seq_of(_ring_seqs[mid]) <= target:
            lo = mid + 1
        else:
            hi = mid
    return lo


def _replay_local_since(last_seq: int) -> list[dict]:
    try:
        target = int(last_seq)
    except (TypeError, ValueError):
        target = 0
    with _state_lock:
        idx = _bisect_right_seqs(target)
        return list(islice(_event_ring, idx, None))


# ---------------------------------------------------------------------------
# Redis 客户端（延迟导入 + 探测缓存；缺包/连不上 → None + warning）
# ---------------------------------------------------------------------------
_redis_lock = threading.Lock()
_redis_module = None
_redis_import_failed = False
_redis_client = None
_redis_client_url: str | None = None
_redis_probe_failed: set[str] = set()


def _load_redis_module():
    """延迟导入 redis 包；缺包返回 None（**不得**影响进程启动/业务请求）。"""
    global _redis_module, _redis_import_failed
    if _redis_module is not None:
        return _redis_module
    if _redis_import_failed:
        return None
    try:
        import redis  # noqa: PLC0415  （延迟导入：缺包时本项目零依赖可用）
    except Exception:  # noqa: BLE001
        _redis_import_failed = True
        logger.warning(
            "实时总线：REALTIME_BUS=redis 但 redis 包不可用 → 降级为进程内/转发（不崩）",
            exc_info=True,
        )
        return None
    _redis_module = redis
    return redis


def _get_redis_client(url: str | None = None):
    """返回同步 Redis 客户端（仅用于 INCR/ZSET）；不可用 → None 并只告警一次。"""
    global _redis_client, _redis_client_url
    url = url or redis_url()
    if not url:
        return None
    module = _load_redis_module()
    if module is None:
        return None
    with _redis_lock:
        if _redis_client is not None and _redis_client_url == url:
            return _redis_client
        if url in _redis_probe_failed:
            return None
        try:
            client = module.Redis.from_url(
                url,
                socket_connect_timeout=0.5,
                socket_timeout=0.5,
                decode_responses=True,
            )
            client.ping()
        except Exception:  # noqa: BLE001
            _redis_probe_failed.add(url)
            logger.warning(
                "实时总线：Redis 连接失败（%s）→ 降级为进程内/转发（实时功能不停摆）",
                url,
                exc_info=True,
            )
            return None
        _redis_client = client
        _redis_client_url = url
        return client


# ---------------------------------------------------------------------------
# 模式解析
# ---------------------------------------------------------------------------
def resolve_mode() -> str:
    """解析当前生效的总线模式。

    - `auto`：配了 `REALTIME_REDIS_URL` 且 Redis 可用 → `redis`，否则 `local`
      （零配置 = 与改造前完全一致）；
    - `redis`：不可用（缺包/未配 URL/连不上）→ `forward`（配了 FORWARD_URL）或 `local`；
    - 其余（`local`/`hub`/`forward`）原样返回。
    """
    configured = str(_conf("REALTIME_BUS", MODE_AUTO) or MODE_AUTO).strip().lower()
    if configured not in VALID_MODES:
        logger.warning("实时总线：REALTIME_BUS=%r 非法 → 按 local 处理", configured)
        configured = MODE_LOCAL

    if configured == MODE_AUTO:
        url = redis_url()
        if url:
            if _get_redis_client(url) is not None:
                return MODE_REDIS
            logger.warning(
                "实时总线：REALTIME_BUS=auto 且配置了 REALTIME_REDIS_URL 但 Redis 不可用 → 落点 local"
            )
        return MODE_LOCAL

    if configured == MODE_REDIS:
        url = redis_url()
        if url and _get_redis_client(url) is not None:
            return MODE_REDIS
        fallback = MODE_FORWARD if forward_url() else MODE_LOCAL
        logger.warning(
            "实时总线：REALTIME_BUS=redis 不可用（url=%r）→ 降级为 %s（实时功能不停摆）",
            url,
            fallback,
        )
        return fallback

    return configured


def is_hub_mode() -> bool:
    """是否由本进程持有「权威 seq + 环形缓冲」（local/hub 都是；redis 也持有，只是在 Redis 里）。"""
    return resolve_mode() in (MODE_LOCAL, MODE_HUB, MODE_REDIS)


# ---------------------------------------------------------------------------
# seq / 环形缓冲（emit 侧调用；语义与改造前一致，仅存储介质随模式变化）
# ---------------------------------------------------------------------------
def allocate_seq() -> int:
    """分配下一个序号。

    - `local`/`hub`：进程内单调计数（= 改造前）；
    - `redis`：Redis `INCR`（跨进程唯一单调）；
    - `forward`：本进程计数**不权威**（hub 收到转发后重新分配并覆盖）。
    """
    if resolve_mode() == MODE_REDIS:
        client = _get_redis_client()
        if client is not None:
            try:
                return int(client.incr(REDIS_SEQ_KEY))
            except Exception:  # noqa: BLE001
                logger.warning("实时总线：Redis INCR 失败 → 本次改用进程内序号", exc_info=True)
    return _next_local_seq()


def current_seq() -> int:
    """当前序号（`server_seq` 的实现：forward 进程返回本地计数，不权威）。"""
    if resolve_mode() == MODE_REDIS:
        client = _get_redis_client()
        if client is not None:
            try:
                return int(client.get(REDIS_SEQ_KEY) or 0)
            except Exception:  # noqa: BLE001
                logger.warning("实时总线：Redis GET 序号失败 → 本次返回进程内序号", exc_info=True)
    return _local_current_seq()


def push_ring(event: str, data: dict, room: str | None) -> None:
    """把事件写入重放环形缓冲（forward 进程不入环：权威环在 hub 进程）。

    广播路径请优先用 `assign_seq_and_ring` / `setdefault_seq_and_ring`：
    它们把「取号 + 入环」放在同一临界区，环内 seq 必然单调（见模块内注释与
    code_audit/U02-backend-identity.md）。本函数保留给「序号已确定」的调用方
    （hub 受理转发时也可用，但已改用原子版本）。
    """
    entry = _ring_entry(event, data, room) if isinstance(data, dict) else {
        "event": event,
        "data": data,
        "room": room,
        "seq": None,
        "ts_ms": None,
    }
    mode = resolve_mode()
    if mode == MODE_REDIS:
        client = _get_redis_client()
        if client is not None:
            try:
                _redis_push_ring(client, entry)
                return
            except Exception:  # noqa: BLE001
                logger.warning("实时总线：Redis ZADD 失败 → 本次退化为进程内环形缓冲", exc_info=True)
    elif mode == MODE_FORWARD:
        return
    _push_local_ring(entry)


# ---------------------------------------------------------------------------
# 「取号 + 入环」原子组合（修复既有并发缺陷，见文件顶部 _state_lock 注释）
# ---------------------------------------------------------------------------
def _assign_seq_in_data_locked(data: dict, *, overwrite: bool):
    """在 _state_lock 内取号并写入 `data["seq"]`（**调用方必须持锁**）。

    - `overwrite=True`：总是分配新号并覆盖（= 改造前 `data["seq"] = _next_seq()`）；
    - `overwrite=False`：`data` 已有 seq 时沿用且**不消耗新号**（= 改造前
      `data.setdefault("seq", _next_seq())` 的短路语义）。
    """
    global _seq_value
    if overwrite or "seq" not in data:
        _seq_value += 1
        data["seq"] = _seq_value
    return data.get("seq")


def _seq_and_ring(event: str, data, room: str | None, *, overwrite: bool, replay: bool):
    if not isinstance(data, dict):
        # 非 dict 负载：仍消耗一个序号（与改造前 `_next_seq()` 的调用一致），不入环
        return allocate_seq()
    mode = resolve_mode()
    if mode == MODE_REDIS:
        # redis：INCR 天然原子；ZSET score=seq 天然按序号有序
        # 注意先判短路再取号：已有 seq 且不覆盖时**不得**消耗新号（= setdefault 语义）
        if not overwrite and "seq" in data:
            seq = data.get("seq")
        else:
            seq = allocate_seq()
            data["seq"] = seq
        if replay:
            client = _get_redis_client()
            if client is not None:
                try:
                    _redis_push_ring(client, _ring_entry(event, data, room))
                except Exception:  # noqa: BLE001
                    logger.warning("实时总线：Redis ZADD 失败 → 本次不入环", exc_info=True)
        return seq
    max_len = ring_max_len()
    with _state_lock:
        seq = _assign_seq_in_data_locked(data, overwrite=overwrite)
        if replay and mode != MODE_FORWARD:
            # forward 进程不入环（权威环在 hub）；local/hub 在同一临界区内入环
            _push_local_ring_locked(_ring_entry(event, data, room), max_len)
    return seq


def assign_seq(data: dict):
    """分配序号并写入 `data["seq"]`（**不入环**；总是覆盖）。

    对应改造前「带序号但不进重放环」的调用形态（如 `emit_resource_changed_to_users`）。
    """
    if not isinstance(data, dict):
        return allocate_seq()
    if resolve_mode() == MODE_REDIS:
        data["seq"] = allocate_seq()
        return data["seq"]
    with _state_lock:
        return _assign_seq_in_data_locked(data, overwrite=True)


def setdefault_seq(data: dict):
    """同 `assign_seq`，但 `data` 已有 seq 时沿用且不消耗新号（**不入环**）。"""
    if not isinstance(data, dict):
        return allocate_seq()
    if resolve_mode() == MODE_REDIS:
        if "seq" not in data:
            data["seq"] = allocate_seq()
        return data.get("seq")
    with _state_lock:
        return _assign_seq_in_data_locked(data, overwrite=False)


def assign_seq_and_ring(event: str, data, room: str | None = None) -> int:
    """**原子地**「取号 + 写入 data['seq'] + 入环」，返回分配的序号。

    修复改造前 `_next_seq()` / `_push_ring()` 分处两把锁导致的环内 seq 非单调
    （详见本模块 `_state_lock` 处注释）。语义：
    - local/hub：同一临界区内取号与入环 → 环内 seq 严格单调递增；
    - forward：只取号写 `data["seq"]`（序号不权威，hub 收到后会覆盖），不入环；
    - redis：`INCR` + `ZADD`（score=seq）。
    """
    return _seq_and_ring(event, data, room, overwrite=True, replay=True)


def setdefault_seq_and_ring(event: str, data, room: str | None = None) -> int:
    """同 `assign_seq_and_ring`，但 `data` 已有 seq 时沿用调用方序号且不消耗新号。

    对应改造前 `emit_system_event` 的 `data.setdefault("seq", _next_seq())` 语义。
    """
    return _seq_and_ring(event, data, room, overwrite=False, replay=True)


def _redis_push_ring(client, entry: dict) -> None:
    """ZSET 环形缓冲：score=seq，超长按 rank 裁剪（设计说明 §2）。"""
    member = json.dumps(entry, ensure_ascii=False, sort_keys=True, default=str)
    seq = entry.get("seq")
    score = seq if isinstance(seq, int) else int(time.time() * 1000)
    pipe = client.pipeline()
    pipe.zadd(REDIS_RING_KEY, {member: score})
    # 保留最新 ring_max_len 条：删除 rank 0..-(max+1)
    pipe.zremrangebyrank(REDIS_RING_KEY, 0, -(ring_max_len() + 1))
    pipe.execute()


def replay_since(last_seq: int) -> list[dict]:
    """返回 seq > last_seq 的重放事件（条目结构与改造前一致）。"""
    mode = resolve_mode()
    if mode == MODE_REDIS:
        client = _get_redis_client()
        if client is not None:
            try:
                return _redis_replay_since(client, last_seq)
            except Exception:  # noqa: BLE001
                logger.warning("实时总线：Redis ZRANGEBYSCORE 失败 → 本次返回空重放", exc_info=True)
                return []
    if mode == MODE_FORWARD:
        # 权威环在 hub 进程；forward 进程不服务 sync:replay，返回空而不是伪造历史
        return []
    return _replay_local_since(last_seq)


def _redis_replay_since(client, last_seq: int) -> list[dict]:
    try:
        target = int(last_seq)
    except (TypeError, ValueError):
        target = 0
    raw = client.zrangebyscore(REDIS_RING_KEY, f"({target}", "+inf")
    events: list[dict] = []
    for item in raw:
        try:
            parsed = json.loads(item)
        except (TypeError, ValueError):
            continue
        if isinstance(parsed, dict):
            events.append(parsed)
    return events


# ---------------------------------------------------------------------------
# 投递出口：transport 层
# ---------------------------------------------------------------------------
def _deliver_local(event: str, data, room: str | None) -> None:
    """把事件投到本进程已注册的 ASGI loop（= 改造前的 `emit._emit_sio` 主体）。

    延迟导入 emit：emit → bus 是模块级依赖，反向只能延迟，避免循环导入。
    """
    try:
        from . import emit as emit_mod

        emit_mod._deliver_local(event, data, room)
    except Exception:  # noqa: BLE001
        logger.debug("实时总线：本进程投递失败 event=%s", event, exc_info=True)


def _transport(event: str, data, room: str | None, *, replay: bool, allow_forward: bool) -> None:
    mode = resolve_mode()
    if mode == MODE_FORWARD and allow_forward:
        _forward_enqueue(
            {"event": event, "payload": data, "room": room, "replay": bool(replay)}
        )
        return
    if mode == MODE_REDIS:
        _publish_via_sio(event, data, room)
        return
    # local / hub / （forward 但 forbid：受理转发时绝不 HTTP 再转发，防自环）
    _deliver_local(event, data, room)


def deliver(event: str, data, room: str | None = None, *, replay: bool = False) -> None:
    """emit 的唯一投递出口（transport 层，不含 seq/入环）。

    - seq 分配与入环**不在这里**：由调用方（`emit` 或 hub 受理转发）显式完成，
      从而与改造前逐行等价；
    - `replay` 仅在 forward 模式下随转发体发给 hub，用于让 hub 侧的环形缓冲成员
      与单进程 `local` 模式逐事件一致；缺省 False（= 纯即时通知，本地模式下也不入环）。
    """
    _transport(event, data, room, replay=replay, allow_forward=True)


def _publish_via_sio(event: str, data, room: str | None) -> None:
    """redis 模式：经带 AsyncRedisManager 的 `sio` 发布（跨进程由 Redis 广播）。"""
    try:
        from .gateway import sio
    except Exception:  # noqa: BLE001
        logger.warning("实时总线：redis 模式下无法导入 sio → 丢弃事件 %s", event, exc_info=True)
        return

    async def _do_emit():
        try:
            if room:
                await sio.emit(event, data, room=room)
            else:
                await sio.emit(event, data)
        except Exception:  # noqa: BLE001
            logger.warning("实时总线：redis 发布失败 event=%s", event, exc_info=True)

    # 1) 当前就在某个 loop 内（socket 处理器）：直接 create_task
    try:
        cur_loop = asyncio.get_running_loop()
    except RuntimeError:
        cur_loop = None
    if cur_loop is not None:
        cur_loop.create_task(_do_emit())
        return

    # 2) 已注册的 ASGI loop（daphne 进程里的同步视图/信号）
    try:
        from . import emit as emit_mod

        asgi_loop = emit_mod._get_loop_safe()
    except Exception:  # noqa: BLE001
        asgi_loop = None
    if asgi_loop is not None and asgi_loop.is_running():
        try:
            asyncio.run_coroutine_threadsafe(_do_emit(), asgi_loop)
            return
        except Exception:  # noqa: BLE001
            logger.warning("实时总线：向 ASGI loop 投递发布任务失败 event=%s", event, exc_info=True)

    # 3) WSGI 进程：用总线自己的后台 loop 线程发布
    publish_loop = _ensure_publish_loop()
    if publish_loop is None:
        logger.warning("实时总线：无可用 event loop → 丢弃事件 %s", event)
        return
    try:
        asyncio.run_coroutine_threadsafe(_do_emit(), publish_loop)
    except Exception:  # noqa: BLE001
        logger.warning("实时总线：向发布 loop 投递失败 event=%s", event, exc_info=True)


# --- redis 模式在 WSGI 进程里发布事件用的私有 loop 线程 -----------------------
_publish_lock = threading.Lock()
_publish_loop: asyncio.AbstractEventLoop | None = None


def _ensure_publish_loop() -> asyncio.AbstractEventLoop | None:
    global _publish_loop
    with _publish_lock:
        if _publish_loop is not None and not _publish_loop.is_closed():
            return _publish_loop
        try:
            loop = asyncio.new_event_loop()
            thread = threading.Thread(
                target=_run_publish_loop, args=(loop,), name="gipfel-realtime-publish", daemon=True
            )
            thread.start()
            deadline = time.monotonic() + 2.0
            while not loop.is_running() and time.monotonic() < deadline:
                time.sleep(0.005)
            if not loop.is_running():
                logger.warning("实时总线：发布 loop 未能启动 → redis 模式投递不可用")
                return None
            _publish_loop = loop
            return loop
        except Exception:  # noqa: BLE001
            logger.warning("实时总线：创建发布 loop 失败", exc_info=True)
            return None


def _run_publish_loop(loop: asyncio.AbstractEventLoop) -> None:
    asyncio.set_event_loop(loop)
    loop.run_forever()


# ---------------------------------------------------------------------------
# forward：独立后台线程 + 有界队列（业务请求不等待）
# ---------------------------------------------------------------------------
_forward_lock = threading.Lock()
_forward_queue: queue.Queue | None = None
_forward_worker: threading.Thread | None = None
_forward_pending = 0


def _ensure_forward_queue() -> queue.Queue:
    """惰性创建有界队列 + 后台发送线程（daemon，随进程退出）。"""
    global _forward_queue, _forward_worker
    if _forward_queue is not None:
        return _forward_queue
    _forward_queue = queue.Queue(maxsize=FORWARD_QUEUE_MAXSIZE)
    _forward_worker = threading.Thread(
        target=_forward_worker_loop,
        args=(_forward_queue,),
        name="gipfel-realtime-forward",
        daemon=True,
    )
    _forward_worker.start()
    return _forward_queue


def _forward_enqueue(item: dict) -> None:
    """非阻塞入队；队列满/异常 → warning + 丢弃（绝不抛给业务）。"""
    global _forward_pending
    try:
        with _forward_lock:
            q = _ensure_forward_queue()
            q.put_nowait(item)
            _forward_pending += 1
    except queue.Full:
        logger.warning(
            "实时总线：转发队列已满（maxsize=%s）→ 丢弃事件 %s（业务请求不受影响）",
            FORWARD_QUEUE_MAXSIZE,
            item.get("event"),
        )
    except Exception:  # noqa: BLE001
        logger.warning("实时总线：转发入队失败 → 丢弃事件 %s", item.get("event"), exc_info=True)


def _forward_worker_loop(q: queue.Queue) -> None:
    global _forward_pending
    while True:
        item = q.get()
        try:
            if item is None:  # 停机哨兵
                return
            _post_forward(item)
        except Exception:  # noqa: BLE001
            logger.warning("实时总线：转发线程异常（已忽略）", exc_info=True)
        finally:
            try:
                q.task_done()
            except Exception:  # noqa: BLE001
                pass
            with _forward_lock:
                if _forward_pending > 0:
                    _forward_pending -= 1


def _post_forward(item: dict) -> None:
    """把一条事件 POST 给 hub；失败/超时只 warning，绝不抛。"""
    base = forward_url()
    if not base:
        logger.warning(
            "实时总线：REALTIME_BUS=forward 但未配置 REALTIME_FORWARD_URL → 丢弃事件 %s",
            item.get("event"),
        )
        return
    url = base + internal_path()
    body = json.dumps(item, ensure_ascii=False, default=str).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            INTERNAL_TOKEN_HEADER: internal_token(),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=forward_timeout()) as resp:
            resp.read()
            if int(getattr(resp, "status", 200)) != 200:
                logger.warning(
                    "实时总线：hub 返回非 200（%s）event=%s", resp.status, item.get("event")
                )
    except urllib.error.HTTPError as exc:
        logger.warning(
            "实时总线：内部转发被拒绝 HTTP %s event=%s url=%s", exc.code, item.get("event"), url
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "实时总线：内部转发失败（hub 不可达/超时）→ 丢弃事件 event=%s url=%s",
            item.get("event"),
            url,
            exc_info=True,
        )


def forward_pending() -> int:
    """未发送完成的转发条数（测试/诊断用）。"""
    with _forward_lock:
        return _forward_pending


def flush_forward(timeout: float = 2.0) -> bool:
    """等待转发队列排空（测试/关停辅助；业务代码无需调用）。"""
    deadline = time.monotonic() + max(0.0, float(timeout))
    while time.monotonic() < deadline:
        if forward_pending() <= 0:
            return True
        time.sleep(0.005)
    return forward_pending() <= 0


# ---------------------------------------------------------------------------
# 踢人（跨进程）
# ---------------------------------------------------------------------------
_redis_kick_warned = False


def kick_user_sessions(user_id: int | None, reason: str = "token_version_mismatch") -> None:
    """模式感知的「踢掉某账号全部会话」。

    - `local`/`hub`：本进程先发 `auth:required` 再逐个 `sio.disconnect`（= 改造前）；
    - `forward`，或 `redis` 且配了 `REALTIME_FORWARD_URL`：POST 内部端点
      （`action=kick`）由 hub 代执行断开——否则进程分离后 I-01 顶号 / I-03 管理动作
      只发通知、不断开（安全修复失效）；
    - 纯 `redis` 且无 forward URL：只能跨进程送达 `auth:required`，强制断开需 hub 侧
      协助（已知限制，见模块 docstring）。
    """
    global _redis_kick_warned
    if user_id is None:
        return
    uid = int(user_id)
    mode = resolve_mode()
    if mode == MODE_FORWARD or (mode == MODE_REDIS and forward_url()):
        _forward_enqueue(
            {
                "event": EVENT_AUTH_REQUIRED,
                "payload": {"reason": reason},
                "room": f"user-{uid}",
                "replay": False,
                "action": ACTION_KICK,
                "userId": uid,
                "reason": reason,
            }
        )
        return
    if mode == MODE_REDIS:
        if not _redis_kick_warned:
            _redis_kick_warned = True
            logger.warning(
                "实时总线：redis 模式下未配置 REALTIME_FORWARD_URL → 无法跨进程强制断开 socket，"
                "仅下发 auth:required（user=%s）",
                uid,
            )
        _publish_via_sio(EVENT_AUTH_REQUIRED, {"reason": reason}, f"user-{uid}")
        return
    _kick_local(uid, reason)


def _kick_local(user_id: int, reason: str) -> None:
    """本进程踢人（复用 emit 的 loop 投递；loop 未就绪时静默降级）。"""
    from . import emit as emit_mod

    emit_mod._run_coro_on_loop(emit_mod.kick_user_sessions_async(user_id, reason))


# ---------------------------------------------------------------------------
# hub 侧：受理内部转发（分配 seq / 入环 / 投递；绝不再次转发）
# ---------------------------------------------------------------------------
def handle_inbound(
    event: str, payload, room: str | None = None, *, replay: bool = True
) -> int:
    """受理一条转发事件，返回分配的权威 seq。

    规则（与单进程 local 模式逐事件等价）：
    - `replay=True`（缺省，= 契约原文）：取号 + 覆盖 payload 的 seq + 入环（原子，见
      `assign_seq_and_ring`），可被 `sync:replay` 补发；
    - `replay=False` 且 payload 自带 `seq`：用本进程序号覆盖（对应
      `emit_resource_changed_to_users`：带号但不入环）；
    - `replay=False` 且 payload 无 `seq`：payload 原样透传、不入环（对应 `emit_to_users`）；
    - 投递只走本进程/Redis，**绝不 HTTP 再转发**（`allow_forward=False`，防自环）。
    """
    if not isinstance(payload, dict):
        seq = allocate_seq()
        _transport(event, payload, room, replay=replay, allow_forward=False)
        return seq
    if replay:
        seq = assign_seq_and_ring(event, payload, room)
    elif "seq" in payload:
        seq = assign_seq(payload)
    else:
        seq = allocate_seq()
    _transport(event, payload, room, replay=replay, allow_forward=False)
    return seq


def handle_inbound_kick(user_id: int, reason: str) -> int:
    """hub 侧代 forward/redis 进程执行踢人：先发 `auth:required`、再断开房间内每个 sid。

    受理端（hub）才有 socket 与 ASGI loop；本进程无 loop 时 `_kick_local` 静默降级
    （与改造前一致），绝不抛。
    """
    seq = allocate_seq()
    _kick_local(int(user_id), reason)
    return seq


# ---------------------------------------------------------------------------
# 测试辅助
# ---------------------------------------------------------------------------
def reset_state_for_tests() -> None:
    """清空进程内 seq / 环形缓冲 / 转发队列 / Redis 探测缓存（仅测试使用）。"""
    global _seq_value, _forward_queue, _forward_worker, _forward_pending
    global _redis_module, _redis_import_failed, _redis_client, _redis_client_url
    global _redis_kick_warned
    with _state_lock:
        _seq_value = 0
        _event_ring.clear()
        _ring_seqs.clear()
    with _forward_lock:
        old_queue, _forward_queue, _forward_worker, _forward_pending = (
            _forward_queue,
            None,
            None,
            0,
        )
    if old_queue is not None:
        try:
            old_queue.put_nowait(None)  # 让旧 worker 退出
        except Exception:  # noqa: BLE001
            pass
    with _redis_lock:
        _redis_module = None
        _redis_import_failed = False
        _redis_client = None
        _redis_client_url = None
        _redis_probe_failed.clear()
    _redis_kick_warned = False
