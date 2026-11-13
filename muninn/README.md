# muninn (application package)

## Overview

The Python package behind the Muninn desktop app, a Windows 10/11 tkinter app. It
records microphone and loopback audio (WASAPI via `pyaudiowpatch`) and
screens or windows (ffmpeg `ddagrab`/`gdigrab`). It transcribes the recordings
with DeepInfra Nvidia Nemotron, splits the call audio by voice, lets the user
put names on the voices, and writes a recording folder (`transcript.md/.json`,
`.srt`, MP4 with two embedded subtitle tracks: with and without speaker names). It also shows live captions and a
"Notaio" log; both windows are hidden from screen capture when `live_hide` is
set (the default).

The repository's [`main.py`](../main.py) starts the app by calling `app.main()`.

| Layer | Modules |
|---|---|
| UI / shell | `app` (main window), `confirm` (naming the voices), `live` (`LiveWindows`), `theme`, `tray`, `guide`, `i18n` |
| Recording | `session` (`Session`), `audio` (`AudioSource`), `video` (`VideoRecorder`), `devtest` (levels, tests, previews) |
| Processing | `transcriber` (`DeepInfraClient`), `speakers`, `outputs`, `dsp` |
| Platform | `winutil` (Win32 via ctypes), `config` |

## Interface

### Entry point and UI

- `app.main()`: handles `--selftest <file>`, exits if `tray.another_instance_running()` is true, otherwise runs `App().run()`.
- `app.App`: the main window, with the tabs Audio, Video, Transcript, Settings and Guide. Key methods:
  - `toggle()`, `start()`, `stop()`: start and stop recording.
  - `toggle_live(realtime_en=False)`, `import_files()`, `retranscribe()`, `run_on_folder(action, status, ask=False)`, `assign_names()`.
  - `refresh_sources()`, `refresh_video()`, `add_window()`, `remove_window()`, `toggle_levels()`, `test_source(row)`.
  - `save_settings(quiet=False)`, `check_key(key)`, `restart()`, `open_folder()`, `on_close()`, `to_tray()`, `show_window()`, `set_status(msg)`.
- `app.selftest(out)`: writes a JSON report on the bundled components: ffmpeg, icons, the language `.json` catalogs and `guide_*.txt` files found, audio devices, pystray, websocket and PIL.
- `confirm.ConfirmDialog(root, folder, known_names, on_done)`: the modal dialog for naming speakers. It plays clips of `CLIP_SEC = 5.0` s per voice. `on_done(changed, used_names)` is called when it closes.
- `theme.set_app_id()`, `icon()`, `logo()`, `set_window_icon()`, `apply(root, k)`, `header()`: the ttk `clam` theme and icons from `assets/png`.
- `tray.Tray(post, labels, is_recording)`, `tray.TaskbarHider(root)`, `tray.another_instance_running()`, `tray.listen_for_instances(on_show, on_quit)`.
- `guide.GUIDE`: the English text of the Guide tab. `i18n.guide()` returns the localized version from `lang/guide_<code>.txt`.
- `i18n.SOURCE_LANG = "en"`, `i18n.LANGS` (`en`, `it`, `fr`, `de`, `es`), `i18n.set_lang(code)`, `i18n._(text, *args, **kw)`: translation keyed by the English source text.

### Recording and sessions

- `session.Session(cfg, sources, monitors, windows, all_monitors, log, on_status)`:
  - `.start()` and `.stop(progress)`. `stop` blocks and returns the folder.
  - `.levels()` and `.elapsed()`.
- Folder operations:
  - `session.import_file(src, cfg, log, progress)`.
  - `session.transcribe_folder(folder, cfg, log, progress)`, which ends by calling `render_outputs`.
  - `session.render_outputs(folder, cfg, log, progress)`.
- Helpers: `session.data_dir`, `meta_path`, `raw_path`, `load_meta`, `save_meta`, `is_recording_folder`, `probe(ffmpeg, path)`, `Timer`, `MEDIA_TYPES`.
- Audio:
  - `audio.list_sources()` lists the devices.
  - `audio.AudioSource(info, label, diarize, wav_path, t0, chunk_sec, on_chunk, log)` records one device to a WAV file.
- Video:
  - `video.find_ffmpeg()` prefers the `imageio_ffmpeg` binary and falls back to `ffmpeg` on `PATH`.
  - `video.VIDEO_CODECS` and `VIDEO_QUALITY` (CRF: high=23, medium=28, low=33, minimum=38).
  - `video.video_settings(cfg)`, `build_monitor_cmd(...)`, `build_gdigrab_cmd(...)`, `VideoRecorder(name, cmds, out_path, log)`.
