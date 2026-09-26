"""C1 机制复核探针（Lead 独立复现）：简报 C1.1/C1.3 的「单线程瓶颈」在真实请求路径上是否成立。

背景：简报用「直接调用 `sync_to_async(..., thread_sensitive=True)`」测得 8 并发串行 1.61s，
据此断言「全进程所有同步视图共用 asgiref 的进程级 single_thread_executor（max_workers=1）」。

但 Django 的 ASGI handler 在**每个请求**外面包了 `async with ThreadSensitiveContext():`
（`django/core/handlers/asgi.py:169`），而 asgiref 在 ThreadSensitiveContext 生效时走的是
**该 context 专属的** `ThreadPoolExecutor(max_workers=1)`（`asgiref/sync.py:467-478`），
只有无 context 时才落到进程级 `single_thread_executor`（`asgiref/sync.py:488`）。
即：真实请求 = 每个请求一个专属线程（请求之间并行），而**不是**全进程共用一条线程。

本脚本用三种形态各跑 8 个「睡 0.20s」的同步任务，做同机对照：
  A) 无 context（= 简报 C1.3 的测法）          → 预期串行 ≈1.6s
  B) 每任务一个 ThreadSensitiveContext（模拟真实请求路径） → 预期并行 ≈0.2s
  C) 同一 context 内 8 个任务（同一请求内的串行同步工作）   → 预期串行 ≈1.6s（每请求内仍是单线程）

运行：backend\\.venv\\Scripts\\python.exe tests\\c1_mechanism_probe.py
"""
from __future__ import annotations

import asyncio
import threading
import time

from asgiref.sync import ThreadSensitiveContext, sync_to_async

SLEEP = 0.20
N = 8
_seen: list[int] = []
_lock = threading.Lock()


def _work(tag: str) -> str:
    with _lock:
        _seen.append(threading.get_ident())
    time.sleep(SLEEP)
    return tag


async def scenario_a() -> tuple[float, int]:
    """无 ThreadSensitiveContext（简报 C1.3 的原始测法）。"""
    _seen.clear()
    t0 = time.perf_counter()
    await asyncio.gather(*[sync_to_async(_work, thread_sensitive=True)(f"a{i}") for i in range(N)])
    return time.perf_counter() - t0, len(set(_seen))


async def scenario_b() -> tuple[float, int]:
    """每个任务独立 ThreadSensitiveContext（≈ Django ASGI handler 的每请求形态）。"""
    _seen.clear()

    async def one(i: int) -> str:
        async with ThreadSensitiveContext():
            return await sync_to_async(_work, thread_sensitive=True)(f"b{i}")

    t0 = time.perf_counter()
    await asyncio.gather(*[one(i) for i in range(N)])
    return time.perf_counter() - t0, len(set(_seen))


async def scenario_c() -> tuple[float, int]:
    """同一 ThreadSensitiveContext 内 8 个任务（≈ 单个请求内的多段同步工作）。"""
    _seen.clear()
    async with ThreadSensitiveContext():
        t0 = time.perf_counter()
        await asyncio.gather(
            *[sync_to_async(_work, thread_sensitive=True)(f"c{i}") for i in range(N)]
        )
        return time.perf_counter() - t0, len(set(_seen))


async def main() -> int:
    rows = []
    for name, coro in (("A no-context", scenario_a()), ("B per-task-context", scenario_b()), ("C shared-context", scenario_c())):
        wall, threads = await coro
        rows.append((name, wall, threads))
        print(f"{name:<20} wall={wall:.2f}s  distinct_threads={threads}  (serial≈{N * SLEEP:.2f}s, parallel≈{SLEEP:.2f}s)")

    wall_a = rows[0][1]
    wall_b = rows[1][1]
    wall_c = rows[2][1]
    ok_a = wall_a > 1.2
    ok_b = wall_b < 0.6
    ok_c = wall_c > 1.2
    print()
    print(f"CHECK A 无 context 串行（≈{N * SLEEP:.2f}s）           : {'PASS' if ok_a else 'FAIL'}  ({wall_a:.2f}s)")
    print(f"CHECK B 每任务 context 并行（≈{SLEEP:.2f}s）            : {'PASS' if ok_b else 'FAIL'}  ({wall_b:.2f}s, threads={rows[1][2]})")
    print(f"CHECK C 同 context 内串行（≈{N * SLEEP:.2f}s）          : {'PASS' if ok_c else 'FAIL'}  ({wall_c:.2f}s, threads={rows[2][2]})")
    print()
    if ok_a and ok_b and ok_c:
        print("RESULT: PASS —— 简报 C1.1/C1.3 的测法可复现（A 串行），但**不代表真实请求路径**：")
        print("        Django ASGI handler 每请求一个 ThreadSensitiveContext（B 并行），")
        print("        单请求内部的同步工作仍串行（C）。")
        return 0
    print("RESULT: FAIL —— 与预期不符，需重新核对框架版本与调用方式")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
