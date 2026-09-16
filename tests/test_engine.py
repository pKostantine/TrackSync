"""
Ground-truth validation for the sync engine.

Every case here builds two synthetic "recordings" of one synthetic event,
with a KNOWN offset, so the recovered number can be checked rather than
eyeballed. The two recordings deliberately differ the way two real recorders
in different places differ: different reverb tail, different noise floor,
different spectral tilt, different level, different length.

Run:  python -m tests.test_engine
"""

from __future__ import annotations

import os
import sys
import tempfile

import numpy as np
import soundfile as sf

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tracksync import engine  # noqa: E402

RNG = np.random.default_rng(20260903)
TOL_MS = 1.0                      # the spec: accurate to a millisecond


# --------------------------------------------------------------------------
# synthetic world
# --------------------------------------------------------------------------

def make_source(duration, fs):
    """Speech-ish: voiced bursts with harmonics, plus transients, plus gaps."""
    n = int(duration * fs)
    t = np.arange(n) / fs
    x = np.zeros(n, np.float64)

    pos = 0.0
    while pos < duration - 0.5:
        seg = RNG.uniform(0.25, 0.9)
        i0, i1 = int(pos * fs), int(min(duration, pos + seg) * fs)
        m = i1 - i0
        if m > 8:
            f0 = RNG.uniform(85, 240)
            tt = np.arange(m) / fs
            v = np.zeros(m)
            for h in range(1, 14):
                v += (1.0 / h) * np.sin(2 * np.pi * f0 * h * tt
                                        + RNG.uniform(0, 2 * np.pi))
            env = np.hanning(m)
            v *= env
            v += 0.35 * RNG.standard_normal(m) * env      # fricative noise
            x[i0:i1] += v * RNG.uniform(0.4, 1.0)
        pos += seg + RNG.uniform(0.05, 0.45)

    # a few sharp transients (door, clap) -- these carry the alignment
    for _ in range(int(duration / 12) + 3):
        i = RNG.integers(0, max(1, n - 2000))
        k = np.exp(-np.arange(1500) / 90.0) * RNG.standard_normal(1500)
        x[i:i + 1500] += 3.0 * k

    return x / (np.max(np.abs(x)) + 1e-9)


def room(x, fs, rt60, mix, seed):
    """Cheap exponential-decay reverb, different per 'microphone position'."""
    rng = np.random.default_rng(seed)
    n = int(rt60 * fs)
    ir = rng.standard_normal(n) * np.exp(-6.9 * np.arange(n) / n)
    ir[0] = 1.0
    nfft = 1 << (len(x) + n - 1).bit_length()
    y = np.fft.irfft(np.fft.rfft(x, nfft) * np.fft.rfft(ir, nfft),
                     nfft)[:len(x)]
    y /= np.max(np.abs(y)) + 1e-9
    return (1 - mix) * x + mix * y


def tilt(x, fs, slope_db_per_oct, seed=0):
    """Different mic/distance -> different spectral tilt."""
    nfft = 1 << (len(x) - 1).bit_length()
    X = np.fft.rfft(x, nfft)
    f = np.fft.rfftfreq(nfft, 1 / fs)
    oct_from_1k = np.log2(np.maximum(f, 20.0) / 1000.0)
    X *= 10 ** (slope_db_per_oct * oct_from_1k / 20.0)
    return np.fft.irfft(X, nfft)[:len(x)]


def record(source, fs, start_s, dur_s, *, rt60, mix, slope, snr_db,
           gain, drift_ppm=0.0, seed=1):
    """Cut a recording out of the absolute timeline.

    `start_s` is when this recorder was switched on. `drift_ppm` makes its
    clock run fast/slow, exactly like a real recorder's crystal.
    """
    n = int(dur_s * fs)
    idx = np.arange(n) * (1.0 + drift_ppm * 1e-6) + start_s * fs
    seg = np.interp(idx, np.arange(len(source)), source, left=0.0, right=0.0)

    seg = room(seg, fs, rt60, mix, seed)
    seg = tilt(seg, fs, slope)
    rms = np.sqrt(np.mean(seg ** 2)) + 1e-12
    noise = np.random.default_rng(seed + 77).standard_normal(n)
    seg = seg + noise * rms * 10 ** (-snr_db / 20.0)
    return (seg * gain).astype(np.float64)


