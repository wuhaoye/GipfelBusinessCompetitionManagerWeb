# -*- coding: utf-8 -*-
"""C1.3/C1.5 对照实验：ASGI(daphne) 与 WSGI 多线程的真实并发行为。

做法（不使用任何压测工具，避免引入依赖）：
- 用 `tests/ops_check/probe_settings.py` 起 Django：**真实中间件链 + 真实视图**
  （`/api/health`），只在最外层插一个「先睡 OPS_CHECK_SLOW_MS 毫秒」的中间件，
  让单个请求耗时可控且占绝对主导（默认 200ms）；
- 该中间件同时把「线程 id + 进出场时刻」写入 trace 文件，用**最大重叠数**
  （而非只看总墙钟）判定请求是否真的并行；
- 三种承载方式：A. wsgiref + ThreadingMixIn（≈ gunicorn gthread）；
  B. wsgiref 单线程（对照组）；C. daphne（真实 ASGI，`backend.asgi:application`）。

⚠️ 结论提示（实测得出，见报告）：daphne 下**并没有**出现简报 C1.1/C1.3 声称的
「全进程共用一个单线程执行器」——Django 的 ASGIHandler 给**每个请求**都开了
`ThreadSensitiveContext()`（django/core/handlers/asgi.py:169），于是
`sync_to_async(..., thread_sensitive=True)` 落到「每请求一个 ThreadPoolExecutor(max_workers=1)」
（asgiref/sync.py:467-478），而不是进程级的 single_thread_executor（asgiref/sync.py:488）。
因此本脚本对 ASGI 的期望是「并行」——与简报相反，这是本次验收的独立发现。

用法（仓库根目录）：
    backend\\.venv\\Scripts\\python.exe tests\\ops_check\\probe_concurrency.py
"""
from __future__ import annotations

import http.client
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

N_BRIEF = int(os.environ.get("OPS_CHECK_N_BRIEF", "8"))
N_C15 = int(os.environ.get("OPS_CHECK_N_C15", "20"))
SLOW_MS = int(os.environ.get("OPS_CHECK_SLOW_MS", "200"))
DELAY_S = SLOW_MS / 1000.0


# --------------------------------------------------------------------------
def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def one_request(port: int, path: str = "/api/health", timeout: float = 120.0) -> tuple[int, float]:
    t0 = time.perf_counter()
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        resp.read()
        return resp.status, time.perf_counter() - t0
    finally:
        conn.close()


def burst(port: int, n: int, path: str = "/api/health") -> tuple[float, list[int]]:
    results: list[int] = []
    lock = threading.Lock()
    barrier = threading.Barrier(n)

    def worker() -> None:
        barrier.wait()
        try:
            status, _dt = one_request(port, path)
        except Exception:  # noqa: BLE001
            status = -1
        with lock:
            results.append(status)

    threads = [threading.Thread(target=worker) for _ in range(n)]
    t0 = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return time.perf_counter() - t0, results


def burst_with_probe(port: int, n: int, path: str = "/api/health") -> tuple[float, float]:
    """并发 n 个请求期间追加一个请求并测其延迟（C1.5 第二条）。"""
    threads = [threading.Thread(target=lambda: one_request(port, path), daemon=True) for _ in range(n)]
    t0 = time.perf_counter()
    for t in threads:
        t.start()
    time.sleep(DELAY_S / 2)  # 确保负载已在飞
    _status, probe_latency = one_request(port, path)
    for t in threads:
        t.join()
    return time.perf_counter() - t0, probe_latency


def analyze_trace(trace: Path) -> dict:
    if not trace.exists():
        return {"rows": 0, "peak_overlap": None, "threads": 0}
    rows = [json.loads(ln) for ln in trace.read_text(encoding="utf-8").splitlines() if ln.strip()]
    events = []
    for r in rows:
        events.append((r["enter"], 1))
        events.append((r["exit"], -1))
    events.sort()
    cur = peak = 0
    for _t, d in events:
        cur += d
        peak = max(peak, cur)
    return {"rows": len(rows), "peak_overlap": peak,
            "threads": len({r["thread"] for r in rows}),
            "pids": sorted({r["pid"] for r in rows})}


# --------------------------------------------------------------------------
class ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True
    # wsgiref 默认 request_queue_size=5：>=8 个客户端同时连接时 Windows 会丢弃 SYN
    # 并触发 ~500ms 重传，把总墙钟伪装成"串行"。这里放大监听队列，
    # 让测量反映**线程模型**而不是监听队列长度。
    request_queue_size = 256


