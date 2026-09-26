"""C2「一键回退」验证：SQLITE_TUNING_ENABLED=false 时是否完全回到改造前的连接参数。

改造前 = `journal_mode=delete` + `busy_timeout=5000`（sqlite3 默认）+ `synchronous=2`。
`SQLITE_TUNING_ENABLED=false` 现在同时关掉：① connection_created 的 PRAGMA 层；
② settings.DATABASES 的 OPTIONS["timeout"]（Lead 在封板前补的联动）。

实现要点（踩过的坑）：`override_settings(DATABASES=...)` + `connections.close_all()` 在
**非 TestCase 场景**下并不可靠 —— ConnectionHandler 的 settings 缓存与已建连接会让测量
落在 `backend/db.sqlite3`（真实库）上。故本脚本改用「临时 settings 模块」：把
`backend.settings` 全量导入后仅替换 `DATABASES['default']['NAME']`，在**独立进程**里
`django.setup()`，并硬断言连接确实落在临时库（否则结论不可信）。

不触碰 backend/db.sqlite3（含只读）。
运行：backend\\.venv\\Scripts\\python.exe tests\\c2_revert_check.py [tuned|revert]
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "backend"
PY = BACKEND / ".venv" / "Scripts" / "python.exe"

CHILD = r'''
import os, sys
from pathlib import Path

tmp_db = Path(os.environ["C2_PROBE_DB"])
repo = Path(os.environ["C2_REPO"])

import django
from django.conf import settings

settings.configure()  # noqa: F841  (占位，随后用真实模块覆盖)
django.setup()

from django.db import connections

conn = connections["default"]
with conn.cursor() as cur:
    cur.execute("PRAGMA database_list")
    db_list = cur.fetchall()
    cur.execute("PRAGMA journal_mode")
    journal = cur.fetchone()[0]
    cur.execute("PRAGMA busy_timeout")
    busy = cur.fetchone()[0]
    cur.execute("PRAGMA synchronous")
    sync = cur.fetchone()[0]

print("conn NAME     =", conn.settings_dict["NAME"])
print("database_list =", db_list)
print("journal_mode  =", journal)
print("busy_timeout  =", busy)
print("synchronous   =", sync)
print("temp file     =", tmp_db.exists())
'''


def child_source(mode: str) -> str:
    """子进程代码：自建 settings 模块（导入 backend.settings 后改 NAME）。"""
    return f'''
import os, sys, tempfile
from pathlib import Path

REPO = Path(r"{REPO}")
BACKEND = REPO / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(REPO))

os.environ["SQLITE_TUNING_ENABLED"] = "{'true' if mode == 'tuned' else 'false'}"
os.environ.setdefault("JWT_SECRET", "x" * 40)
os.environ["C2_PROBE_DIR"] = str(REPO / "tests" / ".tmp_c2_probe")

import backend.settings as base  # noqa: E402
from django.conf import settings as dj  # noqa: E402

_probe_dir = Path(os.environ["C2_PROBE_DIR"])
_probe_dir.mkdir(parents=True, exist_ok=True)
tmp_db = _probe_dir / "probe.sqlite3"
if tmp_db.exists():
    tmp_db.unlink()

import types
mod = types.ModuleType("probe_settings")
for _k in dir(base):
    if _k.isupper():
        setattr(mod, _k, getattr(base, _k))
mod.DATABASES = {{
    "default": {{**base.DATABASES["default"], "NAME": str(tmp_db)}}
}}
mod.SQLITE_PRAGMA_STATEMENTS = base.SQLITE_PRAGMA_STATEMENTS
sys.modules["probe_settings"] = mod
os.environ["DJANGO_SETTINGS_MODULE"] = "probe_settings"
os.environ["C2_PROBE_DB"] = str(tmp_db)
os.environ["C2_REPO"] = str(REPO)

import django
django.setup()

from django.db import connections

conn = connections["default"]
with conn.cursor() as cur:
    cur.execute("PRAGMA database_list")
    db_list = cur.fetchall()
    cur.execute("PRAGMA journal_mode")
    journal = cur.fetchone()[0]
    cur.execute("PRAGMA busy_timeout")
    busy = cur.fetchone()[0]
    cur.execute("PRAGMA synchronous")
    sync = cur.fetchone()[0]

name = str(conn.settings_dict["NAME"])
print("conn NAME     =", name)
print("database_list =", db_list)
print("journal_mode  =", journal)
print("busy_timeout  =", busy)
print("synchronous   =", sync)
print("temp file     =", tmp_db.exists())
print("OPTIONS       =", dj.DATABASES["default"].get("OPTIONS"))

if Path(name).resolve() != tmp_db.resolve():
    print("RESULT: FAIL —— 连接未指向临时库，测量值不可信")
    sys.exit(1)

if "{mode}" == "revert":
    ok = journal == "delete" and busy == 5000 and sync == 2
    print("期望（改造前）: delete / 5000 / 2")
else:
    ok = journal == "wal" and busy == 20000 and sync == 1
    print("期望（调优后）: wal / 20000 / 1")
print("RESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
'''


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "revert"
    if mode not in {"tuned", "revert"}:
        print(f"usage: {Path(__file__).name} [tuned|revert]")
        return 2
    probe_dir = REPO / "tests" / ".tmp_c2_probe"
    probe_dir.mkdir(parents=True, exist_ok=True)
    tmp = probe_dir / "child.py"
    tmp.write_text(child_source(mode), encoding="utf-8")
    print(f"--- mode={mode} (独立进程，临时库) ---")
    proc = subprocess.run([str(PY), str(tmp)], capture_output=True, text=True)
    sys.stdout.write(proc.stdout)
    if proc.stderr.strip():
        sys.stderr.write(proc.stderr)
    # 清理产物（脚本可重复运行，不留垃圾）
    for leftover in probe_dir.glob("probe.sqlite3*"):
        leftover.unlink(missing_ok=True)
    tmp.unlink(missing_ok=True)
    try:
        probe_dir.rmdir()
    except OSError:
        pass
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
