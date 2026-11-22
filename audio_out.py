"""
Робота з аудіо через ffmpeg: стиснення в M4A/MP3, збірка M4B з розділами,
вшивання обкладинки та тегів.

ffmpeg береться з PATH, а якщо його нема - з пакета imageio-ffmpeg.
"""

import io
import os
import re
import shutil
import subprocess
import tempfile

import numpy as np

NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)

FORMATS = {
    "m4b": "M4B - одна книга з розділами (найкраще для телефона)",
    "m4a": "M4A - окремий файл на розділ (AAC)",
    "mp3": "MP3 - окремий файл на розділ",
    "wav": "WAV - без стиснення",
}
AUDIO_EXT = (".wav", ".mp3", ".m4a", ".m4b", ".flac", ".ogg", ".opus", ".aac")

_FF = None


def ffmpeg_exe():
    global _FF
    if _FF:
        return _FF
    _FF = shutil.which("ffmpeg")
    if not _FF:
        try:
            import imageio_ffmpeg
            _FF = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            _FF = None
    return _FF


def _run(cmd, inp=None):
    p = subprocess.run(cmd, input=inp, capture_output=True, creationflags=NOWIN)
    if p.returncode != 0:
        err = p.stderr.decode("utf-8", "ignore").strip().splitlines()
        raise RuntimeError("ffmpeg: " + (" | ".join(err[-3:]) if err else "помилка"))
    return p


def decode_to_float(data: bytes, sr: int):
    """Будь-які аудіо-байти (mp3 тощо) -> numpy float32 mono."""
    p = _run([ffmpeg_exe(), "-loglevel", "error", "-i", "pipe:0",
              "-f", "f32le", "-ac", "1", "-ar", str(sr), "pipe:1"], data)
    return np.frombuffer(p.stdout, dtype="float32").copy()


def duration(path):
    """Тривалість файлу в секундах (через вивід ffmpeg -i)."""
    p = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", path], capture_output=True,
                       creationflags=NOWIN)
    m = re.search(rb"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", p.stderr)
    if not m:
        return 0.0
    h, mi, s = m.groups()
    return int(h) * 3600 + int(mi) * 60 + float(s)


# ----------------------------------------------------------------------------
# Обкладинка
# ----------------------------------------------------------------------------

def prepare_cover(data: bytes, out_path, max_side=1200):
    """Будь-яке зображення -> JPEG (RGB, до 1200 px). Повертає шлях або None."""
    if not data:
        return None
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(data))
        im.load()
        if im.mode not in ("RGB", "L"):
            bg = Image.new("RGB", im.size, (255, 255, 255))
            im = im.convert("RGBA")
            bg.paste(im, mask=im.split()[-1])
            im = bg
        else:
            im = im.convert("RGB")
        im.thumbnail((max_side, max_side))
        im.save(out_path, "JPEG", quality=90)
        return out_path
    except Exception:
        return None


# ----------------------------------------------------------------------------
# Кодування
# ----------------------------------------------------------------------------

def _tag_args(tags):
    out = []
    for k, v in tags.items():
        if v not in (None, ""):
            out += ["-metadata", f"{k}={v}"]
    return out


def encode(src, dst, fmt, bitrate=64, cover=None, tags=None):
    """src: шлях до аудіофайлу АБО (numpy_array, sr). fmt: m4a | m4b | mp3 | wav."""
    ff = ffmpeg_exe()
    if not ff:
        raise RuntimeError("ffmpeg не знайдено (встановіть пакет imageio-ffmpeg)")
    tags = dict(tags or {})
    inp = None
    if isinstance(src, tuple):
        audio, sr = src
        cmd = [ff, "-y", "-loglevel", "error", "-f", "f32le", "-ar", str(sr), "-ac", "1",
               "-i", "pipe:0"]
        inp = audio.astype("float32").tobytes()
    else:
        cmd = [ff, "-y", "-loglevel", "error", "-i", src]

    use_cover = cover and os.path.exists(cover) and fmt in ("m4a", "m4b", "mp3")
    if use_cover:
        cmd += ["-i", cover, "-map", "0:a", "-map", "1:v", "-c:v", "copy",
                "-disposition:v:0", "attached_pic",
                "-metadata:s:v", "title=Album cover", "-metadata:s:v", "comment=Cover (front)"]
    else:
        cmd += ["-map", "0:a", "-vn"]

    if fmt in ("m4a", "m4b"):
        cmd += ["-c:a", "aac", "-b:a", f"{bitrate}k", "-movflags", "+faststart",
                "-f", "mp4"]
        if fmt == "m4b":
            tags.setdefault("genre", "Audiobook")
            tags["media_type"] = "2"           # «аудіокнига» для iTunes/Apple Books
    elif fmt == "mp3":
        cmd += ["-c:a", "libmp3lame", "-b:a", f"{max(bitrate, 32)}k", "-id3v2_version", "3",
                "-f", "mp3"]
    elif fmt == "wav":
        cmd += ["-c:a", "pcm_s16le", "-f", "wav"]
    else:
        raise ValueError(fmt)
    cmd += _tag_args(tags)
    tmp = dst + ".tmp"
    cmd += [tmp]
    try:
        _run(cmd, inp)
        os.replace(tmp, dst)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return dst


