@echo off
REM Gipfel acceptance one-click runner (Windows, double-click or CLI).
REM NOTE: keep this file ASCII-only and CRLF-terminated; cmd.exe mis-parses
REM       LF-only files with non-ASCII REM text.
REM
REM   tests\ops_check\run_all.cmd              full run (incl. 584-test suite, ~12-16 min)
REM   tests\ops_check\run_all.cmd --fast       skip the full test suite
REM   tests\ops_check\run_all.cmd --only C,E   only the listed steps
REM   tests\ops_check\run_all.cmd --keep-db    keep temp DB copies (default: cleanup)
REM
REM Exit code: 0 = all PASS; 1 = some FAIL; 2 = environment problem.
setlocal
set "HERE=%~dp0"
set "PY=%HERE%..\..\backend\.venv\Scripts\python.exe"

if not exist "%PY%" (
  echo [FATAL] venv python not found: %PY%
  exit /b 2
)

"%PY%" "%HERE%verify_all.py" %*
exit /b %ERRORLEVEL%
