@echo off
rem Ground-truth tests: synthetic recordings with known offsets and known
rem drift, so the numbers are checked rather than eyeballed.
setlocal
cd /d "%~dp0"

rem A stray PYTHONHOME/PYTHONPATH sends the interpreter looking for its
rem standard library in the wrong place. Cleared for this script only.
set "PYTHONHOME="
set "PYTHONPATH="
set "PYTHONNOUSERSITE=1"

if not exist ".venv\Scripts\python.exe" (
    echo Run run_dev.bat once first to create the environment.
    pause
    exit /b 1
)

echo === engine ===
.venv\Scripts\python.exe -m tests.test_engine
echo.
echo === app ===
set QT_QPA_PLATFORM=offscreen
.venv\Scripts\python.exe -m tests.test_app
pause
