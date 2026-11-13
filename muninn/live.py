"""Live captions and the "Notary".

DeepInfra does not offer a true streaming connection for Nemotron 3.5 ASR Streaming, but the model is very fast
(~0.6 s): the current sentence is resent as soon as the previous response comes back (partial result) and, at the
first pause, once more as final, with priority.
The captions and the Notary window are excluded from screen sharing and recordings."""
import base64
import json
import queue
import re
import threading
import time
import tkinter as tk
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from tkinter import scrolledtext, ttk

import numpy as np
import requests

from .i18n import _
from .dsp import SR, Resampler, float_to_int16, to_mono_float, wav_bytes
from .winutil import exclude_from_capture

try:
    import pyaudiowpatch as pyaudio
except ImportError:
    pyaudio = None
try:
    import websocket  # websocket-client, only for the real time EN captions
except ImportError:
    websocket = None

LIVE_URL = "https://api.deepinfra.com/v1/inference/nvidia/Nemotron-3.5-ASR-Streaming-Multilingual-0.6b"
# true streaming over WebSocket: DeepInfra offers it only for the English model (the multilingual one answers
# "model_not_realtime", checked in November 2026)
REALTIME_URL = "wss://api.deepinfra.com/v1/realtime?model=nvidia/Nemo3-ASR-Streaming"
COLORS = ["#7dd3fc", "#fca5a5", "#86efac", "#fde68a", "#c4b5fd", "#f9a8d4"]


# ---------------- capture ----------------

class LiveSource:
    """Opens a device (microphone or loopback output) and passes 16 kHz mono audio to on_audio(label, x)."""

    def __init__(self, info, label, on_audio):
        self.info, self.label, self.on_audio = info, label, on_audio
        self.q = queue.Queue()
        self.res = Resampler(info["rate"], SR)
        self._stop = threading.Event()
        self._pa = self._stream = None

    def _cb(self, data, frames, ti, status):
        self.q.put(data)
        return (None, pyaudio.paContinue)

    def start(self):
        self._pa = pyaudio.PyAudio()
        err = None
        for ch in dict.fromkeys([self.info["channels"], self.info.get("max_channels", 1), 1, 2]):
            try:
                self._stream = self._pa.open(format=pyaudio.paInt16, channels=ch, rate=self.info["rate"], input=True,
                                             input_device_index=self.info["index"],
                                             frames_per_buffer=int(self.info["rate"] * 0.05), stream_callback=self._cb)
                self.channels = ch
                break
            except Exception as e:
                err = e
        if self._stream is None:
            raise RuntimeError(_("Cannot open '{0}': {1}", self.info['name'], err))
        threading.Thread(target=self._run, daemon=True).start()
        self._stream.start_stream()

    def _run(self):
        while not self._stop.is_set():
            try:
                data = self.q.get(timeout=0.2)
            except queue.Empty:
                continue
            self.on_audio(self.label, self.res.process(to_mono_float(data, self.channels)))

    def stop(self):
        self._stop.set()
        try:
            self._stream.stop_stream()
            self._stream.close()
            self._pa.terminate()
        except Exception:
            pass


# ---------------- near real time transcription ----------------

FRAME = 0.03  # seconds per frame in the volume calculation


def _drop_short_runs(v, k):
    """Removes "voice" runs shorter than k frames (pops, clicks, sharp noises)."""
    v = v.copy()
    i, n = 0, len(v)
    while i < n:
        if v[i]:
            j = i
            while j < n and v[j]:
                j += 1
            if j - i < k:
                v[i:j] = False
            i = j
        else:
            i += 1
    return v


def _stable_words(prev, cur):
    """How many leading words are equal between two consecutive partials (they can be considered "settled")."""
    n = 0
    for a, b in zip(prev.split(), cur.split()):
        if a.strip(".,;:!?…").lower() != b.strip(".,;:!?…").lower():
            break
        n += 1
    return n