def write(path, x, fs, channels=1, subtype="PCM_24"):
    if channels > 1:
        x = np.stack([x] + [x * 0.92] * (channels - 1), axis=1)
    sf.write(path, x, fs, subtype=subtype)
    return path


# --------------------------------------------------------------------------
# harness
# --------------------------------------------------------------------------

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}"
          + (f"   [{detail}]" if detail else ""))
    return ok


def case_basic(tmp):
    """A starts 3.4127 s before B. Different rooms, 14 dB SNR gap."""
    fs = 48000
    src = make_source(200.0, fs)
    true_off = 3.4127                      # B switched on this much later

    a = record(src, fs, 0.0, 150.0, rt60=0.35, mix=0.25, slope=-1.0,
               snr_db=34, gain=0.7, seed=3)
    b = record(src, fs, true_off, 165.0, rt60=1.10, mix=0.62, slope=+2.5,
               snr_db=20, gain=0.25, seed=9)

    pa = write(os.path.join(tmp, "A.wav"), a, fs, channels=2)
    pb = write(os.path.join(tmp, "B.wav"), b, fs, channels=1)

    r = engine.analyze(pa, pb)
    err_ms = (r.offset_seconds - true_off) * 1000.0
    print(engine.format_report(r))
    print()
    check("basic: offset within 1 ms", abs(err_ms) < TOL_MS,
          f"error {err_ms:+.3f} ms")
    check("basic: late track is B", r.late_track == "B", r.late_track)
    check("basic: status ok", r.status == "ok", r.status)
    check("basic: no phantom drift", abs(r.drift_ppm) < 2.0,
          f"{r.drift_ppm:+.2f} ppm")
    return r, pa, pb, fs, true_off


def case_reversed(tmp):
    """B starts first -> offset must go negative and A must be the late one."""
    fs = 48000
    src = make_source(120.0, fs)
    true_off = -7.8091                     # A switched on 7.8 s after B

    a = record(src, fs, -true_off, 100.0, rt60=0.5, mix=0.4, slope=+1.0,
               snr_db=26, gain=0.5, seed=11)
    b = record(src, fs, 0.0, 110.0, rt60=0.25, mix=0.2, slope=-2.0,
               snr_db=30, gain=0.9, seed=17)

    pa = write(os.path.join(tmp, "rA.wav"), a, fs)
    pb = write(os.path.join(tmp, "rB.wav"), b, fs)
    r = engine.analyze(pa, pb)
    err_ms = (r.offset_seconds - true_off) * 1000.0
    check("reversed: offset within 1 ms", abs(err_ms) < TOL_MS,
          f"error {err_ms:+.3f} ms")
    check("reversed: late track is A", r.late_track == "A", r.late_track)


def case_subsample(tmp):
    """Offset deliberately not a whole number of samples."""
    fs = 48000
    src = make_source(90.0, fs)
    true_off = 1.0 + 17.4 / fs             # 1 s + 17.4 samples
    a = record(src, fs, 0.0, 80.0, rt60=0.3, mix=0.3, slope=0.0,
               snr_db=40, gain=0.8, seed=23)
    b = record(src, fs, true_off, 80.0, rt60=0.45, mix=0.35, slope=+1.5,
               snr_db=34, gain=0.6, seed=29)
    pa = write(os.path.join(tmp, "sA.wav"), a, fs)
    pb = write(os.path.join(tmp, "sB.wav"), b, fs)
    r = engine.analyze(pa, pb)
    err_us = (r.offset_seconds - true_off) * 1e6
    check("sub-sample: within 100 us", abs(err_us) < 100.0,
          f"error {err_us:+.1f} us")


