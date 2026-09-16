"""
player.py -- streaming two-track monitor.

Plays both recordings simultaneously on the SYNCED timeline, with independent
gain, mute and solo, so the alignment can be judged by ear before anything is
written. Nothing is loaded into memory whole: a reader thread keeps a couple
of seconds buffered ahead of the audio callback, which means a pair of
hour-long 24-bit files costs a few megabytes rather than a few gigabytes.

The synced timeline is the thing the exported files will live on: track X
starts at `pad_frames[X]`, which is exactly the silence export_synced() will
prepend. So what you hear is what you get.
"""

from __future__ import annotations

import threading

import numpy as np
import soxr

from . import audiofile

try:
    import sounddevice as sd
    HAVE_AUDIO = True
    AUDIO_ERROR = ""
except Exception as exc:                       # no PortAudio, no output device
    sd = None
    HAVE_AUDIO = False
    AUDIO_ERROR = str(exc)


BLOCK = 1024
BUFFER_SECONDS = 2.0
READ_CHUNK = 8192


class _Source:
    """One track, read on demand, resampled to the output rate, in stereo."""

    def __init__(self, path, fs_out, pad_frames):
        self.path = str(path)
        info = audiofile.info(self.path)
        self.fs_native = int(info.samplerate)
        self.fs_out = int(fs_out)
        self.channels = int(info.channels)
        self.native_frames = int(info.frames)
        self.pad = int(pad_frames)

        # length on the synced timeline
        self.length = self.pad + int(round(self.native_frames
                                           * self.fs_out / self.fs_native))

        self._file = audiofile.open_audio(self.path)
        self._resampler = None
        self._buf = []            # list of (n,2) float32
        self._buflen = 0
        self._read_pos = 0        # next synced-timeline frame the reader will produce
        self._eof = False
        self._lock = threading.Lock()

        self.gain = 1.0
        self.muted = False
        self.peak = 0.0           # last block's peak, for a meter

        self._reset_reader(0)

    # -- reader side (background thread) ----------------------------------

    def _reset_reader(self, pos):
        with self._lock:
            self._buf.clear()
            self._buflen = 0
            self._read_pos = int(pos)
            self._eof = False
            if self.fs_native != self.fs_out:
                self._resampler = soxr.ResampleStream(
                    self.fs_native, self.fs_out, self.channels,
                    dtype="float32", quality="MQ")
            else:
                self._resampler = None
            # position the file at the corresponding native frame
            native = max(0, int(round((pos - self.pad)
                                      * self.fs_native / self.fs_out)))
            native = min(native, self.native_frames)
            try:
                self._file.seek(native)
            except Exception:
                pass

    def _produce(self):
        """Append one chunk to the buffer. Returns False at end of track."""
        if self._eof:
            return False

        # still inside the leading silence?
        with self._lock:
            pos = self._read_pos + self._buflen
        if pos < self.pad:
            n = min(READ_CHUNK, self.pad - pos)
            self._append(np.zeros((n, 2), np.float32))
            return True

        x = self._file.read(READ_CHUNK)
        last = x.shape[0] < READ_CHUNK
        if x.shape[0] == 0 and not last:
            return False

        if self._resampler is not None:
            x = self._resampler.resample_chunk(x, last=last)
        if x.shape[0]:
            self._append(_to_stereo(x))
        if last:
            self._eof = True
            return False
        return True

    def _append(self, block):
        with self._lock:
            self._buf.append(block)
            self._buflen += block.shape[0]

    def buffered(self):
        with self._lock:
            return self._buflen

    def finished(self):
        return self._eof and self.buffered() == 0

    # -- callback side (audio thread; never touches the disk) --------------

    def pull(self, n):
        """Take n frames of stereo. Zero-fills on underrun or past the end."""
        out = np.zeros((n, 2), np.float32)
        got = 0
        with self._lock:
            while got < n and self._buf:
                head = self._buf[0]
                take = min(n - got, head.shape[0])
                out[got:got + take] = head[:take]
                got += take
                if take == head.shape[0]:
                    self._buf.pop(0)
                else:
                    self._buf[0] = head[take:]
                self._buflen -= take
            self._read_pos += got
        return out

    def close(self):
        try:
            self._file.close()
        except Exception:
            pass


def _to_stereo(x):
    if x.shape[1] == 1:
        return np.repeat(x, 2, axis=1)
    if x.shape[1] > 2:
        return np.ascontiguousarray(x[:, :2])
    return x


