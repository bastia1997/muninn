"""Speakers. Nemotron separates the voices (Speaker 1, 2…); here the ids are assigned, the data is prepared for the
window where you give names by listening to a few clips, and the voices of the same person are linked across chunks."""
import json
from pathlib import Path

from .i18n import _


# ---------------- speakers ----------------

def assign_ids(segs):
    """Unique id (S01, S02…) for each voice separated by Nemotron, in order of first appearance.
    If the call is split into several chunks, the same person can have different ids: the name joins them."""
    keys = {}
    for s in sorted(segs, key=lambda x: x["start"]):
        if s.get("diarized") and s.get("speaker") is not None:
            k = (s["source"], s.get("block", 0), s["speaker"])
            if k not in keys:
                keys[k] = f"S{len(keys) + 1:02d}"
            s["pid"] = keys[k]
        else:
            s["pid"] = None
    return {pid: {"source": k[0], "block": k[1], "nemotron_voice": k[2]} for k, pid in keys.items()}


def placeholder(pid):
    return f"Speaker {int(pid[1:])}"


def speaker_stats(segs):
    st = {}
    for s in segs:
        k = s.get("pid") or s["source"]
        d = st.setdefault(k, {"seconds": 0.0, "turns": 0})
        d["seconds"] += max(0.0, s["end"] - s["start"])
        d["turns"] += 1
    return st


def solo_moments(segs, pid, max_n=3, min_gap=20.0):
    """Moments where only that speaker talks (no other overlapping voice): the longest ones, spaced apart."""
    mine = [s for s in segs if s.get("pid") == pid]
    others = [s for s in segs if s.get("pid") != pid]

    def alone(s):
        return all(o["end"] <= s["start"] or o["start"] >= s["end"] for o in others)

    picked = []
    for s in sorted(mine, key=lambda s: (alone(s), s["end"] - s["start"]), reverse=True):
        t = (s["start"] + s["end"]) / 2
        if all(abs(t - p[0]) >= min_gap for p in picked):
            picked.append((t, s["text"]))
        if len(picked) >= max_n:
            break
    return sorted(picked)


def sample_lines(segs, pid, n_long=1):
    """Representative lines: the first, the last and the longest (whoever opens and closes the recording is recognisable)."""
    mine = sorted((s for s in segs if s.get("pid") == pid), key=lambda s: s["start"])
    if not mine:
        return []
    picks = [mine[0], mine[-1]] + sorted(mine, key=lambda s: len(s["text"]), reverse=True)[:n_long]
    out, seen = [], set()
    for s in sorted(picks, key=lambda s: s["start"]):
        if id(s) not in seen:
            seen.add(id(s))
            out.append(((s["start"] + s["end"]) / 2, s["text"]))
    return out


def write_placeholders(folder, ids):
    """speakers.json with placeholders only (Speaker N): the names are assigned in the dedicated window."""
    data = {
        "instructions": "Assign the names from the program (Transcript tab → Name the speakers), "
                        "or type them here in the «name» fields and press «Apply edited speakers.json».",
        "speakers": {pid: {"name": None, "placeholder": placeholder(pid), **info} for pid, info in ids.items()},
    }
    (Path(folder) / "speakers.json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def load_names(folder):
    p = Path(folder) / "speakers.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("speakers", {})
    except ValueError:
        return {}


def display_names(segs, names, user_name=""):
    """seg['display'] = real name, «Speaker N», or the source (e.g. «Me» → your name)."""
    for s in segs:
        pid = s.get("pid")
        if not pid:
            s["display"] = user_name if (user_name and s["source"] == "Me") else s["source"]
            continue
        n = names.get(pid) or {}
        if n.get("name"):
            s["display"] = n["name"] + (" (?)" if n.get("confidence") == "low" else "")
        else:
            s["display"] = placeholder(pid)


# ---------------- voices of the same person in different chunks ----------------

def link_blocks(folder, meta, segs, ids, client, log, clip_sec=6.0, clips_per_voice=3, gap=1.0, min_share=0.6):
    """If the call was split into several chunks, Nemotron numbers the voices from scratch in each one. Here a few
    clips of every voice of every chunk are taken, joined into one short audio and separated again by Nemotron:
    the clips of the same person end up under the same voice. Returns {id to merge: main id}."""
    import numpy as np
    from .dsp import read_wav

    blocks = {}
    for pid, info in ids.items():
        blocks.setdefault(info["source"], set()).add(info["block"])
    mapping = {}
    for src, blks in blocks.items():
        if len(blks) < 2:
            continue
        pids = sorted(p for p, i in ids.items() if i["source"] == src)
        src_file = next((x["file"] for x in meta["sources"] if x["label"] == src), None)
        if not src_file:
            continue
        x, sr = read_wav(Path(folder) / src_file)
        silence = np.zeros(int(gap * sr), dtype=np.float32)
        pieces, spans, t = [], [], 0.0
        for pid in pids:
            for seg in _solo_segments(segs, pid, clips_per_voice):
                mid = (seg["start"] + seg["end"]) / 2
                a = max(seg["start"], mid - clip_sec / 2)
                b = min(seg["end"], a + clip_sec)
                clip = x[int(a * sr): int(b * sr)]
                if len(clip) < int(1.5 * sr):
                    continue
                spans.append((pid, t, t + len(clip) / sr))
                pieces += [clip, silence]
                t += len(clip) / sr + gap
        if len({p for p, _x, _x in spans}) < 2:
            continue
        log(_("Linking voices across the chunks of \"{0}\": {1} clips, {2:.0f} s of audio…", src, len(spans), t))
        res = client.transcribe_diarized(np.concatenate(pieces), sr)
        if res is None:
            log(_("Linking chunks failed: the voices of each chunk stay separate."))
            continue
        csegs, _x = res
        votes = {}
        for pid, a, b in spans:
            for s in csegs:
                if s.get("speaker") is None:
                    continue
                o = min(b, s["end"]) - max(a, s["start"])
                if o > 0:
                    v = votes.setdefault(pid, {})
                    v[s["speaker"]] = v.get(s["speaker"], 0.0) + o
        label = {}
        for pid, v in votes.items():
            best = max(v, key=v.get)
            if v[best] / sum(v.values()) >= min_share:  # voice clearly attributed
                label[pid] = best
        groups = {}
        for pid in sorted(label, key=lambda p: (ids[p]["block"], p)):
            grp = groups.setdefault(label[pid], [])
            if any(ids[g]["block"] == ids[pid]["block"] for g in grp):
                continue  # two voices of the same chunk are different people: don't merge them
            grp.append(pid)
        for grp in groups.values():
            for p in grp[1:]:
                mapping[p] = grp[0]
    return mapping


def _solo_segments(segs, pid, n):
    mine = [s for s in segs if s.get("pid") == pid and s["end"] - s["start"] >= 1.5]
    others = [s for s in segs if s.get("pid") != pid]

    def alone(s):
        return all(o["end"] <= s["start"] or o["start"] >= s["end"] for o in others)

    ranked = sorted(mine, key=lambda s: (alone(s), s["end"] - s["start"]), reverse=True)
    picked = []
    for s in ranked:
        if all(abs(s["start"] - p["start"]) > 20 for p in picked):
            picked.append(s)
        if len(picked) >= n:
            break
    return picked


def voice_period(segs, pid):
    mine = [s for s in segs if s.get("pid") == pid]
    return (min(s["start"] for s in mine), max(s["end"] for s in mine)) if mine else (0.0, 0.0)
