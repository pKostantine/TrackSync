# TrackSync

Aligns two recordings of the same event that were made in different places,
started at different times, and are different lengths — and tells you the
numbers while it does it.

Resolve and Premiere can do this too, but when their auto-sync fails you get
no diagnostic. TrackSync reports the offset, the clock drift, and a table of
independent per-probe measurements, so a failure tells you *why* it failed.

---

## Build it — Windows

Double-click **`build.bat`** once. It makes its own build environment,
installs everything, verifies the result, and produces:

```
dist\TrackSync\TrackSync.exe
```

The first build takes a few minutes; later ones are quicker. The only
prerequisite is Python 3.10+ on PATH, used for the build only. If you'd
rather not build, `run_dev.bat` runs it straight from source.

### Give it to someone else

Then run **`make_installer.bat`**, which produces one file:

```
dist\TrackSync-Setup-1.2.0.exe
```

That's an ordinary Windows installer: double-click, press Install, done.
It puts TrackSync in the Start Menu, optionally on the Desktop, and in Add
or Remove Programs, with a working uninstaller. It installs under your own
profile, so it never asks for an administrator password. Whoever you send it
to needs nothing installed first — no Python, no ffmpeg, no codecs.

The compiler behind it is [Inno Setup](https://jrsoftware.org/isdl.php),
which is free. If it isn't on the machine, `make_installer.bat` offers to
fetch it (via winget, or straight from jrsoftware.org) and then carries on.
If you decline or it can't reach the network, the script falls back to
`dist\TrackSync-Installer.zip` — the same program, installed by a script
instead of a wizard — so you're never left with nothing.

The build also produces a second executable, `TrackSync-check.exe`. It is
the same program with a console attached, and it exists so `--selftest` can
actually report: a windowed Windows program has no stdout at all, so any
verification run against `TrackSync.exe` passes no matter what is wrong
with it. Nobody needs to run it by hand — `build.bat` and the installer do.

---

## Build it — macOS

An app bundle has to be built on a Mac; PyInstaller cannot cross-compile, so
there is no way to produce one from Windows. On the Mac, in Terminal:

```
cd /path/to/TrackSync
./make_dmg.sh
```

That builds the app from source and packages it, giving you one file:

```
dist/TrackSync-1.2.0.dmg
```

Open it and drag TrackSync onto the Applications shortcut — the usual Mac
install. `./build.sh` on its own stops at `dist/TrackSync.app` if you just
want to run it locally, and `./run_dev.sh` runs it from source without
building anything.

The only prerequisite is Python 3.9+. macOS ships one with the Xcode Command
Line Tools — if `python3` isn't found, the system offers to install them, or
`brew install python` works.

**The first launch needs one extra click.** The app is ad-hoc signed, not
signed with a paid Apple Developer certificate, so macOS says the developer
cannot be verified. Right-click the app, choose Open, then Open again. After
that it behaves like any other app. The DMG carries a note saying so, for
whoever you send it to.

There is no `TrackSync-check` twin on macOS: a windowed Mac binary keeps a
working stdout when launched from a terminal, so `--selftest` reports
properly from inside the bundle. That is a Windows-only limitation.

**On the Mac the shortcuts are Command, not Control** — ⌘+ / ⌘− / ⌘0 for
interface scale. Qt swaps the two modifiers on macOS, so the same code gives
the right key on each platform, and the interface font starts at 13 pt there
rather than 9 pt so "100%" means the same thing on both.

---

## Use it

1. Drop the two recordings onto the **A** and **B** slots (or browse).
   Order doesn't matter — TrackSync works out which one started first.
   Reads WAV, FLAC, AIFF, CAF, W64, OGG, **MP3** and **M4A/AAC**, plus the
   audio inside MP4 and MOV. **Help → Supported formats** lists them all.
2. **Analyse sync.** Nothing is written to disk during analysis.
3. Read the result. The headline is the offset; underneath it says which
   track gets the silence and how much.
4. **Play** to hear them overlapped. Independent faders, mute and solo per
   track. Sync errors are far more obvious by ear than by eye — a few
   milliseconds out sounds hollow and flanged; correct sounds like one mic.
5. Scroll on the timeline to zoom. The readout in the corner tells you how
   much time a pixel is worth; at full zoom it's microseconds, and you can
   see the alignment directly.
6. **Export aligned pair…** — a dialog asks which tracks to write, in what
   format, and whether to save the JSON report.

**Ctrl +** and **Ctrl −** scale the whole interface; **Ctrl 0** resets it
(also on the **View** menu). The size is remembered between launches. The
Tracks header and the Monitor bar stay pinned; the timeline, measurement and
probes scroll between them, so nothing gets half cut off at 175% or 250%.

The waveform is drawn from a whole-track envelope built in one pass when the
analysis finishes. After that, zooming and panning are resampled out of
memory rather than re-read from the files — on a 40-minute pair the first
pass takes about a second and every zoom afterwards is under a millisecond.
Zoom past the cache's resolution (roughly a 17-second view) and the exact
samples are read from disk, which by then is a tiny slice of the file. The
rendered lanes are also cached as an image, so the moving playhead costs
under a millisecond a frame instead of redrawing both waveforms 30 times a
second.

### What export writes

Padding only. The track that started later gets digital silence prepended;
the other is copied sample for sample. Neither is resampled, trimmed, or
folded down — both keep their original sample rate and channel count.

```
cam_a_synced.wav        the early track, unchanged
recorder_b_synced.wav   2,731.489 ms of silence + the original
tracksync_report.json   every number from the analysis
```

Drop both on your DAW timeline at 00:00:00. No nudging.

Accuracy is the rounding of the pad to a whole sample: **±0.01 ms at 48 kHz**,
about a hundredth of the millisecond you asked for.

**Choices in the export dialog**

- **Which tracks.** Only the one that changes, by default. The other is a
  byte-for-byte copy of a file already on your disk, and for a three-hour
  take that's gigabytes written for nothing. The padding is computed from
  the pair either way, so a lone exported file still lands correctly against
  its partner. Tick both if you want them side by side.
- **Container.** WAV by default, whatever came in — it's what every DAW
  opens without argument. FLAC and AIFF are there if you'd rather.
- **Bit depth.** "Same as source" keeps the incoming depth, which is what
  makes a WAV-to-WAV export bit-identical; the dialog says, per track,
  whether the copy will be bit-identical or re-encoded.
- **The JSON report.** Off by default; tick it when you want the numbers
  kept alongside the audio.

A compressed source (MP3, M4A) can't be padded in place — you can't prepend
silence to an AAC stream without re-encoding it — so export decodes and
writes uncompressed. The offset is measured on that same decoded audio, so
the pair still lines up exactly. Use the exported files rather than the
originals: some codecs carry a few milliseconds of encoder priming, and
different decoders handle it differently.

---

## Reading the probe table

This is the part worth understanding, because it's what a black-box syncer
won't give you.

A probe is an independent measurement of the offset, taken from a ten-second
window at one point in the take. Thirteen of them are spread across the
overlap. If they all agree, the answer is one number and you're done. If they
disagree, *how* they disagree names the problem.

**Confidence** is normalised so that **1.0 means "no better than random"** —
measured, not assumed: unrelated audio scores 0.68–0.84 whether the search
covers two thousand lags or two hundred thousand. The raw peak-to-noise
ratio has no such property, because the largest of *N* noise samples grows
like √(2 ln N) on its own — searching eight million lags gives about 5.2 on
pure noise, so any fixed threshold quietly stops meaning anything as files
get longer. Dividing by that expected maximum is what pins the floor.

Above **2.0** a probe is used. Between 2.0 and 3.0 it's used *and flagged*,
because that band is genuinely ambiguous: a heavily reverberant measurement
that is right and one that is 3.7 ms wrong both land there, and no threshold
separates them — the thing that breaks GCC-PHAT in a live room is a strong
early reflection beating the direct sound, and that produces an honestly
sharp peak. Raising the bar until the wrong ones were excluded also excluded
correct measurements accurate to five microseconds. So the ambiguity is
reported instead: *probably right, check this one by ear.*

One caveat on the coarse number specifically: it is the best of dozens of
candidate blocks, and the maximum of many trials is inflated even when every
trial is noise — the same effect that makes a wide lag search inflate
peak/rms. So the coarse score is a search statistic, not a verdict. **The
probes decide.** If not one independent probe can confirm the candidate at
the native rate, TrackSync reports no alignment however well the winning
block happened to score.

### The three failure modes

**No probe can confirm the candidate.**
The files don't overlap, or one is too reverberant or too noisy to correlate.
No tool would sync these. Verdict: *no reliable alignment found.*

**Good probes, but the offset column trends.**
Clock drift — the two recorders' crystals aren't running at the same rate.
This is the case Resolve silently fails at: it locks one offset, and
everything past a few minutes goes soft. TrackSync reports it in ppm and in
milliseconds per hour. Padding fixes the start; the ends still separate.
40 ppm is 144 ms over an hour, which is very audible.

**Probes confident but scattered with no trend.**
A dropout or an edit in one file. There is no single offset that works, and
anything that reports one is lying to you. The probe table shows *where* the
discontinuity is — split the file there and sync the halves separately.
TrackSync flags this as `scattered` and asks before exporting anyway.

The **Δ fit** column is each probe's distance from the best-fit line. Small
and random is healthy. One large value is a bad probe. A step change is an
edit.

---

## How it works

Two stages, in `tracksync/engine.py`.

**Coarse.** Both files are streamed down to 1.5–8 kHz mono (the rate is
chosen so a file of any length fits a fixed memory budget). Then a short,
high-energy **anchor** is taken out of the shorter file and slid across the
**entire** longer one in overlapping blocks — blocks overlap by a full anchor
length, so no possible alignment can fall through a seam. The best-scoring
block wins.

That scan is the reason a twenty-minute take that lines up two and a half
hours into a three-hour recording is found just as readily as one that lines
up in the first minute. Correlating two such files end to end in one FFT is
enormous, and truncating the long one to make it affordable means anything
late is never found at all. If the first anchor finds nothing convincing,
another is taken from a different part of the file and the scan repeats — an
anchor can land on a passage that simply isn't shared (a pause, a lens cap,
someone talking only into one mic), and the answer to that is to look
somewhere else rather than to declare the recordings unrelated.

