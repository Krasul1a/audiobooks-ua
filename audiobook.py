#!/usr/bin/env python3
"""
Аудіокниги українською та російською (консоль). Графічний режим: gui.py

Книги: .txt .fb2 .fb2.zip .epub .mobi .azw3 .pdf
Результат: M4B (одна книга з розділами й обкладинкою), M4A, MP3 або WAV.
Можна переривати: при повторному запуску готові розділи пропускаються.

Приклади:
  python audiobook.py книга.epub                       (мова визначиться сама)
  python audiobook.py книга.fb2 --lang ru --engine silero --voice xenia
  python audiobook.py книга.epub --format mp3 --bitrate 96 --cover cover.jpg
  python audiobook.py --convert "папка з wav" --format m4b       (лише стиснути)
  python audiobook.py --list-voices --engine holos
  python audiobook.py --test "Привет, это проверка." --lang ru

Наголос вручну: "+" ПІСЛЯ наголошеної голосної: "за+мок", "замо+к".
"""

import argparse
import os
import shutil
import sys
import time
import warnings
from dataclasses import dataclass, field

warnings.filterwarnings("ignore", category=SyntaxWarning)
warnings.filterwarnings("ignore", message=".*TypedStorage is deprecated.*")

import numpy as np

import app_paths  # noqa: F401
import audio_out as ao
import book_reader as br
import tts_engines as te


@dataclass
class Job:
    book: str
    lang: str = "auto"               # auto | uk | ru
    engine: str = "auto"             # auto | holos | silero
    voice: str = None
    clone: str = None
    speed: float = 1.0
    fmt: str = "m4b"                 # m4b | m4a | mp3 | wav
    bitrate: int = 64
    cover: str = None                # шлях до картинки; None = з книги
    no_cover: bool = False
    out: str = None
    only: list = None                # номери розділів (з 1)
    pause: float = 0.15
    para_pause: float = 0.7
    title: str = None
    author: str = None
    keep_parts: bool = False


@dataclass
class Callbacks:
    log: callable = print
    progress: callable = lambda frac, text: None     # 0..1
    should_stop: callable = lambda: False
    chapter_done: callable = lambda idx: None


def silence(sec, sr):
    return np.zeros(int(sr * sec), dtype="float32")


def fmt_time(sec):
    sec = int(sec)
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def parse_range(s, n):
    if not s:
        return list(range(1, n + 1))
    out = set()
    for part in str(s).split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-")
            out.update(range(int(a), int(b) + 1))
        else:
            out.add(int(part))
    return sorted(i for i in out if 1 <= i <= n)


def resolve_lang_engine(job, book):
    lang = book.lang if job.lang == "auto" else job.lang
    engine = te.DEFAULT_ENGINE[lang] if job.engine == "auto" else job.engine
    if lang not in te.ENGINES[engine]["langs"]:
        raise ValueError(f"Рушій {engine} не підтримує мову {lang}. "
                         f"Для цієї мови: {', '.join(te.engines_for(lang))}")
    return lang, engine


def synth_text(engine, text, pause, para_pause, cb, base_frac=0.0, span=1.0, label=""):
    chunks = []
    paragraphs = [p for p in text.split("\n") if p.strip()]
    sents = [te.split_sentences(p, engine.max_len, engine.group) for p in paragraphs]
    n = sum(len(s) for s in sents) or 1
    k = 0
    for ps in sents:
        for s in ps:
            if cb.should_stop():
                raise InterruptedError
            k += 1
            try:
                chunks += [engine.say(s), silence(pause, engine.sr)]
            except InterruptedError:
                raise
            except Exception as e:
                cb.log(f"  ! пропущено шматок через помилку: {e}\n    {s[:100]}")
            if k % 5 == 0 or k == n:
                cb.progress(base_frac + span * k / n, f"{label} шматок {k}/{n}")
        chunks.append(silence(para_pause, engine.sr))
    audio = np.concatenate(chunks) if chunks else silence(1, engine.sr)
    peak = float(np.abs(audio).max()) if audio.size else 0.0
    if peak > 0:
        audio = audio / peak * 0.9           # однакова гучність розділів
    return audio


