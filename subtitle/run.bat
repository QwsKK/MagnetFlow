@echo off
REM ============================================================
REM  115Translate unified launcher (now part of MagnetFlow)
REM
REM    double-click            -> full pipeline, window stays open
REM    run.bat doctor          -> environment check
REM    run.bat scan            -> list videos to process
REM    run.bat run             -> full pipeline
REM    run.bat run --limit 2   -> process 2 videos only
REM    run.bat status          -> processing progress
REM    run.bat reset --failed  -> clear failed records for retry
REM ============================================================
setlocal
set "ROOT=%~dp0"
if exist "%ROOT%.venv\Scripts\python.exe" (
    set "PY=%ROOT%.venv\Scripts\python.exe"
) else if exist "%ROOT%..\.venv\Scripts\python.exe" (
    REM merged MagnetFlow layout: python lives in the project-root .venv
    set "PY=%ROOT%..\.venv\Scripts\python.exe"
) else (
    echo [error] no python found. Create a venv at project root first:
    echo         py -3.12 -m venv ..\.venv
    echo         ..\.venv\Scripts\python -m pip install -r requirements.txt
    pause
    exit /b 1
)

if "%~1"=="" goto nightly

:cli
"%PY%" "%ROOT%run.py" %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
    echo.
    echo [exit %RC%] finished with errors. Press any key to close.
    pause >nul
)
endlocal & exit /b %RC%

:nightly
title 115Translate
chcp 65001 >nul
echo [%date% %time%] 115Translate start
echo ------------------------------------------------------------
"%PY%" "%ROOT%run.py" run
set "RC=%ERRORLEVEL%"
echo ------------------------------------------------------------
echo [%date% %time%] finished, exit code %RC%
echo Results: work\subs  ^|  log: logs\run.log
echo.
echo Press any key to close.
pause >nul
endlocal & exit /b %RC%
