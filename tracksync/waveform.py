"""
waveform.py -- peak envelopes and the widget that draws them.

The envelope is computed on the SYNCED timeline, so the leading silence that
export_synced() will write is drawn as real silence, and two lanes that line
up on screen are the fastest confirmation that the number in the report is
right.

The view is zoomable, which is the point: at full zoom a 2-hour take is about
6 seconds per pixel, so nothing sub-second is visible. Scroll to zoom in and
the envelope is recomputed for the visible span only -- at maximum zoom you
are looking at individual samples and can see a one-millisecond error.

Zooming has to feel instant, and re-reading the file on every wheel notch
does not: a 78-minute M4A means opening a container, seeking and decoding
for each step. So each track is scanned ONCE into a high-resolution
envelope cache, and any view coarser than that cache is derived from it in
memory -- no disk, no decode, a fraction of a millisecond. Only a view
zoomed in past the cache's own resolution goes back to the file, and by
then the span is small enough that the read is cheap either way.
"""

from __future__ import annotations

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from . import audiofile

MIN_SPAN = 0.002          # 2 ms across the full width is as far in as it goes

CACHE_BUCKETS = 262144


class Envelope:
    """A whole-track min/max envelope on the synced timeline.

    Built once by a single streaming pass over the file. `derive` cuts any
    coarser view out of it without touching the disk.
    """

    __slots__ = ("mins", "maxs", "filled", "total", "start", "n")

    def __init__(self, mins, maxs, filled, total, start):
        self.mins, self.maxs, self.filled = mins, maxs, filled
        self.total = float(total)          # seconds spanned by the cache
        self.start = float(start)          # where this track begins on it
        self.n = len(mins)

    @property
    def seconds_per_bucket(self):
        return self.total / max(self.n, 1)

    def can_serve(self, n_buckets, view_span):
        """True when the cache is at least as fine as the view needs.

        One cache bucket per output column is the limit; past that the cache
        would be stretching data it does not have, so the caller reads the
        file instead.
        """
        if self.total <= 0 or view_span <= 0:
            return False
        return (view_span / max(n_buckets, 1)) >= self.seconds_per_bucket

    def derive(self, n_buckets, view_start, view_span):
        """Min/max for a view, straight out of the cache. Vectorised."""
        n_buckets = int(max(16, n_buckets))
        out_min = np.zeros(n_buckets, np.float32)
        out_max = np.zeros(n_buckets, np.float32)
        out_fill = np.zeros(n_buckets, bool)
        if self.total <= 0 or view_span <= 0:
            return out_min, out_max, out_fill

        # cache-bucket index at each output-column boundary
        edges = np.linspace(view_start, view_start + view_span,
                            n_buckets + 1) / self.total * self.n
        edges = np.clip(np.floor(edges).astype(np.int64), 0, self.n)
        starts, ends = edges[:-1], edges[1:]
        keep = ends > starts
        if not keep.any():
            return out_min, out_max, out_fill

        idx = np.flatnonzero(keep)
        lo = np.minimum.reduceat(self.mins, starts[keep])
        hi = np.maximum.reduceat(self.maxs, starts[keep])
        any_data = np.maximum.reduceat(self.filled.astype(np.uint8),
                                       starts[keep]).astype(bool)
        out_min[idx] = lo
        out_max[idx] = hi
        out_fill[idx] = any_data
        return out_min, out_max, out_fill


def build_envelope(path, total_seconds, track_start=0.0,
                   n_buckets=CACHE_BUCKETS, progress=None, cancel=None):
    """One streaming pass over a file into an Envelope."""
    mn, mx, fl = compute_peaks(path, n_buckets, 0.0, total_seconds,
                               track_start, cancel=cancel, progress=progress)
    return Envelope(mn, mx, fl, total_seconds, track_start)


