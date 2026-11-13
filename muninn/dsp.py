"""Audio processing: conversion to 16 kHz mono, splitting into blocks, voice detection."""
import io
import wave

import numpy as np

SR = 16000  # sample rate used for files and API


class Resampler:
    """Streaming resampling with anti-aliasing filter (FIR) + linear interpolation."""

    def __init__(self, sr_in, sr_out=SR, taps=63):
        self.sr_in = float(sr_in)
        self.sr_out = float(sr_out)
        self.step = self.sr_in / self.sr_out
        self.h = None
        self.zi = None
        if self.sr_in > self.sr_out * 1.01:
            fc = 0.45 * self.sr_out / self.sr_in  # cutoff at ~7.2 kHz for 16 kHz output
            n = np.arange(taps) - (taps - 1) / 2
            h = np.sinc(2 * fc * n) * np.hamming(taps)
            self.h = (h / h.sum()).astype(np.float32)
            self.zi = np.zeros(taps - 1, dtype=np.float32)
        self.buf = np.zeros(0, dtype=np.float32)
        self.pos = 0.0

    def process(self, x):
        x = np.asarray(x, dtype=np.float32)
        if x.size == 0:
            return np.zeros(0, dtype=np.float32)
        if self.h is not None:
            ext = np.concatenate([self.zi, x])
            x = np.convolve(ext, self.h, mode="valid").astype(np.float32)
            self.zi = ext[-(len(self.h) - 1):]
        if abs(self.step - 1.0) < 1e-9:
            return x
        self.buf = np.concatenate([self.buf, x])
        n = len(self.buf)
        if n < 2 or self.pos > n - 1:
            return np.zeros(0, dtype=np.float32)
        count = int(np.floor((n - 1 - self.pos) / self.step)) + 1
        idx = self.pos + self.step * np.arange(count)
        out = np.interp(idx, np.arange(n), self.buf).astype(np.float32)
        self.pos += self.step * count
        drop = min(int(np.floor(self.pos)), n - 1)
        self.buf = self.buf[drop:]
        self.pos -= drop
        return out


def to_mono_float(raw_bytes, channels):
    a = np.frombuffer(raw_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        usable = (len(a) // channels) * channels
        a = a[:usable].reshape(-1, channels).mean(axis=1)
    return a


def float_to_int16(x):
    return (np.clip(x, -1.0, 1.0) * 32767.0).astype(np.int16)


def has_speech(x, sr=SR, frame_ms=30, rms_thresh=0.01, min_frames=5):
    """True if the block contains at least ~150 ms of signal above the threshold."""
    f = int(sr * frame_ms / 1000)
    n = len(x) // f
    if n == 0:
        return False
    frames = x[: n * f].reshape(n, f)
    rms = np.sqrt((frames ** 2).mean(axis=1))
    return int((rms > rms_thresh).sum()) >= min_frames


def wav_bytes(x, sr=SR):
    bio = io.BytesIO()
    with wave.open(bio, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(float_to_int16(x).tobytes())
    return bio.getvalue()


def read_wav(path):
    with wave.open(str(path), "rb") as w:
        sr = w.getframerate()
        ch = w.getnchannels()
        data = w.readframes(w.getnframes())
    return to_mono_float(data, ch), sr


class Chunker:
    """Accumulates samples and cuts blocks of ~target seconds at the quietest point of the last few seconds."""

    def __init__(self, target_sec, search_sec=3.0, sr=SR):
        self.sr = sr
        self.target = int(target_sec * sr)
        self.search = int(min(search_sec, target_sec / 2) * sr)
        self.parts = []
        self.length = 0
        self.start = 0  # index (in samples) of the start of the current block

    def push(self, x):
        out = []
        if len(x):
            self.parts.append(x)
            self.length += len(x)
        while self.length >= self.target:
            buf = np.concatenate(self.parts)
            cut = self._find_cut(buf)
            out.append((self.start, buf[:cut]))
            rest = buf[cut:]
            self.start += cut
            self.parts = [rest] if len(rest) else []
            self.length = len(rest)
        return out

    def flush(self):
        if self.length == 0:
            return []
        buf = np.concatenate(self.parts)
        res = [(self.start, buf)]
        self.start += len(buf)
        self.parts, self.length = [], 0
        return res

    def _find_cut(self, buf):
        hi = self.target
        lo = max(1, hi - self.search)
        f = int(0.05 * self.sr)  # 50 ms windows
        region = buf[lo:hi]
        n = len(region) // f
        if n < 2:
            return hi
        e = (region[: n * f].reshape(n, f) ** 2).mean(axis=1)
        k = int(np.argmin(e))
        return lo + k * f + f // 2


def split_blocks(x, sr, target_sec, search_sec=10.0):
    """Splits long audio into blocks of ~target_sec, cutting at silences (without copying the array)."""
    ch = Chunker(target_sec, search_sec=search_sec, sr=sr)
    out, pos = [], 0
    while len(x) - pos > ch.target:
        cut = ch._find_cut(x[pos:pos + ch.target])
        out.append((pos, x[pos:pos + cut]))
        pos += cut
    if len(x) - pos > 0:
        out.append((pos, x[pos:]))
    return out


# ---------------- speech: regions, groups, text alignment ----------------

def speech_regions(x, sr=SR, frame_ms=30, min_gap=0.35, min_len=0.25, pad=0.15):
    """Intervals (start, end) in seconds containing voice, with a threshold adapted to the background noise."""
    f = int(sr * frame_ms / 1000)
    n = len(x) // f
    if n == 0:
        return []
    rms = np.sqrt((x[: n * f].reshape(n, f) ** 2).mean(axis=1))
    floor = float(np.percentile(rms, 10))
    thr = min(0.05, max(0.006, floor * 3.5))
    voiced = rms > thr
    regions, start = [], None
    for i, v in enumerate(voiced):
        if v and start is None:
            start = i
        elif not v and start is not None:
            regions.append([start, i])
            start = None
    if start is not None:
        regions.append([start, n])
    fs = frame_ms / 1000
    merged = []
    for a, b in regions:
        if merged and (a - merged[-1][1]) * fs < min_gap:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    dur = len(x) / sr
    return [(max(0.0, a * fs - pad), min(dur, b * fs + pad)) for a, b in merged if (b - a) * fs >= min_len]


_SENT = __import__("re").compile(r"(?<=[.!?…])\s+")


def split_sentences(text, max_chars=140):
    parts = []
    for s in _SENT.split(text.strip()):
        s = s.strip()
        while len(s) > max_chars:  # long sentences: split on comma or space
            cut = s.rfind(", ", 0, max_chars)
            cut = cut + 1 if cut > max_chars // 3 else s.rfind(" ", 0, max_chars)
            if cut <= 0:
                cut = max_chars
            parts.append(s[:cut].strip())
            s = s[cut:].strip()
        if s:
            parts.append(s)
    return parts


def align_text(text, regions):
    """Spreads the sentences over the actual speaking time (skips pauses): [(start, end, sentence)]."""
    sents = split_sentences(text)
    if not sents or not regions:
        return []
    total_speech = sum(b - a for a, b in regions)
    total_chars = sum(len(s) for s in sents)

    def at(speech_t):
        for a, b in regions:
            if speech_t <= b - a:
                return a + speech_t
            speech_t -= b - a
        return regions[-1][1]

    out, acc = [], 0
    for s in sents:
        t0 = at(total_speech * acc / total_chars)
        acc += len(s)
        t1 = at(total_speech * acc / total_chars)
        out.append((t0, max(t1, t0 + 0.3), s))
    return out
