"""只读体检 backend/db.sqlite3（C2 上线后 dev 库已切 WAL 的验收证据）。

只读打开（mode=ro），不做任何写操作、不动 -wal/-shm。
运行：backend\\.venv\\Scripts\\python.exe tests\\db_health_readonly.py
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DB = REPO / "backend" / "db.sqlite3"

if not DB.exists():
    print(f"[SKIP] 库不存在: {DB}")
    sys.exit(0)

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
try:
    rows = {
        "journal_mode": con.execute("PRAGMA journal_mode").fetchone()[0],
        "integrity_check": con.execute("PRAGMA integrity_check").fetchone()[0],
        "quick_check": con.execute("PRAGMA quick_check").fetchone()[0],
        "tables": con.execute(
            "select count(*) from sqlite_master where type='table'"
        ).fetchone()[0],
    }
    try:
        rows["audit_log_rows"] = con.execute("select count(*) from audit_log").fetchone()[0]
    except sqlite3.Error as exc:  # 表名变化时不影响其它结论
        rows["audit_log_rows"] = f"<{exc}>"
    try:
        rows["users_rows"] = con.execute("select count(*) from users").fetchone()[0]
    except sqlite3.Error as exc:
        rows["users_rows"] = f"<{exc}>"
finally:
    con.close()

for k, v in rows.items():
    print(f"{k:>18} = {v}")

sidecars = sorted(p.name for p in DB.parent.glob("db.sqlite3-*"))
print(f"{'sidecars':>18} = {sidecars or '（无 -wal/-shm：上次关闭已 checkpoint）'}")
ok = rows["integrity_check"] == "ok" and rows["quick_check"] == "ok"
print("RESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