def compute_peaks(path, n_buckets, view_start, view_span, track_start=0.0,
                  cancel=None, progress=None):
    """Min/max envelope of `path` over the visible span of the synced timeline.

    `track_start` is where this file begins on that timeline (its padding).
    Only the overlapping part of the file is read, so zooming stays fast on
    long recordings.

    Returns (mins, maxs, filled) of length n_buckets.
    """
    n_buckets = int(max(16, n_buckets))
    mins = np.zeros(n_buckets, np.float32)
    maxs = np.zeros(n_buckets, np.float32)
    filled = np.zeros(n_buckets, bool)
    if view_span <= 0:
        return mins, maxs, filled

    meta = audiofile.info(str(path))
    fs = meta.samplerate
    frames = meta.frames
    scale = n_buckets / float(view_span)          # buckets per second

    # native sample range that falls inside the view
    n0 = int(np.floor((view_start - track_start) * fs))
    n1 = int(np.ceil((view_start + view_span - track_start) * fs)) + 1
    n0, n1 = max(0, n0), min(frames, n1)
    if n1 <= n0:
        return mins, maxs, filled

    samples_per_bucket = view_span * fs / n_buckets

    with audiofile.open_audio(str(path)) as f:
        f.seek(n0)

        if samples_per_bucket < 2.0:
            # zoomed in past one sample per column: draw the samples themselves
            x = f.read(min(n1 - n0, 1 << 20))
            mono = x.mean(axis=1) if x.shape[1] > 1 else x[:, 0]
            if mono.size:
                t = track_start + (n0 + np.arange(mono.size)) / fs
                b = np.floor((t - view_start) * scale).astype(np.int64)
                ok = (b >= 0) & (b < n_buckets)
                b, v = b[ok], mono[ok]
                if b.size:
                    np.minimum.at(mins, b, v)
                    np.maximum.at(maxs, b, v)
                    filled[b] = True
                    # bridge the gaps between columns so the trace stays solid
                    _bridge(mins, maxs, filled)
            return mins, maxs, filled

        block = max(1 << 15, int(samples_per_bucket * 8))
        pos = n0
        while pos < n1:
            if cancel is not None and cancel():
                break
            x = f.read(min(block, n1 - pos))
            n = x.shape[0]
            if n == 0:
                break
            mono = x.mean(axis=1) if x.shape[1] > 1 else x[:, 0]

            # buckets this block touches
            t0 = track_start + pos / fs
            t1 = track_start + (pos + n) / fs
            b0 = int(np.floor((t0 - view_start) * scale))
            b1 = int(np.ceil((t1 - view_start) * scale))
            b0, b1 = max(0, b0), min(n_buckets, b1)
            if b1 > b0:
                # exact sample index inside this block where each bucket starts
                bs = np.arange(b0, b1 + 1, dtype=np.float64)
                edges = ((bs / scale + view_start - track_start) * fs - pos)
                edges = np.clip(edges, 0, n).astype(np.int64)
                starts, ends = edges[:-1], edges[1:]
                keep = ends > starts
                if keep.any():
                    idx = np.arange(b0, b1)[keep]
                    lo = np.minimum.reduceat(mono, starts[keep])
                    hi = np.maximum.reduceat(mono, starts[keep])
                    np.minimum.at(mins, idx, lo)
                    np.maximum.at(maxs, idx, hi)
                    # a bucket touched twice must not keep a stale zero
                    fresh = idx[~filled[idx]]
                    if fresh.size:
                        first = np.isin(idx, fresh)
                        mins[idx[first]] = lo[first]
                        maxs[idx[first]] = hi[first]
                    filled[idx] = True
            pos += n
            if progress:
                progress(min(1.0, (pos - n0) / max(n1 - n0, 1)))

    return mins, maxs, filled


def _bridge(mins, maxs, filled):
    """Join isolated columns so a zoomed-in trace reads as a line, not dots.

    Vectorised: forward-fill the last filled column across each gap and take
    the union with the next one, rather than looping over the gaps.
    """
    n = filled.size
    if filled.sum() < 2:
        return
    idx = np.where(filled, np.arange(n), -1)
    prev = np.maximum.accumulate(idx)
    nxt = np.minimum.accumulate(
        np.where(filled, np.arange(n), n)[::-1])[::-1]
    gap = (~filled) & (prev >= 0) & (nxt < n)
    if not gap.any():
        return
    a, b = prev[gap], nxt[gap]
    mins[gap] = np.minimum(mins[a], mins[b])
    maxs[gap] = np.maximum(maxs[a], maxs[b])
    filled[gap] = True


