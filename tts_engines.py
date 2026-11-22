"""
Рушії озвучки.

Усі рушії - нейромережеві (ШІ), працюють локально на процесорі, без інтернету.

  holos  - HolosTTS, українська
  kokoro - Kokoro-RU (StyleTTS2, як і Holos), російська, ONNX (~330 МБ один раз)
  silero - Silero TTS v5, російська (модель завантажується один раз у папку models)

Кожен рушій: .sr (частота), .max_len / .group (розмір шматків тексту),
             .say(text) -> numpy float32
"""

import html
import os
import re
import subprocess
import unicodedata

import numpy as np

import app_paths

ENGINES = {
    "holos": {"name": "HolosTTS (офлайн)", "langs": ("uk",)},
    "kokoro": {"name": "Kokoro-RU (офлайн)", "langs": ("ru",)},
    "silero": {"name": "Silero v5 (офлайн)", "langs": ("ru",)},
}

SILERO_VOICES = ["xenia", "baya", "kseniya", "aidar", "eugene"]
KOKORO_VOICES = ["dima", "sveta", "masha"]
DEFAULT_ENGINE = {"uk": "holos", "ru": "kokoro"}

HERE = app_paths.APP_DIR
HOLOS_VOICES_CACHE = os.path.join(HERE, "holos_voices.txt")


def engines_for(lang):
    return [k for k, v in ENGINES.items() if lang in v["langs"]]


def static_voices(engine, lang):
    """Голоси без завантаження моделі (для holos - з кешу, якщо є)."""
    if engine == "silero":
        return list(SILERO_VOICES)
    if engine == "kokoro":
        return list(KOKORO_VOICES)
    if engine == "holos" and os.path.exists(HOLOS_VOICES_CACHE):
        return [l.strip() for l in open(HOLOS_VOICES_CACHE, encoding="utf-8") if l.strip()]
    return []


def holos_list_voices():
    from holos.models import HolosTTS
    w, v = app_paths.holos_weights()
    m = HolosTTS(w, device="cpu", voices_path=v)
    voices = [str(v) for v in m.list_voices()]
    with open(HOLOS_VOICES_CACHE, "w", encoding="utf-8") as f:
        f.write("\n".join(voices))
    return voices


# ----------------------------------------------------------------------------
# Підготовка тексту
# ----------------------------------------------------------------------------

try:
    from num2words import num2words
except ImportError:
    num2words = None

VOWELS = "аеєиіїоуюяыэё"


def numbers_to_words(text, lang):
    if num2words is None:
        return text

    def repl(m):
        try:
            return num2words(int(m.group(0)), lang=lang)
        except Exception:
            return m.group(0)
    return re.sub(r"\d+", repl, text)


def base_clean(t):
    t = html.unescape(t)
    t = re.sub(r'["«»„“”]', "", t)
    t = unicodedata.normalize("NFKC", t)
    t = re.sub(r"[‒–—―−]", "-", t)
    t = re.sub(r"^\s*-\s*", "", t)               # тире прямої мови на початку
    t = re.sub(r"[*_#~<>\[\]{}|]", " ", t)
    t = " ".join(t.split())
    if t and t[-1] not in ".?!:-…":
        t += "."
    return t


def has_letters(t):
    return bool(re.search(r"[^\W\d_]", t))


def split_sentences(paragraph, max_len=280, group=100):
    """Ділить абзац на шматки; короткі речення групує до ~group символів."""
    parts = _split_raw(paragraph, max_len)
    out = []
    for p in parts:
        if out and len(out[-1]) <= group and len(out[-1]) + len(p) < max_len:
            out[-1] += " " + p
        else:
            out.append(p)
    return out


def _split_raw(paragraph, max_len):
    parts = re.split(r"(?<=[.!?…])\s+", paragraph)
    out = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        while len(p) > max_len:            # довге речення ріжемо по комах
            cut = max(p.rfind(",", 0, max_len), p.rfind(";", 0, max_len),
                      p.rfind(" ", 0, max_len))
            if cut <= 0:
                cut = max_len
            out.append(p[:cut + 1].strip())
            p = p[cut + 1:].strip()
        if p:
            out.append(p)
    merged = []
    for s in out:
        if merged and len(s) < 15 and len(merged[-1]) + len(s) < max_len:
            merged[-1] += " " + s
        else:
            merged.append(s)
    return merged


def empty():
    return np.zeros(0, dtype="float32")


# ----------------------------------------------------------------------------
# HolosTTS (українська)
# ----------------------------------------------------------------------------

