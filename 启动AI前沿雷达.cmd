@echo off
REM ============================================================
REM  AI 前沿雷达 · 图形界面启动器
REM
REM  双击本文件即可。它会：
REM    1) 用 pythonw 启动本地服务（pythonw = 没有黑色控制台窗口）
REM    2) 自动打开浏览器里的界面
REM
REM  关闭程序：在网页右下角点「退出程序」。
REM  直接关掉浏览器标签页不会退出后台服务（设计如此，方便你以后再打开）。
REM
REM  Must keep CRLF line endings.
REM ============================================================

setlocal
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"

REM 优先用 pythonw.exe（无控制台窗口），找不到再退回 python.exe
set "PY="
if defined AF_PYTHON set "AF_PYW=%AF_PYTHON:python.exe=pythonw.exe%"
if defined AF_PYW if exist "%AF_PYW%" set "PY=%AF_PYW%"

set "CAND1=%LOCALAPPDATA%\Programs\DeepSeek Harness\resources\runtime\primary-runtime\dependencies\python\pythonw.exe"
if not defined PY if exist "%CAND1%" set "PY=%CAND1%"
set "CAND2=%USERPROFILE%\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\pythonw.exe"
if not defined PY if exist "%CAND2%" set "PY=%CAND2%"
if not defined PY set "CAND3=%LOCALAPPDATA%\Programs\DeepSeek Harness\resources\runtime\primary-runtime\dependencies\python\python.exe"
if not defined PY if exist "%CAND3%" set "PY=%CAND3%"
if not defined PY set "PY=pythonw"

REM start /b 让启动器立刻退出，不留黑框
start "" "%PY%" "src\app_server.py"
exit /b 0