- Device tests and previews: `devtest.LevelMonitor(sources)`, `test_input(...)`, `test_output(...)`, `grab_preview(target, max_w, max_h)`.

### Live captions

- `live.LiveCaptions(root, cfg, sources, log, on_stopped, realtime_en=False)` connects `LiveSource` → the transcriber → `LiveWindows`. It reads the config keys `live_hide` (default True), `live_font` (17) and `live_language` (`"it"`), all defined in `config.DEFAULTS`.
- `live.LiveTranscriber`: near-real-time HTTPS transcription with partial and final results. Its `cost` comes from the API response (`inference_status.cost`).
- `live.RealtimeTranscriber`: WebSocket streaming, English only. Its `cost` is an estimate: `seconds / 60 × PRICE_PER_MIN` (0.001).
- Both transcribers expose `feed(label, x)`, `note()`, `stop()`, `cost` and `seconds`.
- `live.LiveSource`, `live.LiveWindows(root, save_path, hide, font_size, on_close)`, `live.clean_text(t)`.

### Transcription and outputs

- `transcriber.DeepInfraClient(key, log, ffmpeg, codec, bitrate_kbps)`:
  - `.transcribe_samples(x, sr, timeout=120)`.
  - `.transcribe_diarized(x, sr, timeout=900)`, which returns `None` once diarization is unavailable.
  - `total_cost` and `bytes_sent`.
- Other `transcriber` symbols:
  - Exceptions: `ApiError`, `FormatRejected`, `DiarizeUnavailable`.
  - Functions: `normalize(resp, duration)`, `words_to_sentences(...)`, `encode_args(fmt, kbps=32)`, `encode_audio(ffmpeg, x, sr, fmt, kbps=32)`.
  - Constants: `CODECS`, `MIME`.
- `speakers.assign_ids`, `placeholder`, `write_placeholders`, `load_names`, `display_names`, `link_blocks`, `speaker_stats`, `solo_moments`, `sample_lines`, `voice_period`.
- `outputs.write_srt(segs, path, shift=0.0, names=True)`: `names=False` writes the text without the `Name: ` prefix.
- `outputs.embed_subtitles(ffmpeg, video_path, tracks, log)`: `tracks` is a list of `(srt_path, title)`; the first is the default track.
- `outputs.write_transcripts`, `split_cue`, `wrap`, `fmt_ts`, `speaker_name`, `merge_turns`, `run_ffmpeg`, `mix_audio`, `mux_video`, `embed_subtitles`.
- `dsp.SR = 16000`, `Resampler`, `Chunker`, `split_blocks`, `has_speech`, `speech_regions`, `split_sentences`, `align_text`, `to_mono_float`, `float_to_int16`, `wav_bytes`, `read_wav`.

### Config, platform and version

- `config.FROZEN`, `APP_DIR`, `DATA_DIR`, `DEFAULT_OUTPUT`, `CONFIG_PATH`, `DEFAULTS`, `load()`, `save(cfg)`.
- `winutil.set_dpi_aware`, `top_hwnd`, `exclude_from_capture`, `list_monitors`, `list_windows`, `window_rect`, `is_window`, `is_minimized`, `region_for_window`.
- `__version__ = "1.0.0"` in `__init__.py`. The build reads it, and it is the version shown in Windows "Installed apps".

### Data subfolder

- `lang/` holds the `<code>.json` catalogs (`it`, `fr`, `de`, `es`) and `guide_<code>.txt` for the same languages. English has no catalog: it is the source text. It contains no code, so it has no contract file of its own.

## Invariants

### Threading and state

- **Tk thread only:** worker threads never touch widgets. They post `(kind, value)` to `ui_q`, and `App._poll` applies the messages on the Tk thread every 150 ms. Tray and instance-listener callbacks use the same queue [`app.py:App._poll`, `App._tray_action`]. `LiveCaptions` polls its own queue every 50 ms [`live.py:LiveCaptions._poll`].
- **Recording state machine:** `idle → starting → recording → stopping → idle`. Most actions return early unless the state is `idle` [`app.py:App.toggle`, `refresh_sources`, `import_files`, `run_on_folder`, …].

### Configuration

