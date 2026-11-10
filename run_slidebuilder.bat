@echo off
rem Slide Builder launcher: double-click to run. First run creates a local .venv and installs dependencies.
setlocal
cd /d "%~dp0"

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY (
    echo Python 3.10 or newer was not found. Install it from https://www.python.org/downloads/ and re-run.
    pause
    exit /b 1
)

if not exist ".venv\.deps_ok" (
    echo First run: creating environment and installing packages ^(a few minutes, needs internet^)...
    if not exist ".venv\Scripts\python.exe" %PY% -m venv .venv || goto :fail
    ".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
    ".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt || goto :fail
    echo ok> ".venv\.deps_ok"
)

start "" ".venv\Scripts\pythonw.exe" -m slidebuilder
exit /b 0

:fail
echo.
echo Setup failed. Delete the .venv folder and try again.
pause
exit /b 1