class LiveTranscriber:
    """Keeps the current sentence for each source.

    DeepInfra runs an account's requests one at a time (measured: in parallel they come out ~0.7 s apart), so
    every extra request delays the others:
    - Partials: at most one in flight for all sources, the next starts as soon as the previous returns
      (not before `partial_every` s). No partial while a final is pending.
    - Final: at a pause of `pause_sec` s, immediately, with priority over partials; one retry if it fails.
    - Long sentences: beyond `max_sec` s they are cut at the quietest point of the last 3 s, without losing audio.
    on_update(label, text, final, t_start, sentence_uid, stable_words)."""

    def __init__(self, key, labels, on_update, log, language="it", partial_every=0.7, pause_sec=0.5,
                 max_sec=12.0):
        self.key, self.on_update, self.log, self.language = key, on_update, log, language
        self.partial_every, self.pause_sec, self.max_sec = partial_every, pause_sec, max_sec
        self.partial_busy = False   # a partial in flight (for all sources)
        self.finals_pending = 0
        self.lock = threading.Lock()
        self.st = {lab: self._new_state() for lab in labels}
        self.pool = ThreadPoolExecutor(max_workers=8)
        self.session = requests.Session()
        self.session.mount("https://", requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=16))
        self.cost = 0.0
        self.seconds = 0.0
        self.errors = 0
        self.latency = None        # moving average of the finals' response time (s)
        self._last_net = 0.0
        self._stop = threading.Event()
        self._send_lang = bool(language)
        self.pool.submit(self._warm)  # open the connection right away (DNS + TLS can take seconds)
        threading.Thread(target=self._loop, daemon=True).start()

    @staticmethod
    def _new_state():
        return {"buf": [], "t0": None, "uid": 0, "seq": 0, "shown": -1, "last": 0.0,
                "hist": deque(maxlen=int(10 / FRAME)), "rem": np.zeros(0, np.float32),
                "next_emit": 0, "done": {}, "prev": ""}

    # ----- incoming audio (capture thread)
    def feed(self, label, x):
        if len(x) == 0:
            return
        f = int(FRAME * SR)
        with self.lock:
            s = self.st[label]
            if s["t0"] is None:
                s["t0"] = time.time() - len(x) / SR
            s["buf"].append(x)
            r = np.concatenate([s["rem"], x])
            n = len(r) // f
            if n:  # volume history of the last 10 s: used to estimate the background noise
                s["hist"].extend(np.sqrt((r[: n * f].reshape(n, f) ** 2).mean(axis=1)).tolist())
            s["rem"] = r[n * f:]

    # ----- control loop
    def _loop(self):
        while not self._stop.is_set():
            time.sleep(0.1)
            now = time.time()
            for label in list(self.st):
                try:
                    self._tick(label, now)
                except Exception as e:
                    self._error(e)
            if now - self._last_net > 30:  # keep the connection warm during silences
                self._last_net = now
                self.pool.submit(self._warm)

    def _tick(self, label, now):
        with self.lock:
            s = self.st[label]
            if not s["buf"]:
                return
            x = s["buf"][0] if len(s["buf"]) == 1 else np.concatenate(s["buf"])
            s["buf"] = [x]  # whatever arrives from now on is queued after x
            hist = np.fromiter(s["hist"], dtype=float)
            t0 = s["t0"]
        f = int(FRAME * SR)
        n = len(x) // f
        if n == 0:
            return
        rms = np.sqrt((x[: n * f].reshape(n, f) ** 2).mean(axis=1))
        floor = float(np.percentile(hist, 15)) if len(hist) >= 30 else 0.002
        voiced = _drop_short_runs(rms > max(0.006, floor * 3.0), 3)
        if not voiced.any():  # only silence: keep the last half second
            keep_from = max(0, len(x) - int(0.5 * SR))
            self._cut(label, x, keep_from, t0)
            return
        first = int(np.argmax(voiced))
        last = n - 1 - int(np.argmax(voiced[::-1]))
        tail = (n - 1 - last) * FRAME
        speech_len = (last - first + 1) * FRAME
        start = max(0, first * f - int(0.2 * SR))
        end = min(len(x), (last + 1) * f + int(0.2 * SR))
        if tail >= self.pause_sec:
            if speech_len < 0.2:  # isolated noise, not a word
                self._cut(label, x, (last + 1) * f, t0)
            else:
                self._close(label, x, start, end, (last + 1) * f, t0)
            return
        if (len(x) - start) / SR >= self.max_sec:  # sentence too long: cut at the quietest point
            lo = max(first + 1, n - int(3 / FRAME))
            cut = (lo + int(np.argmin(rms[lo:n]))) * f + f // 2
            self._close(label, x, start, cut, cut, t0)
            return
        if speech_len >= 0.5 and now - s["last"] >= self.partial_every and not self.partial_busy                 and not self.finals_pending:
            with self.lock:
                s["last"] = now
                self.partial_busy = True
                s["seq"] += 1
                seq, uid = s["seq"], s["uid"]
            self.pool.submit(self._partial, label, x[start:end], uid, seq, t0 + start / SR)

    def _cut(self, label, x, keep_from, t0):
        """Discards the audio before keep_from (already handled or silence)."""
        with self.lock:
            s = self.st[label]
            s["buf"] = [x[keep_from:]] + s["buf"][1:]
            s["t0"] = t0 + keep_from / SR

    def _close(self, label, x, start, end, rest_from, t0):
        with self.lock:
            s = self.st[label]
            uid = s["uid"]
            s["uid"] += 1
            self.finals_pending += 1
            s["seq"], s["shown"], s["last"], s["prev"] = 0, -1, 0.0, ""
        self._cut(label, x, rest_from, t0)
        threading.Thread(target=self._final, args=(label, x[start:end], uid, t0 + start / SR), daemon=True).start()

    # ----- requests
    def _partial(self, label, x, uid, seq, t_start):
        try:
            text = self._post(x)
            with self.lock:
                s = self.st[label]
                if uid != s["uid"] or seq <= s["shown"]:
                    return  # superseded by a newer partial or by the sentence closing
                s["shown"] = seq
                stable = _stable_words(s["prev"], text)
                s["prev"] = text
            self.on_update(label, text, False, t_start, uid, stable)
        except Exception as e:
            self._error(e)
        finally:
            self.partial_busy = False

    def _final(self, label, x, uid, t_start):
        t = time.time()
        text = None
        for attempt in range(2):  # a second attempt if the first request fails
            if self._stop.is_set():
                return
            try:
                text = self._post(x)
                break
            except Exception as e:
                self._error(e)
        with self.lock:
            self.finals_pending -= 1
        if text is None:
            text = ""
        else:
            dt = time.time() - t
            self.latency = dt if self.latency is None else 0.7 * self.latency + 0.3 * dt
        with self.lock:  # deliver the finals in the order they were spoken
            s = self.st[label]
            s["done"][uid] = (text, t_start)
            out = []
            while s["next_emit"] in s["done"]:
                k = s["next_emit"]
                out.append((k, *s["done"].pop(k)))
                s["next_emit"] += 1
        for k, txt, ts in out:
            self.on_update(label, txt, True, ts, k, 0)

    def note(self):
        return _("response ~{0:.1f} s", self.latency) if self.latency is not None else ""

    def _post(self, x):
        audio = wav_bytes(x)
        hdr = {"Authorization": f"bearer {self.key}"}
        data = {"chunk_level": "segment"}
        if self._send_lang:
            data["language"] = self.language
        self._last_net = time.time()
        r = self.session.post(LIVE_URL, headers=hdr, data=data,
                              files={"audio": ("live.wav", audio, "audio/wav")}, timeout=20)
        if r.status_code in (400, 422) and self._send_lang:
            self._send_lang = False  # the language field is not accepted: fall back to automatic detection
            r = self.session.post(LIVE_URL, headers=hdr, data={"chunk_level": "segment"},
                                  files={"audio": ("live.wav", audio, "audio/wav")}, timeout=20)
        if r.status_code != 200:
            raise RuntimeError(_("error {0}: {1}", r.status_code, r.text[:150]))
        j = r.json()
        cost = (j.get("inference_status") or {}).get("cost")
        with self.lock:
            self.cost += float(cost or 0)
            self.seconds += len(x) / SR
        return clean_text(j.get("text", ""))

    def _warm(self):
        try:
            self._last_net = time.time()
            self.session.get("https://api.deepinfra.com/v1/me", headers={"Authorization": f"Bearer {self.key}"},
                             timeout=10)
        except Exception:
            pass

    def _error(self, e):
        self.errors += 1
        if self.errors in (1, 5) or self.errors % 20 == 0:
            self.log(_("Live captions: {0}", e))

    def stop(self):
        self._stop.set()
        self.pool.shutdown(wait=False, cancel_futures=True)


