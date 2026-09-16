# TrackSync

TrackSync measures the timing difference between two recordings of the same
event. It reports the start offset, clock drift, confidence, and independent
probe measurements, then exports audio with the required padding.

It is useful for aligning recordings from separate cameras, field recorders,
microphones, or other devices when the files started at different times or
their clocks ran at slightly different speeds.

## What it does

- Finds the relative offset even when the recordings have different lengths
  or the shared material occurs later in one file.
- Estimates clock drift across the overlapping part of the recordings.
- Shows a measurement summary and a probe table so unreliable or changing
  alignment is visible instead of being hidden behind one number.
- Displays aligned waveforms with zoom and a shared timeline.
- Plays both tracks together with independent volume, mute, and solo controls.
- Exports one or both tracks with silence prepended where necessary.
- Optionally writes a JSON report containing the analysis values.

TrackSync does not modify the input files during analysis.

## Requirements

For running from source, use Python 3.10 or newer and install the packages in
`requirements.txt`:

```text
numpy
soundfile
soxr
sounddevice
av
PySide6
```

`av` provides support for formats that libsndfile cannot open, including
M4A/AAC and audio in video containers. Audio monitoring also requires a
working output device; analysis and export still work without one.

## Run from source

### Windows

```bat
run_dev.bat
```

The script creates `.venv` when needed, installs the requirements, and starts
the application. To run the Python entry point directly after installing the
requirements:

```bat
python TrackSync.py
```

### macOS and Linux

```sh
./run_dev.sh
```

The equivalent module entry point is:

```sh
python3 -m tracksync
```

## Use the application

1. Choose a recording for track A and a recording for track B with
   **Browse...**, or drag a file onto either track row. The row shows duration,
   sample rate, channel count, and detected subtype.
2. Click **Analyse sync**. Use **Settings...** first if the defaults do not
   suit the recordings.
3. Review the offset, clock drift, confidence, probe scatter, and verdict in
   the **Measurement** panel. The **Probes** table shows each independent
   measurement and whether it was used in the fit.
4. Use **Play**, the scrubber, and the A/B mixer controls to listen to the
   alignment. The timeline can be zoomed and clicked to seek.
5. Click **Export aligned pair...** to choose the output folder, tracks,
   container, bit depth, and whether to write a JSON report.

The order of the input files does not matter. Use **Swap A / B** if you want
to reverse their display order.

### Keyboard shortcuts

| Action | Windows/Linux | macOS |
|---|---|---|
| Open track A | Ctrl+O | Cmd+O |
| Open track B | Ctrl+Shift+O | Cmd+Shift+O |
| Analyse sync | Ctrl+R | Cmd+R |
| Export | Ctrl+E | Cmd+E |
| Zoom in/out | Ctrl++ / Ctrl+- | Cmd++ / Cmd+- |
| Reset interface scale | Ctrl+0 | Cmd+0 |
| Quit | Ctrl+Q | Cmd+Q |

The interface scale is remembered between launches. The same actions are also
available from the File, View, and Help menus.

## Analysis settings

The defaults are suitable for most recordings:

- **Probe window:** 10 seconds. Longer windows can help with noisy material,
  but take longer and provide fewer independent positions.
- **Number of probes:** 13. More probes improve drift and discontinuity
  diagnosis at the cost of analysis time.
- **Refinement window:** 250 ms around the coarse result. Widen it when the
  report says probes reached the edge of the window.
- **Correlation band:** 120 Hz to 6 kHz. Raise the lower edge to reduce heavy
  rumble or lower the upper edge for very muffled recordings.

The probe table is important when the recordings are not a simple alignment:

- A consistent offset is a usable alignment.
- A steady change in offset indicates clock drift. Padding fixes the start,
  but the recordings may still separate later.
- Scattered measurements can indicate a dropout, edit, or other discontinuity.
- If there is no reliable overlap, TrackSync refuses to present a misleading
  alignment.

## Export behavior

Export prepends digital silence to the track that started later. The other
track is copied without trimming or resampling. The source sample rate and
channel count are preserved where the selected output format allows it.

By default, only the track that needs padding is selected. Select both tracks
when you want a complete exported pair. The export dialog also lets you
choose the output container and bit depth and optionally create
`tracksync_report.json`.

The default output format is WAV. FLAC and AIFF are also available. Compressed
sources are decoded and re-encoded because silence cannot be prepended to a
compressed stream in place. Use the exported files together in your DAW and
place them at the same start time.

## Supported input formats

The exact list is available at **Help > Supported formats** and depends on the
installed backends. The normal installation supports formats handled by
libsndfile, including:

- WAV, AIFF, FLAC, CAF, W64, RF64
- OGG, OGA, Opus, MP3
- AU, VOC, MAT, SD2, IFF, and SVX where supported by libsndfile

With `av` installed, TrackSync can also read formats such as M4A, AAC, WMA,
audio in MP4/MOV, and additional FFmpeg-supported containers. TrackSync reads
the first audio stream it finds in a container.

## Command-line use

The main script can open the GUI, analyze two files, or run a build self-test:

```sh
python TrackSync.py
python TrackSync.py A.wav B.wav
python TrackSync.py --selftest
```

The two-file form prints the formatted analysis report and does not export
audio. The self-test checks required imports, creates a synthetic pair with a
known offset, analyzes it, and verifies an export.

The engine can also be used without Qt:

```sh
python -m tracksync.engine A.wav B.wav
python -m tracksync.engine A.wav B.wav output_folder
```

## Build distributable versions

### Windows

```bat
build.bat
```

The result is `dist\\TrackSync\\TrackSync.exe`. To create a Windows installer,
run:

```bat
make_installer.bat
```

The installer script uses Inno Setup when available and has a scripted ZIP
fallback. The build scripts require Python and install their build-time
dependencies into an isolated environment.

### macOS

Build macOS packages on a Mac; PyInstaller does not cross-compile:

```sh
./build.sh
./make_dmg.sh
```

These produce `dist/TrackSync.app` and a DMG respectively. An ad-hoc-signed
build may require opening the app from Finder once and confirming the macOS
security prompt.

## Run tests

The test scripts run the engine and application tests:

```bat
run_tests.bat
```

```sh
./run_tests.sh
```

The test dependencies are installed by the development setup. The frozen
Windows build can also be checked with:

```bat
TrackSync.exe --selftest
```

## Project layout

```text
TrackSync.py              GUI, CLI, and frozen-build entry point
tracksync/audiofile.py    Audio readers and format detection
tracksync/engine.py       Analysis, reporting, and export
tracksync/player.py       Two-track audio monitor
tracksync/ui.py           Qt application window and dialogs
tracksync/waveform.py     Timeline waveform generation and display
tests/                    Engine and application tests
tools/                    Version and icon utilities
assets/                   Application icons and images
*.spec                    PyInstaller build specifications
installer/                Windows installer files
```

The analysis engine does not depend on Qt, so it can be used from scripts or
other front ends.