def wsgi_app():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.ops_check.probe_settings")
    os.environ["OPS_CHECK_SLOW_MS"] = str(SLOW_MS)
    os.environ.setdefault("OPS_CHECK_DB", str(ART / "db_copy.sqlite3"))
    for p in (str(REPO), str(BACKEND)):
        if p not in sys.path:
            sys.path.insert(0, p)
    import django

    django.setup()
    from django.core.wsgi import get_wsgi_application

    return get_wsgi_application()


def scenario_wsgi_threaded() -> dict:
    app = wsgi_app()
    port = free_port()
    srv = make_server("127.0.0.1", port, app, server_class=ThreadingWSGIServer)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.3)
    try:
        one_request(port)  # 预热（含 Django 首次请求开销）
        wall_brief, codes = burst(port, N_BRIEF)
        trace = ART / "trace_wsgi_threaded.jsonl"
        if trace.exists():
            trace.unlink()
        os.environ["OPS_CHECK_SLOW_TRACE"] = str(trace)
        wall_c15, probe_lat = burst_with_probe(port, N_C15)
        wall_c15b, codes_c15 = burst(port, N_C15)
        os.environ.pop("OPS_CHECK_SLOW_TRACE", None)
        tr = analyze_trace(trace)
    finally:
        srv.shutdown()
    return {"name": "WSGI 多线程(ThreadingMixIn≈gunicorn gthread)", "wall_brief": wall_brief,
            "wall_c15": wall_c15b, "probe_latency": probe_lat, "codes": sorted(set(codes)),
            "trace": tr}


def scenario_wsgi_single() -> dict:
    app = wsgi_app()
    port = free_port()
    srv = make_server("127.0.0.1", port, app)  # 默认单线程 WSGIServer
    srv.request_queue_size = 256
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.3)
    try:
        one_request(port)
        wall_brief, codes = burst(port, N_BRIEF)
    finally:
        srv.shutdown()
    return {"name": "WSGI 单线程(对照组)", "wall_brief": wall_brief, "wall_c15": None,
            "probe_latency": None, "codes": sorted(set(codes)), "trace": {}}


def scenario_daphne() -> dict:
    port = free_port()
    trace = ART / "trace_daphne.jsonl"
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
    log = ART / "D_daphne_stdout.log"
    with open(log, "wb") as fh:
        proc = subprocess.Popen(
            [str(PYTHON), "-m", "daphne", "-b", "127.0.0.1", "-p", str(port),
             "backend.asgi:application"],
            cwd=str(BACKEND), env=env, stdout=fh, stderr=subprocess.STDOUT,
        )
    try:
        deadline = time.time() + 40
        ready = False
        while time.time() < deadline:
            if proc.poll() is not None:
                break
            try:
                status, _ = one_request(port, timeout=3)
                if status == 200:
                    ready = True
                    break
            except Exception:  # noqa: BLE001
                time.sleep(0.3)
        if not ready:
            tail = log.read_text(encoding="utf-8", errors="replace")[-1500:]
            return {"name": "daphne(ASGI)", "error": f"未就绪；日志尾部：\n{tail}",
                    "wall_brief": None, "wall_c15": None, "probe_latency": None,
                    "codes": [], "trace": {}}
        wall_brief, codes = burst(port, N_BRIEF)
        wall_c15, probe_lat = burst_with_probe(port, N_C15)
        wall_c15b, _ = burst(port, N_C15)
        time.sleep(0.4)  # 等中间件把 trace 刷完
        tr = analyze_trace(trace)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    return {"name": "daphne(ASGI)", "wall_brief": wall_brief, "wall_c15": wall_c15b,
            "probe_latency": probe_lat, "codes": sorted(set(codes)), "trace": tr}


# --------------------------------------------------------------------------
def report(res: dict) -> None:
    if res.get("error"):
        print(f"[SCENARIO] {res['name']} ERROR: {res['error']}", flush=True)
        return
    tr = res.get("trace") or {}
    print(
        f"[SCENARIO] {res['name']}: N={N_BRIEF} 并发总墙钟={res['wall_brief']:.2f}s "
        f"(单请求={DELAY_S:.2f}s)；N={N_C15} 总墙钟="
        + (f"{res['wall_c15']:.2f}s" if res.get("wall_c15") else "n/a")
        + "；在途追加请求延迟="
        + (f"{res['probe_latency']:.2f}s" if res.get("probe_latency") else "n/a")
        + f"；状态码={res['codes']}；最大同时 sleeping={tr.get('peak_overlap')}"
        + f"（线程数={tr.get('threads')}）",
        flush=True,
    )


