"""Video recording of screens and windows with ffmpeg."""
import shutil
import subprocess
import sys
import threading
import time

from .i18n import _

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def find_ffmpeg():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass
    return shutil.which("ffmpeg")


VIDEO_CODECS = {  # key → (display name, ffmpeg encoder)
    "h264": (_("H.264 (plays everywhere)"), "libx264"),
    "h264_qsv": (_("H.264 Intel Quick Sync (less CPU)"), "h264_qsv"),
    "h265": (_("H.265 / HEVC (smaller files)"), "libx265"),
    "hevc_qsv": (_("H.265 Intel Quick Sync (small and light)"), "hevc_qsv"),
}
VIDEO_QUALITY = {"high": 23, "medium": 28, "low": 33, "minimum": 38}


def video_settings(cfg):
    return {"codec": cfg.get("video_codec", "h264"), "quality": cfg.get("video_quality", "medium"),
            "max_height": int(cfg.get("video_max_height", 0) or 0)}


def _encode_args(fps, enc, live=True):
    """Encoding arguments. enc: {codec, quality, max_height} (or a number = H.264 CRF, for compatibility)."""
    if not isinstance(enc, dict):
        enc = {"codec": "h264", "quality": int(enc), "max_height": 0}
    q = enc["quality"] if isinstance(enc["quality"], int) else VIDEO_QUALITY.get(enc["quality"], 28)
    h = enc.get("max_height") or 0
    scale = (f"scale=-2:'trunc(min(ih,{h})/2)*2'" if h else "scale=trunc(iw/2)*2:trunc(ih/2)*2")
    codec = enc.get("codec", "h264")
    if codec in ("h264_qsv", "hevc_qsv"):
        args = ["-vf", scale + ",format=nv12", "-c:v", codec, "-preset", "veryfast", "-global_quality", str(q)]
    elif codec == "h265":
        args = ["-vf", scale, "-c:v", "libx265", "-preset", "ultrafast" if live else "fast", "-crf", str(q),
                "-pix_fmt", "yuv420p", "-x265-params", "log-level=error"]
    else:
        args = ["-vf", scale, "-c:v", "libx264", "-preset", "ultrafast" if live else "veryfast", "-crf", str(q),
                "-pix_fmt", "yuv420p"]
    if codec in ("h265", "hevc_qsv"):
        args += ["-tag:v", "hvc1"]  # so the HEVC MP4 also opens on Apple/Windows players
    if live:  # recording: fixed fps and a readable file even if the program closes badly
        args += ["-r", str(fps), "-g", str(fps * 2), "-movflags", "+frag_keyframe+empty_moov+default_base_moof"]
    return args


def build_monitor_cmd(ffmpeg, mon, out_path, fps=10, enc=30, region=None):
    """Captures a screen (or an area of it) with Desktop Duplication (ddagrab)."""
    src = f"ddagrab=output_idx={mon['index']}:framerate={fps}:draw_mouse=1"
    if region:
        ox, oy, w, h = region
        src += f":offset_x={ox}:offset_y={oy}:video_size={w}x{h}"
    src += ",hwdownload,format=bgra"
    return [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", src,
            *_encode_args(fps, enc), str(out_path)]


def build_gdigrab_cmd(ffmpeg, out_path, fps=10, enc=30, title=None, rect=None):
    """GDI alternative: window by title (even if covered) or desktop area."""
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "gdigrab", "-framerate", str(fps), "-draw_mouse", "1"]
    if rect:
        x, y, w, h = rect
        cmd += ["-offset_x", str(x), "-offset_y", str(y), "-video_size", f"{w}x{h}", "-i", "desktop"]
    else:
        cmd += ["-i", f"title={title}"]
    return cmd + _encode_args(fps, enc) + [str(out_path)]


class VideoRecorder:
    def __init__(self, name, cmds, out_path, log=print):
        """cmds: list of alternative commands; the first one that starts is used."""
        self.name = name
        self.cmds = cmds
        self.out_path = out_path
        self.log = log
        self.proc = None
        self.start_time = None
        self.errors = []

    def start(self):
        for k, cmd in enumerate(self.cmds):
            try:
                p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.PIPE, creationflags=CREATE_NO_WINDOW)
            except OSError as e:
                self.errors.append(str(e))
                continue
            t = time.time()
            time.sleep(1.5)
            if p.poll() is None:
                self.proc, self.start_time, self.used = p, t, k
                threading.Thread(target=self._drain, daemon=True).start()
                return True
            err = p.stderr.read().decode("utf-8", "replace").strip()
            self.errors.append(err[-400:])
        self.log(_("Video '{0}' not started: {1}", self.name, ' | '.join(self.errors)[-600:]))
        return False

    def _drain(self):
        # drains stderr so ffmpeg does not block; keeps the last lines for diagnostics
        for line in iter(self.proc.stderr.readline, b""):
            msg = line.decode("utf-8", "replace").strip()
            if msg:
                self.errors.append(msg)
                self.errors = self.errors[-20:]

    def stop(self):
        if not self.proc:
            return
        try:
            self.proc.stdin.write(b"q")
            self.proc.stdin.flush()
            self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
