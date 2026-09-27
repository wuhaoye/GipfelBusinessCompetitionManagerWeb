"""X-16 回归：tests/ 顶层不得再出现"没有任何断言、固定退出码 0"的伪测试。

改前实测（本机 Git for Windows bash + backend/.venv python，逐文件真实执行）：

    $ cd <仓库根> && for f in tests/test_*.py; do python "$f" >/dev/null 2>&1; echo "$f exit=$?"; done
    tests/test_callback_mechanism.py        exit=0
    tests/test_fix.py                       exit=0
    tests/test_improved_mm.py               exit=0
    tests/test_kline.py                     exit=0
    tests/test_market_maker.py              exit=0
    tests/test_natural_fluctuation.py       exit=0
    tests/test_parameter_fluctuation.py     exit=0
    tests/test_reduced_intervention.py      exit=0
    tests/test_relative_ranking.py          exit=0
    tests/test_solution2.py                 exit=0
    tests/test_user_volume.py               exit=0
    tests/test_stock_ui.js                  exit=0（node）
    静态计数：12/12 个文件 assert=0、exit=0

即"跑完一片 ✅ 与退出码 0"，却不校验任何东西；其中 4 个还把
`backend/apps/stock/engine.py` 的生产评分算法整段抄成副本。

运行：backend/.venv/Scripts/python.exe tests/fix_verify/scripts/test_x16_no_pseudo_tests.py
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
TESTS = REPO / "tests"

# 含有生产算法历史副本、必须额外声明"副本可能漂移"的文件
ALGO_COPY_FILES = {
    "explore_improved_mm.py",
    "explore_market_maker.py",
    "explore_relative_ranking.py",
    "explore_solution2.py",
}

BANNER_MARK = "【非测试脚本】"
ASSERT_RE = re.compile(r"(?m)^\s*(assert\b|self\.assert)")
EXIT_RE = re.compile(r"sys\.exit|process\.exit|raise SystemExit")


class X16NoPseudoTests(unittest.TestCase):
    def test_no_top_level_test_named_python_or_js(self):
        """pytest/unittest 的默认收集模式是 test_*.py —— 顶层不得再有这类伪测试。"""
        offenders = sorted(
            p.name
            for p in TESTS.iterdir()
            if p.is_file() and p.suffix in {".py", ".js"} and p.name.startswith("test_")
        )
        self.assertEqual(offenders, [], f"tests/ 顶层仍有 test_* 伪测试：{offenders}")

    def test_every_explore_script_declares_itself_non_test(self):
        explores = sorted(TESTS.glob("explore_*"))
        self.assertGreaterEqual(len(explores), 12, "explore_* 观察脚本数量异常")
        for p in explores:
            with self.subTest(file=p.name):
                text = p.read_text(encoding="utf-8", errors="replace")
                self.assertIn(BANNER_MARK, text, f"{p.name} 缺少「非测试脚本」声明")
                self.assertIn("manage.py test apps", text, f"{p.name} 未指向真实回归入口")

    def test_algorithm_copy_scripts_warn_about_drift(self):
        for name in sorted(ALGO_COPY_FILES):
            with self.subTest(file=name):
                text = (TESTS / name).read_text(encoding="utf-8", errors="replace")
                self.assertIn("历史副本", text, f"{name} 未声明算法是生产实现的历史副本")
                self.assertIn("apps/stock/engine.py", text, f"{name} 未指出生产实现位置")

    def test_remaining_top_level_python_has_real_outcome(self):
        """顶层剩下的 .py 必须真的能表达成败（有 assert 或有退出码）。"""
        remaining = [
            p
            for p in sorted(TESTS.glob("*.py"))
            if not p.name.startswith("explore_")
        ]
        self.assertTrue(remaining, "顶层应至少保留 big_number_smoke.py 等真实脚本")
        for p in remaining:
            with self.subTest(file=p.name):
                text = p.read_text(encoding="utf-8", errors="replace")
                self.assertTrue(
                    ASSERT_RE.search(text) or EXIT_RE.search(text),
                    f"{p.name} 既无断言也无退出码，仍是伪测试",
                )

    def test_explore_scripts_are_behaviourally_unchanged(self):
        """重命名+加注释不得改变行为：逐字节复核（剥掉横幅后 == HEAD 原文）。"""
        import subprocess
        import sys

        checker = REPO / "code_audit" / "harness" / "_x16_strip_check.py"
        if not checker.is_file():
            self.skipTest("等价性校验脚本不存在（应随仓库提供：code_audit/harness/_x16_strip_check.py）")
        proc = subprocess.run(
            [sys.executable, str(checker)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(REPO),
        )
        self.assertEqual(proc.returncode, 0, f"等价性校验失败：\n{proc.stdout}\n{proc.stderr}")
        self.assertIn("12/12", proc.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
