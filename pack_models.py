"""Копіює моделі озвучки поруч із зібраним exe (dist/Аудіокниги/models),
щоб програма працювала на будь-якому ПК без інтернету."""

import glob
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEST = os.path.join(HERE, "dist", "Аудіокниги", "models")


def copy_file(src, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if not os.path.exists(dst) or os.path.getsize(dst) != os.path.getsize(src):
        shutil.copy2(src, dst)
    print("  +", os.path.relpath(dst, DEST))


def copy_tree(src, dst):
    shutil.copytree(src, dst, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns(".cache", "*.lock", "*.incomplete"))
    print("  +", os.path.relpath(dst, DEST) + os.sep)


def main():
    if not os.path.isdir(os.path.dirname(DEST)):
        sys.exit("Спершу зберіть exe (dist/Аудіокниги не знайдено)")
    ok = True

    # HolosTTS (українська) - з кешу Hugging Face
    hub = os.path.join(os.path.expanduser("~"), ".cache", "huggingface", "hub",
                       "models--patriotyk--HolosTTS", "snapshots")
    snaps = sorted(glob.glob(os.path.join(hub, "*")), key=os.path.getmtime)
    if snaps and os.path.exists(os.path.join(snaps[-1], "holos.pt")):
        for f in ("holos.pt", "voices.pt", "voice_encoder.pt"):
            p = os.path.join(snaps[-1], f)
            if os.path.exists(p):
                copy_file(p, os.path.join(DEST, "holos", f))
    else:
        print("! HolosTTS не знайдено в кеші - запустіть українську озвучку хоч раз")
        ok = False

    # stanza (наголоси для української)
    import stanza.resources.common as sc
    if os.path.exists(os.path.join(sc.DEFAULT_MODEL_DIR, "resources.json")):
        copy_tree(sc.DEFAULT_MODEL_DIR, os.path.join(DEST, "stanza"))
    else:
        print("! stanza (uk) не знайдено")
        ok = False

    # Kokoro-RU і Silero (російська)
    src_models = os.path.join(HERE, "models")
    if os.path.isdir(os.path.join(src_models, "kokoro-ru")):
        copy_tree(os.path.join(src_models, "kokoro-ru"), os.path.join(DEST, "kokoro-ru"))
    else:
        print("! Kokoro-RU не знайдено")
        ok = False
    for f in glob.glob(os.path.join(src_models, "*.pt")):
        copy_file(f, os.path.join(DEST, os.path.basename(f)))

    # кеш голосів HolosTTS, щоб список одразу був у вікні
    cache = os.path.join(HERE, "holos_voices.txt")
    if os.path.exists(cache):
        shutil.copy2(cache, os.path.join(os.path.dirname(DEST), "holos_voices.txt"))

    print("Готово" if ok else "Готово, але не все знайдено (див. вище)")


if __name__ == "__main__":
    main()