- **A DeepInfra key is required** before recording, live captions or import. It comes from `config.json`, or from the `DEEPINFRA_API_KEY` environment variable when the setting is empty [`app.py:App._active_key`, `config.py:load`].
- **Paths:** when frozen, settings live in `%APPDATA%\Muninn` and recordings in `Documents\Muninn`. When run from source, both live in the repo root (`config.json`, `recordings\`) [`config.py:FROZEN`, `DATA_DIR`, `DEFAULT_OUTPUT`].
- **Translation keys are the English source text.** A missing translation, or a format error, falls back to the English text, and an unknown language becomes `en` (also the default `ui_language`) [`i18n.py:_`, `set_lang`].

### Audio and session folders

- **Audio format:** files and API requests use 16 kHz mono 16-bit [`dsp.py:SR`].
- **Track alignment:** all tracks are aligned to the session start `t0`. Gaps longer than 0.25 s are filled with silence, and all tracks are padded to the same end [`audio.py:AudioSource._pad_to`, `stop`].
- **Folder name:** `<output_dir>/<YYYY-mm-dd_HH-MM-SS>[_stem]/` [`session.py`: module docstring].
- **Recorded sessions:**
  - The folder root holds `video_<name>.mp4`, `transcript.md/.json` and `speakers.json`.
  - `data/` holds `audio_<label>.wav`, `audio_mix.m4a`, `session.json`, `transcript_raw.json`, `api_responses.jsonl` and the `.srt` files: `transcript.srt`, plus `<video>.srt` and `<video>_no_names.srt` for each video.
  - While recording, the raw video goes to `data/video_<short>_raw.mp4`. On stop it is deleted after a successful mux, or moved to the final path [`session.py:Session.start`, `_start_video`, `stop`, `transcribe_folder`].
- **Imported files:** the audio is `data/audio_file.wav` with the source label `Audio`. A video whose stem already starts with `video` keeps `<stem>.mp4` [`session.py:import_file`].
- **Subtitle tracks:** `render_outputs` embeds two `mov_text` tracks in each video: `Transcript` (with names, default) and `Transcript (no names)`. Existing subtitle tracks are dropped first, so re-rendering replaces them. The session meta records `subtitles`, `subtitles_no_names` and `subtitles_in_video` per video [`session.py:render_outputs`, `outputs.py:embed_subtitles`].
- **Video codec fallback:** recording falls back to H.264 when the chosen video codec does not start [`session.py:Session.start`, `_start_video`].

### Transcription

- **Batch blocks:**
  - Plain sources use blocks of `final_chunk_min` minutes. The default is 8, in `config.DEFAULTS` and as the fallback in `transcribe_folder`. The 2–12 clamp is applied only when the settings are saved from the UI [`app.py:App.save_settings`].
  - Diarized sources use `diarize_block_minutes` (default 60, Nemotron's per-request maximum).
  - Blocks without speech are skipped.
  - Up to `parallel_requests` requests run in parallel. The default is 8 [`config.py:DEFAULTS`].
- **Diarization fallback:** when `transcribe_diarized` returns `None`, the block is transcribed without speaker separation, with a 180 s timeout [`session.py:transcribe_folder`].
- **Block linking** runs only when diarized audio spans more blocks than there are sources, and its errors are swallowed [`session.py:transcribe_folder`].
  - It never merges two voices from the same block.
  - It votes on concatenated solo clips (1.5–6 s each, 3 per voice), and a vote share of at least 0.6 is needed to link [`speakers.py:link_blocks`].
- **Speaker IDs** are `S01`, `S02`… in order of first appearance, and placeholders are `Speaker N` [`speakers.py:assign_ids`, `placeholder`].
- **Display names:** segments whose source label is exactly `Me` are shown under the user's name. A ` (?)` suffix marks segments with `confidence == "low"` [`speakers.py:display_names`].
- **DeepInfra retries:** up to 4 attempts.
  - Network errors back off `2**attempt` s.
  - HTTP 408, 429, 500, 502, 503, 504, 524 and 529 back off `min(30, 2**attempt)` s. Other status codes are not retried [`transcriber.py:DeepInfraClient._post`].
- **Plain path (`_plain`):**
  - A rejected format (400/415/422) drops to the next codec: the chosen one, then flac, then wav. Without ffmpeg only wav is used.
  - 401/403 means an invalid key, and 402 means no credit left [`transcriber.py:_plain`, `_drop_format`].
- **Diarized path (`_diarized`):** 400/401/403/404/422 raise `DiarizeUnavailable`. That turns diarization off for the rest of the client's life, with no format fallback [`transcriber.py:_diarized`, `transcribe_diarized`].
- **Bitrate per client:** each `DeepInfraClient` keeps its own `kbps` (clamped to 8–256) and passes it to `encode_audio`. There is no shared module state [`transcriber.py:DeepInfraClient.__init__`].

### Live captions

- `LiveTranscriber` allows at most one partial request in flight, and none while a final is pending [`live.py:LiveTranscriber._tick`].
- Finals are emitted in `uid` order and get one retry each [`live.py:LiveTranscriber._final`].
- `language` is dropped for good after a 400 or 422 response [`live.py:LiveTranscriber._post`].
- `RealtimeTranscriber` reconnects with a backoff from 1 s up to 15 s [`live.py:RealtimeTranscriber._run`].

### Windows integration

- **Capture exclusion** uses `SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE=0x11)`, which needs Windows 10 2004 or later [`winutil.py:exclude_from_capture`]. It is applied:
  - to the main window when `hide_main_from_capture` is set (the default) [`app.py:App._apply_hide_main`];
  - to the overlay and Notaio windows when `live_hide` is set (the default) [`live.py:LiveWindows`, `LiveCaptions`].
- **Monitor enumeration** uses the first DXGI adapter, so the order matches `ddagrab` `output_idx`. It falls back to GDI `EnumDisplayMonitors` [`winutil.py:list_monitors`].
- **Single instance:** a localhost socket on `127.0.0.1:47613` accepts `show` and `quit`. The installer uses the same port [`tray.py:SINGLE_PORT`, `listen_for_instances`].

### Naming dialog and self-test

- After the user names the voices by hand, `speakers.json` is rewritten with `confirmed_by_hand = True`, and the dialog does not open on its own again [`confirm.py:ConfirmDialog`, `app.py:App._needs_confirm`].
- After the dialog, outputs are re-rendered only if the names changed (`App._open_confirm` callback).
- `known_names` is capped at 300 entries and saved to `config.json` [`app.py:App._open_confirm`].

## Side Effects

- **Network (DeepInfra):**
  - Batch: `POST …/v1/inference/nvidia/NVIDIA-Nemotron-3.5-Transcribe-30B-A4B` [`transcriber.py:DEEPINFRA_URL`].
  - Live: `POST …/v1/inference/nvidia/Nemotron-3.5-ASR-Streaming-Multilingual-0.6b` [`live.py:LIVE_URL`].
  - English streaming: `wss://api.deepinfra.com/v1/realtime?model=nvidia/Nemo3-ASR-Streaming` [`live.py:REALTIME_URL`].
  - Key check and keep-warm: `GET /v1/me` [`app.py:App.check_key`, `live.py:LiveTranscriber._warm`].
