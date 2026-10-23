"""Работа с текстом: кодировки, токенизация, стемминг (рус/англ), запросы FTS, сниппеты."""
from __future__ import annotations

import codecs
import html
import re

import snowballstemmer

_RU = snowballstemmer.stemmer("russian")
_EN = snowballstemmer.stemmer("english")

WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)
CYR_RE = re.compile(r"[Ѐ-ӿ]")
LAT_RE = re.compile(r"[A-Za-zÀ-ɏ]")
BAD_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

_stem_cache: dict[str, str] = {}


# ---------------------------------------------------------------- кодировки
_COMMON_RU = set("оеаинтсрвлкмдпуяыьгзбчйхжшюцщэфъ")


def _score_cyr(s: str) -> int:
    return sum(1 for ch in s if ch in _COMMON_RU)


def decode_bytes(data: bytes) -> tuple[str, str]:
    """Определить кодировку и декодировать. Возвращает (текст, кодировка)."""
    if data.startswith(codecs.BOM_UTF8):
        return data[3:].decode("utf-8", "replace"), "utf-8-sig"
    if data.startswith(codecs.BOM_UTF16_LE) or data.startswith(codecs.BOM_UTF16_BE):
        return data.decode("utf-16", "replace"), "utf-16"
    head = data[:4000]
    if head and head.count(0) > len(head) // 4:  # UTF-16 без BOM
        enc = "utf-16-le" if head[1::2].count(0) > head[0::2].count(0) else "utf-16-be"
        return data.decode(enc, "replace"), enc
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass
    sample = data[:300_000]
    high = sum(1 for b in sample if b >= 0x80)
    best_enc, best_score = "cp1251", -1
    for enc in ("cp1251", "koi8-r", "cp866", "mac-cyrillic"):
        sc = _score_cyr(sample.decode(enc, "replace"))
        if sc > best_score:
            best_enc, best_score = enc, sc
    if best_score < high * 0.4:  # кириллицы почти нет — западноевропейская
        best_enc = "cp1252"
    return data.decode(best_enc, "replace"), best_enc


def normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return BAD_CTRL_RE.sub("", text)


def detect_lang(text: str) -> str:
    sample = text[:20000]
    cyr = len(CYR_RE.findall(sample))
    lat = len(LAT_RE.findall(sample))
    return "ru" if cyr >= lat else "en"


# ---------------------------------------------------------------- стемминг
def stem(word: str) -> str:
    w = word.lower().replace("ё", "е")
    r = _stem_cache.get(w)
    if r is not None:
        return r
    if w.isdigit():
        r = w.lstrip("0") or "0"
    elif CYR_RE.search(w):
        r = _RU.stemWord(w)
    elif LAT_RE.search(w):
        r = _EN.stemWord(w)
    else:
        r = w
    if len(_stem_cache) < 2_000_000:
        _stem_cache[w] = r
    return r


def stem_text(text: str) -> str:
    return " ".join(stem(m) for m in WORD_RE.findall(text))


def plain_tokens(text: str) -> str:
    """Токены без стемминга (для кода книги)."""
    return " ".join(stem(m) if m.isdigit() else m.lower() for m in WORD_RE.findall(text))


# ---------------------------------------------------------------- запросы
_TERM_RE = re.compile(r'(-?)"([^"]*)"|(\S+)')

SCOPES = {
    "all": None,
    "title": "{title code}",
    "author": "{author}",
    "text": "{body}",
}


class Query:
    def __init__(self, fts: str | None, stems: set[str], prefixes: set[str]):
        self.fts = fts
        self.stems = stems
        self.prefixes = prefixes

    def matches(self, word: str) -> bool:
        s = stem(word)
        if s in self.stems:
            return True
        w = word.lower().replace("ё", "е")
        return any(w.startswith(p) for p in self.prefixes)


