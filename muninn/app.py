"""Muninn main window."""
import json
import os
import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

import requests

try:
    from PIL import ImageStat, ImageTk
except ImportError:  # no previews without Pillow
    ImageTk = None

from . import config, theme
from .i18n import _
from .theme import APP_NAME
from .tray import Tray, TaskbarHider, another_instance_running, listen_for_instances
from .audio import list_sources
from .outputs import fmt_ts
from .confirm import ConfirmDialog
from . import devtest, i18n
from .live import LiveCaptions
from .session import MEDIA_TYPES, Session, import_file, is_recording_folder, raw_path, render_outputs, transcribe_folder
from .video import VIDEO_CODECS, find_ffmpeg
from .winutil import exclude_from_capture, is_window, list_monitors, list_windows, set_dpi_aware

QUALITY_LABELS = {"high": _("High (larger files)"), "medium": _("Medium (recommended)"), "low": _("Low"),
                  "minimum": _("Minimum (tiny files)")}
RES_LABELS = {0: _("Original"), 1080: _("Up to 1080p"), 720: _("Up to 720p")}
CODEC_LABELS = {"opus": _("Opus (recommended)"), "mp3": _("MP3"), "flac": _("FLAC (lossless)"),
                "wav": _("WAV (no compression)")}
METHODS = {"area": _("Screen area (recommended for Teams)"), "direct": _("Direct window (even when covered)")}


