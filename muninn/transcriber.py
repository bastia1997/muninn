"""Calls to the DeepInfra transcription API (Nemotron 3.5) and normalization of the response."""
import re
import subprocess
import threading
import time

import requests

from .i18n import _
from .dsp import wav_bytes
from .video import CREATE_NO_WINDOW

DEEPINFRA_URL = "https://api.deepinfra.com/v1/inference/nvidia/NVIDIA-Nemotron-3.5-Transcribe-30B-A4B"
SPK_TAG = re.compile(r"<\s*spk\s*:\s*(\d+)\s*>", re.IGNORECASE)


class ApiError(Exception):
    pass


def _speaker_index(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v)
    m = re.search(r"(\d+)", str(v))
    return int(m.group(1)) if m else str(v)


def normalize(resp, duration):
    """Returns a list of segments {start, end, text, speaker} with times relative to the chunk."""
    segs = resp.get("segments") or []
    words = resp.get("words") or []

    # 1) words with speaker: rebuild the turns
    if words and any(w.get("speaker") is not None or w.get("speaker_label") for w in words):
        out, cur = [], None
        for w in words:
            if w.get("type") == "audio_event":
                continue
            spk = _speaker_index(w.get("speaker", w.get("speaker_label")))
            st, en = float(w.get("start", 0)), float(w.get("end", 0))
            txt = str(w.get("word", "")).strip()
            if not txt:
                continue
            if cur and cur["speaker"] == spk and st - cur["end"] < 1.5:
                cur["text"] += " " + txt
                cur["end"] = en
            else:
                cur = {"start": st, "end": en, "text": txt, "speaker": spk}
                out.append(cur)
        if out:
            return out

    # 2) words with timings (no speaker): sentences with precise timings
    if words:
        out = words_to_sentences(words)
        if out:
            return out

    # 3) segments
    out = []
    for s in segs:
        txt = str(s.get("text", "")).strip()
        if not txt:
            continue
        spk = _speaker_index(s.get("speaker", s.get("speaker_label")))
        st, en = float(s.get("start", 0)), float(s.get("end", duration))
        out.extend(_split_tags(txt, st, en, spk))
    if out:
        return out

    # 4) text only
    txt = str(resp.get("text", "")).strip()
    if not txt:
        return []
    return _split_tags(txt, 0.0, duration, None)


def words_to_sentences(words, max_gap=0.8, max_sec=15.0, max_chars=200):
    """Groups words into sentences: a new one starts after . ? ! … or on a pause, or if the sentence is too long."""
    out, cur = [], None
    for w in words:
        if w.get("type") == "audio_event":
            continue
        txt = str(w.get("word", "")).strip()
        if not txt:
            continue
        st, en = float(w.get("start", 0)), float(w.get("end", 0))
        if cur is not None:
            prev_end_sentence = cur["text"].rstrip()[-1:] in ".?!…"
            too_long = en - cur["start"] > max_sec and cur["text"].rstrip()[-1:] in ",;:"
            if prev_end_sentence or st - cur["end"] > max_gap or too_long or len(cur["text"]) > max_chars:
                cur = None
        if cur is None:
            cur = {"start": st, "end": en, "text": txt, "speaker": None}
            out.append(cur)
        else:
            cur["text"] += " " + txt
            cur["end"] = en
    return out


def _split_tags(text, start, end, speaker):
    """Handles any <spk:N> tags in the text, distributing the time in proportion to the length."""
    if not SPK_TAG.search(text):
        return [{"start": start, "end": end, "text": text, "speaker": speaker}]
    pieces = []
    cur_spk = speaker
    pos = 0
    for m in SPK_TAG.finditer(text):
        before = text[pos:m.start()].strip()
        if before:
            pieces.append((cur_spk, before))
        cur_spk = int(m.group(1))
        pos = m.end()
    tail = text[pos:].strip()
    if tail:
        pieces.append((cur_spk, tail))
    total = sum(len(t) for _x, t in pieces) or 1
    out, t = [], start
    for spk, txt in pieces:
        d = (end - start) * len(txt) / total
        out.append({"start": t, "end": t + d, "text": txt, "speaker": spk})
        t += d
    return out