def case_drift(tmp):
    """Known clock drift. The fit must recover the ppm and the mid-offset."""
    fs = 48000
    src = make_source(700.0, fs)
    true_off = 12.0
    true_ppm = 42.0                        # B's clock runs fast

    a = record(src, fs, 0.0, 620.0, rt60=0.4, mix=0.3, slope=-1.0,
               snr_db=32, gain=0.7, seed=31)
    b = record(src, fs, true_off, 620.0, rt60=0.8, mix=0.5, slope=+2.0,
               snr_db=24, gain=0.4, drift_ppm=true_ppm, seed=37)

    pa = write(os.path.join(tmp, "dA.wav"), a, fs)
    pb = write(os.path.join(tmp, "dB.wav"), b, fs)
    r = engine.analyze(pa, pb, n_probes=15)

    # B's clock running fast by p ppm means B consumes source faster, so the
    # A-minus-B offset grows by p samples per sample of B.
    ppm_err = r.drift_ppm - true_ppm
    check("drift: ppm recovered", abs(ppm_err) < 4.0,
          f"got {r.drift_ppm:+.2f}, true {true_ppm:+.1f}")
    check("drift: reported in verdict", "drift" in r.verdict.lower(),
          r.verdict[:60])
    # offset is reported at the middle of the measured span; check it against
    # the true offset evaluated at that same instant
    t_mid = float(np.median([p.t_seconds for p in r.probes if p.used]))
    true_at_mid = true_off + true_ppm * 1e-6 * t_mid
    err_ms = (r.offset_seconds - true_at_mid) * 1000.0
    check("drift: mid-span offset within 1 ms", abs(err_ms) < TOL_MS,
          f"error {err_ms:+.3f} ms")


def case_mixed_rates(tmp):
    """44.1 kHz against 48 kHz."""
    src48 = make_source(120.0, 48000)
    true_off = 5.25
    a = record(src48, 48000, 0.0, 100.0, rt60=0.3, mix=0.3, slope=0.0,
               snr_db=32, gain=0.8, seed=41)
    src441 = np.interp(np.arange(int(120 * 44100)) * (48000 / 44100),
                       np.arange(len(src48)), src48)
    b = record(src441, 44100, true_off, 100.0, rt60=0.7, mix=0.5, slope=+2.0,
               snr_db=26, gain=0.5, seed=43)
    pa = write(os.path.join(tmp, "mA.wav"), a, 48000, subtype="PCM_16")
    pb = write(os.path.join(tmp, "mB.wav"), b, 44100, subtype="PCM_24")
    r = engine.analyze(pa, pb)
    err_ms = (r.offset_seconds - true_off) * 1000.0
    check("mixed rates: offset within 1 ms", abs(err_ms) < TOL_MS,
          f"error {err_ms:+.3f} ms")
    check("mixed rates: analysis at 48 k", r.analysis_rate == 48000,
          str(r.analysis_rate))


def case_no_overlap(tmp):
    """Two unrelated recordings. Must refuse, not invent an answer."""
    fs = 48000
    a = record(make_source(60.0, fs), fs, 0.0, 55.0, rt60=0.4, mix=0.3,
               slope=0.0, snr_db=30, gain=0.7, seed=51)
    b = record(make_source(60.0, fs), fs, 0.0, 55.0, rt60=0.9, mix=0.6,
               slope=+2.0, snr_db=22, gain=0.4, seed=61)
    pa = write(os.path.join(tmp, "nA.wav"), a, fs)
    pb = write(os.path.join(tmp, "nB.wav"), b, fs)
    r = engine.analyze(pa, pb)
    check("no overlap: refuses", r.status == "no_overlap",
          f"status={r.status} conf={r.coarse_confidence:.2f} "
          f"good={r.n_good_probes}")


