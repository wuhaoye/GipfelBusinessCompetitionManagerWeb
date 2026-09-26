# -*- coding: utf-8 -*-
"""C1-a 总线探针（对抗式）：契约签名、转发、降级、内部端点矩阵、_after_commit、seq 单调性。

全部使用 `_artifacts/db_copy.sqlite3` 副本（见 probe_settings.py），不触碰活库。

用法（仓库根目录）：
    backend\\.venv\\Scripts\\python.exe tests\\ops_check\\probe_bus.py
"""
from __future__ import annotations

import ast
import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import _probe_common as pc  # noqa: E402

pc.bootstrap()

from django.test import Client  # noqa: E402
from django.test.utils import override_settings  # noqa: E402

from apps.realtime import bus, emit  # noqa: E402

REPO = pc.REPO
TOKEN = None  # 由 settings 读取后赋值


# --------------------------------------------------------------------------
# 假 hub：记录收到的转发请求
# --------------------------------------------------------------------------
class _HubHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # noqa: D102
        pass

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        srv = self.server  # type: ignore[attr-defined]
        try:
            body = json.loads(raw.decode("utf-8"))
        except Exception:  # noqa: BLE001
            body = {"_raw": raw.decode("utf-8", "replace")}
        with srv.lock:  # type: ignore[attr-defined]
            srv.received.append(  # type: ignore[attr-defined]
                {
                    "path": self.path,
                    "token": self.headers.get(bus.INTERNAL_TOKEN_HEADER, ""),
                    "body": body,
                    "ts": time.time(),
                }
            )
        delay = getattr(srv, "delay", 0.0)  # type: ignore[attr-defined]
        if delay:
            time.sleep(delay)
        payload = json.dumps({"ok": True, "seq": 1}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class FakeHub:
    def __init__(self, delay: float = 0.0):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _HubHandler)
        self.httpd.lock = threading.Lock()  # type: ignore[attr-defined]
        self.httpd.received = []  # type: ignore[attr-defined]
        self.httpd.delay = delay  # type: ignore[attr-defined]
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def received(self) -> list[dict]:
        return self.httpd.received  # type: ignore[attr-defined]

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def closed_port() -> int:
    """占一个端口后立刻关闭 —— 之后连接该端口必定被拒（用于模拟 hub 不可达）。"""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _host_override():
    """Django 测试客户端的 Host 是 `testserver`，生产 ALLOWED_HOSTS 不含它。

    这里只在探针请求期间放行 Host（与本次验收无关的框架细节），
    其余设置保持生产值不变。
    """
    return override_settings(ALLOWED_HOSTS=["testserver", "127.0.0.1", "localhost", "::1"])


# --------------------------------------------------------------------------
def check_signature_parity() -> None:
    """C-a1：emit 公开 API 的签名与 HEAD 基线逐参数一致（ast 解析，不信注释）。"""
    baseline = subprocess.run(
        ["git", "show", "HEAD:backend/apps/realtime/emit.py"],
        cwd=str(REPO), capture_output=True, text=True, encoding="utf-8",
    )
    if baseline.returncode != 0:
        pc.fail("C-a1-emit-signature", f"git show 失败：{baseline.stderr[:200]}")
        return
    base_src = baseline.stdout
    cur_src = (REPO / "backend" / "apps" / "realtime" / "emit.py").read_text(encoding="utf-8")

    def public_funcs(src: str) -> dict[str, str]:
        tree = ast.parse(src)
        out = {}
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
                out[node.name] = ast.unparse(node.args)
        return out

    base = public_funcs(base_src)
    cur = public_funcs(cur_src)
    missing = sorted(set(base) - set(cur))
    pc.check("C-a1-emit-no-public-func-removed", not missing, f"基线公开函数仍全部存在；缺失={missing}")
    changed = sorted(n for n in set(base) & set(cur) if base[n] != cur[n])
    pc.check("C-a1-emit-signature-unchanged", not changed,
             f"公开函数签名逐参数一致；变更={[(n, base[n], cur[n]) for n in changed]}")
    added = sorted(set(cur) - set(base))
    pc.info(f"新增公开函数（允许）：{added}")
    # 设计说明 §2 点名的 11 个函数必须在
    needs = ["emit_resource_changed", "emit_resource_changed_to_users", "emit_to_users",
             "emit_to_competition", "emit_system_event", "emit_system_restored",
             "emit_permissions_changed", "kick_user_sessions", "replay_since",
             "server_seq", "register_loop"]
    pc.check("C-a1-emit-required-api", all(f in cur for f in needs),
             f"契约点名的 {len(needs)} 个函数全部存在；缺失={[f for f in needs if f not in cur]}")
    # 调用方零改动：git diff 里 signals.py / snapshots 不得出现
    diff = subprocess.run(["git", "status", "--porcelain"], cwd=str(REPO),
                          capture_output=True, text=True, encoding="utf-8").stdout
    touched_callers = [ln for ln in diff.splitlines()
                       if "signals.py" in ln and "apps/common/signals.py" in ln]
    pc.check("C-a1-callers-untouched", not touched_callers,
             f"调用方 apps/common/signals.py 未被改动；命中={touched_callers}")


