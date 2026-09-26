# -*- coding: utf-8 -*-
"""C1 机制复核（对抗式）：Django 的 ASGI 路径到底有没有把同步中间件串行化？

背景：简报 C1.1/C1.3 断言「全部同步视图经 sync_to_async(thread_sensitive=True) 走同一个
单线程执行器」，并用一段**直接调用 asgiref** 的实验（8 个 sleep(0.2) → 1.61s）作为证据。
本脚本分三层复核：
  ① 原样复现简报那段 asgiref 实验（机制本身是否成立）；
  ② 检查 `ASGIHandler._middleware_chain` 的真实类型与 `thread_sensitive` 标志；
  ③ **不经过 daphne**，在进程内用真实 ASGI scope 并发驱动 `backend.asgi:application`
     8 次，用慢中间件记录线程 id 与进出场时间，看是否重叠。
用法（仓库根目录）：
    backend\\.venv\\Scripts\\python.exe tests\\ops_check\\_probe_c1_mechanism.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import _probe_common as pc  # noqa: E402

ART = pc.ARTIFACTS


def step1_asgiref_repro() -> None:
    from asgiref.sync import sync_to_async

    seen: list[tuple[str, int]] = []

    def work(tag: str) -> None:
        seen.append((tag, threading.get_ident()))
        time.sleep(0.20)

    async def main() -> None:
        await asyncio.gather(
            *[sync_to_async(work, thread_sensitive=True)(f"req{i}") for i in range(8)]
        )

    t0 = time.time()
    asyncio.run(main())
    wall = time.time() - t0
    threads = sorted({i for _, i in seen})
    print(f"[STEP1] asgiref 直连 sync_to_async(thread_sensitive=True)："
          f"thread_ids={threads} wall={wall:.2f}s"
          f"（简报 C1.3 声称 1.61s / 单线程）", flush=True)


def step2_inspect_chain() -> None:
    from django.core.asgi import get_asgi_application

    app = get_asgi_application()
    chain = app._middleware_chain
    print(f"[STEP2] ASGIHandler._middleware_chain 类型={type(chain).__module__}."
          f"{type(chain).__name__}", flush=True)
    print(f"[STEP2] _thread_sensitive={getattr(chain, '_thread_sensitive', None)} "
          f"func={getattr(chain, 'func', None)}", flush=True)
    print(f"[STEP2] single_thread_executor={getattr(type(chain), 'single_thread_executor', None)}",
          flush=True)


async def _call_asgi(app, path: str) -> list[dict]:
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "GET", "path": path, "raw_path": path.encode(), "query_string": b"",
        "root_path": "", "headers": [(b"host", b"127.0.0.1")],
        "client": ("127.0.0.1", 45678), "server": ("127.0.0.1", 80), "scheme": "http",
    }
    messages: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    await app(scope, receive, send)
    return messages


def step3_inprocess_asgi() -> None:
    trace = ART / "trace_inprocess_asgi.jsonl"
    if trace.exists():
        trace.unlink()
    os.environ["OPS_CHECK_SLOW_TRACE"] = str(trace)
    from backend.asgi import application

    async def main() -> None:
        results = await asyncio.gather(*[_call_asgi(application, "/api/health") for _ in range(8)])
        codes = [
            next((m["status"] for m in msgs if m["type"] == "http.response.start"), None)
            for msgs in results
        ]
        print(f"[STEP3] 进程内 ASGI 并发 8 请求状态码={codes}", flush=True)

    t0 = time.perf_counter()
    asyncio.run(main())
    wall = time.perf_counter() - t0
    rows = []
    if trace.exists():
        rows = [json.loads(ln) for ln in trace.read_text(encoding="utf-8").splitlines() if ln.strip()]
    ev = []
    for r in rows:
        ev.append((r["enter"], 1))
        ev.append((r["exit"], -1))
    ev.sort()
    cur = peak = 0
    for _t, d in ev:
        cur += d
        peak = max(peak, cur)
    print(f"[STEP3] 进程内 ASGI 8 并发 wall={wall:.2f}s 参与者={len(rows)} "
          f"线程数={len({r['thread'] for r in rows})} 最大重叠={peak}", flush=True)


def main() -> int:
    os.environ["OPS_CHECK_SLOW_MS"] = os.environ.get("OPS_CHECK_SLOW_MS", "200")
    pc.bootstrap(need_django=True)
    step1_asgiref_repro()
    step2_inspect_chain()
    step3_inprocess_asgi()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