def case_edit(tmp):
    """A dropout in one file: probes confident but the offset jumps."""
    fs = 48000
    src = make_source(300.0, fs)
    a = record(src, fs, 0.0, 260.0, rt60=0.35, mix=0.3, slope=0.0,
               snr_db=34, gain=0.8, seed=71)
    b = record(src, fs, 2.0, 260.0, rt60=0.6, mix=0.45, slope=+1.5,
               snr_db=28, gain=0.5, seed=73)
    # splice 400 ms out of the middle of B, as a dropped buffer would
    cut = int(130 * fs)
    b = np.concatenate((b[:cut], b[cut + int(0.4 * fs):]))
    pa = write(os.path.join(tmp, "eA.wav"), a, fs)
    pb = write(os.path.join(tmp, "eB.wav"), b, fs)
    r = engine.analyze(pa, pb, n_probes=15)
    jump = max(p.offset_ms for p in r.probes) - min(p.offset_ms
                                                    for p in r.probes)
    check("edit: discontinuity visible in probe table", jump > 100.0,
          f"spread {jump:.0f} ms")
    check("edit: diagnosed as scattered, not silently averaged",
          r.status == "scattered", r.status)
    # the 400 ms splice is bigger than the +/-250 ms refinement window, so the
    # engine must say so rather than reporting clamped numbers as fact
    check("edit: warns that probes hit the search-window edge",
          any("edge of the" in n for n in r.notes),
          "; ".join(r.notes)[:80])


def case_never_silently_wrong(tmp):
    """The invariant that matters: never confidently wrong.

    Heavy reverberation is the one condition that breaks GCC-PHAT quietly.
    It pulls the correlation peak off the direct sound and pulls EVERY probe
    the same way, so the probes agree with each other and the scatter looks
    healthy while the answer is milliseconds out. Measured cliff: probes at
    confidence >= 2.0 land within 0.003 ms; probes just below it are wrong
    by 3.7 ms.

    So the requirement is not "always right" -- no estimator manages that on
    a mic drowned in a room. It is: every answer is either within a
    millisecond, or visibly flagged.
    """
    fs = 48000
    src = make_source(90.0, fs)
    true_off = 4.321
    conditions = [
        ("dry",              40, 0.15, 0.25, 0.80),
        ("normal room",      28, 0.35, 0.50, 0.60),
        ("live room",        18, 0.60, 1.00, 0.25),
        ("drowned in room",  14, 0.80, 1.80, 0.20),
        ("0 dB SNR",          0, 0.45, 0.70, 0.35),
        ("0 dB + heavy verb", 0, 0.85, 2.00, 0.15),
    ]
    silent = []
    accurate = 0
    for label, snr, mix, rt60, gain in conditions:
        a = record(src, fs, 0.0, 70.0, rt60=0.3, mix=0.25, slope=-1.0,
                   snr_db=34, gain=0.8, seed=5)
        b = record(src, fs, true_off, 70.0, rt60=rt60, mix=mix, slope=+2.0,
                   snr_db=snr, gain=gain, seed=13)
        pa = write(os.path.join(tmp, "rb_a.wav"), a, fs)
        pb = write(os.path.join(tmp, "rb_b.wav"), b, fs)
        r = engine.analyze(pa, pb, n_probes=9)
        err = abs(r.offset_seconds - true_off) * 1000
        flagged = (r.status != "ok"
                   or any("marginal" in n for n in r.notes))
        if err < TOL_MS:
            accurate += 1
        elif not flagged:
            silent.append(f"{label} ({err:.1f} ms, conf "
                          f"{r.median_confidence:.1f})")

    check("robustness: no silently wrong answers", not silent,
          "; ".join(silent) if silent else f"{accurate}/{len(conditions)} "
          "within 1 ms, the rest flagged")
    check("robustness: accurate whenever conditions allow", accurate >= 4,
          f"{accurate}/{len(conditions)}")


