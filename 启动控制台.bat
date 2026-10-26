@echo off
rem ============================================================
rem  MagnetFlow Web console launcher
rem  Double-click to start the local web console on port 8420.
rem  NOTE: this file must stay ASCII-only (cmd.exe reads .bat as
rem  GBK; UTF-8 Chinese text here turns into garbage commands).
rem ============================================================
setlocal
cd /d "%~dp0"

set "PY=%~dp0.venv\Scripts\python.exe"

if not exist "%PY%" (
    echo [MagnetFlow] .venv not found. Run these commands first:
    echo     py -3.12 -m venv .venv
    echo     .venv\Scripts\python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    pause
    exit /b 1
)

echo [MagnetFlow] Starting web console: http://127.0.0.1:8420
echo [MagnetFlow] Close this window to stop the server.
"%PY%" -m mflow.web.server --port 8420
if errorlevel 1 pause
