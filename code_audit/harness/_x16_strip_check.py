"""X-16 等价性验证：从 tests/explore_* 剥掉新增的横幅，与 HEAD 原文逐字节比较。

用法：backend/.venv/Scripts/python.exe code_audit/harness/_x16_strip_check.py
退出码：0 = 12 个文件全部逐字节等价；1 = 存在差异。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]  # code_audit/harness/ -> 仓库根

PAIRS = [
    ("test_callback_mechanism.py", "explore_callback_mechanism.py"),
    ("test_fix.py", "explore_fix.py"),
    ("test_improved_mm.py", "explore_improved_mm.py"),
    ("test_kline.py", "explore_kline.py"),
    ("test_market_maker.py", "explore_market_maker.py"),
    ("test_natural_fluctuation.py", "explore_natural_fluctuation.py"),
    ("test_parameter_fluctuation.py", "explore_parameter_fluctuation.py"),
    ("test_reduced_intervention.py", "explore_reduced_intervention.py"),
    ("test_relative_ranking.py", "explore_relative_ranking.py"),
    ("test_solution2.py", "explore_solution2.py"),
    ("test_user_volume.py", "explore_user_volume.py"),
    ("test_stock_ui.js", "explore_stock_ui.js"),
]

MARK = "【非测试脚本】"


def strip_banner(text: str) -> str:
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        # 横幅 = 以 ==== 开头的注释行起，到含 MARK 的块结束
        if i + 1 < len(lines) and lines[i].lstrip("#/ ").startswith("====") and MARK in lines[i + 1]:
            i += 1
            while i < len(lines) and MARK not in lines[i]:
                i += 1
            # 跳过 MARK 行之后的横幅正文，直到收尾的 ==== 行
            while i < len(lines):
                s = lines[i].strip()
                i += 1
                if s.startswith("# ====") or s.startswith("// ===="):
                    break
            # 跳过紧跟其后的"历史副本"提示（若有）
            while i < len(lines) and ("历史副本" in lines[i] or "生产实现变更后副本不会同步" in lines[i]):
                i += 1
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out)


def _baseline_rev() -> str:
    """找到最后一个仍然含有旧文件名 `tests/test_callback_mechanism.py` 的提交。

    改名之后 HEAD 里已经没有 `tests/test_*.py` 了；而 X-16 之后还发生了多次提交，
    写死 hash 会失效，rename 检测也依赖 diff.renames 配置。这里直接沿 HEAD 往回找，
    第一个仍含旧路径的提交就是"改名提交的父提交"。
    """
    revs = subprocess.run(
        ["git", "rev-list", "HEAD"], cwd=str(REPO), capture_output=True, check=True
    ).stdout.decode("utf-8").split()
    for rev in revs:
        probe = subprocess.run(
            ["git", "cat-file", "-e", f"{rev}:tests/test_callback_mechanism.py"],
            cwd=str(REPO), capture_output=True,
        )
        if probe.returncode == 0:
            return rev
    return "HEAD"


def main() -> int:
    bad = 0
    base = _baseline_rev()
    print(f"基线提交：{base}（改名提交的父提交）")
    for old, new in PAIRS:
        original = subprocess.run(
            ["git", "show", f"{base}:tests/{old}"],
            cwd=str(REPO),
            capture_output=True,
            check=True,
        ).stdout.decode("utf-8")
        current = (REPO / "tests" / new).read_text(encoding="utf-8", newline="")
        stripped = strip_banner(current.replace("\r\n", "\n"))
        if stripped == original.replace("\r\n", "\n"):
            print(f"  OK    {old:34} == tests/{new}（剥掉横幅后逐字节相同）")
        else:
            bad += 1
            print(f"  DIFF  {old:34} != tests/{new}")
            import difflib

            for ln in list(
                difflib.unified_diff(
                    original.splitlines(), stripped.splitlines(), lineterm="", n=1
                )
            )[:20]:
                print(f"        {ln}")
    print(f"结论：{len(PAIRS) - bad}/{len(PAIRS)} 个文件逐字节等价，{bad} 个存在差异")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
