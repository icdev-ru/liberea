@echo off
rem Scan the main library. Full reindex: scan.bat --force
rem Extra library: scan.bat --extra    INPX collection: scan.bat --inpx
cd /d "%~dp0"
set "PY=.venv\Scripts\python.exe"
if exist "python\python.exe" set "PY=python\python.exe"
"%PY%" manage.py scan %*
pause
