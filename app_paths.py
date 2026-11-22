"""
Шляхи програми. Працює і зі скриптів, і зі зібраного exe (PyInstaller).

  APP_DIR   - папка, куди можна писати: моделі, налаштування, журнал
              (поруч із exe або поруч зі скриптами)
  RES_DIR   - вбудовані ресурси (іконка тощо)
  MODELS    - APP_DIR/models
"""

import os
import sys

FROZEN = getattr(sys, "frozen", False)
HERE = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(os.path.abspath(sys.executable)) if FROZEN else HERE
RES_DIR = getattr(sys, "_MEIPASS", HERE)
MODELS = os.path.join(APP_DIR, "models")


def model_path(*parts):
    return os.path.join(MODELS, *parts)


def holos_weights():
    """(weights, voices) - локальні файли з models/holos, якщо є, інакше Hugging Face."""
    w, v = model_path("holos", "holos.pt"), model_path("holos", "voices.pt")
    if os.path.isfile(w) and os.path.isfile(v):
        return w, v
    return "patriotyk/HolosTTS", "voices.pt"


def _setup():
    # у віконному exe немає консолі: print() і прогрес-бари мають кудись писати
    if FROZEN and (sys.stdout is None or sys.stderr is None):
        try:
            f = open(os.path.join(APP_DIR, "журнал.txt"), "a", encoding="utf-8", buffering=1)
        except OSError:
            f = open(os.devnull, "w")
        sys.stdout = sys.stdout or f
        sys.stderr = sys.stderr or f
    # моделі stanza (наголоси для української) - з папки програми, якщо є
    stanza_dir = model_path("stanza")
    if os.path.isdir(stanza_dir):
        os.environ.setdefault("STANZA_RESOURCES_DIR", stanza_dir)
    if FROZEN:
        # усе, що доведеться докачати, - теж у папку програми
        os.environ.setdefault("HF_HOME", model_path("hf"))
        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    os.environ.setdefault("PYTHONUTF8", "1")


_setup()
