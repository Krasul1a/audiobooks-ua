@echo off
cd /d "%~dp0"
echo === Installing audiobook generator (Ukrainian + Russian) ===
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python not found. Install Python 3.11 from python.org
  echo         and tick "Add python.exe to PATH" during install.
  goto end
)
where git >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Git not found. Install Git for Windows: https://git-scm.com/download/win
  goto end
)
python --version

if not exist holos_env\Scripts\python.exe (
  echo Creating virtual environment...
  python -m venv holos_env
  if errorlevel 1 ( echo [ERROR] Could not create venv & goto end )
)

set PY=holos_env\Scripts\python.exe
%PY% -m pip install --upgrade pip
%PY% -m pip install torch --index-url https://download.pytorch.org/whl/cpu
if errorlevel 1 ( echo [ERROR] torch install failed & goto end )
%PY% -m pip install soundfile numpy num2words
%PY% -m pip install imageio-ffmpeg mobi pymupdf pillow customtkinter onnxruntime ruaccent misaki phonemizer-fork espeakng-loader
if errorlevel 1 ( echo [ERROR] extra modules install failed & goto end )
%PY% -m pip install git+https://github.com/patriotyk/ukrainian-word-stress.git git+https://github.com/patriotyk/ipa-uk.git
if errorlevel 1 ( echo [ERROR] stress/ipa install failed & goto end )
%PY% -m pip install "holos @ git+https://github.com/patriotyk/HolosTTS.git"
if errorlevel 1 %PY% -m pip install holos-tts
%PY% -c "import holos, ipa_uk, ukrainian_word_stress, soundfile, imageio_ffmpeg, mobi, pymupdf, PIL, customtkinter, onnxruntime, ruaccent, misaki, espeakng_loader; print('OK: all modules installed')"
if errorlevel 1 ( echo [ERROR] Some modules are missing, see messages above & goto end )

echo.
echo === DONE. Start the program with gui.bat ===
:end
echo.
pause
