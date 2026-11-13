"""Audio device tests and video previews, to see at a glance whether the right one was chosen.

- LevelMonitor: opens all sources only to measure the level (no file), so you can see which bar moves.
- test_input: records a few seconds from the microphone showing the level, then plays it back.
- test_output: plays a short chime on that output while listening to its loopback: if the sound arrives,
  the bar moves and you know it is the right output (even if you cannot hear it, e.g. headphones not worn).
- grab_preview: thumbnail of a screen or a window (as the recording would see it)."""
import time

import numpy as np

from .dsp import to_mono_float

try:
    import pyaudiowpatch as pyaudio
except ImportError:
    pyaudio = None

try:
    from PIL import Image, ImageGrab
except ImportError:
    Image = ImageGrab = None


def _open_input(pa, info, callback):
    last = None
    for ch in dict.fromkeys([info["channels"], info.get("max_channels", info["channels"]), 1, 2]):
        try:
            s = pa.open(format=pyaudio.paInt16, channels=ch, rate=info["rate"], input=True,
                        input_device_index=info["index"], frames_per_buffer=int(info["rate"] * 0.05),
                        stream_callback=lambda d, n, t, st, ch=ch: (callback(d, ch), (None, pyaudio.paContinue))[1])
            s.start_stream()
            return s
        except Exception as e:
            last = e
    raise RuntimeError(str(last))


def _rms(data, ch):
    x = to_mono_float(data, ch)
    return float(np.sqrt((x ** 2).mean())) if len(x) else 0.0


class LevelMonitor:
    """Continuously measures the level of all sources (key -> rms). No recording."""

    def __init__(self, sources):
        self._levels = {}
        self._last = {}
        self._pa = pyaudio.PyAudio() if pyaudio else None
        self._streams = []
        for info in sources:
            key = info["key"]
            try:
                self._streams.append(_open_input(self._pa, info, lambda d, ch, key=key: self._set(key, d, ch)))
            except Exception:
                self._levels[key] = -1.0  # cannot be opened

    def _set(self, key, data, ch):
        self._levels[key] = max(_rms(data, ch), self._levels.get(key, 0) * 0.6)  # decays smoothly
        self._last[key] = time.time()

    def levels(self):
        now = time.time()
        # the loopback sends nothing when the output is silent: after a moment the level drops to zero
        return {k: (v if v < 0 or now - self._last.get(k, 0) < 0.4 else 0.0) for k, v in self._levels.items()}

    def stop(self):
        for s in self._streams:
            try:
                s.stop_stream()
                s.close()
            except Exception:
                pass
        self._streams = []
        if self._pa:
            try:
                self._pa.terminate()
            except Exception:
                pass
            self._pa = None


def _render_index(pa, loopback_name):
    """WASAPI playback device matching an output "… [Loopback]"."""
    target = loopback_name.replace(" [Loopback]", "").strip()
    wasapi = pa.get_host_api_info_by_type(pyaudio.paWASAPI)["index"]
    for i in range(pa.get_device_count()):
        d = pa.get_device_info_by_index(i)
        if d.get("hostApi") == wasapi and d["maxOutputChannels"] > 0 and not d.get("isLoopbackDevice") \
                and d["name"].strip() == target:
            return d
    return None


def _chime(rate, channels):
    """Three ascending notes (C-E-G) with soft attack and release, ~1.2 s."""
    parts = []
    for f in (523.25, 659.25, 783.99):
        n = int(rate * 0.32)
        t = np.arange(n) / rate
        env = np.minimum(1, t / 0.01) * np.exp(-t * 5)
        tone = 0.35 * env * (np.sin(2 * np.pi * f * t) + 0.3 * np.sin(4 * np.pi * f * t))
        parts.append(tone)
    x = np.concatenate(parts + [np.zeros(int(rate * 0.15))]).astype(np.float32)
    x = np.repeat(x[:, None], channels, axis=1)
    return (np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes()


def test_input(info, on_level, on_done, seconds=4.0):
    """Records `seconds` from the microphone (on_level(rms) while you speak), then plays it on the default output.
    on_done(peak, error). Call it from a thread."""
    pa = pyaudio.PyAudio()
    chunks, peak = [], [0.0]
    ch_used = [1]

    def cb(data, ch):
        ch_used[0] = ch
        chunks.append(data)
        r = _rms(data, ch)
        peak[0] = max(peak[0], r)
        on_level(r)
    try:
        s = _open_input(pa, info, cb)
        time.sleep(seconds)
        s.stop_stream()
        s.close()
        on_level(0.0)
        audio = b"".join(chunks)
        if audio:
            out = pa.open(format=pyaudio.paInt16, channels=ch_used[0], rate=info["rate"], output=True)
            out.write(audio)
            out.stop_stream()
            out.close()
        on_done(peak[0], None)
    except Exception as e:
        on_done(peak[0], e)
    finally:
        pa.terminate()


def test_output(info, on_level, on_done):
    """Plays the chime on the output and measures how much of it comes back from its loopback. on_done(peak, error)."""
    pa = pyaudio.PyAudio()
    peak = [0.0]

    def cb(data, ch):
        r = _rms(data, ch)
        peak[0] = max(peak[0], r)
        on_level(r)
    try:
        dev = _render_index(pa, info["name"])
        if dev is None:
            raise RuntimeError("render device not found")
        loop = _open_input(pa, info, cb)
        rate, ch = int(dev["defaultSampleRate"]), int(min(2, dev["maxOutputChannels"]))
        out = pa.open(format=pyaudio.paInt16, channels=ch, rate=rate, output=True,
                      output_device_index=dev["index"])
        out.write(_chime(rate, ch))
        time.sleep(0.3)
        out.stop_stream()
        out.close()
        loop.stop_stream()
        loop.close()
        on_level(0.0)
        on_done(peak[0], None)
    except Exception as e:
        on_done(peak[0], e)
    finally:
        pa.terminate()


# ---------------- video previews ----------------

def grab_preview(target, max_w, max_h):
    """target: ("monitor", m) | ("area", hwnd) | ("direct", hwnd). Returns a scaled-down PIL image or None."""
    if ImageGrab is None:
        return None
    from .winutil import is_minimized, is_window, window_rect
    kind, obj = target
    try:
        if kind == "monitor":
            m = obj
            im = ImageGrab.grab(bbox=(m["x"], m["y"], m["x"] + m["w"], m["y"] + m["h"]), all_screens=True)
        else:
            if not is_window(obj):
                return None
            if is_minimized(obj):
                return None
            if kind == "direct":
                im = ImageGrab.grab(window=obj)  # like "Direct window": if it is black here, the video will be too
            else:
                x, y, w, h = window_rect(obj)
                im = ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True)
        im.thumbnail((max_w, max_h), Image.BILINEAR)
        return im
    except Exception:
        return None
