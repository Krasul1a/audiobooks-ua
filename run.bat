@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
chcp 65001 >nul
if not exist holos_env\Scripts\activate.bat (
  echo [ERROR] holos_env not found in %CD%
  echo Run install.bat first, in this same folder.
  pause
  exit /b
)
call holos_env\Scripts\activate.bat
echo Environment is ready. GUI: gui.bat   Console examples:
echo   python audiobook.py book.epub
echo   python audiobook.py book.fb2 --lang ru --engine silero --voice xenia
echo   python audiobook.py book.pdf --format m4b
echo   python audiobook.py --convert "folder with wav" --format m4b --cover cover.jpg
echo   python audiobook.py --help
cmd /k
