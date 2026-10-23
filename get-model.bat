@echo off
rem Download the en-ru translation model (~100 MB, once)
cd /d "%~dp0"
set "PY=.venv\Scripts\python.exe"
if exist "python\python.exe" set "PY=python\python.exe"
"%PY%" manage.py get-model %*
pause
