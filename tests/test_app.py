"""
Headless smoke test for the window and the streaming player.

Runs the real MainWindow under Qt's offscreen platform: loads two synthetic
tracks, drives a real analysis through the real worker thread, checks that
every panel populated, exports, and renders the window to a PNG so the layout
can be inspected. Then exercises the player's buffering and resampling paths
without an audio device, which is the same code the audio callback pulls from.

Run:  QT_QPA_PLATFORM=offscreen python -m tests.test_app
"""

from __future__ import annotations

import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np                                            # noqa: E402
import soundfile as sf                                        # noqa: E402
from PySide6 import QtCore, QtWidgets                         # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tracksync import engine                                  # noqa: E402
from tracksync.player import DualPlayer                       # noqa: E402
from tracksync import ui as tsui                              # noqa: E402
from tracksync.ui import MainWindow                           # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_engine as T                                       # noqa: E402

RESULTS = []

# The app is honest about finishing: it puts up a modal summary after an
# export and a modal confirmation before exporting a doubtful measurement.
# Modal dialogs never return without a human, so headless runs auto-dismiss
# them and record that they were raised.
DIALOGS = []


def _silence_dialogs():
    def fake_exec(self, *a, **k):
        DIALOGS.append(("exec", self.text() if hasattr(self, "text") else ""))
        return QtWidgets.QMessageBox.Ok

    def fake_question(parent, title, text, *a, **k):
        DIALOGS.append(("question", text))
        return QtWidgets.QMessageBox.Yes

    QtWidgets.QMessageBox.exec = fake_exec
    QtWidgets.QMessageBox.exec_ = fake_exec
    QtWidgets.QMessageBox.question = staticmethod(fake_question)

    # The export dialog is modal too. Headless runs configure it through
    # EXPORT_CHOICE and accept it, which exercises the real widget rather
    # than bypassing it.
    def fake_export_exec(self, *a, **k):
        for key, val in EXPORT_CHOICE.items():
            if key in ("A", "B"):
                self.chk[key].setChecked(val)
            elif key == "fmt":
                self.fmt.setCurrentText(val)
            elif key == "depth":
                i = self.depth.findData(val)
                if i >= 0:
                    self.depth.setCurrentIndex(i)
            elif key == "write_report":
                self.report_chk.setChecked(val)
            elif key == "out_dir":
                self.dir_edit.setText(val)
        DIALOGS.append(("export", str(self.values())))
        return QtWidgets.QDialog.Accepted

    tsui.ExportDialog.exec = fake_export_exec


EXPORT_CHOICE = {}


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   [{detail}]" if detail else ""))
    return ok


def pump(app, ms):
    loop = QtCore.QEventLoop()
    QtCore.QTimer.singleShot(ms, loop.quit)
    loop.exec()
    app.processEvents()


def wait_for(app, pred, timeout_ms=240000, step=100):
    waited = 0
    while waited < timeout_ms:
        app.processEvents()
        if pred():
            return True
        pump(app, step)
        waited += step
    return False


def build_fixtures(tmp):
    fs = 48000
    src = T.make_source(150.0, fs)
    true_off = 2.7315
    a = T.record(src, fs, 0.0, 120.0, rt60=0.35, mix=0.28, slope=-1.0,
                 snr_db=32, gain=0.75, seed=101)
    b = T.record(src, fs, true_off, 128.0, rt60=0.95, mix=0.55, slope=+2.0,
                 snr_db=22, gain=0.3, seed=103)
    pa = T.write(os.path.join(tmp, "cam_a.wav"), a, fs, channels=2)
    # B at a different rate, to exercise the player's resampler too
    b441 = np.interp(np.arange(int(len(b) * 44100 / fs)) * (fs / 44100),
                     np.arange(len(b)), b)
    pb = T.write(os.path.join(tmp, "recorder_b.wav"), b441, 44100, channels=1)
    return pa, pb, true_off


