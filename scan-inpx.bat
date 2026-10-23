@echo off
rem Import / update the INPX collection
cd /d "%~dp0"
set "PY=.venv\Scripts\python.exe"
if exist "python\python.exe" set "PY=python\python.exe"
"%PY%" manage.py scan --inpx %*
pause
