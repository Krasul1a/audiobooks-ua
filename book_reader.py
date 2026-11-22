"""
Читання книг: .txt, .fb2, .fb2.zip, .epub, .mobi/.azw/.azw3, .pdf

read_book(path) -> Book(title, author, lang, chapters=[(назва, текст), ...],
                        cover=bytes|None)
"""

import base64
import html
import os
import re
import shutil
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import unquote

SUPPORTED = (".txt", ".fb2", ".fb2.zip", ".epub", ".mobi", ".azw", ".azw3", ".pdf")


@dataclass
class Book:
    title: str = ""
    author: str = ""
    lang: str = "uk"                       # "uk" або "ru"
    chapters: list = field(default_factory=list)
    cover: bytes = None                    # сирі байти зображення (jpg/png/...)


# ----------------------------------------------------------------------------
# Допоміжне
# ----------------------------------------------------------------------------

def decode_bytes(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp1251", "utf-16"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="ignore")


def detect_lang(text: str) -> str:
    """Грубе визначення мови за характерними літерами."""
    sample = text[:200000].lower()
    uk = sum(sample.count(c) for c in "іїєґ")
    ru = sum(sample.count(c) for c in "ыэъё")
    return "ru" if ru > uk else "uk"


def part_label(lang, n):
    return f"{'Часть' if lang == 'ru' else 'Частина'} {n}"


def intro_label(lang):
    return "Вступление" if lang == "ru" else "Вступ"


def _local(tag):
    return tag.split("}")[-1] if isinstance(tag, str) else ""


class _HtmlText(HTMLParser):
    BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6",
             "tr", "blockquote", "section", "article", "title"}
    SKIP = {"script", "style", "head"}
    HEADINGS = ("h1", "h2", "h3")

    def __init__(self, split_headings=False):
        super().__init__(convert_charrefs=True)
        self.parts, self.heading, self._skip, self._in_h = [], None, 0, None
        self._hbuf = []
        # для mobi7: ділимо один великий html на розділи за заголовками
        self.split_headings = split_headings
        self.sections = []                  # [(heading, [parts])]

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        if self.split_headings and (tag in self.HEADINGS or tag == "mbp:pagebreak"):
            self._flush_section()
        if tag in self.BLOCK:
            self.parts.append("\n")
        if tag in self.HEADINGS and self._in_h is None:
            self._in_h, self._hbuf = tag, []

    def handle_startendtag(self, tag, attrs):
        if tag == "mbp:pagebreak" and self.split_headings:
            self._flush_section()
        elif tag == "br":
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        if tag in self.BLOCK:
            self.parts.append("\n")
        if tag == self._in_h:
            h = " ".join("".join(self._hbuf).split())
            if h and (self.heading is None or self.split_headings):
                self.heading = h
            self._in_h = None

    def handle_data(self, data):
        if self._skip:
            return
        self.parts.append(data)
        if self._in_h:
            self._hbuf.append(data)

    def _flush_section(self):
        txt = self._join(self.parts)
        if txt:
            self.sections.append((self.heading, txt))
        self.parts, self.heading = [], None

    @staticmethod
    def _join(parts):
        t = "".join(parts)
        lines = [" ".join(l.split()) for l in t.split("\n")]
        return "\n".join(l for l in lines if l)

    def text(self):
        return self._join(self.parts)

    def all_sections(self):
        self._flush_section()
        return self.sections


# ----------------------------------------------------------------------------
# EPUB
# ----------------------------------------------------------------------------

