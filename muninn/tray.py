"""Windows tray icon, window without a taskbar button, and single instance.

- The main window (and the Notary) become "owned" by a hidden window: Windows does not show a taskbar button
  for owned windows.
- Minimize and X hide the window in the tray; clicking the icon reopens it, "Quit" really closes it.
- If Muninn is already open, a second launch (e.g. from the Desktop shortcut) reopens the existing window.
Tray commands arrive from the pystray thread: they are passed to the GUI with `post(action)`."""
import ctypes
import socket
import sys
import threading
import tkinter as tk

from .theme import APP_NAME, ASSETS

try:
    import pystray
    from PIL import Image
except ImportError:  # without pystray/Pillow the app works as a normal window
    pystray = None

SINGLE_PORT = 47613  # local port used only to detect an already open instance
GWLP_HWNDPARENT = -8
GWL_EXSTYLE = -20
WS_EX_APPWINDOW = 0x00040000


# ---------------- single instance ----------------

def another_instance_running():
    """True if Muninn is already open: asks it to show itself. Otherwise False (and this becomes the instance)."""
    try:
        with socket.create_connection(("127.0.0.1", SINGLE_PORT), timeout=0.5) as s:
            s.sendall(b"show")
        return True
    except OSError:
        return False


def listen_for_instances(on_show, on_quit=None):
    """Keeps listening: a second launch sends "show", the installer "quit" (callbacks from the socket thread)."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        srv.bind(("127.0.0.1", SINGLE_PORT))
    except OSError:
        return None
    srv.listen(2)

    def run():
        while True:
            try:
                c, _ = srv.accept()
            except OSError:
                return
            try:
                c.settimeout(1)
                msg = c.recv(16)
                if msg.startswith(b"show"):
                    on_show()
                elif msg.startswith(b"quit") and on_quit:
                    on_quit()
            except OSError:
                pass
            finally:
                c.close()
    threading.Thread(target=run, daemon=True).start()
    return srv


# ---------------- no taskbar button ----------------

def _wrapper_hwnd(win):
    win.update_idletasks()
    return int(win.wm_frame(), 16)


class TaskbarHider:
    """Makes windows "owned" by a hidden Toplevel, so they do not appear in the taskbar."""

    def __init__(self, root):
        self.ok = sys.platform == "win32"
        if not self.ok:
            return
        self.owner = tk.Toplevel(root)
        self.owner.withdraw()
        self.owner.title(APP_NAME + " (owner)")
        self.owner_hwnd = _wrapper_hwnd(self.owner)
        u = ctypes.windll.user32
        self._set = getattr(u, "SetWindowLongPtrW", None) or u.SetWindowLongW
        self._get = getattr(u, "GetWindowLongPtrW", None) or u.GetWindowLongW
        self._set.restype = ctypes.c_ssize_t
        self._set.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
        self._get.restype = ctypes.c_ssize_t
        self._get.argtypes = [ctypes.c_void_p, ctypes.c_int]

    def hide(self, win):
        if not self.ok:
            return
        try:
            h = _wrapper_hwnd(win)
            visible = win.winfo_viewable()
            if visible:
                win.withdraw()  # Windows recomputes the button only when the window reappears
            self._set(h, GWLP_HWNDPARENT, self.owner_hwnd)
            self._set(h, GWL_EXSTYLE, self._get(h, GWL_EXSTYLE) & ~WS_EX_APPWINDOW)
            if visible:
                win.deiconify()
        except Exception:
            pass


# ---------------- tray icon ----------------

class Tray:
    """Tray icon with menu. labels: already translated texts; post(action) goes to the GUI thread."""

    def __init__(self, post, labels, is_recording):
        self.post = post
        self.icon = None
        if pystray is None:
            return
        img = Image.open(ASSETS / "png" / "muninn_64.png")
        menu = pystray.Menu(
            pystray.MenuItem(labels["open"], lambda: post("show"), default=True),
            pystray.MenuItem(lambda item: labels["stop"] if is_recording() else labels["record"],
                             lambda: post("record")),
            pystray.MenuItem(labels["live"], lambda: post("live")),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(labels["quit"], lambda: post("quit")))
        self.icon = pystray.Icon("muninn", img, APP_NAME, menu)
        self.icon.run_detached()

    @property
    def available(self):
        return self.icon is not None

    def notify(self, text):
        if self.icon:
            try:
                self.icon.notify(text, APP_NAME)
            except Exception:
                pass

    def refresh(self):
        if self.icon:
            try:
                self.icon.update_menu()
            except Exception:
                pass

    def stop(self):
        if self.icon:
            try:
                self.icon.stop()
            except Exception:
                pass
            self.icon = None
