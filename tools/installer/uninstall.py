"""Muninn-Uninstall.exe: removes Muninn installed with Muninn-Setup.exe.

Removes the program, shortcuts and the «Installed apps» entry. Recordings are never deleted; the
settings (with the DeepInfra key) only on request. An exe cannot delete itself while running:
if started from the install folder it copies itself to %TEMP% and continues from there."""
import os
import shutil
import subprocess
import sys
import tempfile
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from common import (APP, DATA_DIR, EXE, NO_WINDOW, T, close_running, recordings_dir, registered_dir,
                    remove_shortcuts, unregister, window)


def find_install():
    d = registered_dir()
    if d and (d / EXE).exists():
        return d
    here = Path(sys.executable).resolve().parent
    return here if (here / EXE).exists() else d


def relaunch_from_temp(install_dir):
    """If running inside the folder to delete, copy myself to %TEMP% and restart from there."""
    me = Path(sys.executable).resolve()
    inside = install_dir is not None and os.path.normcase(str(me)).startswith(
        os.path.normcase(str(install_dir.resolve())) + os.sep)
    if not getattr(sys, "frozen", False) or not inside:
        return False
    tmp = Path(tempfile.gettempdir()) / f"Muninn-Uninstall-{os.getpid()}.exe"
    shutil.copy2(me, tmp)
    subprocess.Popen([str(tmp), "--run", str(install_dir)] + [a for a in sys.argv[1:] if a.startswith("--")],
                     creationflags=NO_WINDOW)
    return True


def _inside(child, parent):
    """True if child is parent or lies inside parent (resolved paths, case-insensitive)."""
    c = os.path.normcase(str(Path(child).resolve()))
    p = os.path.normcase(str(Path(parent).resolve())).rstrip(os.sep)
    return c == p or c.startswith(p + os.sep)


def remove_tree(path, tries=10, keep=None):
    """Deletes path (retrying if some file is in use). If keep (the recordings folder) lies inside
    path, deletes everything else and leaves keep intact. Returns True if everything due was removed."""
    path = Path(path)
    if keep is not None and _inside(keep, path):
        if _inside(path, keep):  # path is the folder to keep itself (or lies inside it): touch nothing
            return True
        ok = True
        try:
            entries = list(path.iterdir())
        except FileNotFoundError:
            return True
        for entry in entries:
            ok = remove_tree(entry, tries, keep) and ok
        return ok
    for _ in range(tries):
        try:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink()
            return True
        except FileNotFoundError:
            return True
        except OSError:
            time.sleep(0.7)
    return not path.exists()


def uninstall(install_dir, delete_settings):
    if not close_running():
        raise RuntimeError(T("Muninn è ancora aperto (forse sta registrando): chiudilo e riprova.",
                             "Muninn is still running (maybe recording): close it and try again."))
    remove_shortcuts(install_dir)
    unregister()
    rec = recordings_dir()  # recordings are never touched, even if they lie inside one of the folders to remove
    keep = Path(rec) if rec else None
    ok = remove_tree(install_dir, keep=keep)
    if delete_settings:
        remove_tree(DATA_DIR, keep=keep)
    return ok


def self_delete_later():
    """The uninstaller copied to %TEMP% deletes itself a few seconds after closing."""
    me = Path(sys.executable).resolve()
    tmp = os.path.normcase(str(Path(tempfile.gettempdir()).resolve())) + os.sep  # resolve: no 8.3 short names
    if getattr(sys, "frozen", False) and os.path.normcase(str(me)).startswith(tmp):
        subprocess.Popen(f'cmd /c ping 127.0.0.1 -n 4 > nul & del /f /q "{me}"', creationflags=NO_WINDOW)


def gui(install_dir):
    root, body, k = window(T("Disinstalla Muninn", "Uninstall Muninn"))
    if install_dir is None or not install_dir.exists():
        root.withdraw()
        messagebox.showinfo(APP, T("Muninn non risulta installato su questo PC.", "Muninn is not installed on this PC."))
        root.destroy()
        return
    rec = recordings_dir()
    ttk.Label(body, text=T(f"Vuoi togliere Muninn da\n{install_dir}?", f"Do you want to remove Muninn from\n{install_dir}?"),
              font=("Segoe UI", 10, "bold")).pack(anchor="w")
    ttk.Label(body, text=T("Vengono tolti programma, collegamenti e voce in «App installate».",
                           "The program, shortcuts and the «Installed apps» entry are removed."),
              wraplength=int(440 * k)).pack(anchor="w", pady=(8, 0))
    if rec:
        ttk.Label(body, text=T(f"Le registrazioni restano dove sono:\n{rec}", f"Recordings stay where they are:\n{rec}"),
                  foreground="#6f6488", wraplength=int(440 * k)).pack(anchor="w", pady=(8, 0))
    v_del = tk.BooleanVar(value=False)
    ttk.Checkbutton(body, variable=v_del, text=T("Elimina anche le impostazioni (compresa la chiave DeepInfra)",
                                                 "Also delete the settings (including the DeepInfra key)")
                    ).pack(anchor="w", pady=(12, 0))
    status = ttk.Label(body, text="", foreground="#6f6488")
    status.pack(anchor="w", pady=(10, 0))
    btns = ttk.Frame(body)
    btns.pack(fill="x", pady=(12, 0))

    def go():
        for b in btns.winfo_children():
            b.configure(state="disabled")
        status.configure(text=T("Disinstallo…", "Uninstalling…"))
        root.update()
        try:
            ok = uninstall(install_dir, v_del.get())
            msg = T("Muninn è stato disinstallato.", "Muninn has been uninstalled.") if ok else T(
                f"Muninn è stato disinstallato, ma alcuni file sono rimasti in uso: cancella a mano\n{install_dir}",
                f"Muninn has been uninstalled, but some files were in use: delete by hand\n{install_dir}")
            messagebox.showinfo(APP, msg)
            root.destroy()
        except Exception as e:
            messagebox.showerror(APP, str(e))
            for b in btns.winfo_children():
                b.configure(state="normal")
            status.configure(text="")

    ttk.Button(btns, text=T("Disinstalla", "Uninstall"), style="Go.TButton", command=go).pack(side="right")
    ttk.Button(btns, text=T("Annulla", "Cancel"), command=root.destroy).pack(side="right", padx=8)
    root.mainloop()


if __name__ == "__main__":
    args = sys.argv[1:]
    target = Path(args[args.index("--run") + 1]) if "--run" in args else find_install()
    if "--run" not in args and relaunch_from_temp(target):
        sys.exit(0)
    try:
        if "--quiet" in args:  # without windows: for tests or automated deployment
            # exit code: 0 = removed, 1 = some files in use, 2 = Muninn not installed (errors: exception)
            if not target or not Path(target).exists():
                print("Muninn is not installed")
                sys.exit(2)
            if not uninstall(target, "--delete-settings" in args):
                print(f"Some files were in use: delete by hand {target}")
                sys.exit(1)
        else:
            gui(target)
    finally:
        self_delete_later()