class App:
    def __init__(self):
        set_dpi_aware()
        theme.set_app_id()
        self.cfg = config.load()
        self.root = tk.Tk()
        self.root.title(APP_NAME)
        k = max(1.0, self.root.winfo_fpixels("1i") / 96.0)  # scale for high-DPI screens
        self.k = k
        theme.set_window_icon(self.root)
        self.root.geometry(f"{int(1140 * k)}x{int(720 * k)}")
        self.root.minsize(int(820 * k), int(600 * k))
        self.ui_q = queue.Queue()
        self.session = None
        self.state = "idle"  # idle | starting | recording | stopping
        self.source_rows = []
        self.monitor_vars = []
        self.chosen_windows = []
        self.monitors = []
        self._build()
        self.refresh_sources()
        self.refresh_video()
        self._fit_width()
        # tray: X and "minimise" hide the window, "Quit" in the tray menu really closes it
        self.hider = TaskbarHider(self.root)
        self.tray = Tray(lambda a: self.ui_q.put(("tray", a)),
                         {"open": _("Open {0}", APP_NAME), "record": _("Start recording"),
                          "stop": _("Stop and save"), "live": _("Live captions"), "quit": _("Quit")},
                         lambda: self.state == "recording")
        self._instance_srv = listen_for_instances(lambda: self.ui_q.put(("tray", "show")),
                                                  lambda: self.ui_q.put(("tray", "quit")))
        self._tray_hint = False
        self.root.protocol("WM_DELETE_WINDOW", self.to_tray if self.tray.available else self.on_close)
        self.root.bind("<Unmap>", self._on_unmap)
        self.root.after(100, lambda: self.hider.hide(self.root))
        self.root.after(300, self._apply_hide_main)
        self.root.after(1000, self._preview_loop)
        self.root.after(150, self._poll)

    def _fit_width(self):
        """Widen the window as much as the button bar needs (French/German texts are longer)."""
        self.root.update_idletasks()
        bar = sum(w.winfo_reqwidth() for w in self.btn.master.winfo_children()) + int(70 * self.k)
        tabs = [self.tab_audio, self.tab_video, self.tab_set]
        need_w = max([bar] + [t.winfo_reqwidth() + int(40 * self.k) for t in tabs])
        extra_h = self.root.winfo_reqheight() - self.nb.winfo_reqheight()  # header, buttons, status
        need_h = max(t.winfo_reqheight() for t in tabs) + extra_h + int(60 * self.k)
        w = min(max(int(1140 * self.k), need_w), int(self.root.winfo_screenwidth() * 0.95))
        h = min(max(int(720 * self.k), need_h), int(self.root.winfo_screenheight() * 0.9))
        self.root.geometry(f"{w}x{h}")

    # ------------------------------------------------------------ tray
    def _on_unmap(self, e):
        if e.widget is self.root and self.tray.available and self.root.state() == "iconic":
            self.root.after(0, self.to_tray)  # with no taskbar button, a minimised window would end up in a corner

    def to_tray(self):
        self.root.withdraw()
        if not self._tray_hint:
            self._tray_hint = True
            self.tray.notify(_("{0} keeps running here in the tray. Click the icon to reopen it, \"Quit\" to close it.",
                               APP_NAME))

    def show_window(self):
        self.root.deiconify()
        self.root.state("normal")
        self.root.lift()
        self.root.focus_force()

    def _tray_action(self, action):
        if action == "show":
            self.show_window()
        elif action == "record":
            if self.state == "idle":
                self.show_window()
            self.toggle()
        elif action == "live":
            self.toggle_live()
        elif action == "quit":
            self.show_window()
            self.on_close()

    # ------------------------------------------------------------ interface
    def _build(self):
        k = self.k
        theme.apply(self.root, k)
        ic = lambda n: theme.icon(n, k)
        _x, self.timer = theme.header(self.root, k)

        top = ttk.Frame(self.root, padding=(12, 12, 12, 0))
        top.pack(fill="x")
        self.btn = ttk.Button(top, style="Rec.TButton", command=self.toggle, compound="left")
        self.btn.pack(side="left")
        self._rec_btn(_("Start recording"), "record")
        self.live_btn = ttk.Button(top, text=_("Live captions"), image=ic("live"), compound="left",
                                   style="Arcane.TButton", command=self.toggle_live)
        self.live_btn.pack(side="left", padx=(10, 0))
        self.live_en_btn = ttk.Button(top, text=_("Real time EN"), image=ic("live"), compound="left",
                                      style="Arcane.TButton", command=lambda: self.toggle_live(realtime_en=True))
        self.live_en_btn.pack(side="left", padx=(6, 0))
        self.live = None
        self.open_btn = ttk.Button(top, text=_("Open folder"), image=ic("chest"), compound="left",
                                   command=self.open_folder)
        self.open_btn.pack(side="right")
        self.save_btn = ttk.Button(top, text=_("Save settings"), image=ic("seal"), compound="left",
                                   command=self.save_settings)
        self.save_btn.pack(side="right", padx=(0, 6))
        self.redo_btn = ttk.Button(top, text=_("Transcribe again…"), image=ic("redo"), compound="left",
                                   command=self.retranscribe)
        self.redo_btn.pack(side="right", padx=6)
        self.file_btn = ttk.Button(top, text=_("Process files…"), image=ic("scroll"), compound="left",
                                   command=self.import_files)
        self.file_btn.pack(side="right")

        self.status = ttk.Label(self.root, text=_("Ready."), padding=(12, 8), style="Status.TLabel",
                                image=ic("feather"), compound="left")
        self.status.pack(fill="x")

        nb = ttk.Notebook(self.root)
        nb.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.tab_audio = ttk.Frame(nb, padding=14)
        self.tab_video = ttk.Frame(nb, padding=14)
        self.tab_text = ttk.Frame(nb, padding=14)
        self.tab_set = ttk.Frame(nb, padding=14)
        self.tab_guide = ttk.Frame(nb, padding=14)
        nb.add(self.tab_audio, text=_("  Audio  "))
        nb.add(self.tab_video, text=_("  Video  "))
        nb.add(self.tab_text, text=_("  Transcript  "))
        nb.add(self.tab_set, text=_("  Settings  "))
        nb.add(self.tab_guide, text=_("  Guide  "))
        self.nb = nb
        nb.bind("<<NotebookTabChanged>>", lambda e: self._tab_changed())
        self._build_audio()
        self._build_video()
        self._build_text()
        self._build_settings()
        self._build_guide()
        if not self._active_key():
            nb.select(self.tab_set)

    def _rec_btn(self, text, icon=None, state="normal"):
        img = theme.icon(icon, self.k) if icon else None
        self.btn.configure(text=text, image=img or "", state=state)

    def _build_audio(self):
        f = self.tab_audio
        head = ttk.Frame(f)
        head.pack(fill="x")
        ttk.Label(head, text=_("Choose what to record. Each source is transcribed separately: its label becomes the speaker's name (e.g. microphone = \"Me\", i.e. you)."),
                  wraplength=800).pack(side="left")
        ttk.Button(head, text=_("Refresh"), image=theme.icon("refresh", self.k), compound="left",
                   command=self.refresh_sources).pack(side="right")
        self.levels_btn = ttk.Button(head, text=_("Show levels"), command=self.toggle_levels)
        self.levels_btn.pack(side="right", padx=6)
        self.level_mon = None
        self._testing = None
        ttk.Label(f, text=_("Tip: use headphones, so the microphone does not also pick up the other people's voices."),
                  style="Muted.TLabel").pack(anchor="w", pady=(4, 0))
        ttk.Label(f, text=_("Not sure which device is the right one? \"Test\" on a microphone records 4 seconds and plays them back; on an output it plays a chime and checks that it arrives. Or press \"Show levels\", speak or play some audio: the bar of the right device moves."),
                  style="Muted.TLabel", wraplength=int(1000 * self.k)).pack(anchor="w", pady=(2, 8))
        self.src_frame = ttk.Frame(f)
        self.src_frame.pack(fill="both", expand=True)

    def refresh_sources(self):
        if self.state != "idle" or self._testing:
            return
        self._stop_levels()
        for w in self.src_frame.winfo_children():
            w.destroy()
        self.source_rows = []
        try:
            sources = list_sources()
        except Exception as e:
            ttk.Label(self.src_frame, text=_("Error listing audio devices: {0}", e)).grid(row=0, column=0)
            return
        hdr = [_("Use"), _("Type"), _("Device"), _("Label (who speaks)"), _("Several people (call)"), _("Level"), ""]
        for c, h in enumerate(hdr):
            ttk.Label(self.src_frame, text=h, style="Head.TLabel").grid(row=0, column=c, sticky="w", padx=4, pady=2)
        sel = set(self.cfg.get("selected_sources") or [])
        labels = self.cfg.get("source_labels", {})
        diar = self.cfg.get("source_diarize", {})
        for r, s in enumerate(sources, 1):
            default_on = s["default"] if not sel else s["key"] in sel
            default_label = "Me" if s["kind"] == "input" and s["default"] else (
                "Call" if s["kind"] == "output" and s["default"] else s["name"].split("(")[0].strip()[:20])
            v_on = tk.BooleanVar(value=default_on)
            v_label = tk.StringVar(value=labels.get(s["key"], default_label))
            v_diar = tk.BooleanVar(value=diar.get(s["key"], s["kind"] == "output"))
            ttk.Checkbutton(self.src_frame, variable=v_on).grid(row=r, column=0, padx=4)
            ttk.Label(self.src_frame, text=_("🎤 Input") if s["kind"] == "input" else _("🔊 Output")).grid(row=r, column=1, sticky="w", padx=4)
            name = s["name"] + (_("  (default)") if s["default"] else "")
            ttk.Label(self.src_frame, text=name[:60]).grid(row=r, column=2, sticky="w", padx=4)
            ttk.Entry(self.src_frame, textvariable=v_label, width=16).grid(row=r, column=3, padx=4, pady=1)
            ttk.Checkbutton(self.src_frame, variable=v_diar).grid(row=r, column=4, padx=4)
            bar = ttk.Progressbar(self.src_frame, length=90, maximum=100)
            bar.grid(row=r, column=5, padx=4)
            row = {"info": s, "on": v_on, "label": v_label, "diar": v_diar, "bar": bar}
            row["test"] = ttk.Button(self.src_frame, text=_("Test"), command=lambda row=row: self.test_source(row))
            row["test"].grid(row=r, column=6, padx=4, pady=1)
            self.source_rows.append(row)
        if not sources:
            ttk.Label(self.src_frame, text=_("No device found (the program only works on Windows).")).grid(row=1, column=0, columnspan=6)

    # ----- audio device tests
    def toggle_levels(self):
        if self.level_mon:
            self._stop_levels()
            return
        if self.state != "idle" or devtest.pyaudio is None:
            return
        self.level_mon = devtest.LevelMonitor([r["info"] for r in self.source_rows])
        self.levels_btn.configure(text=_("Hide levels"))
        self.set_status(_("Live levels: speak or play some audio and watch which bar moves."))

    def _stop_levels(self):
        if self.level_mon:
            self.level_mon.stop()
            self.level_mon = None
            for r in self.source_rows:
                r["bar"]["value"] = 0
        if hasattr(self, "levels_btn"):
            self.levels_btn.configure(text=_("Show levels"))

    def test_source(self, row):
        if self.state != "idle" or self._testing or devtest.pyaudio is None:
            return
        self._testing = row
        self._levels_were_on = self.level_mon is not None
        self._stop_levels()  # two captures on the same loopback cannot coexist: pause the levels
        for r in self.source_rows:
            r["test"].configure(state="disabled")
        info = row["info"]
        key = info["key"]
        name = info["name"].replace(" [Loopback]", "")
        on_level = lambda v: self.ui_q.put(("testlevel", (key, v)))  # noqa: E731
        on_done = lambda peak, err: self.ui_q.put(("testdone", (row, peak, err)))  # noqa: E731
        if info["kind"] == "input":
            self.set_status(_("Speak now: I'm recording 4 seconds from \"{0}\" and then I'll play them back to you…", name))
            target = lambda: devtest.test_input(info, on_level, on_done)  # noqa: E731
        else:
            self.set_status(_("Playing a chime on \"{0}\": if it is the output you use, you hear it and the bar moves…", name))
            target = lambda: devtest.test_output(info, on_level, on_done)  # noqa: E731
        threading.Thread(target=target, daemon=True).start()

    def _test_level(self, key, v):
        for r in self.source_rows:
            if r["info"]["key"] == key:
                r["bar"]["value"] = min(100, v * 400)

    def _test_done(self, row, peak, err):
        self._testing = None
        for r in self.source_rows:
            r["test"].configure(state="normal")
        name = row["info"]["name"].replace(" [Loopback]", "")
        if err is not None:
            msg = _("Test failed on \"{0}\": {1}", name, err)
        elif row["info"]["kind"] == "input":
            if peak > 0.02:
                msg = _("✓ \"{0}\" works: good signal. If you heard yourself, it is the right microphone.", name)
            elif peak > 0.004:
                msg = _("~ \"{0}\" gets a weak signal: move closer or raise the microphone volume in Windows.", name)
            else:
                msg = _("✗ No signal from \"{0}\": it is not the microphone you are using (or it is muted).", name)
        else:
            if peak > 0.003:
                msg = _("✓ The chime came out of \"{0}\". If you heard it in your call headphones/speakers, it is the right output.", name)
            else:
                msg = _("✗ No sound detected on \"{0}\": the device is not active or not connected.", name)
        self.set_status(msg)
        self._add_log(msg)
        if getattr(self, "_levels_were_on", False) and str(self.nb.select()) == str(self.tab_audio):
            self.toggle_levels()
            self.set_status(msg)

    def _build_video(self):
        k = self.k
        f0 = self.tab_video
        right = ttk.Frame(f0)
        right.pack(side="right", fill="y", padx=(int(16 * k), 0))
        f = ttk.Frame(f0)
        f.pack(side="left", fill="both", expand=True)
        wrap = int(560 * k)

        # --- right column: preview and compression
        ttk.Label(right, text=_("Preview"), style="Section.TLabel").pack(anchor="w")
        self.pv_w, self.pv_h = int(340 * k), int(191 * k)
        self.pv_box = tk.Frame(right, bg=theme.HEADER, width=self.pv_w, height=self.pv_h)
        self.pv_box.pack(anchor="w", pady=(4, 0))
        self.pv_box.pack_propagate(False)
        self.pv_label = tk.Label(self.pv_box, bg=theme.HEADER, fg="#b9aee0", font=("Segoe UI", 10, "italic"),
                                 text=_("Tick a screen or choose a window to see its preview."),
                                 wraplength=self.pv_w - 30, justify="center")
        self.pv_label.pack(fill="both", expand=True)
        self.pv_caption = ttk.Label(right, text="", style="Muted.TLabel", wraplength=self.pv_w)
        self.pv_caption.pack(anchor="w", pady=(4, 0))
        self.pv_warn = ttk.Label(right, text="", foreground="#b45309", wraplength=self.pv_w)
        self.pv_warn.pack(anchor="w")
        self.preview_target = None
        self._pv_busy = False
        self._pv_img = None

        ttk.Label(right, text=_("Video compression"), style="Section.TLabel").pack(anchor="w", pady=(14, 4))
        cg = ttk.Frame(right)
        cg.pack(anchor="w")
        self.v_vcodec = tk.StringVar(value=VIDEO_CODECS.get(self.cfg.get("video_codec", "h264"), VIDEO_CODECS["h264"])[0])
        self.v_vqual = tk.StringVar(value=QUALITY_LABELS.get(self.cfg.get("video_quality", "medium"), QUALITY_LABELS["medium"]))
        self.v_vres = tk.StringVar(value=RES_LABELS.get(int(self.cfg.get("video_max_height", 0) or 0), RES_LABELS[0]))
        self.v_recomp = tk.BooleanVar(value=bool(self.cfg.get("recompress_imported", False)))
        for r, (lab, var, vals) in enumerate([
                (_("Format"), self.v_vcodec, [v[0] for v in VIDEO_CODECS.values()]),
                (_("Quality"), self.v_vqual, list(QUALITY_LABELS.values())),
                (_("Maximum resolution"), self.v_vres, list(RES_LABELS.values()))]):
            ttk.Label(cg, text=lab).grid(row=r, column=0, sticky="w", padx=(0, 10), pady=2)
            ttk.Combobox(cg, textvariable=var, values=vals, state="readonly", width=34).grid(row=r, column=1, sticky="w", pady=2)
        ttk.Checkbutton(right, variable=self.v_recomp, text=_("also recompress the video of processed files (slower, smaller files)")
                        ).pack(anchor="w", pady=(4, 0))
        ttk.Label(right, text=_("Quick Sync = Intel video chip: almost no CPU, ideal during calls. H.265 = files 2-3 times smaller, but some programs/AIs read H.264 better. If a format is not available, H.264 is used."),
                  foreground=theme.MUTED, wraplength=self.pv_w).pack(anchor="w", pady=(6, 0))

        # --- left column: what to record
        ttk.Label(f, text=_("Optional: record screens and/or windows. This window (if the Privacy option is on) does not appear in videos or in screen sharing."), wraplength=wrap).pack(anchor="w")
        row = ttk.Frame(f)
        row.pack(fill="x", pady=(8, 4))
        ttk.Label(row, text=_("Screens"), style="Section.TLabel").pack(side="left")
        ttk.Button(row, text=_("Refresh"), image=theme.icon("refresh", k), compound="left",
                   command=self.refresh_video).pack(side="right")
        self.mon_frame = ttk.Frame(f)
        self.mon_frame.pack(fill="x")

        ttk.Label(f, text=_("Windows"), style="Section.TLabel").pack(anchor="w", pady=(14, 4))
        self.win_combo = ttk.Combobox(f, state="readonly")
        self.win_combo.pack(fill="x")
        wr = ttk.Frame(f)
        wr.pack(fill="x", pady=(4, 0))
        self.method_combo = ttk.Combobox(wr, width=38, state="readonly", values=list(METHODS.values()))
        self.method_combo.current(0)
        self.method_combo.pack(side="left")
        ttk.Button(wr, text=_("Add"), command=self.add_window).pack(side="left", padx=6)
        for cb in (self.win_combo, self.method_combo):
            cb.bind("<<ComboboxSelected>>", lambda e: self._preview_combo())
        self.win_list = tk.Listbox(f, height=3)
        self.win_list.pack(fill="x", pady=4)
        self.win_list.bind("<<ListboxSelect>>", lambda e: self._preview_list())
        ttk.Button(f, text=_("Remove selected"), command=self.remove_window).pack(anchor="w")
        ttk.Label(f, text=_("\"Screen area\" records the area where the window is when recording starts: if you move or cover it, it records whatever is on top. \"Direct window\" follows the window even when covered, but with Teams and browsers it sometimes gives black video: in that case use the area or record the whole screen."),
                  foreground=theme.MUTED, wraplength=wrap).pack(anchor="w", pady=(6, 0))

        fr = ttk.Frame(f)
        fr.pack(anchor="w", pady=(14, 0))
        ttk.Label(fr, text=_("Frames per second:")).pack(side="left")
        self.fps_var = tk.IntVar(value=int(self.cfg["video_fps"]))
        ttk.Spinbox(fr, from_=2, to=30, textvariable=self.fps_var, width=5).pack(side="left", padx=6)
        ttk.Label(f, text=_("(10 is enough for meetings and slides; higher = larger files and a busier PC)"),
                  foreground=theme.MUTED, wraplength=wrap).pack(anchor="w")
        if not find_ffmpeg():
            ttk.Label(f, text=_("⚠ ffmpeg not found: run install.bat to enable video."),
                      foreground="#b45309").pack(anchor="w", pady=8)

    # ----- video preview
    def _preview_combo(self):
        i = self.win_combo.current()
        if 0 <= i < len(self.windows):
            hwnd, title = self.windows[i]
            method = "area" if self.method_combo.current() == 0 else "direct"
            self._set_preview((method, hwnd), title)

    def _preview_list(self):
        sel = self.win_list.curselection()
        if sel and sel[0] < len(self.chosen_windows):
            w = self.chosen_windows[sel[0]]
            self._set_preview((w["method"], w["hwnd"]), w["title"])

    def _preview_monitor(self, m):
        self._set_preview(("monitor", m), _("Screen {0}  ({1}×{2}{3})", m['index'] + 1, m['w'], m['h'], ""))

    def _set_preview(self, target, caption):
        self.preview_target = target
        what = {"monitor": "", "area": METHODS["area"], "direct": METHODS["direct"]}[target[0]]
        self.pv_caption.configure(text=caption[:90] + (f"\n{what}" if what else ""))
        self.pv_warn.configure(text="")
        self._preview_refresh(force=True)

    def _preview_default(self):
        if self.preview_target is None and self.monitors:
            chosen = [m for m, v in self.monitor_vars if v.get()]
            prim = [m for m in self.monitors if m["x"] == 0 and m["y"] == 0]
            self._preview_monitor((chosen or prim or self.monitors)[0])

    def _preview_refresh(self, force=False):
        """Refresh the preview (in a thread: grabbing the screen takes a few tens of ms)."""
        if self._pv_busy or self.preview_target is None or ImageTk is None:
            return
        if not force and (str(self.nb.select()) != str(self.tab_video) or not self.root.winfo_viewable()):
            return
        self._pv_busy = True
        target = self.preview_target

        def run():
            img = devtest.grab_preview(target, self.pv_w, self.pv_h)
            self.ui_q.put(("preview", (target, img)))
        threading.Thread(target=run, daemon=True).start()

    def _preview_show(self, target, img):
        self._pv_busy = False
        if target != self.preview_target:
            return
        if img is None:
            self._pv_img = None
            self.pv_label.configure(image="", text=_("Window not available (closed or minimised)."))
            return
        if target[0] == "direct":
            dark = ImageStat.Stat(img.convert("L")).mean[0] < 4
            self.pv_warn.configure(text=_("⚠ With \"Direct window\" this window comes out black: use \"Screen area\" or record the whole screen.") if dark else "")
        self._pv_img = ImageTk.PhotoImage(img)
        self.pv_label.configure(image=self._pv_img, text="")

    def _tab_changed(self):
        sel = str(self.nb.select())
        if sel != str(self.tab_audio):
            self._stop_levels()
        if sel == str(self.tab_video):
            self._preview_default()
            self._preview_refresh()

    def _preview_loop(self):
        self._preview_refresh()
        self.root.after(1000, self._preview_loop)

    def refresh_video(self):
        if self.state != "idle":
            return
        for w in self.mon_frame.winfo_children():
            w.destroy()
        self.monitors = list_monitors()
        self.monitor_vars = []
        for m in self.monitors:
            v = tk.BooleanVar(value=False)
            prim = _(" — primary") if m["x"] == 0 and m["y"] == 0 else ""
            ttk.Checkbutton(self.mon_frame, variable=v, command=lambda m=m: self._preview_monitor(m),
                            text=_("Screen {0}  ({1}×{2}{3})", m['index'] + 1, m['w'], m['h'], prim)).pack(anchor="w")
            self.monitor_vars.append((m, v))
        own = {self.root.title()}
        self.windows = [(h, t) for h, t in list_windows() if t not in own]
        self.win_combo["values"] = [t[:90] for _x, t in self.windows]
        teams = [i for i, (_x, t) in enumerate(self.windows) if "teams" in t.lower()]
        if teams:
            self.win_combo.current(teams[0])
        elif self.windows:
            self.win_combo.current(0)

    def add_window(self):
        i = self.win_combo.current()
        if i < 0:
            return
        hwnd, title = self.windows[i]
        method = "area" if self.method_combo.current() == 0 else "direct"
        self.chosen_windows.append({"hwnd": hwnd, "title": title, "method": method})
        self.win_list.insert("end", f"{title[:80]}   [{METHODS[method]}]")
        self.win_list.selection_clear(0, "end")
        self.win_list.selection_set("end")
        self._preview_list()

    def remove_window(self):
        sel = self.win_list.curselection()
        if sel:
            self.win_list.delete(sel[0])
            del self.chosen_windows[sel[0]]

    def _build_text(self):
        bar = ttk.Frame(self.tab_text)
        bar.pack(fill="x", pady=(0, 6))
        ttk.Button(bar, text=_("Name the speakers…"), command=self.assign_names).pack(side="left")
        ttk.Button(bar, text=_("Apply edited speakers.json…"), command=lambda: self.run_on_folder(
            "apply", _("Applying names…"))).pack(side="left", padx=6)
        ttk.Label(bar, text=_("(they apply to the last recording, or you choose one)"),
                  foreground=theme.MUTED).pack(side="left")
        self.textbox = scrolledtext.ScrolledText(self.tab_text, wrap="word", font=("Segoe UI", 10), state="disabled",
                                                 padx=12, pady=8)
        self.textbox.pack(fill="both", expand=True)
        self.textbox.tag_configure("who", font=("Segoe UI", 10, "bold"), foreground=theme.ACCENT)
        self.textbox.tag_configure("log", foreground=theme.MUTED)

    def _build_settings(self):
        f = self.tab_set
        g = ttk.Frame(f)
        g.pack(fill="x")
        self.v_final_min = tk.IntVar(value=int(self.cfg["final_chunk_min"]))
        self.v_out = tk.StringVar(value=self.cfg["output_dir"])
        self.v_hide = tk.BooleanVar(value=self.cfg["hide_main_from_capture"])
        self.v_confirm = tk.BooleanVar(value=self.cfg.get("confirm_names", True))

        self.v_user = tk.StringVar(value=self.cfg["user_name"])
        self.v_di_key = tk.StringVar(value=self.cfg.get("deepinfra_key", ""))
        self.v_codec = tk.StringVar(value=CODEC_LABELS.get(self.cfg.get("audio_codec", "opus"), CODEC_LABELS["opus"]))
        self.v_bitrate = tk.StringVar(value=str(self.cfg.get("audio_bitrate_kbps", 32)))

        rows = [
            (_("Language") if _("Language") == "Language" else _("Language") + " / Language", lambda p: self._lang_row(p)),
            (_("DeepInfra key"), lambda p: self._key_row(p, self.v_di_key)),
            (_("Microphone chunks"), lambda p: self._spin_note(p, self.v_final_min, 2, 12,
                _("minutes, only for your own track (the call goes in 1-hour chunks). Lower it only if you see timeout errors"))),
            (_("Audio compression"), lambda p: self._codec_row(p)),
            (_("Recordings folder"), lambda p: self._out_row(p)),
            (_("Speaker names"), lambda p: self._check_note(p, self.v_confirm,
                _("when processing ends, ask me who each voice is (with clips to listen to)"))),
            (_("Your name"), lambda p: self._spin_entry(p, self.v_user, _("shown instead of \"Me\" and in the list of names"))),
            (_("Privacy"), lambda p: self._check_note(p, self.v_hide, _("also hide this window from screen sharing and recordings"))),
        ]
        for r, (lab, make) in enumerate(rows):
            ttk.Label(g, text=lab).grid(row=r, column=0, sticky="w", pady=5, padx=(0, 12))
            make(g).grid(row=r, column=1, sticky="w", pady=5)
        ttk.Label(f, text=_("Transcription starts when you press \"Stop and save\": the audio is split into chunks (cut at pauses) and sent to DeepInfra. The result is in transcript.md / .json."),
                  foreground=theme.MUTED, wraplength=850).pack(anchor="w", pady=(12, 0))

    def _lang_row(self, p):
        fr = ttk.Frame(p)
        names = list(i18n.LANGS.values())
        self.v_ui_lang = tk.StringVar(value=i18n.LANGS.get(self.cfg.get("ui_language", "en"), "English"))
        cb = ttk.Combobox(fr, textvariable=self.v_ui_lang, values=names, state="readonly", width=16)
        cb.pack(side="left")
        cb.bind("<<ComboboxSelected>>", lambda e: self._lang_changed())
        ttk.Label(fr, text=_("interface language (restart needed)"), style="Muted.TLabel").pack(side="left", padx=8)
        return fr

    def _ui_lang(self):
        return next((k for k, v in i18n.LANGS.items() if v == self.v_ui_lang.get()), "en")

    def _lang_changed(self):
        code = self._ui_lang()
        if code == self.cfg.get("ui_language", "en"):
            return
        self.save_settings(quiet=True)
        if self.state != "idle" or self.live:
            messagebox.showinfo(APP_NAME, _("The new language will be used the next time you start the app."))
            return
        if messagebox.askyesno(APP_NAME, _("Restart {0} now to change the language?", APP_NAME)):
            self.restart()

    def restart(self):
        """Close and reopen the app (for the language): the new process starts once this one has freed the port."""
        import subprocess
        import sys
        self.save_settings(quiet=True)
        main_py = Path(__file__).resolve().parent.parent / "main.py"
        cmd = [sys.executable] if config.FROZEN else [sys.executable, str(main_py)]
        self.tray.stop()
        if self._instance_srv:
            self._instance_srv.close()
            self._instance_srv = None
        subprocess.Popen(cmd, cwd=str(config.APP_DIR),
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.root.destroy()

    def _codec_row(self, p):
        fr = ttk.Frame(p)
        cb = ttk.Combobox(fr, textvariable=self.v_codec, values=list(CODEC_LABELS.values()), state="readonly", width=26)
        cb.pack(side="left")
        ttk.Label(fr, text="bitrate").pack(side="left", padx=(10, 4))
        self.bitrate_cb = ttk.Combobox(fr, textvariable=self.v_bitrate, values=["16", "24", "32", "48", "64", "96", "128"],
                                       width=5)
        self.bitrate_cb.pack(side="left")
        ttk.Label(fr, text="kbps").pack(side="left", padx=(4, 10))
        self.codec_note = ttk.Label(fr, foreground=theme.MUTED)
        self.codec_note.pack(side="left")
        cb.bind("<<ComboboxSelected>>", lambda e: self._codec_changed())
        self.bitrate_cb.bind("<<ComboboxSelected>>", lambda e: self._codec_changed())
        self.bitrate_cb.bind("<KeyRelease>", lambda e: self._codec_changed())
        self.root.after(50, self._codec_changed)
        return fr

    def _codec(self):
        return next((k for k, v in CODEC_LABELS.items() if v == self.v_codec.get()), "opus")

    def _codec_changed(self):
        c = self._codec()
        lossy = c in ("opus", "mp3")
        self.bitrate_cb.configure(state="normal" if lossy else "disabled")
        try:
            kb = int(self.v_bitrate.get())
        except ValueError:
            kb = 32
        mb_h = {"flac": 60, "wav": 115}.get(c, kb * 3600 / 8 / 1000)  # 16 kHz mono
        hint = {"opus": _("recommended: 24-32 kbps is enough for voice"),
                "mp3": _("plays everywhere; 48-64 kbps for voice"),
                "flac": _("lossless, larger files"),
                "wav": _("no compression: slower upload")}[c]
        self.codec_note.configure(text=_("≈ {0:.0f} MB per hour of audio — {1}", mb_h, hint))

    def _key_row(self, p, var, note=""):
        fr = ttk.Frame(p)
        ttk.Entry(fr, textvariable=var, width=56, show="•").pack(side="left")
        ttk.Button(fr, text=_("Check"), command=lambda: self.check_key(var.get())).pack(side="left", padx=6)
        if note:
            ttk.Label(fr, text=note, foreground=theme.MUTED).pack(side="left", padx=4)
        return fr

    def _active_key(self):
        return self.cfg.get("deepinfra_key", "")

    def _spin_note(self, p, var, lo, hi, note):
        fr = ttk.Frame(p)
        ttk.Spinbox(fr, from_=lo, to=hi, textvariable=var, width=5).pack(side="left")
        ttk.Label(fr, text=note, foreground=theme.MUTED).pack(side="left", padx=8)
        return fr

    def _check_note(self, p, var, note):
        return ttk.Checkbutton(p, variable=var, text=note)

    def _spin_entry(self, p, var, note):
        fr = ttk.Frame(p)
        ttk.Entry(fr, textvariable=var, width=30).pack(side="left")
        ttk.Label(fr, text=note, foreground=theme.MUTED).pack(side="left", padx=8)
        return fr

    def _out_row(self, p):
        fr = ttk.Frame(p)
        ttk.Entry(fr, textvariable=self.v_out, width=56).pack(side="left")
        ttk.Button(fr, text=_("Browse…"), command=lambda: self.v_out.set(
            filedialog.askdirectory(initialdir=self.v_out.get()) or self.v_out.get())).pack(side="left", padx=6)
        return fr

    def save_settings(self, quiet=False):
        def num(var, default):
            try:
                return int(var.get())
            except (tk.TclError, ValueError):
                return default
        self.cfg.update({
            "final_chunk_min": max(2, min(12, num(self.v_final_min, 8))), "output_dir": self.v_out.get().strip(),
            "hide_main_from_capture": self.v_hide.get(),
            "confirm_names": self.v_confirm.get(),
            "video_codec": next((k for k, v in VIDEO_CODECS.items() if v[0] == self.v_vcodec.get()), "h264"),
            "video_quality": next((k for k, v in QUALITY_LABELS.items() if v == self.v_vqual.get()), "medium"),
            "video_max_height": next((k for k, v in RES_LABELS.items() if v == self.v_vres.get()), 0),
            "recompress_imported": self.v_recomp.get(),
            "audio_codec": self._codec(), "audio_bitrate_kbps": max(8, min(256, num(self.v_bitrate, 32))),
            "ui_language": self._ui_lang() if hasattr(self, "v_ui_lang") else self.cfg.get("ui_language", "en"),
            "user_name": self.v_user.get().strip(), "deepinfra_key": self.v_di_key.get().strip(), "video_fps": max(2, min(30, num(self.fps_var, 10))),
        })
        self.cfg["source_labels"] = {r["info"]["key"]: r["label"].get().strip() for r in self.source_rows}
        self.cfg["source_diarize"] = {r["info"]["key"]: r["diar"].get() for r in self.source_rows}
        self.cfg["selected_sources"] = [r["info"]["key"] for r in self.source_rows if r["on"].get()]
        config.save(self.cfg)
        self._apply_hide_main()
        if not quiet:
            self.set_status(_("Settings saved."))

    def check_key(self, key):
        key = key.strip()

        def run():
            try:
                r = requests.get("https://api.deepinfra.com/v1/me", headers={"Authorization": f"Bearer {key}"}, timeout=15)
                if r.status_code == 200:
                    msg = _("DeepInfra key is valid.")
                elif r.status_code in (401, 403):
                    msg = _("Invalid DeepInfra key ({0}).", r.status_code)
                else:
                    msg = _("I cannot check the DeepInfra key (response {0}): try it with a recording.", r.status_code)
            except Exception as e:
                msg = _("Check failed: {0}", e)
            self.ui_q.put(("status", msg))
        threading.Thread(target=run, daemon=True).start()

    def _apply_hide_main(self):
        try:
            exclude_from_capture(self.root, bool(self.cfg.get("hide_main_from_capture")))
        except Exception:
            pass

    # ------------------------------------------------------------ recording
    def toggle(self):
        if self.state == "idle":
            self.start()
        elif self.state == "recording":
            self.stop()

    def _build_guide(self):
        t = scrolledtext.ScrolledText(self.tab_guide, wrap="word", font=("Segoe UI", 10), padx=18, pady=10)
        t.pack(fill="both", expand=True)
        t.tag_configure("h", font=(theme.SERIF, 14, "bold"), foreground=theme.ACCENT, spacing1=14, spacing3=4)
        t.tag_configure("li", lmargin1=14, lmargin2=30, spacing1=3)
        t.tag_configure("dot", foreground=theme.GOLD)
        t.tag_configure("p", spacing1=2)
        for line in i18n.guide().strip().splitlines():
            if line.startswith("# "):
                t.insert("end", line[2:] + "\n", ("h",))
            elif line.startswith("- "):
                t.insert("end", "✦  ", ("li", "dot"))
                t.insert("end", line[2:] + "\n", ("li",))
            elif line.strip():
                t.insert("end", line + "\n", ("p",))
        t.configure(state="disabled")

    # ------------------------------------------------------------ live captions
    def toggle_live(self, realtime_en=False):
        """Live captions: Italian (and other languages) via requests, or "Real time EN": true streaming, English only."""
        if self.live:
            self.live.stop()
            return
        self.save_settings(quiet=True)
        if not self._active_key():
            messagebox.showwarning(APP_NAME, _("Enter the DeepInfra key in Settings."))
            self.nb.select(self.tab_set)
            return
        sources = [{"info": dict(r["info"]), "label": r["label"].get().strip() or r["info"]["name"][:20]}
                   for r in self.source_rows if r["on"].get()]
        if not sources:
            messagebox.showwarning(APP_NAME, _("Tick at least one source in the Audio tab."))
            self.nb.select(self.tab_audio)
            return
        user = self.cfg.get("user_name", "")
        for s in sources:  # in captions "Me" becomes your name
            if user and s["label"] == "Me":
                s["label"] = user.split()[0]

        mine, other = (self.live_en_btn, self.live_btn) if realtime_en else (self.live_btn, self.live_en_btn)
        idle_text = _("Real time EN") if realtime_en else _("Live captions")

        def stopped():
            self.live = None
            mine.configure(text=idle_text, image=theme.icon("live", self.k))
            other.configure(state="normal")
        try:
            self.live = LiveCaptions(self.root, self.cfg, sources, lambda m: self.ui_q.put(("log", m)), stopped,
                                     realtime_en=realtime_en)
            self.hider.hide(self.live.win.nt)
            mine.configure(text=_("Stop captions"), image=theme.icon("stop", self.k))
            other.configure(state="disabled")
            kind = _("real time in English (streaming)") if realtime_en else "live"
            self._add_log(_("Captions ({0}) started: close the Notary window or press \"Stop captions\" to stop them.", kind))
        except Exception as e:
            self.live = None
            messagebox.showerror(APP_NAME, _("Live captions not started: {0}", e))

    def start(self):
        self._stop_levels()
        self.save_settings(quiet=True)
        if not self._active_key():
            messagebox.showwarning(APP_NAME, _("Enter the DeepInfra key in Settings."))
            self.nb.select(self.tab_set)
            return
        sources = [{"info": dict(r["info"]), "label": r["label"].get().strip() or r["info"]["name"][:20],
                    "diarize": r["diar"].get()} for r in self.source_rows if r["on"].get()]
        monitors = [m for m, v in self.monitor_vars if v.get()]
        windows = [w for w in self.chosen_windows if is_window(w["hwnd"])]
        if not sources and not monitors and not windows:
            messagebox.showwarning(APP_NAME, _("Select at least one audio or video source."))
            return
        if not sources:
            if not messagebox.askyesno(APP_NAME, _("No audio source: I will record video only, without a transcript. Continue?")):
                return
        self.state = "starting"
        self._rec_btn(_("Starting…"), state="disabled")
        self._clear_text()
        self.session = Session(self.cfg, sources, monitors, windows, self.monitors,
                               log=lambda m: self.ui_q.put(("log", m)),
                               on_status=lambda m: self.ui_q.put(("status", m)))

        def run():
            try:
                self.session.start()
                self.ui_q.put(("started", None))
            except Exception as e:
                self.ui_q.put(("failed", str(e)))
        threading.Thread(target=run, daemon=True).start()

    def stop(self):
        self.state = "stopping"
        self._rec_btn(_("Closing…"), state="disabled")

        def run():
            try:
                folder = self.session.stop(progress=lambda m: self.ui_q.put(("status", m)))
                self.ui_q.put(("done", str(folder)))
            except Exception as e:
                self.ui_q.put(("failed", f"Error while closing: {e}"))
        threading.Thread(target=run, daemon=True).start()

    # ------------------------------------------------------------ UI updates
    def _poll(self):
        try:
            while True:
                kind, val = self.ui_q.get_nowait()
                if kind == "tray":
                    self._tray_action(val)
                elif kind == "preview":
                    self._preview_show(*val)
                elif kind == "testlevel":
                    self._test_level(*val)
                elif kind == "testdone":
                    self._test_done(*val)
                elif kind == "log":
                    self._add_log(val)
                elif kind == "status":
                    self.set_status(val)
                elif kind == "started":
                    self.state = "recording"
                    self._rec_btn(_("Stop and save"), "stop")
                    self.timer.configure(fg="#ff6b81")
                    self.redo_btn.configure(state="disabled")
                    self.file_btn.configure(state="disabled")
                    self._add_log(_("Recording started. The transcript will be made when you press \"Stop and save\"."))
                    self.nb.select(self.tab_text)
                    self.tray.refresh()
                    if getattr(self, "_closing", False):
                        self.stop()
                elif kind in ("done", "failed"):
                    self.state = "idle"
                    self._rec_btn(_("Start recording"), "record")
                    self.timer.configure(fg="#8d82b3")
                    self.redo_btn.configure(state="normal")
                    self.file_btn.configure(state="normal")
                    self.tray.refresh()
                    if kind == "done":
                        self.show_window()  # the names window and the final question must be visible
                        self.last_folder = val
                        self._show_transcript(val)
                        if self._needs_confirm(val):
                            self._open_confirm(val)
                        elif messagebox.askyesno(APP_NAME, _("Done. Open the recording folder?")):
                            os.startfile(val)
                    else:
                        self.set_status(val)
                        self._add_log(val)
                    if getattr(self, "_closing", False):
                        self.root.destroy()
                        return
        except queue.Empty:
            pass
        if self.level_mon and self.state == "idle" and not self._testing:
            lv = self.level_mon.levels()
            for r in self.source_rows:
                r["bar"]["value"] = min(100, max(0.0, lv.get(r["info"]["key"], 0)) * 400)
        if self.session and self.state in ("recording", "stopping"):
            el = self.session.elapsed()
            self.timer.configure(text=fmt_ts(el))
            levels = dict(self.session.levels())
            for r in self.source_rows:
                lab = r["label"].get().strip()
                r["bar"]["value"] = min(100, levels.get(lab, 0) * 400) if lab in levels else 0
        self.root.after(150, self._poll)

    def set_status(self, msg):
        self.status.configure(text=msg)

    def _clear_text(self):
        self.textbox.configure(state="normal")
        self.textbox.delete("1.0", "end")
        self.textbox.configure(state="disabled")

    def _needs_confirm(self, folder):
        """Ask to confirm the names only if the AI has just proposed them and they have not been confirmed yet."""
        if not self.cfg.get("confirm_names", True) or getattr(self, "_skip_confirm", False):
            self._skip_confirm = False
            return False
        p = Path(folder) / "speakers.json"
        if not p.exists():
            return False
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            return False
        return bool(d.get("speakers")) and not d.get("confirmed_by_hand")

    def _open_confirm(self, folder):
        def done(changed, used):
            if used:  # remember the names for next time
                self.cfg["known_names"] = sorted(set(self.cfg.get("known_names", [])) | set(used), key=str.lower)[:300]
                config.save(self.cfg)
            if changed:
                self._skip_confirm = True
                self.state = "stopping"
                for b in (self.btn, self.redo_btn, self.file_btn):
                    b.configure(state="disabled")
                self._clear_text()
                log = lambda m: self.ui_q.put(("log", m))

                def run():
                    try:
                        render_outputs(folder, self.cfg, log, lambda m: self.ui_q.put(("status", m)))
                        self.ui_q.put(("status", "Names applied to transcript and subtitles."))
                        self.ui_q.put(("done", folder))
                    except Exception as e:
                        self.ui_q.put(("failed", f"Applying names failed: {e}"))
                threading.Thread(target=run, daemon=True).start()
        known = list(self.cfg.get("known_names", [])) + [self.cfg.get("user_name", "")]
        ConfirmDialog(self.root, folder, known, done)

    def assign_names(self):
        if self.state != "idle":
            return
        folder = getattr(self, "last_folder", None)
        if not folder or not (Path(folder) / "speakers.json").exists():
            folder = filedialog.askdirectory(initialdir=self.cfg["output_dir"], title=_("Choose the recording"))
        if not folder:
            return
        if not (Path(folder) / "speakers.json").exists():
            messagebox.showinfo(APP_NAME, _("This recording has no separated voices to name (it needs \"Several people (call)\" or a processed file)."))
            return
        self._open_confirm(folder)

    def _show_transcript(self, folder):
        md = Path(folder) / "transcript.md"
        if md.exists():
            self.textbox.configure(state="normal")
            self.textbox.insert("end", "\n" + "─" * 60 + "\n\n" + md.read_text(encoding="utf-8"))
            self.textbox.see("1.0")
            self.textbox.configure(state="disabled")
            self.nb.select(self.tab_text)

    def import_files(self):
        if self.state != "idle":
            return
        self.save_settings(quiet=True)
        if not self._active_key():
            messagebox.showwarning(APP_NAME, _("Enter the DeepInfra key in Settings."))
            self.nb.select(self.tab_set)
            return
        files = filedialog.askopenfilenames(title=_("Choose recordings to process"), filetypes=MEDIA_TYPES)
        if not files:
            return
        self.state = "stopping"
        for b in (self.btn, self.redo_btn, self.file_btn):
            b.configure(state="disabled")
        self._clear_text()
        self.nb.select(self.tab_text)
        log = lambda m: self.ui_q.put(("log", m))

        def run():
            last, errors = None, 0
            for k, f in enumerate(files, 1):
                prog = lambda m, k=k: self.ui_q.put(("status", f"[{k}/{len(files)}] {m}"))
                log(f"── {Path(f).name}")
                try:
                    last = import_file(f, self.cfg, log, prog)
                except Exception as e:
                    errors += 1
                    log(_("{0}: not processed — {1}", Path(f).name, e))
            if last:
                self.ui_q.put(("status", f"Processed {len(files) - errors} of {len(files)} files."))
                self.ui_q.put(("done", str(last)))
            else:
                self.ui_q.put(("failed", "No file processed."))
        threading.Thread(target=run, daemon=True).start()

    def retranscribe(self):
        self.run_on_folder("transcribe", _("Transcribing…"), ask=True)

    def run_on_folder(self, action, status, ask=False):
        if self.state != "idle":
            return
        self.save_settings(quiet=True)
        folder = None if ask else getattr(self, "last_folder", None)
        if not folder:
            folder = filedialog.askdirectory(initialdir=self.cfg["output_dir"], title=_("Choose the recording folder"))
        if not folder:
            return
        if action != "transcribe" and not raw_path(folder).exists():
            messagebox.showwarning(APP_NAME, _("This folder has no transcript_raw.json: transcribe it first."))
            return
        if not is_recording_folder(folder):
            messagebox.showwarning(APP_NAME, _("This folder does not contain a recording (session.json is missing)."))
            return
        self.state = "stopping"
        self.btn.configure(state="disabled")
        self.redo_btn.configure(state="disabled")
        self.file_btn.configure(state="disabled")
        self._clear_text()
        self.nb.select(self.tab_text)

        self.set_status(status)
        log = lambda m: self.ui_q.put(("log", m))
        prog = lambda m: self.ui_q.put(("status", m))

        def run():
            try:
                if action == "transcribe":
                    transcribe_folder(folder, self.cfg, log, prog)
                else:
                    render_outputs(folder, self.cfg, log, prog)
                prog(_("Done."))
                self.ui_q.put(("done", folder))
            except Exception as e:
                self.ui_q.put(("failed", f"Operation failed: {e}"))
        threading.Thread(target=run, daemon=True).start()

    def _add_log(self, msg):
        self.textbox.configure(state="normal")
        self.textbox.insert("end", f"[{time.strftime('%H:%M:%S')}] {msg}\n", ("log",))
        self.textbox.see("end")
        self.textbox.configure(state="disabled")

    def open_folder(self):
        p = Path(getattr(self, "last_folder", "") or self.v_out.get())
        p.mkdir(parents=True, exist_ok=True)
        os.startfile(str(p))

    def on_close(self):
        self._stop_levels()
        if self.live:
            self.live.stop()
        if self.state == "recording":
            if messagebox.askyesno(APP_NAME, _("Recording in progress. Stop, save and quit?")):
                self._closing = True
                self.stop()
            return
        if self.state in ("starting", "stopping"):
            self._closing = True
            return
        self.save_settings(quiet=True)
        self.root.destroy()

    def run(self):
        try:
            self.root.mainloop()
        finally:
            self.tray.stop()
            if self._instance_srv:
                self._instance_srv.close()


def selftest(out):
    """Muninn.exe --selftest file.json: check that the exe contains everything (used after the build)."""
    import sys
    from . import live, tray
    from .audio import list_sources
    from .video import find_ffmpeg
    res = {"frozen": config.FROZEN, "python": sys.version.split()[0], "data_dir": str(config.DATA_DIR),
           "output_dir": config.DEFAULTS["output_dir"], "ffmpeg": bool(find_ffmpeg()),
           "icon": (theme.ASSETS / "muninn.ico").exists(), "png": len(list((theme.ASSETS / "png").glob("*.png"))),
           "langs": sorted(p.stem for p in i18n.LANG_DIR.glob("*.json")), "guides": sorted(p.stem for p in i18n.LANG_DIR.glob("guide_*.txt")),
           "audio_devices": len(list_sources()), "pystray": tray.pystray is not None,
           "websocket": live.websocket is not None, "pil": devtest.ImageGrab is not None and ImageTk is not None}
    Path(out).write_text(json.dumps(res, indent=1), encoding="utf-8")


def main():
    import sys
    if "--selftest" in sys.argv:
        selftest(sys.argv[sys.argv.index("--selftest") + 1])
        return
    if another_instance_running():  # already open (maybe in the tray): it was asked to show itself
        return
    App().run()