def check_forward_ok() -> None:
    """C-a2：forward 模式转发到假 hub 成功，负载/房间/令牌/replay 字段正确。"""
    hub = FakeHub()
    try:
        bus.reset_state_for_tests()
        with override_settings(REALTIME_BUS="forward", REALTIME_FORWARD_URL=hub.url,
                               REALTIME_INTERNAL_TOKEN="tok-for-probe"):
            t0 = time.perf_counter()
            emit.emit_resource_changed("companies", 7, 3, "updated")
            elapsed = time.perf_counter() - t0
            drained = bus.flush_forward(3.0)
        got = list(hub.received)
        pc.check("C-a2-forward-delivered", drained and len(got) == 1,
                 f"假 hub 收到 {len(got)} 条（flush={drained}）")
        if got:
            rec = got[0]
            body = rec["body"]
            pc.check("C-a2-forward-path", rec["path"] == "/_internal/realtime/emit",
                     f"路径={rec['path']}")
            pc.check("C-a2-forward-token-header", rec["token"] == "tok-for-probe",
                     f"令牌头={rec['token']!r}")
            pc.check("C-a2-forward-event", body.get("event") == "resource:changed",
                     f"event={body.get('event')!r}")
            pc.check("C-a2-forward-room", body.get("room") == "comp-3", f"room={body.get('room')!r}")
            pc.check("C-a2-forward-replay-true", body.get("replay") is True,
                     f"replay={body.get('replay')!r}（该事件本地模式入环 → 转发也必须入环）")
            payload = body.get("payload") or {}
            pc.check("C-a2-forward-payload",
                     payload.get("resource") == "companies" and payload.get("id") == 7
                     and payload.get("ids") == [7] and payload.get("action") == "updated"
                     and payload.get("competitionId") == 3 and isinstance(payload.get("seq"), int),
                     f"payload={payload}")
        pc.info(f"单次 emit 调用耗时={elapsed*1000:.1f}ms（不含后台发送）")
        pc.check("C-a2-emit-not-blocking", elapsed < 0.05,
                 f"emit 返回耗时 {elapsed*1000:.1f}ms < 50ms（排队在后台线程）")
    finally:
        hub.close()
        bus.reset_state_for_tests()


