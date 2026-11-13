"""Audio capture from microphones (input) and from what plays on speakers/headphones (WASAPI loopback)."""
import queue
import threading
import time
import wave

import numpy as np

from .i18n import _
from .dsp import SR, Chunker, Resampler, float_to_int16, to_mono_float

try:
    import pyaudiowpatch as pyaudio
except ImportError:  # outside Windows (tests)
    pyaudio = None


def list_sources():
    """List of WASAPI sources: [{key, name, kind ('input'|'output'), index, channels, rate, default}]."""
    if pyaudio is None:
        return []
    p = pyaudio.PyAudio()
    try:
        wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)
        default_in = wasapi.get("defaultInputDevice", -1)
        try:
            default_loop = p.get_default_wasapi_loopback()["index"]
        except Exception:
            default_loop = -1
        # manual list of all WASAPI devices (does not depend on the library version)
        devices = []
        for i in range(p.get_device_count()):
            try:
                d = p.get_device_info_by_index(i)
            except Exception:
                continue
            if d.get("hostApi") == wasapi["index"]:
                devices.append(d)
        res = []
        for d in devices:
            if d.get("isLoopbackDevice") or "[Loopback]" in d["name"]:
                continue
            if d["maxInputChannels"] > 0:
                res.append({
                    "key": "IN: " + d["name"], "name": d["name"], "kind": "input", "index": d["index"],
                    "channels": int(min(2, d["maxInputChannels"])), "max_channels": int(d["maxInputChannels"]),
                    "rate": int(d["defaultSampleRate"]), "default": d["index"] == default_in,
                })
        for d in devices:
            if not (d.get("isLoopbackDevice") or "[Loopback]" in d["name"]) or d["maxInputChannels"] <= 0:
                continue
            res.append({
                "key": "OUT: " + d["name"].replace(" [Loopback]", ""), "name": d["name"], "kind": "output",
                "index": d["index"], "channels": int(d["maxInputChannels"]), "max_channels": int(d["maxInputChannels"]),
                "rate": int(d["defaultSampleRate"]),
                "default": d["index"] == default_loop,
            })
        return res
    finally:
        p.terminate()


class AudioSource:
    """A recorded source: writes a 16 kHz mono WAV aligned to the session start and
    passes ready blocks to on_chunk(source, start_sec, samples)."""

    def __init__(self, info, label, diarize, wav_path, t0, chunk_sec, on_chunk, log=print):
        self.info = info
        self.label = label
        self.diarize = diarize
        self.wav_path = wav_path
        self.t0 = t0
        self.on_chunk = on_chunk
        self.log = log
        self.q = queue.Queue()
        self.resampler = Resampler(info["rate"], SR)
        self.chunker = Chunker(chunk_sec) if on_chunk else None
        self.written = 0
        self.level = 0.0
        self._stop = threading.Event()
        self._thread = None
        self._pa = None
        self._stream = None
        self._wav = None

    # driver callback: only queueing, no heavy work
    def _callback(self, in_data, frame_count, time_info, status):
        self.q.put((time.time(), in_data, frame_count))
        return (None, pyaudio.paContinue)

    def start(self):
        self._wav = wave.open(str(self.wav_path), "wb")
        self._wav.setnchannels(1)
        self._wav.setsampwidth(2)
        self._wav.setframerate(SR)
        self._thread = threading.Thread(target=self._run, daemon=True, name=f"audio-{self.label}")
        self._thread.start()
        self._pa = pyaudio.PyAudio()
        last_err = None
        for ch in dict.fromkeys([self.info["channels"], self.info.get("max_channels", self.info["channels"]), 1, 2]):
            try:
                self.info["channels"] = ch
                self._stream = self._pa.open(
                    format=pyaudio.paInt16,
                    channels=ch,
                    rate=self.info["rate"],
                    input=True,
                    input_device_index=self.info["index"],
                    frames_per_buffer=int(self.info["rate"] * 0.05),
                    stream_callback=self._callback,
                )
                break
            except Exception as e:
                last_err = e
        if self._stream is None:
            raise RuntimeError(_("Cannot open '{0}': {1}", self.info['name'], last_err))
        self._stream.start_stream()

    def _append(self, x):
        if len(x) == 0:
            return
        self._wav.writeframes(float_to_int16(x).tobytes())
        self.written += len(x)
        if self.chunker:
            for start, chunk in self.chunker.push(x):
                self.on_chunk(self, start / SR, chunk)

    def _pad_to(self, wall_time):
        """Inserts silence if the driver sent no data (the loopback is silent when nothing is playing)."""
        expected = int((wall_time - self.t0) * SR)
        gap = expected - self.written
        if gap > int(0.25 * SR):
            self._append(np.zeros(gap, dtype=np.float32))

    def _run(self):
        ch = self.info["channels"]
        rate = self.info["rate"]
        while not (self._stop.is_set() and self.q.empty()):
            try:
                t, data, frames = self.q.get(timeout=0.2)
            except queue.Empty:
                continue
            x = to_mono_float(data, ch)
            self.level = float(np.sqrt((x ** 2).mean())) if len(x) else 0.0
            self._pad_to(t - frames / rate)
            self._append(self.resampler.process(x))

    def stop(self, t_end):
        try:
            if self._stream is not None:
                self._stream.stop_stream()
                self._stream.close()
        except Exception as e:
            self.log(_("Closing audio {0}: {1}", self.label, e))
        try:
            if self._pa is not None:
                self._pa.terminate()
        except Exception:
            pass
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=10)
        # align the end of all tracks and send the last block
        expected = int((t_end - self.t0) * SR)
        if expected > self.written:
            self._wav.writeframes(np.zeros(expected - self.written, dtype=np.int16).tobytes())
            self.written = expected
        if self.chunker:
            for start, chunk in self.chunker.flush():
                self.on_chunk(self, start / SR, chunk)
        self._wav.close()
