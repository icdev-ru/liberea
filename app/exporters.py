"""Экспорт книг и подборок в TXT, PDF, FB2."""
from __future__ import annotations

import base64
import io
import re
import uuid
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path
from typing import Callable

import warnings

from fpdf import FPDF

warnings.filterwarnings("ignore", message="Dimensions for page format")
from PIL import Image

from .config import CONFIG
from .importer import load_text
from .textproc import BAD_CTRL_RE, detect_lang, split_paragraphs

Progress = Callable[[int, int], None] | None


def safe_filename(s: str, maxlen: int = 150) -> str:
    s = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', " ", s)
    s = re.sub(r"\s+", " ", s).strip(" .")
    return (s[:maxlen].rstrip(" .") or "book")


def book_filename(b: dict, ext: str) -> str:
    base = f"{b['code']} {b['title']}" + (f" ({b['author']})" if b["author"] else "")
    if b.get("_variant") == "ru":
        base += " [RU]"
    return safe_filename(base) + "." + ext


def cover_path(b: dict) -> Path | None:
    if not b.get("cover_file"):
        return None
    if b.get("src") == "inpx":
        from .inpx import cover_path as inpx_cover
        return inpx_cover(b)
    p = CONFIG.library_root / b["dir_name"] / b["cover_file"]
    return p if p.exists() else None


def book_text(b: dict) -> str:
    return load_text(b)


# ====================================================================== TXT
def export_txt(b: dict, out: Path) -> None:
    out.write_text(book_text(b).replace("\n", "\r\n"), encoding="utf-8-sig")


# ====================================================================== PDF
def _image_for_pdf(path: Path):
    """Вернуть то, что примет fpdf2 (путь или BytesIO), и размер в пикселях."""
    with Image.open(path) as im:
        size = im.size
        if im.format in ("JPEG",) and im.mode in ("RGB", "L"):
            return str(path), size
        if im.format == "PNG":
            return str(path), size
        buf = io.BytesIO()
        im.convert("RGB").save(buf, "JPEG", quality=88)
        buf.seek(0)
        return buf, size


class BookPDF(FPDF):
    def __init__(self):
        super().__init__(format=CONFIG.page_format, unit="mm")
        small = CONFIG.page_format.upper() == "A5"
        self.margin = 13 if small else 20
        self.set_margins(self.margin, self.margin, self.margin)
        self.set_auto_page_break(True, margin=self.margin)
        self.add_font("body", "", str(CONFIG.font_regular))
        self.add_font("body", "B", str(CONFIG.font_bold))
        self.add_font("body", "I", str(CONFIG.font_italic))
        self.fs = CONFIG.font_size
        self.lh = self.fs * 0.3528 * 1.35  # высота строки в мм
        self.running_title = ""
        self.show_footer = False
        self.fresh_page = False  # текущая страница пуста (после оглавления)

    def new_page(self):
        if self.fresh_page:
            self.fresh_page = False
            self.set_xy(self.l_margin, self.t_margin)
        else:
            self.add_page()

    def header(self):
        if self.show_footer and self.running_title:
            self.set_font("body", "I", self.fs * 0.7)
            self.set_text_color(130)
            self.set_y(self.margin * 0.45)
            self.cell(0, 4, self.running_title[:90], align="C")
            self.set_text_color(0)
            self.set_y(self.margin)

    def footer(self):
        if self.show_footer:
            self.set_y(-self.margin * 0.75)
            self.set_font("body", "", self.fs * 0.75)
            self.set_text_color(110)
            self.cell(0, 4, str(self.page_no()), align="C")
            self.set_text_color(0)

    # -------------------------------------------------------------
    def cover_page(self, path: Path, section: str | None = None) -> None:
        self.show_footer = False
        self.new_page()
        if section:
            self.start_section(section, level=0)
        try:
            img, (w, h) = _image_for_pdf(path)
        except Exception:  # noqa: BLE001 — битая картинка
            return
        pw, ph = self.w - 16, self.h - 16
        scale = min(pw / w, ph / h)
        iw, ih = w * scale, h * scale
        self.image(img, x=(self.w - iw) / 2, y=(self.h - ih) / 2, w=iw, h=ih)

    @staticmethod
    def label(b: dict) -> str:
        return b["title"] + (f" — {b['author']}" if b["author"] else "")

    def book_body(self, b: dict, paragraphs: list[str], section: bool) -> None:
        self.show_footer = True
        self.running_title = ""
        self.new_page()
        if section:
            self.start_section(self.label(b), level=0)
        self.ln(self.lh * 2)
        self.set_font("body", "B", self.fs * 1.6)
        self.multi_cell(0, self.fs * 0.3528 * 2.1, b["title"], align="C", new_x="LMARGIN", new_y="NEXT")
        if b["author"]:
            self.ln(2)
            self.set_font("body", "I", self.fs * 1.15)
            self.multi_cell(0, self.lh, b["author"], align="C", new_x="LMARGIN", new_y="NEXT")
        if b["code"]:
            self.set_font("body", "", self.fs * 0.8)
            self.set_text_color(120)
            self.multi_cell(0, self.lh, b["code"], align="C", new_x="LMARGIN", new_y="NEXT")
            self.set_text_color(0)
        self.ln(self.lh * 2)
        self.running_title = b["title"] + (f" · {b['author']}" if b["author"] else "")
        self.set_font("body", "", self.fs)
        indent = "  "
        for p in paragraphs:
            self.multi_cell(0, self.lh, indent + p, align="J", new_x="LMARGIN", new_y="NEXT")
            self.ln(self.lh * 0.2)


