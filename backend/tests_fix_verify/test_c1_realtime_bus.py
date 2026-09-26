# -*- coding: utf-8 -*-
"""C1-a 验证：跨进程实时事件总线（`apps/realtime/bus.py` + 内部转发端点）。

对应交付：
- `docs/运维约束整改设计说明.md` §2（模式语义、内部端点契约、降级要求）；
- 《架构性运维约束整改简报》C1.4/C1.5（进程分离后实时广播仍须到达、不得因缺依赖而崩）。

本文件覆盖：
1. `local` 模式：seq 单调、`replay_since` 二分语义不变、载荷契约不变、`_after_commit`
   延迟提交语义不变（回滚不广播、提交后分配 seq）、本进程 ASGI loop 投递不变；
2. 环成员对照：`emit_to_users` / `emit_resource_changed_to_users` / `emit_to_competition`
   **不入环**（local 语义），而 forward 模式下 hub 侧环成员与 local 逐事件一致（`replay` 生效）；
3. `forward` 模式：签名头 / 路径 / 负载断言（本地假 hub）、业务请求不等待、hub 不可达不抛、
   队列满丢弃不抛；
4. 内部端点：回环 + 令牌（错令牌 403、非回环 403、缺令牌 403）、`csrf_exempt`（强制 CSRF 校验
   下仍 200）、仅 POST、`action=kick` 由 hub 代执行断开、**受理后不再转发**（防自环）、
   暂停期间不被 423 拦（EXEMPT_PREFIXES）；
5. 缺 redis 包 / redis 连不上：`resolve_mode` 回落且不抛，gateway 不挂 client_manager；
6. redis 逻辑：`INCR` 序号 + `ZSET` 环形缓冲（假 Redis 客户端，无需真机）。
"""
from __future__ import annotations

import asyncio
import contextlib
import http.server
import inspect
import json
import socket
import threading
import time
from unittest import mock

from django.test import Client, TestCase

from apps.realtime import bus
from apps.realtime import emit as emit_mod

#: 冻结的 emit 公开 API 签名（改造不得改名/改签名，见设计说明 §2）
FROZEN_SIGNATURES = {
    "emit_resource_changed": "(resource: str, record_id: int | None, competition_id: int | None, action: str, *, ids: list[int] | None = None) -> None",
    "emit_resource_changed_to_users": "(resource: str, record_id: int, user_ids, action: str, competition_id: int | None = None, ids: list[int] | None = None) -> None",
    "emit_to_users": "(user_ids, event: str, data) -> None",
    "emit_to_competition": "(competition_id: int | None, event: str, data) -> None",
    "emit_system_event": "(event: str, payload: dict | None = None, *, replay: bool = True, room: str | None = None) -> None",
    "emit_system_restored": "(restored: dict) -> None",
    "emit_permissions_changed": "(user_id: int, permission_version: int) -> None",
    "kick_user_sessions": "(user_id: int, reason: str = 'token_version_mismatch') -> None",
    "replay_since": "(last_seq: int) -> list[dict]",
    "server_seq": "() -> int",
    "register_loop": "(loop: asyncio.AbstractEventLoop) -> None",
}


def _signature_text(func) -> str:
    """与 Python 版本无关的签名描述（注解文本 / 关键字-only 标记 / 默认值）。

    直接 `str(inspect.signature(...))` 会把 `from __future__ import annotations`
    的注解渲染成带引号的字符串，跨版本不稳定；这里统一取注解原文比较。
    """
    signature = inspect.signature(func)
    parameters = list(signature.parameters.values())
    var_positional = any(p.kind is p.VAR_POSITIONAL for p in parameters)
    parts: list[str] = []
    marked_kwonly = False
    for param in parameters:
        if param.kind is param.VAR_POSITIONAL:
            parts.append(f"*{param.name}")
            marked_kwonly = True
            continue
        if param.kind is param.VAR_KEYWORD:
            parts.append(f"**{param.name}")
            continue
        if param.kind is param.KEYWORD_ONLY and not marked_kwonly and not var_positional:
            parts.append("*")
            marked_kwonly = True
        annotation = param.annotation
        if isinstance(annotation, str):
            annotation_text = annotation
        elif annotation is param.empty:
            annotation_text = ""
        else:
            annotation_text = getattr(annotation, "__name__", str(annotation))
        text = param.name + (f": {annotation_text}" if annotation_text else "")
        if param.default is not param.empty:
            text += f" = {param.default!r}"
        parts.append(text)
    returned = signature.return_annotation
    if isinstance(returned, str):
        return_text = returned
    elif returned is signature.empty:
        return_text = ""
    else:
        return_text = getattr(returned, "__name__", str(returned))
    return f"({', '.join(parts)}) -> {return_text}"