def read_epub(path):
    book = Book()
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        container = ET.fromstring(z.read("META-INF/container.xml"))
        rootfile = next(el.get("full-path") for el in container.iter()
                        if _local(el.tag) == "rootfile")
        base = os.path.dirname(rootfile)
        opf = ET.fromstring(z.read(rootfile))

        def resolve(href):
            full = os.path.normpath(os.path.join(base, href.split("#")[0])).replace("\\", "/")
            if full in names:
                return full
            full = unquote(full)
            return full if full in names else None

        manifest = {}                       # id -> (href, media-type, properties)
        cover_id = None
        for el in opf.iter():
            t = _local(el.tag)
            if t == "item":
                manifest[el.get("id")] = (el.get("href") or "", el.get("media-type") or "",
                                          el.get("properties") or "")
            elif t == "meta" and el.get("name") == "cover":
                cover_id = el.get("content")
            elif t == "title" and not book.title and el.text:
                book.title = el.text.strip()
            elif t == "creator" and not book.author and el.text:
                book.author = el.text.strip()
            elif t == "language" and el.text:
                book.lang = "ru" if el.text.strip().lower().startswith("ru") else "uk"

        # --- обкладинка
        cover_href = None
        for iid, (href, mt, props) in manifest.items():
            if "cover-image" in props.split():
                cover_href = href
        if not cover_href and cover_id in manifest:
            href, mt, _ = manifest[cover_id]
            if mt.startswith("image/"):
                cover_href = href
        if not cover_href:
            for iid, (href, mt, _) in manifest.items():
                if mt.startswith("image/") and ("cover" in iid.lower() or "cover" in href.lower()):
                    cover_href = href
                    break
        if cover_href:
            full = resolve(cover_href)
            if full:
                book.cover = z.read(full)

        # --- зміст (NCX для EPUB2 або nav для EPUB3): файл -> назва розділу
        toc_titles = {}
        for iid, (href, mt, props) in manifest.items():
            full = resolve(href) if href else None
            if not full:
                continue
            try:
                if mt == "application/x-dtbncx+xml":
                    ncx = ET.fromstring(z.read(full))
                    ncx_base = os.path.dirname(full)
                    for np_ in ncx.iter():
                        if _local(np_.tag) != "navPoint":
                            continue
                        label = next((" ".join("".join(t.itertext()).split())
                                      for t in np_.iter() if _local(t.tag) == "text"), "")
                        src = next((c.get("src") for c in np_ if _local(c.tag) == "content"), None)
                        if src and label:
                            f = os.path.normpath(os.path.join(ncx_base, unquote(src.split("#")[0])))
                            toc_titles.setdefault(f.replace("\\", "/"), label)
                elif "nav" in props.split():
                    nav = ET.fromstring(z.read(full))
                    nav_base = os.path.dirname(full)
                    for a in nav.iter():
                        if _local(a.tag) == "a" and a.get("href"):
                            label = " ".join("".join(a.itertext()).split())
                            f = os.path.normpath(os.path.join(nav_base, unquote(a.get("href").split("#")[0])))
                            if label:
                                toc_titles.setdefault(f.replace("\\", "/"), label)
            except ET.ParseError:
                pass

        # --- текст
        spine = [el.get("idref") for el in opf.iter() if _local(el.tag) == "itemref"]
        for idref in spine:
            href = manifest.get(idref, ("",))[0]
            full = resolve(href) if href else None
            if not full:
                continue
            p = _HtmlText()
            p.feed(decode_bytes(z.read(full)))
            txt = p.text()
            if len(txt) < 30:                 # обкладинки, порожні сторінки
                continue
            book.chapters.append((toc_titles.get(full) or p.heading or "", txt))

    _finish(book, path)
    return book


# ----------------------------------------------------------------------------
# FB2
# ----------------------------------------------------------------------------

def _parse_fb2_bytes(raw):
    m = re.search(rb'encoding=["\']([\w-]+)["\']', raw[:200])
    enc = m.group(1).decode() if m else "utf-8"
    try:
        s = raw.decode(enc)
    except (LookupError, UnicodeDecodeError):
        s = decode_bytes(raw)
    s = re.sub(r"^<\?xml[^>]*\?>", "", s.lstrip("﻿").lstrip())
    # деякі fb2 містять html-сутності, яких не знає XML
    def fix_entity(m):
        u = html.unescape(m.group(0))
        return u if u != m.group(0) and u not in "<>&" else " "
    s = re.sub(r"&(?!(?:amp|lt|gt|quot|apos);)\w+;", fix_entity, s)
    return ET.fromstring(s)


