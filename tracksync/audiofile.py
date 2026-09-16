"""
audiofile.py -- one reading interface for every format we accept.

libsndfile covers WAV, FLAC, AIFF, CAF, W64, OGG and (since 1.1) MP3, and it
gives exact sample-accurate seeking, so it is used whenever it can open the
file. It cannot open M4A/AAC/ALAC, WMA or audio inside MP4/MOV containers, so
those fall through to PyAV, which ships ffmpeg's decoders in its wheel and
needs no separate install.

PyAV is the harder path because a compressed stream has no sample index: you
seek to a keyframe *before* what you want and decode forward to reach the
exact sample. That is what `_AVReader` does, and it is why the reader keeps
its position -- sequential reads stay cheap, and only a real jump pays for a
re-seek.

Everything else in TrackSync goes through `info()` and `open_audio()`, so no
other module has to care which backend a file uses.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import numpy as np
import soundfile as sf

try:
    import av
    import av.audio.resampler
    HAVE_AV = True
except Exception:
    av = None
    HAVE_AV = False


#: What the file dialog offers. MP3 is handled by libsndfile; the rest of the
#: compressed formats come from PyAV when it is available.
SNDFILE_EXTS = {".wav", ".wave", ".flac", ".aif", ".aiff", ".aifc", ".caf",
                ".w64", ".rf64", ".ogg", ".oga", ".opus", ".mp3", ".au",
                ".snd", ".voc", ".w64", ".mat", ".sd2", ".iff", ".svx"}
AV_EXTS = {".m4a", ".mp4", ".m4b", ".m4r", ".aac", ".mov", ".wma", ".asf",
           ".mka", ".mkv", ".webm", ".3gp", ".amr", ".ac3", ".dts", ".wv",
           ".ape", ".mpc", ".tta", ".avi", ".mts", ".m2ts"}


def supported_extensions():
    exts = set(SNDFILE_EXTS)
    if HAVE_AV:
        exts |= AV_EXTS
    return sorted(exts)


def file_dialog_filter():
    pats = " ".join("*" + e for e in supported_extensions())
    parts = [f"Audio and video files ({pats})"]
    if not HAVE_AV:
        parts.append("Note: M4A/AAC needs the 'av' package (All files)")
    parts.append("All files (*)")
    return ";;".join(parts)


class UnsupportedAudio(Exception):
    pass


@dataclass
class Info:
    path: str
    name: str
    samplerate: int
    channels: int
    frames: int
    duration: float
    subtype: str
    format: str
    backend: str          # "sndfile" | "av"
    lossy: bool           # True if the source is a compressed/lossy codec

    def as_dict(self):
        from dataclasses import asdict
        return asdict(self)


_LOSSY_HINTS = ("MPEG", "MP3", "VORBIS", "OPUS", "AAC", "WMA", "ALAC",
                "AC3", "DTS", "AMR", "MS_ADPCM", "IMA_ADPCM", "G721", "G723")


def info(path) -> Info:
    """Probe a file with whichever backend can read it."""
    path = str(path)
    if not os.path.exists(path):
        raise FileNotFoundError(path)

    try:
        i = sf.info(path)
        subtype = str(i.subtype or "")
        fmt = str(i.format or "")
        return Info(
            path=path, name=os.path.basename(path),
            samplerate=int(i.samplerate), channels=int(i.channels),
            frames=int(i.frames), duration=float(i.duration),
            subtype=subtype, format=fmt, backend="sndfile",
            lossy=any(h in (subtype + " " + fmt).upper() for h in _LOSSY_HINTS),
        )
    except Exception as snd_error:
        if not HAVE_AV:
            raise UnsupportedAudio(
                f"{os.path.basename(path)} is not a format libsndfile can "
                f"read, and the 'av' package (which handles M4A/AAC) is not "
                f"installed. ({snd_error})") from snd_error

    try:
        with av.open(path) as c:
            streams = [s for s in c.streams if s.type == "audio"]
            if not streams:
                raise UnsupportedAudio(
                    f"{os.path.basename(path)} contains no audio stream.")
            s = streams[0]
            rate = int(s.codec_context.sample_rate or s.rate or 48000)
            ch = int(getattr(s.codec_context, "channels", 0)
                     or getattr(getattr(s.codec_context, "layout", None),
                                "nb_channels", 0) or 2)
            if s.duration is not None and s.time_base:
                dur = float(s.duration * s.time_base)
            elif c.duration:
                dur = float(c.duration) / 1_000_000.0
            else:
                dur = 0.0
            codec = (s.codec_context.name or "").upper()
            return Info(
                path=path, name=os.path.basename(path),
                samplerate=rate, channels=ch,
                frames=int(round(dur * rate)), duration=dur,
                subtype=codec, format=(c.format.name or "").upper(),
                backend="av", lossy=True,
            )
    except UnsupportedAudio:
        raise
    except Exception as exc:
        raise UnsupportedAudio(
            f"Could not read {os.path.basename(path)}: {exc}") from exc


def open_audio(path):
    """Return a reader: .samplerate .channels .frames .seek(n) .read(n) .close()

    read() returns float32 (frames, channels); a short return means the end
    of the file. Use as a context manager.
    """
    i = info(path)
    if i.backend == "sndfile":
        return _SndReader(i)
    return _AVReader(i)


class _SndReader:
    def __init__(self, i: Info):
        self.info = i
        self.samplerate = i.samplerate
        self.channels = i.channels
        self.frames = i.frames
        self._f = sf.SoundFile(i.path)

    def seek(self, frame):
        self._f.seek(int(max(0, min(frame, self.frames))))

    def read(self, n):
        return self._f.read(int(n), dtype="float32", always_2d=True)

    def close(self):
        try:
            self._f.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


class _AVReader:
    """Sample-accurate reads over a compressed stream.

    Positions come from each decoded frame's own presentation timestamp, not
    from counting samples since the last seek. That distinction matters: a
    seek lands on a keyframe at or before the target, and *where* it lands is
    not knowable in advance, so a reader that assumes it landed on the target
    drifts by a different amount on every jump. Trusting the timestamps makes
    a seek exact, which is what the probe stage needs -- probes that each
    believe they are somewhere they are not would poison the drift fit.

    Sequential reading never re-seeks, so scanning a long file stays cheap.
    """

    #: seek this far before the target, then decode forward to it
    SEEK_BACKOFF = 1.0          # seconds

    def __init__(self, i: Info):
        self.info = i
        self.samplerate = i.samplerate
        self.channels = i.channels
        self.frames = i.frames
        self._container = None
        self._stream = None
        self._decoder = None
        self._resampler = None
        self._buf = np.zeros((0, self.channels), np.float32)
        self._buf_start = 0         # absolute index of _buf[0]
        self._pos = 0               # next frame index read() will return
        self._origin = 0            # pts of the stream's first sample
        self._open()

    def _open(self):
        self._container = av.open(self.info.path)
        self._stream = next(s for s in self._container.streams
                            if s.type == "audio")
        self._stream.thread_type = "AUTO"
        # normalise every codec's native layout to planar float
        self._resampler = av.audio.resampler.AudioResampler(
            format="fltp", layout=self._stream.codec_context.layout,
            rate=self.samplerate)
        st = self._stream.start_time
        if st:
            self._origin = self._pts_to_frame(st, absolute=True)
        self._restart_decoder(0)

    def _pts_to_frame(self, pts, absolute=False):
        tb = self._stream.time_base
        n = int(round(float(pts * tb) * self.samplerate))
        return n if absolute else n - self._origin

    def _restart_decoder(self, at_frame):
        self._decoder = self._container.decode(self._stream)
        self._buf = np.zeros((0, self.channels), np.float32)
        self._buf_start = int(at_frame)
        self._pos = int(at_frame)

    # -- decoding ---------------------------------------------------------

    def _frame_to_array(self, frame):
        try:
            out = self._resampler.resample(frame)
        except Exception:
            out = [frame]
        chunks = []
        for f in (out if isinstance(out, (list, tuple)) else [out]):
            if f is None:
                continue
            a = f.to_ndarray()          # fltp -> (channels, samples)
            if a.ndim == 1:
                a = a[None, :]
            if a.dtype != np.float32:
                a = a.astype(np.float32)
            chunks.append(np.ascontiguousarray(a.T))
        if not chunks:
            return np.zeros((0, self.channels), np.float32)
        x = np.concatenate(chunks, axis=0)
        if x.shape[1] != self.channels:
            if x.shape[1] > self.channels:
                x = x[:, :self.channels]
            else:
                x = np.pad(x, ((0, 0), (0, self.channels - x.shape[1])))
        return x

    def _pull_more(self):
        """Decode one more frame into the buffer, placed by its timestamp."""
        for frame in self._decoder:
            x = self._frame_to_array(frame)
            if not x.shape[0]:
                continue
            pos = (self._pts_to_frame(frame.pts) if frame.pts is not None
                   else self._buf_start + self._buf.shape[0])

            if self._buf.shape[0] == 0:
                self._buf = x
                self._buf_start = pos
                return True

            end = self._buf_start + self._buf.shape[0]
            if pos == end:
                self._buf = np.concatenate((self._buf, x), axis=0)
            elif pos > end:
                # a gap in the stream: keep absolute positions honest by
                # filling it rather than sliding later audio earlier
                pad = np.zeros((pos - end, self.channels), np.float32)
                self._buf = np.concatenate((self._buf, pad, x), axis=0)
            else:
                overlap = end - pos
                if overlap < x.shape[0]:
                    self._buf = np.concatenate((self._buf, x[overlap:]), axis=0)
            return True
        return False

    def _align_buffer(self):
        """Make _buf begin exactly at _pos."""
        if self._buf.shape[0] and self._buf_start < self._pos:
            drop = min(self._pos - self._buf_start, self._buf.shape[0])
            self._buf = self._buf[drop:]
            self._buf_start += drop
        if self._buf.shape[0] == 0:
            self._buf_start = self._pos
        elif self._buf_start > self._pos:
            pad = np.zeros((self._buf_start - self._pos, self.channels),
                           np.float32)
            self._buf = np.concatenate((pad, self._buf), axis=0)
            self._buf_start = self._pos

    # -- public interface -------------------------------------------------

    def seek(self, frame):
        frame = int(max(0, min(frame, self.frames)))
        if frame == self._pos:
            return
        ahead = frame - self._pos
        if 0 <= ahead < self.samplerate * 4:
            self._pos = frame           # cheaper to decode through
            self._align_buffer()
            return

        back = max(0.0, frame / float(self.samplerate) - self.SEEK_BACKOFF)
        try:
            self._container.seek(
                int(round(back / float(self._stream.time_base)))
                + (self._stream.start_time or 0),
                stream=self._stream, backward=True, any_frame=False)
        except Exception:
            try:
                self._container.seek(0)
            except Exception:
                pass
        self._restart_decoder(frame)
        # the first decoded frame reports where the seek actually landed;
        # _pull_more places it by timestamp and _align_buffer does the rest
        self._buf = np.zeros((0, self.channels), np.float32)
        self._pull_more()
        self._align_buffer()

    def read(self, n):
        n = int(n)
        self._align_buffer()
        while self._buf.shape[0] < n:
            if not self._pull_more():
                break
            self._align_buffer()
        take = min(n, self._buf.shape[0])
        out = self._buf[:take].copy()
        self._buf = self._buf[take:]
        self._buf_start += take
        self._pos += take
        return out

    def close(self):
        try:
            self._container.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