# ---------------------------------------------------------------------------
# 测试替身
# ---------------------------------------------------------------------------
class _FakeSio:
    """记录 emit 调用的假 sio（与 tests_fix_verify/test_i01 同形）。"""

    def __init__(self):
        self.emitted: list[tuple[str, dict, str | None]] = []
        self.disconnected: list[str] = []
        self.manager = mock.Mock()
        self.manager.get_participants = lambda namespace, room: iter(())

    async def emit(self, event, data=None, room=None):
        self.emitted.append((event, data, room))

    async def disconnect(self, sid, namespace=None, ignore_queue=False):
        self.disconnected.append(sid)


class _LoopThread:
    """后台线程里的真实 asyncio loop（供同步侧的 run_coroutine_threadsafe 投递）。"""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        deadline = time.time() + 2
        while not self.loop.is_running() and time.time() < deadline:
            time.sleep(0.005)

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def close(self):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=2)


class _FakeHub:
    """本地假 hub：记录内部转发请求（路径 / 头 / JSON 体）。"""

    def __init__(self, delay: float = 0.0):
        self.delay = delay
        self.requests: list[dict] = []
        self._lock = threading.Lock()
        hub = self

        class _Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                if hub.delay:
                    time.sleep(hub.delay)
                try:
                    body = json.loads(raw.decode("utf-8") or "{}")
                except ValueError:
                    body = {"__invalid__": raw.decode("utf-8", "replace")}
                with hub._lock:
                    hub.requests.append(
                        {
                            "path": self.path,
                            # 小写归一：urllib 会把头名 capitalize()，断言时用小写查更稳
                            "headers": {k.lower(): v for k, v in self.headers.items()},
                            "body": body,
                        }
                    )
                payload = json.dumps({"ok": True, "seq": 1}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):  # 静音
                return

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class _FakeRedisPipeline:
    def __init__(self, client):
        self._client = client
        self._ops: list[tuple] = []

    def zadd(self, key, mapping):
        self._ops.append(("zadd", key, mapping))
        return self

    def zremrangebyrank(self, key, start, stop):
        self._ops.append(("zremrangebyrank", key, start, stop))
        return self

    def execute(self):
        for op in self._ops:
            if op[0] == "zadd":
                self._client.zadd(op[1], op[2])
            else:
                self._client.zremrangebyrank(op[1], op[2], op[3])
        self._ops.clear()


class _FakeRedisClient:
    """最小 Redis 替身：INCR/GET + ZSET（zadd / zremrangebyrank / zrangebyscore）。"""

    def __init__(self):
        self.kv: dict[str, int] = {}
        self.zsets: dict[str, dict[str, float]] = {}

    def ping(self):
        return True

    def incr(self, key):
        self.kv[key] = int(self.kv.get(key, 0)) + 1
        return self.kv[key]

    def get(self, key):
        return self.kv.get(key)

    def pipeline(self):
        return _FakeRedisPipeline(self)

    def zadd(self, key, mapping):
        store = self.zsets.setdefault(key, {})
        store.update(mapping)
        return len(mapping)

    def zremrangebyrank(self, key, start, stop):
        store = self.zsets.get(key) or {}
        ordered = [m for m, _ in sorted(store.items(), key=lambda kv: (kv[1], kv[0]))]
        n = len(ordered)
        s, e = int(start), int(stop)
        if s < 0:
            s += n
        if e < 0:
            e += n
        s = max(0, s)
        e = min(n - 1, e)
        for member in ordered[s : e + 1]:
            store.pop(member, None)
        return 0

    def zrangebyscore(self, key, minimum, maximum):
        store = self.zsets.get(key) or {}
        raw = str(minimum)
        exclusive = raw.startswith("(")
        lower = float(raw.lstrip("("))
        items = sorted(store.items(), key=lambda kv: (kv[1], kv[0]))
        out = []
        for member, score in items:
            if (exclusive and score <= lower) or (not exclusive and score < lower):
                continue
            out.append(member)
        return out


class _FakeRedisModule:
    """假的 `redis` 模块（`redis.Redis.from_url` → 替身客户端）。"""

    def __init__(self, client):
        outer = self

        class Redis:
            @staticmethod
            def from_url(url, **kwargs):
                return outer._client

        self._client = client
        self.Redis = Redis


def _closed_port() -> int:
    """返回一个刚被释放的本地端口（连接必然被拒绝 = 「hub 不可达」）。"""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class _BusTestCase(TestCase):
    """每个用例前清空总线进程内状态，避免 seq/环/队列跨用例串味。"""

    def setUp(self):
        bus.reset_state_for_tests()

    def tearDown(self):
        bus.reset_state_for_tests()

    @contextlib.contextmanager
    def _fake_hub(self, delay: float = 0.0):
        hub = _FakeHub(delay=delay)
        try:
            yield hub
        finally:
            hub.close()

    @contextlib.contextmanager
    def _fake_redis_env(self, client, ring_max_len: int = 5000):
        module = _FakeRedisModule(client)
        with mock.patch.object(bus, "_load_redis_module", return_value=module), self.settings(
            REALTIME_BUS="redis",
            REALTIME_REDIS_URL="redis://fake:6379/0",
            REALTIME_RING_MAX_LEN=ring_max_len,
            REALTIME_FORWARD_URL="",
        ):
            yield client


