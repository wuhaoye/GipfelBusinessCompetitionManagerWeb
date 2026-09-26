# -*- coding: utf-8 -*-
"""并发对照实验用的「慢中间件」：先睡固定毫秒，再交给后续中间件链（真实视图）。

放在 MIDDLEWARE 最外层（见 probe_settings.py），因此：
- 在 ASGI/daphne 下，Django 的 ASGIHandler 会把**整条同步中间件栈**包成
  `sync_to_async(method, thread_sensitive=True)`（django/core/handlers/base.py:129 + :99），
  于是这次 sleep 落在 asgiref 的进程级单线程执行器上 → 期望串行；
- 在 WSGI/gunicorn（或任何多线程 WSGI 服务器）下，每个请求各自一个线程 → 期望并行。

`OPS_CHECK_SLOW_TRACE=<文件>` 时，把每次进入/离开的
「线程 id、进程 id、起止时刻」追加写入该文件（每行一个 JSON），
用于**事后判定 sleep 是否真的重叠**——只看总墙钟容易被误读。
"""
from __future__ import annotations

import json
import os
import threading
import time
import traceback


def _stack_summary(depth: int = 9) -> list[str]:
    """当前调用栈摘要（file:line:name），用于判定同步代码跑在哪个线程/执行器里。"""
    frames = traceback.extract_stack()[:-1][-depth:]
    out = []
    for f in frames:
        out.append(f"{os.path.basename(f.filename)}:{f.lineno}:{f.name}")
    return out


class SlowMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.delay = float(os.environ.get("OPS_CHECK_SLOW_MS", "200")) / 1000.0
        self._lock = threading.Lock()

    def __call__(self, request):
        trace_path = os.environ.get("OPS_CHECK_SLOW_TRACE") or ""  # 每次读，便于探针动态开关
        if not trace_path:
            time.sleep(self.delay)
            return self.get_response(request)
        t0 = time.perf_counter()
        time.sleep(self.delay)
        t1 = time.perf_counter()
        line = json.dumps(
            {
                "pid": os.getpid(),
                "thread": threading.get_ident(),
                "thread_name": threading.current_thread().name,
                "enter": t0,
                "exit": t1,
                "path": getattr(request, "path", ""),
                "stack": _stack_summary() if os.environ.get("OPS_CHECK_SLOW_STACK") else None,
            }
        )
        with self._lock:
            with open(trace_path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        return self.get_response(request)
