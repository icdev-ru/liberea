@echo off
rem Liberea - start the server
cd /d "%~dp0"
if exist "python\python.exe" goto portable

if not exist ".venv\Scripts\python.exe" (
    echo [1/3] Creating virtual environment...
    python -m venv .venv || (echo Python 3.11+ not found. Install it from python.org with "Add to PATH". & pause & exit /b 1)
    echo [2/3] Installing packages...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt || (pause & exit /b 1)
)
set "PY=.venv\Scripts\python.exe"
goto start

:portable
set "PY=python\python.exe"

:start
if not exist "config.toml" copy config.example.toml config.toml >nul
echo Starting Liberea...
"%PY%" manage.py serve
pause