def case_confidence_is_calibrated(tmp):
    """Confidence must mean the same thing at every search width.

    This is the property the threshold actually rests on. The naive
    peak/rms ratio does NOT have it: the largest of N noise samples grows
    like sqrt(2 ln N), so a wide search inflates the score of pure noise
    (peak/rms ~= 5.2 over 8 M lags) and any fixed threshold silently becomes
    meaningless as the file gets longer. Dividing by that expected maximum
    is what pins the noise floor at 1.0 regardless of geometry.
    """
    fs = 48000
    rng = np.random.default_rng(4242)
    floors = []
    for n_lag in (2048, 24000, 200000):
        for _ in range(3):
            x = rng.standard_normal(int(2.5 * n_lag)).astype(np.float32)
            y = rng.standard_normal(int(2.5 * n_lag)).astype(np.float32)
            _, conf, _, _ = engine.gcc_phat(x, y, fs, max_lag=n_lag)
            floors.append(conf)

    worst = max(floors)
    check("calibration: unrelated audio scores ~1.0 at every search width",
          worst < 1.35, f"worst null score {worst:.2f} over "
          f"{len(floors)} trials (spread {min(floors):.2f}-{worst:.2f})")
    check("calibration: the threshold clears the noise floor with margin",
          engine.GOOD_PROBE_CONFIDENCE > worst * 1.4,
          f"threshold {engine.GOOD_PROBE_CONFIDENCE} vs floor {worst:.2f}")

    # and a real alignment must clear it comfortably
    src = make_source(40.0, fs)
    a = record(src, fs, 0.0, 30.0, rt60=0.3, mix=0.25, slope=-1.0,
               snr_db=34, gain=0.8, seed=5)
    b = record(src, fs, 2.5, 30.0, rt60=0.5, mix=0.35, slope=+2.0,
               snr_db=26, gain=0.5, seed=13)
    win, search, off = int(10 * fs), int(0.25 * fs), int(2.5 * fs)
    _, conf, _, _ = engine.gcc_phat(
        a[off - search: off + win + search], b[:win], fs, max_lag=2 * search)
    check("calibration: a real alignment scores far above the floor",
          conf > 3.0, f"{conf:.2f}")


def make_long_source(duration, fs, seed=0):
    """A long, cheap, broadband source. Vectorised: an hour costs a second."""
    rng = np.random.default_rng(seed)
    n = int(duration * fs)
    x = rng.standard_normal(n).astype(np.float32) * 0.25
    # slow amplitude contour, so the material is not uniformly exciting --
    # this is what makes anchor CHOICE matter
    env = np.interp(np.arange(n),
                    np.linspace(0, n, 400),
                    np.abs(rng.standard_normal(400)) ** 1.5).astype(np.float32)
    x *= env / (env.max() + 1e-9)
    # transients, which are what the correlation actually locks onto
    for i in rng.integers(0, n - 4000, size=int(duration / 6) + 4):
        k = (np.exp(-np.arange(3000) / 200.0)
             * rng.standard_normal(3000)).astype(np.float32)
        x[i:i + 3000] += 2.5 * k
    return x / (np.abs(x).max() + 1e-9)


def taps_room(x, taps, seed=0):
    """Reverb as a handful of discrete reflections. Cheap at any length."""
    y = x.copy()
    for d, g in taps:
        y[d:] += g * x[:len(x) - d]
    return y / (np.abs(y).max() + 1e-9)


