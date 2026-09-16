"""
ui.py -- TrackSync main window.

The whole reason to build this instead of using a black-box auto-sync is
seeing the numbers, so the probe table is a first-class part of the window,
not a debug view hidden behind a menu.
"""

from __future__ import annotations

import os
import subprocess
import sys

from PySide6 import QtCore, QtGui, QtWidgets

from . import audiofile, engine
from .player import DualPlayer, HAVE_AUDIO, AUDIO_ERROR
from .waveform import WaveformView, build_envelope, compute_peaks

APP_NAME = "TrackSync"

#: Scaling. Ctrl+= / Ctrl+- step through these; Ctrl+0 returns to 1.0.
#: On macOS these are Command rather than Control: Qt swaps the two on that
#: platform, so Qt.ControlModifier IS the Command key there and the same code
#: gives ⌘+ / ⌘− without a special case.
SCALE_STEPS = (0.75, 0.85, 1.0, 1.15, 1.3, 1.5, 1.75, 2.0, 2.5)

#: Interface font size at 100%. Each desktop has its own idea of "normal" --
#: 9 pt reads correctly on Windows and looks shrunken on macOS, whose system
#: font is 13 pt. Matching the platform means 100% means 100% everywhere.
if sys.platform == "darwin":
    BASE_POINT_SIZE = 13.0
elif sys.platform.startswith("win"):
    BASE_POINT_SIZE = 9.0
else:
    BASE_POINT_SIZE = 10.0

STATUS_COLOUR = {
    "ok": "#38b26a",
    "offset_only": "#d9a441",
    "scattered": "#d9a441",
    "no_overlap": "#e0555f",
}

def _check_icon_url():
    """Path to the tick, in the form Qt stylesheets want.

    A pure-CSS checkbox can be coloured but cannot draw a tick, and an
    unticked-looking blue square is worse than a grey one. So the tick is a
    small bundled image; forward slashes because QSS needs them on Windows
    too, and an empty string if the asset is missing so the rest of the
    sheet still parses.
    """
    path = resource_path("assets", "check.png")
    return path.replace("\\", "/") if os.path.exists(path) else ""


_CHECK_URL = ""


def style_sheet(k=1.0):
    """The whole stylesheet in one place, sized by a single factor.

    Everything the user sees is derived from `k`, so Ctrl+= scales borders,
    padding and control heights together instead of only enlarging text
    inside boxes that stay the same size.
    """
    def px(v):
        return max(1, int(round(v * k)))

    global _CHECK_URL
    if not _CHECK_URL:
        _CHECK_URL = _check_icon_url()

    return f"""
QWidget {{ background: #14161a; color: #d5dae2; }}
QGroupBox {{ border: 1px solid #262b34; border-radius: {px(6)}px;
            margin-top: {px(9)}px; padding-top: {px(9)}px; font-weight: 600; }}
QGroupBox::title {{ subcontrol-origin: margin; left: {px(9)}px;
                   padding: 0 {px(4)}px; color: #8b95a4; font-weight: 600; }}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: #1b1f27; border: 1px solid #2b313b; border-radius: {px(4)}px;
    padding: {px(4)}px {px(6)}px; selection-background-color: #1a56db; }}
QComboBox::drop-down {{ border: 0; width: {px(18)}px; }}
QComboBox QAbstractItemView {{ background: #1b1f27; border: 1px solid #2b313b;
    selection-background-color: #1a56db; }}
QLineEdit:read-only {{ color: #aab3c0; }}
QPushButton {{ background: #232935; border: 1px solid #313945;
              border-radius: {px(5)}px; padding: {px(6)}px {px(13)}px; }}
QPushButton:hover {{ background: #2b3341; }}
QPushButton:pressed {{ background: #1d2129; }}
QPushButton:disabled {{ color: #5a626e; background: #1a1e25;
                       border-color: #23272f; }}
QPushButton#primary {{ background: #1a56db; border-color: #2563eb;
                      color: white; font-weight: 600; }}
QPushButton#primary:hover {{ background: #2563eb; }}
QPushButton#primary:disabled {{ background: #23303f; color: #63708a;
                               border-color: #263243; }}
QPushButton:checked {{ background: #1a56db; border-color: #2563eb;
                      color: white; }}
QTableWidget {{ background: #12141a; gridline-color: #222732;
               border: 1px solid #262b34; border-radius: {px(6)}px; }}
QHeaderView::section {{ background: #1b1f27; border: 0;
    border-bottom: 1px solid #262b34; padding: {px(5)}px; color: #8b95a4;
    font-weight: 600; }}
QProgressBar {{ background: #1b1f27; border: 1px solid #2b313b;
    border-radius: {px(4)}px; text-align: center; height: {px(16)}px; }}
QProgressBar::chunk {{ background: #1a56db; border-radius: {px(3)}px; }}
QSlider::groove:horizontal {{ height: {px(4)}px; background: #2b313b;
    border-radius: {px(2)}px; }}
QSlider::handle:horizontal {{ background: #c8d0dc; width: {px(12)}px;
    margin: -{px(5)}px 0; border-radius: {px(6)}px; }}
QSlider::sub-page:horizontal {{ background: #1a56db;
    border-radius: {px(2)}px; }}
QCheckBox {{ spacing: {px(8)}px; }}
QCheckBox::indicator {{ width: {px(15)}px; height: {px(15)}px;
    border: 1px solid #3a4353; border-radius: {px(4)}px; background: #1b1f27; }}
QCheckBox::indicator:hover {{ border-color: #4a9eff; }}
QCheckBox::indicator:checked {{ background: #1a56db; border-color: #2563eb;
    image: url("{_CHECK_URL}"); }}
QCheckBox::indicator:checked:hover {{ background: #2563eb; }}
QCheckBox::indicator:disabled {{ background: #1a1e25; border-color: #262b34; }}
QRadioButton::indicator {{ width: {px(15)}px; height: {px(15)}px; }}
QLabel#value {{ color: #ffffff; font-weight: 600; }}
QLabel#hint {{ color: #7b8494; }}
QSplitter::handle {{ background: #1e222a; }}
QScrollArea {{ border: 0; background: transparent; }}
QScrollBar:vertical {{ background: #14161a; width: {px(12)}px; margin: 0; }}
QScrollBar::handle:vertical {{ background: #333b49; min-height: {px(30)}px;
    border-radius: {px(5)}px; margin: {px(2)}px; }}
QScrollBar::handle:vertical:hover {{ background: #46536a; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0;
    background: none; border: none; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: none; }}
QScrollBar:horizontal {{ background: #14161a; height: {px(12)}px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: #333b49; min-width: {px(30)}px;
    border-radius: {px(5)}px; margin: {px(2)}px; }}
QScrollBar::handle:horizontal:hover {{ background: #46536a; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0;
    background: none; border: none; }}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
    background: none; }}
QPushButton#exportTall {{ background: #1a56db; border: 1px solid #2f6feb;
    color: #ffffff; font-weight: 600; border-radius: {px(6)}px;
    padding: {px(10)}px {px(14)}px; }}
QPushButton#exportTall:hover {{ background: #2563eb; border-color: #4a9eff; }}
QPushButton#exportTall:pressed {{ background: #17419f; }}
QPushButton#exportTall:disabled {{ background: #22303f; color: #63708a;
    border-color: #263243; }}
QMenuBar {{ background: #14161a; }}
QMenuBar::item {{ padding: {px(4)}px {px(9)}px; }}
QMenuBar::item:selected {{ background: #232935; }}
QMenu {{ background: #1b1f27; border: 1px solid #2b313b;
    padding: {px(4)}px; }}
QMenu::item {{ padding: {px(5)}px {px(26)}px; }}
QMenu::item:selected {{ background: #1a56db; }}
QMenu::separator {{ height: 1px; background: #2b313b;
    margin: {px(4)}px {px(6)}px; }}
QToolTip {{ background: #1b1f27; color: #d5dae2; border: 1px solid #2b313b;
    padding: {px(4)}px; }}
"""



