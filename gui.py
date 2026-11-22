#!/usr/bin/env python3
"""Аудіокниги — графічний інтерфейс (CustomTkinter). Українська та російська озвучка."""

import io
import json
import os
import queue
import sys
import tempfile
import threading
import traceback
import tkinter as tk
from tkinter import filedialog, messagebox

import app_paths  # noqa: F401  (налаштовує шляхи до моделей - першим)
import customtkinter as ctk
from PIL import Image

import audio_out as ao
import audiobook as ab
import book_reader as br
import tts_engines as te

SETTINGS = os.path.join(app_paths.APP_DIR, "gui_settings.json")
ICON_ICO = os.path.join(app_paths.RES_DIR, "assets", "icon.ico")
ICON_PNG = os.path.join(app_paths.RES_DIR, "assets", "icon.png")

APP_NAME = "Аудіокниги"
LANGS = {"auto": "Авто", "uk": "Українська", "ru": "Русский"}
LANG_FULL = {"uk": "українська", "ru": "російська"}
FORMATS = {"m4b": "M4B", "m4a": "M4A", "mp3": "MP3", "wav": "WAV"}
FORMAT_HINT = {
    "m4b": "Одна книга з розділами й обкладинкою — найкраще для телефона",
    "m4a": "Окремий файл AAC на кожен розділ",
    "mp3": "Окремий MP3 на кожен розділ — грає будь-де",
    "wav": "Без стиснення — дуже великі файли",
}
BITRATES = ["32 кбіт/с", "48 кбіт/с", "64 кбіт/с", "96 кбіт/с", "128 кбіт/с"]
THEMES = {"light": "Світла", "dark": "Темна", "system": "Авто"}
BOOK_TYPES = [("Книги", "*.txt *.fb2 *.zip *.epub *.mobi *.azw *.azw3 *.pdf"),
              ("Усі файли", "*.*")]
IMG_TYPES = [("Зображення", "*.jpg *.jpeg *.png *.webp *.bmp *.gif"), ("Усі файли", "*.*")]

# --- палітра (світла, темна)
BLUE = ("#0057B7", "#2F7DE1")
BLUE_HOVER = ("#00478F", "#2267C4")
YELLOW = "#FFD500"
YELLOW_HOVER = "#F0C400"
INK = "#1B2433"
BG = ("#EEF2F8", "#0F1520")
CARD = ("#FFFFFF", "#1A2230")
CARD_BORDER = ("#DDE3EC", "#273246")
SIDEBAR = ("#0B3D91", "#0A1A33")
MUTED = ("#5B6575", "#9AA4B5")
TEXT = ("#1B2433", "#E8ECF2")
FIELD = ("#F4F6FA", "#111826")


# Перемикачі та списки: у світлій темі - світлі з темним текстом,
# у темній - як і було (синє з білим текстом)
SEG_STYLE = dict(
    fg_color=("#E6ECF5", "#2A3446"),
    selected_color=(YELLOW, "#2F7DE1"), selected_hover_color=(YELLOW_HOVER, "#2267C4"),
    unselected_color=("#E6ECF5", "#2A3446"), unselected_hover_color=("#D6DFEC", "#34405A"),
    text_color=(INK, "#FFFFFF"), text_color_disabled=("#9AA4B5", "#6B7587"))
OPT_STYLE = dict(
    fg_color=("#F4F6FA", "#2F7DE1"), button_color=("#E3E9F2", "#2267C4"),
    button_hover_color=("#D3DCEA", "#1D5AAE"), text_color=(INK, "#FFFFFF"),
    text_color_disabled=("#9AA4B5", "#9AB4DD"))
COMBO_STYLE = dict(
    fg_color=FIELD, border_color=CARD_BORDER, button_color=("#E3E9F2", "#2F7DE1"),
    button_hover_color=("#D3DCEA", "#2267C4"), text_color=TEXT)


def rev(d, value):
    return next((k for k, v in d.items() if v == value), None)


def font(size=13, weight="normal"):
    return ctk.CTkFont(family="Segoe UI", size=size, weight=weight)


class Card(ctk.CTkFrame):
    def __init__(self, master, title=None, **kw):
        super().__init__(master, fg_color=CARD, corner_radius=14, border_width=1,
                         border_color=CARD_BORDER, **kw)
        self.columnconfigure(0, weight=1)
        if title:
            ctk.CTkLabel(self, text=title, font=font(15, "bold"), text_color=TEXT,
                         anchor="w").grid(row=0, column=0, columnspan=3, sticky="ew",
                                          padx=16, pady=(12, 4))


