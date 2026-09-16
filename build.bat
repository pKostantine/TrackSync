@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo.
echo  ============================================
echo   TrackSync - Windows build
echo  ============================================
echo.

rem ---- isolate the build from a polluted environment ----------------------
rem A stray PYTHONHOME / PYTHONPATH makes the interpreter look for its
rem standard library in the wrong place ("Could not find platform independent
rem libraries") and can make PyInstaller freeze against a DIFFERENT python
rem DLL than the one the virtual environment is built on. setlocal keeps
rem these cleared for this script only; nothing outside it is changed.
if defined PYTHONHOME (
    echo  Note: PYTHONHOME is set to "%PYTHONHOME%" - ignoring it for this build.
)
if defined PYTHONPATH (
    echo  Note: PYTHONPATH is set - ignoring it for this build.
)
set "PYTHONHOME="
set "PYTHONPATH="
set "PYTHONNOUSERSITE=1"

rem ---- find a Python ------------------------------------------------------
set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY ( where python >nul 2>&1 && set "PY=python" )
if not defined PY (
    echo  [X] No Python found on PATH.
    echo      Install Python 3.10 or newer from https://www.python.org/downloads/
    echo      and tick "Add python.exe to PATH" in the installer.
    echo.
    pause
    exit /b 1
)

for /f "tokens=*" %%v in ('%PY% -c "import sys;print(sys.version.split()[0])"') do set "PYVER=%%v"
echo  Using Python !PYVER!
%PY% -c "import sys; sys.exit(0 if sys.version_info>=(3,9) else 1)"
if errorlevel 1 (
    echo  [X] Python 3.9 or newer is required.
    pause
    exit /b 1
)

rem ---- build environment --------------------------------------------------
if not exist ".venv-build\Scripts\python.exe" (
    echo  Creating build environment...
    %PY% -m venv .venv-build || goto :fail
)
set "VPY=.venv-build\Scripts\python.exe"

"%VPY%" -c "import sys; print('    prefix', sys.prefix); print('    base  ', sys.base_prefix)"

echo  Installing dependencies...
"%VPY%" -m pip install --upgrade pip --quiet || goto :fail
"%VPY%" -m pip install -r requirements.txt --quiet || goto :fail

rem ---- what version are we building? --------------------------------------
rem
rem The version is read by a helper script that WRITES a file, and batch
rem only reads that file. Capturing a program's output inline is where this
rem script kept going wrong: a `for /f` whose command starts with a quote
rem gets split on the inner quotes, and cmd tries to run half of it as a
rem program name. Writing a file and reading it back has no such trap.
set "TSVERFILE=%TEMP%\tracksync_version.txt"
if exist "%TSVERFILE%" del /q "%TSVERFILE%"
"%VPY%" tools\version.py --write "%TSVERFILE%" >nul
set "TSVER="
if exist "%TSVERFILE%" set /p TSVER=<"%TSVERFILE%"
if exist "%TSVERFILE%" del /q "%TSVERFILE%"
if not defined TSVER (
    echo  [X] Could not read the version out of tracksync\__init__.py.
    goto :fail
)
echo  Building TrackSync !TSVER!

rem ---- sanity check before spending time on the freeze ---------------------
echo  Checking the engine...
"%VPY%" -c "import numpy,soundfile,soxr,sounddevice,PySide6,av;from tracksync import audiofile,engine;print('    imports OK -',len(audiofile.supported_extensions()),'formats')" || goto :fail

rem ---- freeze -------------------------------------------------------------
echo  Building (this takes a few minutes the first time)...
if exist "build" rmdir /s /q "build"
if exist "dist"  rmdir /s /q "dist"
"%VPY%" -m PyInstaller --clean --noconfirm TrackSync.spec || goto :fail

if not exist "dist\TrackSync\TrackSync.exe" (
    echo  [X] The build finished but TrackSync.exe is missing.
    goto :fail
)

rem Stamp the folder with what went into it, so a later packaging step can
rem tell whether it is looking at a fresh build or yesterday's.
rem Stamp the frozen folder with what went into it. Written by the helper
rem so there is exactly one place that knows how to spell the version.
"%VPY%" tools\version.py --write "dist\TrackSync\VERSION.txt" >nul

rem ---- prove the frozen build actually works ------------------------------
rem A freeze can succeed and still produce an exe that will not launch, e.g.
rem when a needed DLL or binding module was left out. This runs the estimator
rem inside the built program against a known offset, so that shows up here
rem rather than as a window that never opens.
rem
rem It runs TrackSync-check.exe, not TrackSync.exe. The windowed build has no
rem console: Windows gives a GUI-subsystem process no stdout, cmd does not
rem wait for it, and the errorlevel that comes back means nothing -- so
rem checking the windowed exe would silently pass no matter what was wrong.
echo.
echo  Verifying the build...
echo.
if not exist "dist\TrackSync\TrackSync-check.exe" (
    echo  [X] TrackSync-check.exe is missing from the build.
    goto :fail
)
"dist\TrackSync\TrackSync-check.exe" --selftest
if errorlevel 1 (
    echo.
    echo  [X] The program was built but failed its self-test. See above.
    goto :fail
)

echo.
echo  ============================================
echo   Done.
echo.
echo   TrackSync !TSVER!
echo   dist\TrackSync\TrackSync.exe
echo.
echo   Move the whole "TrackSync" folder wherever you
echo   like and make a shortcut to the .exe inside it.
echo  ============================================
echo.
pause
exit /b 0

:fail
echo.
echo  [X] Build failed. The last lines above say why.
echo.
pause
exit /b 1