def _ffmeta_escape(s):
    return re.sub(r"([=;#\\\n])", r"\\\1", str(s))


def build_m4b(parts, dst, cover=None, tags=None, bitrate=64, log=print):
    """parts: [(шлях, назва_розділу), ...] -> один .m4b з розділами.
    Якщо всі частини вже AAC (.m4a) - склеює без перекодування."""
    ff = ffmpeg_exe()
    tags = dict(tags or {})
    tags.setdefault("genre", "Audiobook")
    tmpdir = tempfile.mkdtemp(prefix="m4b_")
    try:
        all_aac = all(p.lower().endswith((".m4a", ".m4b")) for p, _ in parts)
        list_file = os.path.join(tmpdir, "list.txt")
        meta_file = os.path.join(tmpdir, "meta.txt")
        pos, chapters = 0.0, []
        with open(list_file, "w", encoding="utf-8") as lf:
            for path, title in parts:
                d = duration(path)
                chapters.append((pos, pos + d, title))
                pos += d
                lf.write("file '" + os.path.abspath(path).replace("'", "'\\''") + "'\n")
        with open(meta_file, "w", encoding="utf-8") as mf:
            mf.write(";FFMETADATA1\n")
            for k, v in tags.items():
                if v:
                    mf.write(f"{k}={_ffmeta_escape(v)}\n")
            for s, e, t in chapters:
                mf.write(f"[CHAPTER]\nTIMEBASE=1/1000\nSTART={int(s * 1000)}\n"
                         f"END={int(e * 1000)}\ntitle={_ffmeta_escape(t)}\n")

        cmd = [ff, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", list_file,
               "-i", meta_file]
        use_cover = cover and os.path.exists(cover)
        if use_cover:
            cmd += ["-i", cover]
        cmd += ["-map", "0:a", "-map_metadata", "1", "-map_chapters", "1"]
        if use_cover:
            cmd += ["-map", "2:v", "-c:v", "copy", "-disposition:v:0", "attached_pic"]
        if all_aac:
            cmd += ["-c:a", "copy"]
        else:
            log("  перекодовую в AAC…")
            cmd += ["-c:a", "aac", "-b:a", f"{bitrate}k", "-ac", "1"]
        cmd += ["-metadata", "media_type=2", "-movflags", "+faststart", "-f", "mp4"]
        tmp = dst + ".tmp"
        cmd += [tmp]
        _run(cmd)
        os.replace(tmp, dst)
        return dst
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
        if os.path.exists(dst + ".tmp"):
            os.remove(dst + ".tmp")


def natural_key(s):
    return [int(x) if x.isdigit() else x.lower() for x in re.split(r"(\d+)", s)]


def title_from_filename(name):
    t = os.path.splitext(name)[0]
    t = re.sub(r"^\d+\s*[-_.]\s*", "", t)
    return t or name


def convert_folder(folder, fmt, out_dir=None, cover=None, title=None, author=None,
                   bitrate=64, log=print, should_stop=lambda: False):
    """Стиснути готові аудіофайли з папки: у m4b (одним файлом) або m4a/mp3 (по файлу)."""
    files = sorted((f for f in os.listdir(folder)
                    if f.lower().endswith(AUDIO_EXT) and not f.endswith(".part.wav")),
                   key=natural_key)
    if not files:
        raise ValueError("У папці немає аудіофайлів")
    title = title or os.path.basename(os.path.normpath(folder)).replace(" - аудіо", "")
    out_dir = out_dir or folder
    os.makedirs(out_dir, exist_ok=True)
    cover_jpg = None
    if cover:
        cover_jpg = prepare_cover(open(cover, "rb").read(), os.path.join(out_dir, "cover.jpg"))
    tags = {"album": title, "artist": author, "album_artist": author}
    if fmt == "m4b":
        # спершу кожен файл -> AAC (так склеювання надійне), потім в одну книгу
        tmpdir = tempfile.mkdtemp(prefix="conv_")
        try:
            parts = []
            for i, f in enumerate(files, 1):
                if should_stop():
                    raise InterruptedError
                log(f"[{i}/{len(files)}] {f}")
                dst = os.path.join(tmpdir, f"{i:04d}.m4a")
                encode(os.path.join(folder, f), dst, "m4a", bitrate)
                parts.append((dst, title_from_filename(f)))
            out = os.path.join(out_dir, safe_filename(title) + ".m4b")
            log("Збираю M4B з розділами…")
            build_m4b(parts, out, cover_jpg, dict(tags, title=title), bitrate, log)
            return [out]
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
    outs = []
    for i, f in enumerate(files, 1):
        if should_stop():
            raise InterruptedError
        dst = os.path.join(out_dir, os.path.splitext(f)[0] + "." + fmt)
        if os.path.abspath(dst) == os.path.abspath(os.path.join(folder, f)):
            dst = os.path.splitext(dst)[0] + "_new." + fmt
        log(f"[{i}/{len(files)}] {f} -> {os.path.basename(dst)}")
        encode(os.path.join(folder, f), dst, fmt, bitrate, cover_jpg,
               dict(tags, title=title_from_filename(f), track=i))
        outs.append(dst)
    return outs


def safe_filename(s, n=80):
    s = re.sub(r'[\\/:*?"<>|\r\n\t]', " ", str(s))
    s = " ".join(s.split())[:n].strip(" .")
    return s or "book"