class DeepInfraClient:
    """Nemotron 3.5 Transcribe on DeepInfra's native endpoint: transcription with word timings and, for the call
    audio, voice separation."""

    def __init__(self, key, log=print, ffmpeg=None, codec="opus", bitrate_kbps=32):
        self.key = (key or "").strip()
        self.log = log
        self.total_cost = 0.0
        self.bytes_sent = 0
        self._lock = threading.Lock()
        self.session = requests.Session()
        self.ffmpeg = ffmpeg
        # format chosen in Settings, then the fallbacks if the service does not accept it
        first = CODECS.get(codec, "ogg")
        self.formats = list(dict.fromkeys([first, "flac", "wav"])) if ffmpeg else ["wav"]
        self.kbps = max(8, min(256, int(bitrate_kbps or 32)))  # for opus and mp3, specific to this client
        self.diarize_ok = bool(self.key)  # becomes False if DeepInfra rejects voice separation
        self._warned_no_diarize = False

    def transcribe_samples(self, x, sr, timeout=120):
        """Compresses the audio chunk (Opus → FLAC → WAV) and transcribes it. Returns (segments, response)."""
        duration = len(x) / sr
        while True:
            with self._lock:
                fmt = self.formats[0]
            try:
                audio = encode_audio(self.ffmpeg, x, sr, fmt, self.kbps)
            except Exception as e:
                if self._drop_format(fmt, f"{fmt} compression failed ({e})"):
                    continue
                raise ApiError(_("Cannot prepare the audio: {0}", e))
            with self._lock:
                self.bytes_sent += len(audio)
            try:
                return self._plain(audio, fmt, duration, timeout)
            except FormatRejected as e:
                if not self._drop_format(fmt, str(e)):
                    raise ApiError(str(e))

    def transcribe_diarized(self, x, sr, timeout=900):
        """Transcription with voice separation (speaker_0, speaker_1…).
        Returns (segments, response) or None if separation is not possible."""
        with self._lock:
            if not self.diarize_ok:
                if not self._warned_no_diarize:
                    self._warned_no_diarize = True
                    self.log(_("Separating the voices of the call needs the DeepInfra key (Settings)."))
                return None
        audio = fmt = None
        for f in list(self.formats):
            try:
                audio, fmt = encode_audio(self.ffmpeg, x, sr, f, self.kbps), f
                break
            except Exception as e:
                err = e
        if audio is None:
            raise ApiError(_("Cannot prepare the audio: {0}", err))
        with self._lock:
            self.bytes_sent += len(audio)
        try:
            return self._diarized(audio, fmt, len(x) / sr, timeout)
        except DiarizeUnavailable as e:
            with self._lock:
                if self.diarize_ok:
                    self.diarize_ok = False
                    self._warned_no_diarize = True
                    self.log(_("Voice separation via {0} not available ({1}).", "DeepInfra", e))
            return None

    def _plain(self, audio, fmt, duration, timeout):
        """Plain transcription, with word timings."""
        if not self.key:
            raise ApiError(_("The DeepInfra key is missing"))
        r = self._post({"chunk_level": "word"}, audio, fmt, timeout)
        if r.status_code in (400, 415, 422) and fmt != "wav":
            raise FormatRejected(_("error {0}: {1}", r.status_code, _error_message(r)[:200]))
        if r.status_code in (401, 403):
            raise ApiError(_("Invalid DeepInfra key"))
        if r.status_code == 402:
            raise ApiError(_("DeepInfra credit used up (402)"))
        return self._parse(r, duration)

    def _diarized(self, audio, fmt, duration, timeout):
        r = self._post({"diarize": "true", "chunk_level": "word"}, audio, fmt, timeout)
        if r.status_code in (401, 403):
            raise DiarizeUnavailable(_("invalid DeepInfra key"))
        if r.status_code in (400, 404, 422):
            raise DiarizeUnavailable(_error_message(r)[:150])
        return self._parse(r, duration)

    def _post(self, data, audio, fmt, timeout):
        for attempt in range(1, 5):
            try:
                r = self.session.post(DEEPINFRA_URL, headers={"Authorization": f"bearer {self.key}"}, data=data,
                                      files={"audio": (f"audio.{fmt}", audio, MIME[fmt])}, timeout=timeout)
            except requests.RequestException as e:
                if attempt < 4:
                    time.sleep(2 ** attempt)
                    continue
                raise ApiError(_("Network error: {0}", e))
            if r.status_code in (408, 429, 500, 502, 503, 504, 524, 529) and attempt < 4:
                time.sleep(min(30, 2 ** attempt))
                continue
            return r

    def _parse(self, r, duration):
        if r.status_code != 200:
            raise ApiError(_("API error {0}: {1}", r.status_code, _error_message(r)))
        try:
            resp = r.json()
        except ValueError:
            raise ApiError(_("Invalid response from the API"))
        cost = (resp.get("inference_status") or {}).get("cost")
        if cost:
            with self._lock:
                self.total_cost += float(cost)
        return normalize(resp, duration), resp

    def _drop_format(self, fmt, why):
        """Switches to the next format; False if there are no others."""
        with self._lock:
            if self.formats[0] == fmt and len(self.formats) > 1:
                self.formats.pop(0)
                self.log(_("Format {0} not usable: {1}. Switching to {2}.", fmt, why, self.formats[0]))
            return self.formats[0] != fmt


MIME = {"ogg": "audio/ogg", "mp3": "audio/mpeg", "flac": "audio/flac", "wav": "audio/wav"}
CODECS = {  # choice in Settings → format of the file sent
    "opus": "ogg",
    "mp3": "mp3",
    "flac": "flac",
    "wav": "wav",
}


def encode_args(fmt, kbps=32):
    kb = f"{int(kbps)}k"
    return {
        "ogg": ["-c:a", "libopus", "-b:a", kb, "-application", "voip", "-f", "ogg"],
        "mp3": ["-c:a", "libmp3lame", "-b:a", kb, "-f", "mp3"],
        "flac": ["-c:a", "flac", "-compression_level", "5", "-f", "flac"],
    }[fmt]


class FormatRejected(Exception):
    pass


class DiarizeUnavailable(Exception):
    pass


def encode_audio(ffmpeg, x, sr, fmt, kbps=32):
    wav = wav_bytes(x, sr)
    if fmt == "wav":
        return wav
    if not ffmpeg:
        raise RuntimeError(_("ffmpeg not available"))
    r = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "wav", "-i", "pipe:0",
                        *encode_args(fmt, kbps), "pipe:1"],
                       input=wav, capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=120)
    if r.returncode != 0 or len(r.stdout) < 100:
        raise RuntimeError(r.stderr.decode("utf-8", "replace").strip()[-200:] or "empty output")
    return r.stdout


def _error_message(r):
    try:
        j = r.json()
        e = j.get("error") or {}
        return str(e.get("message") or j)[:500]
    except ValueError:
        return r.text[:500]
