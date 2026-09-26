# -*- coding: utf-8 -*-
"""验收探针专属 settings（只读地继承 backend.settings，按需覆盖）。

两个用途：
1. `OPS_CHECK_DB=<路径>` → 把 default 数据库指向**副本**，保证所有写操作
   （company_fields、audit_archive、gate 状态等）都不会碰到 `backend/db.sqlite3`。
2. `OPS_CHECK_SLOW_MS=<毫秒>` → 在中间件链最外层注入一个「先睡 N 毫秒」的中间件，
   用于 C1 的并发对照实验（ASGI/daphne 会被 asgiref 单线程执行器串行，WSGI 不会）。

本文件不复制任何业务逻辑，`from backend.settings import *` 保证除显式覆盖项外
与生产设置逐项一致（含 MIDDLEWARE / DATABASES ENGINE / INSTALLED_APPS）。
"""
from __future__ import annotations

import os

from backend.settings import *  # noqa: F401,F403  （探针：继承真实设置）

# ---------------- 数据库指向副本 ----------------
_OPS_DB = os.environ.get("OPS_CHECK_DB", "").strip()
if _OPS_DB:
    DATABASES = {  # noqa: F405
        **DATABASES,  # noqa: F405
        "default": {**DATABASES["default"], "NAME": _OPS_DB},  # noqa: F405
    }

# ---------------- 并发对照：最外层慢中间件 ----------------
_SLOW_MS = os.environ.get("OPS_CHECK_SLOW_MS", "").strip()
if _SLOW_MS:
    MIDDLEWARE = [  # noqa: F405
        "tests.ops_check.probe_slow_middleware.SlowMiddleware",
        *MIDDLEWARE,  # noqa: F405
    ]

# ---------------- 快照归档目录也指到探针工作区 ----------------
# apps/snapshots 默认把归档写到 <BASE_DIR>/snapshots、把上传文件目录当 UPLOAD_DIR；
# 探针跑闭环时不应污染仓库里的真实目录，故显式改到 _artifacts 下。
_OPS_SNAP_DIR = os.environ.get("OPS_CHECK_SNAPSHOT_DIR", "").strip()
if _OPS_SNAP_DIR:
    SNAPSHOT_DIR = _OPS_SNAP_DIR  # noqa: F405  （只改写入目录；uploads 只读，保持真实值）