# --------------------------------------------------------------------------
# workers
# --------------------------------------------------------------------------

class AnalyzeWorker(QtCore.QThread):
    progressed = QtCore.Signal(float, str)
    finished_ok = QtCore.Signal(object)
    failed = QtCore.Signal(str)

    def __init__(self, a, b, opts, parent=None):
        super().__init__(parent)
        self.a, self.b, self.opts = a, b, opts

    def run(self):
        try:
            rep = engine.analyze(
                self.a, self.b,
                progress=lambda f, m="": self.progressed.emit(f, m),
                **self.opts)
            self.finished_ok.emit(rep)
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class EnvelopeWorker(QtCore.QThread):
    """Scans each track once into a whole-timeline envelope cache.

    This is the only pass that reads a whole file for display. Everything
    after it -- fit, every wheel notch, every window resize -- is served out
    of the result in memory.
    """
    ready = QtCore.Signal(str, object, str, float)   # which, Envelope, label, start
    progressed = QtCore.Signal(float, str)

    def __init__(self, jobs, total, parent=None):
        super().__init__(parent)
        self.jobs, self.total = jobs, total
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        n = max(len(self.jobs), 1)
        for i, (which, path, label, start) in enumerate(self.jobs):
            try:
                env = build_envelope(
                    path, self.total, start,
                    cancel=lambda: self._cancel,
                    progress=lambda f, i=i: self.progressed.emit(
                        (i + f) / n, "Drawing waveforms"))
            except Exception:
                continue
            if self._cancel:
                return
            self.ready.emit(which, env, label, start)


class RefineWorker(QtCore.QThread):
    """Exact envelope for one view, read from the files.

    Only runs when the view is zoomed in past the cache's resolution. The
    cache-derived version is already on screen by then, so this is a quality
    pass rather than something the user waits for.
    """
    lane_ready = QtCore.Signal(str, object, object, object, str, float, int)

    def __init__(self, jobs, buckets, view_start, view_span, token, parent=None):
        super().__init__(parent)
        self.jobs, self.buckets = jobs, buckets
        self.view_start, self.view_span = view_start, view_span
        self.token = token
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        for which, path, label, start in self.jobs:
            try:
                mn, mx, fl = compute_peaks(path, self.buckets, self.view_start,
                                           self.view_span, start,
                                           cancel=lambda: self._cancel)
            except Exception:
                continue
            if self._cancel:
                return
            self.lane_ready.emit(which, mn, mx, fl, label, start, self.token)


class ExportWorker(QtCore.QThread):
    progressed = QtCore.Signal(str, float)
    finished_ok = QtCore.Signal(object)
    failed = QtCore.Signal(str)

    def __init__(self, report, out_dir, opts, parent=None):
        super().__init__(parent)
        self.report, self.out_dir, self.opts = report, out_dir, opts

    def run(self):
        try:
            res = engine.export_synced(
                self.report, self.out_dir,
                progress=lambda w, p: self.progressed.emit(w, p),
                **self.opts)
            self.finished_ok.emit(res)
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")


# --------------------------------------------------------------------------
# small widgets
# --------------------------------------------------------------------------

class FileRow(QtWidgets.QWidget):
    changed = QtCore.Signal()

    def __init__(self, letter, colour, parent=None):
        super().__init__(parent)
        self.letter = letter
        self.path = ""
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        self._colour = colour
        self._tag = QtWidgets.QLabel(letter)
        self._tag.setAlignment(QtCore.Qt.AlignCenter)
        lay.addWidget(self._tag)

        self.edit = QtWidgets.QLineEdit()
        self.edit.setReadOnly(True)
        self.edit.setPlaceholderText(f"Choose track {letter}…")
        lay.addWidget(self.edit, 1)

        self.info = QtWidgets.QLabel("")
        self.info.setObjectName("hint")
        lay.addWidget(self.info)

        btn = QtWidgets.QPushButton("Browse…")
        btn.clicked.connect(self.browse)
        lay.addWidget(btn)

        self.setAcceptDrops(True)
        self.rescale(1.0)

    def rescale(self, k):
        self._tag.setFixedWidth(int(round(22 * k)))
        self._tag.setStyleSheet(
            f"color:{self._colour}; font-weight:700; "
            f"font-size:{int(round(14 * k))}px;")
        self.info.setFixedWidth(int(round(258 * k)))

    def browse(self):
        start = os.path.dirname(self.path) if self.path else ""
        p, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, f"Choose track {self.letter}", start,
            audiofile.file_dialog_filter())
        if p:
            self.set_path(p)

    def set_path(self, p):
        self.path = p or ""
        self.edit.setText(self.path)
        self.info.setText("")
        if self.path:
            try:
                t = engine.probe_file(self.path)
                self.info.setText(
                    f"{_hms(t.duration)}  ·  {t.samplerate} Hz  ·  "
                    f"{t.channels} ch  ·  {t.subtype}")
                self.info.setToolTip(
                    f"{t.format} / {t.subtype}, read by {t.backend}"
                    + ("  (compressed source)" if t.lossy else ""))
            except Exception as exc:
                self.info.setText("unreadable")
                self.info.setToolTip(str(exc))
        self.changed.emit()

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        urls = e.mimeData().urls()
        if urls:
            self.set_path(urls[0].toLocalFile())


class ChannelStrip(QtWidgets.QWidget):
    changed = QtCore.Signal()

    def __init__(self, letter, colour, parent=None):
        super().__init__(parent)
        self.letter = letter
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        self._colour = colour
        self._tag = QtWidgets.QLabel(letter)
        self._tag.setStyleSheet(f"color:{colour}; font-weight:700;")
        lay.addWidget(self._tag)

        self.slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider.setRange(-600, 120)       # tenths of a dB
        self.slider.setValue(0)
        self.slider.valueChanged.connect(self._on_slider)
        lay.addWidget(self.slider, 1)

        self.readout = QtWidgets.QLabel("0.0 dB")
        self.readout.setObjectName("value")
        self.readout.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        lay.addWidget(self.readout)

        self.meter = QtWidgets.QProgressBar()
        self.meter.setRange(0, 100)
        self.meter.setValue(0)
        self.meter.setTextVisible(False)
        self.meter.setStyleSheet(
            "QProgressBar{background:#1b1f27;border:1px solid #2b313b;}"
            f"QProgressBar::chunk{{background:{colour};}}")
        lay.addWidget(self.meter)

        self.mute = QtWidgets.QPushButton("M")
        self.solo = QtWidgets.QPushButton("S")
        for b in (self.mute, self.solo):
            b.setCheckable(True)
            b.setStyleSheet("QPushButton{padding:5px 0;}")
            b.setToolTip("Mute" if b is self.mute else "Solo")
            b.clicked.connect(lambda *_: self.changed.emit())
            lay.addWidget(b)
        self.rescale(1.0)

    def rescale(self, k):
        r = lambda v: int(round(v * k))
        self._tag.setFixedWidth(r(16))
        self.readout.setFixedWidth(r(58))
        self.meter.setFixedWidth(r(70))
        self.meter.setFixedHeight(r(8))
        for b in (self.mute, self.solo):
            b.setFixedWidth(r(30))

    def _on_slider(self, v):
        self.readout.setText("-∞" if v <= -600 else f"{v / 10:+.1f} dB")
        self.changed.emit()

    def gain(self):
        v = self.slider.value()
        return 0.0 if v <= -600 else 10 ** (v / 200.0)

    def set_peak(self, peak):
        self.meter.setValue(int(min(1.0, peak) * 100))