# ===========================================================================
# 1. local 模式（零配置 = 与改造前等价）
# ===========================================================================
class BusLocalModeTests(_BusTestCase):
    def test_auto_without_redis_falls_back_to_local(self):
        with self.settings(REALTIME_BUS="auto", REALTIME_REDIS_URL=""):
            self.assertEqual(bus.resolve_mode(), "local")
            self.assertEqual(bus.MODE_LOCAL, "local")

    def test_local_seq_is_monotonic_and_replay_since_semantics_unchanged(self):
        with self.captureOnCommitCallbacks(execute=True):
            emit_mod.emit_resource_changed("companies", 11, 7, "updated")
            emit_mod.emit_resource_changed("companies", 12, 7, "created")
            emit_mod.emit_resource_changed("industry-types", 3, None, "updated")

        events = bus.replay_since(0)
        self.assertEqual(len(events), 3)
        seqs = [e["seq"] for e in events]
        self.assertEqual(seqs, [1, 2, 3], "local 模式 seq 必须从 1 起单调递增")
        self.assertEqual(emit_mod.server_seq(), 3)
        # 二分定位：只返回 seq > last_seq，末尾为空
        self.assertEqual(len(bus.replay_since(seqs[0])), 2)
        self.assertEqual(bus.replay_since(seqs[-1]), [])
        # 房间：非全局资源 → comp-<id>；GLOBAL_RESOURCES → None
        self.assertEqual([e["room"] for e in events], ["comp-7", "comp-7", None])
        # 条目结构与改造前一致
        first = events[0]
        self.assertEqual(first["event"], "resource:changed")
        self.assertEqual(first["ts_ms"], first["data"]["ts"])
        self.assertEqual(
            set(first.keys()), {"event", "data", "room", "seq", "ts_ms"}
        )
        # 前端契约 resource-changed.ts 的 ResourceChangedEvent
        payload = first["data"]
        self.assertEqual(payload["resource"], "companies")
        self.assertEqual(payload["id"], 11)
        self.assertEqual(payload["ids"], [11])
        self.assertEqual(payload["action"], "updated")
        self.assertEqual(payload["competitionId"], 7)
        self.assertIsInstance(payload["ts"], int)

    def test_immediate_notifications_do_not_enter_ring(self):
        """对照断言：这三类即时通知在 local 模式下不入环（改造前后一致）。"""
        with self.captureOnCommitCallbacks(execute=True):
            emit_mod.emit_to_users([9], "message:new", {"id": 1})
            emit_mod.emit_resource_changed_to_users("message", 1, [9], "created")
            emit_mod.emit_to_competition(7, "fiscal-year:changed", {"id": 2})
        self.assertEqual(bus.replay_since(0), [], "即时通知不得进入重放环")
        self.assertEqual(
            emit_mod.server_seq(), 1, "emit_resource_changed_to_users 仍分配一个 seq（语义不变）"
        )

    def test_system_event_replay_flag_controls_ring(self):
        with self.captureOnCommitCallbacks(execute=True):
            emit_mod.emit_system_event("system:paused", {"mode": "PAUSED"})
            emit_mod.emit_system_restored({"snapshotId": 1})
            emit_mod.emit_system_event("system:progress", {"progress": "10%"}, replay=False)
        events = bus.replay_since(0)
        self.assertEqual([e["event"] for e in events], ["system:paused", "system:restored"])

    def test_permissions_changed_enters_ring(self):
        emit_mod.emit_permissions_changed(9, 4)
        events = bus.replay_since(0)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event"], "permissions:changed")
        self.assertEqual(events[0]["room"], "user-9")
        self.assertEqual(events[0]["data"]["version"], 4)

    def test_after_commit_semantics_preserved(self):
        """回滚不广播、提交才广播、seq 在提交后分配（_after_commit 语义不变）。"""
        from django.db import transaction

        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                emit_mod.emit_resource_changed("companies", 1, 7, "updated")
                raise RuntimeError("rollback")
        self.assertEqual(bus.replay_since(0), [], "回滚的事务不得广播（seq 也不得被消耗）")

        with self.captureOnCommitCallbacks(execute=True):
            emit_mod.emit_resource_changed("companies", 2, 7, "updated")
        events = bus.replay_since(0)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["seq"], 1, "seq 必须在提交后才分配")

    def test_local_delivery_still_goes_through_registered_asgi_loop(self):
        """投递出口换成 bus 后，local 模式仍走「已注册 ASGI loop」（= 改造前）。"""
        fake = _FakeSio()
        loop = _LoopThread()
        old_loop = emit_mod._loop
        emit_mod._loop = None  # 让 register_loop 接受测试 loop（与 test_i01 同法）
        emit_mod.register_loop(loop.loop)
        try:
            with mock.patch("apps.realtime.gateway.sio", fake):
                with self.captureOnCommitCallbacks(execute=True):
                    emit_mod.emit_resource_changed("companies", 5, 7, "updated")
                deadline = time.time() + 3
                while not fake.emitted and time.time() < deadline:
                    time.sleep(0.01)
        finally:
            emit_mod._loop = old_loop
            loop.close()

        self.assertEqual(len(fake.emitted), 1, "事件必须经 ASGI loop 投递到 sio")
        event, data, room = fake.emitted[0]
        self.assertEqual(event, "resource:changed")
        self.assertEqual(room, "comp-7")
        self.assertEqual(data["ids"], [5])
        self.assertEqual(data["seq"], 1)

    def test_frozen_public_api_signatures(self):
        for name, expected in FROZEN_SIGNATURES.items():
            self.assertTrue(hasattr(emit_mod, name), f"emit.{name} 必须存在")
            self.assertEqual(
                _signature_text(getattr(emit_mod, name)),
                expected,
                f"emit.{name} 签名被改动（设计说明 §2 冻结）",
            )

    def test_kick_still_disconnects_sessions_locally(self):
        """local/hub：kick 仍走本进程 loop「先通知、再断开」（I-01/I-03 语义不变）。"""
        fake = _FakeSio()
        fake.manager.get_participants = lambda namespace, room: iter(
            [("sid-a", "eio-a"), ("sid-b", "eio-b")]
        )
        loop = _LoopThread()
        old_loop = emit_mod._loop
        emit_mod._loop = None
        emit_mod.register_loop(loop.loop)
        try:
            with mock.patch("apps.realtime.gateway.sio", fake):
                emit_mod.kick_user_sessions(42, "password_reset")
                deadline = time.time() + 3
                while not fake.disconnected and time.time() < deadline:
                    time.sleep(0.01)
        finally:
            emit_mod._loop = old_loop
            loop.close()
        self.assertEqual(sorted(fake.disconnected), ["sid-a", "sid-b"], "必须真正断开房间内 sid")
        self.assertEqual(fake.emitted[0][0], "auth:required")
        self.assertEqual(fake.emitted[0][2], "user-42")

    def test_kick_without_loop_is_silent(self):
        """无 ASGI loop（脚本/测试/migrate）时踢人必须静默降级、不抛。"""
        with self.settings(REALTIME_BUS="local"):
            emit_mod.kick_user_sessions(999999, reason="test")
            emit_mod.kick_user_sessions(None, reason="test")

    def test_concurrent_broadcast_ring_seq_is_strictly_monotonic(self):
        """并发广播：取号与入环必须在同一临界区（修复 code_audit/U02 既有缺陷）。

        改造前 `_next_seq()`（_seq_lock）与 `_push_ring()`（_ring_lock）分处两把锁：
        A 拿 41、B 拿 42 时若 B 先 append，环内 seq 变 [.., 42, 41]，`replay_since`
        的二分定位随之偏移（重连补发重复/漏发）。本用例用 8 线程 × 25 条事件钉住修复。
        """
        thread_count, per_thread = 8, 25
        total = thread_count * per_thread
        barrier = threading.Barrier(thread_count)
        errors: list[BaseException] = []

        def _worker(tag: int) -> None:
            try:
                barrier.wait(timeout=10)
                for i in range(per_thread):
                    emit_mod.emit_resource_changed("companies", tag * 1000 + i, 7, "updated")
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        workers = [
            threading.Thread(target=_worker, args=(tag,), name=f"c1-emit-{tag}")
            for tag in range(thread_count)
        ]
        # 直接执行 on_commit 回调（否则 TestCase 的事务包裹会把回调推迟到永不提交）
        with mock.patch("django.db.transaction.on_commit", side_effect=lambda fn: fn()):
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(timeout=20)
        self.assertEqual(errors, [])
        self.assertTrue(all(not w.is_alive() for w in workers), "并发线程必须全部结束")

        events = bus.replay_since(0)
        self.assertEqual(len(events), total, "不得丢失事件")
        seqs = [e["seq"] for e in events]
        self.assertEqual(len(set(seqs)), total, "不得有重复 seq")
        self.assertEqual(seqs, sorted(seqs), "环内 seq 必须单调递增（修复前可能乱序）")
        self.assertEqual(seqs, list(range(1, total + 1)), "序号必须是连续的 1..N")
        ids = sorted(e["data"]["ids"][0] for e in events)
        self.assertEqual(ids, sorted(tag * 1000 + i for tag in range(thread_count) for i in range(per_thread)))
        # `replay_since(k)` 与「直接过滤 seq > k」必须完全一致（二分落点正确）
        for k in (0, 1, 7, 100, total - 1, total):
            self.assertEqual(
                [e["seq"] for e in bus.replay_since(k)],
                [s for s in seqs if s > k],
                f"replay_since({k}) 的二分落点必须与直接过滤一致",
            )


