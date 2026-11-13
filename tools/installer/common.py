"""Parts shared by Muninn-Setup.exe and Muninn-Uninstall.exe (Python standard library only)."""
import ctypes
import os
import socket
import subprocess
import sys
import time
import tkinter as tk
import winreg
from pathlib import Path
from tkinter import ttk

APP = "Muninn"
EXE = "Muninn.exe"
UNINSTALLER = "Muninn-Uninstall.exe"
REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Muninn"
SINGLE_PORT = 47613  # same port the app uses to detect an instance that is already running
DEFAULT_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Programs" / APP
DATA_DIR = Path(os.environ.get("APPDATA") or Path.home()) / APP
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

HEADER, GOLD, BG, INK, ACCENT = "#1d1442", "#ffe8a3", "#f5eedc", "#2a2140", "#5b3aa8"


def resource(name):
    """File bundled in the exe (PyInstaller extracts it to sys._MEIPASS)."""
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / name


# ---------------- language: Italian if Windows is in Italian, otherwise English ----------------

def _is_italian():
    try:
        return (ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF) == 0x10
    except Exception:
        return False


IT = _is_italian()


def T(it, en):
    return it if IT else en


# ---------------- running Muninn ----------------

def is_running():
    try:
        with socket.create_connection(("127.0.0.1", SINGLE_PORT), timeout=0.3):
            return True
    except OSError:
        return False


def close_running(timeout=20):
    """Asks Muninn to quit and waits. True if in the end it is no longer running."""
    if not is_running():
        return True
    try:
        with socket.create_connection(("127.0.0.1", SINGLE_PORT), timeout=0.5) as s:
            s.sendall(b"quit")
    except OSError:
        pass
    t = time.time()
    while time.time() - t < timeout:
        time.sleep(0.5)
        if not is_running():
            time.sleep(1.0)  # let it finish closing its files
            return True
    return False


# ---------------- shortcuts and Windows registry ----------------

def _ps(script):
    return subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                          capture_output=True, text=True, creationflags=NO_WINDOW, timeout=60)


def _q(s):
    return "'" + str(s).replace("'", "''") + "'"


def create_shortcuts(install_dir, desktop=True, start_menu=True):
    install_dir = Path(install_dir).resolve()
    exe = Path(install_dir) / EXE
    parts = ["$w=New-Object -ComObject WScript.Shell"]
    targets = []
    if desktop:
        targets.append("[Environment]::GetFolderPath('Desktop')")
    if start_menu:
        targets.append("[Environment]::GetFolderPath('Programs')")
    for t in targets:
        parts.append(f"$s=$w.CreateShortcut(({t})+'\\{APP}.lnk'); $s.TargetPath={_q(exe)}; "
                     f"$s.WorkingDirectory={_q(install_dir)}; $s.IconLocation={_q(str(exe) + ',0')}; "
                     f"$s.Description='Muninn: hears all, remembers all'; $s.Save()")
    _ps("; ".join(parts))


def remove_shortcuts(install_dir):
    """Removes the «Muninn» Desktop and Start shortcuts that point to this installation."""
    install_dir = Path(install_dir).resolve()
    # literal comparison with the trailing separator: «...\Muninn2» must not count as «...\Muninn», and
    # square brackets in the path must not act as wildcards (as they would with -like)
    prefix = str(install_dir).rstrip("\\") + "\\"
    script = (
        "$w=New-Object -ComObject WScript.Shell; "
        "foreach($d in @([Environment]::GetFolderPath('Desktop'),[Environment]::GetFolderPath('Programs'))){"
        f" $p=$d+'\\{APP}.lnk'; if(Test-Path -LiteralPath $p){{ $t=$w.CreateShortcut($p).TargetPath;"
        f" if($t -and $t.StartsWith({_q(prefix)},[StringComparison]::OrdinalIgnoreCase))"
        "{ Remove-Item -LiteralPath $p -Force } } }")
    _ps(script)


def register(install_dir, version, size_kb):
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, REG_KEY) as k:
        exe = str(Path(install_dir) / EXE)
        for name, val in [("DisplayName", APP), ("DisplayVersion", version), ("Publisher", APP),
                          ("DisplayIcon", exe + ",0"), ("InstallLocation", str(install_dir)),
                          ("UninstallString", f'"{Path(install_dir) / UNINSTALLER}"'),
                          ("Comments", "Hears all. Remembers all.")]:
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, val)
        for name, val in [("NoModify", 1), ("NoRepair", 1), ("EstimatedSize", int(size_kb))]:
            winreg.SetValueEx(k, name, 0, winreg.REG_DWORD, val)


def registered_dir():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_KEY) as k:
            return Path(winreg.QueryValueEx(k, "InstallLocation")[0])
    except OSError:
        return None


def unregister():
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, REG_KEY)
    except OSError:
        pass


def recordings_dir():
    """Where the user saves recordings (read from config.json), to tell them they stay there."""
    try:
        import json
        return json.loads((DATA_DIR / "config.json").read_text(encoding="utf-8")).get("output_dir")
    except Exception:
        return None


# ---------------- window ----------------

def window(title):
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass
    root = tk.Tk()
    root.title(title)
    root.configure(bg=BG)
    root.resizable(False, False)
    try:
        root.iconbitmap(default=str(resource("muninn.ico")))
    except tk.TclError:
        pass
    k = max(1.0, root.winfo_fpixels("1i") / 96.0)
    s = ttk.Style(root)
    try:
        s.theme_use("clam")
    except tk.TclError:
        pass
    s.configure(".", background=BG, foreground=INK, font=("Segoe UI", 10))
    s.configure("TButton", padding=(12, 5), background="#efe4c8", bordercolor="#d6c49a")
    s.map("TButton", background=[("active", "#f7ecd0"), ("disabled", "#ece5d3")])
    s.configure("Go.TButton", background=ACCENT, foreground="white", font=("Segoe UI", 10, "bold"))
    s.map("Go.TButton", background=[("active", "#6a48bd"), ("disabled", "#a99ccb")])
    s.configure("TCheckbutton", background=BG)
    s.map("TCheckbutton", background=[("active", BG)], indicatorbackground=[("selected", ACCENT)])
    s.configure("Horizontal.TProgressbar", background="#3fb6d9", troughcolor="#e8dcbc", thickness=10)
    head = tk.Frame(root, bg=HEADER)
    head.pack(fill="x")
    try:
        root._logo = tk.PhotoImage(file=str(resource("muninn_64.png")))
        tk.Label(head, image=root._logo, bg=HEADER).pack(side="left", padx=int(14 * k), pady=int(10 * k))
    except tk.TclError:
        pass
    tx = tk.Frame(head, bg=HEADER)
    tx.pack(side="left")
    tk.Label(tx, text=APP, bg=HEADER, fg=GOLD, font=("Palatino Linotype", 20, "bold")).pack(anchor="w")
    tk.Label(tx, text="hears all  ·  remembers all", bg=HEADER, fg="#b9aee0",
             font=("Palatino Linotype", 10, "italic")).pack(anchor="w")
    tk.Frame(root, bg="#d9a441", height=2).pack(fill="x")
    body = ttk.Frame(root, padding=int(16 * k))
    body.pack(fill="both", expand=True)
    return root, body, k
