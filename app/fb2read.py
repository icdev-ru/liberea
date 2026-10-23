"""Чтение FB2: чистый текст и метаданные (название, авторы, язык)."""
from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET

from .textproc import decode_bytes, normalize_text

_BINARY_RE = re.compile(rb"<binary[^>]*>.*?</binary>", re.S | re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_ENC_RE = re.compile(rb'<\?xml[^>]*encoding=["\']([\w\-]+)["\']', re.I)

# элементы, после которых нужен перевод строки
_BLOCK = {"p", "v", "subtitle", "text-author", "empty-line", "title", "epigraph", "stanza", "cite", "poem", "section"}


def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _text_of(el) -> str:
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip()


def _find(el, path: list[str]):
    cur = el
    for name in path:
        if cur is None:
            return None
        cur = next((c for c in cur if _local(c.tag) == name), None)
    return cur


def _authors(title_info) -> list[str]:
    out = []
    if title_info is None:
        return out
    for a in title_info:
        if _local(a.tag) != "author":
            continue
        parts = {}
        for c in a:
            parts[_local(c.tag)] = _text_of(c)
        name = " ".join(x for x in (parts.get("first-name"), parts.get("middle-name"), parts.get("last-name")) if x)
        name = name or parts.get("nickname", "")
        if name:
            out.append(name)
    return out


def _body_lines(body) -> list[str]:
    lines: list[str] = []

    def walk(el):
        name = _local(el.tag)
        if name in ("p", "v", "subtitle", "text-author"):
            t = _text_of(el)
            if t:
                lines.append(t)
            return
        if name == "empty-line":
            lines.append("")
            return
        for c in el:
            walk(c)
        if name in ("title", "stanza", "epigraph"):
            lines.append("")

    walk(body)
    return lines


def parse_fb2(data: bytes) -> dict:
    """Вернуть {'text', 'title', 'authors', 'lang', 'encoding'}. Битые файлы читаются «грубо»."""
    stripped = _BINARY_RE.sub(b"", data)
    m = _ENC_RE.search(stripped[:300])
    enc = (m.group(1).decode("ascii", "ignore").lower() if m else "utf-8")
    try:
        root = ET.fromstring(stripped)
        desc = _find(root, ["description", "title-info"])
        title_el = _find(desc, ["book-title"]) if desc is not None else None
        lang_el = _find(desc, ["lang"]) if desc is not None else None
        lines: list[str] = []
        for body in root:
            if _local(body.tag) != "body":
                continue
            if body.get("name") in ("notes", "comments") and lines:
                lines.append("")
                lines.append("Примечания")
            lines.extend(_body_lines(body))
        text = "\n".join(lines)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        return {"text": normalize_text(text), "title": _text_of(title_el) if title_el is not None else "",
                "authors": _authors(desc), "lang": _text_of(lang_el) if lang_el is not None else "",
                "encoding": enc}
    except ET.ParseError:
        pass
    # запасной путь: снять теги регулярными выражениями
    raw, enc2 = decode_bytes(stripped)
    head = raw[:20000]
    authors = []
    for a in re.findall(r"<author>(.*?)</author>", head, re.S | re.I)[:5]:
        f = re.search(r"<first-name>(.*?)</first-name>", a, re.S)
        l_ = re.search(r"<last-name>(.*?)</last-name>", a, re.S)
        n = " ".join(x.group(1).strip() for x in (f, l_) if x)
        if n:
            authors.append(html.unescape(n))
    t = re.search(r"<book-title>(.*?)</book-title>", head, re.S | re.I)
    mb = re.search(r"<body[^>]*>", raw, re.I)
    body = raw[mb.end():] if mb else raw
    body = re.sub(r"</(p|v|subtitle|title|section)>", "\n", body, flags=re.I)
    text = html.unescape(_TAG_RE.sub("", body))
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip()
    return {"text": normalize_text(text), "title": html.unescape(t.group(1).strip()) if t else "",
            "authors": authors, "lang": "", "encoding": enc2 + " (fb2, повреждён)"}
