"""X-10 回归：部署/升级脚本必须有可用的一致性回滚副本与"migrate 后失败"的回滚路径。

改前实测（真实 Git bash；A 段让写入进程保持运行、WAL 未 checkpoint，再分别取副本）：

    ### X-10 探针
    [A] 活库（写入进程保持运行、WAL 未 checkpoint）上的副本对比
         4096 live.sqlite3
        32768 live.sqlite3-shm
       650992 live.sqlite3-wal
      snapshot_sqlite_consistent: 成功
      copy-plain.sqlite3: 打不开/损坏 -> no such table: t     ← cp -a 活库拿到的副本根本不能用
      copy-snapshot.sqlite3: integrity_check=ok 可见行数=3500
    [B] print_rollback_hint 的实际输出（见 harness 输出）

  改前两个脚本的备份都是 `cp -a <活库> … || true`（失败还被吞掉），而它是**唯一的回滚副本**；
  并且 `migrate` 之后还有 collectstatic / 前端构建 / 重启服务等步骤，任何一步失败都留下
  "新库结构 + 旧代码 + 服务停摆"，脚本里既没有失败陷阱也没有回滚指引。

运行：backend/.venv/Scripts/python.exe tests/fix_verify/scripts/test_x10_rollback_path.py
"""

from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
DEPLOY = REPO / "scripts" / "deploy-linux.sh"
UPDATE = REPO / "scripts" / "update-from-github.sh"
LIB = REPO / "scripts" / "lib" / "deploy-common.sh"
HARNESS = REPO / "code_audit" / "_x10_harness.sh"
PY = REPO / "backend" / ".venv" / "Scripts" / "python.exe"


def _find_bash() -> str | None:
    for cand in (r"D:\Git\bin\bash.exe", "/bin/bash", "/usr/bin/bash"):
        p = Path(cand)
        if not p.is_file() or "WindowsApps" in str(p):
            continue
        try:
            proc = subprocess.run(
                [str(p), "-c", "echo ok"], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=60,
            )
            if proc.returncode == 0 and "ok" in proc.stdout:
                return str(p)
        except Exception:  # noqa: BLE001
            continue
    found = shutil.which("bash")
    return found if found and "WindowsApps" not in found else None


class X10LibTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.lib = LIB.read_bytes().decode("utf-8")

    def test_snapshot_helper_exists_and_uses_vacuum_into(self):
        self.assertIn("snapshot_sqlite_consistent() {", self.lib)
        self.assertIn("VACUUM INTO ?", self.lib)
        self.assertIn("pragma integrity_check", self.lib)
        self.assertIn("PYTHON_FOR_SNAPSHOT", self.lib)

    def test_rollback_hint_helper_exists(self):
        self.assertIn("print_rollback_hint() {", self.lib)
        for needle in (
            "git -C",
            "pip",
            "npm ci",
            "api/health",
            "只含数据",
        ):
            self.assertIn(needle, self.lib, f"回滚指引缺少：{needle}")

        # 停服命令：C1-a 之后**写这个库的进程从 2 个变成 3 个**（多了 gunicorn/WSGI，
        # 且它是 /api/ 的主力写入方），所以这里不再写死 "gipfel gipfel-logviewer" 字面量，
        # 而改为断言「同一条停服命令必须覆盖三个进程」——要求比原来更严，而不是放宽。
        stop_lines = [ln for ln in self.lib.splitlines() if "systemctl stop" in ln]
        self.assertTrue(stop_lines, "回滚指引必须给出停服命令（先停服再动库）")
        self.assertTrue(
            any(
                all(s in ln for s in ("gipfel", "gipfel-wsgi", "gipfel-logviewer"))
                for ln in stop_lines
            ),
            f"停服命令必须同时停掉 daphne/WSGI/日志查看器三个写库进程：{stop_lines}",
        )

        # C2 阶段 1 起库是 WAL 模式：恢复前必须删 -wal/-shm 残留（否则旧 WAL 会被重放到恢复出来的库上）
        self.assertIn("db.sqlite3-wal", self.lib, "恢复指引必须删 -wal 残留")
        self.assertIn("db.sqlite3-shm", self.lib, "恢复指引必须删 -shm 残留")


class X10StaticTests(unittest.TestCase):
    def _code(self, path: Path) -> str:
        return "\n".join(
            ln
            for ln in path.read_bytes().decode("utf-8").splitlines()
            if not ln.lstrip().startswith("#")
        )

    def test_both_scripts_install_exit_trap(self):
        for path in (DEPLOY, UPDATE):
            code = self._code(path)
            with self.subTest(script=path.name):
                self.assertIn("trap _on_exit EXIT", code)
                self.assertIn('"$MIGRATE_STARTED" == "1"', code)
                self.assertIn("print_rollback_hint", code)

    def test_migrate_flags_wrap_the_migrate_call(self):
        for path in (DEPLOY, UPDATE):
            code = self._code(path)
            with self.subTest(script=path.name):
                self.assertIn("MIGRATE_STARTED=1", code)
                self.assertIn("MIGRATE_OK=1", code)
                migrate_at = code.find("MIGRATE_STARTED=1")
                ok_at = code.find("MIGRATE_OK=1")
                self.assertLess(migrate_at, ok_at, "MIGRATE_OK 必须在 migrate 之后")
                self.assertIn("manage.py migrate", code[migrate_at:ok_at])

    def test_db_backup_uses_consistent_snapshot(self):
        for path in (DEPLOY, UPDATE):
            code = self._code(path)
            with self.subTest(script=path.name):
                self.assertIn("snapshot_sqlite_consistent", code)
                # 不允许再把**活库**当作 cp 的源做备份（从备份恢复回去的 cp 是正常的）
                self.assertNotIn(
                    'cp -a "$INSTALL_DIR/backend/db.sqlite3"', code,
                    "仍在用 cp -a 备份活库",
                )

    def test_sources_shared_lib(self):
        for path in (DEPLOY, UPDATE):
            with self.subTest(script=path.name):
                self.assertIn("deploy-common.sh", path.read_bytes().decode("utf-8"))


class X10RuntimeTests(unittest.TestCase):
    def test_snapshot_is_usable_and_hint_is_printed(self):
        if _find_bash() is None:
            self.skipTest("本机没有可执行的 bash，跳过真实执行（静态断言已覆盖）")
        if not HARNESS.is_file():
            self.skipTest("code_audit/_x10_harness.sh 不存在（非交付物，允许缺失）")

        proc = subprocess.run(
            [_find_bash(), str(HARNESS)], cwd=str(REPO), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=600,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        self.assertEqual(proc.returncode, 0, out)

        self.assertIn("snapshot_sqlite_consistent: 成功", out)
        self.assertRegex(out, r"copy-snapshot\.sqlite3: integrity_check=ok 可见行数=[1-9]\d*")
        # 活库当时有未 checkpoint 的数据（-wal 非空），说明这是真正的"活库"场景
        self.assertRegex(out, r"\d{5,} .*live\.sqlite3-wal")

        for needle in (
            "回滚指引（照抄即可）",
            "systemctl stop gipfel gipfel-logviewer",
            "checkout 'abc1234def'",
            "npm ci && sudo npm run build",
            "http://127.0.0.1/api/health",
            "只含数据，不含代码",
        ):
            self.assertIn(needle, out, f"回滚指引输出缺少：{needle}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
