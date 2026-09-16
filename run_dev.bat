@echo off
rem Run TrackSync from source, without building an .exe.
rem Useful for editing the code and seeing the change immediately.
setlocal
cd /d "%~dp0"

rem A stray PYTHONHOME/PYTHONPATH sends the interpreter looking for its
rem standard library in the wrong place. Cleared for this script only.
set "PYTHONHOME="
set "PYTHONPATH="
set "PYTHONNOUSERSITE=1"

set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY ( where python >nul 2>&1 && set "PY=python" )
if not defined PY (
    echo No Python found on PATH. Install Python 3.10+ and tick "Add to PATH".
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo First run: creating a virtual environment and installing dependencies...
    %PY% -m venv .venv || (pause & exit /b 1)
    .venv\Scripts\python.exe -m pip install --upgrade pip --quiet
    .venv\Scripts\python.exe -m pip install -r requirements.txt || (pause & exit /b 1)
)

.venv\Scripts\python.exe TrackSync.py %*
if errorlevel 1 pause