class SettingsDialog(QtWidgets.QDialog):
    def __init__(self, opts, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Analysis settings")
        self.setModal(True)
        form = QtWidgets.QFormLayout(self)

        self.probe_seconds = QtWidgets.QDoubleSpinBox()
        self.probe_seconds.setRange(1.0, 60.0)
        self.probe_seconds.setSuffix(" s")
        self.probe_seconds.setValue(opts["probe_seconds"])
        form.addRow("Probe window", self.probe_seconds)

        self.n_probes = QtWidgets.QSpinBox()
        self.n_probes.setRange(3, 60)
        self.n_probes.setValue(opts["n_probes"])
        form.addRow("Number of probes", self.n_probes)

        self.search = QtWidgets.QDoubleSpinBox()
        self.search.setRange(10.0, 5000.0)
        self.search.setSuffix(" ms")
        self.search.setValue(opts["drift_search_ms"])
        form.addRow("Refinement window", self.search)

        self.lo = QtWidgets.QDoubleSpinBox()
        self.lo.setRange(10.0, 2000.0)
        self.lo.setSuffix(" Hz")
        self.lo.setValue(opts["band"][0])
        form.addRow("Correlation band, low", self.lo)

        self.hi = QtWidgets.QDoubleSpinBox()
        self.hi.setRange(200.0, 22000.0)
        self.hi.setSuffix(" Hz")
        self.hi.setValue(opts["band"][1])
        form.addRow("Correlation band, high", self.hi)

        note = QtWidgets.QLabel(
            "Widen the refinement window if the report warns that probes hit\n"
            "its edge. Raise the low band edge for takes with heavy rumble.")
        note.setObjectName("hint")
        form.addRow(note)

        bb = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def values(self):
        return dict(
            probe_seconds=self.probe_seconds.value(),
            n_probes=self.n_probes.value(),
            drift_search_ms=self.search.value(),
            band=(self.lo.value(), self.hi.value()),
        )



class ExportDialog(QtWidgets.QDialog):
    """What to write, where, and in what format.

    Track selection earns its place: half the time the early track is already
    sitting on the timeline and the only thing needed is the late one with
    its silence in front. Exporting just that file saves copying gigabytes of
    audio that has not changed. The padding is computed from the pair either
    way, so a lone exported track still lands correctly against its partner.
    """

    def __init__(self, report, out_dir, defaults=None, parent=None):
        super().__init__(parent)
        self.report = report
        self.setWindowTitle("Export aligned pair")
        self.setModal(True)
        d = defaults or {}

        lay = QtWidgets.QVBoxLayout(self)
        lay.setSpacing(12)

        # -- which tracks --------------------------------------------------
        box = QtWidgets.QGroupBox("Tracks to write")
        gl = QtWidgets.QVBoxLayout(box)
        self.chk = {}
        for which in ("A", "B"):
            info = report.a if which == "A" else report.b
            pad = report.pad_frames_for(which) / max(info.samplerate, 1) * 1000
            c = QtWidgets.QCheckBox(
                f"{which}   {info.name}\n"
                + ("       " + (f"{pad:,.3f} ms of silence prepended" if pad
                                else "unchanged, sample for sample")))
            # Default to just the track that actually changes. The other is
            # a byte-for-byte copy of a file already on disk, and for a
            # three-hour take that is gigabytes written for nothing.
            # If neither is padded the pair already lines up, so offer both.
            default_on = (report.late_track == "none"
                          or which == report.late_track)
            c.setChecked(bool(d.get(which, default_on)))
            c.toggled.connect(self._validate)
            gl.addWidget(c)
            self.chk[which] = c
        lay.addWidget(box)

        # -- format --------------------------------------------------------
        form = QtWidgets.QGroupBox("Output format")
        fl = QtWidgets.QFormLayout(form)

        self.fmt = QtWidgets.QComboBox()
        for name in engine.EXPORT_FORMATS:
            self.fmt.addItem(name)
        self.fmt.setCurrentText(d.get("fmt", engine.DEFAULT_FORMAT))
        self.fmt.currentTextChanged.connect(self._refresh_depths)
        fl.addRow("Container", self.fmt)

        self.depth = QtWidgets.QComboBox()
        fl.addRow("Bit depth", self.depth)

        self.note = QtWidgets.QLabel("")
        self.note.setObjectName("hint")
        self.note.setWordWrap(True)
        fl.addRow(self.note)
        lay.addWidget(form)

        # -- extras --------------------------------------------------------
        self.report_chk = QtWidgets.QCheckBox(
            "Also write tracksync_report.json (every number from the analysis)")
        self.report_chk.setChecked(d.get("write_report", False))
        lay.addWidget(self.report_chk)

        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Folder"))
        self.dir_edit = QtWidgets.QLineEdit(out_dir)
        self.dir_edit.setReadOnly(True)
        row.addWidget(self.dir_edit, 1)
        pick = QtWidgets.QPushButton("Choose…")
        pick.clicked.connect(self._pick_dir)
        row.addWidget(pick)
        lay.addLayout(row)

        self.bb = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        self.bb.button(QtWidgets.QDialogButtonBox.Ok).setText("Export")
        self.bb.button(QtWidgets.QDialogButtonBox.Ok).setObjectName("primary")
        self.bb.accepted.connect(self.accept)
        self.bb.rejected.connect(self.reject)
        lay.addWidget(self.bb)

        self._refresh_depths(self.fmt.currentText())
        if d.get("depth"):
            i = self.depth.findData(d["depth"])
            if i >= 0:
                self.depth.setCurrentIndex(i)
        self._validate()

    def _refresh_depths(self, fmt):
        current = self.depth.currentData()
        self.depth.clear()
        self.depth.addItem("Same as source where possible", "source")
        labels = {"PCM_16": "16-bit integer", "PCM_24": "24-bit integer",
                  "PCM_32": "32-bit integer", "FLOAT": "32-bit float"}
        for st in engine.EXPORT_FORMATS[fmt]["subtypes"]:
            self.depth.addItem(labels.get(st, st), st)
        i = self.depth.findData(current)
        self.depth.setCurrentIndex(max(i, 0))
        self._describe()
        self.depth.currentIndexChanged.connect(lambda *_: self._describe())

    def _describe(self):
        fmt = self.fmt.currentText()
        depth = self.depth.currentData() or "source"
        lines = []
        for which in ("A", "B"):
            info = self.report.a if which == "A" else self.report.b
            st = engine.resolve_subtype(info, fmt, depth)
            exact = engine._is_bit_identical(info, fmt, st)
            lines.append(
                f"{which}: {st}" + (" — bit-identical to the source" if exact
                                    else " — re-encoded from the source"))
        self.note.setText("   ·   ".join(lines))

    def _pick_dir(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Export folder", self.dir_edit.text())
        if d:
            self.dir_edit.setText(d)

    def _validate(self):
        ok = any(c.isChecked() for c in self.chk.values())
        self.bb.button(QtWidgets.QDialogButtonBox.Ok).setEnabled(ok)

    def values(self):
        return {
            "out_dir": self.dir_edit.text(),
            "tracks": tuple(w for w, c in self.chk.items() if c.isChecked()),
            "write_report": self.report_chk.isChecked(),
            "fmt": self.fmt.currentText(),
            "depth": self.depth.currentData() or "source",
        }


# --------------------------------------------------------------------------
# main window
# --------------------------------------------------------------------------

class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        # The version goes in the title on purpose: "am I actually running
        # the new build?" should be answerable at a glance, without digging
        # into a menu.
        from . import __version__
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.setWindowIcon(app_icon())
        self.resize(1180, 830)

        self.report = None
        self.player = DualPlayer()
        self.analyzer = None
        self.env_worker = None
        self.refine_worker = None
        self.envelopes = {}        # 'A'/'B' -> Envelope
        self.lane_meta = {}        # 'A'/'B' -> (label, start_seconds)
        self._view_token = 0
        self.exporter = None
        self.settings = QtCore.QSettings("TrackSync", "TrackSync")
        self.opts = dict(probe_seconds=10.0, n_probes=13,
                         drift_search_ms=250.0, band=engine.DEFAULT_BAND)
        self.export_opts = {}
        self.out_dir = ""
        try:
            self.scale = float(self.settings.value("ui_scale", 1.0))
        except (TypeError, ValueError):
            self.scale = 1.0
        self._scalables = []          # (widget, base_width, base_height|None)

        self._build()
        self._install_shortcuts()
        self.apply_scale(self.scale)

        self._redraw = QtCore.QTimer(self)
        self._redraw.setSingleShot(True)
        self._redraw.timeout.connect(self._refine_view)

        self.tick = QtCore.QTimer(self)
        self.tick.setInterval(33)
        self.tick.timeout.connect(self._on_tick)
        self.tick.start()

    # -- scaling -----------------------------------------------------------

    def _fix(self, widget, w=None, h=None):
        """Register a fixed size that should grow with the UI scale."""
        self._scalables.append((widget, w, h))
        return widget

    def _install_shortcuts(self):
        def add(seq, fn):
            sc = QtGui.QShortcut(QtGui.QKeySequence(seq), self)
            sc.setContext(QtCore.Qt.ApplicationShortcut)
            sc.activated.connect(fn)

        # Zoom, open, export and analyse all live on the menu, which owns
        # their shortcuts; binding them here as well would make Qt call both
        # handlers and warn about an ambiguous overload. keyPressEvent below
        # is the safety net for keyboards the menu bindings miss.
        del add

    def keyPressEvent(self, event):
        """Catch the zoom keys the menu bindings can miss.

        Layouts differ in what Ctrl and the minus key actually produce, and
        the numeric keypad reports its own key codes with a keypad modifier.
        Matching on the key here covers both without caring which.
        """
        if event.modifiers() & QtCore.Qt.ControlModifier:
            k = event.key()
            if k in (QtCore.Qt.Key_Minus, QtCore.Qt.Key_Underscore):
                self.step_scale(-1)
                event.accept()
                return
            if k in (QtCore.Qt.Key_Plus, QtCore.Qt.Key_Equal):
                self.step_scale(+1)
                event.accept()
                return
            if k == QtCore.Qt.Key_0:
                self.apply_scale(1.0)
                event.accept()
                return
        super().keyPressEvent(event)

    def step_scale(self, direction):
        steps = list(SCALE_STEPS)
        # nearest current step, then move one along
        i = min(range(len(steps)), key=lambda j: abs(steps[j] - self.scale))
        i = max(0, min(len(steps) - 1, i + direction))
        self.apply_scale(steps[i])

    def apply_scale(self, k):
        k = float(max(SCALE_STEPS[0], min(SCALE_STEPS[-1], k)))
        self.scale = k
        self.settings.setValue("ui_scale", k)

        app = QtWidgets.QApplication.instance()
        if app is not None:
            f = app.font()
            f.setPointSizeF(BASE_POINT_SIZE * k)
            app.setFont(f)
            self.setFont(f)
        self.setStyleSheet(style_sheet(k))

        for widget, w, h in self._scalables:
            try:
                if w is not None:
                    widget.setFixedWidth(int(round(w * k)))
                if h is not None:
                    widget.setFixedHeight(int(round(h * k)))
            except RuntimeError:
                continue            # widget already destroyed
        for w in (self.row_a, self.row_b, self.strip_a, self.strip_b):
            w.rescale(k)
        self.wave.setMinimumHeight(int(round(150 * k)))
        self.wave_box.setMinimumHeight(int(round(300 * k)))
        self.lower_split.setMinimumHeight(int(round(330 * k)))
        # keep the pinned header and footer able to show themselves in full
        self.setMinimumWidth(int(round(880 * k)))
        self.setMinimumHeight(int(round(420 * k)))
        self.lbl_offset.setStyleSheet(
            f"font-size:{int(round(27 * k))}px; font-weight:700; color:#ffffff;")
        self.lbl_action.setStyleSheet(
            f"color:#9fb4d8; font-size:{int(round(13 * k))}px;")
        self.statusBar().showMessage(f"Interface scale {k * 100:.0f}%", 1500)
        self.updateGeometry()

    # -- construction ------------------------------------------------------

    def _build(self):
        """Header, scrolling middle, footer.

        The two sections you always need -- which files, and the transport
        plus export -- are pinned outside the scroll area so they can never
        be half cut off. Everything that is a readout rather than a control
        goes in the middle and scrolls, which is what makes the window
        usable at 150% or 250% on a laptop screen.
        """
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # ---- fixed header ------------------------------------------------
        header = QtWidgets.QWidget()
        hl = QtWidgets.QVBoxLayout(header)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(8)
        hl.addWidget(self._build_files())
        hl.addLayout(self._build_actions())
        header.setSizePolicy(QtWidgets.QSizePolicy.Preferred,
                             QtWidgets.QSizePolicy.Fixed)
        root.addWidget(header)

        # ---- scrolling middle --------------------------------------------
        self.scroll = QtWidgets.QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarAsNeeded)
        self.scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)

        inner = QtWidgets.QWidget()
        il = QtWidgets.QVBoxLayout(inner)
        il.setContentsMargins(0, 0, 0, 0)
        il.setSpacing(10)
        self.wave_box = self._build_waveform()
        il.addWidget(self.wave_box)
        self.lower_split = self._build_lower()
        il.addWidget(self.lower_split)
        self.scroll.setWidget(inner)
        root.addWidget(self.scroll, 1)

        # ---- fixed footer ------------------------------------------------
        self.footer = self._build_transport()
        self.footer.setSizePolicy(QtWidgets.QSizePolicy.Preferred,
                                  QtWidgets.QSizePolicy.Fixed)
        root.addWidget(self.footer)

        self._build_menu()
        from . import __version__
        self.statusBar().showMessage(
            f"TrackSync {__version__} — ready." if HAVE_AUDIO else
            f"Ready — monitoring unavailable ({AUDIO_ERROR}). "
            "Analysis and export still work.")

    def _build_menu(self):
        def act(menu, text, fn, shortcut=None):
            a = menu.addAction(text)
            a.triggered.connect(fn)
            if shortcut:
                a.setShortcut(QtGui.QKeySequence(shortcut))
            return a

        mb = self.menuBar()
        m = mb.addMenu("&File")
        act(m, "Open track &A…", self.row_a.browse, "Ctrl+O")
        act(m, "Open track &B…", self.row_b.browse, "Ctrl+Shift+O")
        m.addSeparator()
        act(m, "&Analyse sync", self.analyze, "Ctrl+R")
        act(m, "&Export…", self.export, "Ctrl+E")
        m.addSeparator()
        act(m, "&Quit", self.close, "Ctrl+Q")

        v = mb.addMenu("&View")
        # Bind zoom by KEY, and bind each chord exactly ONCE.
        #
        # Both halves matter. "Ctrl+-" as a string is ambiguous to Qt's
        # parser, which uses "+" as its own separator. And registering the
        # same chord twice -- say Key_Minus alongside QKeySequence.ZoomOut,
        # which IS Ctrl+Minus -- makes Qt call it an "ambiguous shortcut
        # overload" and fire NEITHER copy, while still swallowing the key.
        # That is what made Ctrl+- dead while Ctrl+_ worked: the underscore
        # had one binding and the minus had two.
        C = QtCore.Qt.ControlModifier
        K = QtCore.Qt.Key
        zoom_in = act(v, "Zoom &in", lambda: self.step_scale(+1))
        zoom_in.setShortcuts(_unique_sequences([
            QtGui.QKeySequence(C | K.Key_Plus),
            QtGui.QKeySequence(C | K.Key_Equal),
            QtGui.QKeySequence(QtGui.QKeySequence.ZoomIn),
        ]))
        zoom_out = act(v, "Zoom &out", lambda: self.step_scale(-1))
        zoom_out.setShortcuts(_unique_sequences([
            QtGui.QKeySequence(C | K.Key_Minus),
            QtGui.QKeySequence(C | K.Key_Underscore),
            QtGui.QKeySequence(QtGui.QKeySequence.ZoomOut),
        ]))
        reset = act(v, "&Reset zoom", lambda: self.apply_scale(1.0))
        reset.setShortcuts(_unique_sequences([
            QtGui.QKeySequence(C | K.Key_0),
        ]))
        v.addSeparator()
        h = mb.addMenu("&Help")
        act(h, "Supported formats…", self._show_formats)
        act(h, f"About {APP_NAME}", self._show_about)

    def _show_formats(self):
        exts = ", ".join(audiofile.supported_extensions())
        extra = ("" if audiofile.HAVE_AV else
                 "\n\nM4A/AAC support needs the 'av' package, which is not "
                 "installed in this build.")
        QtWidgets.QMessageBox.information(
            self, APP_NAME,
            "TrackSync reads:\n\n" + exts + extra
            + "\n\nExport always writes an uncompressed format — a "
              "compressed file cannot have silence prepended without "
              "re-encoding it.")

    def _show_about(self):
        from . import __version__
        QtWidgets.QMessageBox.about(
            self, f"About {APP_NAME}",
            f"<b>{APP_NAME} {__version__}</b><br><br>"
            "Measures the offset and clock drift between two recordings of "
            "the same event, and writes them aligned.<br><br>"
            "Ctrl + and Ctrl - scale the interface; Ctrl 0 resets it.")

    def _build_files(self):
        box = QtWidgets.QGroupBox("Tracks")
        lay = QtWidgets.QVBoxLayout(box)
        lay.setSpacing(6)
        self.row_a = FileRow("A", "#4a9eff")
        self.row_b = FileRow("B", "#ffb347")
        for r in (self.row_a, self.row_b):
            r.changed.connect(self._on_files_changed)
            lay.addWidget(r)
        return box

    def _build_actions(self):
        lay = QtWidgets.QHBoxLayout()
        lay.setSpacing(8)

        self.btn_analyze = QtWidgets.QPushButton("Analyse sync")
        self.btn_analyze.setObjectName("primary")
        self.btn_analyze.setEnabled(False)
        self.btn_analyze.clicked.connect(self.analyze)
        lay.addWidget(self.btn_analyze)

        btn_set = QtWidgets.QPushButton("Settings…")
        btn_set.clicked.connect(self.open_settings)
        lay.addWidget(btn_set)

        btn_swap = QtWidgets.QPushButton("Swap A / B")
        btn_swap.clicked.connect(self.swap)
        lay.addWidget(btn_swap)

        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setFormat("")
        lay.addWidget(self.progress, 1)

        return lay

    def _build_waveform(self):
        box = QtWidgets.QGroupBox("Aligned timeline")
        lay = QtWidgets.QVBoxLayout(box)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        self.wave = WaveformView()
        self.wave.seeked.connect(self._on_seek_wave)
        self.wave.view_changed.connect(self._on_view_changed)
        lay.addWidget(self.wave, 1)
        box.setMinimumHeight(300)

        bar = QtWidgets.QHBoxLayout()
        bar.setSpacing(6)
        hint = QtWidgets.QLabel(
            "Scroll to zoom · shift-drag to pan · double-click to fit · "
            "click to move the playhead")
        hint.setObjectName("hint")
        bar.addWidget(hint)
        bar.addStretch(1)
        for label, span, tip in (
                ("Fit", None, "Show the whole take"),
                ("1 s", 2.0, "Two seconds around the playhead"),
                ("100 ms", 0.1, "A tenth of a second around the playhead"),
                ("10 ms", 0.01, "Close enough to read the alignment by eye")):
            b = QtWidgets.QPushButton(label)
            b.setToolTip(tip)
            self._fix(b, 76)
            b.clicked.connect(lambda _=False, s=span: self._zoom_preset(s))
            bar.addWidget(b)
        lay.addLayout(bar)
        return box

    def _zoom_preset(self, span):
        if not self.wave.total_seconds:
            return
        if span is None:
            self.wave.reset_zoom()
        else:
            t = self.player.position / max(self.player.fs, 1)
            self.wave.zoom_to(t - span / 2, span)

    def _build_lower(self):
        split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        split.addWidget(self._build_report())
        split.addWidget(self._build_table())
        split.setStretchFactor(0, 4)
        split.setStretchFactor(1, 5)
        split.setMinimumHeight(330)
        return split

    def _build_report(self):
        box = QtWidgets.QGroupBox("Measurement")
        lay = QtWidgets.QVBoxLayout(box)
        lay.setSpacing(8)

        self.lbl_offset = QtWidgets.QLabel("—")
        self.lbl_offset.setStyleSheet(
            "font-size:27px; font-weight:700; color:#ffffff;")
        lay.addWidget(self.lbl_offset)

        self.lbl_action = QtWidgets.QLabel(
            "Load two recordings of the same event and press Analyse sync.")
        self.lbl_action.setWordWrap(True)
        self.lbl_action.setStyleSheet("color:#9fb4d8; font-size:13px;")
        lay.addWidget(self.lbl_action)

        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(9)
        self.stat = {}
        for i, (key, label) in enumerate((
                ("drift", "Clock drift"),
                ("conf", "Coarse confidence"),
                ("probes", "Confident probes"),
                ("scatter", "Probe scatter"),
                ("rate", "Analysis rate"))):
            k = QtWidgets.QLabel(label)
            k.setObjectName("hint")
            v = QtWidgets.QLabel("—")
            v.setObjectName("value")
            grid.addWidget(k, i, 0)
            grid.addWidget(v, i, 1)
            self.stat[key] = v
        grid.setColumnStretch(1, 1)
        lay.addLayout(grid)

        self.lbl_verdict = QtWidgets.QLabel("")
        self.lbl_verdict.setWordWrap(True)
        self.lbl_verdict.setStyleSheet(
            "background:#1b1f27; border:1px solid #262b34; border-radius:5px;"
            "padding:8px; color:#c2cad6;")
        self.lbl_verdict.setVisible(False)
        lay.addWidget(self.lbl_verdict)

        lay.addStretch(1)

        row = QtWidgets.QHBoxLayout()
        self.btn_copy = QtWidgets.QPushButton("Copy report")
        self.btn_copy.setEnabled(False)
        self.btn_copy.clicked.connect(self.copy_report)
        row.addWidget(self.btn_copy)
        row.addStretch(1)
        lay.addLayout(row)
        return box

    def _build_table(self):
        box = QtWidgets.QGroupBox("Probes")
        lay = QtWidgets.QVBoxLayout(box)
        hint = QtWidgets.QLabel(
            "Each row is an independent measurement at a different point in "
            "the take. A clean trend is drift; scatter is a dropout or an edit.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        self.table = QtWidgets.QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["#", "Time", "Offset (ms)", "Δ fit (ms)", "Conf.", "Used"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QtWidgets.QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QTableWidget.SelectRows)
        self.table.setAlternatingRowColors(False)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        h.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        h.setSectionResizeMode(5, QtWidgets.QHeaderView.ResizeToContents)
        self.table.itemDoubleClicked.connect(self._on_probe_double_clicked)
        lay.addWidget(self.table, 1)
        return box

    def _build_transport(self):
        """Transport and mixer on the left, one tall Export on the right.

        The export folder used to live here as well as in the export dialog.
        Two places to set one thing is one too many, and the dialog is where
        the rest of the export decisions already are -- so the folder moved
        there and this section kept the controls you touch while listening.
        """
        box = QtWidgets.QGroupBox("Monitor and export")
        outer = QtWidgets.QHBoxLayout(box)
        outer.setSpacing(12)

        left = QtWidgets.QVBoxLayout()
        left.setSpacing(8)

        row = QtWidgets.QHBoxLayout()
        row.setSpacing(8)
        self.btn_play = QtWidgets.QPushButton("▶  Play")
        self._fix(self.btn_play, 96)
        self.btn_play.setEnabled(False)
        self.btn_play.clicked.connect(self.toggle_play)
        row.addWidget(self.btn_play)

        self.btn_stop = QtWidgets.QPushButton("■")
        self._fix(self.btn_stop, 38)
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.stop)
        row.addWidget(self.btn_stop)

        self.scrub = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.scrub.setRange(0, 1000)
        self.scrub.setEnabled(False)
        self.scrub.sliderMoved.connect(self._on_scrub)
        row.addWidget(self.scrub, 1)

        self.lbl_time = QtWidgets.QLabel("00:00.000 / 00:00.000")
        self.lbl_time.setObjectName("value")
        self._fix(self.lbl_time, 170)
        self.lbl_time.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        row.addWidget(self.lbl_time)
        left.addLayout(row)

        mix = QtWidgets.QHBoxLayout()
        mix.setSpacing(18)
        self.strip_a = ChannelStrip("A", "#4a9eff")
        self.strip_b = ChannelStrip("B", "#ffb347")
        for st in (self.strip_a, self.strip_b):
            st.changed.connect(self._apply_mixer)
            mix.addWidget(st, 1)
        left.addLayout(mix)

        outer.addLayout(left, 1)

        right = QtWidgets.QVBoxLayout()
        right.setSpacing(4)
        self.lbl_export_opts = QtWidgets.QLabel("")
        self.lbl_export_opts.setObjectName("hint")
        self.lbl_export_opts.setAlignment(QtCore.Qt.AlignHCenter
                                          | QtCore.Qt.AlignBottom)
        self.lbl_export_opts.setToolTip(
            "Set in the export dialog. Click Export aligned pair to change.")
        right.addWidget(self.lbl_export_opts)

        self.btn_export = QtWidgets.QPushButton("Export\naligned pair…")
        self.btn_export.setObjectName("exportTall")
        self.btn_export.setEnabled(False)
        self.btn_export.clicked.connect(self.export)
        self.btn_export.setSizePolicy(QtWidgets.QSizePolicy.Fixed,
                                      QtWidgets.QSizePolicy.Expanding)
        self._fix(self.btn_export, 168, None)
        right.addWidget(self.btn_export, 1)
        outer.addLayout(right)

        self._update_export_summary()
        return box

    def _update_export_summary(self):
        """Show what Export would actually do right now.

        Before the dialog has been opened once there are no stored choices,
        so this has to derive the same defaults the dialog will: just the
        track that gains silence, and no report.
        """
        o = self.export_opts
        if o:
            on = [w for w in ("A", "B") if o.get(w)]
            fmt = o.get("fmt", engine.DEFAULT_FORMAT)
            rep = o.get("write_report", False)
        elif self.report is not None:
            late = self.report.late_track
            on = ["A", "B"] if late == "none" else [late]
            fmt, rep = engine.DEFAULT_FORMAT, False
        else:
            on, fmt, rep = ["A", "B"], engine.DEFAULT_FORMAT, False
        tracks = " + ".join(on) or "—"
        self.lbl_export_opts.setText(
            f"{tracks} · {fmt} · " + ("report on" if rep else "no report"))

    def _export_default(self, which):
        """Only the track that actually changes is ticked by default.

        The early track comes out byte for byte identical to its source, so
        writing it again is usually a pointless copy of a very large file.
        The late track is the one carrying the silence, and that is the one
        you need.
        """
        if self.report is None:
            return True
        return which == self.report.late_track

    # -- state -------------------------------------------------------------

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.report is not None:
            self._render_view()

    def _on_files_changed(self):
        ready = bool(self.row_a.path and self.row_b.path)
        self.btn_analyze.setEnabled(ready)
        self._clear_results()
        if self.row_a.path and not self.out_dir:
            self.out_dir = os.path.join(os.path.dirname(self.row_a.path),
                                        "synced")

    def _clear_results(self):
        self.report = None
        self.envelopes.clear()
        self.lane_meta.clear()
        self.player.unload()
        self.wave.clear()
        self.table.setRowCount(0)
        self.lbl_offset.setText("—")
        self.lbl_action.setText(
            "Load two recordings of the same event and press Analyse sync.")
        self.lbl_verdict.setVisible(False)
        for v in self.stat.values():
            v.setText("—")
        for b in (self.btn_play, self.btn_stop, self.btn_export,
                  self.btn_copy):
            b.setEnabled(False)
        self.scrub.setEnabled(False)
        self.scrub.setValue(0)

    def swap(self):
        a, b = self.row_a.path, self.row_b.path
        self.row_a.set_path(b)
        self.row_b.set_path(a)

    def open_settings(self):
        dlg = SettingsDialog(self.opts, self)
        if dlg.exec():
            self.opts.update(dlg.values())
            self.statusBar().showMessage("Settings updated. Re-analyse to apply.")

    # -- analysis ----------------------------------------------------------

    def analyze(self):
        if not (self.row_a.path and self.row_b.path):
            return
        if os.path.abspath(self.row_a.path) == os.path.abspath(self.row_b.path):
            QtWidgets.QMessageBox.warning(
                self, APP_NAME, "Both slots point at the same file.")
            return
        self._clear_results()
        self.btn_analyze.setEnabled(False)
        self.progress.setValue(0)
        self.analyzer = AnalyzeWorker(self.row_a.path, self.row_b.path,
                                      dict(self.opts), self)
        self.analyzer.progressed.connect(self._on_progress)
        self.analyzer.finished_ok.connect(self._on_analyzed)
        self.analyzer.failed.connect(self._on_failed)
        self.analyzer.start()

    def _on_progress(self, frac, msg):
        self.progress.setValue(int(frac * 1000))
        self.progress.setFormat(msg)
        if msg:
            self.statusBar().showMessage(msg)

    def _on_failed(self, msg):
        self.btn_analyze.setEnabled(True)
        self.progress.setValue(0)
        self.progress.setFormat("")
        QtWidgets.QMessageBox.critical(self, APP_NAME, msg)
        self.statusBar().showMessage("Analysis failed.")

    def _on_analyzed(self, rep: engine.Report):
        self.report = rep
        self.btn_analyze.setEnabled(True)
        self.progress.setValue(1000)
        self.progress.setFormat("")

        QtCore.QTimer.singleShot(400, lambda: self.progress.setValue(0))
        self._show_report(rep)
        self._update_export_summary()
        self._fill_table(rep)
        self._load_monitor(rep)
        self._start_peaks(rep)

        ok = rep.status != "no_overlap"
        self.btn_export.setEnabled(ok)
        self.btn_copy.setEnabled(True)
        self.statusBar().showMessage(rep.verdict)

    def _show_report(self, rep):
        ms = rep.offset_seconds * 1000.0
        self.lbl_offset.setText(f"{ms:+,.3f} ms")

        if rep.status == "no_overlap":
            self.lbl_action.setText(
                "These two files do not line up. Nothing to export.")
        elif rep.late_track == "none":
            self.lbl_action.setText("The tracks already start together.")
        else:
            late = rep.late_track
            other = "B" if late == "A" else "A"
            name = (rep.a.name if late == "A" else rep.b.name)
            self.lbl_action.setText(
                f"Track {other} started first. Export prepends "
                f"<b>{rep.pad_seconds * 1000:,.3f} ms</b> of silence to track "
                f"{late} ({name}) so both drop onto the timeline at 00:00.")
        self.lbl_action.setTextFormat(QtCore.Qt.RichText)

        self.stat["drift"].setText(
            f"{rep.drift_ppm:+.2f} ppm  ({rep.drift_ms_per_hour():+.0f} ms/hour)"
            if abs(rep.drift_ppm) >= engine.DRIFT_NOISE_FLOOR_PPM
            else "none measurable")
        self.stat["conf"].setText(f"{rep.coarse_confidence:.2f}")
        self.stat["probes"].setText(
            f"{rep.n_good_probes} of {len(rep.probes)}"
            + (f"   (median {rep.median_confidence:.1f})"
               if rep.n_good_probes else ""))
        self.stat["scatter"].setText(f"±{rep.offset_scatter_ms:.2f} ms")
        self.stat["rate"].setText(f"{rep.analysis_rate:,} Hz")

        colour = STATUS_COLOUR.get(rep.status, "#8b95a4")
        text = rep.verdict
        for n in rep.notes:
            text += f"\n\n• {n}"
        self.lbl_verdict.setText(text)
        self.lbl_verdict.setStyleSheet(
            f"background:#1b1f27; border:1px solid {colour}; border-left:3px "
            f"solid {colour}; border-radius:5px; padding:8px; color:#c2cad6;")
        self.lbl_verdict.setVisible(True)

    def _fill_table(self, rep):
        self.table.setRowCount(len(rep.probes))
        good = [p for p in rep.probes if p.used and
                p.confidence > engine.GOOD_PROBE_CONFIDENCE]
        fit = None
        if len(good) >= 2:
            import numpy as np
            t = np.array([p.t_seconds for p in good])
            y = np.array([p.offset_ms for p in good])
            m, c = np.polyfit(t, y, 1)
            fit = (m, c)

        for r, p in enumerate(rep.probes):
            trusted = p.confidence > engine.GOOD_PROBE_CONFIDENCE
            delta = ("—" if fit is None
                     else f"{p.offset_ms - (fit[0] * p.t_seconds + fit[1]):+.2f}")
            cells = [str(p.index), _hms(p.t_seconds), f"{p.offset_ms:+,.3f}",
                     delta, f"{p.confidence:.2f}",
                     "yes" if (trusted and p.used) else "no"]
            for c, text in enumerate(cells):
                it = QtWidgets.QTableWidgetItem(text)
                if c:
                    it.setTextAlignment(QtCore.Qt.AlignRight
                                        | QtCore.Qt.AlignVCenter)
                if not trusted:
                    it.setForeground(QtGui.QColor("#e0555f"))
                elif not p.used:
                    it.setForeground(QtGui.QColor("#d9a441"))
                it.setData(QtCore.Qt.UserRole, p.t_seconds)
                self.table.setItem(r, c, it)

    def _on_probe_double_clicked(self, item):
        t = item.data(QtCore.Qt.UserRole)
        if t is None or not self.report:
            return
        # probe times are on B's timeline; move to the same instant on the
        # synced timeline so the ear lands where the number came from
        self.player.seek_seconds(t + self.report.pad_frames_for("B")
                                 / max(self.report.b.samplerate, 1))

    # -- monitor -----------------------------------------------------------

    def _load_monitor(self, rep):
        pad_a = rep.pad_seconds if rep.late_track == "A" else 0.0
        pad_b = rep.pad_seconds if rep.late_track == "B" else 0.0
        try:
            self.player.load(rep.a.path, rep.b.path, pad_a, pad_b)
        except Exception as exc:
            self.statusBar().showMessage(f"Monitor unavailable: {exc}")
            return
        self._apply_mixer()
        self.scrub.setEnabled(True)
        self.btn_play.setEnabled(HAVE_AUDIO)
        self.btn_stop.setEnabled(HAVE_AUDIO)
        self.wave.set_total(self.player.length / self.player.fs)

    def _lane_jobs(self, rep):
        pad_a = rep.pad_seconds if rep.late_track == "A" else 0.0
        pad_b = rep.pad_seconds if rep.late_track == "B" else 0.0
        return [("A", rep.a.path, rep.a.name, pad_a),
                ("B", rep.b.path, rep.b.name, pad_b)]

    def _start_peaks(self, rep=None):
        """Kick off the one full scan per track that feeds every later view."""
        rep = rep or self.report
        if rep is None:
            return
        if self.env_worker and self.env_worker.isRunning():
            self.env_worker.cancel()
            self.env_worker.wait(800)
        self.envelopes.clear()
        total = max(self.player.length / max(self.player.fs, 1), 0.001)
        self.env_worker = EnvelopeWorker(self._lane_jobs(rep), total, self)
        self.env_worker.ready.connect(self._on_envelope)
        self.env_worker.start()

    def _on_envelope(self, which, env, label, start):
        self.envelopes[which] = env
        self.lane_meta[which] = (label, start)
        self._render_view()

    def _buckets(self):
        # one column per pixel; no point computing more than can be drawn
        return max(320, min(self.wave.width(), 4096))

    def _render_view(self):
        """Draw the current view from the cache. Cheap enough to call freely."""
        if not self.envelopes:
            return
        n = self._buckets()
        span = self.wave.view_span or self.wave.total_seconds
        if span <= 0:
            return
        approximate = False
        for which, env in self.envelopes.items():
            mn, mx, fl = env.derive(n, self.wave.view_start, span)
            label, start = self.lane_meta.get(which, ("", 0.0))
            self.wave.set_lane(which, mn, mx, fl, label, start)
            if not env.can_serve(n, span):
                approximate = True
        # zoomed in past what the cache holds: schedule the exact version
        if approximate:
            self._redraw.start(180)
        else:
            self._redraw.stop()

    def _refine_view(self):
        """Read the files for the exact view. Only for deep zooms."""
        if self.report is None or not self.envelopes:
            return
        if self.refine_worker and self.refine_worker.isRunning():
            self.refine_worker.cancel()
            self.refine_worker.wait(400)
        self._view_token += 1
        span = self.wave.view_span or self.wave.total_seconds
        self.refine_worker = RefineWorker(
            self._lane_jobs(self.report), self._buckets(),
            self.wave.view_start, span, self._view_token, self)
        self.refine_worker.lane_ready.connect(self._on_refined)
        self.refine_worker.start()

    def _on_refined(self, which, mn, mx, fl, label, start, token):
        # the view may have moved on while this was being read
        if token == self._view_token:
            self.wave.set_lane(which, mn, mx, fl, label, start)

    def _on_view_changed(self, start, span):
        self._render_view()

    def _apply_mixer(self):
        self.player.set_gain("A", self.strip_a.gain())
        self.player.set_gain("B", self.strip_b.gain())
        self.player.set_mute("A", self.strip_a.mute.isChecked())
        self.player.set_mute("B", self.strip_b.mute.isChecked())
        solo = None
        if self.strip_a.solo.isChecked() and not self.strip_b.solo.isChecked():
            solo = "A"
        elif self.strip_b.solo.isChecked() and not self.strip_a.solo.isChecked():
            solo = "B"
        self.player.set_solo(solo)

    def toggle_play(self):
        if self.player.playing:
            self.player.pause()
        else:
            if not self.player.play() and self.player.error:
                QtWidgets.QMessageBox.warning(self, APP_NAME, self.player.error)
        self._sync_play_button()

    def stop(self):
        self.player.stop()
        self._sync_play_button()

    def _sync_play_button(self):
        self.btn_play.setText("‖  Pause" if self.player.playing
                              else "▶  Play")

    def _on_scrub(self, v):
        if self.player.length:
            self.player.seek(int(v / 1000.0 * self.player.length))

    def _on_seek_wave(self, seconds):
        self.player.seek_seconds(seconds)

    def _on_tick(self):
        if not self.player.sources:
            return
        fs = max(self.player.fs, 1)
        pos = self.player.position / fs
        total = self.player.length / fs
        self.wave.set_playhead(pos)
        self.lbl_time.setText(f"{_hms(pos)} / {_hms(total)}")
        if not self.scrub.isSliderDown() and self.player.length:
            self.scrub.setValue(int(pos / max(total, 1e-9) * 1000))
        self.strip_a.set_peak(self.player.sources["A"].peak
                              if self.player.playing else 0.0)
        self.strip_b.set_peak(self.player.sources["B"].peak
                              if self.player.playing else 0.0)
        self._sync_play_button()

    # -- export ------------------------------------------------------------

    def export(self):
        if not self.report:
            return
        out = self.out_dir.strip() or os.path.join(
            os.path.dirname(self.report.a.path), "synced")

        if self.report.status in ("scattered", "offset_only", "no_overlap"):
            r = QtWidgets.QMessageBox.question(
                self, APP_NAME,
                f"{self.report.verdict}\n\nExport a single fixed offset anyway?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
            if r != QtWidgets.QMessageBox.Yes:
                return

        dlg = ExportDialog(self.report, out, self.export_opts, self)
        if not dlg.exec():
            return
        vals = dlg.values()
        out = vals.pop("out_dir")
        # remember the choices so the dialog opens on them next time
        self.export_opts = {
            "A": "A" in vals["tracks"], "B": "B" in vals["tracks"],
            "fmt": vals["fmt"], "depth": vals["depth"],
            "write_report": vals["write_report"],
        }
        self.out_dir = out
        self._update_export_summary()

        self.btn_export.setEnabled(False)
        self.progress.setValue(0)
        self.exporter = ExportWorker(self.report, out, vals, self)
        n = max(len(vals["tracks"]), 1)
        order = {w: i for i, w in enumerate(vals["tracks"])}
        self.exporter.progressed.connect(
            lambda w, p: self._on_progress((order.get(w, 0) + p) / n,
                                           f"Writing track {w}"))
        self.exporter.finished_ok.connect(self._on_exported)
        self.exporter.failed.connect(self._on_export_failed)
        self.exporter.start()

    def _on_export_failed(self, msg):
        self.btn_export.setEnabled(True)
        self.progress.setValue(0)
        QtWidgets.QMessageBox.critical(self, APP_NAME, msg)

    def _on_exported(self, res):
        self.btn_export.setEnabled(True)
        self.progress.setFormat("")
        QtCore.QTimer.singleShot(400, lambda: self.progress.setValue(0))
        out_dir = os.path.dirname(res["outputs"][0]["path"])

        lines = []
        for o in res["outputs"]:
            pad = o["pad_seconds"] * 1000
            lines.append(
                f"{os.path.basename(o['path'])}\n"
                f"    {o['samplerate']} Hz · {o['channels']} ch · "
                f"{o['format']}/{o['subtype']}"
                + ("  (bit-identical)" if o.get("bit_identical") else "")
                + "\n    "
                + (f"{pad:,.3f} ms of silence prepended" if pad
                   else "unchanged, sample for sample"))

        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle(APP_NAME)
        n = len(res["outputs"])
        box.setText(
            ("Exported. Drop both files on your DAW timeline at 00:00:00 — "
             "no nudging needed." if n > 1 else
             "Exported. Drop this file on your DAW timeline at 00:00:00, "
             "against its partner — no nudging needed."))
        box.setInformativeText("\n\n".join(lines))
        box.setStandardButtons(QtWidgets.QMessageBox.Ok)
        open_btn = box.addButton("Open folder", QtWidgets.QMessageBox.ActionRole)
        box.exec()
        if box.clickedButton() is open_btn:
            _open_folder(out_dir)
        self.statusBar().showMessage(f"Exported to {out_dir}")

    def copy_report(self):
        if self.report:
            QtWidgets.QApplication.clipboard().setText(
                engine.format_report(self.report))
            self.statusBar().showMessage("Report copied to the clipboard.")

    # -- shutdown ----------------------------------------------------------

    def closeEvent(self, e):
        self.tick.stop()
        for w in (self.env_worker, self.refine_worker):
            if w is not None and w.isRunning():
                w.cancel()
        for w in (self.env_worker, self.refine_worker, self.analyzer,
                  self.exporter):
            if w is not None and w.isRunning():
                w.wait(3000)
        try:
            self.player.unload()
        except Exception:
            pass
        super().closeEvent(e)


def _unique_sequences(seqs):
    """Drop duplicate key sequences, keeping order.

    Qt treats one chord bound twice to the same action as ambiguous and
    refuses to fire it, so a convenience alias that happens to equal an
    explicit binding silently kills the shortcut.
    """
    out, seen = [], set()
    for s in seqs:
        key = s.toString()
        if key and key not in seen:
            seen.add(key)
            out.append(s)
    return out


def resource_path(*parts):
    """Locate a bundled asset in a dev checkout and in a PyInstaller build."""
    base = getattr(sys, "_MEIPASS", None)
    if base is None:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *parts)


