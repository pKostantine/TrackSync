@echo off
rem Double-click me.
rem
rem PowerShell blocks unsigned scripts by default; -ExecutionPolicy Bypass
rem applies to THIS invocation only and changes nothing on the machine.
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install-TrackSync.ps1"
if errorlevel 1 (
    echo.
    echo Installation did not complete. The messages above say why.
    pause
)