def main() -> int:
    pc.bootstrap(need_django=False)
    print(f"[INFO] 单请求固定耗时={DELAY_S:.2f}s（OPS_CHECK_SLOW_MS={SLOW_MS}），"
          f"CPU={os.cpu_count()}", flush=True)
    if not (ART / "db_copy.sqlite3").exists():
        subprocess.run([str(PYTHON), str(HERE / "make_db_copy.py")], check=True)
    a = scenario_wsgi_threaded()
    b = scenario_wsgi_single()
    c = scenario_daphne()
    for r in (a, b, c):
        report(r)

    serial = DELAY_S * (N_BRIEF - 1)

    # ---- 验收标准（简报 C1.5 / 设计说明 §6-6）----
    pc.check("D1-wsgi-20-concurrent-near-single-time",
             a.get("wall_c15") is not None and a["wall_c15"] < DELAY_S + 0.4,
             f"C1.5①：WSGI N={N_C15} 并发总墙钟 {a.get('wall_c15')} < {DELAY_S + 0.4:.2f}s"
             f"（≈单请求 {DELAY_S:.2f}s，而非 {N_C15}×）")
    pc.check("D2-wsgi-inflight-probe-latency",
             a.get("probe_latency") is not None and a["probe_latency"] < 1.0,
             f"C1.5②：{N_C15} 在途时追加请求延迟 {a.get('probe_latency')} < 1s（WSGI）")
    pc.check("D3-wsgi-really-parallel",
             (a.get("trace") or {}).get("peak_overlap", 0) and a["trace"]["peak_overlap"] >= N_C15 - 1,
             f"WSGI 侧最大同时 sleeping={(a.get('trace') or {}).get('peak_overlap')}"
             f"（应≈{N_C15}，证明真的并行而不是测量错觉）")
    pc.check("D4-single-thread-control-serial",
             b.get("wall_brief") is not None and b["wall_brief"] > serial,
             f"对照：单线程服务器 {N_BRIEF} 并发 {b.get('wall_brief')} > {serial:.2f}s（串行，证明测量能识别串行）")
    pc.check("D5-all-200", a.get("codes") == [200] and b.get("codes") == [200] and c.get("codes") == [200],
             f"三场景状态码：{a.get('codes')}/{b.get('codes')}/{c.get('codes')}")
    pc.check("D6-daphne-also-parallel-and-fast",
             c.get("wall_c15") is not None and c["wall_c15"] < DELAY_S + 0.4,
             f"C1.5 在**改造前形态**（daphne）下同样成立：N={N_C15} 墙钟 {c.get('wall_c15')}"
             f" < {DELAY_S + 0.4:.2f}s，最大同时 sleeping={(c.get('trace') or {}).get('peak_overlap')}")

    # ---- 对抗式发现：简报 C1.1/C1.3 的机制在真实请求路径上不成立 ----
    daphne_peak = (c.get("trace") or {}).get("peak_overlap")
    if daphne_peak and daphne_peak >= N_C15 - 1:
        pc.finding(
            "F1-briefing-c1-premise-not-reproducible",
            f"daphne(ASGI) 下 {N_C15} 个并发请求的最大同时 sleeping={daphne_peak}"
            "（每请求一个线程）——与简报 C1.1/C1.3「全进程共用 single_thread_executor=1 线程」矛盾。"
            "根因：django/core/handlers/asgi.py:169 `async with ThreadSensitiveContext():` 使 "
            "sync_to_async(thread_sensitive=True) 落到 asgiref/sync.py:467-478 的"
            "「每请求一个 ThreadPoolExecutor(max_workers=1)」，而非 asgiref/sync.py:488 的进程级单线程执行器。"
            "简报 C1.3 的实验直接调 sync_to_async 而**没有** ThreadSensitiveContext，故得到 1.61s 串行。",
        )
    if c.get("wall_brief") and a.get("wall_brief") and c["wall_brief"] <= a["wall_brief"] * 1.5:
        pc.finding(
            "F2-c1a-not-needed-for-concurrency",
            f"改造前形态 daphne 的并发表现（N={N_BRIEF} 墙钟 {c['wall_brief']:.2f}s）"
            f"不劣于 WSGI 多线程（{a['wall_brief']:.2f}s）→ C1-a 对「并发度」的收益在本机无法观测到；"
            "其真实收益应表述为「有界线程池/背压 + 长连接与短请求进程隔离」，"
            "同时引入跨进程总线与门禁白名单等新风险面（见报告风险清单）。",
        )
    return pc.finish()


if __name__ == "__main__":
    raise SystemExit(main())
