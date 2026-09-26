# -*- coding: utf-8 -*-
"""验收探针公共脚手架（tests/ops_check 专用）。

只做三件事，避免每个探针重复：
1. 把仓库根与 backend 目录加入 sys.path（探针既要从 backend 导入 Django 设置，
   又要以 `tests.ops_check.*` 形式导入本目录模块）；
2. 打印统一的 `[PASS]/[FAIL]/[INFO]` 行并累计失败数；
3. 结束时按失败数决定进程退出码（0 = 全过；1 = 有 FAIL），便于 verify_all.py 汇总。

注意：探针**不修改任何实现文件**；需要数据库时一律使用 `OPS_CHECK_DB` 指向的副本。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
BACKEND = REPO / "backend"
ARTIFACTS = HERE / "_artifacts"
DB_COPY = ARTIFACTS / "db_copy.sqlite3"

_FAILS: list[str] = []
_PASSES = 0


def bootstrap(need_django: bool = True, db_copy: bool | None = None) -> None:
    """准备 sys.path / 环境变量，并按需 django.setup()。

    `db_copy=True` 时把数据库指向副本（默认：只要 DB_COPY 存在就用副本）。
    """
    for p in (str(REPO), str(BACKEND)):
        if p not in sys.path:
            sys.path.insert(0, p)
    ARTIFACTS.mkdir(parents=True, exist_ok=True)

    use_copy = DB_COPY.exists() if db_copy is None else db_copy
    if need_django:
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.ops_check.probe_settings")
        if use_copy:
            os.environ.setdefault("OPS_CHECK_DB", str(DB_COPY))
        import django

        django.setup()


def ok(check_id: str, message: str = "") -> None:
    global _PASSES
    _PASSES += 1
    print(f"[PASS] {check_id} {message}".rstrip(), flush=True)


def fail(check_id: str, message: str = "") -> None:
    _FAILS.append(check_id)
    print(f"[FAIL] {check_id} {message}".rstrip(), flush=True)


def info(message: str) -> None:
    print(f"[INFO] {message}", flush=True)


_FINDINGS: list[str] = []


def finding(check_id: str, message: str = "") -> None:
    """记录一条「对抗式发现」：不参与 PASS/FAIL 判定，但必须进报告。

    用于「实测结果与设计/简报描述不一致」这类事项——它不是实现缺陷的 FAIL，
    也不能被静默吞掉。
    """
    _FINDINGS.append(check_id)
    print(f"[FINDING] {check_id} {message}".rstrip(), flush=True)


def check(check_id: str, condition: bool, message: str = "") -> bool:
    if condition:
        ok(check_id, message)
    else:
        fail(check_id, message)
    return bool(condition)


def finish() -> int:
    print(
        f"[SUMMARY] pass={_PASSES} fail={len(_FAILS)} findings={len(_FINDINGS)}"
        + (f" failed_ids={','.join(_FAILS)}" if _FAILS else "")
        + (f" finding_ids={','.join(_FINDINGS)}" if _FINDINGS else ""),
        flush=True,
    )
    sys.exit(1 if _FAILS else 0)
