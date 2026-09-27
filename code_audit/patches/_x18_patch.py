"""X-18：改写 scripts/bootstrap-dev.bat 的保窗守卫（一次性补丁，不属于交付物）。

要求：纯 ASCII + CRLF（文件顶部维护规则第 4 条），因此用 Python 精确写回，
不走 PowerShell 字符串手术（历史上曾把 CRLF 文件改坏）。
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TARGET = REPO / "scripts" / "bootstrap-dev.bat"

OLD = '''REM --- keep the window open even if the script dies on a syntax error -----
if not "%GIPFEL_NOEXIT%"=="1" (
  set "GIPFEL_NOEXIT=1"
  cmd /k call "%~f0" %*
  exit /b
)

setlocal
chcp 65001 >nul
cd /d "%~dp0"
'''

NEW = '''REM --- keep the window open even if the script dies on a syntax error -----
REM Audit X-18: the old guard used a GLOBAL ENV VAR as its "already re-entered"
REM marker and ran before setlocal, so `set "GIPFEL_NOEXIT=1"` was written into
REM the PARENT cmd.exe environment. Running this script a second time in the
REM same window therefore found GIPFEL_NOEXIT already 1, skipped the guard and
REM ran as a plain batch file - and if it then died before reaching pause, the
REM window flashed away (exactly the failure rule 1 above exists to prevent).
REM `cmd /k` also never returns, so any caller that chains scripts hangs.
REM The marker is now a dedicated ARGUMENT, which only affects this one process
REM tree and never leaks. Automation should pass --no-keep-open: that path runs
REM inline and returns the real exit code instead of the always-0 of cmd /k.
if /i "%~1"=="--no-keep-open" goto :guard_done
if /i "%~1"=="__kept__" goto :guard_done
cmd /k call "%~f0" __kept__ %*
exit /b

:guard_done
if /i "%~1"=="--no-keep-open" shift
if /i "%~1"=="__kept__" shift

setlocal
chcp 65001 >nul
cd /d "%~dp0"
'''

OLD_EXIT = '''echo [TIP]  Press any key to close this window...
pause
exit 0
'''
NEW_EXIT = '''echo [TIP]  Press any key to close this window...
pause
exit /b 0
'''

OLD_FAIL = '''echo [TIP]  Press any key to close this window...
pause
exit 1
'''
NEW_FAIL = '''echo [TIP]  Press any key to close this window...
pause
exit /b 1
'''


def main() -> int:
    raw = TARGET.read_bytes()
    text = raw.decode("ascii")  # 必须是纯 ASCII，否则直接报错
    changed = 0
    for old, new in ((OLD, NEW), (OLD_EXIT, NEW_EXIT), (OLD_FAIL, NEW_FAIL)):
        o = old.replace("\n", "\r\n")
        n = new.replace("\n", "\r\n")
        if o not in text:
            print(f"[skip] 未找到待替换片段：{old.splitlines()[0][:50]}")
            continue
        text = text.replace(o, n, 1)
        changed += 1
    TARGET.write_bytes(text.encode("ascii"))
    print(f"替换 {changed} 处；文件仍为纯 ASCII，行尾 CRLF：{TARGET.read_bytes().count(b'\\r\\n')} 行")
    return 0 if changed == 3 else 1


if __name__ == "__main__":
    raise SystemExit(main())