class RealtimeTranscriber:
    """True streaming (English only): one WebSocket connection per source to DeepInfra's Nemo3-ASR-Streaming model,
    compatible with OpenAI's Realtime API. Audio goes out in 100 ms pieces, text comes back as it goes
    (delta = partial, completed = confirmed piece). The server confirms in pieces of 2-4 words: they are rebuilt into
    sentences, closed by a full stop, by a pause of `flush_sec` s or beyond ~220 characters. Same interface as LiveTranscriber."""

    PRICE_PER_MIN = 0.001  # $ per minute of audio sent (DeepInfra price list)

    def __init__(self, key, labels, on_update, log, flush_sec=0.9, **_):
        if websocket is None:
            raise RuntimeError(_("the websocket-client package is missing: run install.bat again"))
        self.key, self.on_update, self.log, self.flush_sec = key, on_update, log, flush_sec
        self.lock = threading.Lock()
        self.cost = self.seconds = 0.0
        self.errors = 0
        self.latency = None
        self._stop = threading.Event()
        self.conn = {lab: {"ws": None, "ready": False, "pend": [], "pend_len": 0, "uid": 0, "t_start": None,
                           "prev": "", "sent": "", "last_ev": 0.0, "lock": threading.Lock()} for lab in labels}
        for lab in labels:
            threading.Thread(target=self._run, args=(lab,), daemon=True).start()
        threading.Thread(target=self._flusher, daemon=True).start()

    def note(self):
        return _("streaming EN")

    # ----- connection (one thread per source, reconnects by itself)
    def _run(self, label):
        c = self.conn[label]
        delay = 1.0
        while not self._stop.is_set():
            ws = websocket.WebSocketApp(
                REALTIME_URL, header=[f"Authorization: Bearer {self.key}"],
                on_open=lambda w: self._on_open(label, w),
                on_message=lambda w, m: self._on_message(label, m),
                on_error=lambda w, e: self._error(f"{label}: {e}"))
            c["ws"] = ws
            t = time.time()
            ws.run_forever(ping_interval=20, ping_timeout=10)
            c["ready"] = False
            if self._stop.is_set():
                break
            delay = 1.0 if time.time() - t > 30 else min(delay * 2, 15)
            self._stop.wait(delay)

    def _on_open(self, label, ws):
        ws.send(json.dumps({"type": "session.update", "session": {
            "type": "transcription", "audio": {"input": {"format": {"type": "audio/pcm", "rate": SR}}}}}))
        self.conn[label]["ready"] = True

    def _on_message(self, label, msg):
        try:
            e = json.loads(msg)
        except ValueError:
            return
        typ = e.get("type", "")
        c = self.conn[label]
        if typ.endswith("input_audio_transcription.delta"):
            with c["lock"]:
                cur = clean_text(e.get("transcript") or (c["prev"] + " " + (e.get("delta") or "")))
                c["prev"] = cur
                self._touch(c)
                self._emit_partial(label, c, cur)
        elif typ.endswith("input_audio_transcription.completed"):
            with c["lock"]:
                piece = clean_text(e.get("transcript") or c["prev"])
                c["prev"] = ""
                c["sent"] = clean_text(c["sent"] + " " + piece)
                self._touch(c)
                ends = list(re.finditer(r"[.?!…](?=\s|$)", c["sent"]))
                if ends or len(c["sent"]) > 220:  # the full stop often arrives at the start of the next piece (". Okay")
                    cut = ends[-1].end() if ends else len(c["sent"])
                    rest = c["sent"][cut:].strip()
                    self._emit_final(label, c, c["sent"][:cut])
                    if rest:
                        c["sent"] = rest
                        self._touch(c)
                        self._emit_partial(label, c, "")
                else:
                    self._emit_partial(label, c, "")
        elif typ == "error":
            err = e.get("error") or {}
            self._error(f"{label}: {err.get('code') or ''} {err.get('message') or e}")

    # ----- sentences (call with c["lock"])
    def _touch(self, c):
        c["last_ev"] = time.time()
        if c["t_start"] is None:
            c["t_start"] = time.time() - 1.0  # the first text arrives ~half a second after the sentence starts

    def _emit_partial(self, label, c, cur):
        fixed = c["sent"]
        text = clean_text(fixed + " " + cur)
        if text:  # already confirmed pieces are stable
            self.on_update(label, text, False, c["t_start"], c["uid"], len(fixed.split()))

    def _emit_final(self, label, c, text=None):
        text = clean_text(text if text is not None else c["sent"] + " " + c["prev"])
        uid, ts = c["uid"], c["t_start"] or time.time()
        c["uid"] += 1
        c["sent"], c["prev"], c["t_start"] = "", "", None
        self.on_update(label, text, True, ts, uid, 0)

    def _flusher(self):
        """Closes the sentence if nobody has spoken for flush_sec seconds (sentences without a full stop)."""
        while not self._stop.wait(0.2):
            now = time.time()
            for label, c in self.conn.items():
                with c["lock"]:
                    if c["sent"] and not c["prev"] and now - c["last_ev"] >= self.flush_sec:
                        self._emit_final(label, c)

    # ----- incoming audio (capture thread)
    def feed(self, label, x):
        c = self.conn.get(label)
        if c is None or len(x) == 0:
            return
        c["pend"].append(x)
        c["pend_len"] += len(x)
        if c["pend_len"] < int(0.1 * SR):
            return
        chunk = np.concatenate(c["pend"])
        c["pend"], c["pend_len"] = [], 0
        if not c["ready"]:
            return  # not connected: the audio of this moment is lost
        try:
            c["ws"].send(json.dumps({"type": "input_audio_buffer.append",
                                     "audio": base64.b64encode(float_to_int16(chunk).tobytes()).decode()}))
            with self.lock:
                self.seconds += len(chunk) / SR
                self.cost = self.seconds / 60 * self.PRICE_PER_MIN
        except Exception as e:
            c["ready"] = False
            self._error(f"{label}: {e}")

    def _error(self, e):
        self.errors += 1
        if self.errors in (1, 5) or self.errors % 20 == 0:
            self.log(_("Real time EN captions: {0}", e))

    def stop(self):
        self._stop.set()
        for c in self.conn.values():
            ws = c["ws"]
            if ws is None:
                continue
            try:
                if c["ready"]:
                    ws.send(json.dumps({"type": "session.close"}))
            except Exception:
                pass
            try:
                ws.close()
            except Exception:
                pass


