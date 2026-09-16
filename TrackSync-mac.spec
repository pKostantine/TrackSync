# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for the macOS app bundle.

Build with:   ./build.sh        (or: pyinstaller --clean --noconfirm TrackSync-mac.spec)
Output:       dist/TrackSync.app

Kept separate from TrackSync.spec rather than branching inside one file: the
Windows build makes a folder with two .exe files, the macOS build makes a
single .app bundle whose console twin lives inside it. Those are different
enough that one spec full of `if sys.platform` reads worse than two.
"""

import os

block_cipher = None

EXCLUDE_QT = [
    "PySide6.QtNetwork", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuick3D",
    "PySide6.QtQuickWidgets", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebChannel", "PySide6.QtWebSockets", "PySide6.Qt3DCore",
    "PySide6.Qt3DRender", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets",
    "PySide6.QtPdf", "PySide6.QtPdfWidgets",
    "PySide6.QtPositioning", "PySide6.QtSql",
    "PySide6.QtTest", "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtSerialPort",
    "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtUiTools",
    "PySide6.QtTextToSpeech", "PySide6.QtSensors", "PySide6.QtRemoteObjects",
    "PySide6.QtScxml", "PySide6.QtStateMachine", "PySide6.QtSpatialAudio",
    "PySide6.QtHttpServer", "PySide6.QtGraphs", "PySide6.QtLocation",
    # shiboken6 is NOT excludable: it is the binding core every PySide6 module
    # imports. Excluding it builds cleanly and then fails at launch.
]

# NB distutils and setuptools are deliberately NOT excluded -- Python 3.12
# removed distutils from the stdlib, setuptools vendors it, and PyInstaller's
# alias hook dies part-way through the build if the name is on this list.
EXCLUDE_OTHER = [
    "tkinter", "unittest", "pydoc_data", "test", "lib2to3",
    "matplotlib", "scipy", "pandas", "PIL", "IPython", "pytest",
]

a = Analysis(
    ["TrackSync.py"],
    pathex=[os.path.abspath(".")],
    binaries=[],
    datas=[
        ("assets/TrackSync.icns", "assets"),
        ("assets/TrackSync.ico", "assets"),
        ("assets/logo.png", "assets"),
        ("assets/check.png", "assets"),
    ],
    hiddenimports=[
        "soundfile", "sounddevice", "_soundfile_data", "soxr",
        "numpy", "cffi", "_cffi_backend",
        "av", "av.audio", "av.audio.resampler", "av.codec", "av.container",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDE_QT + EXCLUDE_OTHER,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

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
    argv_emulation=False,      # we take file arguments ourselves, not via Finder
    target_arch=None,          # build for whatever Mac this is (arm64 or x86_64)
    codesign_identity=None,    # ad-hoc signed by PyInstaller; enough to run locally
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="TrackSync",
)

app = BUNDLE(
    coll,
    name="TrackSync.app",
    icon="assets/TrackSync.icns",
    bundle_identifier="com.tracksync.TrackSync",
    version=os.environ.get("TRACKSYNC_VERSION", "0.0.0"),
    info_plist={
        "CFBundleName": "TrackSync",
        "CFBundleDisplayName": "TrackSync",
        "CFBundleShortVersionString": os.environ.get("TRACKSYNC_VERSION", "0.0.0"),
        "CFBundleVersion": os.environ.get("TRACKSYNC_VERSION", "0.0.0"),
        # without this the window is rendered at 1x and scaled up, which looks
        # soft on every Mac made in the last decade
        "NSHighResolutionCapable": True,
        # let the app follow the system theme instead of being forced to Aqua;
        # the interface is dark by design
        "NSRequiresAquaSystemAppearance": False,
        "LSMinimumSystemVersion": "11.0",
        "LSApplicationCategoryType": "public.app-category.music",
        "CFBundleDocumentTypes": [
            {
                "CFBundleTypeName": "Audio file",
                "CFBundleTypeRole": "Viewer",
                "LSHandlerRank": "Alternate",
                "LSItemContentTypes": [
                    "public.audio", "public.mp3", "public.aiff-audio",
                    "com.microsoft.waveform-audio",
                    "com.apple.m4a-audio", "org.xiph.flac",
                ],
            }
        ],
    },
)
