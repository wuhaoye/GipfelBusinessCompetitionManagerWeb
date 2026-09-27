"""X-18 运行时探针：在真实 cmd.exe 里对比改前/改后的保窗守卫。

全部发生在仓库内 .tmp/x18/ 这个**空壳目录**里（没有 ..\\backend、没有 ..\\frontend），
并把 PATH 收窄到 System32，于是 bootstrap-dev.bat 在环境检查那一步就以非 0 结束 ——
不会真的建 venv、装依赖。

两个独立 cmd 会话：
  A. 连续两次 `call bootstrap-dev.bat --no-keep-open`（自动化用法）
  B. 连续两次 `call bootstrap-dev.bat`（双击/交互用法，走保窗守卫）
每次调用后打印 `RC=%ERRORLEVEL% MARKER=[%GIPFEL_NOEXIT%]` 与存活哨兵，用来观察
① 退出码是否真实传递；② 标记是否污染父 cmd 环境；③ 守卫第二次是否被跳过；④ 父 shell 是否被杀。

用法：
    backend/.venv/Scripts/python.exe code_audit/harness/_x18_harness.py before|after
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]  # code_audit/harness/ -> 仓库根
WORK = REPO / ".tmp" / "x18"
CMD_EXE = r"C:\Windows\System32\cmd.exe"

HEADER = """@echo off
set "PATH=C:\\Windows\\System32;C:\\Windows"
cd /d "%~dp0"
"""

SESSION_A = HEADER + """echo === A1: call bootstrap-dev.bat --no-keep-open ===
call "scripts\\bootstrap-dev.bat" --no-keep-open
echo RC_A1=%ERRORLEVEL% MARKER=[%GIPFEL_NOEXIT%]
echo ALIVE_AFTER_A1
echo === A2: same cmd session, second call (--no-keep-open) ===
call "scripts\\bootstrap-dev.bat" --no-keep-open
echo RC_A2=%ERRORLEVEL% MARKER=[%GIPFEL_NOEXIT%]
echo ALIVE_AFTER_A2
"""

SESSION_B = HEADER + """echo === B1: call bootstrap-dev.bat (no args, keep-open guard) ===
call "scripts\\bootstrap-dev.bat"
echo RC_B1=%ERRORLEVEL% MARKER=[%GIPFEL_NOEXIT%]
echo ALIVE_AFTER_B1
echo === B2: same cmd session, second call (no args) ===
call "scripts\\bootstrap-dev.bat"
echo RC_B2=%ERRORLEVEL% MARKER=[%GIPFEL_NOEXIT%]
echo ALIVE_AFTER_B2
"""


def _run(session: str, name: str) -> None:
    (WORK / f"{name}.cmd").write_bytes(session.replace("\n", "\r\n").encode("ascii"))
    proc = subprocess.run(
        [CMD_EXE, "/c", f"{name}.cmd"],
        cwd=str(WORK),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    verbose = "--verbose" in sys.argv
    for ln in out.splitlines():
        s = ln.strip()
        if verbose or s.startswith(("===", "RC_", "ALIVE", "[ERROR]", "[OK]")):
            print("  " + s)
    print(f"  <cmd 进程退出码={proc.returncode}>")


def main() -> int:
    which = (sys.argv[1] if len(sys.argv) > 1 else "after").lower()
    if not Path(CMD_EXE).is_file():
        print("[环境错误] 找不到 cmd.exe", file=sys.stderr)
        return 2

    if WORK.exists():
        shutil.rmtree(WORK, ignore_errors=True)
    (WORK / "scripts").mkdir(parents=True, exist_ok=True)

    if which == "before":
        blob = subprocess.run(
            ["git", "show", "HEAD:scripts/bootstrap-dev.bat"],
            cwd=str(REPO), capture_output=True, check=True,
        ).stdout
        (WORK / "scripts" / "bootstrap-dev.bat").write_bytes(blob)
        label = "改前（HEAD 版本）"
    else:
        shutil.copy2(REPO / "scripts" / "bootstrap-dev.bat", WORK / "scripts" / "bootstrap-dev.bat")
        label = "改后（工作区版本）"

    print(f"### {label}")
    print("-- 会话 A：连续两次 --no-keep-open --")
    _run(SESSION_A, "session_a")
    print("-- 会话 B：连续两次无参调用（保窗守卫） --")
    _run(SESSION_B, "session_b")
    leftovers = sorted(p.name for p in WORK.iterdir())
    print(f"  工作目录内容：{leftovers}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