def case_long_file_deep_offset(tmp):
    """The case this tool exists for: a short take buried in a long one.

    A three-hour continuous recording paired with a twenty-minute one is the
    stated workflow. Correlating the two end to end would need an enormous
    FFT, and truncating the long file to make that affordable means anything
    that lines up late is never found at all. The scan must cover the whole
    file.
    """
    fs = 16000
    total_min = 70.0
    src = make_long_source(total_min * 60, fs, seed=1234)

    # the short take starts 52 minutes into the long one
    true_off = 52.0 * 60 + 7.375
    off = int(true_off * fs)
    short_len = int(6.0 * 60 * fs)

    long_sig = taps_room(src, [(431, 0.35), (1277, 0.22)])
    short_sig = taps_room(src[off:off + short_len],
                          [(853, 0.5), (2311, 0.3)]) * 0.35
    short_sig += (np.random.default_rng(9).standard_normal(short_sig.size)
                  .astype(np.float32) * 0.02)

    pa = write(os.path.join(tmp, "long_take.wav"), long_sig, fs,
               subtype="PCM_16")
    pb = write(os.path.join(tmp, "short_take.wav"), short_sig, fs,
               subtype="PCM_16")

    seen = []
    r = engine.analyze(pa, pb, n_probes=9,
                       progress=lambda f, m: seen.append(m))
    err_ms = (r.offset_seconds - true_off) * 1000

    check("long file: alignment 52 min in is found", abs(err_ms) < TOL_MS,
          f"error {err_ms:+.3f} ms (true {true_off / 60:.2f} min)")
    check("long file: reported as a clean result", r.status == "ok",
          f"{r.status}, {r.n_good_probes}/{len(r.probes)} probes")
    check("long file: scan covered more than one block",
          r.coarse_blocks_scanned >= 3, f"{r.coarse_blocks_scanned} blocks")
    check("long file: progress reported where it was scanning",
          any("Scanning" in m for m in seen),
          next((m for m in seen if "Scanning" in m), ""))
    check("long file: A is correctly the earlier track",
          r.late_track == "B", r.late_track)


def case_short_take_at_the_very_end(tmp):
    """Alignment in the last few minutes -- the far edge of the scan."""
    fs = 16000
    src = make_long_source(40.0 * 60, fs, seed=77)
    true_off = 36.0 * 60 + 3.125
    off = int(true_off * fs)
    short_len = int(3.0 * 60 * fs)
    long_sig = taps_room(src, [(311, 0.3)])
    short_sig = taps_room(src[off:off + short_len], [(977, 0.45)]) * 0.4

    pa = write(os.path.join(tmp, "end_long.wav"), long_sig, fs,
               subtype="PCM_16")
    pb = write(os.path.join(tmp, "end_short.wav"), short_sig, fs,
               subtype="PCM_16")
    r = engine.analyze(pa, pb, n_probes=7)
    err_ms = (r.offset_seconds - true_off) * 1000
    check("tail: alignment in the last 10% is found", abs(err_ms) < TOL_MS,
          f"error {err_ms:+.3f} ms")


def _encode(src_wav, dst, codec, rate):
    import av
    inp = av.open(src_wav)
    out = av.open(dst, "w")
    st = out.add_stream(codec, rate=rate)
    rs = av.audio.resampler.AudioResampler(
        format=st.format.name, layout=st.layout, rate=rate)
    for frame in inp.decode(inp.streams.audio[0]):
        for f in (rs.resample(frame) or []):
            f.pts = None
            for pkt in st.encode(f):
                out.mux(pkt)
    for pkt in st.encode(None):
        out.mux(pkt)
    out.close()
    inp.close()
    return dst


