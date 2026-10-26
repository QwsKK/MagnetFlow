@echo off
chcp 65001 >nul
REM MagnetFlow subtitle nightly entry point.
REM Used by Windows Task Scheduler (register_task.ps1).

set "ROOT=%~dp0.."
pushd "%ROOT%"

if exist "%ROOT%\.venv\Scripts\python.exe" (
    set "PY=%ROOT%\.venv\Scripts\python.exe"
) else if exist "%ROOT%\..\.venv\Scripts\python.exe" (
    REM merged MagnetFlow layout: venv lives one level up (project root)
    set "PY=%ROOT%\..\.venv\Scripts\python.exe"
) else (
    set "PY=python"
)

echo [%date% %time%] 115Translate nightly run start.
"%PY%" "%ROOT%\run.py" run
set RC=%ERRORLEVEL%
echo [%date% %time%] 115Translate nightly run finished. exit=%RC%

popd
exit /b %RC%
