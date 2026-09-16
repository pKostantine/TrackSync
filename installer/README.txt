TrackSync
=========

Aligns two recordings of the same event that were made in different places
and started at different times, and tells you the numbers while it does it.


To install
----------

Unzip this folder somewhere first -- running the installer from inside the
.zip will not work -- then double-click:

    Install TrackSync.bat

It installs for the current user only, so it never asks for an administrator
password. TrackSync then appears in the Start Menu and on the Desktop.

Windows may show "Windows protected your PC" the first time, because the
program is not code-signed. Click "More info", then "Run anyway".


To use it
---------

1. Drop your two recordings onto the A and B slots. Order does not matter.
2. Press "Analyse sync".
3. Press Play to hear them overlapped, with a fader for each track.
4. Press "Export aligned pair" and choose what to write.

The exported files drop onto a DAW timeline at 00:00:00 with no nudging:
whichever track started later gets exactly the right amount of silence in
front of it.

Reads WAV, FLAC, AIFF, MP3, M4A/AAC, OGG, and the audio inside MP4 and MOV.
Exports WAV by default, and FLAC or AIFF if you prefer.

Ctrl and + or - scale the whole interface; Ctrl and 0 puts it back.


To remove it
------------

Settings > Apps > Installed apps > TrackSync > Uninstall.
Or run Uninstall-TrackSync.ps1 from the install folder.