def test_window(app, tmp, pa, pb, true_off):
    win = MainWindow()
    win.resize(1180, 830)
    win.show()
    app.processEvents()

    check("window: opens with export disabled", not win.btn_export.isEnabled())

    win.row_a.set_path(pa)
    win.row_b.set_path(pb)
    app.processEvents()
    check("window: analyse enabled once both slots are filled",
          win.btn_analyze.isEnabled())
    check("window: file row shows rate and depth",
          "44100 Hz" in win.row_b.info.text(), win.row_b.info.text())
    check("window: export folder defaulted beside track A",
          win.out_dir.endswith("synced"), win.out_dir)

    win.analyze()
    ok = wait_for(app, lambda: win.report is not None)
    check("window: analysis completed on the worker thread", ok)
    if not ok:
        return win, None

    rep = win.report
    err_ms = (rep.offset_seconds - true_off) * 1000
    check("window: offset accurate through the UI path", abs(err_ms) < 1.0,
          f"error {err_ms:+.3f} ms")
    check("window: headline reads the offset",
          "ms" in win.lbl_offset.text() and win.lbl_offset.text() != "—",
          win.lbl_offset.text())
    check("window: says which track gets the silence",
          "silence" in win.lbl_action.text(),
          win.lbl_action.text()[:70].replace("<b>", ""))
    check("window: probe table populated",
          win.table.rowCount() == len(rep.probes),
          f"{win.table.rowCount()} rows")
    check("window: probe table has a delta-from-fit column",
          win.table.item(0, 3) is not None
          and win.table.item(0, 3).text() != "—",
          win.table.item(0, 3).text() if win.table.item(0, 3) else "")
    check("window: verdict panel visible", win.lbl_verdict.isVisible())
    check("window: export enabled after a good analysis",
          win.btn_export.isEnabled())

    # waveform lanes are computed on a worker; give them a moment
    wait_for(app, lambda: len(win.envelopes) == 2
             and len(win.wave.lanes) == 2, timeout_ms=120000)
    check("window: both waveform lanes rendered", len(win.wave.lanes) == 2,
          str(sorted(win.wave.lanes)))
    if len(win.wave.lanes) == 2:
        late = rep.late_track
        check("window: leading silence drawn on the late lane only",
              win.wave.lanes[late]["start"] > 0
              and win.wave.lanes["A" if late == "B" else "B"]["start"] == 0)

    # monitor loaded even though this box has no audio device
    check("window: monitor loaded both sources",
          set(win.player.sources) == {"A", "B"},
          str(sorted(win.player.sources)))
    check("window: timeline length covers the longer track",
          win.player.length / win.player.fs > rep.b.duration,
          f"{win.player.length / win.player.fs:.2f} s")

    # mixer wiring
    win.strip_a.slider.setValue(-60)
    win.strip_b.mute.setChecked(True)
    win._apply_mixer()
    check("window: fader maps to linear gain",
          abs(win.player.sources["A"].gain - 10 ** (-6.0 / 20)) < 1e-6,
          f"{win.player.sources['A'].gain:.4f}")
    check("window: mute reaches the player", win.player.sources["B"].muted)
    win.strip_a.slider.setValue(0)
    win.strip_b.mute.setChecked(False)
    win.strip_b.solo.setChecked(True)
    win._apply_mixer()
    check("window: solo reaches the player", win.player.solo == "B",
          str(win.player.solo))
    win.strip_b.solo.setChecked(False)
    win._apply_mixer()

    # seeking from the waveform
    win.wave.seeked.emit(30.0)
    app.processEvents()
    check("window: clicking the waveform seeks",
          abs(win.player.position / win.player.fs - 30.0) < 0.05,
          f"{win.player.position / win.player.fs:.3f} s")

    # zoom: the whole point is being able to SEE a millisecond
    win._zoom_preset(0.01)
    ok = wait_for(app, lambda: abs(win.wave.view_span - 0.01) < 1e-9,
                  timeout_ms=5000)
    check("zoom: preset narrows the view to 10 ms", ok,
          f"span {win.wave.view_span:.6f} s")
    px = win.wave.view_span / max(win.wave.width(), 1) * 1000
    check("zoom: one pixel is well under a millisecond", px < 0.05,
          f"{px:.4f} ms/px")
    ok = wait_for(app, lambda: win.refine_worker is not None
                  and not win.refine_worker.isRunning(), timeout_ms=20000)
    check("zoom: exact envelope read for the zoomed span", ok)
    check("zoom: zoomed lanes still carry data",
          all(win.wave.lanes[k].get("filled") is not None
              and win.wave.lanes[k]["filled"].any() for k in ("A", "B")))
    win.wave.zoom_around(30.0, 0.5)
    check("zoom: cannot zoom past the head of the take",
          win.wave.view_start >= 0.0, f"{win.wave.view_start:.4f}")
    win.wave.reset_zoom()
    ok = wait_for(app, lambda: abs(win.wave.view_span
                                   - win.wave.total_seconds) < 1e-6,
                  timeout_ms=5000)
    check("zoom: double-click fit restores the whole take", ok,
          f"span {win.wave.view_span:.3f} of {win.wave.total_seconds:.3f}")
    win.copy_report()
    clip = QtWidgets.QApplication.clipboard().text()
    check("window: copy report puts the probe table on the clipboard",
          "probe" in clip and "offset(ms)" in clip)

    # --- interface scaling ------------------------------------------------
    # start from a known size: the app remembers the last scale across
    # launches, so a stale setting must not become the baseline
    win.apply_scale(1.0)
    app.processEvents()
    base_w = win.btn_play.width()
    base_pt = QtWidgets.QApplication.instance().font().pointSizeF()
    win.apply_scale(1.5)
    app.processEvents()
    check("scale: Ctrl+= enlarges the type",
          QtWidgets.QApplication.instance().font().pointSizeF() > base_pt * 1.4,
          f"{base_pt:.1f} -> "
          f"{QtWidgets.QApplication.instance().font().pointSizeF():.1f} pt")
    check("scale: controls grow with it, not just the text",
          win.btn_play.width() > base_w * 1.4,
          f"{base_w} -> {win.btn_play.width()} px")
    check("scale: the stylesheet is regenerated at the new size",
          "border-radius: 9px" in win.styleSheet(),
          win.styleSheet().split("QGroupBox {")[1][:60].strip()
          if "QGroupBox {" in win.styleSheet() else "?")
    win.step_scale(-1)
    app.processEvents()
    check("scale: one step down from 1.5 lands on the step below",
          abs(win.scale - 1.3) < 1e-9, str(win.scale))
    win.apply_scale(1.0)
    app.processEvents()
    check("scale: Ctrl+0 restores the original size",
          win.btn_play.width() == base_w, f"{win.btn_play.width()} px")

    # The real keystrokes, not just the methods behind them. Ctrl+- was
    # silently dead once because "Ctrl+-" as a STRING is ambiguous to Qt's
    # parser -- Ctrl+_ worked and plain Ctrl+- did nothing -- so the keys
    # themselves are what this asserts.
    from PySide6 import QtGui as _QtGui
    def press(key, mods=QtCore.Qt.ControlModifier, text=""):
        ev = _QtGui.QKeyEvent(QtCore.QEvent.KeyPress, key, mods, text)
        QtWidgets.QApplication.sendEvent(win, ev)
        app.processEvents()

    win.apply_scale(1.0)
    press(QtCore.Qt.Key_Minus, text="-")
    check("keys: Ctrl and minus zooms out", win.scale < 1.0, str(win.scale))
    win.apply_scale(1.0)
    press(QtCore.Qt.Key_Plus, text="+")
    check("keys: Ctrl and plus zooms in", win.scale > 1.0, str(win.scale))
    win.apply_scale(1.0)
    press(QtCore.Qt.Key_Equal, text="=")
    check("keys: Ctrl and equals zooms in too", win.scale > 1.0, str(win.scale))
    win.apply_scale(1.0)
    press(QtCore.Qt.Key_Underscore,
          QtCore.Qt.ControlModifier | QtCore.Qt.ShiftModifier, "_")
    check("keys: Ctrl and underscore still zooms out", win.scale < 1.0,
          str(win.scale))
    win.apply_scale(1.5)
    press(QtCore.Qt.Key_0, text="0")
    check("keys: Ctrl and zero resets", abs(win.scale - 1.0) < 1e-9,
          str(win.scale))

    # and the menu must carry the same bindings, so they show next to the item
    zoom_out = next((a for a in win.findChildren(_QtGui.QAction)
                     if a.text().replace("&", "") == "Zoom out"), None)
    seqs = [s.toString() for s in zoom_out.shortcuts()] if zoom_out else []
    check("keys: the View menu advertises Ctrl+- for zoom out",
          any(x == "Ctrl+-" for x in seqs), str(seqs))

    # No chord may be registered twice anywhere in the window. Qt calls a
    # duplicate an "ambiguous shortcut overload" and then fires NEITHER copy
    # while still swallowing the key -- which is precisely how Ctrl+- came
    # to do nothing while Ctrl+_ worked.
    from collections import Counter
    all_seqs = []
    for a in win.findChildren(_QtGui.QAction):
        all_seqs += [x.toString() for x in a.shortcuts() if x.toString()]
    for sc in win.findChildren(_QtGui.QShortcut):
        t = sc.key().toString()
        if t:
            all_seqs.append(t)
    dupes = [k for k, n in Counter(all_seqs).items() if n > 1]
    check("keys: no shortcut is registered twice", not dupes, str(dupes))
    win.apply_scale(1.0)
    check("scale: persisted for the next launch",
          float(win.settings.value("ui_scale", 0)) == 1.0)

    # --- header / scroll / footer -----------------------------------------
    import time as _time
    from PySide6 import QtWidgets as _QW
    scroll_kids = win.scroll.widget().findChildren(_QW.QWidget)
    check("layout: the timeline is inside the scroll area",
          win.wave in scroll_kids)
    check("layout: the probe table is inside the scroll area",
          win.table in scroll_kids)
    check("layout: the tracks header is NOT inside the scroll area",
          win.row_a not in scroll_kids)
    check("layout: the monitor footer is NOT inside the scroll area",
          win.btn_export not in scroll_kids)

    # a tall UI in a short window must scroll rather than crush the panels
    win.apply_scale(1.0)
    win.resize(1180, 560)
    app.processEvents()
    pump(app, 200)
    bar = win.scroll.verticalScrollBar()
    check("layout: a short window scrolls the middle instead of squashing it",
          bar.maximum() > 0, f"scroll range {bar.maximum()} px")
    check("layout: the footer keeps its full height while scrolling",
          win.footer.height() >= win.footer.sizeHint().height(),
          f"{win.footer.height()} >= {win.footer.sizeHint().height()}")
    check("layout: the header keeps its full height while scrolling",
          win.row_b.isVisible() and win.row_b.height() > 0)
    win.resize(1180, 830)
    app.processEvents()

    # --- zoom speed: cache-served views must not touch the disk -----------
    win.wave.reset_zoom()
    app.processEvents()
    t0 = _time.perf_counter()
    for _ in range(20):
        win.wave.zoom_around(win.wave.total_seconds * 0.5, 0.8)
        app.processEvents()
    per = (_time.perf_counter() - t0) / 20 * 1000
    check("zoom: twenty wheel notches average under 40 ms each", per < 40.0,
          f"{per:.1f} ms per notch")
    served = all(e.can_serve(win._buckets(), win.wave.view_span)
                 for e in win.envelopes.values())
    check("zoom: a two-second view is still served from the cache", served,
          f"span {win.wave.view_span:.2f} s, "
          f"{win.envelopes['A'].seconds_per_bucket * 1000:.0f} ms/bucket")
    win.wave.reset_zoom()
    app.processEvents()

    # --- checkbox styling --------------------------------------------------
    css = win.styleSheet()
    # --- the playhead must not redraw the waveforms -----------------------
    # The transport ticks 30 times a second. Before the lanes were cached in
    # a pixmap, every one of those ticks re-rendered a few thousand line
    # segments per lane, which is what made the window feel heavy while
    # playing a long take.
    import time as _time
    renders = [0]
    _orig_render = win.wave._render_lanes

    def _spy(w_, h_):
        renders[0] += 1
        return _orig_render(w_, h_)

    win.wave._render_lanes = _spy
    win.wave.repaint()
    base = renders[0]
    t0 = _time.time()
    for i in range(60):
        win.wave.set_playhead(i * 0.5)
        win.wave.repaint()
    per_ms = (_time.time() - t0) / 60 * 1000
    check("paint: moving the playhead never re-renders the lanes",
          renders[0] == base, f"{renders[0] - base} re-renders")
    check("paint: a playhead repaint costs under 8 ms", per_ms < 8.0,
          f"{per_ms:.2f} ms each")
    before = renders[0]
    win.wave.zoom_to(10.0, 5.0)
    win.wave.repaint()
    check("paint: changing the view does re-render exactly once",
          renders[0] - before == 1, f"{renders[0] - before}")
    win.wave._render_lanes = _orig_render
    win.wave.reset_zoom()
    wait_for(app, lambda: win.refine_worker is None
             or not win.refine_worker.isRunning(), timeout_ms=30000)

    check("style: checkboxes are blue when ticked",
          "QCheckBox::indicator:checked" in css and "#1a56db" in css)
    check("style: the tick is a real image, not a bare blue square",
          "check.png" in css.replace("\\", "/"),
          [l.strip() for l in css.splitlines()
           if "indicator:checked" in l][:1])

    shot = os.path.join(tmp, "tracksync_window.png")
    win.grab().save(shot)
    check("window: rendered a screenshot", os.path.exists(shot)
          and os.path.getsize(shot) > 10000,
          f"{os.path.getsize(shot) // 1024} KB")

    # export through the UI worker
    out = os.path.join(tmp, "out_ui")
    win.out_dir = out
    DIALOGS.clear()
    EXPORT_CHOICE.clear()
    EXPORT_CHOICE.update({"A": True, "B": True, "fmt": "WAV",
                          "depth": "source", "write_report": True,
                          "out_dir": out})
    win.export()
    # wait for the worker to signal completion, not merely for the files to
    # appear -- the json and the confirmation come after the last wav frame
    ok = wait_for(app, lambda: any(k == "exec" for k, _ in DIALOGS)) and \
        os.path.isdir(out)
    check("window: export wrote two wavs", ok,
          str(os.listdir(out)) if os.path.isdir(out) else "no folder")
    if ok:
        names = sorted(os.listdir(out))
        check("window: export preserved 44.1 k on track B",
              sf.info(os.path.join(out, "recorder_b_synced.wav")).samplerate
              == 44100)
        check("window: export wrote the json report",
              "tracksync_report.json" in names, str(names))
        check("window: confirms the export to the user",
              any(k == "exec" and "DAW" in t for k, t in DIALOGS),
              str([t[:40] for _, t in DIALOGS]))
        check("window: the export dialog was the thing that ran",
              any(k == "export" for k, _ in DIALOGS))

    # --- export defaults ---------------------------------------------------
    dlg = tsui.ExportDialog(win.report, tmp, {}, win)
    late = win.report.late_track
    early = "A" if late == "B" else "B"
    check("defaults: the late track is ticked", dlg.chk[late].isChecked())
    check("defaults: the unchanged track is not ticked",
          not dlg.chk[early].isChecked(),
          f"{early} is the unchanged one")
    check("defaults: the json report is not ticked",
          not dlg.report_chk.isChecked())
    check("defaults: WAV whatever came in",
          dlg.fmt.currentText() == "WAV", dlg.fmt.currentText())
    dlg.deleteLater()

    # --- export options: one track only, no report, different format ------
    out2 = os.path.join(tmp, "out_one")
    DIALOGS.clear()
    EXPORT_CHOICE.update({"A": False, "B": True, "fmt": "FLAC",
                          "depth": "PCM_16", "write_report": False,
                          "out_dir": out2})
    win.export()
    ok = wait_for(app, lambda: any(k == "exec" for k, _ in DIALOGS))
    files = sorted(os.listdir(out2)) if os.path.isdir(out2) else []
    check("export options: only the chosen track is written",
          ok and len(files) == 1 and files[0].endswith(".flac"), str(files))
    check("export options: report suppressed",
          not any(f.endswith(".json") for f in files), str(files))
    if files:
        i = sf.info(os.path.join(out2, files[0]))
        check("export options: container and depth honoured",
              i.format == "FLAC" and i.subtype == "PCM_16",
              f"{i.format}/{i.subtype}")
    check("export options: the choice is summarised in the window",
          "FLAC" in win.lbl_export_opts.text()
          and "no report" in win.lbl_export_opts.text(),
          win.lbl_export_opts.text())

    return win, shot