def make_audiobook(job: Job, cb: Callbacks = None, engine_factory=None):
    cb = cb or Callbacks()
    engine_factory = engine_factory or te.make_engine
    book = br.read_book(job.book)
    if not book.chapters:
        raise ValueError("Не вдалося знайти текст у книзі.")
    lang, engine_name = resolve_lang_engine(job, book)
    title = job.title or book.title
    author = job.author or book.author
    cb.log(f"Книга: {title}" + (f" — {author}" if author else "")
           + f". Мова: {lang}. Розділів: {len(book.chapters)}")

    if not ao.ffmpeg_exe() and job.fmt != "wav":
        cb.log("! ffmpeg не знайдено - зберігатиму WAV.")
        job.fmt = "wav"

    out_dir = job.out or os.path.join(os.path.dirname(os.path.abspath(job.book)),
                                      ao.safe_filename(title) + " - аудіо")
    os.makedirs(out_dir, exist_ok=True)

    # --- обкладинка
    cover_jpg = None
    if not job.no_cover:
        data = open(job.cover, "rb").read() if job.cover else book.cover
        cover_jpg = ao.prepare_cover(data, os.path.join(out_dir, "cover.jpg"))
        cb.log("Обкладинка: " + ("є" if cover_jpg else "немає"))

    selected = job.only or list(range(1, len(book.chapters) + 1))
    selected = [i for i in selected if 1 <= i <= len(book.chapters)]
    tags = {"album": title, "artist": author, "album_artist": author,
            "genre": "Audiobook", "language": {"uk": "ukr", "ru": "rus"}[lang]}

    # m4b: розділи складаються у підпапку, наприкінці - склеюються в один файл
    if job.fmt == "m4b":
        parts_dir = os.path.join(out_dir, "_розділи")
        part_fmt = "m4a"
    else:
        parts_dir, part_fmt = out_dir, job.fmt
    os.makedirs(parts_dir, exist_ok=True)

    def part_path(idx):
        t = book.chapters[idx - 1][0]
        return os.path.join(parts_dir, f"{idx:03d} - {ao.safe_filename(t, 60)}.{part_fmt}")

    todo = [i for i in selected if not os.path.exists(part_path(i))]
    if len(todo) < len(selected):
        cb.log(f"Вже готово розділів: {len(selected) - len(todo)} (пропускаю)")

    engine = None
    if todo:
        engine = engine_factory(engine_name, lang, job.voice, job.clone, job.speed, log=cb.log)

    total_chars = sum(len(book.chapters[i - 1][1]) for i in todo) or 1
    done_chars, t0 = 0, time.time()
    for n, idx in enumerate(todo, 1):
        ch_title, text = book.chapters[idx - 1]
        cb.log(f"\n[{idx}/{len(book.chapters)}] {ch_title}")
        frac0 = done_chars / total_chars
        span = len(text) / total_chars
        audio = synth_text(engine, text, job.pause, job.para_pause, cb, frac0, span,
                           f"Розділ {n}/{len(todo)}:")
        dst = part_path(idx)
        ao.encode((audio, engine.sr), dst, part_fmt, job.bitrate,
                  cover_jpg if job.fmt != "m4b" else None,
                  dict(tags, title=ch_title, track=idx))
        done_chars += len(text)
        elapsed = time.time() - t0
        eta = elapsed / done_chars * (total_chars - done_chars)
        cb.log(f"  збережено ({fmt_time(len(audio) / engine.sr)} аудіо). "
               f"Минуло {fmt_time(elapsed)}, залишилось ~{fmt_time(eta)}")
        cb.progress(done_chars / total_chars, f"Готово розділів {n}/{len(todo)}")
        cb.chapter_done(idx)

    result = out_dir
    if job.fmt == "m4b":
        parts = [(part_path(i), book.chapters[i - 1][0]) for i in selected
                 if os.path.exists(part_path(i))]
        dst = os.path.join(out_dir, ao.safe_filename(title) + ".m4b")
        cb.log("\nЗбираю M4B з розділами та обкладинкою…")
        ao.build_m4b(parts, dst, cover_jpg, dict(tags, title=title), job.bitrate, cb.log)
        if not job.keep_parts and len(parts) == len(book.chapters):
            shutil.rmtree(parts_dir, ignore_errors=True)
        result = dst
    cb.progress(1.0, "Готово")
    cb.log(f"\nГотово! {result}")
    return result


