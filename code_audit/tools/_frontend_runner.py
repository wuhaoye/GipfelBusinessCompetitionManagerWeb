"""按每个前端用例头部注释里记录的"用法"跑一遍（一次性 runner，不属于交付物）。"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FE = REPO / "tests" / "fix_verify" / "frontend"
BUILD = FE / ".build"

BUNDLE_RE = re.compile(
    r"bundle\.ps1\s+-Entry\s+(\S+)\s+-Outfile\s+(\S+)"
)
NODE_RE = re.compile(r"node\s+(tests/fix_verify/frontend/\S+\.mjs)(?:\s+(\S+))?")


def main() -> int:
    BUILD.mkdir(parents=True, exist_ok=True)
    total_assert = 0
    failed: list[str] = []
    files = sorted(FE.glob("test_*.mjs"))
    print(f"前端用例：{len(files)} 个文件")
    for path in files:
        head = "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[:30])
        asserts = len(re.findall(r"\bassert\.", path.read_text(encoding="utf-8", errors="replace")))
        total_assert += asserts

        bundle = BUNDLE_RE.search(head)
        if bundle:
            entry, outfile = bundle.group(1), bundle.group(2)
            proc = subprocess.run(
                ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-File", "tests\\fix_verify\\frontend\\bundle.ps1",
                 "-Entry", entry, "-Outfile", outfile],
                cwd=str(REPO), capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=600,
            )
            if proc.returncode != 0:
                failed.append(path.name)
                print(f"  BUNDLE-FAIL {path.name}: {(proc.stdout or '')[-200:]}{(proc.stderr or '')[-300:]}")
                continue

        node_cmd = NODE_RE.search(head)
        argv = ["node", str(path.relative_to(REPO)).replace("\\", "/")]
        if node_cmd and node_cmd.group(2):
            argv.append(node_cmd.group(2))
        proc = subprocess.run(
            argv, cwd=str(REPO), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=600,
        )
        status = "OK" if proc.returncode == 0 else "FAIL"
        if proc.returncode != 0:
            failed.append(path.name)
        print(f"  {status:4} {path.name:46} asserts={asserts}")
        if proc.returncode != 0:
            print("       " + ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()[-1][:200])

    print(f"合计断言数={total_assert}；失败={len(failed)} {failed}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
