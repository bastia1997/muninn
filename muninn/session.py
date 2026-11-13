"""A recording session (audio + video) and the transcription of the saved files.

Folder of a recording:
    video_<name>.mp4          video with audio and subtitle track
    transcript.md / .json     transcript for people and for AI
    speakers.json             speaker names (can be fixed by hand)
    data/                     audio tracks, session.json, raw transcript, frames, .srt, API responses
"""
import json
import os
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from .i18n import _
from . import outputs, speakers
from .audio import AudioSource
from .dsp import align_text, has_speech, read_wav, speech_regions, split_blocks
from .transcriber import DeepInfraClient
from .video import VIDEO_CODECS, VideoRecorder, _encode_args, build_gdigrab_cmd, build_monitor_cmd, find_ffmpeg, video_settings
from .winutil import region_for_window

DATA = "data"


def _safe(name):
    keep = "".join(c if c.isalnum() or c in " -_" else "_" for c in name).strip()
    return "_".join((keep or "x")[:40].split())


def data_dir(folder):
    return Path(folder) / DATA


def meta_path(folder):
    return data_dir(folder) / "session.json"


def raw_path(folder):
    return data_dir(folder) / "transcript_raw.json"


def load_meta(folder):
    return json.loads(meta_path(folder).read_text(encoding="utf-8"))


def save_meta(folder, meta):
    p = meta_path(folder)
    p.parent.mkdir(exist_ok=True)
    p.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")


def is_recording_folder(folder):
    return meta_path(folder).exists()