def check_forward_not_blocking_when_hub_slow() -> None:
    """C-a2b：hub 每次响应慢 0.5s，业务侧 20 次 emit 仍必须立即返回。"""
    hub = FakeHub(delay=0.5)
    try:
        bus.reset_state_for_tests()
        with override_settings(REALTIME_BUS="forward", REALTIME_FORWARD_URL=hub.url,
                               REALTIME_INTERNAL_TOKEN="t"):
            t0 = time.perf_counter()
            for i in range(20):
                emit.emit_resource_changed("companies", i, 1, "updated")
            elapsed = time.perf_counter() - t0
        pc.check("C-a2b-20-emits-fast", elapsed < 1.0,
                 f"20 次 emit 总耗时 {elapsed:.3f}s < 1.0s（hub 每次慢 0.5s，仍未拖住业务）")
    finally:
        hub.close()
        bus.reset_state_for_tests()


def check_forward_hub_unreachable() -> None:
    """C-a3：hub 不可达 → 不抛异常、业务不阻断、事件被丢弃（有界队列）。"""
    port = closed_port()
    bus.reset_state_for_tests()
    with override_settings(REALTIME_BUS="forward",
                           REALTIME_FORWARD_URL=f"http://127.0.0.1:{port}",
                           REALTIME_INTERNAL_TOKEN="t", REALTIME_FORWARD_TIMEOUT=0.3):
        raised = None
        t0 = time.perf_counter()
        try:
            for i in range(5):
                emit.emit_resource_changed("companies", i, 1, "updated")
                emit.emit_system_event("system:paused", {"mode": "paused"})
                emit.kick_user_sessions(1, "probe")
        except Exception as exc:  # noqa: BLE001
            raised = exc
        elapsed = time.perf_counter() - t0
        drained = bus.flush_forward(5.0)
    pc.check("C-a3-no-raise", raised is None, f"hub 不可达时未抛异常；raised={raised!r}")
    pc.check("C-a3-business-not-blocked", elapsed < 0.5,
             f"15 次 emit 总耗时 {elapsed:.3f}s < 0.5s（业务请求不等待转发）")
    pc.check("C-a3-queue-drained-by-drop", drained, "转发队列最终排空（失败即丢弃，不堆积）")
    bus.reset_state_for_tests()


def check_forward_replay_and_kick_flags() -> None:
    """C-a2c：转发体的 replay/action 字段与「本地模式是否入环」逐事件一致。

    `local` 模式下 `emit_to_users` / `emit_resource_changed_to_users` / `emit_to_competition`
    从不入环，若转发时不带 `replay=false`，hub 会把这些事件写进重放环 → 重连 `sync:replay`
    重复补发（语义漂移）。`kick_user_sessions` 在 forward 模式必须带 `action="kick"`，
    否则进程分离后顶号只发通知、不断开（I-01/I-03 修复失效）。
    """
    hub = FakeHub()
    try:
        bus.reset_state_for_tests()
        with override_settings(REALTIME_BUS="forward", REALTIME_FORWARD_URL=hub.url,
                               REALTIME_INTERNAL_TOKEN="t"):
            emit.emit_resource_changed("companies", 1, 5, "updated")
            emit.emit_resource_changed_to_users("companies", 1, [9], "updated", 5)
            emit.emit_to_users([9], "sys:ping", {"a": 1})
            emit.emit_to_competition(5, "fiscal-year:changed", {"id": 2})
            emit.emit_system_event("system:paused", {"mode": "paused"})
            emit.emit_permissions_changed(9, 3)
            emit.kick_user_sessions(9, "token_version_mismatch")
            bus.flush_forward(3.0)
        by_key = {}
        for rec in hub.received:
            body = rec["body"]
            by_key.setdefault((body.get("event"), body.get("room")), []).append(body)
        # 逐 (event, room) 期望：同一 event 在不同房间的 replay 语义本来就不同
        want = [
            ("resource:changed", "comp-5", True),      # 全局资源变更（重放环）
            ("resource:changed", "user-9", False),     # 定向通知：带号但不入环
            ("fiscal-year:changed", "comp-5", False),  # emit_to_competition：不入环
            ("sys:ping", "user-9", False),             # emit_to_users：不入环
            ("system:paused", None, True),             # 系统门禁事件：入环
            ("permissions:changed", "user-9", True),   # 权限变更：入环
            ("auth:required", "user-9", False),        # kick：不入环
        ]
        for event, room, expected in want:
            got = by_key.get((event, room)) or []
            if not got:
                pc.fail(f"C-a2c-{event}@{room}", f"假 hub 未收到；实际键={sorted(by_key, key=str)}")
                continue
            flags = [bool(r.get("replay")) for r in got]
            pc.check(f"C-a2c-{event}@{room}", all(f is expected for f in flags),
                     f"event={event} room={room} replay={flags}（期望全 {expected}）")
        rc_rooms = sorted(str(k[1]) for k in by_key if k[0] == "resource:changed")
        pc.check("C-a2c-two-resource-changed", rc_rooms == ["comp-5", "user-9"],
                 f"resource:changed 出现在两个房间：{rc_rooms}")
        # 通知类事件在 local 模式下"带号但不入环"：payload 应带 seq
        rcu = by_key.get(("resource:changed", "user-9")) or []
        pc.check("C-a2c-notify-has-seq",
                 bool(rcu) and isinstance((rcu[0].get("payload") or {}).get("seq"), int),
                 "emit_resource_changed_to_users 转发体 payload 带 seq（= 本地模式 assign_seq 语义）")
        # kick
        kicks = [r for r in (by_key.get(("auth:required", "user-9")) or [])
                 if r.get("action") == "kick"]
        pc.check("C-a2c-kick-action",
                 len(kicks) == 1 and kicks[0].get("userId") == 9 and kicks[0].get("room") == "user-9",
                 f"kick 转发体={kicks}")
    finally:
        hub.close()
        bus.reset_state_for_tests()


