@echo off
REM ============================================================
REM  Daily collection entry point for Windows Task Scheduler.
REM
REM  Why this file exists: schtasks /TR cannot survive nested
REM  quotes plus spaces in the interpreter path. Pointing the task
REM  at a wrapper script keeps all quoting in one place and makes
REM  the task definition readable.
REM
REM  Must keep CRLF line endings.
REM
REM  This ONLY collects. Writing the actual report needs web
REM  verification and domain judgement, which is done in a DSH
REM  session via the ai-frontier-daily skill.
REM ============================================================

setlocal
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
chcp 65001 >nul 2>nul

set "PY="
if defined AF_PYTHON if exist "%AF_PYTHON%" set "PY=%AF_PYTHON%"
if not defined PY set "AF_C1=%LOCALAPPDATA%\Programs\DeepSeek Harness\resources\runtime\primary-runtime\dependencies\python\python.exe"
if not defined PY if exist "%AF_C1%" set "PY=%AF_C1%"
if not defined PY set "AF_C2=%USERPROFILE%\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
if not defined PY if exist "%AF_C2%" set "PY=%AF_C2%"
if not defined PY set "PY=python"

if not exist "out\logs" mkdir "out\logs"

REM 日志文件名刻意不带日期。两个坑：
REM   1) %DATE% 跟区域设置绑定，中文系统上会拼出 "task-周一 22610.log"。
REM   2) for /f 启动子命令时会在空格处切断解释器路径（"%PY%" 含空格），
REM      所以"让 Python 算日期"这条路在 batch 里同样走不通。
REM 保留一份滚动日志最省事：每次覆盖，内容自带完整输出。
REM 需要按天归档的详细记录在 out\logs\collect-<日期>.json 里。
"%PY%" src\collect.py >> "out\logs\task-last.log" 2>&1
set "RC=%ERRORLEVEL%"
exit /b %RC%
