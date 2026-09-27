"""X-21 运行时探针：migrate-server.sh 的退出码语义 / --dry-run 零副作用 / --ssh-port 校验。

安全性：所有场景都不会真的连上任何服务器 ——
  * 连通性探测用 `u@127.0.0.1`（本机回环）+ `BatchMode=yes` + `ConnectTimeout=10`；
  * `--dry-run` 场景在改后**根本不会发起连接**；
  * 改前那些会真实建连的分支连的是 127.0.0.1:22/abc，立刻失败，不会挂住。

用法：backend/.venv/Scripts/python.exe code_audit/harness/_x21_harness.py
"""

from __future__ import annotations

import glob
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]  # code_audit/harness/ -> 仓库根
BASH = r"D:\Git\bin\bash.exe"
MIG = "scripts/migrate-server.sh"


def _bash(argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [BASH, "-c", f"cd '{REPO.as_posix()}' && {argv}"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )


def _clean(text: str) -> str:
    for code in ("\033[0;32m", "\033[1;33m", "\033[0;31m", "\033[0;34m", "\033[0m"):
        text = text.replace(code, "")
    return text.strip()


def _show(title: str, proc: subprocess.CompletedProcess, keep: int = 4) -> None:
    out = ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()
    print(f"  -- {title} --")
    shown = 0
    for ln in out:
        s = _clean(ln)
        if not s:
            continue
        print("     " + s)
        shown += 1
        if shown >= keep:
            break
    print(f"     exit={proc.returncode}")


def _tmp_dirs() -> int:
    return len(glob.glob("/tmp/gipfel-migration-*")) + len(
        glob.glob(str(Path.home() / "AppData/Local/Temp/gipfel-migration-*"))
    )


def main() -> int:
    if not Path(BASH).is_file():
        print("[环境错误] 找不到 bash", file=sys.stderr)
        return 2

    print("### X-21 探针")
    print("[1] 退出码语义")
    _show("--help", _bash(f"bash {MIG} --help"), keep=2)
    _show("--install_dir /opt/gipfel（拼错的参数）", _bash(f"bash {MIG} --install_dir /opt/gipfel"), keep=3)
    _show("缺 --mode", _bash(f"bash {MIG} --target u@127.0.0.1"), keep=3)
    _show("push 缺 --target", _bash(f"bash {MIG} --mode push"), keep=3)

    print("[2] --ssh-port 校验（用本机回环，不会真的连上任何服务器）")
    for port in ("abc", "99999", "0"):
        _show(
            f"--ssh-port {port}",
            _bash(f"bash {MIG} --mode push --target u@127.0.0.1 --install-dir /tmp --ssh-port {port}"),
            keep=3,
        )

    print("[3] --dry-run 必须零网络、零副作用")
    before = _tmp_dirs()
    proc = _bash(f"bash {MIG} --dry-run --mode pull --source u@127.0.0.1 --install-dir /tmp")
    _show("--dry-run --mode pull --source u@127.0.0.1", proc, keep=8)
    after = _tmp_dirs()
    print(f"     [残留] /tmp/gipfel-migration-* 数量：运行前={before} 运行后={after}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