def read_fb2(path, raw=None):
    if raw is None:
        raw = open(path, "rb").read()
    root = _parse_fb2_bytes(raw)
    book = Book()

    def para_text(el):
        return " ".join("".join(el.itertext()).split())

    # --- опис
    desc = next((c for c in root if _local(c.tag) == "description"), None)
    cover_ref = None
    if desc is not None:
        ti = next((c for c in desc if _local(c.tag) == "title-info"), None)
        if ti is not None:
            for el in ti:
                t = _local(el.tag)
                if t == "book-title" and el.text:
                    book.title = el.text.strip()
                elif t == "author" and not book.author:
                    parts = [para_text(x) for x in el
                             if _local(x.tag) in ("first-name", "last-name")]
                    book.author = " ".join(p for p in parts if p)
                elif t == "lang" and el.text:
                    book.lang = "ru" if el.text.strip().lower().startswith("ru") else "uk"
                elif t == "coverpage":
                    for img in el.iter():
                        for k, v in img.attrib.items():
                            if k.endswith("href") and v.startswith("#"):
                                cover_ref = v[1:]
    if cover_ref:
        for el in root:
            if _local(el.tag) == "binary" and el.get("id") == cover_ref and el.text:
                try:
                    book.cover = base64.b64decode(el.text)
                except Exception:
                    pass

    # --- текст
    def collect(sec):
        out = []
        for el in sec.iter():
            if _local(el.tag) in ("p", "v", "subtitle", "text-author"):
                t = para_text(el)
                if t:
                    out.append(t)
        return "\n".join(out)

    bodies = [b for b in root if _local(b.tag) == "body"]
    body = next((b for b in bodies if b.get("name") not in ("notes", "comments", "footnotes")),
                bodies[0] if bodies else None)
    if body is not None:
        def walk(sec, depth=0):
            subs = [c for c in sec if _local(c.tag) == "section"]
            title_el = next((c for c in sec if _local(c.tag) == "title"), None)
            title = para_text(title_el) if title_el is not None else ""
            # якщо розділ складається з великих підрозділів - ділимо глибше (томи/частини)
            if subs and depth < 2 and len(subs) > 1 and sum(len(collect(s)) for s in subs) > 5000:
                own = [c for c in sec if _local(c.tag) not in ("section",)]
                own_txt = "\n".join(collect(c) for c in own if _local(c.tag) != "title")
                if len(own_txt) >= 30:
                    book.chapters.append((title, own_txt))
                for s in subs:
                    walk(s, depth + 1)
                return
            txt = collect(sec)
            if len(txt) >= 30:
                book.chapters.append((title, txt))

        sections = [c for c in body if _local(c.tag) == "section"]
        if sections:
            for s in sections:
                walk(s)
        else:
            txt = collect(body)
            if txt:
                book.chapters.append(("", txt))

    _finish(book, path)
    return book


def read_fb2_zip(path):
    with zipfile.ZipFile(path) as z:
        name = next(n for n in z.namelist() if n.lower().endswith(".fb2"))
        return read_fb2(path, z.read(name))


# ----------------------------------------------------------------------------
# TXT
# ----------------------------------------------------------------------------

CHAPTER_RE = re.compile(
    r"^[ \t]*(?:(?:розділ|глава|частина|часть|раздел|книга|том|chapter|part)"
    r"[ \t]+(?:[0-9]+|[IVXLCDM]+|[а-яіїєґё'’]+)\b[^\n]{0,70}"
    r"|(?:пролог|епілог|эпилог|передмова|післямова|предисловие|послесловие|вступ|вступление)"
    r"[ \t.:!]*[^\n]{0,50})$",
    re.IGNORECASE | re.MULTILINE)


def split_text_chapters(text, lang):
    marks = [m for m in CHAPTER_RE.finditer(text) if len(m.group(0).strip()) <= 80]
    chapters = []
    if len(marks) >= 1:
        if text[:marks[0].start()].strip():
            chapters.append((intro_label(lang), text[:marks[0].start()]))
        for i, m in enumerate(marks):
            end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
            chapters.append((m.group(0).strip()[:80], text[m.start():end]))
    else:
        # без заголовків: ріжемо на шматки ~20 000 символів по абзацах
        buf, size = [], 0
        for para in text.split("\n"):
            buf.append(para)
            size += len(para)
            if size > 20000:
                chapters.append(("", "\n".join(buf)))
                buf, size = [], 0
        if "\n".join(buf).strip():
            chapters.append(("", "\n".join(buf)))
    return [(t, c) for t, c in chapters if c.strip()]


def read_txt(path):
    text = decode_bytes(open(path, "rb").read()).replace("\r\n", "\n")
    book = Book(lang=detect_lang(text))
    book.chapters = split_text_chapters(text, book.lang)
    _finish(book, path)
    return book


# ----------------------------------------------------------------------------
# PDF
# ----------------------------------------------------------------------------

def _reflow_pdf_text(text):
    """PDF дає рядок на кожен рядок сторінки. Склеюємо рядки в абзаци,
    прибираємо переноси слів і номери сторінок."""
    lines = [l.strip() for l in text.replace("\r", "").split("\n")]
    out, cur = [], ""
    for l in lines:
        if not l:
            if cur:
                out.append(cur)
                cur = ""
            continue
        if re.fullmatch(r"[-–—\s]*\d{1,4}[-–—\s]*", l):      # номер сторінки
            continue
        if not cur:
            cur = l
        elif cur.endswith("\xad"):
            cur = cur[:-1] + l                               # м'який перенос
        elif cur.endswith("-") and l[:1].islower():
            cur = cur[:-1] + l                               # пере-нос
        elif re.search(r"[.!?…:»\"]$", cur) and (l[:1].isupper() or l[:1] in "—–-«\""):
            out.append(cur)                                  # схоже на новий абзац
            cur = l
        else:
            cur += " " + l
    if cur:
        out.append(cur)
    return "\n".join(out).replace("\xad", "")