def case_compressed_formats(tmp):
    """MP3 and M4A must work, and must export to something a DAW can use.

    An M4A cannot be padded in place -- you cannot prepend silence to an AAC
    stream without re-encoding it -- so export decodes and writes WAV. The
    thing that has to hold is that the offset measured on the decoded audio
    and the audio actually written are the SAME decoded audio, because AAC
    carries encoder priming (about 21 ms here) that shifts the whole stream.
    Measure with one decoder and write with another and every M4A would come
    out quietly late.
    """
    from tracksync import audiofile

    fs = 48000
    src = make_source(120.0, fs)
    true_off = 6.125
    a = record(src, fs, 0.0, 100.0, rt60=0.3, mix=0.28, slope=-1.0,
               snr_db=34, gain=0.8, seed=301)
    b = record(src, fs, true_off, 100.0, rt60=0.6, mix=0.4, slope=+1.5,
               snr_db=26, gain=0.5, seed=307)
    wa = write(os.path.join(tmp, "cf_a.wav"), a, fs)
    wb = write(os.path.join(tmp, "cf_b.wav"), b, fs)

    # --- mp3, handled by libsndfile -------------------------------------
    try:
        ma = _encode(wa, os.path.join(tmp, "cf_a.mp3"), "mp3", fs)
        mb = _encode(wb, os.path.join(tmp, "cf_b.mp3"), "mp3", fs)
    except Exception as exc:
        check("compressed: mp3 encode available for the test", False, str(exc))
        return
    r = engine.analyze(ma, mb, n_probes=7)
    err = (r.offset_seconds - true_off) * 1000
    check("mp3: offset within 1 ms", abs(err) < TOL_MS, f"error {err:+.3f} ms")
    check("mp3: recognised as lossy", r.a.lossy and r.b.lossy,
          f"{r.a.subtype}/{r.b.subtype}")
    check("mp3: flagged so the user exports rather than reusing the source",
          any("compressed format" in n for n in r.notes))

    if not audiofile.HAVE_AV:
        check("m4a: PyAV present", False, "install 'av' for M4A support")
        return

    # --- m4a, handled by PyAV -------------------------------------------
    qa = _encode(wa, os.path.join(tmp, "cf_a.m4a"), "aac", fs)
    qb = _encode(wb, os.path.join(tmp, "cf_b.m4a"), "aac", fs)
    i = audiofile.info(qa)
    check("m4a: opened by the PyAV backend", i.backend == "av",
          f"{i.backend}/{i.subtype}")

    r = engine.analyze(qa, qb, n_probes=7)
    err = (r.offset_seconds - true_off) * 1000
    check("m4a: offset within 1 ms", abs(err) < TOL_MS, f"error {err:+.3f} ms")

    # the real test: export, then re-analyse what was written
    out = os.path.join(tmp, "cf_out")
    res = engine.export_synced(r, out)
    paths = {o["track"]: o["path"] for o in res["outputs"]}
    check("m4a: export writes wav", all(p.endswith(".wav")
                                        for p in paths.values()),
          str(sorted(os.path.basename(p) for p in paths.values())))
    check("m4a: export is not claimed to be bit-identical",
          not any(o["bit_identical"] for o in res["outputs"]))

    r2 = engine.analyze(paths["A"], paths["B"], n_probes=7)
    check("m4a: the exported pair reads back aligned",
          abs(r2.offset_seconds) * 1000 < TOL_MS,
          f"residual {r2.offset_seconds * 1000:+.3f} ms")


def case_export_options(tmp, r):
    """Track selection, report toggle and output format."""
    out = os.path.join(tmp, "opt1")
    res = engine.export_synced(r, out, tracks=("B",), write_report=False)
    check("options: exporting one track writes one file",
          len(res["outputs"]) == 1 and res["outputs"][0]["track"] == "B",
          str([o["track"] for o in res["outputs"]]))
    check("options: report suppressed when unticked",
          res["report_path"] is None
          and not any(f.endswith(".json") for f in os.listdir(out)),
          str(os.listdir(out)))

    out2 = os.path.join(tmp, "opt2")
    res = engine.export_synced(r, out2, fmt="FLAC", depth="PCM_16")
    check("options: format and depth honoured",
          all(o["path"].endswith(".flac") for o in res["outputs"]),
          str([os.path.basename(o["path"]) for o in res["outputs"]]))
    i = sf.info(res["outputs"][0]["path"])
    check("options: flac written at the requested depth",
          i.format == "FLAC" and i.subtype == "PCM_16",
          f"{i.format}/{i.subtype}")

    # single-track export must still carry the right padding
    single = [o for o in res["outputs"] if o["track"] == r.late_track]
    if single:
        check("options: the late track keeps its padding when exported alone",
              single[0]["pad_frames"] > 0, str(single[0]["pad_frames"]))

    out3 = os.path.join(tmp, "opt3")
    res = engine.export_synced(r, out3, fmt="WAV", depth="FLOAT")
    i = sf.info(res["outputs"][0]["path"])
    check("options: 32-bit float export", i.subtype == "FLOAT", i.subtype)