class DualPlayer:
    """Two sources, one output stream, one shared playhead."""

    def __init__(self):
        self.sources: dict[str, _Source] = {}
        self.fs = 48000
        self.length = 0
        self.position = 0                 # frames on the synced timeline
        self.master = 1.0
        self.solo = None                  # None | 'A' | 'B'
        self.playing = False
        self.error = ""

        self._stream = None
        self._reader = None
        self._stop = threading.Event()
        self._seek_to = None
        self._applied = {"A": 1.0, "B": 1.0}   # gains actually in effect
        self.on_finish = None

    # -- lifecycle ---------------------------------------------------------

    def load(self, a_path, b_path, pad_a_seconds, pad_b_seconds, fs_out=None):
        """Pads are given in SECONDS -- the same silence export will write."""
        self.unload()
        fs_a = audiofile.info(str(a_path)).samplerate
        fs_b = audiofile.info(str(b_path)).samplerate
        self.fs = int(fs_out or max(fs_a, fs_b))
        self.sources = {
            "A": _Source(a_path, self.fs, round(pad_a_seconds * self.fs)),
            "B": _Source(b_path, self.fs, round(pad_b_seconds * self.fs)),
        }
        self.length = max(s.length for s in self.sources.values())
        self.position = 0
        self._start_reader()

    def unload(self):
        self.stop()
        self._stop.set()
        if self._reader and self._reader.is_alive():
            self._reader.join(timeout=1.0)
        self._reader = None
        for s in self.sources.values():
            s.close()
        self.sources = {}
        self.length = 0
        self.position = 0

    def _start_reader(self):
        self._stop.clear()
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _read_loop(self):
        target = int(BUFFER_SECONDS * self.fs)
        while not self._stop.is_set():
            if self._seek_to is not None:
                pos = self._seek_to
                self._seek_to = None
                for s in self.sources.values():
                    s._reset_reader(pos)
            did = False
            for s in self.sources.values():
                while s.buffered() < target and not s._eof:
                    if not s._produce():
                        break
                    did = True
                    if self._seek_to is not None:
                        break
            if not did:
                self._stop.wait(0.01)

    # -- transport ---------------------------------------------------------

    def play(self):
        if not self.sources:
            return False
        if not HAVE_AUDIO:
            self.error = ("No audio output available. "
                          + (AUDIO_ERROR or "PortAudio did not initialise."))
            return False
        if self.playing:
            return True
        try:
            self._stream = sd.OutputStream(
                samplerate=self.fs, channels=2, dtype="float32",
                blocksize=BLOCK, callback=self._callback,
                finished_callback=self._on_stream_end)
            self._stream.start()
        except Exception as exc:
            self.error = f"Could not open the audio device: {exc}"
            self._stream = None
            return False
        self.playing = True
        self.error = ""
        return True

    def pause(self):
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
        self._stream = None
        self.playing = False

    def stop(self):
        self.pause()
        self.seek(0)

    def seek(self, frame):
        frame = int(max(0, min(frame, self.length)))
        self.position = frame
        self._seek_to = frame

    def seek_seconds(self, t):
        self.seek(int(t * self.fs))

    # -- mixer -------------------------------------------------------------

    def set_gain(self, which, gain):
        if which in self.sources:
            self.sources[which].gain = float(max(0.0, gain))

    def set_mute(self, which, muted):
        if which in self.sources:
            self.sources[which].muted = bool(muted)

    def set_solo(self, which):
        self.solo = which

    def _effective(self, which):
        s = self.sources[which]
        if s.muted:
            return 0.0
        if self.solo is not None and self.solo != which:
            return 0.0
        return s.gain * self.master

    # -- audio callback ----------------------------------------------------

    def _callback(self, outdata, frames, time_info, status):
        mix = np.zeros((frames, 2), np.float32)
        ramp = np.linspace(0.0, 1.0, frames, dtype=np.float32)[:, None]
        for which, s in self.sources.items():
            blk = s.pull(frames)
            g1 = self._effective(which)
            g0 = self._applied[which]
            if g0 == g1:
                blk = blk * g1
            else:
                # ramp the gain across the block: a jumped fader clicks
                blk = blk * (g0 + (g1 - g0) * ramp)
                self._applied[which] = g1
            s.peak = float(np.abs(blk).max()) if blk.size else 0.0
            mix += blk

        np.clip(mix, -1.0, 1.0, out=mix)
        outdata[:] = mix
        self.position = min(self.position + frames, self.length)
        if self.position >= self.length:
            raise sd.CallbackStop()

    def _on_stream_end(self):
        self.playing = False
        if self.on_finish:
            try:
                self.on_finish()
            except Exception:
                pass