def check_redis_missing_degrades() -> None:
    """C-a4：redis 包缺失 → 降级不崩。

    两路证据：
    ① **本机真实状态**：`redis` 未安装（`importlib.util.find_spec('redis') is None`），
       直接 `REALTIME_BUS=redis` 走一遍真实降级；
    ② `sys.modules['redis']=None` 人为制造 ImportError，覆盖"已声明但装不上"的分支。
    """
    import importlib.util

    natural_missing = importlib.util.find_spec("redis") is None
    pc.info(f"本机 redis 包是否缺失：{natural_missing}（requirements.txt 声明了 redis==5.0.8）")
    bus.reset_state_for_tests()
    natural_raised = None
    try:
        with override_settings(REALTIME_BUS="redis",
                               REALTIME_REDIS_URL="redis://127.0.0.1:6399/0",
                               REALTIME_FORWARD_URL=""):
            natural_mode = bus.resolve_mode()
            emit.emit_resource_changed("companies", 1, 2, "updated")
    except Exception as exc:  # noqa: BLE001
        natural_raised = exc
        natural_mode = None
    pc.check("C-a4-natural-degrade", natural_mode == "local" and natural_raised is None,
             f"真实缺包时 resolve_mode={natural_mode}，异常={natural_raised!r}")

    saved = sys.modules.get("redis", "ABSENT")
    sys.modules["redis"] = None  # type: ignore[assignment]  → import redis 抛 ImportError
    try:
        bus.reset_state_for_tests()
        hub = FakeHub()
        try:
            with override_settings(REALTIME_BUS="redis",
                                   REALTIME_REDIS_URL="redis://127.0.0.1:6399/0",
                                   REALTIME_FORWARD_URL="", REALTIME_INTERNAL_TOKEN="t"):
                mode_no_fwd = bus.resolve_mode()
            with override_settings(REALTIME_BUS="redis",
                                   REALTIME_REDIS_URL="redis://127.0.0.1:6399/0",
                                   REALTIME_FORWARD_URL=hub.url, REALTIME_INTERNAL_TOKEN="t"):
                mode_with_fwd = bus.resolve_mode()
                bus.reset_state_for_tests()
                raised = None
                try:
                    emit.emit_resource_changed("companies", 1, 2, "updated")
                    emit.emit_to_users([1], "x", {})
                    emit.emit_permissions_changed(1, 1)
                    seq = emit.server_seq()
                except Exception as exc:  # noqa: BLE001
                    raised = exc
                    seq = None
                bus.flush_forward(3.0)
                from apps.realtime import gateway as gw

                mgr = gw._build_client_manager()
            pc.check("C-a4-degrade-no-forward-url", mode_no_fwd == "local",
                     f"REALTIME_BUS=redis + 无 forward URL + 无 redis 包 → {mode_no_fwd}")
            pc.check("C-a4-degrade-to-forward", mode_with_fwd == "forward",
                     f"REALTIME_BUS=redis + 有 forward URL + 无 redis 包 → {mode_with_fwd}")
            pc.check("C-a4-emit-no-raise", raised is None, f"降级路径 emit 未抛异常；raised={raised!r}")
            pc.check("C-a4-seq-fallback-int", isinstance(seq, int) and seq > 0,
                     f"seq 回落进程内计数={seq}")
            pc.check("C-a4-client-manager-none", mgr is None,
                     "redis 包缺失时 AsyncRedisManager 不挂载且不崩")
        finally:
            hub.close()
    finally:
        if saved == "ABSENT":
            sys.modules.pop("redis", None)
        else:
            sys.modules["redis"] = saved
        bus.reset_state_for_tests()