def read_pdf(path):
    import pymupdf

    book = Book()
    doc = pymupdf.open(path)
    meta = doc.metadata or {}
    book.title = (meta.get("title") or "").strip()
    book.author = (meta.get("author") or "").strip()
    pages = [doc[i].get_text("text") for i in range(doc.page_count)]
    toc = [(lvl, t, p) for lvl, t, p in doc.get_toc() if lvl == 1 and p >= 1]
    # обкладинка - перша сторінка картинкою
    try:
        book.cover = doc[0].get_pixmap(dpi=110).tobytes("jpeg")
    except Exception:
        book.cover = None
    doc.close()

    full = "\n".join(pages)
    book.lang = detect_lang(full)
    if len(toc) >= 2:
        toc.sort(key=lambda x: x[2])
        if toc[0][2] > 1:
            pre = _reflow_pdf_text("\n".join(pages[:toc[0][2] - 1]))
            if len(pre) > 200:
                book.chapters.append((intro_label(book.lang), pre))
        for i, (_, title, p) in enumerate(toc):
            end = toc[i + 1][2] - 1 if i + 1 < len(toc) else len(pages)
            txt = _reflow_pdf_text("\n".join(pages[p - 1:max(end, p)]))
            if len(txt) >= 30:
                book.chapters.append((title.strip(), txt))
    else:
        book.chapters = split_text_chapters(_reflow_pdf_text(full), book.lang)
    if not full.strip():
        raise ValueError("У PDF немає текстового шару (схоже на скан). Потрібне розпізнавання (OCR).")
    _finish(book, path)
    return book


# ----------------------------------------------------------------------------
# MOBI / AZW3
# ----------------------------------------------------------------------------

def read_mobi(path):
    import mobi
    tmpdir, filepath = mobi.extract(path)
    try:
        ext = os.path.splitext(filepath)[1].lower()
        if ext == ".epub":
            book = read_epub(filepath)
        elif ext == ".pdf":
            book = read_pdf(filepath)
        else:
            book = Book()
            p = _HtmlText(split_headings=True)
            p.feed(decode_bytes(open(filepath, "rb").read()))
            secs = [(h or "", t) for h, t in p.all_sections() if len(t) >= 30]
            # дрібні шматки (сторінки без заголовків) клеїмо до попереднього розділу
            for h, t in secs:
                if book.chapters and not h and len(t) < 3000:
                    ph, pt = book.chapters[-1]
                    book.chapters[-1] = (ph, pt + "\n" + t)
                else:
                    book.chapters.append((h, t))
            book.lang = detect_lang(" ".join(t for _, t in book.chapters))
            # обкладинка: найбільше зображення з "cover" в назві або просто перше
            imgs = []
            for d, _, files in os.walk(tmpdir):
                for f in files:
                    if f.lower().endswith((".jpg", ".jpeg", ".png", ".gif")):
                        fp = os.path.join(d, f)
                        imgs.append(("cover" not in f.lower(), f, fp))
            if imgs:
                imgs.sort()
                book.cover = open(imgs[0][2], "rb").read()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    if not book.title:
        book.title = ""
    _finish(book, path)
    return book


# ----------------------------------------------------------------------------

def _finish(book, path):
    if not book.title:
        name = os.path.basename(path)
        for ext in sorted(SUPPORTED, key=len, reverse=True):
            if name.lower().endswith(ext):
                name = name[:-len(ext)]
                break
        book.title = name
    # мова за самим текстом надійніша за метадані
    sample = " ".join(t for _, t in book.chapters[:5])
    if sample:
        book.lang = detect_lang(sample)
    book.chapters = [((t or part_label(book.lang, i)).strip()[:120], c)
                     for i, (t, c) in enumerate(book.chapters, 1)]


def read_book(path) -> Book:
    low = path.lower()
    if low.endswith(".fb2.zip"):
        return read_fb2_zip(path)
    ext = os.path.splitext(low)[1]
    if ext == ".epub":
        return read_epub(path)
    if ext == ".fb2":
        return read_fb2(path)
    if ext in (".mobi", ".azw", ".azw3", ".prc"):
        return read_mobi(path)
    if ext == ".pdf":
        return read_pdf(path)
    if ext == ".zip":
        with zipfile.ZipFile(path) as z:
            if any(n.lower().endswith(".fb2") for n in z.namelist()):
                return read_fb2_zip(path)
    return read_txt(path)