def build_query(q: str, scope: str = "all") -> Query:
    """Преобразовать пользовательский запрос в выражение FTS5.

    Поддерживается: слова (со стеммингом), "фразы", -исключение, префикс*,
    коды вида ab-421 (ищутся как фраза "ab 421").
    """
    pos: list[str] = []
    neg: list[str] = []
    stems: set[str] = set()
    prefixes: set[str] = set()
    for m in _TERM_RE.finditer(q or ""):
        if m.group(2) is not None:
            negate, body, is_phrase = m.group(1) == "-", m.group(2), True
        else:
            raw = m.group(3)
            negate = raw.startswith("-") and len(raw) > 1
            body = raw[1:] if negate else raw
            is_phrase = False
        prefix = (not is_phrase) and body.endswith("*")
        words = WORD_RE.findall(body)
        if not words:
            continue
        if prefix and len(words) == 1:
            p = words[0].lower().replace("ё", "е")
            expr = f'"{p}"*'
            if not negate:
                prefixes.add(p)
        else:
            st = [stem(w) for w in words]
            expr = '"' + " ".join(st) + '"'
            if not negate:
                stems.update(st)
        (neg if negate else pos).append(expr)
    if not pos:
        return Query(None, stems, prefixes)
    expr = " AND ".join(pos)
    for n in neg:
        expr = f"({expr}) NOT {n}"
    cols = SCOPES.get(scope)
    if cols:
        expr = f"{cols} : ({expr})"
    return Query(expr, stems, prefixes)


# ---------------------------------------------------------------- сниппеты
def make_snippet(text: str, query: Query, width: int = 260, max_scan: int = 3_000_000) -> str | None:
    """Фрагмент текста вокруг первого совпадения, с <mark>. HTML-экранирован."""
    if not (query.stems or query.prefixes):
        return None
    hit = None
    for m in WORD_RE.finditer(text, 0, min(len(text), max_scan)):
        if query.matches(m.group()):
            hit = m
            break
    if hit is None:
        return None
    start = max(0, hit.start() - width // 3)
    end = min(len(text), hit.end() + width)
    # выровнять по границам слов
    if start > 0:
        sp = text.find(" ", start)
        if 0 <= sp < hit.start():
            start = sp + 1
    if end < len(text):
        sp = text.rfind(" ", hit.end(), end)
        if sp > 0:
            end = sp
    frag = text[start:end]
    out, last = [], 0
    for m in WORD_RE.finditer(frag):
        if query.matches(m.group()):
            out.append(html.escape(frag[last:m.start()]))
            out.append("<mark>" + html.escape(m.group()) + "</mark>")
            last = m.end()
    out.append(html.escape(frag[last:]))
    s = "".join(out).replace("\n", " ")
    return ("… " if start > 0 else "") + s + (" …" if end < len(text) else "")


def highlight(s: str, query: Query) -> str:
    out, last = [], 0
    for m in WORD_RE.finditer(s):
        if query.matches(m.group()):
            out.append(html.escape(s[last:m.start()]))
            out.append("<mark>" + html.escape(m.group()) + "</mark>")
            last = m.end()
    out.append(html.escape(s[last:]))
    return "".join(out)


# ---------------------------------------------------------------- абзацы
def split_paragraphs(text: str) -> list[str]:
    """Разбить текст на абзацы. Учитывает и «строка = абзац», и жёсткий перенос строк."""
    lines = text.split("\n")
    nonempty = [ln for ln in lines if ln.strip()]
    if not nonempty:
        return []
    blank = len(lines) - len(nonempty)
    avg = sum(len(ln) for ln in nonempty) / len(nonempty)
    paras: list[str] = []
    if blank >= len(nonempty) * 0.08 and avg < 110:
        # абзацы разделены пустыми строками, внутри — перенос по ширине
        buf: list[str] = []
        for ln in lines:
            if ln.strip():
                buf.append(ln.strip())
            elif buf:
                paras.append(" ".join(buf))
                buf = []
        if buf:
            paras.append(" ".join(buf))
    elif avg < 90 and sum(1 for ln in nonempty if ln.startswith((" ", "\t"))) > len(nonempty) * 0.2:
        # абзац начинается с отступа
        buf = []
        for ln in lines:
            if not ln.strip():
                if buf:
                    paras.append(" ".join(buf)); buf = []
            elif ln.startswith((" ", "\t")) and buf:
                paras.append(" ".join(buf)); buf = [ln.strip()]
            else:
                buf.append(ln.strip())
        if buf:
            paras.append(" ".join(buf))
    else:
        paras = [ln.strip() for ln in nonempty]
    return [re.sub(r"[ \t]{2,}", " ", p) for p in paras]