class Session:
    def __init__(self, cfg, sources, monitors, windows, all_monitors, log, on_status):
        """sources: [{info, label, diarize}]; monitors: [mon]; windows: [{hwnd, title, method}]"""
        self.cfg = cfg
        self.sel_sources = sources
        self.sel_monitors = monitors
        self.sel_windows = windows
        self.all_monitors = all_monitors
        self.log = log
        self.on_status = on_status
        self.audio = []
        self.videos = []
        self.ffmpeg = find_ffmpeg()
        self.t0 = None
        self.t_end = None
        self.folder = None

    # ---------------- recording ----------------
    def start(self):
        out = Path(self.cfg["output_dir"])
        self.folder = out / datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        data = data_dir(self.folder)
        data.mkdir(parents=True, exist_ok=True)
        self.t0 = time.time()

        used = set()
        for s in self.sel_sources:
            name = _safe(s["label"])
            while name in used:
                name += "_"
            used.add(name)
            src = AudioSource(s["info"], s["label"], s["diarize"], data / f"audio_{name}.wav", self.t0,
                              0, None, log=self.log)
            try:
                src.start()
                self.audio.append(src)
            except Exception as e:
                self.log(_("Audio '{0}' not started: {1}", s['label'], e))

        if (self.sel_monitors or self.sel_windows) and not self.ffmpeg:
            self.log(_("ffmpeg not found: no video (run install.bat again)."))
        elif self.ffmpeg:
            fps = int(self.cfg["video_fps"])
            enc = video_settings(self.cfg)
            # chosen encoding, and H.264 as fallback if that one is not available on this PC
            encs = [enc] + ([dict(enc, codec="h264")] if enc["codec"] != "h264" else [])
            used = set()
            for m in self.sel_monitors:
                self._start_video(f"Screen {m['index'] + 1}", f"screen{m['index'] + 1}", used, lambda path, m=m: [
                    c for e in encs for c in (
                        build_monitor_cmd(self.ffmpeg, m, path, fps, e),
                        build_gdigrab_cmd(self.ffmpeg, path, fps, e, rect=(m["x"], m["y"], m["w"], m["h"])))])
            for w in self.sel_windows:
                def cmds(path, w=w):
                    region = region_for_window(w["hwnd"], self.all_monitors)
                    out = []
                    for e in encs:
                        area = None
                        if region:
                            mon, ox, oy, rw, rh = region
                            area = build_monitor_cmd(self.ffmpeg, mon, path, fps, e, region=(ox, oy, rw, rh))
                        direct = build_gdigrab_cmd(self.ffmpeg, path, fps, e, title=w["title"])
                        out += [c for c in ([direct, area] if w["method"] == "direct" else [area, direct]) if c]
                    return out
                short = w["title"].split("|")[-1].strip() if "|" in w["title"] else w["title"]
                self._start_video(w["title"], _safe(short)[:30], used, cmds)

        if not self.audio and not self.videos:
            raise RuntimeError(_("No source started"))
        self.on_status(_("Recording → {0}", self.folder))

    def _start_video(self, name, short, used, make_cmds):
        while short in used:
            short += "_2"
        used.add(short)
        path = data_dir(self.folder) / f"video_{short}_raw.mp4"
        v = VideoRecorder(name, make_cmds(path), path, log=self.log)
        v.short = short
        if v.start():
            self.videos.append(v)
            enc = video_settings(self.cfg)["codec"]
            if enc != "h264" and v.used >= len(v.cmds) // 2:
                self.log(_("Video \"{0}\": {1} is not available on this PC, using H.264.", name, VIDEO_CODECS[enc][0]))

    def levels(self):
        return [(s.label, s.level) for s in self.audio]

    def elapsed(self):
        return (self.t_end or time.time()) - self.t0 if self.t0 else 0

    def stop(self, progress=lambda msg: None):
        """Blocking: closes the files, merges audio and video, then transcribes. Call from a separate thread."""
        self.t_end = time.time()
        timer = Timer(self.log)
        progress(_("Closing audio and video…"))
        for s in self.audio:
            s.stop(self.t_end)
        for v in self.videos:
            v.stop()
        timer.lap("Closing the files")

        rel = lambda p: p.relative_to(self.folder).as_posix()
        meta = {
            "t0": self.t0,
            "start": datetime.fromtimestamp(self.t0).isoformat(timespec="seconds"),
            "duration_sec": round(self.t_end - self.t0, 1),
            "sources": [{"label": s.label, "device": s.info["name"], "file": rel(s.wav_path),
                         "diarize": s.diarize} for s in self.audio],
            "video": [],
        }
        mix = None
        if self.ffmpeg and self.audio:
            progress(_("Creating the combined audio…"))
            mix = data_dir(self.folder) / "audio_mix.m4a"
            if outputs.mix_audio(self.ffmpeg, [s.wav_path for s in self.audio], mix, self.log):
                meta["audio_mix"] = rel(mix)
            else:
                mix = None
            timer.lap("Combined audio")
        for v in self.videos:
            final = self.folder / f"video_{v.short}.mp4"
            delay = v.start_time - self.t0
            ok = False
            if mix:
                progress(_("Adding the audio to video {0}…", v.name))
                ok = outputs.mux_video(self.ffmpeg, v.out_path, mix, delay, final, self.log)
            if ok:
                _remove(v.out_path, self.log)  # no duplicates: keep only the video with audio
            else:
                shutil.move(str(v.out_path), str(final))
            meta["video"].append({"name": v.name, "file": rel(final), "offset_vs_audio_sec": round(delay, 2),
                                  "has_audio": ok})
        if self.videos:
            timer.lap("Final videos")
        meta["timings_sec"] = timer.laps
        save_meta(self.folder, meta)

        if self.audio:
            transcribe_folder(self.folder, self.cfg, self.log, progress)
        else:
            progress(_("Done (video only, no transcript)."))
        self.log(_("Total time after \"Stop\": {0:.1f} s", time.time() - self.t_end))
        return self.folder


def _remove(path, log):
    try:
        os.remove(path)
    except OSError as e:
        log(_("Cannot delete {0}: {1}", Path(path).name, e))


# ---------------- transcription of files ----------------

MEDIA_TYPES = [(_("Audio and video"), "*.mp4 *.mkv *.mov *.webm *.avi *.m4v *.mp3 *.wav *.m4a *.aac *.ogg *.opus *.flac *.wma"),
               (_("All files"), "*.*")]


def probe(ffmpeg, path):
    """(duration in seconds, has video?, has audio?) reading the header with ffmpeg."""
    import re
    import subprocess
    from .video import CREATE_NO_WINDOW
    r = subprocess.run([ffmpeg, "-hide_banner", "-i", str(path)], capture_output=True, creationflags=CREATE_NO_WINDOW,
                       timeout=60)
    err = r.stderr.decode("utf-8", "replace")
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err)
    dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0.0
    has_video = bool(re.search(r"Stream #.*Video:", err)) and "attached pic" not in err
    has_audio = bool(re.search(r"Stream #.*Audio:", err))
    return dur, has_video, has_audio


