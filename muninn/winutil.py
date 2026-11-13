"""Windows helpers: DPI, exclusion from screen sharing, list of screens and windows."""
import ctypes
import sys
from ctypes import wintypes

IS_WIN = sys.platform == "win32"
WDA_NONE = 0x0
WDA_EXCLUDEFROMCAPTURE = 0x11


def set_dpi_aware():
    if not IS_WIN:
        return
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # per-monitor v2
        return
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def top_hwnd(tk_widget):
    """HWND of the top-level window of a Tk widget."""
    if not IS_WIN:
        return None
    return ctypes.windll.user32.GetAncestor(tk_widget.winfo_id(), 2)  # GA_ROOT


def exclude_from_capture(tk_widget, exclude=True):
    """Makes the window invisible to screen sharing and recordings (Windows 10 2004+)."""
    if not IS_WIN:
        return False
    hwnd = top_hwnd(tk_widget)
    ok = ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE if exclude else WDA_NONE)
    return bool(ok)


# ---------- Screens (DXGI outputs, same order used by ffmpeg ddagrab) ----------

class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _DXGI_OUTPUT_DESC(ctypes.Structure):
    _fields_ = [
        ("DeviceName", ctypes.c_wchar * 32),
        ("DesktopCoordinates", _RECT),
        ("AttachedToDesktop", wintypes.BOOL),
        ("Rotation", ctypes.c_uint),
        ("Monitor", ctypes.c_void_p),
    ]


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort), ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]


def _guid(s):
    import uuid
    u = uuid.UUID(s)
    g = _GUID()
    g.Data1, g.Data2, g.Data3 = u.fields[0], u.fields[1], u.fields[2]
    for i, b in enumerate(u.bytes[8:]):
        g.Data4[i] = b
    return g


def _vcall(obj, index, restype, *args, argtypes=()):
    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    fn = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtbl[index])
    return fn(obj, *args)


def _release(obj):
    if obj:
        _vcall(obj, 2, ctypes.c_ulong)


def _dxgi_outputs():
    """Outputs of the first DXGI adapter (the one ddagrab uses)."""
    outs = []
    factory = ctypes.c_void_p()
    iid = _guid("770aae78-f26f-4dba-a829-253c83d1b387")  # IDXGIFactory1
    hr = ctypes.windll.dxgi.CreateDXGIFactory1(ctypes.byref(iid), ctypes.byref(factory))
    if hr != 0:
        raise OSError("CreateDXGIFactory1 failed")
    try:
        adapter = ctypes.c_void_p()
        # IDXGIFactory1::EnumAdapters1 = index 12
        if _vcall(factory, 12, ctypes.c_long, 0, ctypes.byref(adapter),
                  argtypes=(ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p))) != 0:
            return outs
        try:
            i = 0
            while True:
                output = ctypes.c_void_p()
                # IDXGIAdapter::EnumOutputs = index 7
                hr = _vcall(adapter, 7, ctypes.c_long, i, ctypes.byref(output),
                            argtypes=(ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p)))
                if hr != 0:
                    break
                try:
                    desc = _DXGI_OUTPUT_DESC()
                    # IDXGIOutput::GetDesc = index 7
                    _vcall(output, 7, ctypes.c_long, ctypes.byref(desc), argtypes=(ctypes.POINTER(_DXGI_OUTPUT_DESC),))
                    r = desc.DesktopCoordinates
                    outs.append({"index": i, "name": desc.DeviceName, "x": r.left, "y": r.top,
                                 "w": r.right - r.left, "h": r.bottom - r.top})
                finally:
                    _release(output)
                i += 1
        finally:
            _release(adapter)
    finally:
        _release(factory)
    return outs


def _gdi_monitors():
    mons = []
    MonitorEnumProc = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(_RECT), ctypes.c_double)

    def cb(hmon, hdc, lprect, data):
        r = lprect.contents
        mons.append({"index": len(mons), "name": f"DISPLAY{len(mons)+1}", "x": r.left, "y": r.top,
                     "w": r.right - r.left, "h": r.bottom - r.top})
        return 1

    ctypes.windll.user32.EnumDisplayMonitors(None, None, MonitorEnumProc(cb), 0)
    return mons


def list_monitors():
    if not IS_WIN:
        return []
    try:
        mons = _dxgi_outputs()
        if mons:
            return mons
    except Exception:
        pass
    return _gdi_monitors()


# ---------- Windows ----------

def list_windows():
    """Visible windows with a title: list of (hwnd, title)."""
    if not IS_WIN:
        return []
    user32 = ctypes.windll.user32
    res = []
    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        if user32.GetWindow(hwnd, 4):  # GW_OWNER: skip secondary windows
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n == 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        title = buf.value.strip()
        cloaked = ctypes.c_int(0)
        try:
            ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
        except Exception:
            pass
        if title and not cloaked.value and title not in ("Program Manager",):
            res.append((int(hwnd), title))
        return True

    user32.EnumWindows(EnumWindowsProc(cb), 0)
    return res


def window_rect(hwnd):
    """Visible window rectangle (without shadow), in physical pixels."""
    r = _RECT()
    try:
        hr = ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(r), ctypes.sizeof(r))
        if hr != 0:
            raise OSError
    except Exception:
        ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def is_window(hwnd):
    return IS_WIN and bool(ctypes.windll.user32.IsWindow(hwnd))


def is_minimized(hwnd):
    return IS_WIN and bool(ctypes.windll.user32.IsIconic(hwnd))


def region_for_window(hwnd, monitors):
    """Screen containing the window and the area (relative to the screen) to capture."""
    x, y, w, h = window_rect(hwnd)
    cx, cy = x + w // 2, y + h // 2
    mon = None
    for m in monitors:
        if m["x"] <= cx < m["x"] + m["w"] and m["y"] <= cy < m["y"] + m["h"]:
            mon = m
            break
    if mon is None and monitors:
        mon = monitors[0]
    if mon is None:
        return None
    left = max(x, mon["x"])
    top = max(y, mon["y"])
    right = min(x + w, mon["x"] + mon["w"])
    bottom = min(y + h, mon["y"] + mon["h"])
    rw = (right - left) // 2 * 2
    rh = (bottom - top) // 2 * 2
    if rw < 16 or rh < 16:
        return None
    return mon, left - mon["x"], top - mon["y"], rw, rh
