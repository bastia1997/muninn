"""Muninn's look: colors, ttk styles and icons (PNGs generated from assets/svg)."""
import ctypes
import sys
import tkinter as tk
from pathlib import Path
from tkinter import ttk

APP_NAME = "Muninn"
APP_ID = "Muninn.App"

ASSETS = Path(__file__).resolve().parent.parent / "assets"

# palette: parchment, ink, arcane purple, gold
BG = "#f5eedc"
SURFACE = "#fffaf0"
TAB = "#e8dcbc"
LINE = "#d6c49a"
INK = "#2a2140"
MUTED = "#6f6488"
ACCENT = "#5b3aa8"
ACCENT_DARK = "#2e1d63"
HEADER = "#1d1442"
GOLD = "#d9a441"
GOLD_LIGHT = "#ffe8a3"
CRIMSON = "#a3203c"
CRIMSON_DARK = "#7a0f25"
WAVE = "#3fb6d9"

SERIF = "Palatino Linotype"
SANS = "Segoe UI"

_cache = {}


def set_app_id():
    """On Windows, shows Muninn's icon in the taskbar instead of Python's."""
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        except Exception:
            pass


def _nearest(sizes, want):
    return min(sizes, key=lambda s: (abs(s - want), -s))


def icon(name, k=1.0, base=20):
    """Button icon (20 px at 100%, 30/40 px on high resolution screens)."""
    size = _nearest([20, 30, 40], base * k)
    key = (name, size)
    if key not in _cache:
        p = ASSETS / "png" / f"{name}_{size}.png"
        _cache[key] = tk.PhotoImage(file=str(p)) if p.exists() else None
    return _cache[key]


def logo(size):
    size = _nearest([32, 48, 64, 96, 128, 256], size)
    key = ("muninn", size)
    if key not in _cache:
        p = ASSETS / "png" / f"muninn_{size}.png"
        _cache[key] = tk.PhotoImage(file=str(p)) if p.exists() else None
    return _cache[key]


def set_window_icon(root):
    ico = ASSETS / "muninn.ico"
    try:
        if ico.exists():
            root.iconbitmap(default=str(ico))
        imgs = [i for i in (logo(256), logo(48), logo(32)) if i]
        if imgs:
            root.iconphoto(True, *imgs)
    except tk.TclError:
        pass