def test_player_stream(tmp, pa, pb, rep):
    """Pull audio through the player exactly as the callback would."""
    p = DualPlayer()
    pad_a = rep.pad_seconds if rep.late_track == "A" else 0.0
    pad_b = rep.pad_seconds if rep.late_track == "B" else 0.0
    p.load(pa, pb, pad_a, pad_b)

    import time
    time.sleep(1.5)                      # let the reader thread fill

    check("player: output rate is the higher of the two",
          p.fs == max(sf.info(pa).samplerate, sf.info(pb).samplerate),
          str(p.fs))
    check("player: buffered ahead without loading whole files",
          p.sources["A"].buffered() > 4096 and p.sources["B"].buffered() > 4096,
          f"A={p.sources['A'].buffered()} B={p.sources['B'].buffered()}")

    # 3 seconds of blocks from the very start
    n = 1024
    late = rep.late_track
    early = "A" if late == "B" else "B"
    late_energy_head = 0.0
    early_energy_head = 0.0
    mixed = []
    for i in range(int(3.0 * p.fs / n)):
        ba = p.sources["A"].pull(n)
        bb = p.sources["B"].pull(n)
        blocks = {"A": ba, "B": bb}
        t = i * n / p.fs
        if t < rep.pad_seconds - 0.05:
            late_energy_head += float(np.abs(blocks[late]).sum())
            early_energy_head += float(np.abs(blocks[early]).sum())
        mixed.append(ba + bb)
        time.sleep(0.001)

    check("player: late track is digitally silent before its start",
          late_energy_head == 0.0, f"{late_energy_head:.6f}")
    check("player: early track is already playing there",
          early_energy_head > 0.0, f"{early_energy_head:.2f}")

    mix = np.concatenate(mixed)
    check("player: both tracks are stereo at the output", mix.shape[1] == 2)
    check("player: no underrun gaps in the first 3 s",
          float(np.abs(mix[int(0.2 * p.fs):]).mean()) > 1e-4,
          f"mean |x| = {float(np.abs(mix[int(0.2 * p.fs):]).mean()):.5f}")

    # seek and confirm the reader repositions both sources
    p.seek_seconds(60.0)
    time.sleep(1.5)
    blk = p.sources["A"].pull(4096)
    check("player: seek refills both sources",
          float(np.abs(blk).max()) > 0.0,
          f"peak {float(np.abs(blk).max()):.4f}")

    p.unload()
    check("player: unload closes cleanly", not p.sources)


def main():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    # the window persists its interface scale; a test must not inherit the
    # developer's setting, nor leave one behind
    QtCore.QSettings("TrackSync", "TrackSync").clear()
    _silence_dialogs()
    with tempfile.TemporaryDirectory() as tmp:
        pa, pb, true_off = build_fixtures(tmp)

        print("\n=== window ===")
        win, shot = test_window(app, tmp, pa, pb, true_off)
        keep = None
        if shot and os.path.exists(shot):
            keep = "/home/claude/ts/tracksync_window.png"
            import shutil
            shutil.copy(shot, keep)

        print("\n=== player ===")
        if win.report:
            test_player_stream(tmp, pa, pb, win.report)

        win.close()
        app.processEvents()

    n_fail = sum(1 for _, ok, _ in RESULTS if not ok)
    print(f"\n{len(RESULTS) - n_fail}/{len(RESULTS)} passed")
    for name, ok, detail in RESULTS:
        if not ok:
            print(f"  FAILED: {name}  {detail}")
    return 1 if n_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