class App(ctk.CTk):
    def __init__(self):
        self.settings = self._load_settings()
        ctk.set_appearance_mode(self.settings.get("theme", "system"))
        ctk.set_default_color_theme("blue")
        super().__init__(fg_color=BG)
        self.title(APP_NAME)
        self.geometry("1220x820")
        self.minsize(1040, 700)
        self._set_icon()

        self.q = queue.Queue()
        self.stop_event = threading.Event()
        self.worker = None
        self.book = None
        self.cover_data = None
        self.engine_cache = {}
        self.result_path = None

        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)
        self._build_sidebar()

        self.content = ctk.CTkFrame(self, fg_color="transparent")
        self.content.grid(row=0, column=1, sticky="nsew", padx=18, pady=(16, 0))
        self.content.columnconfigure(0, weight=1)
        self.content.rowconfigure(0, weight=1)
        self.pages = {"book": self._build_book_page(), "convert": self._build_convert_page(),
                      "log": self._build_log_page()}
        self._build_bottom_bar()
        self.show_page("book")

        self._refresh_engines()
        self.after(100, self._poll)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ================================================================ layout
    def _set_icon(self):
        try:
            if sys.platform == "win32" and os.path.exists(ICON_ICO):
                self.iconbitmap(ICON_ICO)
                # для панелі задач Windows - окремий ідентифікатор програми
                import ctypes
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("audiobooks.uk.ru")
            if os.path.exists(ICON_PNG):
                self._icon_photo = tk.PhotoImage(file=ICON_PNG).subsample(8)
                self.wm_iconphoto(True, self._icon_photo)
        except Exception:
            pass

    def _build_sidebar(self):
        sb = ctk.CTkFrame(self, width=220, corner_radius=0, fg_color=SIDEBAR)
        sb.grid(row=0, column=0, rowspan=2, sticky="nsw")
        sb.grid_propagate(False)
        sb.columnconfigure(0, weight=1)
        sb.rowconfigure(5, weight=1)

        if os.path.exists(ICON_PNG):
            logo = ctk.CTkImage(Image.open(ICON_PNG), size=(84, 84))
            ctk.CTkLabel(sb, image=logo, text="").grid(row=0, column=0, pady=(26, 6))
        ctk.CTkLabel(sb, text=APP_NAME, font=font(22, "bold"), text_color="#FFFFFF").grid(
            row=1, column=0)
        ctk.CTkLabel(sb, text="українська · русский", font=font(12),
                     text_color="#C9D6EE").grid(row=2, column=0, pady=(0, 2))
        # тонка синьо-жовта смужка
        stripe = ctk.CTkFrame(sb, fg_color="transparent", height=6)
        stripe.grid(row=3, column=0, pady=(10, 18))
        ctk.CTkFrame(stripe, width=70, height=4, fg_color="#3D8BFF", corner_radius=2).pack(
            side="left")
        ctk.CTkFrame(stripe, width=70, height=4, fg_color=YELLOW, corner_radius=2).pack(
            side="left")

        nav = ctk.CTkFrame(sb, fg_color="transparent")
        nav.grid(row=4, column=0, sticky="ew", padx=12)
        nav.columnconfigure(0, weight=1)
        self.nav_buttons = {}
        for i, (key, text) in enumerate([("book", "📖   Озвучити книгу"),
                                         ("convert", "🗜   Стиснути аудіо"),
                                         ("log", "📝   Журнал")]):
            b = ctk.CTkButton(nav, text=text, anchor="w", height=42, corner_radius=10,
                              font=font(14), fg_color="transparent", text_color="#FFFFFF",
                              hover_color=("#164FA8", "#15284A"),
                              command=lambda k=key: self.show_page(k))
            b.grid(row=i, column=0, sticky="ew", pady=3)
            self.nav_buttons[key] = b

        bottom = ctk.CTkFrame(sb, fg_color="transparent")
        bottom.grid(row=6, column=0, sticky="ew", padx=14, pady=16)
        ctk.CTkLabel(bottom, text="Тема", font=font(12), text_color="#C9D6EE").pack(anchor="w")
        self.theme_btn = ctk.CTkSegmentedButton(
            bottom, values=list(THEMES.values()), font=font(12),
            selected_color=YELLOW, selected_hover_color=YELLOW_HOVER, text_color=INK,
            unselected_color=("#E3E9F3", "#DCE4F2"), unselected_hover_color=("#D2DBEA", "#C9D4E8"),
            command=self._set_theme)
        self.theme_btn.set(THEMES.get(self.settings.get("theme", "system"), "Авто"))
        self.theme_btn.pack(fill="x", pady=(4, 0))
        ctk.CTkLabel(bottom, text="Офлайн · ШІ-озвучка на процесорі", font=font(11),
                     text_color="#9FB3D6").pack(anchor="w", pady=(12, 0))

    def show_page(self, key):
        for k, p in self.pages.items():
            if k == key:
                p.grid(row=0, column=0, sticky="nsew")
            else:
                p.grid_forget()
        for k, b in self.nav_buttons.items():
            b.configure(fg_color=("#1F5FC4", "#1C3560") if k == key else "transparent")
        self.btn_start.configure(text="▶   Створити аудіокнигу" if key != "convert"
                                 else "▶   Стиснути")

    def _set_theme(self, value):
        mode = rev(THEMES, value) or "system"
        ctk.set_appearance_mode(mode)
        self.settings["theme"] = mode
        self.after(50, self._style_listbox)

    # ----------------------------------------------------------- book page
    def _build_book_page(self):
        page = ctk.CTkFrame(self.content, fg_color="transparent")
        page.columnconfigure(1, weight=1)
        page.columnconfigure(2, weight=1)
        page.rowconfigure(2, weight=1)

        # --- книга
        head = Card(page)
        head.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 12))
        head.columnconfigure(1, weight=1)
        ctk.CTkButton(head, text="📖  Обрати книгу", height=44, width=190, corner_radius=10,
                      font=font(15, "bold"), fg_color=BLUE, hover_color=BLUE_HOVER,
                      command=self._pick_book).grid(row=0, column=0, rowspan=2, padx=16,
                                                    pady=14, sticky="w")
        self.lbl_file = ctk.CTkLabel(head, text="Файл не обрано  ·  txt, fb2, epub, mobi, pdf",
                                     font=font(15, "bold"), text_color=TEXT, anchor="w")
        self.lbl_file.grid(row=0, column=1, sticky="ew", pady=(14, 0))
        self.lbl_meta = ctk.CTkLabel(head, text="Мова визначиться автоматично", font=font(12),
                                     text_color=MUTED, anchor="w")
        self.lbl_meta.grid(row=1, column=1, sticky="ew", pady=(0, 14))

        meta = ctk.CTkFrame(head, fg_color="transparent")
        meta.grid(row=0, column=2, rowspan=2, padx=16, sticky="e")
        self.v_title, self.v_author = tk.StringVar(), tk.StringVar()
        for r, (lbl, var) in enumerate([("Назва", self.v_title), ("Автор", self.v_author)]):
            ctk.CTkLabel(meta, text=lbl, font=font(12), text_color=MUTED).grid(
                row=r, column=0, sticky="w", padx=(0, 8), pady=3)
            ctk.CTkEntry(meta, textvariable=var, width=300, height=32, fg_color=FIELD,
                         border_color=CARD_BORDER).grid(row=r, column=1, pady=3)
        self.v_book = tk.StringVar()

        # --- обкладинка
        cov = Card(page, title="Обкладинка")
        cov.grid(row=1, column=0, sticky="nsew", padx=(0, 12))
        self.cover_box = ctk.CTkLabel(cov, text="Без обкладинки", width=170, height=240,
                                      fg_color=FIELD, corner_radius=10, text_color=MUTED,
                                      font=font(12))
        self.cover_box.grid(row=1, column=0, columnspan=2, padx=16, pady=(4, 10))
        ctk.CTkButton(cov, text="Своя…", width=80, height=30, fg_color=BLUE,
                      hover_color=BLUE_HOVER, command=self._pick_cover).grid(
            row=2, column=0, padx=(16, 4), pady=(0, 14), sticky="ew")
        ctk.CTkButton(cov, text="Прибрати", width=80, height=30, fg_color="transparent",
                      border_width=1, border_color=CARD_BORDER, text_color=TEXT,
                      hover_color=FIELD, command=lambda: self._set_cover(None)).grid(
            row=2, column=1, padx=(4, 16), pady=(0, 14), sticky="ew")

        # --- голос
        vc = Card(page, title="Голос")
        vc.grid(row=1, column=1, sticky="nsew", padx=(0, 12))
        vc.columnconfigure(1, weight=1)
        r = 1

        def lab(text, row):
            ctk.CTkLabel(vc, text=text, font=font(12), text_color=MUTED, anchor="w").grid(
                row=row, column=0, sticky="w", padx=(16, 8), pady=5)

        lab("Мова", r)
        self.seg_lang = ctk.CTkSegmentedButton(vc, values=list(LANGS.values()), font=font(12),
                                               command=lambda v: self._refresh_engines(),
                                               **SEG_STYLE)
        self.seg_lang.set(LANGS[self.settings.get("lang", "auto")])
        self.seg_lang.grid(row=r, column=1, columnspan=2, sticky="ew", padx=(0, 16), pady=5)
        r += 1
        self.lbl_detected = ctk.CTkLabel(vc, text="", font=font(11), text_color=MUTED,
                                         anchor="w", height=14)
        self.lbl_detected.grid(row=r, column=1, columnspan=2, sticky="w")
        r += 1
        lab("Рушій", r)
        self.opt_engine = ctk.CTkOptionMenu(vc, values=["—"], font=font(12),
                                            command=lambda v: self._refresh_voices(), **OPT_STYLE)
        self.opt_engine.grid(row=r, column=1, columnspan=2, sticky="ew", padx=(0, 16), pady=5)
        r += 1
        lab("Голос", r)
        self.cb_voice = ctk.CTkComboBox(vc, values=[], font=font(12), **COMBO_STYLE)
        self.cb_voice.grid(row=r, column=1, sticky="ew", pady=5)
        self.btn_voices = ctk.CTkButton(vc, text="⟳", width=34, fg_color="transparent",
                                        border_width=1, border_color=CARD_BORDER,
                                        text_color=TEXT, hover_color=FIELD,
                                        command=self._load_holos_voices)
        self.btn_voices.grid(row=r, column=2, padx=(6, 16), pady=5)
        r += 1
        lab("Клон голосу", r)
        self.v_clone = tk.StringVar()
        self.ent_clone = ctk.CTkEntry(vc, textvariable=self.v_clone, fg_color=FIELD,
                                      border_color=CARD_BORDER,
                                      placeholder_text="WAV-зразок 10–20 с (лише HolosTTS)")
        self.ent_clone.grid(row=r, column=1, sticky="ew", pady=5)
        self.btn_clone = ctk.CTkButton(vc, text="…", width=34, fg_color="transparent",
                                       border_width=1, border_color=CARD_BORDER, text_color=TEXT,
                                       hover_color=FIELD, command=self._pick_clone)
        self.btn_clone.grid(row=r, column=2, padx=(6, 16), pady=5)
        r += 1
        lab("Швидкість", r)
        self.v_speed = tk.DoubleVar(value=self.settings.get("speed", 1.0))
        self.lbl_speed = ctk.CTkLabel(vc, text=f"{self.v_speed.get():.2f}×", width=44,
                                      font=font(12, "bold"), text_color=TEXT)
        ctk.CTkSlider(vc, from_=0.6, to=1.6, number_of_steps=20, variable=self.v_speed,
                      button_color=BLUE, button_hover_color=BLUE_HOVER, progress_color=BLUE,
                      command=lambda v: self.lbl_speed.configure(text=f"{float(v):.2f}×")
                      ).grid(row=r, column=1, sticky="ew", pady=5)
        self.lbl_speed.grid(row=r, column=2, padx=(6, 16))
        r += 1
        lab("Перевірка", r)
        self.v_test = tk.StringVar(value="Привіт! Так звучатиме ваша аудіокнига.")
        ctk.CTkEntry(vc, textvariable=self.v_test, fg_color=FIELD, border_color=CARD_BORDER
                     ).grid(row=r, column=1, sticky="ew", pady=(5, 14))
        self.btn_test = ctk.CTkButton(vc, text="▶", width=34, fg_color=YELLOW,
                                      hover_color=YELLOW_HOVER, text_color=INK,
                                      font=font(13, "bold"), command=self._test_voice)
        self.btn_test.grid(row=r, column=2, padx=(6, 16), pady=(5, 14))

        # --- результат
        oc = Card(page, title="Результат")
        oc.grid(row=1, column=2, sticky="nsew")
        oc.columnconfigure(0, weight=1)
        self.seg_fmt = ctk.CTkSegmentedButton(oc, values=list(FORMATS.values()), font=font(12),
                                              command=self._fmt_changed, **SEG_STYLE)
        self.seg_fmt.set(FORMATS[self.settings.get("fmt", "m4b")])
        self.seg_fmt.grid(row=1, column=0, columnspan=2, sticky="ew", padx=16, pady=(4, 2))
        self.lbl_fmt = ctk.CTkLabel(oc, text="", font=font(11), text_color=MUTED,
                                    anchor="w", justify="left", wraplength=290)
        self.lbl_fmt.grid(row=2, column=0, columnspan=2, sticky="ew", padx=16)
        ctk.CTkLabel(oc, text="Якість (стиснення)", font=font(12), text_color=MUTED,
                     anchor="w").grid(row=3, column=0, sticky="w", padx=16, pady=(10, 2))
        self.opt_bitrate = ctk.CTkOptionMenu(oc, values=BITRATES, font=font(12), **OPT_STYLE)
        self.opt_bitrate.set(f"{self.settings.get('bitrate', 64)} кбіт/с")
        self.opt_bitrate.grid(row=4, column=0, columnspan=2, sticky="ew", padx=16)
        ctk.CTkLabel(oc, text="Папка результату", font=font(12), text_color=MUTED,
                     anchor="w").grid(row=5, column=0, sticky="w", padx=16, pady=(10, 2))
        self.v_out = tk.StringVar()
        ctk.CTkEntry(oc, textvariable=self.v_out, fg_color=FIELD, border_color=CARD_BORDER
                     ).grid(row=6, column=0, sticky="ew", padx=(16, 0), pady=(0, 14))
        ctk.CTkButton(oc, text="…", width=34, fg_color="transparent", border_width=1,
                      border_color=CARD_BORDER, text_color=TEXT, hover_color=FIELD,
                      command=lambda: self._pick_dir(self.v_out)).grid(
            row=6, column=1, padx=(6, 16), pady=(0, 14))
        self._fmt_changed(self.seg_fmt.get())

        # --- розділи
        ch = Card(page)
        ch.grid(row=2, column=0, columnspan=3, sticky="nsew", pady=(12, 0))
        ch.rowconfigure(1, weight=1)
        top = ctk.CTkFrame(ch, fg_color="transparent")
        top.grid(row=0, column=0, columnspan=2, sticky="ew", padx=16, pady=(12, 6))
        ctk.CTkLabel(top, text="Розділи", font=font(15, "bold"), text_color=TEXT).pack(
            side="left")
        ctk.CTkLabel(top, text="   Ctrl / Shift — виділити кілька", font=font(11),
                     text_color=MUTED).pack(side="left")
        for txt, cmd in [("Жодного", lambda: self._select(False)),
                         ("Усі", lambda: self._select(True))]:
            ctk.CTkButton(top, text=txt, width=80, height=28, fg_color="transparent",
                          border_width=1, border_color=CARD_BORDER, text_color=TEXT,
                          hover_color=FIELD, command=cmd).pack(side="right", padx=(6, 0))
        self.lbl_chars = ctk.CTkLabel(top, text="", font=font(12), text_color=MUTED)
        self.lbl_chars.pack(side="right", padx=12)

        self.lst = tk.Listbox(ch, selectmode="extended", activestyle="none",
                              exportselection=False, borderwidth=0, highlightthickness=0,
                              font=("Segoe UI", 11))
        self.lst.grid(row=1, column=0, sticky="nsew", padx=(16, 0), pady=(0, 14))
        sbar = ctk.CTkScrollbar(ch, command=self.lst.yview)
        sbar.grid(row=1, column=1, sticky="ns", padx=(2, 10), pady=(0, 14))
        self.lst.configure(yscrollcommand=sbar.set)
        self.lst.bind("<<ListboxSelect>>", lambda e: self._update_selected_info())
        self._style_listbox()
        return page

    def _style_listbox(self):
        dark = ctk.get_appearance_mode() == "Dark"
        self.lst.configure(bg="#111826" if dark else "#F4F6FA",
                           fg="#E8ECF2" if dark else "#1B2433",
                           selectbackground="#22406E" if dark else "#D3E3F8",
                           selectforeground="#FFFFFF" if dark else "#0B2A55")

    def _fmt_changed(self, value):
        fmt = rev(FORMATS, value) or "m4b"
        self.lbl_fmt.configure(text=FORMAT_HINT[fmt])
        self.opt_bitrate.configure(state="disabled" if fmt == "wav" else "normal")

    # -------------------------------------------------------- convert page
    def _build_convert_page(self):
        page = ctk.CTkFrame(self.content, fg_color="transparent")
        page.columnconfigure(0, weight=1)
        c = Card(page, title="Стиснути готове аудіо")
        c.grid(row=0, column=0, sticky="new")
        c.columnconfigure(1, weight=1)
        ctk.CTkLabel(c, text="Перетворіть папку з WAV/MP3 на M4B з розділами чи M4A/MP3 "
                             "з обкладинкою.\nФайли беруться за алфавітом; у M4B кожен файл "
                             "стає розділом.", font=font(12), text_color=MUTED,
                     justify="left", anchor="w").grid(row=1, column=0, columnspan=3,
                                                      sticky="w", padx=16, pady=(0, 10))
        self.c_folder, self.c_cover = tk.StringVar(), tk.StringVar()
        self.c_title, self.c_author, self.c_out = tk.StringVar(), tk.StringVar(), tk.StringVar()
        rows = [("Папка з аудіо", self.c_folder, lambda: self._pick_dir(self.c_folder, True)),
                ("Обкладинка", self.c_cover, lambda: self._pick_file(self.c_cover, IMG_TYPES)),
                ("Назва книги", self.c_title, None),
                ("Автор", self.c_author, None),
                ("Куди зберегти", self.c_out, lambda: self._pick_dir(self.c_out))]
        for i, (lbl, var, cmd) in enumerate(rows, 2):
            ctk.CTkLabel(c, text=lbl, font=font(12), text_color=MUTED, anchor="w").grid(
                row=i, column=0, sticky="w", padx=16, pady=5)
            ctk.CTkEntry(c, textvariable=var, fg_color=FIELD, border_color=CARD_BORDER,
                         height=32).grid(row=i, column=1, sticky="ew", pady=5,
                                         padx=(0, 6 if cmd else 16))
            if cmd:
                ctk.CTkButton(c, text="…", width=34, fg_color="transparent", border_width=1,
                              border_color=CARD_BORDER, text_color=TEXT, hover_color=FIELD,
                              command=cmd).grid(row=i, column=2, padx=(0, 16))
        i = len(rows) + 2
        ctk.CTkLabel(c, text="Формат", font=font(12), text_color=MUTED, anchor="w").grid(
            row=i, column=0, sticky="w", padx=16, pady=(10, 16))
        fm = ctk.CTkFrame(c, fg_color="transparent")
        fm.grid(row=i, column=1, sticky="w", pady=(10, 16))
        self.c_fmt = ctk.CTkSegmentedButton(fm, values=["M4B", "M4A", "MP3"], font=font(12),
                                            **SEG_STYLE)
        self.c_fmt.set("M4B")
        self.c_fmt.pack(side="left")
        self.c_bitrate = ctk.CTkOptionMenu(fm, values=BITRATES, font=font(12), **OPT_STYLE)
        self.c_bitrate.set("64 кбіт/с")
        self.c_bitrate.pack(side="left", padx=12)
        return page

    # ------------------------------------------------------------ log page
    def _build_log_page(self):
        page = ctk.CTkFrame(self.content, fg_color="transparent")
        page.columnconfigure(0, weight=1)
        page.rowconfigure(0, weight=1)
        c = Card(page, title="Журнал")
        c.grid(row=0, column=0, sticky="nsew")
        c.rowconfigure(1, weight=1)
        self.log_box = ctk.CTkTextbox(c, font=ctk.CTkFont(family="Consolas", size=12),
                                      fg_color=FIELD, text_color=TEXT, wrap="word")
        self.log_box.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 16))
        self.log_box.configure(state="disabled")
        return page

    # ---------------------------------------------------------- bottom bar
    def _build_bottom_bar(self):
        bar = Card(self)
        bar.grid(row=1, column=1, sticky="ew", padx=18, pady=16)
        bar.columnconfigure(1, weight=1)
        self.btn_start = ctk.CTkButton(bar, text="▶   Створити аудіокнигу", height=46, width=250,
                                       corner_radius=12, font=font(15, "bold"),
                                       fg_color=YELLOW, hover_color=YELLOW_HOVER, text_color=INK,
                                       text_color_disabled="#7A6A20", command=self._start)
        self.btn_start.grid(row=0, column=0, rowspan=2, padx=14, pady=12)
        self.lbl_status = ctk.CTkLabel(bar, text="Оберіть книгу, щоб почати", font=font(13),
                                       text_color=TEXT, anchor="w")
        self.lbl_status.grid(row=0, column=1, sticky="ew", pady=(14, 2))
        self.pb = ctk.CTkProgressBar(bar, height=10, corner_radius=5, progress_color=BLUE)
        self.pb.set(0)
        self.pb.grid(row=1, column=1, sticky="ew", pady=(2, 14))
        self.btn_stop = ctk.CTkButton(bar, text="■  Стоп", width=90, height=36,
                                      fg_color="transparent", border_width=1,
                                      border_color=CARD_BORDER, text_color=TEXT,
                                      hover_color=FIELD, state="disabled", command=self._stop)
        self.btn_stop.grid(row=0, column=2, rowspan=2, padx=(14, 6))
        self.btn_open = ctk.CTkButton(bar, text="📂  Відкрити", width=110, height=36,
                                      fg_color=BLUE, hover_color=BLUE_HOVER, state="disabled",
                                      command=self._open_result)
        self.btn_open.grid(row=0, column=3, rowspan=2, padx=(0, 14))

    # ================================================================ helpers
    def _load_settings(self):
        try:
            return json.load(open(SETTINGS, encoding="utf-8"))
        except Exception:
            return {}

    def _save_settings(self):
        s = self.settings
        s.update(lang=rev(LANGS, self.seg_lang.get()) or "auto",
                 speed=round(self.v_speed.get(), 2),
                 fmt=rev(FORMATS, self.seg_fmt.get()) or "m4b",
                 bitrate=self._bitrate(self.opt_bitrate.get()))
        eng = self._engine()
        if eng:
            s.setdefault("voices", {})[f"{eng}:{self._lang()}"] = self.cb_voice.get()
            s.setdefault("engines", {})[self._lang()] = eng
        try:
            json.dump(s, open(SETTINGS, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        except OSError:
            pass

    @staticmethod
    def _bitrate(text):
        try:
            return int(str(text).split()[0])
        except (ValueError, IndexError):
            return 64

    def _lang(self):
        lang = rev(LANGS, self.seg_lang.get()) or "auto"
        if lang == "auto":
            lang = self.book.lang if self.book else "uk"
        return lang

    def _engine(self):
        return rev({k: v["name"] for k, v in te.ENGINES.items()}, self.opt_engine.get())

    def log(self, msg):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", msg + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def status(self, text):
        self.lbl_status.configure(text=text)

    def _busy(self, busy):
        st = "disabled" if busy else "normal"
        self.btn_start.configure(state=st)
        self.btn_test.configure(state=st)
        self.btn_stop.configure(state="normal" if busy else "disabled")

    def _pick_file(self, var, types):
        p = filedialog.askopenfilename(filetypes=types)
        if p:
            var.set(p)
        return p

    def _pick_dir(self, var, fill_title=False):
        p = filedialog.askdirectory()
        if p:
            var.set(p)
            if fill_title and not self.c_title.get():
                self.c_title.set(os.path.basename(p).replace(" - аудіо", ""))
                for name in ("cover.jpg", "cover.png", "folder.jpg"):
                    if os.path.exists(os.path.join(p, name)) and not self.c_cover.get():
                        self.c_cover.set(os.path.join(p, name))

    def _pick_clone(self):
        self._pick_file(self.v_clone, [("Голос", "*.wav *.pt"), ("Усі файли", "*.*")])

    def _select(self, all_):
        if all_:
            self.lst.select_set(0, "end")
        else:
            self.lst.select_clear(0, "end")
        self._update_selected_info()

    # ------------------------------------------------------- engine/voices
    def _refresh_engines(self):
        lang = self._lang()
        engines = te.engines_for(lang)
        self.opt_engine.configure(values=[te.ENGINES[e]["name"] for e in engines])
        cur = self._engine()
        if cur not in engines:
            want = self.settings.get("engines", {}).get(lang, te.DEFAULT_ENGINE[lang])
            cur = want if want in engines else te.DEFAULT_ENGINE[lang]
        self.opt_engine.set(te.ENGINES[cur]["name"])
        if self.seg_lang.get() == LANGS["auto"] and self.book:
            self.lbl_detected.configure(text=f"визначено: {LANG_FULL[self.book.lang]}")
        else:
            self.lbl_detected.configure(text="")
        sample = {"uk": "Привіт! Так звучатиме ваша аудіокнига.",
                  "ru": "Привет! Так будет звучать ваша аудиокнига."}
        if self.v_test.get() in sample.values():
            self.v_test.set(sample[lang])
        self._refresh_voices()

    def _refresh_voices(self):
        eng, lang = self._engine(), self._lang()
        voices = te.static_voices(eng, lang)
        self.cb_voice.configure(values=voices)
        saved = self.settings.get("voices", {}).get(f"{eng}:{lang}")
        if saved and (saved in voices or not voices):
            self.cb_voice.set(saved)
        elif self.cb_voice.get() not in voices:
            self.cb_voice.set(voices[0] if voices else "")
        holos = eng == "holos"
        self.btn_voices.configure(state="normal" if holos else "disabled")
        self.ent_clone.configure(state="normal" if holos else "disabled")
        self.btn_clone.configure(state="normal" if holos else "disabled")

    def _load_holos_voices(self):
        self.status("Завантажую список голосів HolosTTS…")

        def job():
            v = te.holos_list_voices()
            self.q.put(("call", lambda: (self._refresh_voices(),
                                          self.status(f"Голосів HolosTTS: {len(v)}"))))
        self._run_bg(job, busy=False)

    def _get_engine(self, engine, lang, voice, clone, speed, log=print):
        key = (engine, lang, voice or "", clone or "", round(speed, 2))
        if key not in self.engine_cache:
            self.engine_cache.clear()            # тримаємо в пам'яті лише одну модель
            self.engine_cache[key] = te.make_engine(engine, lang, voice or None,
                                                    clone or None, speed, log=log)
        return self.engine_cache[key]

    # ----------------------------------------------------------------- book
    def _pick_book(self):
        p = filedialog.askopenfilename(filetypes=BOOK_TYPES)
        if p:
            self._load_book(p)

    def _load_book(self, path):
        self.v_book.set(path)
        self.status("Читаю книгу…")

        def job():
            b = br.read_book(path)
            self.q.put(("call", lambda: self._book_loaded(path, b)))
        self._run_bg(job, busy=False)

    def _book_loaded(self, path, b):
        self.book = b
        self.v_title.set(b.title)
        self.v_author.set(b.author)
        self.v_out.set(os.path.join(os.path.dirname(os.path.abspath(path)),
                                    ao.safe_filename(b.title) + " - аудіо"))
        self.lbl_file.configure(text=os.path.basename(path))
        self.lbl_meta.configure(text=f"Мова: {LANG_FULL[b.lang]}  ·  розділів: "
                                     f"{len(b.chapters)}  ·  обкладинка: "
                                     f"{'є' if b.cover else 'немає'}")
        self.lst.delete(0, "end")
        for i, (t, c) in enumerate(b.chapters, 1):
            self.lst.insert("end", f"  {i:3d}.  {t[:90]}   ·  {len(c):,} симв.".replace(",", " "))
        self.lst.select_set(0, "end")
        self._set_cover(b.cover)
        self._refresh_engines()
        self._update_selected_info()
        self.status("Книга готова. Оберіть голос і натисніть «Створити аудіокнигу»")
        self.log(f"Відкрито: {os.path.basename(path)} — мова {LANG_FULL[b.lang]}, "
                 f"розділів {len(b.chapters)}, обкладинка: {'є' if b.cover else 'немає'}")

    def _update_selected_info(self):
        if not self.book:
            return
        sel = self.lst.curselection()
        chars = sum(len(self.book.chapters[i][1]) for i in sel)
        # груба оцінка: ~ 900 символів на хвилину аудіо
        self.lbl_chars.configure(text=f"обрано {len(sel)} з {len(self.book.chapters)}  ·  "
                                      f"≈ {chars / 900 / 60:.1f} год аудіо")

    def _pick_cover(self):
        p = filedialog.askopenfilename(filetypes=IMG_TYPES)
        if p:
            self._set_cover(open(p, "rb").read())

    def _set_cover(self, data):
        self.cover_data = data
        if not data:
            self.cover_box.configure(image=None, text="Без обкладинки")
            self._cover_img = None
            return
        try:
            im = Image.open(io.BytesIO(data))
            im.load()
            im.thumbnail((170, 240))
            self._cover_img = ctk.CTkImage(im, size=im.size)
            self.cover_box.configure(image=self._cover_img, text="")
        except Exception as e:
            self.cover_data = None
            self.cover_box.configure(image=None, text=f"Не вдалося відкрити\n{e}")

    # ------------------------------------------------------------ actions
    def _start(self):
        if self.pages["convert"].winfo_ismapped():
            self._start_convert()
        else:
            self._start_book()

    def _make_job(self):
        if not self.book or not self.v_book.get():
            raise ValueError("Спершу оберіть книгу")
        sel = [i + 1 for i in self.lst.curselection()]
        if not sel:
            raise ValueError("Не обрано жодного розділу")
        cover_file = None
        if self.cover_data:
            cover_file = os.path.join(tempfile.gettempdir(), "audiobook_cover_src.bin")
            open(cover_file, "wb").write(self.cover_data)
        eng = self._engine()
        return ab.Job(book=self.v_book.get(), lang=rev(LANGS, self.seg_lang.get()) or "auto",
                      engine=eng, voice=self.cb_voice.get().strip() or None,
                      clone=(self.v_clone.get().strip() or None) if eng == "holos" else None,
                      speed=round(self.v_speed.get(), 2),
                      fmt=rev(FORMATS, self.seg_fmt.get()) or "m4b",
                      bitrate=self._bitrate(self.opt_bitrate.get()),
                      cover=cover_file, no_cover=not self.cover_data,
                      out=self.v_out.get().strip() or None,
                      only=None if len(sel) == len(self.book.chapters) else sel,
                      title=self.v_title.get().strip() or None,
                      author=self.v_author.get().strip() or None)

    def _start_book(self):
        try:
            job = self._make_job()
        except Exception as e:
            messagebox.showwarning(APP_NAME, str(e))
            return
        self._save_settings()
        self.log("\n=== Старт ===")
        self.status("Готуюсь…")

        def work():
            cb = ab.Callbacks(log=lambda m: self.q.put(("log", m)),
                              progress=lambda f, t: self.q.put(("progress", (f, t))),
                              should_stop=self.stop_event.is_set)
            res = ab.make_audiobook(job, cb, engine_factory=self._get_engine)
            self.q.put(("done", res))
        self._run_bg(work)

    def _start_convert(self):
        folder = self.c_folder.get().strip()
        if not folder or not os.path.isdir(folder):
            messagebox.showwarning(APP_NAME, "Оберіть папку з аудіофайлами")
            return
        args = dict(folder=folder, fmt=self.c_fmt.get().lower(),
                    out_dir=self.c_out.get().strip() or None,
                    cover=self.c_cover.get().strip() or None,
                    title=self.c_title.get().strip() or None,
                    author=self.c_author.get().strip() or None,
                    bitrate=self._bitrate(self.c_bitrate.get()))
        self.log("\n=== Стиснення ===")
        self.status("Стискаю…")

        def work():
            outs = ao.convert_folder(**args, log=lambda m: self.q.put(("log", m)),
                                     should_stop=self.stop_event.is_set)
            self.q.put(("done", outs[0] if len(outs) == 1 else os.path.dirname(outs[0])))
        self._run_bg(work)

    def _test_voice(self):
        text = self.v_test.get().strip()
        if not text:
            return
        eng, lang = self._engine(), self._lang()
        voice, clone = self.cb_voice.get().strip(), self.v_clone.get().strip()
        speed = round(self.v_speed.get(), 2)
        self._save_settings()
        self.status(f"Готую голос {voice or ''}… (перший раз модель вантажиться довше)")

        def work():
            import soundfile as sf
            e = self._get_engine(eng, lang, voice, clone if eng == "holos" else "", speed,
                                 log=lambda m: self.q.put(("log", m)))
            cb = ab.Callbacks(log=lambda m: self.q.put(("log", m)))
            audio = ab.synth_text(e, text, 0.15, 0.3, cb)
            path = os.path.join(tempfile.gettempdir(), "audiobook_test.wav")
            sf.write(path, audio, e.sr, subtype="PCM_16")
            self.q.put(("idle", "🔊  Відтворюю…"))
            if sys.platform == "win32":
                import winsound
                winsound.PlaySound(path, winsound.SND_FILENAME)
            self.q.put(("status", ""))
        self._run_bg(work)

    def _run_bg(self, fn, busy=True):
        if busy:
            if self.worker and self.worker.is_alive():
                messagebox.showinfo(APP_NAME, "Зачекайте — вже виконується інше завдання")
                return
            self.stop_event.clear()
            self._busy(True)
            self.pb.set(0)

        def wrapper():
            try:
                fn()
            except InterruptedError:
                self.q.put(("log", "\nЗупинено. Готові розділи збережено — натисніть «Створити» "
                                   "ще раз, щоб продовжити з того ж місця."))
                self.q.put(("idle", "Зупинено. Готові розділи збережено"))
            except Exception as e:
                self.q.put(("error", (e, traceback.format_exc())))
        t = threading.Thread(target=wrapper, daemon=True)
        if busy:
            self.worker = t
        t.start()

    def _stop(self):
        self.stop_event.set()
        self.status("Зупиняю після поточного речення…")

    def _open_result(self):
        p = self.result_path
        if p and os.path.exists(p):
            if os.path.isfile(p):
                os.system(f'explorer /select,"{os.path.normpath(p)}"')
            else:
                os.startfile(p)

    def _poll(self):
        try:
            while True:
                kind, data = self.q.get_nowait()
                if kind == "log":
                    self.log(data)
                    line = data.strip().splitlines()[-1] if data.strip() else ""
                    if line and not line.startswith("  !"):
                        self.status(line[:120])
                elif kind == "progress":
                    f, t = data
                    self.pb.set(f)
                    self.status(f"{f * 100:.1f}%   ·   {t}")
                elif kind == "status":
                    self.status(data)
                elif kind == "done":
                    self.result_path = data
                    self._busy(False)
                    self.btn_open.configure(state="normal")
                    self.pb.set(1)
                    self.status(f"✅  Готово!  {os.path.basename(str(data))}")
                    self.bell()
                elif kind == "idle":
                    self._busy(False)
                    self.status(data or "")
                elif kind == "error":
                    e, tb = data
                    self._busy(False)
                    self.log(tb)
                    self.status("❌  Помилка — подробиці в журналі")
                    messagebox.showerror(APP_NAME, str(e))
                elif kind == "call":
                    data()
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _on_close(self):
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno(APP_NAME, "Озвучення ще триває. Зупинити й вийти?\n"
                                                 "(готові розділи збережуться)"):
                return
        self._save_settings()
        self.destroy()


def selftest():
    """Аудіокниги.exe --selftest : озвучує фразу кожним рушієм, пише selftest.txt."""
    import time
    import soundfile as sf
    out_dir = os.path.join(app_paths.APP_DIR, "selftest")
    os.makedirs(out_dir, exist_ok=True)
    report = open(os.path.join(out_dir, "selftest.txt"), "w", encoding="utf-8")

    def say(m):
        report.write(m + "\n")
        report.flush()

    say(f"ffmpeg: {ao.ffmpeg_exe()}")
    cases = [("holos", "uk", None, "Привіт! Перевірка української озвучки."),
             ("kokoro", "ru", "dima", "Привет! Проверка русской озвучки."),
             ("silero", "ru", "xenia", "Привет! Проверка русской озвучки.")]
    for eng, lang, voice, text in cases:
        try:
            t = time.time()
            e = te.make_engine(eng, lang, voice, None, 1.0, log=say)
            audio = ab.synth_text(e, text, 0.15, 0.3, ab.Callbacks(log=say))
            wav = os.path.join(out_dir, f"{eng}.wav")
            sf.write(wav, audio, e.sr)
            ao.encode((audio, e.sr), os.path.join(out_dir, f"{eng}.m4a"), "m4a", 64,
                      None, {"title": eng})
            say(f"OK {eng}: {len(audio) / e.sr:.1f} с аудіо за {time.time() - t:.1f} с")
        except Exception:
            say(f"FAIL {eng}:\n{traceback.format_exc()}")
    say("DONE")
    report.close()


def main():
    if "--selftest" in sys.argv:
        selftest()
        return
    app = App()
    if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
        app._load_book(sys.argv[1])
    app.mainloop()


if __name__ == "__main__":
    main()
