@echo off
cd /d %~dp0
if exist "%~dp0.venv\Scripts\pythonw.exe" (
    start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0ui.py"
) else (
    start "" pythonw "%~dp0ui.py"
)
