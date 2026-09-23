@echo off
REM ========================================================================
REM  contract_watcher - Start the Tkinter GUI
REM  Login, pick the companies to book, request manual booking, and browse
REM  a company's contracts straight from the local SQLite ledger.
REM
REM  Usage:
REM      start_gui.bat                   launch with a console - shows errors
REM      start_gui.bat -w                launch without a console - pythonw
REM      start_gui.bat --config my.json  extra args are forwarded to gui.py
REM      start_gui.bat --selftest        build the window once and exit
REM
REM  What it does:
REM      1. cd into this folder - config.json / data / books live here;
REM      2. pick a Python that has tkinter, preferring one that also has
REM         xlwings - only the Excel write needs xlwings, so without it the
REM         contracts still land in SQLite but threshold / fiscal-year /
REM         manual booking cannot write the xlsx books;
REM      3. run gui.py and pause on failure so the error stays readable.
REM
REM  Pure ASCII + CRLF on purpose: cmd.exe reads .bat bytes with the OEM code
REM  page, so non-ASCII comments/messages would be mangled - the same rule the
REM  other scripts\*.bat files in this repository follow.
REM  NOTE: inside an "if (...)" block a literal "(" or ")" breaks the parser,
REM  so echoed text avoids parentheses entirely.
REM ========================================================================
setlocal
chcp 65001 >nul
cd /d "%~dp0"

if not exist "%~dp0gui.py" (
  echo [ERROR] %~dp0gui.py not found. Keep start_gui.bat next to gui.py.
  goto :fail
)

set "PY="
set "PYW="
set "NO_XLWINGS="

REM ---- 1) "py -3" with tkinter AND xlwings -------------------------------
where py >nul 2>nul
if not errorlevel 1 (
  py -3 -c "import tkinter, xlwings" >nul 2>nul
  if not errorlevel 1 ( set "PY=py -3" & set "PYW=pyw -3" )
)

REM ---- 2) "python" on PATH with tkinter AND xlwings ---------------------
if not defined PY (
  where python >nul 2>nul
  if not errorlevel 1 (
    python -c "import tkinter, xlwings" >nul 2>nul
    if not errorlevel 1 ( set "PY=python" & set "PYW=pythonw" )
  )
)

REM ---- 3) fallback: tkinter only - GUI works, Excel write will fail ------
if not defined PY (
  where py >nul 2>nul
  if not errorlevel 1 (
    py -3 -c "import tkinter" >nul 2>nul
    if not errorlevel 1 ( set "PY=py -3" & set "PYW=pyw -3" & set "NO_XLWINGS=1" )
  )
)
if not defined PY (
  where python >nul 2>nul
  if not errorlevel 1 (
    python -c "import tkinter" >nul 2>nul
    if not errorlevel 1 ( set "PY=python" & set "PYW=pythonw" & set "NO_XLWINGS=1" )
  )
)

if not defined PY (
  echo [ERROR] No Python with tkinter found. Checked: "py -3" and "python".
  echo         Install Python 3.10+ with the Tcl/Tk option enabled, or call
  echo         gui.py with the full path of such an interpreter, e.g.
  echo             C:\Python313\python.exe gui.py
  goto :fail
)

if defined NO_XLWINGS (
  echo [WARN] No xlwings for: %PY%
  echo        Contracts will still be recorded into the local SQLite ledger,
  echo        but writing the Excel books - threshold / fiscal-year / manual
  echo        booking - will fail until xlwings is installed:
  echo            %PY% -m pip install xlwings
  echo.
)

REM ---- "-w": windowed mode - strip the flag before forwarding ------------
set "ARGS=%*"
set "WINDOWED="
if /i "%~1"=="-w" (
  set "WINDOWED=1"
  set "ARGS="
  for /f "tokens=1,*" %%A in ("%*") do set "ARGS=%%B"
)

if defined WINDOWED (
  echo [INFO]  Starting contract_watcher GUI windowed, without a console ...
  start "" %PYW% "%~dp0gui.py" %ARGS%
  exit /b 0
)

echo [INFO]  Starting contract_watcher GUI with: %PY%
echo        The GUI window stays open after this script finishes; closing
echo        that window returns control to this console.
echo.
%PY% "%~dp0gui.py" %ARGS%
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
  echo.
  echo [ERROR] gui.py exited with code %RC% - see the message above.
  goto :fail_rc
)
exit /b 0

:fail
echo.
pause
exit /b 1

:fail_rc
echo.
pause
exit /b %RC%
