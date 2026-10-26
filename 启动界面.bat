@echo off
rem ============================================================
rem  MagnetFlow GUI launcher / MagnetFlow graphical interface
rem  Uses project .venv when present; otherwise falls back to
rem  system python (make sure deps from requirements.txt exist).
rem ============================================================
setlocal
cd /d "%~dp0"

set "PYW=%~dp0.venv\Scripts\pythonw.exe"
set "PY=%~dp0.venv\Scripts\python.exe"

if exist "%PYW%" (
    start "" "%PYW%" app.py
    exit /b 0
)

if exist "%PY%" (
    "%PY%" app.py
    if errorlevel 1 pause
    exit /b %ERRORLEVEL%
)

echo [MagnetFlow] No .venv found. Run this first:
echo     py -3.12 -m venv .venv
echo     .venv\Scripts\python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
echo Then double-click this file again.
echo.
echo Trying system python anyway...
start "" pythonw app.py
