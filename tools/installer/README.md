# installer

## Overview

Per-user Windows installer and uninstaller for Muninn. They use tkinter and the
standard library only, and [`../build.py`](../build.py) bundles each one into a
single-file exe with PyInstaller. `setup.py` closes any running Muninn, extracts
the bundled `payload.zip` into the install folder, copies in the uninstaller,
creates shortcuts and registers an entry in Windows "Installed apps".
`uninstall.py` reverses those steps. It relaunches itself from `%TEMP%` so that
it can delete its own install folder.

## Interface

### `common.py` (shared)

- `APP = "Muninn"`, `EXE = "Muninn.exe"`, `UNINSTALLER = "Muninn-Uninstall.exe"`.
- `REG_KEY`: `Software\Microsoft\Windows\CurrentVersion\Uninstall\Muninn`, always under `HKEY_CURRENT_USER`.
- `SINGLE_PORT = 47613`: the localhost port the running app listens on.
- `DEFAULT_DIR`: `%LOCALAPPDATA%\Programs\Muninn`. `DATA_DIR`: `%APPDATA%\Muninn`. Both fall back to the home folder.
- `resource(name)`: path under `sys._MEIPASS` when frozen, otherwise the script folder.
- `IT` / `T(it, en)`: `T` returns the Italian text when `IT` is true, otherwise the English text. `IT` is true when `GetUserDefaultUILanguage() & 0x3FF == 0x10`, and false if that call raises an exception.
- `is_running()`: tries a TCP connect to `127.0.0.1:47613` with a 0.3 s timeout.
- `close_running(timeout=20)`:
  - It sends `b"quit"` to that port (0.5 s connect timeout; an `OSError` is ignored).
  - It polls every 0.5 s. Once the port is closed it waits another 1 s and returns `True`.
  - It returns `False` after `timeout` seconds.
- `create_shortcuts(install_dir, desktop=True, start_menu=True)`: always runs PowerShell, even when both flags are False. Each `Muninn.lnk` gets `WorkingDirectory`, `IconLocation` (`<exe>,0`) and the description "Muninn: hears all, remembers all".
- `remove_shortcuts(install_dir)`: see Invariants for how it matches the shortcuts to delete.
- `register(install_dir, version, size_kb)` / `registered_dir()` / `unregister()`: write, read (`InstallLocation`) and delete the uninstall registry key. `register` writes:
  - `DisplayName`, `DisplayVersion`, `Publisher`, `DisplayIcon` (`<exe>,0`), `InstallLocation`, a quoted `UninstallString`, `Comments`;
  - `NoModify=1`, `NoRepair=1`;
  - `EstimatedSize`, the total size of the installed files in KiB.
- `NO_WINDOW`: `subprocess.CREATE_NO_WINDOW`, used for PowerShell, for launching `Muninn.exe` and for the relaunched uninstaller.
- `recordings_dir()`: the `output_dir` value from `DATA_DIR\config.json`, or `None`.
- `window(title)`: builds a DPI-aware Tk root with the Muninn header and returns `(root, body, scale)`.

### `setup.py` → `Muninn-Setup.exe`

- `Setup`: the GUI, with an install-folder field and three checkboxes: Desktop shortcut, Start menu shortcut, run after install.
  - The folder field starts with `--dir`, then `registered_dir()` (the previous install), then `DEFAULT_DIR`. An empty field also falls back to `DEFAULT_DIR`.
  - `Setup.browse()` appends `\Muninn` to the chosen folder, unless the folder is already named `Muninn`.
- `VERSION`: read from the bundled `version.txt` when the module is imported, or `"?"`.
- Flags:
  - `--dir <folder>`: the initial install folder.
  - `--quiet`: the Tk window is built but hidden, the app is not launched afterwards, and the install starts after 100 ms.
  - `--no-shortcuts`: skips the shortcuts. The "Creating shortcuts…" message still appears.
  - `--no-register`: skips the registry entry.

### `uninstall.py` → `Muninn-Uninstall.exe`

- `find_install()`: returns the first of these:
  1. the registered `InstallLocation`, if it contains `Muninn.exe`;
  2. the uninstaller's own folder, if it contains `Muninn.exe`;
  3. the registered folder anyway. It may be `None` or stale.
- `relaunch_from_temp(install_dir)`: copies the exe to `%TEMP%\Muninn-Uninstall-<pid>.exe` and relaunches it with `--run <dir>`, every original `--` flag and `CREATE_NO_WINDOW`.
- `remove_tree(path, tries=10, keep=None)`: deletes `path`, leaving `keep` intact if it is inside `path`. `_inside(child, parent)` is the path test it uses.
- `uninstall(install_dir, delete_settings)`.
- `self_delete_later()`: runs `cmd /c ping 127.0.0.1 -n 4 & del /f /q <self>` (about 3 s later). `__main__` calls it in a `finally`, so it also runs after an error.
- `gui(install_dir)`:
  - If no install is found, it shows "not installed" and exits.
  - If some files cannot be removed, it says some files were in use and must be deleted by hand.
