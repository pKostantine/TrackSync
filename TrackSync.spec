# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for the one-folder Windows build.

Build with:   build.bat        (or: pyinstaller --clean --noconfirm TrackSync.spec)
Output:       dist\\TrackSync\\TrackSync.exe
"""

import os

block_cipher = None

# Qt ships far more than a four-panel desktop app needs. Dropping these keeps
# the folder near 90 MB instead of well past 300 MB, and none of them are
# reachable from this UI.
EXCLUDE_QT = [
    "PySide6.QtNetwork", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuick3D",
    "PySide6.QtQuickWidgets", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebChannel", "PySide6.QtWebSockets", "PySide6.Qt3DCore",
    "PySide6.Qt3DRender", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets", "PySide6.QtPdf", "PySide6.QtPdfWidgets",
    "PySide6.QtPositioning", "PySide6.QtPrintSupport", "PySide6.QtSql",
    "PySide6.QtTest", "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtSerialPort",
    "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtUiTools", "PySide6.QtSvgWidgets",
    "PySide6.QtTextToSpeech", "PySide6.QtSensors", "PySide6.QtRemoteObjects",
    "PySide6.QtScxml", "PySide6.QtStateMachine", "PySide6.QtSpatialAudio",
    "PySide6.QtHttpServer", "PySide6.QtGraphs", "PySide6.QtLocation",
    # NB: shiboken6 is NOT excludable -- it is the binding core every PySide6
    # module imports. Excluding it builds cleanly and then fails at launch
    # with "No module named 'shiboken6.Shiboken'". `TrackSync.exe --selftest`
    # exists to catch exactly this class of mistake.
]

# NB: distutils and setuptools are deliberately NOT excluded. Python 3.12
# removed distutils from the standard library, so setuptools vendors its own
# copy and PyInstaller aliases that copy back to the name "distutils". If the
# name is on this list, that alias hook dies with
#   ValueError: Target module "distutils" already imported as ExcludedModule
# part-way through the build. cffi pulls in the chain that triggers it, so
# this bites on 3.12+ and not at all on older interpreters.
EXCLUDE_OTHER = [
    "tkinter", "unittest", "pydoc_data", "test", "lib2to3",
    "matplotlib", "scipy", "pandas", "PIL", "IPython", "pytest",
    "sqlite3", "xmlrpc",
]

a = Analysis(
    ["TrackSync.py"],
    pathex=[os.path.abspath(".")],
    binaries=[],
    datas=[
        ("assets/TrackSync.ico", "assets"),
        ("assets/logo.png", "assets"),
        # the checkbox tick, referenced by the stylesheet at runtime
        ("assets/check.png", "assets"),
    ],
    # soundfile and sounddevice load their DLLs through cffi/ctypes at import
    # time, so PyInstaller's static scan cannot see them without these hooks.
    hiddenimports=[
        "soundfile", "sounddevice", "_soundfile_data", "soxr",
        "numpy", "cffi", "_cffi_backend",
        # PyAV carries ffmpeg's decoders for M4A/AAC and friends. Its shared
        # libraries live in av.libs and are pulled in by PyInstaller's hook.
        "av", "av.audio", "av.audio.resampler", "av.codec", "av.container",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDE_QT + EXCLUDE_OTHER,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# Two executables from one analysis, sharing every DLL in the folder.
#
#   TrackSync.exe        windowed: no console flashes behind the UI
#   TrackSync-check.exe  console: prints, and returns a real exit code
#
# The second one exists because a windowed build cannot report anything. Qt
# apps are linked as GUI subsystem, so Windows gives them no stdout at all --
# sys.stdout is None, print() raises, and the launcher returns immediately
# with an exit code that means nothing. A verification step built on that is
# worse than none, because it always passes. The console twin costs about a
# megabyte and makes `--selftest` tell the truth.

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TrackSync",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/TrackSync.ico",
)

exe_console = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TrackSync-check",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/TrackSync.ico",
)

coll = COLLECT(
    exe,
    exe_console,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="TrackSync",
)
