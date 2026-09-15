"""X-18 回归：bootstrap-dev.bat 的"保窗"守卫不得污染父环境、必须能重复运行。

改前实测（真实 cmd.exe，`.tmp/x18/` 空壳目录 + 收窄 PATH，脚本在环境检查处即失败）：

    会话 A：连续两次 `call scripts\\bootstrap-dev.bat --no-keep-open`
      RC_A1=1 MARKER=[1]          ← GIPFEL_NOEXIT 被写进父 cmd 环境
      ALIVE_AFTER_A1
      === A2 ===
      <cmd 进程退出码=1>          ← 第二次调用后父 shell 直接消失（没有 RC_A2 / ALIVE_AFTER_A2）
    会话 B：连续两次无参调用
      RC_B1=1 MARKER=[1]
      ALIVE_AFTER_B1
      === B2 ===
      <cmd 进程退出码=1>          ← 同上

    原因：`set "GIPFEL_NOEXIT=1"` 写在 `setlocal` **之前**，于是标记留在父 cmd.exe 里；
    第二次运行时守卫被判定为"已重入"而跳过，脚本按普通批处理在当前窗口运行，结尾的裸
    `exit 1` 直接把调用方的 cmd 进程杀掉 —— 正是文件顶部维护规则第 1 条要避免的"闪退"。

后续补充（同一份守卫引入的新缺陷，已一并回归）：保窗守卫里的裸 `shift` 会**连 `%0` 一起移掉**，
于是守卫之后的每一个 `%~dp0` 都从"脚本所在目录"变成"第一个参数"。走 `--no-keep-open`
（文档推荐给 CI/自动化的开关）时 `%SCRIPT_DIR%` 缺失，`BACKEND` 解析到仓库的**上一级**，
实测报 `[ERROR] missing ...\GipfelBusinessCompetitionManagerWeb\..\backend\requirements.txt` 并退出 1。
修法：在任何 shift 之前 `set "SCRIPT_DIR=%~dp0"`，后续一律用 `%SCRIPT_DIR%`。
见 X18StaticTests.test_script_dir_is_captured_before_any_shift / test_paths_use_the_captured_script_dir。

运行：backend/.venv/Scripts/python.exe tests/fix_verify/scripts/test_x18_bootstrap_guard.py
"""

from __future__ import annotations

import re
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
BAT = REPO / "scripts" / "bootstrap-dev.bat"
HARNESS = REPO / "code_audit" / "_x18_harness.py"
PY = REPO / "backend" / ".venv" / "Scripts" / "python.exe"


def _code_only(text: str) -> str:
    """去掉 REM 注释行，只留真正会执行的代码。"""
    return "\n".join(
        ln for ln in text.splitlines() if not ln.lstrip().upper().startswith("REM")
    )