def check_internal_endpoint_matrix() -> None:
    """C-a5：内部端点矩阵（令牌/来源/方法/JSON/自环）。"""
    from django.conf import settings as dj

    token = dj.REALTIME_INTERNAL_TOKEN
    path = dj.REALTIME_INTERNAL_PATH
    client = Client(enforce_csrf_checks=True)  # 真开 CSRF 校验，验证 csrf_exempt 生效
    body = json.dumps({"event": "resource:changed", "payload": {"resource": "companies",
                                                               "ids": [1], "seq": 1},
                       "room": "comp-1"})
    hosts = _host_override()  # 探针用 Django 测试客户端，Host=testserver 需临时放行

    def post(payload: str, remote: str, tok: str | None) -> tuple[int, str]:
        headers = {"HTTP_X_GIPFEL_INTERNAL_TOKEN": tok} if tok is not None else {}
        resp = client.post(path, data=payload, content_type="application/json",
                           REMOTE_ADDR=remote, **headers)
        return resp.status_code, (resp.content or b"")[:200].decode("utf-8", "replace")

    with hosts:
        with override_settings(REALTIME_BUS="hub"):
            bus.reset_state_for_tests()
            st_ok, b_ok = post(body, "127.0.0.1", token)
            st_v6, _ = post(body, "::1", token)
            st_mapped, _ = post(body, "::ffff:127.0.0.1", token)
            st_other, _ = post(body, "8.8.8.8", token)
            st_notoken, _ = post(body, "127.0.0.1", None)
            st_badtoken, _ = post(body, "127.0.0.1", "wrong")
            st_badjson, _ = post("{not json", "127.0.0.1", token)
            st_noevent, _ = post(json.dumps({"event": ""}), "127.0.0.1", token)
            st_get = client.get(path, REMOTE_ADDR="127.0.0.1",
                                HTTP_X_GIPFEL_INTERNAL_TOKEN=token).status_code
            st_kick, b_kick = post(json.dumps({"action": "kick", "userId": 1}), "127.0.0.1", token)

    pc.check("C-a5-200-loopback-v4", st_ok == 200, f"回环 127.0.0.1 + 正确令牌 → {st_ok} {b_ok}")
    pc.check("C-a5-200-loopback-v6", st_v6 == 200, f"回环 ::1 → {st_v6}")
    pc.check("C-a5-200-loopback-mapped", st_mapped == 200, f"回环 ::ffff:127.0.0.1 → {st_mapped}")
    pc.check("C-a5-403-non-loopback", st_other == 403, f"非回环 8.8.8.8 → {st_other}（期望 403）")
    pc.check("C-a5-403-no-token", st_notoken == 403, f"无令牌 → {st_notoken}（期望 403）")
    pc.check("C-a5-403-bad-token", st_badtoken == 403, f"令牌错 → {st_badtoken}（期望 403）")
    pc.check("C-a5-400-bad-json", st_badjson == 400, f"非法 JSON → {st_badjson}（期望 400）")
    pc.check("C-a5-400-empty-event", st_noevent == 400, f"event 为空 → {st_noevent}（期望 400）")
    pc.check("C-a5-405-get", st_get == 405, f"GET → {st_get}（期望 405，require_POST）")
    pc.check("C-a5-200-csrf-exempt-kick", st_kick == 200,
             f"csrf 校验开启下 POST kick → {st_kick} {b_kick}（csrf_exempt 生效）")
    pc.check("C-a5-ok-body-shape", '"ok"' in b_ok and '"seq"' in b_ok,
             f"成功响应体含 ok/seq：{b_ok}")
    pc.info("契约中没有 401 分支：内部端点的鉴权失败一律 403（设计说明 §2 原文），"
            "故 401 不适用（见报告）。")
    bus.reset_state_for_tests()


