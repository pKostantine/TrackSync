"""
engine.py -- measurement and correction for two recordings of the same event.

Design contract (kept deliberately):

    analyze(a_path, b_path) -> Report     pure measurement, writes nothing
    export_synced(...)                    writes aligned copies

Measurement is separate from correction so the UI can show the offset, the
drift and the per-probe confidence BEFORE committing to a render. Most sync
failures are diagnosable from the report alone.

Sign convention used throughout:

    offset = (index of an event in A) - (index of the same event in B)

    offset > 0  =>  the event sits at a LATER index in A
                =>  A has more audio before the event
                =>  A started recording EARLIER
                =>  B is the late track and gets the silence

There is no scipy dependency: the highpass is applied as a band mask inside
the cross-spectrum, which is free because we are already in the frequency
domain. File reading goes through `audiofile`, which uses libsndfile where
it can (WAV, FLAC, AIFF, MP3, ...) and PyAV for the formats it cannot open
(M4A/AAC, WMA, audio inside MP4/MOV).
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field, asdict

import numpy as np
import soundfile as sf
import soxr

from . import audiofile

# --------------------------------------------------------------------------
# tuning constants
# --------------------------------------------------------------------------

#: Correlation band. Below 120 Hz you are correlating HVAC rumble, handling
#: noise and DC, none of which are common to two recorders in different
#: places. Above ~6 kHz the two mics diverge (different HF absorption,
#: different distance) and the bins are mostly noise, which PHAT would
#: otherwise weight exactly as heavily as good bins.
DEFAULT_BAND = (120.0, 6000.0)

#: Confidence is normalised so that 1.0 == "no better than noise" regardless
#: of how many lags were searched (see _confidence).
#:
#: 2.0 is twice the noise floor: unrelated audio scores about 1.0 no matter
#: how many lags were searched, so this is a real margin rather than a
#: tuned number.
#:
#: It is deliberately NOT set higher. Confidence in the 2-3 range is
#: genuinely ambiguous territory -- a heavily reverberant but correct
#: measurement and a heavily reverberant but wrong one both land there, and
#: no threshold separates them, because the thing that breaks GCC-PHAT in a
#: live room (a strong early reflection winning over the direct sound)
#: produces a peak that is honestly sharp. Raising the bar until the wrong
#: ones were excluded also excluded correct measurements accurate to five
#: microseconds.
#:
#: So the ambiguity is reported rather than thresholded away: anything in
#: that band is flagged as marginal and the user is told to check it by
#: ear. Being told "probably right, verify this one" is worth more than a
#: confident answer that is silently 3.7 ms out.
GOOD_PROBE_CONFIDENCE = 2.0

#: below this, say so: the measurement is near the floor and reverberation
#: can bias every probe the same way, which makes the scatter look healthy
#: while the answer is milliseconds out.
MARGINAL_CONFIDENCE = 3.0

#: below this the coarse pass is considered to have found nothing at all.
COARSE_FAIL_CONFIDENCE = 1.3

#: drift smaller than this is indistinguishable from measurement scatter.
DRIFT_NOISE_FLOOR_PPM = 1.0

_EPS = 1e-12


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------

@dataclass
class Probe:
    """One local refinement of the offset, at one point in time."""
    index: int
    t_seconds: float          # position in B's timeline, window centre
    offset_samples: float     # at the analysis rate
    offset_ms: float
    confidence: float
    used: bool = True         # survived the outlier rejection

    def as_dict(self):
        return asdict(self)


@dataclass
class TrackInfo:
    path: str
    name: str
    samplerate: int
    channels: int
    frames: int
    duration: float
    subtype: str
    format: str
    backend: str = "sndfile"    # which reader opened it
    lossy: bool = False         # compressed source: cannot be padded in place

    def as_dict(self):
        return asdict(self)


@dataclass
class Report:
    a: TrackInfo
    b: TrackInfo
    analysis_rate: int
    coarse_rate: int
    offset_samples: float          # at analysis_rate, A relative to B
    offset_seconds: float
    drift_ppm: float
    coarse_confidence: float
    coarse_offset_samples: int
    coarse_anchor_seconds: float = 0.0    # where the winning anchor came from
    coarse_blocks_scanned: int = 0
    coarse_anchors_tried: int = 0
    coarse_runner_up: float = 0.0         # best rejected candidate
    probes: list = field(default_factory=list)
    n_good_probes: int = 0
    median_confidence: float = 0.0   # of the probes that were trusted
    offset_scatter_ms: float = 0.0   # spread of good probes about the fit
    status: str = "ok"               # ok | no_overlap | offset_only | scattered
    verdict: str = ""
    notes: list = field(default_factory=list)

    # -- derived -----------------------------------------------------------

    @property
    def late_track(self) -> str:
        """Which track started recording later, i.e. which one gets silence."""
        if abs(self.offset_seconds) < 1e-9:
            return "none"
        return "B" if self.offset_seconds > 0 else "A"

    @property
    def pad_seconds(self) -> float:
        return abs(self.offset_seconds)

    def pad_frames_for(self, which: str) -> int:
        """Silence to prepend to track `which` ('A'/'B'), in ITS OWN samples."""
        if which != self.late_track:
            return 0
        rate = self.a.samplerate if which == "A" else self.b.samplerate
        return int(round(self.pad_seconds * rate))

    def drift_ms_per_hour(self) -> float:
        return self.drift_ppm * 3.6

    def as_dict(self):
        d = asdict(self)
        d["a"] = self.a.as_dict()
        d["b"] = self.b.as_dict()
        d["probes"] = [p.as_dict() if isinstance(p, Probe) else p
                       for p in self.probes]
        d["late_track"] = self.late_track
        d["pad_seconds"] = self.pad_seconds
        d["drift_ms_per_hour"] = self.drift_ms_per_hour()
        return d

    def to_json(self, indent=2):
        return json.dumps(self.as_dict(), indent=indent)


# --------------------------------------------------------------------------
# core estimator
# --------------------------------------------------------------------------

def gcc_phat(a, b, fs, max_lag=None, band=DEFAULT_BAND):
    """Generalised cross-correlation with phase transform.

    Whitening the cross-spectrum by its own magnitude throws away amplitude
    and keeps only phase. That is exactly what we want when the two mics are
    in different positions: the spectral colouring differs wildly (different
    distance, different reverb, different comb filtering) but the phase
    relationship of the direct sound survives. Plain cross-correlation on
    these signals gives a broad, ambiguous peak; PHAT gives a sharp one.

    `band` restricts the whitening to a frequency range, with a raised-cosine
    taper at each edge so the peak does not ring. This replaces a time-domain
    highpass and costs nothing.

    Returns (lag_in_samples_float, confidence, correlation_curve, lags).
    """
    a = np.asarray(a, dtype=np.float32)
    b = np.asarray(b, dtype=np.float32)
    if a.size == 0 or b.size == 0:
        return 0.0, 0.0, np.zeros(1, np.float32), np.zeros(1, np.int64)

    # mild taper: kills the correlation of the window edges themselves
    a = a * _tukey(a.size, 0.10)
    b = b * _tukey(b.size, 0.10)

    n = a.size + b.size
    nfft = 1 << (n - 1).bit_length()

    A = np.fft.rfft(a, nfft)
    B = np.fft.rfft(b, nfft)
    R = A * np.conj(B)

    mag = np.abs(R)
    np.maximum(mag, _EPS, out=mag)      # avoid divide-by-zero in silent bins
    W = R / mag

    if band is not None:
        W *= _band_mask(nfft, fs, band)

    cc = np.fft.irfft(W, nfft)

    if max_lag is None:
        max_lag = nfft // 2
    max_lag = int(min(max_lag, nfft // 2 - 1))

    # unwrap the circular correlation into lags [-max_lag, +max_lag]
    cc = np.concatenate((cc[-max_lag:], cc[:max_lag + 1]))
    lags = np.arange(-max_lag, max_lag + 1)

    i = int(np.argmax(cc))
    peak = float(cc[i])

    # parabolic interpolation for sub-sample resolution
    frac = 0.0
    if 0 < i < cc.size - 1:
        y0, y1, y2 = float(cc[i - 1]), float(cc[i]), float(cc[i + 1])
        denom = y0 - 2.0 * y1 + y2
        if abs(denom) > _EPS:
            frac = float(np.clip(0.5 * (y0 - y2) / denom, -1.0, 1.0))

    confidence = _confidence(cc, i, peak, fs)
    return float(lags[i] + frac), float(confidence), cc, lags


def _confidence(cc, i, peak, fs):
    """How much the peak stands out. 1.0 means "indistinguishable from noise".

    The primary measure is the peak's height above the correlation's own
    noise floor. The naive peak/rms ratio cannot be compared against a fixed
    threshold, because the largest of N noise samples grows like sqrt(2 ln N)
    all by itself -- searching 8 M lags yields peak/rms ~= 5.2 on pure noise,
    which sails past any fixed threshold of 4. Dividing by that expected
    maximum makes the number mean the same thing at every search width, and
    puts pure noise at 1.0.

    A second measure guards against the one failure the first cannot see: a
    genuine TIE, where two lags are equally good (periodic material, a room
    tone loop) and the winner is arbitrary. That is caught by comparing the
    peak with the best rival outside a +/-100 ms guard. The guard has to be
    that wide because early reflections are part of the true peak, not
    rivals -- a heavily reverberant take has legitimate sidelobes 50 ms out.
    So the rival ratio is used only to demote a near-tie, never to demote a
    peak that simply sits in a live room.
    """
    n = cc.size
    if n < 8 or peak <= 0:
        return 0.0
    cc64 = cc.astype(np.float64)

    rms = float(np.sqrt(np.mean(cc64 ** 2))) or _EPS
    expected_null_max = math.sqrt(2.0 * math.log(max(n, 2)))
    snr = peak / (rms * expected_null_max)

    guard = max(4, int(0.100 * fs))
    lo, hi = max(0, i - guard), min(n, i + guard + 1)
    outside = lo + (n - hi)
    if outside > n // 4:                     # enough curve left to judge by
        rival = max(
            float(cc64[:lo].max()) if lo > 0 else -np.inf,
            float(cc64[hi:].max()) if hi < n else -np.inf,
        )
        psr = peak / max(rival, peak / 50.0, _EPS)
        snr *= min(1.0, psr / 1.25)
    return float(snr)


def _tukey(n, alpha):
    if n <= 1 or alpha <= 0:
        return np.ones(max(n, 1), np.float32)
    w = np.ones(n, np.float32)
    edge = int(alpha * n / 2)
    if edge < 1:
        return w
    ramp = 0.5 * (1 - np.cos(np.pi * np.arange(edge) / edge))
    w[:edge] = ramp
    w[-edge:] = ramp[::-1]
    return w


_MASK_CACHE = {}


def _band_mask(nfft, fs, band):
    key = (nfft, int(fs), band)
    m = _MASK_CACHE.get(key)
    if m is not None:
        return m
    lo, hi = band
    nyq = fs / 2.0
    hi = min(hi, nyq * 0.95)
    lo = min(lo, hi * 0.5)
    freqs = np.fft.rfftfreq(nfft, 1.0 / fs)
    mask = np.zeros(freqs.size, np.float32)
    # raised-cosine skirts, half an octave wide, so the peak does not ring
    lo_a, lo_b = lo / 1.4, lo
    hi_a, hi_b = hi, min(hi * 1.4, nyq)
    band_i = (freqs >= lo_b) & (freqs <= hi_a)
    mask[band_i] = 1.0
    up = (freqs > lo_a) & (freqs < lo_b)
    if up.any() and lo_b > lo_a:
        mask[up] = 0.5 * (1 - np.cos(np.pi * (freqs[up] - lo_a) / (lo_b - lo_a)))
    dn = (freqs > hi_a) & (freqs < hi_b)
    if dn.any() and hi_b > hi_a:
        mask[dn] = 0.5 * (1 + np.cos(np.pi * (freqs[dn] - hi_a) / (hi_b - hi_a)))
    if len(_MASK_CACHE) > 12:
        _MASK_CACHE.clear()
    _MASK_CACHE[key] = mask
    return mask


# --------------------------------------------------------------------------
# io helpers -- everything streams, nothing loads a whole file
# --------------------------------------------------------------------------

def probe_file(path) -> TrackInfo:
    i = audiofile.info(path)
    return TrackInfo(
        path=i.path, name=i.name, samplerate=i.samplerate,
        channels=i.channels, frames=i.frames, duration=i.duration,
        subtype=i.subtype, format=i.format, backend=i.backend, lossy=i.lossy,
    )


def load_decimated(path, target_rate, max_seconds=None, progress=None):
    """Stream a file to mono at `target_rate`. Memory stays bounded.

    Uses soxr's streaming resampler so block boundaries are seamless.
    """
    with audiofile.open_audio(path) as src:
        fs_in = src.samplerate
        limit = None if max_seconds is None else int(max_seconds * fs_in)
        total = min(src.frames, limit) if limit else src.frames

        stream = None
        if fs_in != target_rate:
            stream = soxr.ResampleStream(fs_in, target_rate, 1,
                                         dtype="float32", quality="LQ")
        out = []
        done = 0
        block = 1 << 18
        while True:
            want = block if limit is None else min(block, limit - done)
            if want <= 0:
                break
            x = src.read(want)
            if x.shape[0] == 0:
                break
            done += x.shape[0]
            mono = x.mean(axis=1) if x.shape[1] > 1 else x[:, 0]
            last = (total and done >= total) or (x.shape[0] < want)
            if stream is None:
                out.append(mono)
            else:
                y = stream.resample_chunk(mono, last=bool(last))
                if y.size:
                    out.append(y)
            if progress:
                progress(min(1.0, done / max(total, 1)))
            if last:
                break
    if not out:
        return np.zeros(0, np.float32)
    return np.concatenate(out)


def read_window(path, start, count, fs_target, fs_native=None, reader=None):
    """Read `count` samples at `fs_target` starting at sample `start`.

    start/count are expressed in fs_target units. Returns exactly `count`
    samples, zero-padded if the request runs off either end of the file.

    Pass an already-open `reader` when reading many windows from one file --
    re-opening a compressed container per probe is far more expensive than
    re-opening a WAV.
    """
    own = reader is None
    src = audiofile.open_audio(path) if own else reader
    try:
        fs_native = src.samplerate
        ratio = fs_native / float(fs_target)
        s0 = int(math.floor(start * ratio))
        n0 = int(math.ceil(count * ratio)) + 4

        lo = max(0, s0)
        hi = min(src.frames, s0 + n0)
        if hi <= lo:
            return np.zeros(count, np.float32)
        src.seek(lo)
        x = src.read(hi - lo)
    finally:
        if own:
            src.close()

    if x.shape[0] == 0:
        return np.zeros(count, np.float32)
    mono = x.mean(axis=1) if x.shape[1] > 1 else x[:, 0]
    if fs_native != fs_target:
        mono = soxr.resample(mono, fs_native, fs_target, quality="HQ")

    pad_head = max(0, -s0)
    if fs_native != fs_target:
        pad_head = int(round(pad_head / ratio))
    if pad_head:
        mono = np.concatenate((np.zeros(pad_head, np.float32), mono))

    if mono.size < count:
        mono = np.concatenate((mono, np.zeros(count - mono.size, np.float32)))
    return mono[:count].astype(np.float32, copy=False)


#: Ceiling on how many samples of one decimated file are held at once. The
#: coarse rate is chosen to respect it, so a three-hour take costs the same
#: memory as a three-minute one -- just at a lower analysis rate. Stage 2
#: refines at the native rate anyway, so the coarse pass only has to land
#: within its +/-250 ms window; even 1.5 kHz would do.
MAX_COARSE_SAMPLES = 1 << 25


def _choose_coarse_rate(dur_a, dur_b):
    longest = max(dur_a, dur_b, 1e-6)
    rate = MAX_COARSE_SAMPLES / longest
    return int(min(8000, max(1500, round(rate / 500) * 500)))


# --------------------------------------------------------------------------
# coarse search -- scans the WHOLE of the longer file
# --------------------------------------------------------------------------
#
# The naive approach (correlate the two files end to end in one FFT) breaks
# down exactly where this tool is most needed: a three-hour continuous
# recording paired with a twenty-minute one. A single FFT over both is huge,
# and truncating the long file to keep it affordable means anything that
# lines up in hour three is simply never found.
#
# So instead: take a short, high-energy ANCHOR out of the shorter file and
# slide it across the entire longer file in overlapping blocks. Blocks
# overlap by a full anchor length, so every possible alignment sits wholly
# inside at least one block and none can fall through a seam. The best-
# scoring block wins.
#
# If the first anchor finds nothing convincing, another is taken from a
# different part of the file and the scan repeats. An anchor can land on a
# passage that simply is not shared -- a pause, a lens cap, someone talking
# only into one mic -- and the fix for that is to look somewhere else rather
# than to conclude the recordings are unrelated.

ANCHOR_SECONDS = 180.0
BLOCK_FACTOR = 6            # block length as a multiple of the anchor
ANCHOR_POSITIONS = (0.35, 0.60, 0.15, 0.85)


@dataclass
class CoarseResult:
    offset: float               # A minus B, in coarse-rate samples
    confidence: float
    anchor_t: float             # where in the short file the anchor came from
    anchor_seconds: float
    blocks_scanned: int
    anchors_tried: int
    runner_up: float = 0.0      # best confidence among rejected candidates
    scanned_seconds: float = 0.0


def _pick_anchor(short, rate, want_len, fraction):
    """Choose an anchor near `fraction` through the file, biased to energy.

    A window of silence correlates with nothing. Rather than trusting the
    nominal position, this looks at a few candidates around it and takes the
    one carrying the most signal.
    """
    n = short.size
    want_len = int(min(want_len, max(n // 2, rate)))
    if n <= want_len:
        return 0, n
    centre = int(fraction * (n - want_len))
    span = max(want_len // 2, 1)
    best, best_rms = centre, -1.0
    for c in range(max(0, centre - span), min(n - want_len, centre + span) + 1,
                   max(1, span // 3)):
        seg = short[c:c + want_len]
        r = float(np.sqrt(np.mean(seg.astype(np.float64) ** 2)))
        if r > best_rms:
            best, best_rms = c, r
    return best, want_len


def coarse_search(a, b, rate, band, anchor_seconds=ANCHOR_SECONDS,
                  min_confidence=GOOD_PROBE_CONFIDENCE, progress=None,
                  cancel=None) -> CoarseResult:
    """Find the offset between two decimated signals, scanning all of both."""
    a_is_long = a.size >= b.size
    long_sig, short_sig = (a, b) if a_is_long else (b, a)

    anchor_len = int(anchor_seconds * rate)
    block_len = max(int(anchor_len * BLOCK_FACTOR), anchor_len * 2)
    hop = max(block_len - anchor_len, anchor_len)

    best = CoarseResult(0.0, 0.0, 0.0, anchor_seconds, 0, 0)
    runner_up = 0.0
    n_positions = len(ANCHOR_POSITIONS)
    total_blocks = max(1, (max(long_sig.size - block_len, 0) // hop) + 1)

    for ai, frac in enumerate(ANCHOR_POSITIONS):
        if cancel is not None and cancel():
            break
        s0, alen = _pick_anchor(short_sig, rate, anchor_len, frac)
        anchor = short_sig[s0:s0 + alen]
        if anchor.size < rate or not np.any(anchor):
            continue
        best.anchors_tried = ai + 1

        starts = list(range(0, max(long_sig.size - alen, 1), hop)) or [0]
        for bi, t0 in enumerate(starts):
            if cancel is not None and cancel():
                break
            block = long_sig[t0:t0 + block_len]
            if block.size < alen or not np.any(block):
                continue
            lag, conf, _, _ = gcc_phat(block, anchor, rate, band=band)
            best.blocks_scanned += 1
            best.scanned_seconds = (t0 + block.size) / rate
            if conf > best.confidence:
                runner_up = max(runner_up, best.confidence)
                off_long_minus_short = t0 + lag - s0
                best.offset = (off_long_minus_short if a_is_long
                               else -off_long_minus_short)
                best.confidence = conf
                best.anchor_t = s0 / rate
            else:
                runner_up = max(runner_up, conf)
            if progress:
                done = (ai + (bi + 1) / max(len(starts), 1)) / n_positions
                progress(done,
                         f"Scanning {t0 / rate / 60:.0f}-"
                         f"{(t0 + block.size) / rate / 60:.0f} min "
                         f"(anchor {ai + 1})")

        if best.confidence >= min_confidence:
            break               # this anchor found something; no need for more

    best.runner_up = runner_up
    return best


# analysis
# --------------------------------------------------------------------------

def analyze(a_path, b_path, probe_seconds=10.0, n_probes=13,
            drift_search_ms=250.0, band=DEFAULT_BAND,
            anchor_seconds=ANCHOR_SECONDS, progress=None,
            cancel=None) -> Report:
    """Measure the offset and clock drift between two recordings.

    Stage 1 decimates both files and scans the WHOLE of the longer one for
    the shorter one, so a twenty-minute take that lines up two and a half
    hours into a three-hour recording is found just as readily as one that
    lines up in the first minute. Stage 2 places short probe windows across
    the overlap at the native rate and refines locally. The trend of those
    refinements across time IS the clock drift.
    """
    def tick(frac, msg=""):
        if progress:
            progress(float(np.clip(frac, 0.0, 1.0)), msg)

    ta = probe_file(a_path)
    tb = probe_file(b_path)
    fs = max(ta.samplerate, tb.samplerate)

    # ---- stage 1: coarse, scanning the whole of both files ---------------
    tick(0.01, "Reading files")
    coarse_rate = _choose_coarse_rate(ta.duration, tb.duration)
    a_c = load_decimated(a_path, coarse_rate,
                         progress=lambda p: tick(0.01 + 0.12 * p, "Reading A"))
    b_c = load_decimated(b_path, coarse_rate,
                         progress=lambda p: tick(0.13 + 0.12 * p, "Reading B"))

    coarse_band = (band[0], min(band[1], coarse_rate * 0.45))
    cr = coarse_search(
        a_c, b_c, coarse_rate, coarse_band, anchor_seconds=anchor_seconds,
        cancel=cancel,
        progress=lambda f, m: tick(0.25 + 0.14 * f, m))
    conf_c = cr.confidence
    coarse_offset = int(round(cr.offset * fs / coarse_rate))
    del a_c, b_c

    # ---- stage 2: probe windows at the native rate -----------------------
    len_a = int(round(ta.duration * fs))
    len_b = int(round(tb.duration * fs))
    win = int(probe_seconds * fs)
    search = int(drift_search_ms * 1e-3 * fs)

    # region of B for which the corresponding part of A exists
    lo = max(0, -coarse_offset) + search
    hi = min(len_b, len_a - coarse_offset) - search
    usable = hi - lo - win

    probes: list[Probe] = []
    notes = []
    pinned = 0                # probes that ran into the edge of the search window

    if usable <= 0:
        # overlap is shorter than one probe window -- shrink the window
        span = max(0, hi - lo)
        if span > fs:                       # at least a second of overlap
            win = max(int(0.5 * fs), span // 2)
            usable = span - win
            notes.append(
                f"Overlap is short ({span / fs:.1f} s); probe window reduced "
                f"to {win / fs:.1f} s.")

    if usable > 0:
        n_probes = max(3, int(n_probes))
        starts = np.unique(np.linspace(lo, lo + usable, n_probes).astype(np.int64))
        # one open reader per file: re-opening a compressed container for
        # every probe costs far more than the probe itself
        ra = audiofile.open_audio(a_path)
        rb = audiofile.open_audio(b_path)
        try:
            for k, s in enumerate(starts):
                if cancel is not None and cancel():
                    break
                tick(0.40 + 0.55 * (k / max(len(starts), 1)),
                     f"Probe {k + 1} of {len(starts)}")
                b_seg = read_window(b_path, int(s), win, fs, tb.samplerate,
                                    reader=rb)
                a_seg = read_window(a_path, int(s) + coarse_offset - search,
                                    win + 2 * search, fs, ta.samplerate,
                                    reader=ra)
                if not np.any(b_seg) or not np.any(a_seg):
                    continue
                # a_seg is padded by `search` on both sides, so a zero
                # residual corresponds to lag == search
                lag, conf, _, _ = gcc_phat(a_seg, b_seg, fs,
                                           max_lag=2 * search, band=band)
                residual = lag - search
                if abs(residual) > 0.92 * search:
                    pinned += 1
                off = coarse_offset + residual
                probes.append(Probe(
                    index=k,
                    t_seconds=(s + win / 2.0) / fs,
                    offset_samples=float(off),
                    offset_ms=float(off / fs * 1000.0),
                    confidence=float(conf),
                ))
        finally:
            ra.close()
            rb.close()

    good = [p for p in probes if p.confidence > GOOD_PROBE_CONFIDENCE]

    # ---- fit offset(t) = c + m*t ----------------------------------------
    tick(0.96, "Fitting drift")
    drift_ppm = 0.0
    scatter_ms = 0.0

    if len(good) >= 3:
        t = np.array([p.t_seconds * fs for p in good], dtype=np.float64)
        y = np.array([p.offset_samples for p in good], dtype=np.float64)
        w = np.array([p.confidence for p in good], dtype=np.float64)

        m, c = np.polyfit(t, y, 1, w=w)
        # one round of outlier rejection against the fitted line
        resid = y - (m * t + c)
        mad = float(np.median(np.abs(resid - np.median(resid)))) or 1.0
        keep = np.abs(resid) < 4 * 1.4826 * mad
        if keep.sum() >= 3:
            m, c = np.polyfit(t[keep], y[keep], 1, w=w[keep])
            resid = y - (m * t + c)
        for p, k in zip(good, keep):
            p.used = bool(k)

        drift_ppm = float(m * 1e6)
        # report the offset at the midpoint of the measured span, not at t=0:
        # extrapolating a drift line back to zero amplifies fit error.
        t_mid = float(np.median(t[keep] if keep.sum() >= 3 else t))
        offset_samples = float(m * t_mid + c)
        scatter_ms = float(np.std(resid[keep] if keep.sum() >= 3 else resid)
                           / fs * 1000.0)
    elif good:
        offset_samples = float(np.mean([p.offset_samples for p in good]))
        if len(good) > 1:
            scatter_ms = float(np.std([p.offset_ms for p in good]))
    else:
        offset_samples = float(coarse_offset)

    med_conf = float(np.median([p.confidence for p in good])) if good else 0.0
    status, verdict = _verdict(conf_c, good, len(probes), drift_ppm, scatter_ms)

    if cr.anchors_tried > 1 and status != "no_overlap":
        notes.append(
            f"The first {cr.anchors_tried - 1} excerpt(s) tried did not match "
            "anything; the alignment was found from a later part of the "
            "shorter track. That is normal when a take opens with silence, "
            "room tone, or material only one recorder captured.")

    if good and med_conf < MARGINAL_CONFIDENCE:
        notes.append(
            f"Probe confidence is marginal (median {med_conf:.1f}, and 2.0 is "
            "the floor). The probes agree with each other, but agreement is "
            "not accuracy: a very reverberant or very distant second mic can "
            "pull them all off the direct sound together, by a few "
            "milliseconds. Check this one by ear before trusting it.")

    for which, t in (("A", ta), ("B", tb)):
        if t.lossy:
            notes.append(
                f"Track {which} is {t.subtype}, a compressed format. Export "
                "writes the decoded audio, and the measurement was made on "
                "that same decoded audio, so the pair will line up with each "
                "other. Use the exported files rather than the originals: "
                "some codecs carry a few milliseconds of encoder priming that "
                "different decoders handle differently.")

    if pinned >= 2:
        notes.append(
            f"{pinned} probes hit the edge of the +/-{drift_search_ms:.0f} ms "
            "refinement window, so the real offset at those points is further "
            "out than the window allows. Raise the refinement window and "
            "re-analyse.")

    if abs(drift_ppm) >= DRIFT_NOISE_FLOOR_PPM and status == "ok":
        notes.append(
            "Padding alone will not hold this pair together over a long take. "
            "Sync will be correct at the middle of the overlap and drift by "
            f"{abs(drift_ppm) * 3.6:.0f} ms every hour either side of it.")

    tick(1.0, "Done")
    return Report(
        a=ta, b=tb,
        analysis_rate=fs,
        coarse_rate=coarse_rate,
        offset_samples=offset_samples,
        offset_seconds=offset_samples / fs,
        drift_ppm=drift_ppm,
        coarse_confidence=float(conf_c),
        coarse_offset_samples=int(coarse_offset),
        coarse_anchor_seconds=float(cr.anchor_t),
        coarse_blocks_scanned=int(cr.blocks_scanned),
        coarse_anchors_tried=int(cr.anchors_tried),
        coarse_runner_up=float(cr.runner_up),
        probes=probes,
        n_good_probes=len(good),
        median_confidence=med_conf,
        offset_scatter_ms=scatter_ms,
        status=status,
        verdict=verdict,
        notes=notes,
    )


def _verdict(conf_c, good, n_probes, drift_ppm, scatter_ms):
    """The three failure modes, named.

    Note what decides "no alignment": the PROBES, not the coarse score. The
    coarse pass is a search -- it scores dozens of candidate blocks and keeps
    the best -- and the maximum of many trials is inflated even when every
    trial is noise, the same multiple-comparisons effect that makes a wide
    lag search inflate peak/rms. So a coarse hit is a hypothesis, and the
    probes are the test of it. If not one independent probe can confirm the
    candidate at the native rate, there is no alignment, however well the
    winning block happened to score.
    """
    if not good:
        return ("no_overlap",
                "No reliable alignment found. The coarse scan produced a "
                "candidate but no probe could confirm it, which means the "
                "files probably do not overlap, or one is too reverberant or "
                "too noisy to correlate. No tool would sync these.")

    if len(good) < 3:
        return ("offset_only",
                "Offset found, but there were too few confident probes to "
                "measure drift. Treat the offset as constant and check the "
                "tail of the take by ear.")

    # Probes confident individually but scattered with no trend: a dropout or
    # an edit in one file. The probe table shows where the discontinuity is.
    if scatter_ms > 25.0:
        return ("scattered",
                f"Probes are confident but scattered (+/-{scatter_ms:.0f} ms "
                "about the best-fit line) with no clean trend. That is the "
                "signature of a dropout or an edit in one file. Read the "
                "probe table: the offset jumps at the discontinuity. This "
                "pair needs piecewise alignment, not one offset.")

    if abs(drift_ppm) < DRIFT_NOISE_FLOOR_PPM:
        return ("ok",
                "Constant offset, no meaningful clock drift. Padding the late "
                "track is a complete fix.")

    return ("ok",
            f"Clock drift of {drift_ppm:.1f} ppm detected "
            f"({abs(drift_ppm) * 3.6:.0f} ms per hour). The recorders are not "
            "running at the same rate. Padding fixes the start; the ends will "
            "still separate.")


# --------------------------------------------------------------------------
# correction -- pad only, bit-identical audio
# --------------------------------------------------------------------------

#: Containers offered on export. WAV is the default whatever came in: it is
#: what every DAW imports without argument, and a compressed source has to be
#: decoded to be padded at all.
EXPORT_FORMATS = {
    "WAV":  {"ext": ".wav",  "subtypes": ["PCM_16", "PCM_24", "PCM_32", "FLOAT"]},
    "FLAC": {"ext": ".flac", "subtypes": ["PCM_16", "PCM_24"]},
    "AIFF": {"ext": ".aiff", "subtypes": ["PCM_16", "PCM_24", "PCM_32", "FLOAT"]},
}
DEFAULT_FORMAT = "WAV"

#: subtypes we can round-trip losslessly through an int32 buffer
_INT_SUBTYPES = {"PCM_S8", "PCM_U8", "PCM_16", "PCM_24", "PCM_32"}
_FLOAT_SUBTYPES = {"FLOAT", "DOUBLE"}


def resolve_subtype(info: TrackInfo, fmt=DEFAULT_FORMAT, depth="source"):
    """Pick the sample format to write.

    "source" keeps the incoming bit depth when the container supports it,
    which is what makes a WAV-to-WAV export bit-identical. A compressed
    source has no meaningful bit depth, so it lands on 24-bit.
    """
    allowed = EXPORT_FORMATS.get(fmt, EXPORT_FORMATS[DEFAULT_FORMAT])["subtypes"]
    if depth and depth != "source":
        return depth if depth in allowed else allowed[-1]
    if info.subtype in allowed:
        return info.subtype
    return "PCM_24" if "PCM_24" in allowed else allowed[-1]


def export_synced(report: Report, out_dir, suffix="_synced",
                  tracks=("A", "B"), write_report=True,
                  fmt=DEFAULT_FORMAT, depth="source", progress=None):
    """Write the selected tracks aligned, by prepending silence to the late one.

    Nothing is resampled, nothing is trimmed, no channel is folded down. The
    early track is copied sample for sample; the late track is the same copy
    with a block of digital silence in front of it. Both keep their original
    sample rate and channel count, so the pair drops onto a DAW timeline at
    00:00:00 and needs no further nudging.

    Exporting only ONE track is supported and is often what you want: if the
    early track is already on the timeline, all you need is the late one with
    its silence. The padding is computed from the pair either way, so a
    single exported file still lands correctly against its partner.
    """
    out_dir = str(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    fmt = fmt if fmt in EXPORT_FORMATS else DEFAULT_FORMAT
    ext = EXPORT_FORMATS[fmt]["ext"]

    wanted = [w for w in ("A", "B") if w in tracks]
    if not wanted:
        raise ValueError("Nothing selected to export.")

    outputs = []
    for which in wanted:
        info = report.a if which == "A" else report.b
        pad = report.pad_frames_for(which)
        subtype = resolve_subtype(info, fmt, depth)
        stem = os.path.splitext(info.name)[0]
        dest = _unique(os.path.join(out_dir, f"{stem}{suffix}{ext}"))
        _copy_with_padding(
            info, dest, pad, fmt, subtype,
            progress=(lambda p, w=which: progress(w, p)) if progress else None)
        outputs.append({
            "track": which,
            "source": info.path,
            "path": dest,
            "pad_frames": pad,
            "pad_seconds": pad / info.samplerate,
            "samplerate": info.samplerate,
            "channels": info.channels,
            "format": fmt,
            "subtype": subtype,
            "bit_identical": _is_bit_identical(info, fmt, subtype),
        })

    report_path = None
    if write_report:
        report_path = _unique(os.path.join(out_dir, "tracksync_report.json"))
        payload = report.as_dict()
        payload["exported"] = outputs
        with open(report_path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, indent=2))

    return {"outputs": outputs, "report_path": report_path}


def _is_bit_identical(info: TrackInfo, fmt, subtype):
    """True when the copy carries the source samples through untouched."""
    return (info.backend == "sndfile" and not info.lossy
            and subtype == info.subtype and subtype in _INT_SUBTYPES)


def _copy_with_padding(info: TrackInfo, dest, pad_frames, fmt, subtype,
                       progress=None):
    """Stream-copy a file into `dest`, with `pad_frames` of silence in front.

    When the destination sample format matches an integer-PCM source, the
    audio moves through an int32 buffer and the copy is bit-identical. Any
    other combination -- a depth change, a compressed source, a float source
    -- goes through float64 instead.
    """
    exact = _is_bit_identical(info, fmt, subtype)
    if exact:
        dtype, npdtype = "int32", np.int32
    else:
        dtype, npdtype = "float64", np.float64

    block = 1 << 17
    src = (sf.SoundFile(info.path) if exact
           else audiofile.open_audio(info.path))
    try:
        with sf.SoundFile(dest, "w", samplerate=info.samplerate,
                          channels=info.channels, subtype=subtype,
                          format=fmt) as dst:

            remaining = int(pad_frames)
            if remaining > 0:
                silence = np.zeros((min(block, remaining), info.channels),
                                   dtype=npdtype)
                while remaining > 0:
                    n = min(block, remaining)
                    dst.write(silence[:n])
                    remaining -= n

            done = 0
            total = max(info.frames, 1)
            while True:
                if exact:
                    x = src.read(block, dtype=dtype, always_2d=True)
                else:
                    x = src.read(block)
                    if x.shape[0]:
                        x = x.astype(np.float64)
                if x.shape[0] == 0:
                    break
                dst.write(x)
                done += x.shape[0]
                if progress:
                    progress(min(1.0, done / total))
    finally:
        try:
            src.close()
        except Exception:
            pass
    return dest


def _unique(path):
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    i = 2
    while os.path.exists(f"{stem}_{i}{ext}"):
        i += 1
    return f"{stem}_{i}{ext}"


# --------------------------------------------------------------------------
# cli -- the module stays runnable on its own
# --------------------------------------------------------------------------

def format_report(r: Report) -> str:
    lines = []
    add = lines.append
    add(f"A  {r.a.name}   {r.a.duration:8.3f} s  "
        f"{r.a.samplerate} Hz  {r.a.channels} ch  {r.a.subtype}"
        + ("  (lossy)" if r.a.lossy else ""))
    add(f"B  {r.b.name}   {r.b.duration:8.3f} s  "
        f"{r.b.samplerate} Hz  {r.b.channels} ch  {r.b.subtype}"
        + ("  (lossy)" if r.b.lossy else ""))
    add("")
    add(f"offset            {r.offset_seconds:+.6f} s "
        f"({r.offset_seconds * 1000:+.2f} ms)")
    add(f"late track        {r.late_track}"
        + ("" if r.late_track == "none"
           else f"  -> prepend {r.pad_seconds * 1000:.2f} ms of silence"))
    add(f"drift             {r.drift_ppm:+.2f} ppm "
        f"({r.drift_ms_per_hour():+.1f} ms/hour)")
    add(f"coarse scan      {r.coarse_blocks_scanned} blocks, "
        f"{r.coarse_anchors_tried} anchor(s), winning excerpt at "
        f"{r.coarse_anchor_seconds / 60:.1f} min")
    add(f"coarse confidence {r.coarse_confidence:.2f}   "
        f"good probes {r.n_good_probes}/{len(r.probes)} "
        f"(median conf {r.median_confidence:.2f})   "
        f"scatter {r.offset_scatter_ms:.2f} ms")
    add("")
    add(f"[{r.status}] {r.verdict}")
    for n in r.notes:
        add(f"  note: {n}")
    add("")
    add("probe   time(s)    offset(ms)   conf   used")
    for p in r.probes:
        add(f"{p.index:5d}  {p.t_seconds:8.1f}  {p.offset_ms:12.3f}  "
            f"{p.confidence:5.1f}   {'y' if p.used else 'n'}")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("usage: python -m tracksync.engine A.wav B.wav [outdir]")
        raise SystemExit(2)
    rep = analyze(sys.argv[1], sys.argv[2])
    print(format_report(rep))
    if len(sys.argv) > 3:
        res = export_synced(rep, sys.argv[3])
        for o in res["outputs"]:
            print(f"wrote {o['path']}  (+{o['pad_seconds'] * 1000:.2f} ms)")