def import_file(src, cfg, log, progress=lambda msg: None):
    """Processes an existing audio/video file as if it were a recording: creates the folder,
    extracts the audio, prepares the video, transcribes with voice separation and recognises the names."""
    src = Path(src)
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError(_("ffmpeg not found: run install.bat again"))
    timer = Timer(log)
    dur, has_video, has_audio = probe(ffmpeg, src)
    if not has_audio:
        raise RuntimeError(_("{0}: the file contains no audio", src.name))
    stem = _safe(src.stem)[:40]
    folder = Path(cfg["output_dir"]) / f"{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}_{stem}"
    data = data_dir(folder)
    data.mkdir(parents=True, exist_ok=True)
    log(_("File: {0} — length {1}, {2}", src.name, outputs.fmt_ts(dur), _("audio and video") if has_video else _("audio only")))

    progress(_("{0}: extracting the audio…", src.name))
    wav = data / "audio_file.wav"
    if not outputs.run_ffmpeg([ffmpeg, "-hide_banner", "-y", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000",
                               "-c:a", "pcm_s16le", str(wav)], log):
        raise RuntimeError(_("{0}: I cannot read the audio", src.name))
    timer.lap("Audio extracted")

    videos = []
    if has_video:
        progress(_("{0}: preparing the video…", src.name))
        final = folder / (f"{stem}.mp4" if stem.lower().startswith("video") else f"video_{stem}.mp4")
        ok = False
        if cfg.get("recompress_imported"):  # recompress with the chosen video settings (slower, smaller file)
            log(_("Recompressing the video with the chosen settings…"))
            enc = video_settings(cfg)
            for e in [enc] + ([dict(enc, codec="h264")] if enc["codec"] != "h264" else []):
                ok = outputs.run_ffmpeg([ffmpeg, "-hide_banner", "-y", "-i", str(src), "-map", "0:v:0", "-map", "0:a?",
                                         *_encode_args(int(cfg.get("video_fps", 10)), e, live=False),
                                         "-c:a", "aac", "-b:a", "96k", "-sn", "-movflags", "+faststart", str(final)],
                                        lambda m: None)
                if ok:
                    break
        if not ok:
            ok = outputs.run_ffmpeg([ffmpeg, "-hide_banner", "-y", "-i", str(src), "-map", "0:v:0", "-map", "0:a?",
                                     "-c", "copy", "-sn", "-movflags", "+faststart", str(final)], lambda m: None)
        if not ok:  # codecs not compatible with MP4: re-encode (slower)
            log(_("The video has to be converted to MP4: it takes a little longer."))
            ok = outputs.run_ffmpeg([ffmpeg, "-hide_banner", "-y", "-i", str(src), "-map", "0:v:0", "-map", "0:a?",
                                     "-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-c:a", "aac", "-b:a", "96k",
                                     "-sn", "-movflags", "+faststart", str(final)], log)
        if ok:
            videos.append({"name": src.stem, "file": final.relative_to(folder).as_posix(),
                           "offset_vs_audio_sec": 0.0, "has_audio": True})
        timer.lap("Video prepared")

    start = src.stat().st_mtime - dur  # start time estimated from the file date
    meta = {
        "t0": start,
        "start": datetime.fromtimestamp(start).isoformat(timespec="seconds"),
        "duration_sec": round(dur, 1),
        "original_file": str(src),
        "sources": [{"label": "Audio", "device": src.name, "file": wav.relative_to(folder).as_posix(),
                     "diarize": True}],
        "video": videos,
        "timings_sec": timer.laps,
    }
    save_meta(folder, meta)
    transcribe_folder(folder, cfg, log, progress)
    return folder