def check_no_self_forward() -> None:
    """C-a6：hub 收到转发后绝不再次 HTTP 转发（防自环）。"""
    hub = FakeHub()
    try:
        bus.reset_state_for_tests()
        with override_settings(REALTIME_BUS="forward", REALTIME_FORWARD_URL=hub.url,
                               REALTIME_INTERNAL_TOKEN="t"):
            # hub 进程若再转发，这里会看到请求；handle_inbound 必须 allow_forward=False
            seq = bus.handle_inbound("resource:changed", {"resource": "companies", "ids": [1]},
                                     "comp-1", replay=True)
            client = Client()
            with _host_override():
                resp = client.post("/_internal/realtime/emit",
                                   data=json.dumps({"event": "resource:changed",
                                                    "payload": {"ids": [2]}, "room": "comp-1"}),
                                   content_type="application/json", REMOTE_ADDR="127.0.0.1",
                                   HTTP_X_GIPFEL_INTERNAL_TOKEN="t")
            time.sleep(0.4)
            count = len(hub.received)
        pc.check("C-a6-no-self-forward", count == 0 and resp.status_code == 200,
                 f"受理转发期间对外 HTTP 转发数={count}（期望 0），端点响应={resp.status_code}，seq={seq}")
    finally:
        hub.close()
        bus.reset_state_for_tests()


def check_after_commit() -> None:
    """C-a7：_after_commit 语义 —— 回滚不广播、提交后广播。"""
    from django.db import transaction

    hub = FakeHub()
    try:
        bus.reset_state_for_tests()
        with override_settings(REALTIME_BUS="forward", REALTIME_FORWARD_URL=hub.url,
                               REALTIME_INTERNAL_TOKEN="t"):
            class _Boom(Exception):
                pass

            try:
                with transaction.atomic():
                    emit.emit_resource_changed("companies", 11, 2, "updated")
                    raise _Boom()
            except _Boom:
                pass
            bus.flush_forward(2.0)
            after_rollback = len(hub.received)

            with transaction.atomic():
                emit.emit_resource_changed("companies", 12, 2, "updated")
            bus.flush_forward(2.0)
            after_commit = len(hub.received)

            # 非事务上下文：on_commit 立即执行（= 改造前行为）
            emit.emit_resource_changed("companies", 13, 2, "updated")
            bus.flush_forward(2.0)
            outside_tx = len(hub.received)
        pc.check("C-a7-rollback-no-broadcast", after_rollback == 0,
                 f"事务回滚后假 hub 收到 {after_rollback} 条（期望 0：回滚不广播）")
        pc.check("C-a7-commit-broadcast", after_commit >= 1,
                 f"事务提交后收到 {after_commit} 条（期望 ≥1）")
        pc.check("C-a7-outside-tx-immediate", outside_tx >= after_commit + 1,
                 f"非事务下立即广播：{outside_tx} 条")
    finally:
        hub.close()
        bus.reset_state_for_tests()