- **Local socket:** a TCP server on `127.0.0.1:47613` [`tray.py:listen_for_instances`].
- **Disk:**
  - `config.json`, through `config.save` from `App.save_settings` and from `App._open_confirm` for `known_names`.
  - Session folders and everything in them [`session.py`, `outputs.py`, `speakers.py`].
  - `<output_dir>/live/notary_<stamp>[_EN].txt` [`live.py:LiveWindows`].
  - `data/_listen.wav`, a temporary file [`confirm.py`].
  - The `--selftest` report [`app.py:selftest`].
  - `App.open_folder` creates the output folder.
- **Subprocesses:**
  - ffmpeg for screen capture, mixing, muxing, subtitle embedding, probing and import, and for encoding audio through a pipe [`video.py`, `outputs.py`, `session.py`, `transcriber.py:encode_audio`].
  - A new copy of the app on restart [`app.py:App.restart`].
- **Shell:** `os.startfile` opens folders and files [`app.py:App._poll`, `App.open_folder`, `live.py:LiveWindows._open_file`].
- **Devices:**
  - Opens WASAPI input and loopback streams and plays test audio [`audio.py`, `devtest.py`, `live.py:LiveSource`].
  - Plays the naming clips with `winsound.PlaySound` [`confirm.py:ConfirmDialog.play`].
  - Captures the screen for previews with PIL `ImageGrab` [`devtest.py:grab_preview`].
- **Win32:**
  - DPI awareness and the AppUserModelID [`winutil.py`, `theme.py`].
  - `SetWindowDisplayAffinity` [`winutil.py`].
  - Taskbar-button hiding through the window owner and exstyle [`tray.py:TaskbarHider`].
  - Monitor enumeration (DXGI with a GDI fallback) and window enumeration (`EnumWindows`) [`winutil.py`].
- **Threads:**
  - Daemon threads for recording, transcription, previews and device tests.
  - A `ThreadPoolExecutor` for API calls.
  - The ffmpeg stderr drain, the socket listener and the pystray icon.

## Verification

- There is no automated test suite. As a smoke check of the packaged
  components, run `.venv\Scripts\python.exe main.py --selftest <report.json>`
  from the repo root.
