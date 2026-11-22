# -*- mode: python ; coding: utf-8 -*-
# Збірка: holos_env\Scripts\python.exe -m PyInstaller audiobooks.spec --noconfirm
# (або build_exe.bat - він ще й копіює моделі поруч із exe)

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

datas = [("assets", "assets")]
binaries = []
hiddenimports = []

# пакети з даними/бінарниками, які PyInstaller сам не помічає
for pkg in ("customtkinter", "ruaccent", "imageio_ffmpeg", "espeakng_loader", "misaki",
            "phonemizer", "holos", "ipa_uk", "ukrainian_word_stress", "stanza",
            "num2words", "mobi", "librosa", "language_tags", "segments", "csvw",
            "marisa_trie", "razdel", "ukrainian_accentor"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception as e:
        print("skip", pkg, e)

hiddenimports += collect_submodules("num2words")
hiddenimports += ["onnxruntime", "pymupdf", "fitz", "pypdf", "soundfile", "PIL.ImageTk",
                  "darkdetect", "winsound",
                  # модулі, які імпортує модель Silero (torch.package) під час завантаження
                  "wave", "gzip", "json", "contextlib", "xml.etree.ElementTree",
                  "unicodedata", "torch.jit._script", "audioop",
                  # модель наголосів ukrainian_accentor (теж torch.package)
                  "six", "numpy.core.multiarray", "torch.nn.modules.sparse", "torch._utils",
                  "torch.nn.modules.rnn", "torch.nn.modules.linear",
                  "torch.nn.modules.batchnorm", "torch.nn.modules.activation",
                  "torch.nn.modules.dropout"]

a = Analysis(
    ["gui.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["matplotlib", "IPython", "jupyter", "gradio", "tensorboard", "pytest",
              "torch.utils.tensorboard"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Аудіокниги",
    icon="assets/icon.ico",
    console=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, upx=False, name="Аудіокниги")
