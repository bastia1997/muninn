"""Settings saved in config.json.

- Started from source (run.bat): config.json and recordings\\ next to the program, as always.
- Installed version (Muninn.exe): settings in %APPDATA%\\Muninn and recordings in Documents\\Muninn,
  so the program can be updated or uninstalled without touching the data."""
import json
import os
import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))


def _documents():
    """The real Documents folder (even if moved to OneDrive)."""
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(260)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0 and buf.value:  # CSIDL_PERSONAL
            return Path(buf.value)
    except Exception:
        pass
    return Path.home() / "Documents"


if FROZEN:
    APP_DIR = Path(sys.executable).resolve().parent
    DATA_DIR = Path(os.environ.get("APPDATA") or Path.home()) / "Muninn"
    DEFAULT_OUTPUT = _documents() / "Muninn"
else:
    APP_DIR = Path(__file__).resolve().parent.parent
    DATA_DIR = APP_DIR
    DEFAULT_OUTPUT = APP_DIR / "recordings"
CONFIG_PATH = DATA_DIR / "config.json"

DEFAULTS = {
    "ui_language": "en",             # interface language: en | it | fr | de | es
    "final_chunk_min": 8,
    "confirm_names": True,
    "audio_codec": "opus",           # opus | mp3 | flac | wav: format of the audio sent to Nemotron
    "audio_bitrate_kbps": 32,        # for opus and mp3
    "known_names": [],               # names already assigned, offered in the Speakers window
    "deepinfra_key": "",
    "diarize_block_minutes": 60,     # call audio per request (Nemotron max: 1 hour)
    "parallel_requests": 8,          # parallel requests to DeepInfra during transcription
    "live_hide": True,               # subtitles and Notary hidden from screen sharing
    "live_font": 17,                 # subtitle text size
    "live_language": "it",           # language passed to the live subtitle model
    "user_name": "",
    "video_fps": 10,
    "video_codec": "h264",           # h264 | h264_qsv | h265 | hevc_qsv
    "video_quality": "medium",       # high | medium | low | minimum
    "video_max_height": 0,           # 0 = original resolution, otherwise 1080 / 720
    "recompress_imported": False,    # recompress the videos of processed files
    "output_dir": str(DEFAULT_OUTPUT),
    "hide_main_from_capture": True,
    "source_labels": {},     # device name -> label (e.g. "Me", "Call")
    "source_diarize": {},    # device name -> True/False
    "selected_sources": [],  # names of the devices selected last time
}


def load():
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except Exception:
            pass
    if not cfg.get("deepinfra_key"):
        cfg["deepinfra_key"] = os.environ.get("DEEPINFRA_API_KEY", "")
    return cfg


def save(cfg):
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
