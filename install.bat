@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
echo.
echo === Muninn setup ===
echo.

set "PY="
where py >nul 2>nul
if %errorlevel%==0 (
  py -3.12 -c "import tkinter" >nul 2>nul && set "PY=py -3.12"
  if not defined PY py -3 -c "import sys, tkinter; assert sys.version_info >= (3, 10)" >nul 2>nul && set "PY=py -3"
)
if not defined PY (
  python -c "import sys, tkinter; assert sys.version_info >= (3, 10)" >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo Python 3.10+ not found. Trying to install it with winget...
  winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
  if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
)
if not defined PY (
  echo.
  echo Cannot find or install Python.
  echo Install Python 3.12 from https://www.python.org/downloads/ and run this file again.
  goto end
)
echo Using: %PY%

if not exist ".venv\Scripts\python.exe" (
  %PY% -m venv .venv || goto error
)
".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto error
".venv\Scripts\python.exe" -c "import imageio_ffmpeg; print('ffmpeg:', imageio_ffmpeg.get_ffmpeg_exe())" || echo Warning: ffmpeg not available, video will not work.

echo Creating the Desktop shortcut...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$d=[Environment]::GetFolderPath('Desktop'); $s=(New-Object -ComObject WScript.Shell).CreateShortcut($d+'\Muninn.lnk'); $s.TargetPath='%~dp0.venv\Scripts\pythonw.exe'; $s.Arguments='main.py'; $s.WorkingDirectory='%~dp0'; $s.IconLocation='%~dp0assets\muninn.ico,0'; $s.Description='Muninn: hears all, remembers all'; $s.Save()" >nul 2>nul

echo.
echo Done! Start the program with run.bat or with the "Muninn" icon on the Desktop.
goto end

:error
echo.
echo Setup failed. If you are on the corporate network, pip may be blocked by the proxy.

:end
echo.
pause