- Flags:
  - `--quiet`.
  - `--delete-settings`: only together with `--quiet`.
  - `--run <install_dir>`: internal; used when the uninstaller relaunches itself from `%TEMP%`.

## Invariants

- Installs per user, with no administrator rights. `%LOCALAPPDATA%\Programs\Muninn` is used only when there is no `--dir` and no previous registered install [`common.py:DEFAULT_DIR`, `setup.py:Setup`].
- The registry is touched only at `HKCU\REG_KEY` [`common.py:register`, `registered_dir`, `unregister`].
- Bundled resources are read from `sys._MEIPASS` [`common.py:resource`]:
  - `payload.zip` and `Muninn-Uninstall.exe` in `Setup.install`;
  - `version.txt` when `setup.py` is imported;
  - `muninn.ico` and `muninn_64.png` in `common.window`.

  The uninstaller exe bundles only the two icons.
- If the target folder is not empty and has no `Muninn.exe`, the installer installs into `target\Muninn` instead [`setup.py:Setup.start`].
- On an upgrade, only `_internal\` and `Muninn.exe` are removed before extracting; other files in the folder are kept [`setup.py:Setup.install`].
- Install and uninstall stop with an error if Muninn is still running after `close_running` gives up (20 s) [`setup.py:Setup.install`, `uninstall.py:uninstall`].
- Uninstall never deletes the recordings folder (`recordings_dir()`). If it is inside the install folder or inside `%APPDATA%\Muninn`, `remove_tree(..., keep=...)` deletes everything else and leaves that folder intact [`uninstall.py:uninstall`, `remove_tree`].
- `DATA_DIR` (settings, including the DeepInfra key) is deleted only with `--quiet --delete-settings` or the GUI checkbox, which is unticked by default [`uninstall.py:uninstall`, `gui`].
- A shortcut is removed only when its target starts with `install_dir\`. The comparison is literal and case-insensitive (`StartsWith(..., OrdinalIgnoreCase)`), so a sibling folder such as `…\Muninn2` does not match, and `[` and `]` are not wildcards [`common.py:remove_shortcuts`].
- `--quiet` exit codes [`setup.py:__main__`, `uninstall.py:__main__`]:
  - setup: 0 when installed, 1 when the install failed;
  - uninstall: 0 when removed, 1 when some files were in use, 2 when Muninn is not installed. An unexpected error raises an exception, which also ends with a non-zero code.
- The uninstaller relaunches from `%TEMP%` only when it is frozen and running from inside `install_dir`, and deletes itself only when its exe is under `%TEMP%` [`uninstall.py:relaunch_from_temp`, `self_delete_later`].

## Side Effects

- Opens a TCP connection to `127.0.0.1:47613` and sends `quit` to the running app [`common.py:close_running`].
- Runs `powershell -NoProfile -ExecutionPolicy Bypass` (no window, 60 s timeout) to create and read `.lnk` files [`common.py:_ps`].
- Creates or deletes `Muninn.lnk` on the Desktop and in Start menu → Programs [`common.py:create_shortcuts`, `remove_shortcuts`].
- Writes or deletes the HKCU uninstall key (`DisplayName`, `DisplayVersion`, `InstallLocation`, `UninstallString`, `EstimatedSize`, `NoModify`, `NoRepair`, …) [`common.py:register`, `unregister`].
- Creates the install folder, extracts `payload.zip` into it, copies `Muninn-Uninstall.exe` there, then launches `Muninn.exe` unless `--quiet` is set or "run after install" is unticked [`setup.py:Setup.install`, `Setup.done`].
- Copies itself to `%TEMP%\Muninn-Uninstall-<pid>.exe` and relaunches with `--run <dir>` [`uninstall.py:relaunch_from_temp`].
- Recursively deletes the install folder (up to 10 tries, 0.7 s apart) and, if requested, `%APPDATA%\Muninn` [`uninstall.py:uninstall`, `remove_tree`].
- Runs `cmd /c ping … & del` so the `%TEMP%` copy is deleted after the uninstaller exits [`uninstall.py:self_delete_later`].
- Calls `SetProcessDpiAwareness(2)` [`common.py:window`].

## Verification

- There is no automated test suite. Build with `tools\build.bat`, then run
  `dist\Muninn-Setup.exe --quiet --dir <new empty folder>` followed by
  `<new empty folder>\Muninn-Uninstall.exe --quiet`. The folder must be new or
  empty, otherwise setup installs into `<folder>\Muninn`. Both commands should
  exit with code 0 (see the `--quiet` exit codes under Invariants).