def transcribe_folder(folder, cfg, log, progress=lambda msg: None):
    """Transcribes the audio tracks of a recording turn by turn and writes the final files.
    Can be run again on an existing recording (e.g. if the API was not responding)."""
    folder = Path(folder)
    meta = load_meta(folder)
    timer = Timer(log)
    ffmpeg = find_ffmpeg()
    client = DeepInfraClient(cfg.get("deepinfra_key", ""), log=log, ffmpeg=ffmpeg,
                             codec=cfg.get("audio_codec", "opus"), bitrate_kbps=cfg.get("audio_bitrate_kbps", 32))
    block_sec = float(cfg.get("final_chunk_min", 8)) * 60
    diar_block_sec = float(cfg.get("diarize_block_minutes", 60)) * 60  # 60 min = Nemotron's maximum per request
    data_dir(folder).mkdir(exist_ok=True)
    resp_log = open(data_dir(folder) / "api_responses.jsonl", "w", encoding="utf-8")
    resp_lock = threading.Lock()

    # 1) each track is split into chunks of a few minutes, cut at pauses; silent chunks are skipped
    jobs = []
    for src in meta["sources"]:
        x, sr = read_wav(folder / src["file"])
        regions = speech_regions(x, sr)
        multi = bool(src.get("diarize"))
        for start, block in split_blocks(x, sr, diar_block_sec if multi else block_sec):
            a, b = start / sr, (start + len(block)) / sr
            regs = [(max(a, r0), min(b, r1)) for r0, r1 in regions if r1 > a and r0 < b]
            if regs and has_speech(block, sr=sr):
                jobs.append((src, a, b, regs, block, sr))
    if not jobs:
        log(_("No speech found in the audio tracks."))

    # 2) parallel transcription
    results, failed = [], []
    progress(_("Transcription: 0/{0} chunks", len(jobs)))
    with ThreadPoolExecutor(max_workers=int(cfg.get("parallel_requests", 8))) as ex:
        def run(src, block, sr):
            if src.get("diarize"):
                res = client.transcribe_diarized(block, sr)
                if res is not None:
                    return res
            return client.transcribe_samples(block, sr, 180)

        futs = {ex.submit(run, src, block, sr): k
                for k, (src, a, b, regs, block, sr) in enumerate(jobs)}
        for done, f in enumerate(as_completed(futs), 1):
            k = futs[f]
            src, a, b, regs, _x, _x = jobs[k]
            progress(_("Transcription: {0}/{1} chunks", done, len(jobs)))
            try:
                segs, resp = f.result()
            except Exception as e:
                failed.append((src["label"], a, b))
                log(_("Chunk {0}–{1} of '{2}' not transcribed: {3}", outputs.fmt_ts(a), outputs.fmt_ts(b), src['label'], e))
                continue
            with resp_lock:
                resp_log.write(json.dumps({"source": src["label"], "start": round(a, 2), "response": resp},
                                          ensure_ascii=False) + "\n")
            results.extend(_place(segs, src, a, b, regs, k, client))
    resp_log.close()
    if jobs:
        timer.lap(f"Transcription of {len(jobs)} chunks ({client.bytes_sent / 1e6:.1f} MB sent, "
                  f"format {client.formats[0]})")

    for label, a, b in failed:  # placeholder visible in the transcript
        results.append({"start": a, "end": b, "text": "[not transcribed: run «Transcribe again»]",
                        "speaker": None, "source": label, "diarized": False})

    meta.update({"model": "Nemotron 3.5 Transcribe (DeepInfra)",
                 "transcribed_at": datetime.now().isoformat(timespec="seconds"),
                 "untranscribed_blocks": len(failed),
                 "api_cost_usd": round(client.total_cost, 5)})
    meta.setdefault("timings_sec", {}).update(timer.laps)
    save_meta(folder, meta)

    # "raw" transcript: the base from which the files with names are regenerated
    ids = speakers.assign_ids(results)
    if any(s.get("diarize") for s in meta["sources"]):
        log(_("Voice separation: ") + (_("{0} voices found via {1}", len(ids), "DeepInfra")
                                    if ids else _("failed, the call stays a single voice")))
    if ids and len({i["block"] for i in ids.values()}) > len({i["source"] for i in ids.values()}):
        # the call spans several chunks: link the voices of the same person with a collage of clips
        t_link = time.time()
        try:
            merge = speakers.link_blocks(folder, meta, results, ids, client, log)
        except Exception as e:
            merge = {}
            log(_("Linking chunks failed ({0}): the voices of each chunk stay separate.", e))
        for s in results:
            if s.get("pid") in merge:
                s["pid"] = merge[s["pid"]]
        for p, canon in merge.items():
            ids[canon].setdefault("merged_with", []).append(p)
            ids.pop(p, None)
        if merge:
            log(_("Voices merged across chunks: ") + ", ".join(f"{speakers.placeholder(p)} = {speakers.placeholder(c)}"
                                                    for p, c in sorted(merge.items())))
        log(_("⏱ Linking chunks: {0:.1f} s — now {1} speakers", time.time() - t_link, len(ids)))
        # renumber without gaps (Speaker 1, 2, 3…) in order of first appearance
        first = {}
        for seg in sorted(results, key=lambda r: r["start"]):
            if seg.get("pid") and seg["pid"] not in first:
                first[seg["pid"]] = len(first) + 1
        rename = {old: f"S{n:02d}" for old, n in first.items()}
        for seg in results:
            if seg.get("pid"):
                seg["pid"] = rename[seg["pid"]]
        ids = {rename[p]: info for p, info in ids.items() if p in rename}
    raw_path(folder).write_text(json.dumps(
        {"speaker_ids": ids, "segments": sorted(results, key=lambda r: r["start"])},
        indent=1, ensure_ascii=False), encoding="utf-8")

    if ids:
        speakers.write_placeholders(folder, ids)  # you assign the names in the «Speakers» window
    render_outputs(folder, cfg, log, progress)

    msg = f"Done. Transcription cost: {client.total_cost:.4f} $"
    if failed:
        msg += f" — {len(failed)} chunks not transcribed, retry with «Transcribe again»"
    progress(msg)
    return not failed