class HolosEngine:
    sr = 24000
    max_len, group = 280, 100

    def __init__(self, voice=None, clone=None, speed=1.0, log=print):
        import torch
        from holos.models import HolosTTS
        from ipa_uk import ipa
        from ukrainian_word_stress import Stressifier

        self.torch = torch
        self.ipa = ipa
        self.stressify = Stressifier()
        self.speed = speed
        device = "cpu"
        torch.set_num_threads(max(1, os.cpu_count() or 1))
        log(f"Процесор: {torch.get_num_threads()} потоків. Завантажую HolosTTS…")
        w, v = app_paths.holos_weights()
        self.model = HolosTTS(w, device=device, voices_path=v)

        if clone and clone.lower().endswith(".pt"):
            self.voice = torch.load(clone, map_location="cpu")
            log(f"Голос завантажено з: {clone}")
        elif clone:
            from holos.models import VoiceEncoder
            enc = VoiceEncoder("patriotyk/HolosTTS", device=device)
            with torch.inference_mode():
                if hasattr(enc, "encode"):
                    v = enc.encode(clone).squeeze(0).cpu()
                else:
                    v = enc(clone)
            self.voice = v
            saved = os.path.splitext(clone)[0] + "_voice.pt"
            torch.save(v, saved)
            log(f"Голос клоновано з: {clone} (збережено в {saved})")
        else:
            voices = self.model.list_voices()
            try:
                with open(HOLOS_VOICES_CACHE, "w", encoding="utf-8") as f:
                    f.write("\n".join(map(str, voices)))
            except OSError:
                pass
            names = [str(v) for v in voices]
            if not voice:
                self.voice = voices[0]
            elif voice in voices:
                self.voice = voice
            elif voice in names:
                self.voice = voices[names.index(voice)]
            else:
                raise ValueError(f"Голос '{voice}' не знайдено. Доступні: {', '.join(names)}")
            log(f"Голос: {self.voice}")

    def say(self, sentence):
        t = base_clean(sentence)
        t = t.replace("+", "́")             # ручний наголос: "за+мок"
        t = numbers_to_words(t, "uk")
        t = t.replace(" - ", ": ")
        if not has_letters(t):
            return empty()
        phonemes = self.ipa(self.stressify(t))
        if not phonemes or not phonemes.strip():
            return empty()
        with self.torch.inference_mode():
            audio = self.model.generate(phonemes, self.voice, speed=self.speed)
        return audio.squeeze().float().cpu().numpy()


# ----------------------------------------------------------------------------
# Silero (російська)
# ----------------------------------------------------------------------------

class SileroEngine:
    sr = 48000
    max_len, group = 800, 300
    MODEL = "v5_6_ru"      # автонаголоси, омографи, питання, оклики
    MODEL_URL = f"https://models.silero.ai/models/tts/ru/{MODEL}.pt"

    def __init__(self, voice=None, speed=1.0, log=print):
        import torch
        self.torch = torch
        torch.set_num_threads(max(1, os.cpu_count() or 1))
        self.voice = voice if voice in SILERO_VOICES else SILERO_VOICES[0]
        self.speed = speed
        models_dir = os.path.join(HERE, "models")
        os.makedirs(models_dir, exist_ok=True)
        path = os.path.join(models_dir, f"silero_{self.MODEL}.pt")
        alt = os.path.join(models_dir, f"{self.MODEL}.pt")     # як файл зветься на сайті
        if not os.path.exists(path) and os.path.exists(alt):
            path = alt
        if not os.path.exists(path):
            log(f"Завантажую модель Silero {self.MODEL} (лише перший раз)…")
            try:
                torch.hub.download_url_to_file(self.MODEL_URL, path + ".part", progress=False)
            except Exception as e:
                raise RuntimeError(
                    f"Не вдалося завантажити модель Silero з models.silero.ai ({e}).\n"
                    "Сайт може бути заблокований у вашій мережі. Увімкніть VPN на час "
                    "першого завантаження, або скачайте вручну\n"
                    f"{self.MODEL_URL}\nі покладіть файл сюди:\n{path}") from None
            os.replace(path + ".part", path)
        log("Завантажую Silero…")
        self.model = torch.package.PackageImporter(path).load_pickle("tts_models", "model")
        self.model.to(torch.device("cpu"))
        log(f"Голос: {self.voice}")

    def say(self, sentence):
        t = base_clean(sentence)
        t = re.sub(f"([{VOWELS}{VOWELS.upper()}])\\+", r"+\1", t)   # "за+мок" -> "з+амок"
        t = numbers_to_words(t, "ru")
        t = re.sub(r"[^\w\s.,!?…:;+()'’-]", " ", t)
        t = " ".join(t.split())
        if not re.search(r"[а-яёА-ЯЁ]", t):
            return empty()
        opts = dict(put_accent=True, put_yo=True, put_stress_homo=True, put_yo_homo=True)
        with self.torch.inference_mode():
            try:
                audio = self.model.apply_tts(text=t, speaker=self.voice, sample_rate=self.sr,
                                             **opts)
            except TypeError:          # старіші моделі без параметрів омографів
                audio = self.model.apply_tts(text=t, speaker=self.voice, sample_rate=self.sr,
                                             put_accent=True, put_yo=True)
        audio = audio.float().cpu().numpy()
        if abs(self.speed - 1.0) > 0.01:
            audio = change_speed(audio, self.speed, self.sr)
        return audio


