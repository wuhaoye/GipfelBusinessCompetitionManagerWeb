# -*- coding: utf-8 -*-
"""并发阶梯诊断：用「慢中间件」的进出场时间戳判定 sleep 是否真的重叠。

输出每个并发档位的：总墙钟、参与线程数、**最大同时 sleeping 数**。
最大同时 >1 即证明请求真的并行；=1 即证明被串行化。

用法（仓库根目录）：
    backend\\.venv\\Scripts\\python.exe tests\\ops_check\\_probe_conc_trace.py
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIServer, make_server

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import _probe_common as pc  # noqa: E402

REPO, BACKEND, ART = pc.REPO, pc.BACKEND, pc.ARTIFACTS
PYTHON = BACKEND / ".venv" / "Scripts" / "python.exe"
SLOW_MS = int(os.environ.get("OPS_CHECK_SLOW_MS", "200"))
DELAY = SLOW_MS / 1000.0
LADDER = [1, 2, 4, 8, 20]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def one(port: int, timeout: float = 60.0) -> tuple[int, float]:
    import http.client

    t0 = time.perf_counter()
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        c.request("GET", "/api/health")
        r = c.getresponse()
        r.read()
        return r.status, time.perf_counter() - t0
    finally:
        c.close()


def burst(port: int, n: int) -> tuple[float, list[int]]:
    codes: list[int] = []
    lock = threading.Lock()
    barrier = threading.Barrier(n)

    def w():
        barrier.wait()
        try:
            st, _ = one(port)
        except Exception:  # noqa: BLE001
            st = -1
        with lock:
            codes.append(st)

    ts = [threading.Thread(target=w) for _ in range(n)]
    t0 = time.perf_counter()
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    return time.perf_counter() - t0, codes


def analyze(trace: Path) -> dict:
    if not trace.exists():
        return {"n": 0}
    rows = []
    for ln in trace.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if ln:
            rows.append(json.loads(ln))
    rows.sort(key=lambda r: r["enter"])
    events = []
    for r in rows:
        events.append((r["enter"], 1))
        events.append((r["exit"], -1))
    events.sort()
    cur = peak = 0
    for _t, d in events:
        cur += d
        peak = max(peak, cur)
    return {
        "n": len(rows),
        "threads": sorted({r["thread"] for r in rows}),
        "pids": sorted({r["pid"] for r in rows}),
        "peak_overlap": peak,
        "span": round(rows[-1]["exit"] - rows[0]["enter"], 3) if rows else 0,
    }


class ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True


def run_wsgi_threaded() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.ops_check.probe_settings")
    os.environ["OPS_CHECK_SLOW_MS"] = str(SLOW_MS)
    os.environ.setdefault("OPS_CHECK_DB", str(ART / "db_copy.sqlite3"))
    for p in (str(REPO), str(BACKEND)):
        if p not in sys.path:
            sys.path.insert(0, p)
    import django

    django.setup()
    from django.core.wsgi import get_wsgi_application

    app = get_wsgi_application()
    port = free_port()
    srv = make_server("127.0.0.1", port, app, server_class=ThreadingWSGIServer)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.4)
    print("[LADDER] WSGI 多线程（ThreadingMixIn + 真实 wsgiref）", flush=True)
    try:
        one(port)  # 预热
        for n in LADDER:
            trace = ART / f"trace_wsgi_{n}.jsonl"
            if trace.exists():
                trace.unlink()
            os.environ["OPS_CHECK_SLOW_TRACE"] = str(trace)
            wall, codes = burst(port, n)
            os.environ.pop("OPS_CHECK_SLOW_TRACE", None)
            a = analyze(trace)
            print(
                f"[LADDER]   N={n:2d} wall={wall:5.2f}s codes={sorted(set(codes))} "
                f"threads={len(a.get('threads', []))} peak_overlap={a.get('peak_overlap')} "
                f"span={a.get('span')}s",
                flush=True,
            )
    finally:
        srv.shutdown()


def run_daphne() -> None:
    port = free_port()
    out = ART / "trace_daphne_stdout.log"
    print("[LADDER] daphne（真实 ASGI 服务器 + backend.asgi:application）", flush=True)
    for n in LADDER:
        trace = ART / f"trace_daphne_{n}.jsonl"
        if trace.exists():
            trace.unlink()
        env = dict(os.environ)
        env.update({
            "DJANGO_SETTINGS_MODULE": "tests.ops_check.probe_settings",
            "OPS_CHECK_SLOW_MS": str(SLOW_MS),
            "OPS_CHECK_SLOW_TRACE": str(trace),
            "OPS_CHECK_DB": str(ART / "db_copy.sqlite3"),
            "PYTHONPATH": os.pathsep.join([str(REPO), str(BACKEND), env.get("PYTHONPATH", "")]),
            "PYTHONIOENCODING": "utf-8",
        })
        with open(out, "wb") as fh:
            proc = subprocess.Popen(
                [str(PYTHON), "-m", "daphne", "-b", "127.0.0.1", "-p", str(port),
                 "backend.asgi:application"],
                cwd=str(BACKEND), env=env, stdout=fh, stderr=subprocess.STDOUT,
            )
        try:
            deadline = time.time() + 40
            while time.time() < deadline:
                if proc.poll() is not None:
                    break
                try:
                    st, _ = one(port, timeout=3)
                    if st == 200:
                        break
                except Exception:  # noqa: BLE001
                    time.sleep(0.3)
            wall, codes = burst(port, n)
            time.sleep(0.3)  # 等中间件把 trace 写完
            a = analyze(trace)
            print(
                f"[LADDER]   N={n:2d} wall={wall:5.2f}s codes={sorted(set(codes))} "
                f"threads={len(a.get('threads', []))} peak_overlap={a.get('peak_overlap')} "
                f"span={a.get('span')}s",
                flush=True,
            )
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


def main() -> int:
    pc.bootstrap(need_django=False)
    print(f"[LADDER] 单请求 sleep={DELAY}s，阶梯={LADDER}", flush=True)
    run_wsgi_threaded()
    run_daphne()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