Each block is scored with GCC-PHAT. Whitening the cross-spectrum by its own magnitude throws away
amplitude and keeps only phase — which is exactly right here, because two
mics in different positions colour the sound completely differently (distance,
reverb, comb filtering) but the phase relationship of the direct sound
survives. Plain cross-correlation gives a broad ambiguous peak on this
material; PHAT gives a sharp one.

The correlation is band-limited to 120 Hz – 6 kHz with a raised-cosine taper.
Below 120 Hz you're correlating HVAC rumble and handling noise, which two
recorders in different rooms don't share; above 6 kHz the mics diverge and
the bins are mostly noise, which PHAT would otherwise weight just as heavily
as good ones. This costs nothing, because the estimator is already in the
frequency domain — which is also why there's no scipy dependency.

**Fine.** Thirteen ten-second windows are read at the native rate and refined
locally within ±250 ms of the coarse answer, with parabolic interpolation on
the correlation peak for sub-sample resolution. Fitting a line through those
thirteen measurements gives the offset *and* the drift; one round of
MAD-based outlier rejection keeps a single bad probe from tilting it. The
reported offset is taken at the middle of the measured span rather than
extrapolated back to zero, because extrapolating a drift line amplifies fit
error.

`analyze()` writes nothing and `export_synced()` does no measuring. Keeping
them apart is what lets the UI show you the numbers before committing to a
render — and it means the engine is usable on its own:

```
python -m tracksync.engine A.wav B.wav
python -m tracksync.engine A.wav B.wav out_folder    # also exports
```

Nothing ever loads a whole file. A pair of hour-long 24-bit stereo takes
costs a few megabytes of RAM, not a few gigabytes.

**Reading.** `audiofile.py` puts one interface over two backends. libsndfile
handles WAV, FLAC, AIFF, CAF, W64, OGG and MP3 with exact sample-accurate
seeking. Everything it can't open — M4A/AAC/ALAC, WMA, audio in MP4 and MOV —
goes to PyAV, which ships ffmpeg's decoders in its wheel, so nothing has to
be installed separately. A compressed stream has no sample index, so seeking
means landing on a keyframe before the target and decoding forward to it;
positions come from each decoded frame's own timestamp rather than from
counting samples since the seek, because where a seek actually lands is not
knowable in advance. Get that wrong and every probe believes it is somewhere
it isn't, which would quietly poison the drift fit.

---

## Is it actually accurate?

`run_tests.bat` builds synthetic pairs with **known** offsets — different
reverb tails, different spectral tilt, 14 dB of level difference, different
noise floors, different lengths — and checks the recovered number against
ground truth. Current results:

| case | error |
|---|---|
| clean pair, different rooms | 0.005 ms |
| reversed (B first) | 0.002 ms |
| deliberately fractional offset | 0.004 ms |
| 44.1 kHz against 48 kHz | 0.006 ms |
| 42 ppm clock drift over 10 min | 0.054 ms, drift recovered to 0.03 ppm |
| 6-min take aligned 52 min into a 70-min file | 0.000 ms |
| alignment in the last 10% of a long file | 0.000 ms |
| MP3 pair | 0.002 ms |
| M4A/AAC pair, exported and re-analysed | 0.002 ms |