def change_speed(audio, speed, sr):
    """Зміна темпу без зміни висоти голосу (через ffmpeg atempo)."""
    from audio_out import ffmpeg_exe
    ff = ffmpeg_exe()
    if not ff:
        return audio
    p = subprocess.run([ff, "-loglevel", "error", "-f", "f32le", "-ar", str(sr), "-ac", "1",
                        "-i", "pipe:0", "-filter:a", f"atempo={speed:.3f}",
                        "-f", "f32le", "pipe:1"],
                       input=audio.astype("float32").tobytes(), capture_output=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if p.returncode != 0 or not p.stdout:
        return audio
    return np.frombuffer(p.stdout, dtype="float32").copy()


# ----------------------------------------------------------------------------
# Kokoro-RU (російська) - https://huggingface.co/zaakirio/kokoro-ru
# ----------------------------------------------------------------------------

class KokoroEngine:
    sr = 24000
    max_len, group = 220, 120          # модель приймає до 510 фонем за раз
    REPO = "zaakirio/kokoro-ru"

    def __init__(self, voice=None, speed=1.0, log=print):
        import importlib.util
        import json
        import onnxruntime as ort
        from huggingface_hub import snapshot_download

        self.voice = voice if voice in KOKORO_VOICES else KOKORO_VOICES[0]
        self.speed = float(speed)
        onnx_file = "onnx/model_dima.onnx" if self.voice == "dima" else "onnx/model.onnx"
        repo = os.path.join(HERE, "models", "kokoro-ru")
        need = ["ru_g2p.py", "kokoro-config.json", "config.json", "espeak-data/ru_dict",
                f"voices/{self.voice}.bin", onnx_file]
        if not all(os.path.exists(os.path.join(repo, f)) for f in need):
            log(f"Завантажую Kokoro-RU ({self.voice}, лише перший раз ~330 МБ)…")
            snapshot_download(
                self.REPO, local_dir=repo,
                allow_patterns=["ru_g2p.py", "kokoro-config.json", "config.json",
                                "espeak-data/*", "espeak-data/**", f"voices/{self.voice}.bin",
                                onnx_file])

        # espeak-ng з пакета espeakng-loader (окремо встановлювати не треба)
        import espeakng_loader
        from phonemizer.backend.espeak.wrapper import EspeakWrapper
        EspeakWrapper.set_library(espeakng_loader.get_library_path())

        spec = importlib.util.spec_from_file_location("ru_g2p", os.path.join(repo, "ru_g2p.py"))
        ru_g2p = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ru_g2p)
        log("Завантажую наголоси (RUAccent)…")
        self.g2p = ru_g2p.RuG2P()
        # misaki з PyPI повертає рядок, а ru_g2p чекає (фонеми, токени)
        esp = self.g2p._espeak
        self.g2p._espeak = lambda text: (lambda r: r if isinstance(r, tuple) else (r, None))(esp(text))

        cfg = json.load(open(os.path.join(repo, "config.json"), encoding="utf-8"))
        self.vocab = cfg["vocab"]
        self.style = np.fromfile(os.path.join(repo, "voices", f"{self.voice}.bin"),
                                 dtype=np.float32).reshape(-1, 256)
        so = ort.SessionOptions()
        so.intra_op_num_threads = max(1, os.cpu_count() or 1)
        self.sess = ort.InferenceSession(os.path.join(repo, onnx_file), so,
                                         providers=["CPUExecutionProvider"])
        log(f"Процесор: {so.intra_op_num_threads} потоків. Голос: {self.voice}")

    def _run(self, ipa):
        ids = [self.vocab[c] for c in ipa if c in self.vocab]
        if not ids:
            return empty()
        style = self.style[min(len(ids), len(self.style)) - 1][None]
        audio = self.sess.run(None, dict(
            input_ids=np.array([[0, *ids, 0]], np.int64),
            style=style.astype(np.float32),
            speed=np.array([self.speed], np.float32)))[0]
        return np.asarray(audio, dtype="float32").reshape(-1)

    def say(self, sentence):
        t = base_clean(sentence).replace("+", "")
        t = numbers_to_words(t, "ru")
        t = " ".join(t.split())
        if not re.search(r"[а-яёА-ЯЁ]", t):
            return empty()
        ipa, _oov = self.g2p(t)
        if len(ipa) <= 500:
            return self._run(ipa)
        # задовга фраза - ділимо фонеми по пробілах
        parts, cur = [], ""
        for w in ipa.split(" "):
            if cur and len(cur) + len(w) + 1 > 400:
                parts.append(cur)
                cur = w
            else:
                cur = f"{cur} {w}".strip()
        if cur:
            parts.append(cur)
        return np.concatenate([self._run(p) for p in parts])


def make_engine(engine, lang, voice=None, clone=None, speed=1.0, log=print):
    if engine == "kokoro":
        return KokoroEngine(voice, speed, log=log)
    if engine == "holos":
        return HolosEngine(voice, clone, speed, log=log)
    if engine == "silero":
        return SileroEngine(voice, speed, log=log)
    raise ValueError(f"Невідомий рушій: {engine}")