def app_icon():
    # .icns first on macOS, .ico first on Windows; logo.png works anywhere
    names = (("TrackSync.icns", "TrackSync.ico", "logo.png")
             if sys.platform == "darwin"
             else ("TrackSync.ico", "TrackSync.icns", "logo.png"))
    for name in names:
        p = resource_path("assets", name)
        if os.path.exists(p):
            ic = QtGui.QIcon(p)
            if not ic.isNull():
                return ic
    return QtGui.QIcon()


def _open_folder(path):
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)                       # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception:
        pass


def _hms(t):
    t = max(0.0, float(t))
    m, s = divmod(t, 60)
    h, m = divmod(int(m), 60)
    if h:
        return f"{h}:{m:02d}:{s:06.3f}"
    return f"{m:02d}:{s:06.3f}"


def main():
    QtCore.QCoreApplication.setAttribute(
        QtCore.Qt.AA_DontUseNativeMenuBar, False)
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    app.setStyle("Fusion")
    app.setWindowIcon(app_icon())
    if sys.platform.startswith("win"):
        # without an explicit AppUserModelID Windows groups the taskbar button
        # under python.exe and shows its icon instead of ours
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "TrackSync.TrackSync.1")
        except Exception:
            pass
    win = MainWindow()
    win.show()
    return app.exec()
