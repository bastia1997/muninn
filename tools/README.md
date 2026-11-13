# tools

## Overview

Scripts for developers; the app does not use them at run time. `build.bat` and
`build.py` turn the sources into the standalone installer
(`dist\Muninn-Setup.exe`) and uninstaller (`dist\Muninn-Uninstall.exe`).
`make_icons.py` regenerates the PNG icons and `muninn.ico` from the SVGs in
`assets\svg\`. The sources for the installer and uninstaller exes are in
[`installer/`](./installer/README.md).

## Interface

- `build.bat`: takes no arguments.
  - It sets `chcp 65001` and switches to the repo root.
  - It requires `.venv\Scripts\python.exe`. Otherwise it prints "Run install.bat first", pauses and exits 1.
  - It installs `requirements-dev.txt` (`pip --quiet`) and runs `tools\build.py`.
  - On any failure it prints "Build failed.", pauses and exits 1.
- `build.py` `main()`: the full build pipeline; it takes no CLI arguments.
  - `version()`: takes the first `muninn/__init__.py` line that starts with `__version__` and returns the part after `=` (naive `split("=")[1]`). It returns `"0.0.0"` only when there is no such line.
  - `pyinstaller(*args)`: runs `python -m PyInstaller` with fixed base options, `check=True`, from the repo root.
- `make_icons.py` `main()`: takes no arguments and is run by hand. It renders `assets/svg/*.svg` to PNGs and writes `assets/muninn.ico`.
  - `find_browser()`: raises `SystemExit` if nothing is found. Otherwise it returns the first match in this order:
    - the fixed install paths: Chrome under `%ProgramFiles%`, Chrome under `%LocalAppData%`, Edge under `%ProgramFiles(x86)%`, Edge under `%ProgramFiles%`;
    - `PATH`: `msedge`, then `chrome`, then `chromium`.
  - `render(browser, svg, size, dest)`: screenshots the SVG in a headless browser at `size × size`. It uses `check=True` and `timeout=60`, so a browser that fails or hangs aborts the script.
  - `write_ico(pngs, dest)`: writes an ICO file with PNG-compressed entries.
- Subdirectory: [`installer/`](./installer/README.md) holds `common.py`, `setup.py` and `uninstall.py`, the installer and uninstaller sources.

## Invariants

- Paths: `ROOT` is the folder above `tools/`, plus `BUILD=ROOT/build`, `DIST=ROOT/dist`, `INST=ROOT/tools/installer`, `ICO=ROOT/assets/muninn.ico` [`build.py`: module constants].
- Every PyInstaller call adds `--noconfirm --clean --log-level WARN --workpath build/work --specpath build/spec` [`build.py:pyinstaller`].
- **App build:** `--name Muninn --windowed --icon assets/muninn.ico --distpath build/app`, from `main.py` [`build.py:main`]. It bundles:
  - `assets/png`, `assets/muninn.ico` and `muninn/lang`;
  - the `imageio_ffmpeg` data and binaries;
  - `--hidden-import pystray._win32`.
- The build asserts that `build/app/Muninn/Muninn.exe` exists before it packages anything [`build.py:main`].
- **Installer and uninstaller exes:** both are built with `--onefile --windowed --icon assets/muninn.ico --paths tools/installer --distpath build/exe`. Both bundle `muninn.ico` and `assets/png/muninn_64.png` at the bundle root, because `common.window` reads them [`build.py:main`, `common`].
- **Build order:**
  1. the app;
  2. `build/payload.zip` (deflate, level 6) and `build/version.txt`;
  3. `build/exe/Muninn-Uninstall.exe`;
  4. `build/exe/Muninn-Setup.exe`, which also bundles the payload, the version file and the uninstaller.

  Both exes are then copied to `dist/` with `copy2` [`build.py:main`].
- **Icons:** `make_icons.py` needs Chrome, Edge or Chromium. The sizes are discrete lists [`make_icons.py:ICON_SIZES`, `APP_PNG`, `BUTTON_SIZES`]:
  - ICO: 16, 24, 32, 48, 64, 128 and 256 px;
  - app PNGs: 32, 48, 64, 96, 128 and 256 px;
  - button PNGs: 20, 30 and 40 px (100/150/200 %).
- **ICO format:** each entry is 32 bpp with PNG data, and a 256 px size is written as 0 in the directory entry [`make_icons.py:write_ico`].
- `assets/svg/muninn.svg` is the source of the app icon. Every other SVG produces `<stem>_<size>.png` [`make_icons.py:main`].

## Side Effects

- `build.py` deletes and recreates `build/`, then writes:
  - `build/work`, `build/spec`, `build/app/Muninn/`;
  - `build/payload.zip`, `build/version.txt`;
  - `build/exe/*.exe`.

  It creates `dist/` and overwrites `dist/Muninn-Setup.exe` and `dist/Muninn-Uninstall.exe` [`build.py:main`].
- `build.py` runs PyInstaller three times as subprocesses [`build.py:pyinstaller`].
- `build.bat` installs `requirements-dev.txt` into `.venv` and waits for a keypress (`pause`).
- `make_icons.py` runs a headless browser once per image, in a temporary folder that it deletes afterwards [`make_icons.py:render`].
- `make_icons.py` overwrites `assets/png/*.png` and `assets/muninn.ico`, then deletes the ICO-only `muninn_16.png` and `muninn_24.png` [`make_icons.py:main`].

## Verification

- There is no automated test suite. Run `tools\build.bat`, then check that
  `dist\Muninn-Setup.exe` and `dist\Muninn-Uninstall.exe` exist.
