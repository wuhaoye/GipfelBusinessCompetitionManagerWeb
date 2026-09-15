"""X-19 回归：start-dev.bat 必须传递真实的启动失败，dev.py 提供同步前置检查。

改前实测（真实 cmd.exe / backend/.venv python）：
  * `start` 不传递子进程退出码（cmd 语言语义）：
        > start /B cmd /c "exit 7" & echo START_RC=%ERRORLEVEL%
        START_RC=0
  * 旧 start-dev.bat 第 43-44 行是
        start "Gipfel Dev" "%PY%" "%~dp0dev.py"
        exit /b 0
    全文没有 errorlevel 检查，因此无论 dev.py 成败都以 0 返回。
  * 旧 dev.py 不认识 `--check-only`：把它当普通启动跑
        $ python scripts/dev.py --check-only        # 5173 被占用时
        Gipfel dev supervisor  (Ctrl+C stops Django + Vite + LogViewer)
        [ERROR] Port already in use: 127.0.0.1:5173
        exit=1

改后实测（同一探针）：
  * 5173 被占用时
        dev.py --check-only            → exit=1  [ERROR] Preconditions FAILED; services were NOT started.
        scripts\\start-dev.bat         → exit=1  [ERROR] Preconditions not met; services were NOT started.
  * 端口空闲时
        dev.py --check-only            → exit=0  [OK] Preconditions OK.（且不会留下任何服务）

运行：backend/.venv/Scripts/python.exe tests/fix_verify/scripts/test_x19_start_dev_exit.py
"""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

from _decoding import run_captured

REPO = Path(__file__).resolve().parents[3]
BAT = REPO / "scripts" / "start-dev.bat"
DEV_PY = REPO / "scripts" / "dev.py"
HARNESS = REPO / "code_audit" / "_x19_harness.py"
PY = REPO / "backend" / ".venv" / "Scripts" / "python.exe"


class X19StaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bat = BAT.read_bytes().decode("ascii")
        cls.bat_code = "\n".join(
            ln for ln in cls.bat.splitlines() if not ln.lstrip().upper().startswith("REM")
        )
        cls.py = DEV_PY.read_text(encoding="utf-8", errors="replace")

    def test_bat_is_still_ascii_crlf(self):
        raw = BAT.read_bytes()
        self.assertTrue(all(b < 128 for b in raw), "start-dev.bat 不再是纯 ASCII")
        self.assertEqual(raw.count(b"\r\n"), raw.count(b"\n"), "存在非 CRLF 行尾")

    def test_bat_runs_synchronous_check_before_start(self):
        check_at = self.bat_code.find("--check-only")
        start_at = self.bat_code.find('start "Gipfel Dev"')
        self.assertNotEqual(check_at, -1, "start-dev.bat 没有同步前置检查")
        self.assertNotEqual(start_at, -1)
        self.assertLess(check_at, start_at, "同步检查必须在 start 之前")
        self.assertIn("if errorlevel 1", self.bat_code, "同步检查后没有检查退出码")

    def test_bat_does_not_return_zero_unconditionally(self):
        m = re.search(r'start "Gipfel Dev"[^\n]*\n(.*?)exit /b 0', self.bat_code, re.S)
        self.assertIsNotNone(m, "找不到 start 之后的出口")
        self.assertIn("errorlevel", m.group(1), "start 之后没有 errorlevel 检查，仍会恒返回 0")

    def test_dev_py_has_check_only_entry(self):
        self.assertIn("def check_command() -> int:", self.py)
        self.assertIn('("check", "--check-only")', self.py)
        self.assertIn("_check_preconditions(logviewer_port, services)", self.py.split("def check_command")[1])
        # check 模式不该自己 pause（由批处理的 :fail 负责）；docstring 会提到该函数，先剥掉
        body = self.py.split("def check_command", 1)[1].split("def main()", 1)[0]
        code = re.sub(r'""".*?"""', "", body, flags=re.S)
        code = "\n".join(
            ln for ln in code.splitlines() if not ln.lstrip().startswith("#")
        )
        self.assertNotIn(
            "_pause_if_console", code, "check 模式不应自行 pause，否则双击时要点两次回车"
        )


class X19RuntimeTests(unittest.TestCase):
    def _harness_output(self) -> str:
        if not HARNESS.is_file():
            self.skipTest("code_audit/_x19_harness.py 不存在（非交付物，允许缺失）")
        proc = run_captured(
            [str(PY), str(HARNESS), "after"],
            cwd=str(REPO),
            timeout=900,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        self.assertEqual(proc.returncode, 0, out)
        return out

    def test_start_failure_is_reported_as_nonzero_exit(self):
        out = self._harness_output()

        # cmd 语言事实：start 丢弃子进程退出码
        self.assertIn("START_RC=0", out, f"未采到 start 的退出码观测：\n{out}")

        # dev.py --check-only：端口被占 → 1；端口空闲 → 0
        self.assertRegex(out, r"dev\.py --check-only（端口被占）[\s\S]*?exit=1")
        self.assertIn("Preconditions FAILED; services were NOT started.", out)
        self.assertRegex(out, r"dev\.py --check-only（端口空闲）[\s\S]*?exit=0")
        self.assertIn("Preconditions OK.", out)

        # start-dev.bat：端口被占 → 1，且绝不能走到 start
        self.assertRegex(out, r"start-dev\.bat（端口被占）[\s\S]*?exit=1")
        self.assertIn("Preconditions not met; services were NOT started.", out)
        self.assertNotIn(
            "Starting Gipfel dev services",
            out.split("start-dev.bat（端口被占）")[-1],
            "前置检查失败后仍然去启动了服务",
        )

        # 收尾：三个端口都必须还是空闲的
        self.assertNotIn("仍空闲: False", out, f"探针留下了真实服务：\n{out}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
