@echo off
rem ============================================================
rem  MagnetFlow CLI launcher
rem    run.bat                 -> open Web console (no console window)
rem    run.bat gui             -> open old tkinter desktop window
rem    run.bat web             -> Web console (same as no args, keeps console log)
rem    run.bat chain           -> one-click full pipeline
rem    run.bat doctor          -> environment check (all 3 stages)
rem    run.bat push --test     -> test 115 cookie
rem    run.bat harvest ...     -> harvester passthrough
rem    run.bat subtitle ...    -> subtitle pipeline (rawsub) passthrough
rem ============================================================
setlocal
cd /d "%~dp0"

set "PYW=%~dp0.venv\Scripts\pythonw.exe"
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

if "%~1"=="" (
    if exist "%PYW%" (
        start "" "%PYW%" -m mflow.web.server --port 8420
    ) else (
        start "" "%PY%" -m mflow.web.server --port 8420
    )
    exit /b 0
)

"%PY%" run.py %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
    echo.
    echo [exit code %RC%]
    pause
)
exit /b %RC%
