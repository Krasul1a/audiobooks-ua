@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
if not exist holos_env\Scripts\pythonw.exe (
  echo [ERROR] holos_env not found. Run install.bat first.
  pause
  exit /b
)
start "" holos_env\Scripts\pythonw.exe gui.py %*
