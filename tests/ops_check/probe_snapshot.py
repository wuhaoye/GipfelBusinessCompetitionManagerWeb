# -*- coding: utf-8 -*-
"""G 组：备份/恢复链路与快照系统（简报 §5 最后一条）。

覆盖：
- `snapshot_sqlite_consistent` 是否用 `VACUUM INTO`（WAL 下唯一安全的活库导出方式）；
- WAL 库经 `VACUUM INTO` 得到的副本是否**自洽**（含未 checkpoint 的写入）且不带 -wal；
- `apps.snapshots` 的测试是否全绿；
- `tests/snapshot_tools/roundtrip_real_db.py` 的「快照→破坏→回退→逐表比对」闭环
  在**副本**上跑通（用探针 settings 把 DB 与归档目录都指到 _artifacts，绝不碰活库）；
- 部署文档/脚本的回滚指引是否含「先停服 + rm -f db.sqlite3-wal/-shm」。

用法（仓库根目录）：
    backend\\.venv\\Scripts\\python.exe tests\\ops_check\\probe_snapshot.py
"""
from __future__ import annotations

import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import _probe_common as pc  # noqa: E402

REPO, BACKEND, ART = pc.REPO, pc.BACKEND, pc.ARTIFACTS
PYTHON = BACKEND / ".venv" / "Scripts" / "python.exe"
LIB = REPO / "scripts" / "lib" / "deploy-common.sh"
DEPLOY_README = REPO / "deploy" / "README.md"
ROUNDTRIP = REPO / "tests" / "snapshot_tools" / "roundtrip_real_db.py"


def _env(**extra) -> dict:
    env = dict(os.environ)
    env.update({
        "DJANGO_SETTINGS_MODULE": "tests.ops_check.probe_settings",
        "PYTHONPATH": os.pathsep.join([str(REPO), str(BACKEND), env.get("PYTHONPATH", "")]),
        "PYTHONIOENCODING": "utf-8",
    })
    env.update(extra)
    return env


def check_shell_helper() -> None:
    lib = LIB.read_text(encoding="utf-8")
    fn_start = lib.find("snapshot_sqlite_consistent() {")
    fn = lib[fn_start:lib.find("\n}\n", fn_start)] if fn_start >= 0 else ""
    pc.check("G1-shell-helper-exists", fn_start >= 0 and "VACUUM INTO ?" in fn,
             "`snapshot_sqlite_consistent()` 用 SQLite `VACUUM INTO ?`（WAL 下安全的活库导出）")
    # 关键：不得把**活库**直接 cp 给回滚副本。逐分支核对 helper 里的 cp 用法。
    cp_lines = [ln.strip() for ln in fn.splitlines() if "cp -a" in ln]
    pc.check("G1-cp-only-documented-fallback",
             all(ln.startswith("warn") or ln.startswith("cp -a") for ln in cp_lines)
             and any(ln.startswith("warn") for ln in cp_lines),
             f"helper 主路径用 VACUUM INTO；`cp -a` 只出现在「无 python3」降级分支且伴随明确 warning；"
             f"cp 行={cp_lines}")
    if cp_lines:
        warned = any("warn" in ln and "cp -a" in ln for ln in fn.splitlines())
        pc.finding(
            "F3-cp-fallback-residual-risk",
            f"`snapshot_sqlite_consistent` 在找不到 python3 时会退回 `cp -a` 活库"
            f"（{'有' if warned else '无'}显式 warning，可用 PYTHON_FOR_SNAPSHOT 指定解释器）："
            "该分支在 WAL 下可能产出不一致副本，而它是回滚链条的唯一副本 —— 现场需确认目标机 python3 可用。",
        )
    # 回滚指引里的 cp 源必须是快照/备份目录（不是活库）
    hint_start = lib.find("print_rollback_hint() {")
    hint = lib[hint_start:] if hint_start >= 0 else ""
    restore_cps = [ln for ln in hint.splitlines() if "cp -a" in ln and "db.sqlite3'" in ln]
    ok_src = all(("$db_snapshot" in ln or "$backup_dir" in ln) for ln in restore_cps)
    pc.check("G1-restore-cp-from-snapshot", ok_src and bool(restore_cps),
             f"回滚指引里的落位 cp 源是快照/备份目录：{restore_cps}")


def check_vacuum_into_wal_consistency() -> None:
    """WAL 下「有未 checkpoint 的写入」时 VACUUM INTO 的副本是否自洽。"""
    live = ART / "wal_consistency_src.sqlite3"
    snap = ART / "wal_consistency_snap.sqlite3"
    for p in (live, snap):
        if p.exists():
            p.unlink()
    con = sqlite3.connect(str(live), isolation_level=None)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA wal_autocheckpoint=0")  # 关自动 checkpoint → 写入只留在 -wal 里
    con.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    con.execute("INSERT INTO t (v) VALUES ('a')")
    con.execute("INSERT INTO t (v) VALUES ('b')")  # 未 checkpoint
    wal_bytes = (live.with_name(live.name + "-wal")).stat().st_size if live.with_name(live.name + "-wal").exists() else 0
    con.execute("VACUUM INTO ?", (str(snap),))
    con.close()
    s = sqlite3.connect(f"file:{snap.as_posix()}?mode=ro", uri=True)
    rows = s.execute("SELECT count(*) FROM t").fetchone()[0]
    integrity = s.execute("PRAGMA integrity_check").fetchone()[0]
    s.close()
    snap_wal = snap.with_name(snap.name + "-wal")
    pc.check("G2-vacuum-into-captures-uncheckpointed",
             rows == 2 and integrity == "ok",
             f"活库 -wal 里有未 checkpoint 的写入（{wal_bytes} 字节）时，"
             f"VACUUM INTO 副本行数={rows}（期望 2）、integrity={integrity}")
    pc.check("G2-snapshot-has-no-wal", not snap_wal.exists(),
             "VACUUM INTO 产物自身不带 -wal/-shm（停服后可安全落位）")


