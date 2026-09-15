@echo off
REM ========================================================================
REM  Gipfel - DEVELOPMENT - Bootstrap (self-contained batch, no PowerShell)
REM  Double-click to set up the dev environment once.
REM  Pure ASCII file (English comments only).
REM
REM  MAINTENANCE RULES - learned the hard way, read before editing:
REM    1. NEVER put round brackets in text on a line that belongs to an
REM       if / for block. cmd reads them as command grouping, aborts the
REM       whole script with "<token> was unexpected at this time." and
REM       silently skips every pause below: the window just flashes away.
REM    2. Always initialise a variable before using it as "if %VAR% LSS 3".
REM       An empty VAR rewrites the line to "if  LSS 3 (" = same hard abort.
REM    3. Variables set inside a block are expanded when the block is parsed,
REM       so echo them AFTER the block, not inside it.
REM    4. Keep this file pure ASCII with CRLF line endings.
REM    5. NEVER rely on %~dp0 AFTER a plain "shift". cmd's shift moves %0
REM       as well, so %~dp0 stops meaning "this script's folder" and starts
REM       meaning "the first argument". The folder is captured into SCRIPT_DIR
REM       BEFORE any shift below - always use %SCRIPT_DIR%, never %~dp0.
REM
REM  What it does:
REM    1. Check Python >=3.10 and Node/npm
REM    2. Copy backend\.env.example -> backend\.env if missing
REM    3. Create backend\.venv + pip install -r requirements.txt
REM    4. python manage.py check + migrate   first migrate seeds admin/admin23
REM    5. npm install in frontend
REM
REM  Optional: pass --skip-frontend to skip Node/npm steps.
REM  Optional: pass --no-keep-open to run inline and return the real exit
REM            code (use this from scripts/CI; it never spawns cmd /k).
REM ========================================================================

REM --- capture our own folder BEFORE any shift below -----------------------
REM Audit: cmd's `shift` also discards %0. Every %~dp0 use after the shifts
REM used to resolve to the FIRST ARGUMENT instead of the scripts folder, so
REM `--no-keep-open` (the CI/automation flag) made BACKEND point one level too
REM high ("...\GipfelBusinessCompetitionManagerWeb\..\backend") and the run
REM aborted with "missing ...\..\backend\requirements.txt". %SCRIPT_DIR% is
REM frozen here, while %0 is still this script, and used everywhere below.
set "SCRIPT_DIR=%~dp0"

REM --- keep the window open even if the script dies on a syntax error -----
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
cd /d "%SCRIPT_DIR%"

set "BACKEND=%SCRIPT_DIR%..\backend"
set "FRONTEND=%SCRIPT_DIR%..\frontend"
set "SKIPFE=0"
if /i "%~1"=="--skip-frontend" set "SKIPFE=1"

REM ---------- 1. Environment checks ----------
echo [INFO]  Checking environment...
set "PYCMD="
set "PYVER="
set "PYPROBE=python"
call :probe_python
if not defined PYCMD (
  set "PYPROBE=py -3"
  call :probe_python
)
if not defined PYCMD if exist "%BACKEND%\.venv\Scripts\python.exe" (
  set "PYPROBE="%BACKEND%\.venv\Scripts\python.exe""
  call :probe_python
)
if not defined PYCMD (
  echo [ERROR] Python 3.10+ not found.
  echo         Install Python 3.10+ and enable "Add python.exe to PATH" in the installer,
  echo         or install the Microsoft Store py launcher.
  goto :fail
)
echo %PYVER%| findstr /r "^[0-9][0-9]*\.[0-9][0-9]*$" >nul
if errorlevel 1 (
  echo [ERROR] Cannot parse the Python version reported by: %PYCMD%
  echo         Raw output was: %PYVER%
  goto :fail
)
set "PYMAJOR=0"
set "PYMINOR=0"
for /f "tokens=1,2 delims=." %%a in ("%PYVER%") do set "PYMAJOR=%%a" & set "PYMINOR=%%b"
echo [INFO]   python: %PYVER%   using: %PYCMD%
if %PYMAJOR% LSS 3 goto :py_old
if %PYMAJOR%==3 if %PYMINOR% LSS 10 goto :py_old
goto :py_ok
:py_old
echo [ERROR] Python too old: %PYVER% -- need 3.10 or newer
goto :fail
:py_ok

set "NODEV=unknown"
set "NPMV=unknown"
if %SKIPFE%==0 (
  where node >nul 2>nul
  if errorlevel 1 (
    echo [ERROR] node not found. Install Node.js 18+ / 20 LTS recommended.
    goto :fail
  )
  where npm >nul 2>nul
  if errorlevel 1 (
    echo [ERROR] npm not found -- it ships with Node.js.
    goto :fail
  )
  for /f "tokens=*" %%v in ('node -v') do set "NODEV=%%v"
  for /f "tokens=*" %%v in ('npm -v') do set "NPMV=%%v"
)
if %SKIPFE%==0 echo [INFO]   node: %NODEV%  npm: %NPMV%

