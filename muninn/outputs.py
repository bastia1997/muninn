"""Writing of the final files: transcript (md, srt, json), mixed audio and video with audio."""
import json
import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from .video import CREATE_NO_WINDOW


def fmt_ts(sec, ms=False):
    sec = max(0.0, sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if ms:
        return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int(round((s - int(s)) * 1000)) % 1000:03d}"
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d}"


def speaker_name(seg):
    if seg.get("display"):
        return seg["display"]
    if seg.get("speaker") is None or not seg.get("diarized"):
        return seg["source"]
    spk = seg["speaker"]
    n = spk + 1 if isinstance(spk, int) else spk
    return f"{seg['source']} · Speaker {n}"


def merge_turns(segs, max_gap=2.0):
    """Joins consecutive segments of the same speaker for smoother reading."""
    out = []
    for s in sorted(segs, key=lambda x: x["start"]):
        who = speaker_name(s)
        if out and out[-1]["who"] == who and s["start"] - out[-1]["end"] <= max_gap:
            out[-1]["text"] += " " + s["text"]
            out[-1]["end"] = max(out[-1]["end"], s["end"])
        else:
            out.append({"who": who, "start": s["start"], "end": s["end"], "text": s["text"]})
    return out


def write_transcripts(folder, segs, meta, basename="transcript", srt_path=None):
    t0 = datetime.fromtimestamp(meta["t0"])
    segs = sorted(segs, key=lambda x: x["start"])

    # full JSON (for programs and AI)
    data = {
        "session": meta,
        "timing_note": "start/end are seconds from the start of the recording; local_time is the real clock time.",
        "segments": [
            {
                "start": round(s["start"], 2), "end": round(s["end"], 2),
                "local_time": (t0 + timedelta(seconds=s["start"])).strftime("%H:%M:%S"),
                "speaker": speaker_name(s), "speaker_id": s.get("pid"), "source": s["source"],
                "text": s["text"],
            }
            for s in segs
        ],
    }
    (folder / f"{basename}.json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    # SRT (subtitles for the videos)
    write_srt(segs, srt_path or folder / f"{basename}.srt")

    # readable Markdown
    dur = meta.get("duration_sec", 0)
    md = [
        f"# Transcript of {t0.strftime('%d/%m/%Y')} at {t0.strftime('%H:%M')}",
        "",
        f"- Duration: {fmt_ts(dur)}",
        f"- Audio sources: {', '.join(s['label'] for s in meta.get('sources', []))}",
        f"- Video: {', '.join(Path(v['file']).name for v in meta.get('video', [])) or 'none'}",
        f"- Model: {meta.get('model', '')}",
        "",
        "Times in brackets are from the start of the recording (the videos already contain the subtitles). "
        "Each audio source is transcribed separately, so the source label already tells who is speaking "
        "(e.g. microphone = you). The voices of the call are separated by Nemotron; names are inferred from video screenshots "
        "(\"(?)\" = uncertain, \"Speaker N\" = separated voice without a name).",
        "",
    ]
    if meta.get("speakers"):
        md += ["## Speakers", "", "| Who | Speaking time | Turns |", "|---|---|---|"]
        md += [f"| {d['name']} | {fmt_ts(d['speaking_time_sec'])} | {d['turns']} |" for d in meta["speakers"]]
        md += [""]
    md += ["## Transcript", ""]
    for t in merge_turns(segs):
        clock = (t0 + timedelta(seconds=t["start"])).strftime("%H:%M:%S")
        md.append(f"**[{fmt_ts(t['start'])} · {clock}] {t['who']}:** {t['text']}")
        md.append("")
    (folder / f"{basename}.md").write_text("\n".join(md), encoding="utf-8")


def write_srt(segs, path, shift=0.0, names=True):
    """shift: seconds to subtract (to align with a video that started after the audio).
    names: False writes only the spoken text, without the «Name: » prefix.
    Overlapping sentences (e.g. you and the call together) become a single multi-line cue,
    because the MP4 subtitle track does not allow overlaps."""
    items = []
    for s in segs:
        en0 = s["end"] if s["end"] > s["start"] + 0.2 else s["start"] + 2
        who = speaker_name(s)  # also used without names, to tell different people apart below
        prefix = f"{who}: " if names else ""
        for st, en, txt in split_cue(s["start"], en0, s["text"], max_chars=max(40, 84 - len(prefix))):
            st, en = st - shift, en - shift
            if en > 0:
                items.append([max(0.0, st), en, wrap(prefix + txt), who])
    # small overlaps between different people (< 1 s): shorten the previous cue instead of merging them
    items.sort()
    for k, it in enumerate(items):
        for nxt in items[k + 1:k + 6]:
            if nxt[3] != it[3] and it[0] < nxt[0] < it[1] and it[1] - nxt[0] < 1.0:
                it[1] = nxt[0]
                break
    items = [(a, b, txt) for a, b, txt, _x in items if b - a > 0.05]
    cues = []
    bounds = sorted({t for a, b, _x in items for t in (a, b)})
    active, k = [], 0
    for b0, b1 in zip(bounds, bounds[1:]):
        while k < len(items) and items[k][0] <= b0:
            active.append(items[k])
            k += 1
        active = [it for it in active if it[1] > b0]
        if not active:
            continue
        text = "\n".join(it[2] for it in active)
        if cues and cues[-1][2] == text and abs(cues[-1][1] - b0) < 1e-6:
            cues[-1][1] = b1
        else:
            cues.append([b0, b1, text])
    lines = []
    for i, (a, b, text) in enumerate(cues, 1):
        lines += [str(i), f"{fmt_ts(a, True)} --> {fmt_ts(b, True)}", text, ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def split_cue(start, end, text, max_chars=78, max_sec=6.0):
    """Splits a sentence into short, balanced cues (max ~2 lines), dividing the time proportionally."""
    words = text.split()
    if not words:
        return []
    total = len(text)
    dur = end - start
    n = max(1, -(-total // max_chars), round(dur / max_sec))
    n = min(n, len(words))
    target = total / n
    chunks, cur = [], ""
    for w in words:
        if cur and len(cur) >= target * 0.85 and len(chunks) < n - 1:
            chunks.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    chunks.append(cur)
    tot = sum(len(c) for c in chunks) or 1
    out, t = [], start
    for c in chunks:
        d = dur * len(c) / tot
        out.append((t, t + d, c))
        t += d
    return out


def wrap(text, width=42):
    """Wraps at ~42 characters (two readable lines)."""
    lines, cur = [], ""
    for w in text.split():
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        lines.append(cur)
    return "\n".join(lines)


def run_ffmpeg(cmd, log, timeout=None):
    try:
        r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                           creationflags=CREATE_NO_WINDOW, timeout=timeout)
        if r.returncode != 0:
            log("ffmpeg: " + r.stderr.decode("utf-8", "replace").strip()[-400:])
            return False
        return True
    except Exception as e:
        log(f"ffmpeg: {e}")
        return False


def mix_audio(ffmpeg, wav_paths, out_path, log):
    if not wav_paths:
        return False
    if len(wav_paths) == 1:
        cmd = [ffmpeg, "-hide_banner", "-y", "-i", str(wav_paths[0]), "-c:a", "aac", "-b:a", "64k", str(out_path)]
    else:
        cmd = [ffmpeg, "-hide_banner", "-y"]
        for p in wav_paths:
            cmd += ["-i", str(p)]
        cmd += ["-filter_complex", f"amix=inputs={len(wav_paths)}:duration=longest:normalize=0",
                "-c:a", "aac", "-b:a", "64k", str(out_path)]
    return run_ffmpeg(cmd, log)


def mux_video(ffmpeg, video_path, audio_path, offset_sec, out_path, log):
    """Joins video and mixed audio; offset_sec = start delay of the video relative to the audio."""
    cmd = [ffmpeg, "-hide_banner", "-y", "-i", str(video_path),
           "-ss", f"{max(0.0, offset_sec):.3f}", "-i", str(audio_path),
           "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "copy", "-shortest",
           "-movflags", "+faststart", str(out_path)]
    return run_ffmpeg(cmd, log)


def embed_subtitles(ffmpeg, video_path, tracks, log):
    """Adds (or replaces) the subtitle tracks inside the video, without re-encoding.
    tracks: list of (srt_path, title); the first one is the default track. Subtitle tracks already in the video
    are dropped, so running it again replaces them."""
    tmp = video_path.with_name(video_path.stem + ".tmp.mp4")
    cmd = [ffmpeg, "-hide_banner", "-y", "-i", str(video_path)]
    for srt_path, _title in tracks:
        cmd += ["-i", str(srt_path)]
    cmd += ["-map", "0:v", "-map", "0:a?"]
    for i in range(len(tracks)):
        cmd += ["-map", f"{i + 1}:0"]
    cmd += ["-c:v", "copy", "-c:a", "copy", "-c:s", "mov_text"]
    for i, (_srt, title) in enumerate(tracks):
        cmd += [f"-metadata:s:s:{i}", "language=ita", f"-metadata:s:s:{i}", f"title={title}",
                f"-disposition:s:{i}", "default" if i == 0 else "0"]
    cmd += ["-movflags", "+faststart", str(tmp)]
    if run_ffmpeg(cmd, log):
        os.replace(tmp, video_path)
        return True
    try:
        tmp.unlink()
    except OSError:
        pass
    return False