def _place(segs, src, a, b, regs, block_id, client):
    """Maps the segments of the response onto the recording's timeline.
    If the API gives reliable internal timings they are used, otherwise sentences are spread over the detected speech."""
    diarized = any(s.get("speaker") is not None for s in segs)
    dur = b - a
    timed = len(segs) > 1 or (segs and (segs[0]["start"] > 0.05 or segs[0]["end"] < dur - 0.5))
    out = []
    if timed:
        for s in segs:
            out.append({"start": a + s["start"], "end": a + min(s["end"], dur), "text": s["text"],
                        "speaker": s["speaker"], "source": src["label"], "block": block_id, "diarized": diarized})
    else:
        text = " ".join(s["text"] for s in segs).strip()
        spk = segs[0]["speaker"] if segs else None
        for st, en, sent in align_text(text, regs):
            out.append({"start": st, "end": en, "text": sent, "speaker": spk, "source": src["label"],
                        "block": block_id, "diarized": diarized})
    return out


def _load_raw(folder):
    raw = json.loads(raw_path(folder).read_text(encoding="utf-8"))
    return raw["segments"], raw.get("speaker_ids", {})


def render_outputs(folder, cfg, log, progress=lambda msg: None):
    """Regenerates transcript.md/.json and the subtitles inside the videos, with the names from speakers.json.
    Can be run again after fixing speakers.json by hand."""
    folder = Path(folder)
    t = time.time()
    meta = load_meta(folder)
    segs, ids = _load_raw(folder)
    speakers.display_names(segs, speakers.load_names(folder), cfg.get("user_name", ""))

    agg = {}
    for s in segs:
        base = s["display"].removesuffix(" (?)")
        d = agg.setdefault(base, {"name": base, "seconds": 0.0, "turns": 0, "id": set()})
        d["seconds"] += max(0.0, s["end"] - s["start"])
        d["turns"] += 1
        if s.get("pid"):
            d["id"].add(s["pid"])
    meta["speakers"] = [
        {"name": d["name"], "speaking_time_sec": round(d["seconds"]), "turns": d["turns"],
         "id": sorted(d["id"])}
        for d in sorted(agg.values(), key=lambda d: -d["seconds"])
    ]

    data = data_dir(folder)
    outputs.write_transcripts(folder, segs, meta, srt_path=data / "transcript.srt")
    ffmpeg = find_ffmpeg()
    for item in meta.get("video", []):
        vfile = item.get("file")
        if not vfile or not (folder / vfile).exists():
            continue
        shift = item.get("offset_vs_audio_sec", 0)
        srt = data / (Path(vfile).stem + ".srt")
        srt_plain = data / (Path(vfile).stem + "_no_names.srt")  # same subtitles without the speaker's name
        outputs.write_srt(segs, srt, shift=shift)
        outputs.write_srt(segs, srt_plain, shift=shift, names=False)
        item["subtitles"] = srt.relative_to(folder).as_posix()
        item["subtitles_no_names"] = srt_plain.relative_to(folder).as_posix()
        if ffmpeg and segs:
            progress(_("Adding subtitles to {0}…", Path(vfile).name))
            tracks = [(srt, "Transcript"), (srt_plain, "Transcript (no names)")]
            item["subtitles_in_video"] = outputs.embed_subtitles(ffmpeg, folder / vfile, tracks, log)
    save_meta(folder, meta)
    log(_("⏱ Final files and subtitles: {0:.1f} s", time.time() - t))


class Timer:
    """Measures each step and writes its duration to the log."""

    def __init__(self, log):
        self.log = log
        self.t = time.time()
        self.laps = {}

    def lap(self, name):
        now = time.time()
        self.laps[name] = round(now - self.t, 1)
        self.log(f"⏱ {name}: {now - self.t:.1f} s")
        self.t = now
