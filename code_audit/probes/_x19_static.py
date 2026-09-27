"""X-19 改前的静态证据：旧 start-dev.bat 的唯一出口 / 旧 dev.py 不认识 --check-only。"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def main() -> int:
    old_bat = subprocess.run(
        ["git", "show", "HEAD:scripts/start-dev.bat"], cwd=str(REPO), capture_output=True, check=True
    ).stdout.decode("utf-8")
    old_py = subprocess.run(
        ["git", "show", "HEAD:scripts/dev.py"], cwd=str(REPO), capture_output=True, check=True
    ).stdout.decode("utf-8")

    lines = old_bat.splitlines()
    marker = 'start "Gipfel Dev"'
    for i, line in enumerate(lines):
        if marker in line:
            print(f"[改前] start-dev.bat 第 {i + 1} 行起：")
            for extra in lines[i : i + 3]:
                print("       " + extra)
    print(f"[改前] start-dev.bat 出现 errorlevel 检查：{'errorlevel' in old_bat}")
    print(f"[改前] start-dev.bat 出现 --check-only：{'--check-only' in old_bat}")
    print(f"[改前] dev.py 处理 --check-only：{'check-only' in old_py}")
    print(
        "[改前] dev.py main() 的入口分派："
        + next(
            (ln.strip() for ln in old_py.splitlines() if "stop" in ln and "sys.argv" in ln),
            "(未找到)",
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