def check_snapshots_tests() -> None:
    proc = subprocess.run([str(PYTHON), "manage.py", "test", "apps.snapshots", "-v", "1"],
                          cwd=str(BACKEND), env=_env(OPS_CHECK_DB=""), capture_output=True,
                          text=True, encoding="utf-8", timeout=900)
    combined = (proc.stdout or "") + (proc.stderr or "")
    ran = re.search(r"Ran (\d+) test", combined)
    ok = re.search(r"^OK", combined, re.M)
    pc.check("G3-apps-snapshots-tests-ok",
             proc.returncode == 0 and bool(ok) and bool(ran) and int(ran.group(1)) >= 28,
             f"`manage.py test apps.snapshots` 退出码={proc.returncode}；"
             f"Ran={ran.group(1) if ran else '?'}；OK={'是' if ok else '否'}"
             f"（Django 把测试摘要写 stderr，故合并两路输出判定）")


def check_roundtrip_on_copy() -> None:
    """在副本上跑官方闭环脚本（快照→破坏→回退→比对），用探针 settings 重定向 DB 与归档目录。"""
    db = ART / "db_roundtrip.sqlite3"
    if db.exists():
        db.unlink()
    src = sqlite3.connect(f"file:{pc.DB_COPY.as_posix()}?mode=ro", uri=True)
    src.execute("VACUUM INTO ?", (str(db),))
    src.close()
    snapdir = ART / "snapshots_roundtrip"
    snapdir.mkdir(parents=True, exist_ok=True)
    before_live_mtime = (BACKEND / "db.sqlite3").stat().st_mtime
    proc = subprocess.run(
        [str(PYTHON), str(ROUNDTRIP)], cwd=str(BACKEND),
        env=_env(OPS_CHECK_DB=str(db), OPS_CHECK_SNAPSHOT_DIR=str(snapdir)),
        capture_output=True, text=True, encoding="utf-8", timeout=1800,
    )
    after_live_mtime = (BACKEND / "db.sqlite3").stat().st_mtime
    out = proc.stdout or ""
    tail = " | ".join([ln for ln in out.strip().splitlines() if ln.strip()][-4:])
    pc.check("G4-roundtrip-exit0", proc.returncode == 0,
             f"roundtrip_real_db 在副本上退出码={proc.returncode}；尾部={tail}"
             + (f"；stderr={proc.stderr[-300:]}" if proc.returncode else ""))
    pc.check("G4-roundtrip-asserts", "回退" in out and "逐表" in out or "一致" in out,
             f"闭环输出包含比对结论：{tail}")
    pc.check("G4-live-db-untouched", before_live_mtime == after_live_mtime,
             "跑闭环期间 backend/db.sqlite3 的 mtime 未变化（确认只动了副本）")
    pc.info(f"副本与归档目录：{db.name} / {snapdir.name}")


def check_rollback_docs() -> None:
    lib = LIB.read_text(encoding="utf-8")
    readme = DEPLOY_README.read_text(encoding="utf-8")
    hint_start = lib.find("print_rollback_hint() {")
    hint = lib[hint_start:hint_start + 4000] if hint_start >= 0 else ""
    stop_ok = all(s in hint for s in ("systemctl stop", "gipfel-wsgi", "gipfel-logviewer"))
    pc.check("G5-hint-stop-all-writers", stop_ok,
             "print_rollback_hint 的停服命令覆盖 gipfel / gipfel-wsgi / gipfel-logviewer 三个写库进程")
    pc.check("G5-hint-rm-wal-shm",
             "db.sqlite3-wal" in hint and "db.sqlite3-shm" in hint and "rm -f" in hint,
             "print_rollback_hint 含恢复前 `rm -f db.sqlite3-wal db.sqlite3-shm`")
    pc.check("G5-hint-vacuum-into", "VACUUM INTO" in hint,
             "print_rollback_hint 用 VACUUM INTO 制备份")
    # deploy/README.md 的回滚小节
    idx = readme.find("回滚")
    section = readme[idx:idx + 4000] if idx >= 0 else ""
    pc.check("G5-readme-stop-all", all(s in section for s in ("systemctl stop", "gipfel-wsgi")),
             "deploy/README.md 回滚小节要求先停服（含 gipfel-wsgi）")
    pc.check("G5-readme-rm-wal-shm", "db.sqlite3-wal" in section and "db.sqlite3-shm" in section,
             "deploy/README.md 回滚小节含 rm -f db.sqlite3-wal/-shm")
    pc.check("G5-readme-vacuum-into", "VACUUM INTO" in section,
             "deploy/README.md 回滚小节用 VACUUM INTO 备份当前库")


def main() -> int:
    if not pc.DB_COPY.exists():
        subprocess.run([str(PYTHON), str(HERE / "make_db_copy.py")], check=True)
    check_shell_helper()
    check_vacuum_into_wal_consistency()
    check_snapshots_tests()
    check_rollback_docs()
    check_roundtrip_on_copy()
    return pc.finish()


if __name__ == "__main__":
    raise SystemExit(main())