def case_export(tmp, r, pa, pb, fs, true_off):
    """Pad-only export: right amount of silence, audio otherwise untouched."""
    out = os.path.join(tmp, "out")
    res = engine.export_synced(r, out)
    by = {o["track"]: o for o in res["outputs"]}

    check("export: A gets no padding", by["A"]["pad_frames"] == 0,
          str(by["A"]["pad_frames"]))
    want = int(round(true_off * fs))
    got = by["B"]["pad_frames"]
    check("export: B padding correct to the sample",
          abs(got - want) <= int(0.001 * fs),
          f"{got} vs {want} samples ({(got - want) / fs * 1000:+.3f} ms)")

    ia, ib = sf.info(by["A"]["path"]), sf.info(by["B"]["path"])
    o_a, o_b = sf.info(pa), sf.info(pb)
    check("export: A keeps rate/channels/depth",
          (ia.samplerate, ia.channels, ia.subtype)
          == (o_a.samplerate, o_a.channels, o_a.subtype),
          f"{ia.samplerate}/{ia.channels}/{ia.subtype}")
    check("export: B keeps rate/channels/depth",
          (ib.samplerate, ib.channels, ib.subtype)
          == (o_b.samplerate, o_b.channels, o_b.subtype),
          f"{ib.samplerate}/{ib.channels}/{ib.subtype}")

    src_a = sf.read(pa, dtype="int32", always_2d=True)[0]
    new_a = sf.read(by["A"]["path"], dtype="int32", always_2d=True)[0]
    check("export: unpadded track is bit-identical",
          new_a.shape == src_a.shape and np.array_equal(new_a, src_a))

    src_b = sf.read(pb, dtype="int32", always_2d=True)[0]
    new_b = sf.read(by["B"]["path"], dtype="int32", always_2d=True)[0]
    check("export: padded track is silence + original, bit-identical",
          new_b.shape[0] == src_b.shape[0] + got
          and not new_b[:got].any()
          and np.array_equal(new_b[got:], src_b))

    # the real test: after export, does a fresh analysis say they are aligned?
    r2 = engine.analyze(by["A"]["path"], by["B"]["path"])
    check("export: re-analysis of the pair reads ~0 ms",
          abs(r2.offset_seconds) * 1000 < TOL_MS,
          f"residual {r2.offset_seconds * 1000:+.3f} ms")

    check("export: report json written", os.path.exists(res["report_path"]))


def main():
    with tempfile.TemporaryDirectory() as tmp:
        print("\n=== basic ===")
        r, pa, pb, fs, off = case_basic(tmp)
        print("\n=== reversed ===");     case_reversed(tmp)
        print("\n=== sub-sample ===");   case_subsample(tmp)
        print("\n=== mixed rates ===");  case_mixed_rates(tmp)
        print("\n=== drift ===");        case_drift(tmp)
        print("\n=== no overlap ===");   case_no_overlap(tmp)
        print("\n=== edit/dropout ==="); case_edit(tmp)
        print("\n=== long file, deep offset ===")
        case_long_file_deep_offset(tmp)
        print("\n=== alignment near the end ===")
        case_short_take_at_the_very_end(tmp)
        print("\n=== robustness ===");   case_never_silently_wrong(tmp)
        print("\n=== calibration ==="); case_confidence_is_calibrated(tmp)
        print("\n=== export ===");       case_export(tmp, r, pa, pb, fs, off)
        print("\n=== export options ==="); case_export_options(tmp, r)
        print("\n=== compressed formats ==="); case_compressed_formats(tmp)

    n_fail = sum(1 for _, ok, _ in RESULTS if not ok)
    print(f"\n{len(RESULTS) - n_fail}/{len(RESULTS)} passed")
    if n_fail:
        for name, ok, detail in RESULTS:
            if not ok:
                print(f"  FAILED: {name}  {detail}")
    return 1 if n_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