def apply(root, k=1.0):
    """Applies the theme to the whole app (also to windows opened later)."""
    root.configure(bg=BG)
    for pat, val in [("*Toplevel.background", BG), ("*Canvas.background", BG),
                     ("*Text.background", SURFACE), ("*Text.foreground", INK),
                     ("*Text.selectBackground", ACCENT), ("*Text.selectForeground", "white"),
                     ("*Text.insertBackground", INK), ("*Text.relief", "flat"),
                     ("*Text.highlightThickness", 1), ("*Text.highlightBackground", LINE),
                     ("*Text.highlightColor", ACCENT),
                     ("*Listbox.background", SURFACE), ("*Listbox.foreground", INK),
                     ("*Listbox.selectBackground", ACCENT), ("*Listbox.selectForeground", "white"),
                     ("*Listbox.relief", "flat"), ("*Listbox.highlightThickness", 1),
                     ("*Listbox.highlightBackground", LINE), ("*Listbox.highlightColor", ACCENT),
                     ("*TCombobox*Listbox.background", SURFACE), ("*TCombobox*Listbox.foreground", INK),
                     ("*TCombobox*Listbox.selectBackground", ACCENT),
                     ("*TCombobox*Listbox.selectForeground", "white"),
                     ("*TCombobox*Listbox.font", (SANS, 10))]:
        root.option_add(pat, val)

    s = ttk.Style(root)
    try:
        s.theme_use("clam")
    except tk.TclError:
        return s
    pad = max(1, round(k))
    s.configure(".", background=BG, foreground=INK, font=(SANS, 10), bordercolor=LINE, lightcolor=BG,
                darkcolor=LINE, troughcolor=TAB, focuscolor=ACCENT, selectbackground=ACCENT,
                selectforeground="white", fieldbackground=SURFACE, insertcolor=INK, arrowcolor=ACCENT)
    s.configure("TFrame", background=BG)
    s.configure("TLabel", background=BG, foreground=INK)
    s.configure("Muted.TLabel", foreground=MUTED)
    s.configure("Section.TLabel", font=(SERIF, 12, "bold"), foreground=ACCENT)
    s.configure("Head.TLabel", font=(SANS, 9, "bold"), foreground=MUTED)
    s.configure("Status.TLabel", background=BG, foreground=MUTED, font=(SANS, 10, "italic"))

    # buttons
    s.configure("TButton", background="#efe4c8", foreground=INK, bordercolor=LINE, lightcolor="#f8f0da",
                darkcolor="#e2d3ab", padding=(10 * pad, 5 * pad), relief="flat", focusthickness=0)
    s.map("TButton",
          background=[("disabled", "#ece5d3"), ("pressed", "#dccb9c"), ("active", "#f7ecd0")],
          foreground=[("disabled", "#a79f8f")],
          bordercolor=[("active", GOLD), ("focus", GOLD)])
    s.configure("Rec.TButton", background=CRIMSON, foreground="white", font=(SANS, 12, "bold"),
                bordercolor=CRIMSON_DARK, lightcolor="#c4304e", darkcolor=CRIMSON_DARK,
                padding=(14 * pad, 8 * pad))
    s.map("Rec.TButton",
          background=[("disabled", "#b98a94"), ("pressed", CRIMSON_DARK), ("active", "#bd2a49")],
          foreground=[("disabled", "#f3e6e9")], bordercolor=[("active", GOLD)])
    s.configure("Arcane.TButton", background=ACCENT, foreground="white", font=(SANS, 10, "bold"),
                bordercolor=ACCENT_DARK, lightcolor="#7552c4", darkcolor=ACCENT_DARK,
                padding=(12 * pad, 8 * pad))
    s.map("Arcane.TButton",
          background=[("disabled", "#a99ccb"), ("pressed", ACCENT_DARK), ("active", "#6a48bd")],
          bordercolor=[("active", GOLD)])

    # tabs
    s.configure("TNotebook", background=BG, bordercolor=LINE, tabmargins=(2, 6, 2, 0))
    s.configure("TNotebook.Tab", background=TAB, foreground=MUTED, bordercolor=LINE, lightcolor=TAB,
                padding=(16 * pad, 6 * pad), font=(SANS, 10))
    s.map("TNotebook.Tab",
          background=[("selected", SURFACE), ("active", "#f1e7cc")],
          foreground=[("selected", ACCENT)], lightcolor=[("selected", SURFACE)],
          font=[("selected", (SANS, 10, "bold"))], expand=[("selected", (1, 2, 1, 0))])
    s.configure("Card.TFrame", background=SURFACE)
    s.configure("Card.TLabel", background=SURFACE)
    s.configure("Card.TCheckbutton", background=SURFACE)

    # fields
    for w in ("TEntry", "TCombobox", "TSpinbox"):
        s.configure(w, fieldbackground=SURFACE, bordercolor=LINE, lightcolor=SURFACE, darkcolor=LINE,
                    padding=3 * pad, arrowsize=12 * pad)
        s.map(w, bordercolor=[("focus", ACCENT)], lightcolor=[("focus", SURFACE)],
              fieldbackground=[("readonly", SURFACE), ("disabled", "#ece5d3")])
    s.map("TCombobox", background=[("active", "#f1e7cc"), ("!active", "#efe4c8")])

    for w in ("TCheckbutton", "Card.TCheckbutton", "TRadiobutton"):
        s.configure(w, indicatorbackground=SURFACE, indicatorforeground="white", indicatormargin=(0, 0, 6, 0),
                    upperbordercolor=LINE, lowerbordercolor=LINE, focuscolor=BG)
        s.map(w, indicatorbackground=[("selected", ACCENT), ("pressed", "#e2d3ab")],
              background=[("active", s.lookup(w, "background") or BG)])
    s.configure("TLabelframe", background=SURFACE, bordercolor=LINE, lightcolor=SURFACE, darkcolor=LINE)
    s.configure("TLabelframe.Label", background=SURFACE, foreground=ACCENT, font=(SANS, 10, "bold"))

    s.configure("Horizontal.TProgressbar", background=WAVE, troughcolor=TAB, bordercolor=LINE,
                lightcolor="#8fe0f5", darkcolor="#2a91b0", thickness=10 * pad)
    s.configure("Vertical.TScrollbar", background="#e6dabb", troughcolor=BG, bordercolor=BG,
                arrowcolor=ACCENT, lightcolor="#efe4c8", darkcolor=LINE)
    s.map("Vertical.TScrollbar", background=[("active", "#dccb9c")])
    return s


def header(parent, k=1.0):
    """Dark header with crest, name and motto. Returns (frame, timer_label)."""
    h = tk.Frame(parent, bg=HEADER)
    h.pack(fill="x")
    img = logo(56 * k)
    if img:
        tk.Label(h, image=img, bg=HEADER).pack(side="left", padx=(int(14 * k), int(10 * k)), pady=int(8 * k))
    txt = tk.Frame(h, bg=HEADER)
    txt.pack(side="left", pady=int(6 * k))
    tk.Label(txt, text=APP_NAME, bg=HEADER, fg=GOLD_LIGHT, font=(SERIF, 20, "bold")).pack(anchor="w")
    tk.Label(txt, text="hears all  ·  remembers all", bg=HEADER, fg="#b9aee0",
             font=(SERIF, 10, "italic")).pack(anchor="w")
    timer = tk.Label(h, text="00:00:00", bg=HEADER, fg="#8d82b3", font=("Consolas", 22, "bold"))
    timer.pack(side="right", padx=int(18 * k))
    tk.Frame(parent, bg=GOLD, height=max(2, int(2 * k))).pack(fill="x")  # gold rule
    return h, timer
