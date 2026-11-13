"""Muninn-Setup.exe: installs Muninn for the current user, without administrator rights.

Contains the ready-made program (payload.zip, with bundled Python) and the uninstaller. Copies everything to
%LOCALAPPDATA%\\Programs\\Muninn (or the chosen folder), creates the shortcuts and registers Muninn in
Windows «Installed apps». If Muninn is already installed it updates it: settings and recordings are kept."""
import shutil
import subprocess
import sys
import threading
import tkinter as tk
import zipfile
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from common import (APP, DEFAULT_DIR, EXE, NO_WINDOW, UNINSTALLER, T, close_running, create_shortcuts, register,
                    registered_dir, resource, window)

def _arg(name):
    """Options for automated installs: --dir <folder>, --quiet, --no-shortcuts, --no-register."""
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv[:-1] else None


VERSION = resource("version.txt").read_text(encoding="utf-8").strip() if resource("version.txt").exists() else "?"


class Setup:
    def __init__(self):
        self.root, body, k = window(T("Installa Muninn", "Install Muninn"))
        self.k = k
        ttk.Label(body, text=T(f"Muninn {VERSION} registra le call, le trascrive con DeepInfra e ricorda chi ha parlato.",
                               f"Muninn {VERSION} records calls, transcribes them with DeepInfra and remembers who spoke."),
                  wraplength=int(460 * k)).pack(anchor="w")
        ttk.Label(body, text=T("Cartella di installazione", "Install folder"), font=("Segoe UI", 10, "bold")).pack(
            anchor="w", pady=(14, 2))
        row = ttk.Frame(body)
        row.pack(fill="x")
        self.v_dir = tk.StringVar(value=_arg("--dir") or str(registered_dir() or DEFAULT_DIR))
        ttk.Entry(row, textvariable=self.v_dir, width=48).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text=T("Sfoglia…", "Browse…"), command=self.browse).pack(side="left", padx=(6, 0))
        self.v_desk = tk.BooleanVar(value=True)
        self.v_start = tk.BooleanVar(value=True)
        self.v_run = tk.BooleanVar(value=True)
        for var, txt in [(self.v_desk, T("Collegamento sul Desktop", "Desktop shortcut")),
                         (self.v_start, T("Collegamento nel menu Start", "Start menu shortcut")),
                         (self.v_run, T("Avvia Muninn alla fine", "Start Muninn when done"))]:
            ttk.Checkbutton(body, text=txt, variable=var).pack(anchor="w", pady=(6, 0))
        ttk.Label(body, text=T("Non servono diritti di amministratore né Python: è tutto incluso. Impostazioni e "
                               "registrazioni di un'installazione precedente restano.",
                               "No administrator rights or Python needed: everything is included. Settings and "
                               "recordings from a previous install are kept."),
                  foreground="#6f6488", wraplength=int(460 * k)).pack(anchor="w", pady=(12, 0))
        self.bar = ttk.Progressbar(body, maximum=100)
        self.bar.pack(fill="x", pady=(14, 4))
        self.status = ttk.Label(body, text="", foreground="#6f6488")
        self.status.pack(anchor="w")
        btns = ttk.Frame(body)
        btns.pack(fill="x", pady=(12, 0))
        self.go = ttk.Button(btns, text=T("Installa", "Install"), style="Go.TButton", command=self.start)
        self.go.pack(side="right")
        self.cancel = ttk.Button(btns, text=T("Annulla", "Cancel"), command=self.root.destroy)
        self.cancel.pack(side="right", padx=8)

    def browse(self):
        d = filedialog.askdirectory(initialdir=self.v_dir.get())
        if d:
            p = Path(d)
            self.v_dir.set(str(p if p.name.lower() == APP.lower() else p / APP))

    def ui(self, fn, *a):
        self.root.after(0, lambda: fn(*a))

    def set(self, pct, msg):
        self.ui(lambda: (self.bar.configure(value=pct), self.status.configure(text=msg)))

    def start(self):
        target = Path(self.v_dir.get().strip() or DEFAULT_DIR)
        if target.exists() and any(target.iterdir()) and not (target / EXE).exists():
            target = target / APP  # never install «into» a folder that holds other things
        self.v_dir.set(str(target))
        for w in (self.go, self.cancel):
            w.configure(state="disabled")
        threading.Thread(target=self.install, args=(target,), daemon=True).start()

    def install(self, target):
        try:
            self.set(2, T("Chiudo Muninn se è aperto…", "Closing Muninn if it is running…"))
            if not close_running():
                raise RuntimeError(T("Muninn è ancora aperto (forse sta registrando): chiudilo e riprova.",
                                     "Muninn is still running (maybe recording): close it and try again."))
            self.set(5, T("Preparo la cartella…", "Preparing the folder…"))
            target.mkdir(parents=True, exist_ok=True)
            for old in ("_internal", EXE):  # update: remove only the old program
                p = target / old
                if p.is_dir():
                    shutil.rmtree(p)
                elif p.exists():
                    p.unlink()
            with zipfile.ZipFile(resource("payload.zip")) as z:
                items = z.infolist()
                for i, it in enumerate(items):
                    z.extract(it, target)
                    if i % 40 == 0:
                        self.set(5 + 85 * i / len(items), T(f"Copio i file… {i}/{len(items)}",
                                                             f"Copying files… {i}/{len(items)}"))
            shutil.copy2(resource(UNINSTALLER), target / UNINSTALLER)
            self.set(92, T("Creo i collegamenti…", "Creating shortcuts…"))
            if "--no-shortcuts" not in sys.argv:
                create_shortcuts(target, self.v_desk.get(), self.v_start.get())
            if "--no-register" not in sys.argv:
                size = sum(f.stat().st_size for f in target.rglob("*") if f.is_file()) // 1024
                register(target, VERSION, size)
            self.set(100, T("Fatto.", "Done."))
            self.ui(self.done, target)
        except Exception as e:
            self.ui(self.failed, e)

    def done(self, target):
        if self.v_run.get():
            subprocess.Popen([str(target / EXE)], cwd=str(target), creationflags=NO_WINDOW)
        messagebox.showinfo(APP, T(f"Muninn è installato in\n{target}\n\nLo trovi sul Desktop, nel menu Start e, "
                                   "quando è aperto, nell'icona vicino all'orologio.\nPer toglierlo: «App installate» "
                                   "di Windows oppure Muninn-Uninstall.exe nella cartella.",
                                   f"Muninn is installed in\n{target}\n\nYou will find it on the Desktop, in the "
                                   "Start menu and, while it runs, in the icon next to the clock.\nTo remove it: "
                                   "Windows «Installed apps» or Muninn-Uninstall.exe in the folder."))
        self.root.destroy()

    def failed(self, e):
        messagebox.showerror(APP, T(f"Installazione non riuscita:\n{e}", f"Installation failed:\n{e}"))
        for w in (self.go, self.cancel):
            w.configure(state="normal")

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    if "--quiet" in sys.argv:  # install without windows (for tests or automated deployment)
        s = Setup()
        s.root.withdraw()
        s.v_run.set(False)
        s.done = lambda t: s.root.destroy()
        errors = []
        s.failed = lambda e: (print(e), errors.append(e), s.root.destroy())
        s.root.after(100, s.start)
        s.run()
        sys.exit(1 if errors else 0)  # so automated installers see the failure
    else:
        Setup().run()