class X18StaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.raw = BAT.read_bytes()
        cls.text = cls.raw.decode("ascii")  # 非纯 ASCII 会在此抛错
        cls.code = _code_only(cls.text)

    def test_file_stays_pure_ascii_with_crlf(self):
        self.assertTrue(all(b < 128 for b in self.raw), "bootstrap-dev.bat 不再是纯 ASCII")
        crlf = self.raw.count(b"\r\n")
        self.assertEqual(crlf, self.raw.count(b"\n"), "存在非 CRLF 行尾")

    def test_marker_is_an_argument_not_an_env_var(self):
        self.assertNotIn(
            'set "GIPFEL_NOEXIT=', self.code,
            "仍在用全局环境变量做重入标记（会污染父 cmd 环境）",
        )
        self.assertIn('if /i "%~1"=="__kept__" goto :guard_done', self.code)
        self.assertIn('cmd /k call "%~f0" __kept__ %*', self.code)

    def test_no_keep_open_path_runs_inline(self):
        self.assertIn('if /i "%~1"=="--no-keep-open" goto :guard_done', self.code)

    def test_script_dir_is_captured_before_any_shift(self):
        """cmd 的 shift 会连 %0 一起移掉，之后 %~dp0 就变成"第一个参数"。

        改前实测（本机 cmd.exe，`bootstrap-dev.bat --no-keep-open --skip-frontend`）：

            AFTER1: 0=[--no-keep-open]  1=[--skip-frontend]
                    dp0=[C:\\...\\GipfelBusinessCompetitionManagerWeb\\]   ← 少了 scripts\\ 这一级

        于是 BACKEND 解析成 `...\\GipfelBusinessCompetitionManagerWeb\\..\\backend`（上一级），
        脚本在 `missing ...\\..\\backend\\requirements.txt` 处报错退出 1 —— 而 `--no-keep-open`
        正是文档推荐给 CI/自动化用的开关。修法：在任何 shift 之前把目录固化到 SCRIPT_DIR。
        """
        # 固化语句必须存在，且位于保窗守卫（第一次 shift）之前
        self.assertIn('set "SCRIPT_DIR=%~dp0"', self.code)
        self.assertLess(
            self.code.index('set "SCRIPT_DIR=%~dp0"'),
            self.code.index('cmd /k call "%~f0" __kept__ %*'),
            "SCRIPT_DIR 必须在守卫块（含 shift）之前固化",
        )
        # shift 之后不得再出现 %~dp0
        first_shift = self.code.index("shift")
        tail = self.code[first_shift:]
        self.assertNotIn(
            "%~dp0", tail,
            "shift 之后仍在使用 %~dp0 —— 它会指向第一个参数而不是脚本目录",
        )

    def test_paths_use_the_captured_script_dir(self):
        """BACKEND/FRONTEND/被调用的 .py 都必须走 SCRIPT_DIR，不能依赖 cwd 或 %~dp0。"""
        self.assertIn('set "BACKEND=%SCRIPT_DIR%..\\backend"', self.code)
        self.assertIn('set "FRONTEND=%SCRIPT_DIR%..\\frontend"', self.code)
        self.assertIn('"%PY%" "%SCRIPT_DIR%gen_logviewer_key.py"', self.code)

    def test_batch_returns_instead_of_killing_the_caller(self):
        self.assertNotRegex(self.code, r"(?m)^exit [01]\s*$", "结尾仍是裸 exit，会杀掉调用方 cmd")
        self.assertIn("exit /b 0", self.code)
        self.assertIn("exit /b 1", self.code)

    def test_guard_block_has_no_ascii_parentheses(self):
        """维护规则 1：if 块内的文本带 ASCII 括号会让 cmd 直接中止整个脚本。"""
        m = re.search(
            r"if /i \"%~1\"==\"--no-keep-open\" goto :guard_done(.*?):guard_done", self.code, re.S
        )
        self.assertIsNotNone(m)
        guard = m.group(1)
        self.assertNotIn("(", guard.replace('"%~1"', ""), "守卫块里出现 ASCII 左括号")
        self.assertNotIn(")", guard.replace('"%~1"', ""), "守卫块里出现 ASCII 右括号")


class X18RuntimeTests(unittest.TestCase):
    def test_guard_survives_two_calls_in_one_cmd_session(self):
        if not HARNESS.is_file():
            self.skipTest("code_audit/_x18_harness.py 不存在（非交付物，允许缺失）")
        proc = subprocess.run(
            [str(PY), str(HARNESS), "after"],
            cwd=str(REPO),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode == 2:
            self.skipTest(f"本机无法运行 cmd.exe：{out}")
        self.assertEqual(proc.returncode, 0, out)

        # 标记绝不外泄
        markers = re.findall(r"MARKER=\[([^\]]*)\]", out)
        self.assertTrue(markers, f"没有采到 MARKER 观测值：\n{out}")
        self.assertEqual(set(markers), {""}, f"标记污染了父 cmd 环境：{markers}")

        # 会话 A：两次都拿到真实退出码 1，且调用方存活
        self.assertIn("RC_A1=1", out)
        self.assertIn("ALIVE_AFTER_A1", out)
        self.assertIn("RC_A2=1", out)
        self.assertIn("ALIVE_AFTER_A2", out)

        # 会话 B：连保窗路径也不再让窗口/调用方消失
        self.assertIn("ALIVE_AFTER_B1", out)
        self.assertIn("ALIVE_AFTER_B2", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
