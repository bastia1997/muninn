@echo off
chcp 65001 >nul
cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" (
  echo Run install.bat first
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m pip install --quiet -r requirements-dev.txt || goto error
".venv\Scripts\python.exe" tools\build.py || goto error
echo.
echo Done: dist\Muninn-Setup.exe and dist\Muninn-Uninstall.exe
pause
exit /b 0

:error
echo.
echo Build failed.
pause
exit /b 1