def clean_text(t):
    import re
    t = re.sub(r"<[a-z]{2}-[A-Z]{2}>", "", t or "")  # language tags such as <it-IT>
    return " ".join(t.split())


# ---------------- windows ----------------

class LiveWindows:
    """Captions overlaid on the screen + Notary window with everything that was said."""

    def __init__(self, root, save_path, hide=True, font_size=17, on_close=None):
        self.root = root
        self.save_path = save_path
        self.on_close = on_close
        self.colors = {}
        self.partials = {}          # (label, sentence uid) -> (t, partial text, stable words)
        self.closed = {}            # label -> last final sentence received
        self.recent = deque(maxlen=3)  # last final sentences (label, text)
        self.font_size = font_size
        self.save_path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(save_path, "a", encoding="utf-8")

        # --- captions
        o = tk.Toplevel(root)
        self.ov = o
        o.overrideredirect(True)
        o.attributes("-topmost", True)
        try:
            o.attributes("-alpha", 0.86)
        except tk.TclError:
            pass
        o.configure(bg="#111111")
        sw, sh = o.winfo_screenwidth(), o.winfo_screenheight()
        self.ov_w = int(sw * 0.62)
        self.status = tk.Label(o, text=_("● LIVE"), fg="#22c55e", bg="#111111", font=("Segoe UI", 9, "bold"), anchor="w")
        self.status.pack(fill="x", padx=10, pady=(4, 0))
        self.ov_text = tk.Text(o, bg="#111111", fg="white", bd=0, highlightthickness=0, wrap="word", height=4,
                               width=10, cursor="arrow")
        self.ov_text.pack(fill="both", expand=True, padx=10, pady=(0, 8))
        self._fonts()
        o.geometry(f"{self.ov_w}x{self._ov_h()}+{(sw - self.ov_w) // 2}+{sh - self._ov_h() - 90}")
        for wdg in (o, self.ov_text, self.status):
            wdg.bind("<ButtonPress-1>", self._press)
            wdg.bind("<B1-Motion>", self._drag)
            wdg.bind("<MouseWheel>", self._wheel)

        # --- notary
        n = tk.Toplevel(root)
        self.nt = n
        n.title(_("Notary — everything that was said"))
        n.geometry(f"{int(sw * 0.28)}x{int(sh * 0.6)}+{sw - int(sw * 0.28) - 40}+60")
        n.attributes("-topmost", True)
        bar = ttk.Frame(n, padding=(8, 6))
        bar.pack(fill="x")
        self.v_follow = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text=_("follow the end"), variable=self.v_follow).pack(side="left")
        self.v_top = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text=_("always on top"), variable=self.v_top,
                        command=lambda: n.attributes("-topmost", self.v_top.get())).pack(side="left", padx=8)
        ttk.Button(bar, text=_("Open file"), command=self._open_file).pack(side="right")
        self.nt_partial = tk.Label(n, text="", fg="#888", font=("Segoe UI", 10, "italic"), anchor="w",
                                   justify="left", wraplength=int(sw * 0.26))
        self.nt_partial.pack(side="bottom", fill="x", padx=8, pady=(2, 6))
        self.nt_text = scrolledtext.ScrolledText(n, wrap="word", font=("Segoe UI", 10), state="disabled")
        self.nt_text.pack(fill="both", expand=True)
        self.nt_text.tag_configure("time", foreground="#888")
        self.nt_text.tag_configure("who", font=("Segoe UI", 10, "bold"))
        n.protocol("WM_DELETE_WINDOW", self.close)

        root.update_idletasks()
        if hide:
            exclude_from_capture(o)
            exclude_from_capture(n)
        self._render_overlay()

    # ----- captions: look and movement
    def _fonts(self):
        self.ov_text.configure(font=("Segoe UI", self.font_size))
        self.ov_text.tag_configure("who", font=("Segoe UI", self.font_size, "bold"))
        self.ov_text.tag_configure("partial", foreground="#bbbbbb")

    def _ov_h(self):
        return int(self.font_size * 1.9 * 3 * 1.5) + 30

    def _press(self, e):
        self._dx, self._dy = e.x_root - self.ov.winfo_x(), e.y_root - self.ov.winfo_y()

    def _drag(self, e):
        self.ov.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def _wheel(self, e):
        self.font_size = max(10, min(40, self.font_size + (1 if e.delta > 0 else -1)))
        self._fonts()
        self.ov.geometry(f"{self.ov_w}x{self._ov_h()}")

    def _color(self, who):
        if who not in self.colors:
            c = COLORS[len(self.colors) % len(COLORS)]
            self.colors[who] = c
            self.ov_text.tag_configure("c_" + who, foreground=c)
            self.nt_text.tag_configure("c_" + who, foreground=c)
        return "c_" + who

    # ----- updates (call from the GUI thread)
    def update(self, label, text, final, t_start, uid=0, stable=0):
        if final:
            self.partials.pop((label, uid), None)
            self.closed[label] = max(uid, self.closed.get(label, -1))
            if text:
                self.recent.append((label, text))
                clock = datetime.fromtimestamp(t_start).strftime("%H:%M:%S")
                self._file.write(f"[{clock}] {label}: {text}\n")
                self._file.flush()
                self._notary_add(clock, label, text)
        elif text and uid > self.closed.get(label, -1):
            self.partials[(label, uid)] = (t_start, text, stable)
        self._render_overlay()
        self._render_partials()

    def set_status(self, text):
        self.status.configure(text=text)

    def _render_overlay(self):
        t = self.ov_text
        t.configure(state="normal")
        t.delete("1.0", "end")
        lines = list(self.recent)[-2:] + [(lab, txt, st) for (lab, _x), (_x, txt, st) in
                                          sorted(self.partials.items(), key=lambda kv: kv[1][0])]
        if not lines:
            t.insert("end", _("Listening…"))
        for i, item in enumerate(lines[-3:]):
            lab, txt = item[0], item[1]
            if i:
                t.insert("end", "\n")
            t.insert("end", lab + ": ", ("who", self._color(lab)))
            if len(item) == 2:
                t.insert("end", txt[-220:])
            else:  # partial: already confirmed words in white, the rest in grey
                words = txt.split()
                fixed, moving = " ".join(words[:item[2]]), " ".join(words[item[2]:])
                if len(fixed) + len(moving) > 220:
                    fixed = fixed[-max(0, 220 - len(moving)):]
                    moving = moving[-220:]
                t.insert("end", fixed + (" " if fixed and moving else ""))
                t.insert("end", moving, ("partial",))
        t.see("end")
        t.configure(state="disabled")

    def _notary_add(self, clock, label, text):
        t = self.nt_text
        t.configure(state="normal")
        t.insert("end", f"[{clock}] ", ("time",))
        t.insert("end", label + ": ", ("who", self._color(label)))
        t.insert("end", text + "\n")
        t.configure(state="disabled")
        if self.v_follow.get():
            t.see("end")

    def _render_partials(self):
        """The current (partial) sentence sits on a separate line below the final text."""
        lines = [f"{lab}: {txt} …" for (lab, _x), (_x, txt, _x) in sorted(self.partials.items(), key=lambda kv: kv[1][0])]
        self.nt_partial.configure(text="\n".join(lines))

    def _open_file(self):
        import os
        try:
            os.startfile(str(self.save_path))
        except Exception:
            pass

    def close(self):
        try:
            self._file.close()
        except Exception:
            pass
        for w in (self.ov, self.nt):
            try:
                w.destroy()
            except tk.TclError:
                pass
        if self.on_close:
            cb, self.on_close = self.on_close, None
            cb()