def check_gate_paused_internal_exempt() -> None:
    """C-a8：暂停期间 /_internal/ 不被 423，而业务写仍 423（广播断链检查）。"""
    from apps.snapshots import gate
    from django.conf import settings as dj

    client = Client()
    token = dj.REALTIME_INTERNAL_TOKEN
    try:
        gate.set_mode(gate.MODE_PAUSED, reason="ops_check", broadcast=False)
        with _host_override():
            internal = client.post("/_internal/realtime/emit",
                                   data=json.dumps({"event": "system:paused",
                                                    "payload": {"mode": "paused"}, "room": None}),
                                   content_type="application/json", REMOTE_ADDR="127.0.0.1",
                                   HTTP_X_GIPFEL_INTERNAL_TOKEN=token)
            biz = client.put("/api/company-fields/1", data=json.dumps({"fields": []}),
                             content_type="application/json")
            biz_get = client.get("/api/company-fields/1")
        pc.check("C-a8-internal-not-423", internal.status_code == 200,
                 f"暂停期间 /_internal/realtime/emit → {internal.status_code}（期望 200，不得 423）")
        pc.check("C-a8-business-write-423", biz.status_code == 423,
                 f"暂停期间业务写 PUT /api/company-fields/1 → {biz.status_code}（期望 423）")
        pc.check("C-a8-business-read-ok", biz_get.status_code != 423,
                 f"暂停期间业务读仍放行 → {biz_get.status_code}（期望非 423）")
    finally:
        gate.set_mode(gate.MODE_RUNNING, broadcast=False)
        bus.reset_state_for_tests()


def check_seq_ring_monotonic() -> None:
    """C-a9：8 线程并发取号 + 入环，环内 seq 必须严格单调（修复竞态的验收）。"""
    bus.reset_state_for_tests()
    n_threads, per = 8, 200
    barrier = threading.Barrier(n_threads)

    def worker(tag: int) -> None:
        barrier.wait()
        for i in range(per):
            bus.assign_seq_and_ring("t", {"tag": tag, "i": i, "ts": 0}, None)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
    t0 = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.perf_counter() - t0
    seqs = list(bus._ring_seqs)
    total = n_threads * per
    strictly_inc = all(seqs[i] < seqs[i + 1] for i in range(len(seqs) - 1))
    pc.check("C-a9-ring-length", len(seqs) == total, f"入环条数={len(seqs)}（期望 {total}）")
    pc.check("C-a9-ring-strictly-monotonic", strictly_inc,
             "环内 seq 严格单调递增（并发下无回退/重复）")
    pc.check("C-a9-seq-unique", len(set(seqs)) == len(seqs), "seq 无重复")
    pc.check("C-a9-server-seq", bus.current_seq() == total, f"current_seq={bus.current_seq()}（期望 {total}）")
    pc.check("C-a9-replay-window", len(bus.replay_since(total - 5)) == 5,
             f"replay_since 二分定位正确（total-5 → {len(bus.replay_since(total - 5))} 条）")
    pc.info(f"8 线程 × {per} 次 = {total} 次取号+入环，耗时 {elapsed:.3f}s")
    bus.reset_state_for_tests()


def main() -> int:
    pc.info(f"DB={os.environ.get('OPS_CHECK_DB')}")
    check_signature_parity()
    check_forward_ok()
    check_forward_not_blocking_when_hub_slow()
    check_forward_replay_and_kick_flags()
    check_forward_hub_unreachable()
    check_redis_missing_degrades()
    check_internal_endpoint_matrix()
    check_no_self_forward()
    check_after_commit()
    check_gate_paused_internal_exempt()
    check_seq_ring_monotonic()
    return pc.finish()


if __name__ == "__main__":
    raise SystemExit(main())
