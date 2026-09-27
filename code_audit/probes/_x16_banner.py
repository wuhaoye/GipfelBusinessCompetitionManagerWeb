"""X-16：给 tests/ 下 12 个"无断言伪测试"加非测试声明横幅（一次性脚本，不属于交付物）。

用法：backend/.venv/Scripts/python.exe code_audit/_x16_banner.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TESTS = REPO / "tests"

RENAMES = {
    "test_callback_mechanism.py": "explore_callback_mechanism.py",
    "test_fix.py": "explore_fix.py",
    "test_improved_mm.py": "explore_improved_mm.py",
    "test_kline.py": "explore_kline.py",
    "test_market_maker.py": "explore_market_maker.py",
    "test_natural_fluctuation.py": "explore_natural_fluctuation.py",
    "test_parameter_fluctuation.py": "explore_parameter_fluctuation.py",
    "test_reduced_intervention.py": "explore_reduced_intervention.py",
    "test_relative_ranking.py": "explore_relative_ranking.py",
    "test_solution2.py": "explore_solution2.py",
    "test_user_volume.py": "explore_user_volume.py",
    "test_stock_ui.js": "explore_stock_ui.js",
}

# 内含 backend/apps/stock/engine.py 生产算法历史副本的文件
ALGO_COPY = {
    "explore_improved_mm.py",
    "explore_market_maker.py",
    "explore_relative_ranking.py",
    "explore_solution2.py",
}

PY_BANNER = [
    "# ===========================================================================",
    "# 【非测试脚本】审计 X-16：本文件通篇只有 print，0 个断言、固定以退出码 0 结束，",
    "# 不构成任何测试覆盖。已由 tests/test_*.py 改名为 tests/explore_*.py —— 既避免被",
    "# pytest 当作用例收集，也避免与 tests/fix_verify/ 下的真实回归用例混淆。",
    "# 真实回归入口：backend/.venv/Scripts/python.exe manage.py test apps tests_fix_verify",
    "# ===========================================================================",
]

PY_ALGO_NOTE = [
    "# 注意：本文件里的评分/做市算法是 backend/apps/stock/engine.py 生产实现的**历史副本**，",
    "#       生产实现变更后副本不会同步；任何结论都必须回到生产实现上复核。",
]

JS_BANNER = [ln.replace("# ", "// ").replace("#", "//") for ln in PY_BANNER]
JS_ALGO_NOTE = [ln.replace("# ", "// ").replace("#", "//") for ln in PY_ALGO_NOTE]


def main() -> int:
    for old, new in RENAMES.items():
        src = TESTS / old
        dst = TESTS / new
        if not dst.exists():
            print(f"[skip] {new} 不存在（请先 git mv {old} {new}）", file=sys.stderr)
            return 2
        raw = dst.read_text(encoding="utf-8", newline="")
        nl = "\r\n" if "\r\n" in raw else "\n"
        is_js = dst.suffix == ".js"
        banner = list(JS_BANNER if is_js else PY_BANNER)
        if new in ALGO_COPY:
            banner += list(JS_ALGO_NOTE if is_js else PY_ALGO_NOTE)

        marker = banner[0]
        if marker in raw:
            print(f"[skip] {new} 已含横幅")
            continue

        lines = raw.split(nl)
        insert_at = 0
        if lines and lines[0].startswith("#!"):
            insert_at = 1
        out = lines[:insert_at] + banner + lines[insert_at:]
        dst.write_text(nl.join(out), encoding="utf-8", newline="")
        print(f"[ok]   {new} 插入 {len(banner)} 行横幅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
