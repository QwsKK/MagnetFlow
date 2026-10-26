@echo off
REM prefers the merged project-root venv, falls back to legacy system python
set "PY=%~dp0..\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=D:\DevHub\python\python.exe"
"%PY%" "%~dp0harvest.py" %*