# --------------------------------------------------------------------------


class WaveformView(QtWidgets.QWidget):
    """Two stacked lanes on a shared, zoomable synced timeline."""

    seeked = QtCore.Signal(float)          # seconds
    view_changed = QtCore.Signal(float, float)   # start, span

    LANE_COLOURS = {
        "A": QtGui.QColor("#4a9eff"),
        "B": QtGui.QColor("#ffb347"),
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(190)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding,
                           QtWidgets.QSizePolicy.Expanding)
        self.setCursor(QtCore.Qt.CrossCursor)
        self.setMouseTracking(True)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)

        self.total_seconds = 0.0
        self.view_start = 0.0
        self.view_span = 0.0
        self.lanes = {}
        self.playhead = 0.0
        self._hover_x = None
        self._dragging = False
        self._panning = False
        self._pan_from = None
        self._pixmap = None       # the lanes, rendered once and reused
        self._pm_key = None       # (w, h, devicePixelRatio) it was drawn for

    # -- state -------------------------------------------------------------

    def _invalidate(self):
        self._pixmap = None
        self._pm_key = None
        self.update()

    def clear(self):
        self.lanes = {}
        self.total_seconds = 0.0
        self.view_start = self.view_span = 0.0
        self.playhead = 0.0
        self._invalidate()

    def set_lane(self, which, mins, maxs, filled, label, start_seconds):
        lane = self.lanes.setdefault(which, {})
        lane.update(mins=mins, maxs=maxs, filled=filled,
                    label=label, start=start_seconds)
        self._invalidate()

    def set_total(self, seconds):
        self.total_seconds = float(seconds)
        self.view_start = 0.0
        self.view_span = float(seconds)
        self._invalidate()

    def set_playhead(self, seconds):
        """Move the playhead only -- this must not redraw the waveforms.

        The transport ticks 30 times a second. Re-rendering two lanes of a
        couple of thousand line segments on every tick is what made the
        window feel heavy during playback, so the lanes live in a pixmap and
        a tick just blits it and draws one vertical line on top.
        """
        if abs(seconds - self.playhead) > 1e-4:
            self.playhead = float(seconds)
            self.update()

    def resizeEvent(self, event):
        self._pixmap = None
        self._pm_key = None
        super().resizeEvent(event)

    def zoom_to(self, start, span, announce=True):
        span = max(MIN_SPAN, min(span, self.total_seconds or span))
        start = max(0.0, min(start, max(0.0, (self.total_seconds or span) - span)))
        if abs(start - self.view_start) < 1e-9 and abs(span - self.view_span) < 1e-9:
            return
        self.view_start, self.view_span = start, span
        self._invalidate()
        if announce:
            self.view_changed.emit(self.view_start, self.view_span)

    def reset_zoom(self):
        self.zoom_to(0.0, self.total_seconds)

    def zoom_around(self, t_centre, factor):
        span = self.view_span * factor
        frac = ((t_centre - self.view_start) / self.view_span
                if self.view_span > 0 else 0.5)
        self.zoom_to(t_centre - frac * span, span)

    # -- interaction -------------------------------------------------------

    def _x_to_seconds(self, x):
        if self.view_span <= 0 or self.width() <= 1:
            return 0.0
        return self.view_start + x / self.width() * self.view_span

    def _seconds_to_x(self, t):
        if self.view_span <= 0:
            return 0.0
        return (t - self.view_start) / self.view_span * self.width()

    def wheelEvent(self, e):
        if self.total_seconds <= 0:
            return
        steps = e.angleDelta().y() / 120.0
        if not steps:
            return
        self.zoom_around(self._x_to_seconds(e.position().x()), 0.8 ** steps)
        e.accept()

    def mousePressEvent(self, e):
        if self.total_seconds <= 0:
            return
        if e.button() == QtCore.Qt.MiddleButton or (
                e.button() == QtCore.Qt.LeftButton
                and e.modifiers() & QtCore.Qt.ShiftModifier):
            self._panning = True
            self._pan_from = (e.position().x(), self.view_start)
        elif e.button() == QtCore.Qt.LeftButton:
            self._dragging = True
            self.seeked.emit(self._x_to_seconds(e.position().x()))

    def mouseMoveEvent(self, e):
        self._hover_x = e.position().x()
        if self._panning and self._pan_from:
            x0, s0 = self._pan_from
            dt = (x0 - e.position().x()) / max(self.width(), 1) * self.view_span
            self.zoom_to(s0 + dt, self.view_span)
        elif self._dragging:
            self.seeked.emit(self._x_to_seconds(e.position().x()))
        self.update()

    def mouseReleaseEvent(self, e):
        self._dragging = False
        self._panning = False
        self._pan_from = None

    def mouseDoubleClickEvent(self, e):
        self.reset_zoom()

    def leaveEvent(self, e):
        self._hover_x = None
        self.update()

    # -- painting ----------------------------------------------------------

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        w, h = self.width(), self.height()

        if not self.lanes or self.view_span <= 0:
            p.fillRect(self.rect(), QtGui.QColor("#14161a"))
            p.setPen(QtGui.QColor("#5a6270"))
            p.drawText(self.rect(), QtCore.Qt.AlignCenter,
                       "Load two tracks and analyse to see the aligned waveforms")
            return

        # NB compare against the LOGICAL size we recorded, not pixmap.size():
        # that returns device pixels, so on a scaled display it would never
        # match and the lanes would be re-rendered on every single repaint --
        # exactly the cost the pixmap exists to avoid.
        key = (w, h, self.devicePixelRatioF())
        if self._pixmap is None or self._pm_key != key:
            self._pixmap = self._render_lanes(w, h)
            self._pm_key = key
        p.drawPixmap(0, 0, self._pixmap)

        # -- everything below moves without the lanes being redrawn --------
        x = self._seconds_to_x(self.playhead)
        if -2 <= x <= w + 2:
            p.setPen(QtGui.QPen(QtGui.QColor("#ff5470"), 1.5))
            p.drawLine(QtCore.QPointF(x, 0), QtCore.QPointF(x, h))

        if self._hover_x is not None:
            p.setPen(QtGui.QPen(QtGui.QColor("#ffffff40"), 1))
            p.drawLine(QtCore.QPointF(self._hover_x, 0),
                       QtCore.QPointF(self._hover_x, h))

        self._draw_scale(p, w, h)

    def _render_lanes(self, w, h):
        ratio = self.devicePixelRatioF()
        pm = QtGui.QPixmap(int(w * ratio), int(h * ratio))
        pm.setDevicePixelRatio(ratio)
        pm.fill(QtGui.QColor("#14161a"))

        p = QtGui.QPainter(pm)
        p.setRenderHint(QtGui.QPainter.Antialiasing, False)
        order = [k for k in ("A", "B") if k in self.lanes]
        lane_h = h / max(len(order), 1)
        self._draw_grid(p, w, h)
        for i, which in enumerate(order):
            self._draw_lane(p, self.lanes[which], which,
                            QtCore.QRectF(0, i * lane_h, w, lane_h))
        p.end()
        return pm

    def _draw_grid(self, p, w, h):
        step = _nice_step(self.view_span, w / 110.0)
        if step <= 0:
            return
        p.setPen(QtGui.QPen(QtGui.QColor("#232833"), 1))
        t = np.ceil(self.view_start / step) * step
        while t <= self.view_start + self.view_span:
            x = self._seconds_to_x(t)
            p.drawLine(QtCore.QPointF(x, 0), QtCore.QPointF(x, h))
            t += step

    def _draw_scale(self, p, w, h):
        """Bottom-right: how much time one pixel is worth, and the zoom state."""
        per_px = self.view_span / max(w, 1)
        if per_px >= 1:
            unit = f"{per_px:.2f} s/px"
        elif per_px >= 1e-3:
            unit = f"{per_px * 1e3:.2f} ms/px"
        else:
            unit = f"{per_px * 1e6:.0f} µs/px"
        hover = ("" if self._hover_x is None
                 else "   " + _hms(self._x_to_seconds(self._hover_x)))
        text = f"{_hms(self.view_start)} – {_hms(self.view_start + self.view_span)}   ·   {unit}{hover}"
        p.setPen(QtGui.QColor("#7b8494"))
        fh = p.fontMetrics().height()
        p.drawText(QtCore.QRectF(6, h - fh - 3, w - 12, fh),
                   QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter, text)

    def _draw_lane(self, p, lane, which, rect):
        mins = lane.get("mins")
        if mins is None:
            return
        maxs, filled = lane["maxs"], lane["filled"]
        n = len(mins)
        mid = rect.center().y()
        half = rect.height() * 0.40
        colour = self.LANE_COLOURS[which]

        # the silence export will prepend, shaded, with its edge marked
        start = lane.get("start", 0.0)
        if start > 0:
            xs = self._seconds_to_x(start)
            if xs > 0:
                p.fillRect(QtCore.QRectF(rect.left(), rect.top(),
                                         min(xs, rect.width()), rect.height()),
                           QtGui.QColor("#1d222c"))
            if -2 <= xs <= rect.width() + 2:
                p.setPen(QtGui.QPen(QtGui.QColor("#5b6577"), 1,
                                    QtCore.Qt.DashLine))
                p.drawLine(QtCore.QPointF(xs, rect.top()),
                           QtCore.QPointF(xs, rect.bottom()))

        p.setPen(QtGui.QPen(QtGui.QColor("#2a3038"), 1))
        p.drawLine(QtCore.QPointF(rect.left(), mid),
                   QtCore.QPointF(rect.right(), mid))

        # Vertical auto-scale. Zooming into a quiet passage would otherwise
        # show a flat line, which is exactly when you most need to see the
        # shape. The gain is capped so it never invents detail out of noise,
        # and it is stated on screen so nobody misreads level for alignment.
        vis = float(max(np.abs(maxs[filled]).max(initial=0.0),
                        np.abs(mins[filled]).max(initial=0.0))) if filled.any() else 0.0
        vgain = 1.0 if vis <= 1e-6 else float(np.clip(0.92 / vis, 1.0, 60.0))
        half *= vgain

        # One batched drawLines instead of a drawLine per column. At 1900
        # columns per lane the per-call overhead dominates everything else,
        # and it is what made zooming feel sticky.
        p.setPen(QtGui.QPen(colour, 1))
        sx = rect.width() / max(n, 1)
        vis = np.flatnonzero(filled)
        if vis.size:
            xs = rect.left() + vis * sx
            top = mid - maxs[vis] * half
            bot = mid - mins[vis] * half
            # a column with no visible extent still needs to read as a line
            flat = (bot - top) < 1.0
            if flat.any():
                top = np.where(flat, mid - 0.5, top)
                bot = np.where(flat, mid + 0.5, bot)
            p.drawLines([QtCore.QLineF(x, y0, x, y1)
                         for x, y0, y1 in zip(xs, top, bot)])

        # Label height comes from the font, not a fixed 16 px: at 175% the
        # text is taller than the box and the names were being sliced in half.
        fh = p.fontMetrics().height()
        pad = max(3.0, fh * 0.25)
        p.setPen(QtGui.QColor("#c8d0dc"))
        p.drawText(QtCore.QRectF(rect.left() + pad, rect.top() + pad,
                                 rect.width() - 2 * pad, fh + 2),
                   QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                   f"{which}   {lane.get('label', '')}")
        if vgain > 1.05:
            p.setPen(QtGui.QColor("#6b7484"))
            p.drawText(QtCore.QRectF(rect.left() + pad, rect.top() + pad,
                                     rect.width() - 2 * pad, fh + 2),
                       QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter,
                       f"view gain ×{vgain:.0f}")


def _nice_step(total, want_lines):
    if total <= 0 or want_lines <= 0:
        return 0
    raw = total / want_lines
    for s in (0.0005, 0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.25, 0.5,
              1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600):
        if s >= raw:
            return s
    return 3600


def _hms(t):
    t = max(0.0, float(t))
    m, s = divmod(t, 60)
    h, m = divmod(int(m), 60)
    if h:
        return f"{h}:{m:02d}:{s:06.3f}"
    return f"{m:02d}:{s:06.3f}"
