"""Builds dist\\Muninn-Setup.exe and dist\\Muninn-Uninstall.exe.

    tools\\build.bat        (or: .venv\\Scripts\\python.exe tools\\build.py)

1. packages the app with PyInstaller (folder with Muninn.exe and bundled Python);
2. compresses it into payload.zip;
3. builds the uninstaller (a single exe);
4. builds the installer (a single exe containing payload.zip and the uninstaller).
Only needed to publish a new version: users download the two exes."""
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
DIST = ROOT / "dist"
INST = ROOT / "tools" / "installer"
ICO = ROOT / "assets" / "muninn.ico"
SEP = ";"  # --add-data separator on Windows


def version():
    for line in (ROOT / "muninn" / "__init__.py").read_text(encoding="utf-8").splitlines():
        if line.startswith("__version__"):
            return line.split("=")[1].strip().strip('"\'')
    return "0.0.0"


def pyinstaller(*args):
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--log-level", "WARN",
           "--workpath", str(BUILD / "work"), "--specpath", str(BUILD / "spec"), *args]
    print(">", " ".join(str(a) for a in cmd[3:]))
    subprocess.run(cmd, check=True, cwd=ROOT)


def main():
    ver = version()
    print(f"Muninn {ver}")
    shutil.rmtree(BUILD, ignore_errors=True)
    DIST.mkdir(exist_ok=True)
    BUILD.mkdir()

    # 1. app
    pyinstaller("--name", "Muninn", "--windowed", "--icon", str(ICO), "--distpath", str(BUILD / "app"),
                "--add-data", f"{ROOT / 'assets' / 'png'}{SEP}assets/png",
                "--add-data", f"{ICO}{SEP}assets",
                "--add-data", f"{ROOT / 'muninn' / 'lang'}{SEP}muninn/lang",
                "--collect-data", "imageio_ffmpeg", "--collect-binaries", "imageio_ffmpeg",
                "--hidden-import", "pystray._win32",
                str(ROOT / "main.py"))
    app_dir = BUILD / "app" / "Muninn"
    assert (app_dir / "Muninn.exe").exists()

    # 2. payload
    payload = BUILD / "payload.zip"
    with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for f in sorted(app_dir.rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(app_dir))
    (BUILD / "version.txt").write_text(ver, encoding="utf-8")
    print(f"payload: {payload.stat().st_size / 1e6:.0f} MB")

    common = ["--onefile", "--windowed", "--icon", str(ICO), "--paths", str(INST), "--distpath", str(BUILD / "exe"),
              "--add-data", f"{ICO}{SEP}.", "--add-data", f"{ROOT / 'assets' / 'png' / 'muninn_64.png'}{SEP}."]
    # 3. uninstaller
    pyinstaller("--name", "Muninn-Uninstall", *common, str(INST / "uninstall.py"))
    # 4. installer
    pyinstaller("--name", "Muninn-Setup", *common,
                "--add-data", f"{payload}{SEP}.", "--add-data", f"{BUILD / 'version.txt'}{SEP}.",
                "--add-data", f"{BUILD / 'exe' / 'Muninn-Uninstall.exe'}{SEP}.",
                str(INST / "setup.py"))

    for name in ("Muninn-Setup.exe", "Muninn-Uninstall.exe"):
        shutil.copy2(BUILD / "exe" / name, DIST / name)
        print(f"dist\\{name}: {(DIST / name).stat().st_size / 1e6:.0f} MB")


if __name__ == "__main__":
    main()
