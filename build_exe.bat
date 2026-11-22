@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
chcp 65001 >nul
echo === Building Audiobooks exe ===
holos_env\Scripts\python.exe -m pip install pyinstaller
holos_env\Scripts\python.exe -m PyInstaller audiobooks.spec --noconfirm --distpath dist --workpath build
if errorlevel 1 ( echo [ERROR] build failed & goto end )
echo === Copying voice models next to exe ===
holos_env\Scripts\python.exe pack_models.py
echo.
echo === DONE: dist\Аудіокниги\Аудіокниги.exe ===
echo Copy the whole folder dist\Аудіокниги to any Windows PC.
:end
pause
