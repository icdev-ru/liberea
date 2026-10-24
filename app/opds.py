"""OPDS 1.2 — каталог для читалок (FBReader, Moon+ Reader, CoolReader, KOReader, ReadEra…).

Адрес каталога: http://<сервер>:<порт>/opds
Разделы: поиск, авторы (по алфавиту), серии (по алфавиту), жанры, новые поступления.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from urllib.parse import urlencode
from xml.sax.saxutils import escape, quoteattr

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from . import catalog
from .config import CONFIG
from .db import get_conn
from .genres import GENRES, GROUPS, OTHER_GROUP, genre_name
from .search import TR, search

router = APIRouter(prefix="/opds")
PER = 50
NAV = "application/atom+xml;profile=opds-catalog;kind=navigation"
ACQ = "application/atom+xml;profile=opds-catalog;kind=acquisition"
MIME = {"fb2": "application/x-fictionbook+xml", "pdf": "application/pdf", "txt": "text/plain"}
LIST_LIMIT = 100        # сколько имён показывать списком; больше — дробим по буквам
NEW_EXPR = "coalesce(b.pubdate, substr(b.added_at, 1, 10))"


# ====================================================================== XML
def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ts(s: str | None) -> str:
    if not s:
        return _now()
    s = s.strip().replace(" ", "T")
    return (s if "T" in s else s + "T00:00:00") + "Z"


class Feed:
    def __init__(self, request: Request, fid: str, title: str, kind: str = NAV, up: str | None = "/opds"):
        self.base = str(request.base_url).rstrip("/")
        self.request = request
        self.kind = kind
        self.parts = [
            '<?xml version="1.0" encoding="utf-8"?>',
            '<feed xmlns="http://www.w3.org/2005/Atom" xmlns:dc="http://purl.org/dc/terms/" '
            'xmlns:opds="http://opds-spec.org/2010/catalog" xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">',
            f"<id>urn:liberea:{escape(fid)}</id>",
            f"<title>{escape(title)}</title>",
            f"<updated>{_now()}</updated>",
            f"<author><name>{escape(CONFIG.site_title)}</name></author>",
            self.link("self", str(request.url.path) + (("?" + request.url.query) if request.url.query else ""), kind),
            self.link("start", "/opds", NAV),
            self.link("search", "/opds/opensearch.xml", "application/opensearchdescription+xml"),
            self.link("search", "/opds/search?q={searchTerms}", "application/atom+xml", raw=True),
        ]
        if up:
            self.parts.append(self.link("up", up, NAV))

    def url(self, path: str) -> str:
        return path if path.startswith("http") else self.base + path

    def link(self, rel: str, href: str, typ: str, title: str | None = None, raw: bool = False, extra: str = "") -> str:
        h = self.url(href) if not raw else self.base + href
        t = f" title={quoteattr(title)}" if title else ""
        return f'<link rel="{rel}" href={quoteattr(h)} type="{typ}"{t}{extra}/>'

    def nav(self, eid: str, title: str, href: str, text: str = "", kind: str = NAV, count: int | None = None):
        self.parts.append(
            f"<entry><title>{escape(title)}</title><id>urn:liberea:{escape(eid)}</id><updated>{_now()}</updated>"
            + (f'<content type="text">{escape(text)}</content>' if text else "")
            + self.link("subsection", href, kind) + "</entry>")

    def book(self, b: dict):
        bid = b["id"]
        title = b["title"] or "Без названия"
        if b.get("series"):
            title_note = f'{b["series"]}' + (f' #{b["serno"]}' if b.get("serno") else "")
        else:
            title_note = ""
        e = [f"<entry><title>{escape(title)}</title><id>urn:liberea:book:{bid}</id>"
             f"<updated>{_ts(b.get('updated_at'))}</updated>"]
        for a in catalog.split_authors(b.get("author")):
            e.append(f"<author><name>{escape(a)}</name><uri>{escape(self.url('/opds/author?' + urlencode({'name': a})))}</uri></author>")
        for g in (b.get("genre") or "").split(":"):
            if g:
                e.append(f"<category term={quoteattr(g)} label={quoteattr(genre_name(g))}/>")
        if b.get("lang"):
            e.append(f"<dc:language>{escape(b['lang'])}</dc:language>")
        if b.get("pubdate"):
            e.append(f"<dc:issued>{escape(b['pubdate'])}</dc:issued>")
        info = []
        if title_note:
            info.append("Серия: " + title_note)
        if b.get("author"):
            info.append("Автор: " + b["author"])
        if b.get("genre"):
            info.append("Жанр: " + ", ".join(genre_name(g) for g in b["genre"].split(":") if g))
        info.append("Код: " + (b.get("code") or str(bid)))
        if b.get("txt_size"):
            n = b["txt_size"]
            info.append(f"Размер: {n / 1048576:.1f} МБ" if n >= 1048576 else f"Размер: {max(1, n >> 10)} КБ")
        has_ru = "done" in (b.get("tr_status"), b.get("llm_status"))
        if has_ru:
            info.append("Есть перевод на русский")
        e.append(f'<content type="text">{escape(". ".join(info))}</content>')
        if b.get("cover_file"):
            e.append(self.link("http://opds-spec.org/image", f"/cover/{bid}", "image/jpeg"))
            e.append(self.link("http://opds-spec.org/image/thumbnail", f"/cover/{bid}?thumb=1", "image/jpeg"))
        for fmt in ("fb2", "pdf", "txt"):
            e.append(self.link("http://opds-spec.org/acquisition/open-access", f"/book/{bid}/download.{fmt}",
                               MIME[fmt], title=fmt.upper()))
        if has_ru:
            for fmt in ("fb2", "pdf", "txt"):
                e.append(self.link("http://opds-spec.org/acquisition/open-access",
                                   f"/book/{bid}/download.{fmt}?ru=1", MIME[fmt], title=f"Перевод {fmt.upper()}"))
        e.append(self.link("alternate", f"/book/{bid}", "text/html", title="Страница книги"))
        for a in catalog.split_authors(b.get("author"))[:3]:
            e.append(self.link("related", "/opds/author?" + urlencode({"name": a}), NAV, title=f"Все книги: {a}"))
        if b.get("series"):
            e.append(self.link("related", "/opds/series?" + urlencode({"name": b["series"]}), ACQ,
                               title=f"Серия: {b['series']}"))
        e.append("</entry>")
        self.parts.append("".join(e))

    def pages(self, path: str, params: dict, page: int, total: int, per: int = PER):
        pages = max(1, math.ceil(total / per))
        self.parts.append(f"<opensearch:totalResults>{total}</opensearch:totalResults>"
                          f"<opensearch:itemsPerPage>{per}</opensearch:itemsPerPage>"
                          f"<opensearch:startIndex>{(page - 1) * per + 1}</opensearch:startIndex>")

        def u(p):
            return path + "?" + urlencode({**params, "page": p})
        if page > 1:
            self.parts.append(self.link("first", u(1), self.kind))
            self.parts.append(self.link("previous", u(page - 1), self.kind))
        if page < pages:
            self.parts.append(self.link("next", u(page + 1), self.kind))
            self.parts.append(self.link("last", u(pages), self.kind))

    def response(self) -> Response:
        # ссылки и метаданные должны идти до записей — переставляем
        head = [p for p in self.parts if not p.startswith("<entry>")]
        entries = [p for p in self.parts if p.startswith("<entry>")]
        xml = "\n".join(head + entries + ["</feed>"])
        if _is_browser(self.request):
            return Response(feed_to_html(xml), media_type="text/html;charset=utf-8")
        return Response(xml, media_type=self.kind + ";charset=utf-8")


# ====================================================================== просмотр в браузере
def _is_browser(request: Request) -> bool:
    """Обычный браузер (а не читалка) — показать каталог страницей HTML. ?format=xml — всегда XML."""
    if request.query_params.get("format") == "xml":
        return False
    acc = request.headers.get("accept", "")
    return "text/html" in acc and "application/xhtml+xml" in acc


def feed_to_html(xml: str) -> str:
    import xml.etree.ElementTree as ET
    ns = {"a": "http://www.w3.org/2005/Atom", "os": "http://a9.com/-/spec/opensearch/1.1/"}
    root = ET.fromstring(xml.encode("utf-8"))

    def links(el):
        return [(lk.get("rel", ""), lk.get("href", ""), lk.get("type", ""), lk.get("title", ""))
                for lk in el.findall("a:link", ns)]

    def path(href: str) -> str:  # ссылки — относительно сайта
        i = href.find("/", href.find("//") + 2) if "//" in href else 0
        return href[i:] if i > 0 else href

    title = root.findtext("a:title", "", ns)
    fl = links(root)
    up = next((h for r, h, _t, _ti in fl if r == "up"), None)
    nav = [(lbl, next((h for r, h, _t, _ti in fl if r == rel), None))
           for rel, lbl in (("first", "« в начало"), ("previous", "← назад"), ("next", "дальше →"))]
    total = root.findtext("os:totalResults", "", ns)
    out = ['<!doctype html><html lang="ru"><head><meta charset="utf-8">'
           '<meta name="viewport" content="width=device-width, initial-scale=1">',
           f"<title>{escape(title)} — OPDS</title>",
           '<link rel="stylesheet" href="/static/style.css"><style>'
           '.opds{max-width:900px;margin:0 auto;padding:16px}.opds-e{display:flex;gap:12px;padding:10px 0;'
           'border-bottom:1px solid var(--line)}.opds-e img{width:56px;height:80px;object-fit:cover;border-radius:4px}'
           '.opds-e .t{font:600 1.02rem var(--serif)}.opds-e .c{color:var(--muted);font-size:.88rem}'
           '.opds-e .d a{margin-right:6px}.opds-note{font-size:.85rem;color:var(--muted)}</style></head>'
           '<body><main class="opds">',
           '<p class="opds-note">Это каталог OPDS для читалок (FBReader, Moon+ Reader, KOReader…): добавьте в читалку адрес '
           '<code>…/opds</code>. Здесь он показан для проверки. <a href="/">Обычный сайт →</a></p>',
           '<form action="/opds/search" method="get" class="inline-form">'
           '<input type="search" name="q" placeholder="Поиск в каталоге"><button>Найти</button></form>',
           '<p>' + (f'<a href="{escape(path(up))}">↑ наверх</a> · ' if up else "") + '<a href="/opds">каталог</a></p>',
           f"<h1>{escape(title)}</h1>" + (f'<p class="muted">Всего: {escape(total)}</p>' if total else "")]
    for e in root.findall("a:entry", ns):
        el = links(e)
        t = e.findtext("a:title", "", ns)
        content = e.findtext("a:content", "", ns)
        sub = next((h for r, h, _t, _ti in el if r in ("subsection", "") and "atom+xml" in _t), None)
        thumb = next((h for r, h, _t, _ti in el if r.endswith("image/thumbnail")), None)
        acq = [(h, ti or _t) for r, h, _t, ti in el if "acquisition" in r]
        rel = [(h, ti) for r, h, _t, ti in el if r == "related"]
        authors = ", ".join(a.findtext("a:name", "", ns) for a in e.findall("a:author", ns))
        out.append('<div class="opds-e">')
        if thumb:
            out.append(f'<img loading="lazy" src="{escape(path(thumb))}" alt="" onerror="this.remove()">')
        out.append("<div>")
        out.append(f'<div class="t"><a href="{escape(path(sub))}">{escape(t)}</a></div>' if sub
                   else f'<div class="t">{escape(t)}</div>')
        if authors:
            out.append(f'<div class="c">{escape(authors)}</div>')
        if content:
            out.append(f'<div class="c">{escape(content)}</div>')
        if acq:
            out.append('<div class="d dl">' + "".join(f'<a href="{escape(path(h))}">{escape(ti)}</a>' for h, ti in acq)
                       + "</div>")
        if rel:
            out.append('<div class="c">' + " · ".join(f'<a href="{escape(path(h))}">{escape(ti)}</a>' for h, ti in rel)
                       + "</div>")
        out.append("</div></div>")
    pg = " · ".join(f'<a href="{escape(path(h))}">{lbl}</a>' for lbl, h in nav if h)
    if pg:
        out.append(f'<p class="pager">{pg}</p>')
    out.append("</main></body></html>")
    return "".join(out)


def _books_feed(request, fid, title, books, total, page, path, params, up="/opds"):
    f = Feed(request, fid, title, ACQ, up)
    for b in books:
        f.book(b)
    f.pages(path, params, page, total)
    return f.response()


# ====================================================================== корень и поиск
@router.get("")
@router.get("/")
def root(request: Request):
    f = Feed(request, "root", CONFIG.site_title, NAV, up=None)
    f.nav("authors", "Авторы", "/opds/authors", "По алфавиту")
    f.nav("series", "Серии", "/opds/series-index", "По алфавиту")
    with get_conn() as conn:
        if conn.execute("SELECT 1 FROM genres LIMIT 1").fetchone():
            f.nav("genres", "Жанры", "/opds/genres", "Все жанры и поджанры")
    f.nav("new", "Новые поступления", "/opds/new", "Сначала самые новые", ACQ)
    return f.response()


@router.get("/opensearch.xml")
def opensearch(request: Request):
    base = str(request.base_url).rstrip("/")
    xml = ('<?xml version="1.0" encoding="utf-8"?>'
           '<OpenSearchDescription xmlns="http://a9.com/-/spec/opensearch/1.1/">'
           f"<ShortName>{escape(CONFIG.site_title)}</ShortName>"
           f"<Description>Поиск по библиотеке {escape(CONFIG.site_title)}</Description>"
           "<InputEncoding>UTF-8</InputEncoding><OutputEncoding>UTF-8</OutputEncoding>"
           f'<Url type="{ACQ}" template="{escape(base)}/opds/search?q={{searchTerms}}"/>'
           f'<Url type="application/atom+xml" template="{escape(base)}/opds/search?q={{searchTerms}}"/>'
           "</OpenSearchDescription>")
    return Response(xml, media_type="application/opensearchdescription+xml;charset=utf-8")


@router.get("/search")
def opds_search(request: Request, q: str = "", searchTerms: str = ""):
    q = (q or searchTerms).strip()
    if not q or q == "{searchTerms}":
        f = Feed(request, "search", "Поиск", NAV)
        return f.response()
    with get_conn() as conn:
        _b, nb, _ = search(conn, q, "all", 1, 1, with_snippets=False)
        _a, na = catalog.find(conn, "author", q, 1, 1)
        _s, ns = catalog.find(conn, "series", q, 1, 1)
    qs = urlencode({"q": q})
    if not na and not ns:
        return RedirectResponse(f"/opds/search/books?{qs}", 302)
    f = Feed(request, "search:" + q, f"Поиск: {q}", NAV)
    if nb:
        f.nav("sb:" + q, f"Книги: {nb}", f"/opds/search/books?{qs}", "Книги, где слова есть в названии, авторе, серии, тексте", ACQ, nb)
    if na:
        f.nav("sa:" + q, f"Авторы: {na}", f"/opds/search/authors?{qs}", "Авторы с таким именем", NAV, na)
    if ns:
        f.nav("ss:" + q, f"Серии: {ns}", f"/opds/search/series?{qs}", "Серии с таким названием", NAV, ns)
    return f.response()


@router.get("/search/books")
def search_books(request: Request, q: str, page: int = 1):
    with get_conn() as conn:
        books, total, _ = search(conn, q, "all", page, PER, with_snippets=False)
    return _books_feed(request, "sb:" + q, f"Книги: {q}", books, total, page, "/opds/search/books", {"q": q},
                       up="/opds/search?" + urlencode({"q": q}))


@router.get("/search/{kind}")
def search_names(request: Request, kind: str, q: str, page: int = 1):
    if kind not in ("authors", "series"):
        raise HTTPException(404)
    k = "author" if kind == "authors" else "series"
    with get_conn() as conn:
        rows, total = catalog.find(conn, k, q, page, PER)
    f = Feed(request, f"s{k}:{q}", ("Авторы: " if k == "author" else "Серии: ") + q, NAV,
             up="/opds/search?" + urlencode({"q": q}))
    _name_entries(f, k, rows)
    f.pages(f"/opds/search/{kind}", {"q": q}, page, total)
    return f.response()


def _name_entries(f: Feed, kind: str, rows):
    for r in rows:
        if kind == "author":
            f.nav("a:" + r["name"], r["name"], "/opds/author?" + urlencode({"name": r["name"]}),
                  _count(r["n"]), NAV, r["n"])
        else:
            f.nav("s:" + r["name"], r["name"], "/opds/series?" + urlencode({"name": r["name"]}),
                  _count(r["n"]) + (f" · {r['authors']}" if r.get("authors") else ""), ACQ, r["n"])


def _count(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        w = "книга"
    elif n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        w = "книги"
    else:
        w = "книг"
    return f"{n} {w}"


# ====================================================================== алфавитные указатели
def _index(request: Request, table: str, p: str, path: str, title: str, other: int = 0):
    """Указатель по первым буквам: пока под префиксом много имён — дробим дальше."""
    kind = "author" if table == "authors" else "series"
    cnt = catalog._cnt_expr("")
    p = p.lower()
    hi = p + "\U0010ffff"
    where = f"{cnt} > 0" + (" AND t.sort >= ? AND t.sort < ?" if p else "")
    args = (p, hi) if p else ()
    if other:
        with get_conn() as conn:
            rows = [dict(r) for r in conn.execute(f"SELECT t.*, {cnt} AS n FROM {table} t WHERE {cnt} > 0 "
                                                  f"ORDER BY t.sort LIMIT 2000")]
        f = Feed(request, f"{table}-index:#", title + ": цифры и символы", NAV, up=path)
        _name_entries(f, kind, [r for r in rows if not r["sort"][:1].isalpha()][:500])
        return f.response()
    with get_conn() as conn:
        total = conn.execute(f"SELECT count(*) FROM {table} t WHERE {where}", args).fetchone()[0]
        up = path + ("?" + urlencode({"p": p[:-1]}) if len(p) > 1 else "") if p else "/opds"
        f = Feed(request, f"{table}-index:{p}", title + (f": {p.upper()}…" if p else ""), NAV, up=up)
        if p and (total <= LIST_LIMIT or len(p) >= 6):
            rows = conn.execute(f"SELECT t.*, {cnt} AS n FROM {table} t WHERE {where} ORDER BY t.sort LIMIT 500",
                                args).fetchall()
            _name_entries(f, kind, [dict(r) for r in rows])
            return f.response()
        groups = conn.execute(
            f"SELECT substr(t.sort, 1, {len(p) + 1}) AS pre, count(*) FROM {table} t WHERE {where} "
            f"GROUP BY pre ORDER BY pre", args).fetchall()
        # группы — только по буквам; имена, где после префикса пробел/точка/дефис, показываем сразу,
        # а всё, что начинается не с буквы, — одной группой «#»
        direct, other = [], 0
        # кириллица раньше латиницы
        groups = sorted(groups, key=lambda g: (0 if g[0] and "\u0400" <= g[0][-1] <= "\u04ff" else 1, g[0] or ""))
        for pre, n in groups:
            if not pre:
                continue
            last = pre[-1]
            if not last.isalpha():
                if p:
                    direct += [dict(r) for r in conn.execute(
                        f"SELECT t.*, {cnt} AS n FROM {table} t WHERE {cnt} > 0 AND t.sort >= ? AND t.sort < ? "
                        f"ORDER BY t.sort LIMIT 100", (pre, pre + "\U0010ffff"))]
                else:
                    other += n
                continue
            label = pre[:1].upper() + pre[1:]
            f.nav(f"{table}-index:{pre}", label + "…", path + "?" + urlencode({"p": pre}),
                  f"{n} " + ("авторов" if kind == "author" else "серий"), NAV, n)
        if other:
            f.nav(f"{table}-index:#", "# (цифры и символы)", path + "?" + urlencode({"other": 1}),
                  f"{other} " + ("авторов" if kind == "author" else "серий"), NAV, other)
        _name_entries(f, kind, direct)
    return f.response()


@router.get("/authors")
def authors_index(request: Request, p: str = "", other: int = 0):
    return _index(request, "authors", p, "/opds/authors", "Авторы", other)


@router.get("/series-index")
def series_index(request: Request, p: str = "", other: int = 0):
    return _index(request, "series", p, "/opds/series-index", "Серии", other)


# ====================================================================== автор, серия
@router.get("/author")
def author(request: Request, name: str):
    with get_conn() as conn:
        books = catalog.author_books(conn, name)
    groups = catalog.group_by_series(books)
    series = [(s, bs) for s, bs in groups if s]
    if not series:
        return author_books(request, name)
    f = Feed(request, "a:" + name, name, NAV, up="/opds/authors")
    f.nav("ab:" + name, f"Все книги ({len(books)})", "/opds/author/books?" + urlencode({"name": name}),
          "Сначала по сериям, затем остальные", ACQ, len(books))
    loose = next((bs for s, bs in groups if s is None), [])
    if loose:
        f.nav("al:" + name, f"Вне серий ({len(loose)})",
              "/opds/author/books?" + urlencode({"name": name, "loose": 1}), "", ACQ, len(loose))
    for s, bs in series:
        f.nav("s:" + s, f"Серия: {s}", "/opds/series?" + urlencode({"name": s}), _count(len(bs)), ACQ, len(bs))
    return f.response()


@router.get("/author/books")
def author_books(request: Request, name: str, loose: int = 0, page: int = 1):
    with get_conn() as conn:
        books = catalog.author_books(conn, name)
    flat = [b for s, bs in catalog.group_by_series(books) for b in bs if not loose or s is None]
    chunk = catalog.decorate_all(flat[(page - 1) * PER: page * PER])
    params = {"name": name, **({"loose": 1} if loose else {})}
    return _books_feed(request, "ab:" + name, name + (" — вне серий" if loose else ""), chunk, len(flat), page,
                       "/opds/author/books", params, up="/opds/author?" + urlencode({"name": name}))


@router.get("/series")
def series(request: Request, name: str, page: int = 1):
    with get_conn() as conn:
        books = catalog.series_books(conn, name)
    chunk = catalog.decorate_all(books[(page - 1) * PER: page * PER])
    return _books_feed(request, "s:" + name, f"Серия: {name}", chunk, len(books), page, "/opds/series",
                       {"name": name}, up="/opds/series-index")


# ====================================================================== жанры, новинки
@router.get("/genres")
def genres(request: Request, group: int = 0):
    with get_conn() as conn:
        groups = catalog.genre_groups(conn)
    if not group:
        f = Feed(request, "genres", "Жанры", NAV)
        for i, (title, items, total) in enumerate(groups, 1):
            f.nav(f"gg:{i}", title, f"/opds/genres?group={i}", f"{len(items)} жанров, {_count(total)}", NAV, total)
        return f.response()
    if not 1 <= group <= len(groups):
        raise HTTPException(404)
    title, items, _total = groups[group - 1]
    f = Feed(request, f"gg:{group}", title, NAV, up="/opds/genres")
    for g in items:
        f.nav("g:" + g["code"], g["name"], f"/opds/genre/{g['code']}", _count(g["n"]), ACQ, g["n"])
    return f.response()


@router.get("/genre/{code}")
def genre(request: Request, code: str, page: int = 1, sort: str = "date"):
    with get_conn() as conn:
        books, total = catalog.genre_books(conn, code, sort, page, PER)
    return _books_feed(request, "g:" + code, genre_name(code), books, total, page, f"/opds/genre/{code}",
                       {"sort": sort}, up="/opds/genres")


@router.get("/new")
def new(request: Request, page: int = 1):
    with get_conn() as conn:
        where = f"b.hidden=0 AND {CONFIG.src_sql()}"
        total = conn.execute(f"SELECT count(*) FROM books b WHERE {where}").fetchone()[0]
        total = min(total, 1000)
        rows = conn.execute(f"SELECT b.*, {TR} FROM books b INDEXED BY ix_books_new WHERE {where} "
                            f"ORDER BY {NEW_EXPR} DESC "
                            f"LIMIT ? OFFSET ?", (PER, (page - 1) * PER)).fetchall() if page * PER <= 1000 + PER else []
    return _books_feed(request, "new", "Новые поступления", catalog.decorate_all([dict(r) for r in rows]),
                       total, page, "/opds/new", {})


__all__ = ["router", "GENRES", "GROUPS", "OTHER_GROUP"]
