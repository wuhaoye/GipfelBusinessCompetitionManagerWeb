"""快照注册表自检：打印每张表的作用域归属、回写策略与依赖顺序。

用法：cd backend && .venv\\Scripts\\python.exe scripts\\inspect_snapshot_registry.py
（脚本位于仓库 tests/ 下的临时工具目录，仅供开发排查。）
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BASE))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "backend.settings")

import django  # noqa: E402

django.setup()

from apps.snapshots.registry import build_registry  # noqa: E402


def main() -> None:
    registry = build_registry()
    print(f"共 {len(registry.specs)} 张表（按依赖拓扑序）\n")
    header = f"{'#':>3} {'模型':<34} {'表名':<26} {'作用域':<12} {'回写':<7} 依赖FK"
    print(header)
    print("-" * len(header))
    for spec in registry.specs:
        print(
            f"{spec.order:>3} {spec.label:<34} {spec.table:<26} {spec.scope:<12} "
            f"{spec.restore:<7} {','.join(spec.scope_fks) or '-'}"
        )
    scoped = [s for s in registry.specs if s.in_competition_scope]
    print(f"\n比赛作用域表 {len(scoped)} 张；全局表 {len(registry.specs) - len(scoped)} 张")
    print("全局表：", ", ".join(s.label for s in registry.specs if not s.in_competition_scope))


if __name__ == "__main__":
    main()
