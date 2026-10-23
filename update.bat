@echo off
rem Install / update Python packages after a program update
cd /d "%~dp0"
set "PY=.venv\Scripts\python.exe"
if exist "python\python.exe" set "PY=python\python.exe"
"%PY%" -m pip install -r requirements.txt
pause
