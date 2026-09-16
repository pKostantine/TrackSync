#!/usr/bin/env bash
# Package dist/TrackSync.app into a disk image you can hand to anyone.
#
# The result is the familiar Mac install experience: open the .dmg, drag the
# app onto the Applications shortcut sitting next to it, eject.
set -uo pipefail
cd "$(dirname "$0")"

unset PYTHONHOME PYTHONPATH
export PYTHONNOUSERSITE=1

say()  { printf '%s\n' "$*"; }
fail() { printf '\n [X] %s\n\n' "$*"; exit 1; }

say ""
say " ============================================"
say "  TrackSync - disk image"
say " ============================================"
say ""

[ "$(uname)" = "Darwin" ] || fail "A .dmg can only be made on a Mac."

# ---- always rebuild ------------------------------------------------------
# Same trap the Windows script fell into once: reuse an old dist/ and you
# happily package yesterday's app into a brand-new installer.
say "  Building the app from the current source..."
say ""
./build.sh || fail "The build failed, so there is nothing to package."

APP="dist/TrackSync.app"
[ -d "$APP" ] || fail "$APP is missing."

VPY=.venv-build/bin/python
TSVER="$("$VPY" tools/version.py)"
BUILT="$(cat "$APP/Contents/MacOS/VERSION.txt" 2>/dev/null || echo '')"
[ "$TSVER" = "$BUILT" ] || fail "The built app says '$BUILT' but the source says '$TSVER'.
     Refusing to package a build that does not match the source."

say ""
say "  Packaging TrackSync $TSVER"

# ---- stage ---------------------------------------------------------------
STAGE="build/dmg"
DMG="dist/TrackSync-$TSVER.dmg"
rm -rf "$STAGE" "$DMG"
mkdir -p "$STAGE"

cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"

# a short note, because an unsigned app needs one extra click the first time
cat > "$STAGE/Read me first.txt" <<'NOTE'
TrackSync
=========

To install: drag TrackSync onto the Applications folder shown next to it.

The first time you open it, macOS will say it "cannot be opened because the
developer cannot be verified". That is because the app is not signed with a
paid Apple Developer certificate, not because anything is wrong with it.

To get past it, once:

  Right-click (or Control-click) TrackSync in Applications, choose Open,
  then click Open in the dialog.

After that it opens normally by double-clicking, like any other app.

What it does
------------
Aligns two recordings of the same event that were made in different places
and started at different times. Load both, press Analyse sync, listen to
them overlapped, then export them aligned for your DAW.

Reads WAV, FLAC, AIFF, CAF, MP3 and M4A/AAC, plus the audio inside MP4 and
MOV. Exports WAV by default.

Command-+ and Command-- scale the interface; Command-0 resets it.
NOTE

# ---- build the image -----------------------------------------------------
say "  Creating the disk image..."
hdiutil create \
    -volname "TrackSync $TSVER" \
    -srcfolder "$STAGE" \
    -ov -format UDZO \
    "$DMG" >/dev/null || fail "hdiutil could not create the image."

[ -f "$DMG" ] || fail "The image was not created."
SIZE="$(du -h "$DMG" | cut -f1)"

# drop a copy beside the project if there is a folder waiting for it
SHARE="../TrackSync-Installer"
if [ -d "$SHARE" ]; then
    rm -f "$SHARE"/TrackSync-*.dmg
    cp "$DMG" "$SHARE/"
    say "  Copied to $SHARE"
fi

say ""
say " ============================================"
say "  Done."
say ""
say "  $DMG   ($SIZE)"
say ""
say "  One file. Send it to anyone with a Mac:"
say "  they open it and drag the app to Applications."
say "  Nothing to install first."
say " ============================================"
say ""