def _render_toc(pdf: BookPDF, outline) -> None:
    pdf.show_footer = False
    pdf.set_xy(pdf.l_margin, pdf.t_margin)
    pdf.set_font("body", "B", pdf.fs * 1.4)
    pdf.cell(0, pdf.lh * 2, "Содержание", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(pdf.lh)
    pdf.set_font("body", "", pdf.fs * 0.95)
    usable = pdf.w - 2 * pdf.margin
    for i, sec in enumerate(outline, 1):
        link = pdf.add_link(page=sec.page_number)
        num = str(sec.page_number)
        name = f"{i}. {sec.name}"
        num_w = pdf.get_string_width(num) + 2
        max_w = usable - num_w - 4
        while pdf.get_string_width(name) > max_w and len(name) > 5:
            name = name[:-2]
        if name != f"{i}. {sec.name}":
            name = name.rstrip() + "…"
        y = pdf.get_y()
        pdf.cell(max_w, pdf.lh * 1.15, name, link=link)
        dots_w = usable - num_w - pdf.get_string_width(name)
        pdf.set_text_color(150)
        pdf.set_xy(pdf.margin + pdf.get_string_width(name) + 1, y)
        dot = pdf.get_string_width(".")
        pdf.cell(max(0, dots_w - 2), pdf.lh * 1.15, "." * max(0, int((dots_w - 3) / dot)), link=link)
        pdf.set_text_color(0)
        pdf.set_xy(pdf.w - pdf.margin - num_w, y)
        pdf.cell(num_w, pdf.lh * 1.15, num, align="R", link=link, new_x="LMARGIN", new_y="NEXT")


def export_pdf(books: list[dict], out: Path, title: str | None = None, progress: Progress = None) -> None:
    """Одна книга (title=None) или подборка с титулом и содержанием."""
    pdf = BookPDF()
    pdf.set_creator("Liberea")
    is_coll = title is not None
    if is_coll:
        pdf.set_title(title)
        pdf.add_page()
        pdf.ln(pdf.h * 0.3)
        pdf.set_font("body", "B", pdf.fs * 2.2)
        pdf.multi_cell(0, pdf.fs * 0.3528 * 3, title, align="C", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(pdf.lh)
        pdf.set_font("body", "I", pdf.fs)
        pdf.multi_cell(0, pdf.lh, f"Книг: {len(books)} · {date.today():%d.%m.%Y}", align="C", new_x="LMARGIN", new_y="NEXT")
        per_page = max(10, int((pdf.h - 2 * pdf.margin) / (pdf.lh * 1.15)) - 3)
        pdf.add_page()
        pdf.insert_toc_placeholder(_render_toc, pages=max(1, -(-len(books) // per_page)),
                                   allow_extra_pages=True)
        pdf.fresh_page = True
    else:
        b = books[0]
        pdf.set_title(b["title"])
        pdf.set_author(b["author"])
    for i, b in enumerate(books, 1):
        cp = cover_path(b)
        if cp:
            pdf.cover_page(cp, section=pdf.label(b) if is_coll else None)
        pdf.book_body(b, split_paragraphs(book_text(b)), section=is_coll and not cp)
        if progress:
            progress(i, len(books))
    pdf.output(str(out))


# ====================================================================== FB2
FB_NS = "http://www.gribuser.ru/xml/fictionbook/2.0"
XL_NS = "http://www.w3.org/1999/xlink"
ET.register_namespace("", FB_NS)
ET.register_namespace("l", XL_NS)


def _x(s: str) -> str:
    return BAD_CTRL_RE.sub("", s or "")


def _el(parent, tag, text=None, **attrs):
    e = ET.SubElement(parent, f"{{{FB_NS}}}{tag}", {k: v for k, v in attrs.items()})
    if text is not None:
        e.text = _x(text)
    return e


def _author(parent, name: str):
    a = _el(parent, "author")
    parts = name.split()
    if len(parts) >= 2:
        _el(a, "first-name", " ".join(parts[:-1]))
        _el(a, "last-name", parts[-1])
    else:
        _el(a, "nickname", name or "Неизвестный автор")


def _binary(root, bid: str, path: Path) -> bool:
    try:
        with Image.open(path) as im:
            if im.format == "JPEG" and im.mode in ("RGB", "L"):
                data, ctype = path.read_bytes(), "image/jpeg"
            elif im.format == "PNG":
                data, ctype = path.read_bytes(), "image/png"
            else:
                buf = io.BytesIO()
                im.convert("RGB").save(buf, "JPEG", quality=88)
                data, ctype = buf.getvalue(), "image/jpeg"
    except Exception:  # noqa: BLE001
        return False
    e = _el(root, "binary", None, **{"id": bid, "content-type": ctype})
    e.text = base64.encodebytes(data).decode("ascii")
    return True


def export_fb2(books: list[dict], out: Path, title: str | None = None, progress: Progress = None) -> None:
    is_coll = title is not None
    root = ET.Element(f"{{{FB_NS}}}FictionBook")
    desc = _el(root, "description")
    ti = _el(desc, "title-info")
    _el(ti, "genre", CONFIG.fb2_genre)
    authors = list(dict.fromkeys(b["author"] for b in books if b["author"])) or [""]
    for a in authors[:20]:
        _author(ti, a)
    _el(ti, "book-title", title if is_coll else books[0]["title"])

    covers: dict[int, str] = {}
    binaries = []
    for b in books:
        cp = cover_path(b)
        if cp:
            covers[b["id"]] = f"cover{b['id']}"
            binaries.append((covers[b["id"]], cp))
    first_cover = covers.get(books[0]["id"]) if books else None
    if first_cover:
        cpg = _el(ti, "coverpage")
        _el(cpg, "image", None, **{f"{{{XL_NS}}}href": "#" + first_cover})
    texts = [book_text(b) for b in books] if len(books) <= 3 else None
    lang = detect_lang(texts[0]) if texts else "ru"
    _el(ti, "lang", lang)
    di = _el(desc, "document-info")
    _author(di, "Liberea")
    _el(di, "program-used", "Liberea")
    _el(di, "date", date.today().isoformat(), value=date.today().isoformat())
    _el(di, "id", str(uuid.uuid4()))
    _el(di, "version", "1.0")

    body = _el(root, "body")
    if is_coll:
        t = _el(body, "title")
        _el(t, "p", title)
        _el(t, "p", f"Книг: {len(books)}")
    for i, b in enumerate(books):
        sec = _el(body, "section") if is_coll else body
        if not is_coll:
            t = _el(body, "title")
            _el(t, "p", b["title"])
            if b["author"]:
                _el(t, "p", b["author"])
            sec = _el(body, "section")
        else:
            t = _el(sec, "title")
            _el(t, "p", b["title"])
            if b["author"]:
                _el(t, "p", b["author"])
        if b["id"] in covers:
            _el(sec, "image", None, **{f"{{{XL_NS}}}href": "#" + covers[b["id"]]})
        text = texts[i] if texts else book_text(b)
        paras = split_paragraphs(text)
        if not paras:
            _el(sec, "empty-line")
        for p in paras:
            _el(sec, "p", p)
        if progress:
            progress(i + 1, len(books))

    for bid, path in binaries:
        _binary(root, bid, path)

    tree = ET.ElementTree(root)
    with open(out, "wb") as f:
        tree.write(f, encoding="utf-8", xml_declaration=True)

