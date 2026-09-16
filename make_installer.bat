@echo off
rem Build TrackSync-Setup-<version>.exe -- the single-file installer.
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "PYTHONHOME="
set "PYTHONPATH="
set "PYTHONNOUSERSITE=1"

echo.
echo  ============================================
echo   TrackSync - installer
echo  ============================================
echo.

rem ---- 1. always rebuild ---------------------------------------------------
rem
rem This used to skip the build whenever dist\TrackSync\TrackSync.exe already
rem existed, which is a trap: edit the source, run this, and it cheerfully
rem packages yesterday's frozen app into a brand-new installer. That is
rem exactly how an "updated" setup.exe ends up containing the old program.
rem Freezing takes a couple of minutes; shipping the wrong code costs more.
echo  Building the program from the current source...
echo.
call "%~dp0build.bat"
if not exist "dist\TrackSync\TrackSync.exe" (
    echo  [X] The build did not produce TrackSync.exe.
    pause
    exit /b 1
)

rem ---- 2. confirm the build matches the source -----------------------------
rem The helper compares the stamp in the frozen folder against the source and
rem exits non-zero if they differ, so batch only reads an errorlevel.
.venv-build\Scripts\python.exe tools\version.py --check "dist\TrackSync\VERSION.txt"
if errorlevel 1 (
    echo.
    echo  [X] The frozen build does not match the source version.
    echo      Refusing to package it. Delete dist\ and run again.
    pause
    exit /b 1
)
set "TSVER="
set /p TSVER=<"dist\TrackSync\VERSION.txt"
if not defined TSVER (
    echo  [X] The build left no readable VERSION.txt.
    pause
    exit /b 1
)
echo.
echo  Packaging TrackSync !TSVER!

rem ---- 3. find Inno Setup, fetching it if need be --------------------------
call :find_iscc
if defined ISCC goto :compile

echo.
echo  Inno Setup is not installed. It is the free tool that turns the
echo  built program into a single setup.exe with the usual wizard.
echo  This script can fetch it for you.
echo.
choice /c YN /m " Download and install Inno Setup now"
if errorlevel 2 goto :no_iscc

echo.
echo  Trying winget... (Windows may ask permission once)
where winget >nul 2>&1
if errorlevel 1 goto :try_download
winget install -e --id JRSoftware.InnoSetup --silent --accept-package-agreements --accept-source-agreements
call :find_iscc
if defined ISCC goto :compile

:try_download
echo.
echo  Downloading Inno Setup from jrsoftware.org...
set "ISDL=%TEMP%\innosetup-latest.exe"
if exist "%ISDL%" del /q "%ISDL%"
curl.exe -L --fail --silent --show-error -o "%ISDL%" https://jrsoftware.org/download.php/is.exe
if errorlevel 1 (
    echo  [X] Download failed.
    goto :no_iscc
)
echo  Installing Inno Setup...
"%ISDL%" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP-
call :find_iscc
if not defined ISCC goto :no_iscc

rem ---- 4. compile ----------------------------------------------------------
:compile
echo.
echo  Using %ISCC%
echo  Compiling the installer...
echo.
rem any older installer in dist\ goes, so there is only ever one to run
del /q "dist\TrackSync-Setup*.exe" >nul 2>&1
"%ISCC%" /Q "/DAppVersion=!TSVER!" "installer\TrackSync.iss"
if errorlevel 1 (
    echo.
    echo  [X] Inno Setup reported an error. The lines above say what.
    pause
    exit /b 1
)

set "SETUP=dist\TrackSync-Setup-!TSVER!.exe"
if not exist "!SETUP!" (
    echo  [X] The compile finished but !SETUP! is missing.
    pause
    exit /b 1
)
for %%F in ("!SETUP!") do set /a SETUPMB=%%~zF/1048576

rem drop a copy in the sibling TrackSync-Installer folder, if there is one
set "SHARE=%~dp0..\TrackSync-Installer"
if exist "%SHARE%" (
    del /q "%SHARE%\TrackSync-Setup*.exe" >nul 2>&1
    copy /y "!SETUP!" "%SHARE%\" >nul
    echo  Copied to %SHARE%
)

echo.
echo  ============================================
echo   Done.
echo.
echo   !SETUP!   (!SETUPMB! MB)
echo.
echo   One file. Send it to anyone: they double-click
echo   it and press Install. No Python, no admin
echo   rights, nothing to install first.
echo  ============================================
echo.
pause
exit /b 0

rem ---- fallback ------------------------------------------------------------
:no_iscc
rem Falling back rather than leaving you with nothing: the zip installs the
rem same program, it just looks like a script instead of a wizard.
echo.
echo  Building the zip fallback instead...
call :make_zip
echo.
echo  ============================================
echo   No single-file setup.exe this time.
echo.
echo   Inno Setup is what compiles it. Install it from
echo     https://jrsoftware.org/isdl.php
echo   and run this script again to get one.
echo.
echo   In the meantime:
echo     dist\TrackSync-Installer.zip
echo   Unzip it, double-click "Install TrackSync.bat".
echo  ============================================
echo.
pause
exit /b 1

rem --------------------------------------------------------------------------
:make_zip
set "STAGE=%~dp0build\installer\TrackSync-Installer"
set "ZIP=%~dp0dist\TrackSync-Installer.zip"
if exist "%STAGE%" rmdir /s /q "%STAGE%"
mkdir "%STAGE%\app" 2>nul
robocopy "dist\TrackSync" "%STAGE%\app" /E /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 (
    echo  [X] Could not stage the application files.
    exit /b 1
)
copy /y "installer\Install TrackSync.bat"  "%STAGE%\" >nul
copy /y "installer\Install-TrackSync.ps1"  "%STAGE%\" >nul
copy /y "installer\README.txt"             "%STAGE%\" >nul
if exist "%ZIP%" del /q "%ZIP%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Compress-Archive -Path '%STAGE%' -DestinationPath '%ZIP%' -CompressionLevel Optimal -Force"
exit /b 0

rem --------------------------------------------------------------------------
:find_iscc
rem Locate ISCC.exe, the Inno Setup compiler.
rem
rem No for-loop over candidate paths on purpose: the obvious way to write
rem that puts %ProgramFiles(x86)% inside parentheses, and the closing paren
rem in the variable name ends the block early.
set "ISCC="
set "PF86=%ProgramFiles(x86)%"
set "PF64=%ProgramFiles%"

if exist "%PF86%\Inno Setup 6\ISCC.exe" set "ISCC=%PF86%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%PF64%\Inno Setup 6\ISCC.exe" set "ISCC=%PF64%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%PF86%\Inno Setup 5\ISCC.exe" set "ISCC=%PF86%\Inno Setup 5\ISCC.exe"

if not defined ISCC for /f "delims=" %%I in ('where ISCC.exe 2^>nul') do set "ISCC=%%I"

if not defined ISCC for /f "tokens=2,*" %%A in ('reg query "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 6_is1" /v InstallLocation 2^>nul ^| find "InstallLocation"') do if exist "%%B\ISCC.exe" set "ISCC=%%B\ISCC.exe"

if not defined ISCC for /f "tokens=2,*" %%A in ('reg query "HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 6_is1" /v InstallLocation 2^>nul ^| find "InstallLocation"') do if exist "%%B\ISCC.exe" set "ISCC=%%B\ISCC.exe"

exit /b 0
