"""«Speakers» window: for each voice separated by Nemotron you listen to a few clips and type (or choose)
the name. On confirm the «Speaker N» placeholders are replaced everywhere. No AI calls."""
import json
import tkinter as tk
import wave
from pathlib import Path
from tkinter import ttk

from .i18n import _
from . import speakers
from .outputs import fmt_ts
from .session import _load_raw, data_dir, load_meta

try:
    import winsound
except ImportError:  # outside Windows
    winsound = None

CLIP_SEC = 5.0


class ConfirmDialog:
    def __init__(self, root, folder, known_names, on_done):
        """known_names: names already used (and yours), offered in the menu. on_done(changed, used_names)."""
        self.folder = Path(folder)
        self.on_done = on_done
        self.data = json.loads((self.folder / "speakers.json").read_text(encoding="utf-8"))
        self.persons = self.data.get("speakers", {})
        self.segs, _x = _load_raw(self.folder)
        self.meta = load_meta(self.folder)
        stats = speakers.speaker_stats(self.segs)
        choices = sorted({n for n in known_names if n} | {p["name"] for p in self.persons.values() if p.get("name")},
                         key=str.lower)

        w = tk.Toplevel(root)
        self.win = w
        w.title(_("Speakers"))
        w.transient(root)
        w.grab_set()
        ttk.Label(w, text=_("Listen to the clips of each voice and type (or choose) who it is. If two voices are the same person, give them the same name. Leave it empty if you don't know."),
                  padding=(12, 10), wraplength=760).pack(anchor="w")

        # scrollable list (calls can have many voices)
        outer = ttk.Frame(w)
        outer.pack(fill="both", expand=True, padx=12)
        canvas = tk.Canvas(outer, highlightthickness=0, height=min(600, 150 * max(1, len(self.persons))))
        sb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        body = ttk.Frame(canvas)
        body.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=body, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        if len(self.persons) > 4:
            sb.pack(side="right", fill="y")
            canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(-1 if e.delta > 0 else 1, "units"))

        self.vars = {}
        for r, pid in enumerate(sorted(self.persons)):
            p = self.persons[pid]
            a, b = speakers.voice_period(self.segs, pid)
            merged = len(p.get("merged_with") or [])
            box = ttk.LabelFrame(body, text=_("  {0}  —  speaks for {1}, from {2} to {3}", speakers.placeholder(pid), fmt_ts(stats.get(pid, {}).get('seconds', 0)), fmt_ts(a), fmt_ts(b))
                                            + (_("  (recognised in {0} chunks)", merged + 1) if merged else "") + "  ",
                                 padding=8)
            box.grid(row=r, column=0, sticky="ew", pady=4)
            moments = speakers.solo_moments(self.segs, pid, 3, min_gap=10.0)
            btns = ttk.Frame(box)
            btns.grid(row=0, column=0, rowspan=2, padx=(0, 10), sticky="n")
            for k, (t, _x) in enumerate(moments, 1):
                ttk.Button(btns, text=f"▶ {k}  ({fmt_ts(t)})", width=14,
                           command=lambda pid=pid, t=t: self.play(pid, t)).pack(pady=1)
            lines = speakers.sample_lines(self.segs, pid)[:3]
            ttk.Label(box, text="\n".join(f"«{t[:100]}»" for _x, t in lines), foreground="#4a4060",
                      wraplength=430, justify="left").grid(row=0, column=1, sticky="nw")
            var = tk.StringVar(value=p.get("name") or "")
            ttk.Combobox(box, textvariable=var, values=choices, width=30).grid(row=0, column=2, padx=(10, 0), sticky="ne")
            self.vars[pid] = var
        bar = ttk.Frame(w, padding=12)
        bar.pack(fill="x")
        ttk.Button(bar, text=_("Apply names"), command=self.confirm).pack(side="right")
        ttk.Button(bar, text=_("Later"), command=self.skip).pack(side="right", padx=8)
        ttk.Button(bar, text=_("■ Stop audio"), command=self.stop_audio).pack(side="left")
        w.protocol("WM_DELETE_WINDOW", self.skip)

    def play(self, pid, t):
        """Plays ~5 seconds around moment t, from the track where that voice speaks."""
        if winsound is None:
            return
        seg = next((s for s in self.segs if s.get("pid") == pid), None)
        src = next((x for x in self.meta["sources"] if seg and x["label"] == seg["source"]), None)
        if not src:
            return
        start = max(0.0, t - CLIP_SEC / 2)
        out = data_dir(self.folder) / "_listen.wav"
        self.stop_audio()
        with wave.open(str(self.folder / src["file"]), "rb") as r:
            sr = r.getframerate()
            r.setpos(min(int(start * sr), r.getnframes()))
            frames = r.readframes(int(CLIP_SEC * sr))
            params = r.getparams()
        with wave.open(str(out), "wb") as wv:
            wv.setparams(params)
            wv.writeframes(frames)
        winsound.PlaySound(str(out), winsound.SND_FILENAME | winsound.SND_ASYNC)

    def stop_audio(self):
        if winsound is not None:
            winsound.PlaySound(None, 0)

    def confirm(self):
        used = []
        for pid, var in self.vars.items():
            name = " ".join(var.get().split())
            self.persons[pid]["name"] = name or None
            if name:
                used.append(name)
        self.data["confirmed_by_hand"] = True
        (self.folder / "speakers.json").write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")
        self._close()
        self.on_done(True, used)

    def skip(self):
        self._close()
        self.on_done(False, [])

    def _close(self):
        self.stop_audio()
        try:
            self.win.unbind_all("<MouseWheel>")
        except tk.TclError:
            pass
        try:
            (data_dir(self.folder) / "_listen.wav").unlink()
        except OSError:
            pass
        self.win.grab_release()
        self.win.destroy()