# ===========================================================================
# 2. forward 模式（WSGI 端）
# ===========================================================================
class BusForwardModeTests(_BusTestCase):
    def test_forward_posts_signed_payload_and_does_not_block_caller(self):
        with self._fake_hub(delay=0.35) as hub:
            with self.settings(
                REALTIME_BUS="forward",
                REALTIME_FORWARD_URL=hub.url,
                REALTIME_FORWARD_TIMEOUT=2.0,
            ):
                started = time.monotonic()
                with self.captureOnCommitCallbacks(execute=True):
                    emit_mod.emit_resource_changed("companies", 5, 7, "updated")
                elapsed = time.monotonic() - started
                self.assertLess(
                    elapsed, 0.2, "业务请求必须入队即返回（发送在独立后台线程，不等待 hub）"
                )
                self.assertTrue(bus.flush_forward(3.0), "后台线程最终必须把事件发出去")

            self.assertEqual(len(hub.requests), 1)
            req = hub.requests[0]
            self.assertEqual(req["path"], bus.internal_path())
            self.assertEqual(
                req["headers"].get("x-gipfel-internal-token"), bus.internal_token()
            )
            body = req["body"]
            self.assertEqual(body["event"], "resource:changed")
            self.assertEqual(body["room"], "comp-7")
            self.assertIs(body["replay"], True)
            self.assertEqual(body["payload"]["ids"], [5])
            self.assertEqual(body["payload"]["action"], "updated")
            self.assertIsInstance(body["payload"]["seq"], int)

    def test_forward_replay_flag_matches_local_ring_membership(self):
        """对照断言：hub 侧环成员与 local 模式逐事件一致（replay 增量字段生效）。"""
        with self._fake_hub() as hub:
            with self.settings(REALTIME_BUS="forward", REALTIME_FORWARD_URL=hub.url):
                with self.captureOnCommitCallbacks(execute=True):
                    emit_mod.emit_resource_changed("companies", 1, 7, "updated")  # 入环
                    emit_mod.emit_to_users([9], "message:new", {"id": 1})  # 不入环
                    emit_mod.emit_to_competition(7, "fiscal-year:changed", {"id": 2})  # 不入环
                    emit_mod.emit_system_event("system:paused", {"mode": "PAUSED"})  # 入环
                self.assertTrue(bus.flush_forward(3.0))

            by_event = {r["body"]["event"]: r["body"] for r in hub.requests}
            self.assertIs(by_event["resource:changed"]["replay"], True)
            self.assertIs(by_event["system:paused"]["replay"], True)
            self.assertIs(
                by_event["message:new"]["replay"], False, "emit_to_users 在 local 不入环"
            )
            self.assertIs(
                by_event["fiscal-year:changed"]["replay"],
                False,
                "emit_to_competition 在 local 不入环",
            )
            self.assertEqual(by_event["message:new"]["room"], "user-9")
            self.assertEqual(by_event["fiscal-year:changed"]["room"], "comp-7")
            self.assertEqual(by_event["message:new"]["payload"], {"id": 1})
            # forward 进程本身不入环（权威环在 hub）
            self.assertEqual(bus.replay_since(0), [])

    def test_forward_hub_unreachable_is_dropped_with_warning(self):
        url = f"http://127.0.0.1:{_closed_port()}"
        with self.settings(
            REALTIME_BUS="forward", REALTIME_FORWARD_URL=url, REALTIME_FORWARD_TIMEOUT=0.3
        ):
            with self.assertLogs("gipfel", level="WARNING") as logs:
                with self.captureOnCommitCallbacks(execute=True):
                    emit_mod.emit_resource_changed("companies", 1, 7, "updated")
                self.assertTrue(bus.flush_forward(3.0))
        self.assertTrue(
            any("内部转发失败" in line for line in logs.output),
            f"hub 不可达必须记 warning 而不是抛异常：{logs.output}",
        )

    def test_forward_without_url_drops_with_warning(self):
        with self.settings(REALTIME_BUS="forward", REALTIME_FORWARD_URL=""):
            with self.assertLogs("gipfel", level="WARNING"):
                with self.captureOnCommitCallbacks(execute=True):
                    emit_mod.emit_resource_changed("companies", 1, 7, "updated")
                self.assertTrue(bus.flush_forward(3.0))

    def test_forward_queue_full_drops_without_raising(self):
        release = threading.Event()

        def _stall(_queue):
            release.wait(10)

        with mock.patch.object(bus, "FORWARD_QUEUE_MAXSIZE", 1), mock.patch.object(
            bus, "_forward_worker_loop", _stall
        ), self.settings(REALTIME_BUS="forward", REALTIME_FORWARD_URL="http://127.0.0.1:1"):
            with self.assertLogs("gipfel", level="WARNING") as logs:
                with self.captureOnCommitCallbacks(execute=True):
                    emit_mod.emit_resource_changed("companies", 1, 7, "updated")  # 入队
                    emit_mod.emit_resource_changed("companies", 2, 7, "updated")  # 队列满 → 丢弃
        release.set()
        self.assertTrue(any("转发队列已满" in line for line in logs.output))

    def test_forward_kick_posts_kick_action(self):
        with self._fake_hub() as hub:
            with self.settings(REALTIME_BUS="forward", REALTIME_FORWARD_URL=hub.url):
                emit_mod.kick_user_sessions(42, "password_reset")
                self.assertTrue(bus.flush_forward(3.0))
            body = hub.requests[0]["body"]
            self.assertEqual(body["action"], "kick")
            self.assertEqual(body["userId"], 42)
            self.assertEqual(body["reason"], "password_reset")
            self.assertEqual(body["room"], "user-42")
            self.assertIs(body["replay"], False)


