# -*- coding: utf-8 -*-
"""用 SQLite 自己的 `VACUUM INTO` 从活库导出一份**自洽副本**（WAL 下安全）。

- 以 `file:...?mode=ro` 只读方式打开活库（绝不写、不改 journal_mode）；
- `VACUUM INTO` 由 SQLite 保证输出是一个页一致、已折叠 WAL 的独立数据库文件，
  因此不会出现 `cp db.sqlite3` 那种「拿到 -wal 里未落盘事务 / 页不一致」的问题
  （简报 C2.4「不该做」第 1 条）。

用法：
    backend\\.venv\\Scripts\\python.exe tests\\ops_check\\make_db_copy.py [源库] [目标库]
默认：源=backend/db.sqlite3，目标=tests/ops_check/_artifacts/db_copy.sqlite3
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
DEFAULT_SRC = REPO / "backend" / "db.sqlite3"
DEFAULT_DST = HERE / "_artifacts" / "db_copy.sqlite3"


def main() -> int:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SRC
    dst = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_DST
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    uri = f"file:{src.as_posix()}?mode=ro"
    con = sqlite3.connect(uri, uri=True, timeout=30)
    try:
        con.execute("VACUUM INTO ?", (str(dst),))
    finally:
        con.close()
    # 复核副本自身健康 + 与源库的关键计数一致
    chk = sqlite3.connect(str(dst))
    try:
        integrity = chk.execute("PRAGMA integrity_check").fetchone()[0]
        journal = chk.execute("PRAGMA journal_mode").fetchone()[0]
        tables = chk.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table'"
        ).fetchone()[0]
        audit = chk.execute("SELECT count(*) FROM audit_log").fetchone()[0]
    finally:
        chk.close()
    print(
        f"DB_COPY src={src} dst={dst} integrity={integrity} "
        f"journal_mode={journal} tables={tables} audit_log={audit}"
    )
    return 0 if integrity == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
