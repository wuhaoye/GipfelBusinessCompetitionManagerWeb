"""X-20 运行时探针：quick-sync.sh 相对 INSTALL_DIR 的解析（真实 bash）。

安全性：四段场景都不会真的 rsync ——
  ① 相对且不存在的目录：改后会在前置校验处直接中止；
  ② 相对且存在的目录，但里面没有任何 SYNC_ITEMS（只建一个空的 backend/），
     循环里每一项都是"跳过（不存在）"，不会触发 rsync；
  ③ absolutize_dir 只做纯字符串解析；
  ④ migrate-server.sh 的 `--install-dir` 校验在连接远端之前就返回。

用法：backend/.venv/Scripts/python.exe code_audit/harness/_x20_harness.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]  # code_audit/harness/ -> 仓库根
BASH = r"D:\Git\bin\bash.exe"
WORK = REPO / ".tmp" / "x20"
# 从 .tmp/x20 出发指向仓库 scripts/ 的相对路径（两级向上）
SH = "../../scripts/quick-sync.sh"
LIB = "../../scripts/lib/deploy-common.sh"
MIG = "../../scripts/migrate-server.sh"


def _bash(script: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [BASH, "-c", script],
        cwd=str(cwd),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )


def _clean(text: str) -> str:
    for code in ("\033[0;32m", "\033[1;33m", "\033[0;31m", "\033[0m"):
        text = text.replace(code, "")
    return text.strip()


def _show(title: str, proc: subprocess.CompletedProcess, keep: int = 10) -> None:
    out = ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()
    print(f"  -- {title} --")
    for ln in out[:keep]:
        print("     " + _clean(ln))
    if len(out) > keep:
        print(f"     ...（共 {len(out)} 行）")
    print(f"     exit={proc.returncode}")


def main() -> int:
    if not Path(BASH).is_file():
        print("[环境错误] 找不到 bash", file=sys.stderr)
        return 2
    if WORK.exists():
        shutil.rmtree(WORK, ignore_errors=True)
    (WORK / "empty-install" / "backend").mkdir(parents=True, exist_ok=True)

    print("### X-20 探针（工作目录 .tmp/x20）")
    print("[1] push：相对且**不存在**的 INSTALL_DIR")
    _show(
        f"bash {SH} push user@nonexistent ./no-such-dir",
        _bash(f"bash {SH} push user@nonexistent ./no-such-dir", WORK),
    )

    print("[2] push：相对且存在、但没有任何同步项（循环内不会 rsync）")
    _show(
        f"bash {SH} push user@nonexistent empty-install",
        _bash(f"bash {SH} push user@nonexistent empty-install", WORK),
        keep=14,
    )

    print("[3] absolutize_dir 语义（生产公共库实现）")
    probe = (
        f"source {LIB}; "
        'for p in "." "./a/b/../c" "a/" "/opt/gipfel//x/.." "/tmp/../opt"; do '
        'printf "     %-22s -> %s\\n" "$p" "$(absolutize_dir "$p")"; done'
    )
    _show(f"source {LIB}; absolutize_dir <path>", _bash(probe, WORK), keep=8)

    print("[4] migrate-server.sh：--install-dir 非法路径必须被拒绝（X-03 已覆盖）")
    for arg in ("rel/gipfel", "/opt/gipfel/../etc"):
        _show(
            f"bash {MIG} --mode push --target u@h --install-dir {arg}",
            _bash(f"bash {MIG} --mode push --target u@h --install-dir {arg}", WORK),
            keep=5,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