# ===========================================================================
# 3. 内部转发端点（hub 端）
# ===========================================================================
class InternalEndpointTests(_BusTestCase):
    def setUp(self):
        super().setUp()
        self.url = bus.internal_path()
        self.token = bus.internal_token()
        self.assertTrue(self.token, "settings.REALTIME_INTERNAL_TOKEN 必须非空")

    def _post(self, body, *, token=None, remote=None, client=None, raw=None):
        client = client or Client()
        kwargs = {"content_type": "application/json"}
        if token is not None:
            kwargs["HTTP_X_GIPFEL_INTERNAL_TOKEN"] = token
        if remote is not None:
            kwargs["REMOTE_ADDR"] = remote
        data = raw if raw is not None else json.dumps(body)
        return client.post(self.url, data=data, **kwargs)

    def test_accepts_loopback_with_valid_token_and_assigns_authoritative_seq(self):
        resp = self._post(
            {"event": "resource:changed", "payload": {"seq": 0, "ids": [1]}, "room": "comp-7"},
            token=self.token,
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json(), {"ok": True, "seq": 1})
        events = bus.replay_since(0)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["seq"], 1, "hub 必须用本进程序号覆盖发送方序号")
        self.assertEqual(events[0]["data"]["seq"], 1)
        self.assertEqual(events[0]["room"], "comp-7")

    def test_rejects_wrong_token(self):
        resp = self._post({"event": "resource:changed", "payload": {}, "room": None}, token="bad")
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(resp.json()["ok"])
        self.assertEqual(bus.replay_since(0), [])

    def test_rejects_missing_token(self):
        resp = self._post({"event": "resource:changed", "payload": {}, "room": None})
        self.assertEqual(resp.status_code, 403)

    def test_rejects_non_loopback(self):
        resp = self._post(
            {"event": "resource:changed", "payload": {}, "room": None},
            token=self.token,
            remote="10.1.2.3",
        )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["message"], "loopback_only")

    def test_requires_post(self):
        self.assertEqual(Client().get(self.url).status_code, 405)

    def test_is_csrf_exempt(self):
        """服务间调用没有 CSRF cookie：强制 CSRF 校验下仍必须 200。"""
        resp = self._post(
            {"event": "system:paused", "payload": {"mode": "PAUSED"}, "room": None},
            token=self.token,
            client=Client(enforce_csrf_checks=True),
        )
        self.assertEqual(resp.status_code, 200, resp.content)

    def test_replay_false_does_not_enter_ring(self):
        resp = self._post(
            {"event": "message:new", "payload": {"id": 1}, "room": "user-9", "replay": False},
            token=self.token,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(bus.replay_since(0), [], "replay=False 的即时通知不得入环")

    def test_kick_action_disconnects_on_hub(self):
        with mock.patch.object(bus, "_kick_local") as kick:
            resp = self._post({"action": "kick", "userId": 42, "reason": "password_reset"}, token=self.token)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(resp.json()["ok"])
        kick.assert_called_once_with(42, "password_reset")

    def test_bad_body_is_400_not_500(self):
        self.assertEqual(self._post(None, token=self.token, raw="{not json").status_code, 400)
        self.assertEqual(self._post({"payload": {}}, token=self.token).status_code, 400)

    def test_inbound_is_never_re_forwarded(self):
        """防自环：受理进程即使是 forward 模式，也不得把事件再转发出去。"""
        with self._fake_hub() as hub:
            fake = _FakeSio()
            loop = _LoopThread()
            old_loop = emit_mod._loop
            emit_mod._loop = None
            emit_mod.register_loop(loop.loop)
            try:
                with self.settings(REALTIME_BUS="forward", REALTIME_FORWARD_URL=hub.url):
                    with mock.patch("apps.realtime.gateway.sio", fake):
                        resp = self._post(
                            {
                                "event": "resource:changed",
                                "payload": {"seq": 0, "ids": [1]},
                                "room": "comp-7",
                            },
                            token=self.token,
                        )
                        self.assertEqual(resp.status_code, 200, resp.content)
                        deadline = time.time() + 2
                        while not fake.emitted and time.time() < deadline:
                            time.sleep(0.01)
                    time.sleep(0.3)  # 若错误地再转发，留出足够时间暴露
                    self.assertEqual(bus.forward_pending(), 0)
            finally:
                emit_mod._loop = old_loop
                loop.close()
        self.assertTrue(fake.emitted, "受理的转发必须在 hub 进程本地投递")
        self.assertEqual(hub.requests, [], "内部端点绝不能再次转发（防自环）")

    def test_internal_path_is_exempt_from_snapshot_gate(self):
        """暂停期间 system:paused 自身的转发不能被 423 拦掉（EXEMPT_PREFIXES）。"""
        from apps.snapshots import gate as gate_mod

        paused = {
            "mode": gate_mod.MODE_PAUSED,
            "reason": "验证豁免",
            "message": "paused",
            "dataVersion": 0,
        }
        with mock.patch("apps.snapshots.middleware.gate.load_state", return_value=paused):
            blocked = Client().post(
                "/api/companies", data="{}", content_type="application/json"
            )
            self.assertEqual(blocked.status_code, 423, "对照组：暂停时业务写必须被 423 拦")
            allowed = self._post(
                {"event": "system:paused", "payload": {"mode": "PAUSED"}, "room": None},
                token=self.token,
            )
            self.assertEqual(allowed.status_code, 200, "内部转发端点必须豁免门禁")


# ===========================================================================
# 4. 降级（缺依赖 / 连不上）与 gateway Redis 管理器
# ===========================================================================
class BusDegradeTests(_BusTestCase):
    def test_missing_redis_package_degrades_to_local_and_never_raises(self):
        with mock.patch.object(bus, "_load_redis_module", return_value=None), self.settings(
            REALTIME_BUS="redis",
            REALTIME_REDIS_URL="redis://127.0.0.1:6379/0",
            REALTIME_FORWARD_URL="",
        ):
            with self.assertLogs("gipfel", level="WARNING") as logs:
                mode = bus.resolve_mode()
            self.assertEqual(mode, "local")
            self.assertTrue(any("降级" in line for line in logs.output), logs.output)
            with self.captureOnCommitCallbacks(execute=True):
                emit_mod.emit_resource_changed("companies", 1, 7, "updated")
            self.assertEqual(len(bus.replay_since(0)), 1, "降级后实时功能必须不停摆")

    def test_redis_unreachable_degrades_to_forward_when_forward_url_set(self):
        url = f"redis://127.0.0.1:{_closed_port()}/0"
        with mock.patch.object(bus, "_load_redis_module", return_value=None), self.settings(
            REALTIME_BUS="redis",
            REALTIME_REDIS_URL=url,
            REALTIME_FORWARD_URL="http://127.0.0.1:8000",
        ):
            self.assertEqual(bus.resolve_mode(), "forward")

    def test_real_environment_missing_redis_degrades(self):
        """本机确实未装 redis 包 / 端口不通时的真实降级路径（不 mock 加载器）。"""
        url = f"redis://127.0.0.1:{_closed_port()}/0"
        with self.settings(
            REALTIME_BUS="redis", REALTIME_REDIS_URL=url, REALTIME_FORWARD_URL=""
        ):
            with self.assertLogs("gipfel", level="WARNING"):
                self.assertEqual(bus.resolve_mode(), "local")
            with self.captureOnCommitCallbacks(execute=True):
                emit_mod.emit_resource_changed("companies", 2, 7, "updated")
            self.assertEqual(len(bus.replay_since(0)), 1)

    def test_gateway_client_manager_only_for_redis(self):
        from apps.realtime import gateway

        for mode in ("local", "hub"):
            with self.settings(REALTIME_BUS=mode):
                self.assertIsNone(
                    gateway._build_client_manager(), f"{mode} 模式不得挂 client_manager（= 改造前）"
                )
        with self.settings(REALTIME_BUS="forward", REALTIME_FORWARD_URL="http://127.0.0.1:8000"):
            self.assertIsNone(gateway._build_client_manager())
        # redis 不可用 → 不挂 manager 且不抛
        with mock.patch.object(bus, "_load_redis_module", return_value=None), self.settings(
            REALTIME_BUS="redis", REALTIME_REDIS_URL=f"redis://127.0.0.1:{_closed_port()}/0"
        ):
            self.assertIsNone(gateway._build_client_manager())
        # 当前 sio 是改造前的单进程形态（默认 manager）
        self.assertFalse(isinstance(gateway.sio.manager, gateway.socketio.AsyncRedisManager))

    def test_gateway_attaches_async_redis_manager_in_redis_mode(self):
        from apps.realtime import gateway

        sentinel = object()
        with mock.patch.object(bus, "resolve_mode", return_value="redis"), mock.patch(
            "socketio.AsyncRedisManager", return_value=sentinel
        ) as manager_cls:
            self.assertIs(gateway._build_client_manager(), sentinel)
        manager_cls.assert_called_once_with(bus.redis_url())


# ===========================================================================
# 5. redis 模式逻辑（假 Redis 客户端：INCR 序号 + ZSET 环形缓冲）
# ===========================================================================
class BusRedisLogicTests(_BusTestCase):
    def test_redis_mode_uses_incr_seq_and_zset_ring(self):
        client = _FakeRedisClient()
        with self._fake_redis_env(client, ring_max_len=2):
            self.assertEqual(bus.resolve_mode(), "redis")
            self.assertEqual(bus.allocate_seq(), 1)
            self.assertEqual(bus.allocate_seq(), 2)
            self.assertEqual(bus.current_seq(), 2)
            self.assertEqual(client.get(bus.REDIS_SEQ_KEY), 2)

            for seq in (1, 2, 3):
                bus.push_ring("resource:changed", {"seq": seq, "ids": [seq]}, "comp-7")
            events = bus.replay_since(0)
            self.assertEqual([e["seq"] for e in events], [2, 3], "ZSET 必须按 rank 裁剪到 ring_max_len")
            self.assertEqual(events[0]["data"]["ids"], [2])
            self.assertEqual([e["seq"] for e in bus.replay_since(2)], [3])
            self.assertEqual(bus.replay_since(3), [])

    def test_redis_mode_assign_seq_and_ring_writes_zset(self):
        """redis 模式的原子组合：INCR 取号写回 payload + ZADD（score=seq）入环。"""
        client = _FakeRedisClient()
        with self._fake_redis_env(client, ring_max_len=10):
            payload = {"ids": [1], "ts": 1}
            seq = bus.assign_seq_and_ring("resource:changed", payload, "comp-7")
            self.assertEqual(seq, 1)
            self.assertEqual(payload["seq"], 1, "取号必须写回 payload（与 local 语义一致）")
            # 已带 seq（system 事件语义）：沿用调用方序号且不消耗新号
            data = {"seq": 99, "ts": 2}
            self.assertEqual(bus.setdefault_seq_and_ring("system:paused", data, None), 99)
            self.assertEqual(client.get(bus.REDIS_SEQ_KEY), 1, "沿用已有 seq 时不得消耗新号")
            events = bus.replay_since(0)
            self.assertEqual([e["event"] for e in events], ["resource:changed", "system:paused"])
            self.assertEqual(
                [e["seq"] for e in events], [1, 99], "ZSET 按 score=seq 有序（跨进程也单调）"
            )

    def test_redis_mode_missing_package_after_resolve_never_raises(self):
        """resolve 通过但底层操作失败时，只降级不抛（INCR/ZADD/查询三层各自兜底）。"""
        client = _FakeRedisClient()
        with self._fake_redis_env(client):
            self.assertEqual(bus.resolve_mode(), "redis")
            with mock.patch.object(client, "incr", side_effect=RuntimeError("boom")):
                self.assertEqual(bus.allocate_seq(), 1, "INCR 失败 → 退回进程内序号")
            with mock.patch.object(client, "pipeline", side_effect=RuntimeError("boom")):
                bus.push_ring("resource:changed", {"seq": 1, "ids": [1]}, None)
            with mock.patch.object(
                client, "zrangebyscore", side_effect=RuntimeError("boom")
            ):
                self.assertEqual(bus.replay_since(0), [])

    def test_redis_mode_delivery_publishes_through_sio(self):
        """redis 模式的投递出口是带 AsyncRedisManager 的 sio（此处断言调用参数）。"""
        fake = _FakeSio()
        client = _FakeRedisClient()

        async def _scenario():
            old_loop = emit_mod._loop
            emit_mod._loop = None
            emit_mod.register_loop(asyncio.get_running_loop())
            try:
                with self._fake_redis_env(client), mock.patch(
                    "apps.realtime.gateway.sio", fake
                ):
                    bus.deliver("resource:changed", {"seq": 1}, room="comp-7")
                    bus.deliver("system:paused", {"seq": 2}, room=None)
                    await asyncio.sleep(0.01)
            finally:
                emit_mod._loop = old_loop

        asyncio.run(_scenario())
        self.assertEqual(
            fake.emitted,
            [("resource:changed", {"seq": 1}, "comp-7"), ("system:paused", {"seq": 2}, None)],
        )
