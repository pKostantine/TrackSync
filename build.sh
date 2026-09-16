#!/usr/bin/env bash
# TrackSync - macOS build.
#
# Produces dist/TrackSync.app and verifies it before claiming success.
# Run make_dmg.sh afterwards to get something shareable.
set -uo pipefail
cd "$(dirname "$0")"

# A stray PYTHONHOME/PYTHONPATH sends the interpreter looking for its standard
# library in the wrong place. Cleared for this script only.
unset PYTHONHOME PYTHONPATH
export PYTHONNOUSERSITE=1

say()  { printf '%s\n' "$*"; }
fail() { printf '\n [X] %s\n\n' "$*"; exit 1; }

say ""
say " ============================================"
say "  TrackSync - macOS build"
say " ============================================"
say ""

[ "$(uname)" = "Darwin" ] || fail "This builds a Mac app; run it on a Mac.
     On Windows use build.bat instead."

# ---- find a Python -------------------------------------------------------
PY=""
for c in python3.13 python3.12 python3.11 python3; do
    command -v "$c" >/dev/null 2>&1 && { PY="$c"; break; }
done
[ -n "$PY" ] || fail "No Python 3 found.
     Install it from https://www.python.org/downloads/macos/
     or with Homebrew:  brew install python"

"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' \
    || fail "Python 3.9 or newer is required (found $("$PY" -V))."
say "  Using $("$PY" -V) at $(command -v "$PY")"
say "  Building for $(uname -m)"

# ---- build environment ---------------------------------------------------
if [ ! -x .venv-build/bin/python ]; then
    say "  Creating build environment..."
    "$PY" -m venv .venv-build || fail "Could not create the virtual environment."
fi
VPY=.venv-build/bin/python

say "  Installing dependencies..."
"$VPY" -m pip install --upgrade pip --quiet || fail "pip could not update itself."
"$VPY" -m pip install -r requirements.txt --quiet \
    || fail "Could not install the dependencies (no internet?)."

# ---- version -------------------------------------------------------------
TSVER="$("$VPY" tools/version.py)" || fail "Could not read the version."
[ -n "$TSVER" ] || fail "Could not read the version out of tracksync/__init__.py."
say "  Building TrackSync $TSVER"

# ---- icons ---------------------------------------------------------------
say "  Generating icons..."
"$VPY" tools/make_icons.py || fail "Could not generate the icons."

# ---- sanity check before spending time on the freeze ---------------------
say "  Checking the engine..."
"$VPY" -c "import numpy,soundfile,soxr,sounddevice,PySide6,av
from tracksync import audiofile,engine
print('    imports OK -', len(audiofile.supported_extensions()), 'formats')" \
    || fail "A dependency is missing or broken. See above."

# ---- freeze --------------------------------------------------------------
say "  Building (this takes a few minutes the first time)..."
rm -rf build dist
TRACKSYNC_VERSION="$TSVER" "$VPY" -m PyInstaller --clean --noconfirm \
    TrackSync-mac.spec || fail "PyInstaller failed. See above."

APP="dist/TrackSync.app"
[ -d "$APP" ] || fail "The build finished but $APP is missing."

printf '%s' "$TSVER" > "$APP/Contents/MacOS/VERSION.txt"

# ---- ad-hoc signature ----------------------------------------------------
# Apple Silicon refuses to run a binary with no signature at all. PyInstaller
# ad-hoc signs as it goes, but writing VERSION.txt into the bundle invalidates
# that, so re-sign. This is NOT notarisation: it makes the app run on THIS
# machine and on any Mac where the user right-clicks and chooses Open.
say "  Signing (ad-hoc)..."
codesign --force --deep --sign - "$APP" 2>/dev/null \
    || say "  (codesign unavailable - the app will still run locally)"

# ---- prove it works ------------------------------------------------------
# The bundled binary is windowed, but on macOS a windowed binary still has a
# usable stdout when launched from a terminal, so --selftest reports properly
# here without needing the separate console twin Windows requires.
say ""
say "  Verifying the build..."
say ""
"$APP/Contents/MacOS/TrackSync" --selftest || fail "The app was built but failed its self-test. See above."

say ""
say " ============================================"
say "  Done."
say ""
say "  TrackSync $TSVER"
say "  dist/TrackSync.app"
say ""
say "  Drag it to /Applications, or run make_dmg.sh"
say "  to get a disk image you can send to someone."
say " ============================================"
say ""
