<p align="center"><img src="assets/png/muninn_128.png" width="96" alt=""></p>

<p align="center"><b>English</b> · <a href="README.it.md">Italiano</a></p>

# Muninn

*Hears all. Remembers all.* — Named after Muninn, Odin's raven of memory: he flies across the world, listens to
everything and comes back to report.

Muninn records your microphone, the call audio, screens and windows (e.g. Teams). When you stop recording it transcribes
everything, works out who is speaking and saves a folder ready to hand to an AI. It also shows live captions on screen
and a **Notary** window with everything that has been said.

It only uses **DeepInfra** with Nvidia **Nemotron 3.5**: transcription with per-word timestamps and separation of the
voices in the call. You assign the names yourself by listening to short clips: no other AI is involved. Windows 10/11.

The interface is available in English, Italian, French, German and Spanish (Settings → Language).

## Installation

You need Python 3.10+ with tkinter (the standard installer from python.org).

1. Download the code: **Code → Download ZIP** and extract it, or `git clone https://github.com/bastia1997/muninn.git`.
2. Run **`install.bat`**: it creates `.venv` with the dependencies (ffmpeg included, via `imageio-ffmpeg`) and a
   "Muninn" shortcut on the Desktop. If Python is missing it tries to install it with winget.
3. Start Muninn with **`run.bat`** or the Desktop shortcut.
4. Open **Settings**: paste your DeepInfra API key (https://deepinfra.com/dash/api_keys), enter your name, then
   **Save settings**.

To update, download the new version (or `git pull`) and run `install.bat` again. To uninstall, delete the folder and
the Desktop shortcut.

**Where your data lives**: `config.json` (settings and DeepInfra key, in plain text), `recordings\` and `error.log`
stay in the project folder.

### Standalone installer (optional)

`tools\build.bat` builds `dist\Muninn-Setup.exe` and `dist\Muninn-Uninstall.exe`: a self-contained installer that needs
no Python and no administrator rights. It installs to `%LOCALAPPDATA%\Programs\Muninn` with Desktop and Start menu
shortcuts, and shows up in Windows "Installed apps".

| What | Where (installed version) |
|---|---|
| Settings and DeepInfra key (plain text) | `%APPDATA%\Muninn\config.json` |
| Recordings and Notary | `Documents\Muninn` (can be changed in Settings) |
| Startup errors | `%APPDATA%\Muninn\error.log` |

The exes are not signed, so Windows SmartScreen or some antivirus products may warn about them or block them.

## Usage

1. **Audio**: microphone → label "Me"; headphones/speakers output → label "Call" with *Several people (call)*
   ticked. Not sure which device is the right one? **Test** (on the microphone it records and plays back, on the output
   it plays a chime) or **Show levels** and watch which bar moves.
2. **Video**: tick the screen where Teams is (or add the window): the live **preview** appears on the right.
3. **Start recording** … **Stop and save**. Muninn does the rest.

**Existing recordings** (Teams recordings, MP4, MP3, M4A…): **Process files…** and pick one or more. Each one gets its
own folder, just like a recording.

**Live captions**: on-screen captions (usually 1-2 s behind) and the **Notary** window, both hidden from screen
sharing. They use the sources ticked in *Audio*, work without recording and cost about 1-3 cents per hour.
**Real time EN**: true streaming (about half a second), **English only** (about $0.06 per hour per source): DeepInfra
only offers true streaming for the English model.

**Tray**: Muninn stays out of the taskbar and lives in the icon next to the clock. The X only hides the window
(recording and captions keep going); right-click the icon and choose **Quit** to exit.

The **Guide** tab inside the app explains everything step by step.

## What you get in the recording folder

| File | Content |
|---|---|
| `video_<name>.mp4` | Video with audio and **two embedded subtitle tracks**: *Transcript* (with speaker names, the default) and *Transcript (no names)* (in VLC: Subtitle menu) |
| `transcript.md` | The conversation in order, with times and names: **this is the one to give to the AI** |
| `transcript.json` | The same in structured form (seconds, wall-clock time, speaker, text) |
| `speakers.json` | Who is who: Speaker N → name |
| `data\` | Technical material: audio tracks, `.srt` files (with and without names), API responses, `session.json` |

## How speakers get their names

1. The microphone is transcribed separately and is always you (it shows the name you entered in Settings).
2. The call audio goes to Nemotron with speaker separation: each voice becomes *Speaker 1, 2…*. Up to 1 hour fits in a
   single request. Longer calls are split into blocks and Muninn links the voices of the same person across blocks with
   one extra request (never two voices from the same block).
3. The **Speakers** window opens: for each voice the ▶ buttons play 5 seconds where only that person is talking. Type or
   pick the name and press **Apply names**: names are replaced in the transcript, subtitles and video.
4. Same person → same name; if you don't know, leave it empty. **Later** closes without changing anything (reopen it
   from the *Transcript* tab).

## Good to know

- Wear **headphones**: otherwise the microphone also picks up the other people and the text gets duplicated.
- *Screen area* records the area where the window is when recording starts; *Direct window* follows the window, but
  with Teams and browsers it can produce black video (the preview shows you). The whole screen is the safest choice.
- **Privacy**: the audio is sent to DeepInfra (which states it does not store it on disk or use it for training). For
  work recordings check that this is allowed and let the participants know.

## For developers

```bat
install.bat        :: creates .venv with the dependencies and a Desktop shortcut to the sources
run.bat            :: runs from source (config.json and recordings\ stay in the project folder)
tools\build.bat    :: builds dist\Muninn-Setup.exe and dist\Muninn-Uninstall.exe (PyInstaller)
```

- Code in `muninn\`, installer and uninstaller in `tools\installer\` (standard library only).
- Version: `__version__` in `muninn\__init__.py` (shown in "Installed apps").
- Code, comments and source texts are in English. Translations: `muninn\lang\<language>.json` (key = the English
  text passed to `_()` in the code) and `muninn\lang\guide_<language>.txt`; the English Guide text is in `muninn\guide.py`.
- Icons: SVG in `assets\svg\`; the PNGs and `muninn.ico` are regenerated with `tools\make_icons.py` (uses headless
  Chrome).
- The exes do not belong in the repository (`dist\` is in `.gitignore`).
- `config.json` holds the key in plain text and is in `.gitignore`: keep it there.

**Silent installs**: `Muninn-Setup.exe --quiet [--dir <folder>] [--no-shortcuts] [--no-register]` and
`Muninn-Uninstall.exe --quiet [--delete-settings]`.

## Architecture Blueprint

### System Overview

Muninn is a single-process Windows desktop app (Python 3.10+, tkinter). It
records audio with `pyaudiowpatch` (WASAPI) and video with ffmpeg. ffmpeg comes
from `imageio-ffmpeg`, or from `PATH` if that package is missing. The app sends
audio to DeepInfra Nemotron over HTTPS and WebSocket; no other back end is
involved. A recording goes through these steps:

```text
Session.start ─► AudioSource (WAV per device) + VideoRecorder (ffmpeg)
Session.stop  ─► outputs.mix_audio / mux_video ─► data/session.json
              ─► session.transcribe_folder        (skipped if no audio source)
                   ─► DeepInfraClient (parallel blocks)
                   ─► speakers.link_blocks        (only if diarized audio spans several blocks)
                   ─► speakers.write_placeholders (only if voices were found)
                   ─► session.render_outputs ─► outputs.write_transcripts / write_srt (with and without names) / embed_subtitles
ConfirmDialog.confirm ─► speakers.json
  └► App._open_confirm callback ─► session.render_outputs (only if names changed)
```

Live captions take a separate path, `live.LiveCaptions`, with two transcription
back ends:

- the default `LiveTranscriber`: repeated HTTPS POSTs to
  `Nemotron-3.5-ASR-Streaming-Multilingual-0.6b`;
- "Real time EN" (`RealtimeTranscriber`): a WebSocket stream to
  `Nemo3-ASR-Streaming`.

The text appears in two windows that are hidden from screen capture when
`live_hide` is set (the default).

### Local Interface & Invariants

- [`main.py`](main.py) imports `muninn.app.main` and runs it. When run from
  source, it first puts the repo root on `sys.path`. If startup fails, it
  writes the traceback to `DATA_DIR\error.log` (or to `~\muninn_error.log` if
  `DATA_DIR` is unavailable), shows a Tk error box and re-raises the exception.
- `install.bat` creates `.venv` (it finds Python 3.10+ with tkinter, and
  falls back to `winget`), installs `requirements.txt` and creates a Desktop
  shortcut that runs `pythonw main.py`. `run.bat` starts the app with
  `.venv\Scripts\pythonw.exe`.

### Subdirectories & System Boundaries

- `muninn`: [`./muninn/README.md`](muninn/README.md). The application
  package covers the UI, recording sessions, live captions, DeepInfra
  transcription, speaker naming, outputs and the Win32 helpers. Its `lang/`
  subfolder holds the translations.
- `tools`: [`./tools/README.md`](tools/README.md). It holds the PyInstaller
  build (`build.bat`, `build.py`) and icon generation (`make_icons.py`).
  - `tools/installer`: [`./tools/installer/README.md`](tools/installer/README.md).
    The per-user installer and uninstaller (stdlib only), with the HKCU
    uninstall key and shortcuts.
- `assets`: no code. `svg/` holds the source icons. `tools/make_icons.py`
  generates `png/` and `muninn.ico` from them, and `tools/build.py` bundles
  `png/` and `muninn.ico` (not `svg/`).

### Global Invariants

- `config.json` holds the DeepInfra key in plain text, and it must stay listed
  in `.gitignore`. When the key in it is empty, the app falls back to the
  `DEEPINFRA_API_KEY` environment variable (`muninn/config.py:load`). Only
  `muninn/config.py` and `tools/installer/common.py` (`recordings_dir`) read
  the file.
- When frozen, the app uses `%APPDATA%\Muninn` for settings and
  `Documents\Muninn` for recordings. When run from source, both live in the repo
  folder (`muninn/config.py`). The uninstaller uses the same `%APPDATA%\Muninn`
  (`common.DATA_DIR`); `setup.py` does not.
- The app (`muninn/tray.py`) and the installer (`tools/installer/common.py`)
  share port `127.0.0.1:47613`:
  - a second launch of the app sends `show` there and exits;
  - the installer and uninstaller send `quit` there to close a running app.
- The version has a single source: `__version__` in `muninn/__init__.py`. The
  build writes it into `version.txt` and the installer writes it into the
  registry.
- Translation keys are the English source text passed to `_()`. Every
  `muninn/lang/<code>.json` (`it`, `fr`, `de`, `es`) must use those exact strings
  as keys. English is the default interface language.

### Global Side Effects

- **Network:**
  - The running app talks only to DeepInfra (`api.deepinfra.com`, HTTPS and
    WSS, including the `/v1/me` key check), and the audio leaves the machine.
  - The developer scripts go elsewhere: `install.bat` can run `winget` and
    `pip`, and `tools\build.bat` runs `pip`.
- **Disk:**
  - The app writes `config.json` (including `known_names`), the recording
    folders (with `data/api_responses.jsonl`, `transcript_raw.json` and a
    temporary `_listen.wav`), `live/notary_*.txt` and `error.log`
    (`~\muninn_error.log` if `DATA_DIR` is unavailable).
  - The installed build also writes the install folder (default
    `%LOCALAPPDATA%\Programs\Muninn`, changeable with `--dir` or Browse), an
    HKCU uninstall registry key, and Desktop and Start menu shortcuts.
  - The uninstaller can delete `%APPDATA%\Muninn` (`--delete-settings`).
- **Processes:**
  - ffmpeg subprocesses, used for capture, mixing and muxing, encoding audio
    for upload, and probing and importing files;
  - PowerShell, used for shortcuts;
  - a new copy of the app on restart;
  - `os.startfile`, used to open folders and files;
  - the uninstaller, which relaunches itself from `%TEMP%` and deletes itself.
- **OS integration:**
  - per-monitor DPI awareness and an AppUserModelID;
  - the main window is hidden from screen capture (`hide_main_from_capture`,
    on by default);
  - windows are re-parented so they get no taskbar button;
  - a system tray icon with balloon notifications;
  - a localhost TCP listener;
  - audio playback (`winsound`) and screen grabs for previews.

### System Verification

- There is no automated test suite. Use
  `.venv\Scripts\python.exe main.py --selftest report.json` as a smoke check
  and `tools\build.bat` to build the installer.

## License

[MIT](LICENSE). Muninn uses ffmpeg (via `imageio-ffmpeg`), which is distributed under its own GPL/LGPL
license.
