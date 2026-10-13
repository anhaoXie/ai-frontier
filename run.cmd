@echo off
REM ============================================================
REM  ai-frontier entry point
REM
REM  ASCII only on purpose: cmd.exe reads .cmd files using the OEM
REM  codepage, so non-ASCII comments get mangled. All Chinese
REM  output comes from Python.
REM
REM  This file must keep CRLF line endings. cmd.exe fails on LF-only
REM  files with bizarre errors ("'ai-frontier' is not recognized").
REM
REM  Usage:
REM    run.cmd collect              collect one round (~45s)
REM    run.cmd collect --window 24  override the time window
REM    run.cmd collect --only arxiv_cl,hn_ai
REM    run.cmd report <md> [--pdf]  markdown report -> Word / PDF
REM    run.cmd status               what did we collect today
REM    run.cmd digest               open today's candidate list
REM    run.cmd daily                collect + pdf (what the 8am task runs)
REM ============================================================

setlocal
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
chcp 65001 >nul 2>nul

REM ---------------------------------------------------------------
REM Pick the interpreter. DSH's bundled runtime MUST win over any
REM system Python: it is the one that ships openpyxl and python-docx.
REM A system Python on this machine has neither, so preferring PATH
REM would silently break the Excel and Word outputs (or crash with
REM ModuleNotFoundError).
REM
REM NOTE: deliberately written WITHOUT parenthesised if-blocks.
REM Inside a ( ) block cmd expands %VAR% for the whole block before
REM running any of it, so "set CAND then test %CAND%" inside a block
REM always tests the PREVIOUS value (empty) and silently falls
REM through to the fallback. Measured, not guessed. Keep these flat.
REM
REM Override with: set AF_PYTHON=C:\path\to\python.exe
REM ---------------------------------------------------------------
set "PY="
if defined AF_PYTHON if exist "%AF_PYTHON%" set "PY=%AF_PYTHON%"
if not defined PY set "AF_C1=%LOCALAPPDATA%\Programs\DeepSeek Harness\resources\runtime\primary-runtime\dependencies\python\python.exe"
if not defined PY if exist "%AF_C1%" set "PY=%AF_C1%"
if not defined PY set "AF_C2=%USERPROFILE%\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
if not defined PY if exist "%AF_C2%" set "PY=%AF_C2%"
if not defined PY set "PY=python"

set "CMD=%~1"
if "%CMD%"=="" set "CMD=help"
shift

set "REST="
:collect_args
if "%~1"=="" goto args_ready
set "REST=%REST% %~1"
shift
goto collect_args
:args_ready

if /i "%CMD%"=="help"    goto :c_help
if /i "%CMD%"=="collect" goto :c_collect
if /i "%CMD%"=="report"  goto :c_report
if /i "%CMD%"=="status"  goto :c_status
if /i "%CMD%"=="digest"  goto :c_digest
if /i "%CMD%"=="daily"   goto :c_daily

echo.
echo   Unknown command: %CMD%
goto :c_help

:c_help
echo.
echo   ai-frontier - AI frontier daily radar
echo.
echo   run.cmd collect [--window N] [--only id1,id2] [--json-only]
echo   run.cmd report ^<file.md^> [--pdf] [--no-docx]
echo   run.cmd status
echo   run.cmd digest
echo   run.cmd daily          collect + pdf (what the 8am task runs)
echo.
echo   Interpreter: %PY%
echo   Override with: set AF_PYTHON=^<path^>
echo.
exit /b 0

:c_collect
"%PY%" src\collect.py %REST%
exit /b %ERRORLEVEL%

:c_report
"%PY%" src\make_docx.py %REST%
exit /b %ERRORLEVEL%

:c_status
"%PY%" src\status.py %REST%
exit /b %ERRORLEVEL%

:c_digest
if exist "out\digests\latest.md" (
  start "" "out\digests\latest.md"
) else (
  echo   No digest yet. Run: run.cmd collect
)
exit /b 0

:c_daily
"%PY%" src\collect.py
if errorlevel 1 exit /b %ERRORLEVEL%
if exist "out\digests\latest.md" (
  "%PY%" src\make_docx.py out\digests\latest.md --pdf
)
exit /b %ERRORLEVEL%
