"""Entry point for the frozen Windows build and for `python TrackSync.py`.

    TrackSync.exe                 open the window
    TrackSync.exe --selftest      prove the build works, print, exit
    TrackSync.exe A.wav B.wav     analyse a pair and print the report
"""

import multiprocessing
import sys


def selftest():
    """Analyse a synthetic pair with a known offset and check the answer.

    Worth having in the shipped build: it exercises every native dependency
    (libsndfile, soxr, PortAudio, Qt) and the estimator itself, so a broken
    freeze or a missing DLL shows up as a clear failure instead of a window
    that will not open.
    """
    import os
    import tempfile

    import numpy as np

    print("TrackSync self-test")
    print("-" * 46)

    ok = True
    for name in ("numpy", "soundfile", "soxr", "PySide6.QtWidgets"):
        try:
            __import__(name)
            print(f"  {name:<22} ok")
        except Exception as exc:
            ok = False
            print(f"  {name:<22} FAILED  {exc}")

    # PyAV is what makes M4A/AAC work. Its absence is survivable -- every
    # other format still opens -- so it is reported, not fatal.
    from tracksync import audiofile
    if audiofile.HAVE_AV:
        import av
        print(f"  {'av (M4A/AAC)':<22} ok  (PyAV {av.__version__})")
    else:
        print(f"  {'av (M4A/AAC)':<22} MISSING - M4A files will not open")
    print(f"  {'formats':<22} "
          f"{len(audiofile.supported_extensions())} extensions")

    try:
        import sounddevice as sd
        n = len(sd.query_devices())
        print(f"  {'audio output':<22} ok  ({n} devices)")
    except Exception as exc:
        print(f"  {'audio output':<22} unavailable  ({exc})")
        print("      (analysis and export still work without one)")

    if not ok:
        print("\nA required component is missing. The build is incomplete.")
        return 1

    import soundfile as sf
    from tracksync import engine

    fs = 48000
    true_offset = 1.2345
    rng = np.random.default_rng(7)
    src = rng.standard_normal(int(40 * fs)).astype(np.float64)
    # crude "room": a couple of delayed reflections, different per recorder
    def room(x, taps):
        y = x.copy()
        for d, g in taps:
            y[d:] += g * x[:len(x) - d]
        return y / (np.abs(y).max() + 1e-9)

    a = room(src[:int(30 * fs)], [(1301, .4), (2917, .25)])
    off = int(true_offset * fs)
    b = room(src[off:off + int(30 * fs)], [(701, .55), (4001, .35)]) * 0.3

    with tempfile.TemporaryDirectory() as tmp:
        pa = os.path.join(tmp, "a.wav")
        pb = os.path.join(tmp, "b.wav")
        sf.write(pa, a, fs, subtype="PCM_24")
        sf.write(pb, b, fs, subtype="PCM_24")

        rep = engine.analyze(pa, pb, n_probes=5, probe_seconds=6.0)
        err_ms = (rep.offset_seconds - true_offset) * 1000

        print("-" * 46)
        print(f"  measured offset        {rep.offset_seconds * 1000:+.3f} ms")
        print(f"  true offset            {true_offset * 1000:+.3f} ms")
        print(f"  error                  {err_ms:+.4f} ms")
        print(f"  confidence             {rep.coarse_confidence:.2f}")
        print(f"  good probes            {rep.n_good_probes}/{len(rep.probes)}")

        res = engine.export_synced(rep, os.path.join(tmp, "out"))
        wrote = len(res["outputs"])
        print(f"  export                 wrote {wrote} files")

    good = abs(err_ms) < 1.0 and wrote == 2
    print("-" * 46)
    print("PASS — this build is working." if good
          else "FAIL — the estimator did not recover the known offset.")
    return 0 if good else 1


def cli(a_path, b_path):
    from tracksync import engine
    print(engine.format_report(engine.analyze(a_path, b_path)))
    return 0


def _ensure_streams():
    """A GUI-subsystem build has no stdout; writing to None raises.

    The window never prints, but library code sometimes does, and a stray
    warning should not take the application down.
    """
    import io
    for name in ("stdout", "stderr"):
        if getattr(sys, name, None) is None:
            setattr(sys, name, io.StringIO())


def run():
    multiprocessing.freeze_support()
    _ensure_streams()
    args = [a for a in sys.argv[1:] if a]
    if args and args[0] in ("--selftest", "/selftest", "-t"):
        return selftest()
    if len(args) >= 2 and not args[0].startswith("-"):
        return cli(args[0], args[1])
    from tracksync.ui import main
    return main()


if __name__ == "__main__":
    sys.exit(run())