class LiveCaptions:
    """Ties together capture, transcription and windows. Started and stopped from the "Live captions" button."""

    def __init__(self, root, cfg, sources, log, on_stopped, realtime_en=False):
        self.root, self.cfg, self.log, self.on_stopped = root, cfg, log, on_stopped
        self.ui_q = queue.Queue()
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
        self.save_path = Path(cfg["output_dir"]) / "live" / f"notary_{stamp}{'_EN' if realtime_en else ''}.txt"
        self.win = LiveWindows(root, self.save_path, hide=bool(cfg.get("live_hide", True)),
                               font_size=int(cfg.get("live_font", 17)), on_close=self.stop)
        labels = [s["label"] for s in sources]
        cls = RealtimeTranscriber if realtime_en else LiveTranscriber
        try:
            self.tr = cls(cfg.get("deepinfra_key", ""), labels, lambda *a: self.ui_q.put(a), log,
                          language=cfg.get("live_language", "it"))
        except Exception:
            self.win.on_close = None
            self.win.close()
            raise
        self.sources = []
        for s in sources:
            src = LiveSource(s["info"], s["label"], self.tr.feed)
            try:
                src.start()
                self.sources.append(src)
            except Exception as e:
                log(_("Live captions, '{0}': {1}", s['label'], e))
        self.t0 = time.time()
        self.running = True
        self._poll()

    def _poll(self):
        if not self.running:
            return
        try:
            while True:
                self.win.update(*self.ui_q.get_nowait())
        except queue.Empty:
            pass
        el = int(time.time() - self.t0)
        lat = self.tr.note()
        lat = f"{lat}   ·   " if lat else ""
        self.win.set_status(_("● LIVE {0:02d}:{1:02d}:{2:02d}   ·   {3}{4:.4f} $   ·   wheel = size, drag to move", el // 3600, el % 3600 // 60, el % 60, lat, self.tr.cost))
        self.root.after(50, self._poll)

    def stop(self):
        if not self.running:
            return
        self.running = False
        for s in self.sources:
            s.stop()
        self.tr.stop()
        self.win.close()
        self.log(_("Live captions stopped. Notary saved to {0} (cost {1:.4f} $, {2:.1f} min of audio sent).", self.save_path, self.tr.cost, self.tr.seconds / 60))
        self.on_stopped()