Unrelated files are correctly refused rather than given a confident wrong
answer, and a spliced 400 ms dropout is diagnosed as `scattered` rather than
averaged into a plausible-looking lie.

### Where it stops working

Pushing the second recorder until it breaks, holding the first one fixed:

| track B | error | reported as |
|---|---|---|
| dry | 0.000 ms | ok |
| normal room | 0.001 ms | ok |
| live room, quiet distant mic | 0.001 ms | ok |
| 0 dB SNR (noise as loud as signal) | 0.001 ms | ok |
| drowned in a 1.8 s room | 3.7 ms | **flagged** — 1/9 probes usable |
| 0 dB SNR *and* a 2 s room | 125 ms | **flagged** — 0/9 probes usable |

Noise it shrugs off; reverberation is what actually kills it, because
reflections drag every probe the same way and the agreement between them
looks like health. The guarantee is therefore not "always right" — no
estimator manages that on a mic drowned in a room — but **never silently
wrong**: every answer is either within a millisecond or visibly flagged.
That invariant is a test, so it stays true.

Export is verified end to end: the unpadded track comes out bit-identical,
the padded one is exactly silence + original, and re-analysing the exported
pair reads back 0.004 ms.

Export is checked the same way, and 40 further tests cover the window
itself — analysis on the worker thread, the probe table, zoom, faders,
mute/solo, streaming playback with no underruns, and the export round trip.
`TrackSync.exe --selftest` runs an abbreviated version inside the frozen
build, which is how a missing DLL or a mis-packaged Qt shows up as a clear
message instead of a window that won't open.

---

## Settings

Under **Settings…**, if the defaults don't fit:

- **Probe window** (10 s) — longer is more robust in noise, slower, and
  blurs drift measurement.
- **Number of probes** (13) — more gives a better drift fit on long takes.
- **Refinement window** (±250 ms) — raise it if the report warns that probes
  hit its edge, which means the true offset moves further than the window
  allows (a big splice, or extreme drift).
- **Correlation band** (120 Hz – 6 kHz) — raise the low edge for takes with
  heavy rumble; lower the top edge for very distant or muffled mics.

---

## Troubleshooting

**"No audio output available."** The monitor needs a working output device.
Analysis and export still work without one.

**Windows SmartScreen warns on first launch.** The build isn't code-signed.
"More info" → "Run anyway".

**Antivirus quarantines the exe.** PyInstaller output gets flagged sometimes.
Add an exclusion for the folder, or run from source with `run_dev.bat`.

**"Could not find platform independent libraries \<prefix\>" during the build.**
Something in your environment sets `PYTHONHOME` or `PYTHONPATH` to a Python
that isn't the one being used. The build scripts now clear both for their own
scope and print a note when they do, so this is harmless — but it is worth
fixing at the source, because it can otherwise make PyInstaller freeze
against a different `python313.dll` than the virtual environment was built
on. Check with `echo %PYTHONHOME%` and clear it in System Environment
Variables if it points somewhere stale.

**Analysis is slow on very long files.** Time is dominated by the probe
windows, not the file length. Lower the probe count for a quick look.

---

## Layout

```
TrackSync.py            entry point (also --selftest and a CLI)
tools/
  version.py            single source of truth for the version number
  make_icons.py         .ico, .icns, tick and logo, all from logo.png
tracksync/
  audiofile.py          one reader over libsndfile and PyAV
  engine.py             GCC-PHAT, analyze(), export_synced()  -- no Qt
  player.py             streaming two-track monitor
  waveform.py           peak envelopes + the zoomable view
  ui.py                 the window
tests/
  test_engine.py        ground-truth accuracy checks
  test_app.py           headless UI and player checks
installer/              Inno Setup script, plus the scripted-install fallback
assets/                 logo and icon
TrackSync.spec          PyInstaller spec, Windows
TrackSync-mac.spec      PyInstaller spec, macOS app bundle
build.bat / build.sh            build for Windows / macOS
make_installer.bat              Windows setup.exe
make_dmg.sh                     macOS disk image
run_dev.bat / run_dev.sh        run from source
run_tests.bat / run_tests.sh    run both test suites
```

`engine.py` has no Qt import and no UI assumptions, so it drops into a
script, a batch job, or a different front end unchanged.
#   T r a c k S y n c  
 