REM ---------- required files ----------
if not exist "%BACKEND%\requirements.txt" (
  echo [ERROR] missing %BACKEND%\requirements.txt
  goto :fail
)
if not exist "%BACKEND%\.env.example" (
  echo [ERROR] missing %BACKEND%\.env.example
  goto :fail
)
if %SKIPFE%==0 if not exist "%FRONTEND%\package.json" (
  echo [ERROR] missing %FRONTEND%\package.json
  goto :fail
)

REM ---------- 2. .env ----------
if not exist "%BACKEND%\.env" (
  echo [INFO]  First run: copy .env.example to .env
  copy /Y "%BACKEND%\.env.example" "%BACKEND%\.env" >nul
  echo [WARN]  Production: change JWT_SECRET and CORS_ORIGIN in .env
) else (
  echo [INFO]  .env exists, skip copy
)

REM ---------- 3. Python venv + pip ----------
cd /d "%BACKEND%"
set "PY=%BACKEND%\.venv\Scripts\python.exe"
set "PIP=%BACKEND%\.venv\Scripts\pip.exe"
if not exist "%PY%" (
  echo [INFO]  Creating virtualenv .venv ...
  %PYCMD% -m venv .venv
  if not exist "%PY%" (
    echo [ERROR] venv creation failed
    goto :fail
  )
  echo [INFO]  Upgrading pip / setuptools / wheel ...
  "%PIP%" install --upgrade pip setuptools wheel
) else (
  echo [INFO]  .venv exists, skip creation
)
echo [INFO]  Installing Python deps from requirements.txt ...
"%PIP%" install -r requirements.txt
if errorlevel 1 (
  echo [ERROR] pip install failed
  goto :fail
)
echo [OK]    Python deps installed

REM ---------- 3.5 LOGVIEWER_SECRET_KEY ----------
REM LogViewer fails fast without it; generate a strong random key into backend\.env
REM when empty (idempotent -- existing values are kept).
echo [INFO]  Ensuring LOGVIEWER_SECRET_KEY in backend\.env ...
"%PY%" "%SCRIPT_DIR%gen_logviewer_key.py"
if errorlevel 1 (
  echo [ERROR] failed to ensure LOGVIEWER_SECRET_KEY
  goto :fail
)

REM ---------- 4. migrate ----------
echo [INFO]  Django check + migrate -- first migrate seeds admin/admin23 ...
"%PY%" manage.py check --fail-level ERROR
if errorlevel 1 (
  echo [ERROR] Django check failed
  goto :fail
)
"%PY%" manage.py migrate --noinput
if errorlevel 1 (
  echo [ERROR] migrate failed
  goto :fail
)
echo [OK]    Database migrated
del "%TEMP%\gipfel_admincount.txt" >nul 2>nul
"%PY%" -c "import os,sys;os.environ.setdefault('DJANGO_SETTINGS_MODULE','backend.settings');import django;django.setup();from apps.users.models import User;sys.stdout.write(str(User.objects.filter(username='admin').count()))" > "%TEMP%\gipfel_admincount.txt" 2>nul
if exist "%TEMP%\gipfel_admincount.txt" set /p ADMINN=<"%TEMP%\gipfel_admincount.txt"
if not defined ADMINN set "ADMINN=unknown"
echo [INFO]   admin user count = %ADMINN%

REM ---------- 5. Frontend deps ----------
if %SKIPFE%==0 (
  cd /d "%FRONTEND%"
  if exist "%FRONTEND%\node_modules\.package-lock.json" (
    echo [INFO]  node_modules exists, npm install incremental ...
  ) else (
    echo [INFO]  First install of frontend deps from package.json ...
  )
  npm install --no-audit --no-fund
  if errorlevel 1 (
    echo [ERROR] npm install failed
    goto :fail
  )
  echo [OK]    Frontend deps installed
)

REM ---------- done ----------
echo.
echo [OK]    Bootstrap finished. Next: double-click start-dev.bat
echo    1. Start Django + Vite + LogViewer: scripts\start-dev.bat
echo    2. Open http://localhost:5173 and login with admin / admin23
echo       force-change on first login
echo.
cd /d "%SCRIPT_DIR%"
echo [TIP]  Press any key to close this window...
pause
exit /b 0

:fail
echo.
echo [ERROR] Bootstrap FAILED. See messages above.
echo.
cd /d "%SCRIPT_DIR%"
echo [TIP]  Press any key to close this window...
pause
exit /b 1

REM ---------- subroutine: probe one Python candidate ----------
REM  Sets PYVER and PYCMD when PYPROBE points at a working interpreter.
:probe_python
for /f "tokens=*" %%v in ('%PYPROBE% -c "import sys;v=sys.version_info;print(str(v[0])+chr(46)+str(v[1]))" 2^>nul') do (
  set "PYVER=%%v"
  set "PYCMD=%PYPROBE%"
)
exit /b 0