# ----------------------------------------------------------------------------
# Консоль
# ----------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Аудіокниги українською та російською")
    ap.add_argument("book", nargs="?", help="файл книги: " + " ".join(br.SUPPORTED))
    ap.add_argument("--lang", default="auto", choices=["auto", "uk", "ru"])
    ap.add_argument("--engine", default="auto", choices=["auto"] + list(te.ENGINES))
    ap.add_argument("--voice", help="назва голосу (див. --list-voices)")
    ap.add_argument("--clone", help="WAV-зразок голосу для клонування (лише holos)")
    ap.add_argument("--speed", type=float, default=1.0, help="швидкість, напр. 0.9")
    ap.add_argument("--format", default="m4b", choices=list(ao.FORMATS))
    ap.add_argument("--bitrate", type=int, default=64, help="кбіт/с для m4a/m4b/mp3")
    ap.add_argument("--cover", help="своя обкладинка (jpg/png)")
    ap.add_argument("--no-cover", action="store_true")
    ap.add_argument("--title")
    ap.add_argument("--author")
    ap.add_argument("--out", help="папка для результату")
    ap.add_argument("--only", help="лише вибрані розділи, напр. 1-3 або 1,5,7")
    ap.add_argument("--keep-parts", action="store_true", help="для m4b: не видаляти розділи")
    ap.add_argument("--pause", type=float, default=0.15)
    ap.add_argument("--para-pause", type=float, default=0.7)
    ap.add_argument("--list-voices", action="store_true")
    ap.add_argument("--list-chapters", action="store_true")
    ap.add_argument("--test", help="озвучити лише цей текст у test.wav")
    ap.add_argument("--convert", metavar="ПАПКА", help="стиснути готові аудіофайли з папки")
    a = ap.parse_args()

    if a.convert:
        outs = ao.convert_folder(a.convert, a.format, a.out, a.cover, a.title, a.author,
                                 a.bitrate)
        print("Готово:", *outs, sep="\n  ")
        return

    if a.list_voices:
        engines = [a.engine] if a.engine != "auto" else list(te.ENGINES)
        for e in engines:
            print(f"{te.ENGINES[e]['name']}:")
            if e == "holos":
                voices = te.holos_list_voices()
            else:
                voices = [v for l in te.ENGINES[e]["langs"] for v in te.static_voices(e, l)]
            for v in voices:
                print("   ", v)
        return

    if a.test:
        lang = br.detect_lang(a.test) if a.lang == "auto" else a.lang
        eng_name = te.DEFAULT_ENGINE[lang] if a.engine == "auto" else a.engine
        eng = te.make_engine(eng_name, lang, a.voice, a.clone, a.speed)
        audio = synth_text(eng, a.test, a.pause, a.para_pause, Callbacks())
        import soundfile as sf
        name = f"test_{ao.safe_filename(str(a.voice or eng_name), 30)}.wav"
        sf.write(name, audio, eng.sr)
        print(f"Готово: {name}")
        return

    if not a.book:
        ap.error("вкажіть файл книги або --test / --list-voices / --convert")

    if a.list_chapters:
        b = br.read_book(a.book)
        print(f"{b.title} — {b.author}. Мова: {b.lang}. Обкладинка: {'є' if b.cover else 'немає'}")
        for i, (t, c) in enumerate(b.chapters, 1):
            print(f"{i:3d}. {t[:60]}  ({len(c)} символів)")
        return

    n = len(br.read_book(a.book).chapters) if a.only else 0
    job = Job(book=a.book, lang=a.lang, engine=a.engine, voice=a.voice, clone=a.clone,
              speed=a.speed, fmt=a.format, bitrate=a.bitrate, cover=a.cover,
              no_cover=a.no_cover, out=a.out, only=parse_range(a.only, n) if a.only else None,
              pause=a.pause, para_pause=a.para_pause, title=a.title, author=a.author,
              keep_parts=a.keep_parts)
    last = [0]

    def progress(frac, text):
        p = int(frac * 100)
        if p != last[0]:
            last[0] = p
            print(f"\r  {p:3d}%  {text}      ", end="", flush=True)

    try:
        make_audiobook(job, Callbacks(progress=progress))
    except KeyboardInterrupt:
        print("\nЗупинено. Готові розділи збережено - запустіть ще раз, щоб продовжити.")


if __name__ == "__main__":
    main()
