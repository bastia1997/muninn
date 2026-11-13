"""Converts the SVGs in assets/svg to PNG (for Tk) and to muninn.ico (for the Desktop shortcut).

Only needed when changing the artwork: the PNGs and the ICO are already included. Uses Chrome (or Edge) headless,
so it needs no extra libraries.
    .venv\\Scripts\\python.exe tools\\make_icons.py
"""
import os
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SVG = ROOT / "assets" / "svg"
OUT = ROOT / "assets"

ICON_SIZES = [16, 24, 32, 48, 64, 128, 256]   # inside the ICO
APP_PNG = [32, 48, 64, 96, 128, 256]           # window icon and header
BUTTON_SIZES = [20, 30, 40]                    # button icons (100%, 150%, 200%)


def find_browser():
    cands = [  # Chrome first: headless Edge is sometimes blocked by corporate policies
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
    ]
    for c in cands:
        if Path(c).exists():
            return c
    for name in ("msedge", "chrome", "chromium"):
        if shutil.which(name):
            return shutil.which(name)
    raise SystemExit("Edge or Chrome is needed to convert the SVGs.")


def render(browser, svg: Path, size: int, dest: Path):
    """Renders the SVG to a transparent size×size PNG."""
    with tempfile.TemporaryDirectory() as td:
        page = Path(td) / "p.html"
        page.write_text(
            "<!doctype html><html><head><style>html,body{margin:0;background:transparent;overflow:hidden}"
            f"img{{display:block;width:{size}px;height:{size}px}}</style></head>"
            f"<body><img src='{svg.as_uri()}'></body></html>", encoding="utf-8")
        shot = Path(td) / "s.png"
        subprocess.run([browser, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                        "--force-device-scale-factor=1", "--default-background-color=00000000",
                        f"--user-data-dir={Path(td) / 'profile'}", "--allow-file-access-from-files",
                        f"--window-size={size},{size}", f"--screenshot={shot}", page.as_uri()],
                       check=True, capture_output=True, timeout=60)
        dest.write_bytes(shot.read_bytes())


def write_ico(pngs, dest: Path):
    """ICO with embedded PNGs (supported from Windows Vista on)."""
    datas = [(s, p.read_bytes()) for s, p in pngs]
    head = struct.pack("<HHH", 0, 1, len(datas))
    offset = 6 + 16 * len(datas)
    entries, blobs = b"", b""
    for s, d in datas:
        b = 0 if s >= 256 else s
        entries += struct.pack("<BBBBHHII", b, b, 0, 0, 1, 32, len(d), offset)
        offset += len(d)
        blobs += d
    dest.write_bytes(head + entries + blobs)


def main():
    browser = find_browser()
    (OUT / "png").mkdir(parents=True, exist_ok=True)
    tmp_ico = []
    for s in sorted(set(ICON_SIZES + APP_PNG)):
        p = OUT / "png" / f"muninn_{s}.png"
        render(browser, SVG / "muninn.svg", s, p)
        if s in ICON_SIZES:
            tmp_ico.append((s, p))
    write_ico(tmp_ico, OUT / "muninn.ico")
    for svg in sorted(SVG.glob("*.svg")):
        if svg.stem == "muninn":
            continue
        for s in BUTTON_SIZES:
            render(browser, svg, s, OUT / "png" / f"{svg.stem}_{s}.png")
    for s in ICON_SIZES:  # keep only the sizes the app needs
        if s not in APP_PNG:
            (OUT / "png" / f"muninn_{s}.png").unlink(missing_ok=True)
    print("Done:", OUT)


if __name__ == "__main__":
    main()
