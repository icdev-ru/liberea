"""Справочники авторов и серий: поиск «по автору» / «по серии» выдаёт их, а не книги.

Таблицы authors/series пересобираются целиком после каждого сканирования и правки книги
(600 тыс. книг — несколько секунд). Индексы authors_fts2/series_fts2 хранят слова как есть (без стемминга: «Васильев» ≠ «Василий»).
"""
from __future__ import annotations

import re
import threading
from collections import defaultdict

from .config import CONFIG
from .search import TR, decorate
from .textproc import WORD_RE, build_query

SCHEMA = """
CREATE TABLE IF NOT EXISTS authors (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, sort TEXT,
    cnt INTEGER, cnt_main INTEGER, cnt_rus INTEGER, cnt_inpx INTEGER);
CREATE TABLE IF NOT EXISTS series (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, sort TEXT, authors TEXT,
    cnt INTEGER, cnt_main INTEGER, cnt_rus INTEGER, cnt_inpx INTEGER);
DROP TABLE IF EXISTS authors_fts;
DROP TABLE IF EXISTS series_fts;
CREATE VIRTUAL TABLE IF NOT EXISTS authors_fts2 USING fts5(name, tokenize="unicode61 remove_diacritics 0");
CREATE VIRTUAL TABLE IF NOT EXISTS series_fts2 USING fts5(name, tokenize="unicode61 remove_diacritics 0");
CREATE INDEX IF NOT EXISTS ix_books_series ON books(series);
CREATE INDEX IF NOT EXISTS ix_books_new ON books(coalesce(pubdate, substr(added_at, 1, 10)));
CREATE INDEX IF NOT EXISTS ix_authors_sort ON authors(sort);
CREATE INDEX IF NOT EXISTS ix_series_sort ON series(sort);
CREATE TABLE IF NOT EXISTS book_genres (
    code TEXT NOT NULL, book_id INTEGER NOT NULL, PRIMARY KEY (code, book_id)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS genres (
    code TEXT PRIMARY KEY, cnt INTEGER, cnt_main INTEGER, cnt_rus INTEGER, cnt_inpx INTEGER);
"""
SRCS = ("main", "rus", "inpx")
_lock = threading.Lock()


def init(conn) -> None:
    conn.executescript(SCHEMA)


def split_authors(s: str | None) -> list[str]:
    return [a.strip() for a in (s or "").split(", ") if a.strip()]


def _plain(s: str) -> str:
    return " ".join(w.lower().replace("ё", "е") for w in WORD_RE.findall(s))


def _sortkey(s: str) -> str:
    return re.sub(r"^[^\wА-Яа-яЁё]+", "", s).lower().replace("ё", "е")


def rebuild(conn) -> None:
    """Пересобрать справочники авторов и серий (учитываются только нескрытые книги)."""
    with _lock:
        acnt: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
        scnt: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
        sauth: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        gcnt: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
        links: list[tuple[str, int]] = []
        for bid, author, series, src, genre in conn.execute(
                "SELECT id, author, series, src, genre FROM books WHERE hidden=0"):
            k = SRCS.index(src) if src in SRCS else 0
            if genre:
                for g in set(x for x in genre.split(":") if x):
                    gcnt[g][k] += 1
                    links.append((g, bid))
            names = split_authors(author)
            for a in names:
                acnt[a][k] += 1
            if series:
                scnt[series][k] += 1
                for a in names[:2]:
                    sauth[series][a] += 1
        conn.execute("DELETE FROM authors")
        conn.execute("DELETE FROM authors_fts2")
        conn.execute("DELETE FROM series")
        conn.execute("DELETE FROM series_fts2")
        conn.execute("DELETE FROM book_genres")
        conn.execute("DELETE FROM genres")
        conn.executemany("INSERT OR IGNORE INTO book_genres(code, book_id) VALUES (?,?)", links)
        conn.executemany("INSERT INTO genres(code, cnt, cnt_main, cnt_rus, cnt_inpx) VALUES (?,?,?,?,?)",
                         ((g, sum(c), *c) for g, c in gcnt.items()))
        rows = [(i, a, _sortkey(a), sum(c), *c) for i, (a, c) in enumerate(acnt.items(), 1)]
        conn.executemany("INSERT INTO authors(id, name, sort, cnt, cnt_main, cnt_rus, cnt_inpx) "
                         "VALUES (?,?,?,?,?,?,?)", rows)
        conn.executemany("INSERT INTO authors_fts2(rowid, name) VALUES (?,?)",
                         ((r[0], _plain(r[1])) for r in rows))
        rows = []
        for i, (s, c) in enumerate(scnt.items(), 1):
            top = sorted(sauth[s].items(), key=lambda x: -x[1])[:2]
            rows.append((i, s, _sortkey(s), ", ".join(a for a, _ in top), sum(c), *c))
        conn.executemany("INSERT INTO series(id, name, sort, authors, cnt, cnt_main, cnt_rus, cnt_inpx) "
                         "VALUES (?,?,?,?,?,?,?,?)", rows)
        conn.executemany("INSERT INTO series_fts2(rowid, name) VALUES (?,?)",
                         ((r[0], _plain(r[1])) for r in rows))
        conn.commit()


def rebuild_async() -> None:
    from .db import get_conn

    def work():
        with get_conn() as conn:
            rebuild(conn)
    threading.Thread(target=work, daemon=True).start()


def ensure(conn) -> None:
    """При первом запуске новой версии справочники пусты — собрать."""
    if conn.execute("SELECT 1 FROM authors_fts2 LIMIT 1").fetchone() is None and \
            conn.execute("SELECT 1 FROM books WHERE author != '' LIMIT 1").fetchone() is not None:
        rebuild(conn)
    elif conn.execute("SELECT 1 FROM genres LIMIT 1").fetchone() is None and \
            conn.execute("SELECT 1 FROM books WHERE genre IS NOT NULL LIMIT 1").fetchone() is not None:
        rebuild(conn)


# ====================================================================== поиск
def _match_expr(q: str, kind: str, prefix: bool) -> str | None:
    """Запрос к authors_fts2/series_fts2: все слова должны встретиться.
    Слово целиком (для фамилий — и женская форма: Васильев/Васильева) или по началу слова.
    «-слово» исключает, «слово*» — всегда по началу."""
    pos, neg = [], []
    for raw in (q or "").split():
        negate = raw.startswith("-") and len(raw) > 1
        star = raw.endswith("*")
        words = [w.lower().replace("ё", "е") for w in WORD_RE.findall(raw)]
        for w in words:
            if prefix or star:
                e = f'"{w}"*'
            elif kind == "author":
                vars_ = {w, w + "а"} | ({w[:-1]} if w.endswith("а") and len(w) > 3 else set())
                e = "(" + " OR ".join(f'"{v}"' for v in sorted(vars_)) + ")"
            else:
                e = f'"{w}"'
            (neg if negate else pos).append(e)
    if not pos:
        return None
    expr = " AND ".join(pos)
    for n in neg:
        expr = f"({expr}) NOT {n}"
    return expr


def _cnt_expr(lib: str) -> str:
    if lib in CONFIG.libs:
        return f"t.cnt_{lib}"
    srcs = [k for k in CONFIG.libs if k in SRCS]
    return "(" + " + ".join(f"t.cnt_{k}" for k in srcs) + ")" if srcs else "0"


def find(conn, kind: str, q: str, page: int, per: int, lib: str = ""):
    """kind: 'author' | 'series'. Авторы — по целым словам (если ничего — по началу слов),
    серии — по началу слов. Сортировка по алфавиту. Возвращает (строки, всего)."""
    table = "authors" if kind == "author" else "series"
    cnt = _cnt_expr(lib)
    for prefix in ((False, True) if kind == "author" else (True,)):
        expr = _match_expr(q, kind, prefix)
        if not expr:
            return [], 0
        where = f"{table}_fts2 MATCH ? AND {cnt} > 0"
        base = f"FROM {table}_fts2 JOIN {table} t ON t.id={table}_fts2.rowid WHERE {where}"
        try:
            total = conn.execute(f"SELECT count(*) {base}", (expr,)).fetchone()[0]
            if total or prefix:
                rows = conn.execute(f"SELECT t.*, {cnt} AS n {base} ORDER BY t.sort LIMIT ? OFFSET ?",
                                    (expr, per, (max(page, 1) - 1) * per)).fetchall()
                return [dict(r) for r in rows], total
        except Exception:  # noqa: BLE001 — некорректный запрос FTS
            return [], 0
    return [], 0


def _serno(b) -> tuple:
    s = (b.get("serno") or "").strip()
    m = re.match(r"\d+", s)
    return (int(m.group()) if m else 10 ** 9, s)


def _lang_sql(lang: str) -> str:
    if lang in ("ru", "en"):
        return f" AND b.lang='{lang}'"
    if lang == "other":
        return " AND coalesce(b.lang,'') NOT IN ('ru','en')"
    return ""


def author_books(conn, name: str, lib: str = "", lang: str = "", include_hidden: bool = False) -> list[dict]:
    """Все книги автора (точное совпадение одного из авторов книги)."""
    query = build_query('"' + name.replace('"', " ") + '"', "author")
    extra = ("" if include_hidden else " AND b.hidden=0") + _lang_sql(lang) + f" AND {CONFIG.src_sql()}"
    if lib in CONFIG.libs:
        extra += f" AND b.src='{lib}'"
    if query.fts:
        rows = conn.execute(f"SELECT b.*, {TR} FROM books_fts JOIN books b ON b.id=books_fts.rowid "
                            f"WHERE books_fts MATCH ? {extra}", (query.fts,)).fetchall()
    else:
        rows = conn.execute(f"SELECT b.*, {TR} FROM books b WHERE b.author LIKE ? {extra}",
                            (f"%{name}%",)).fetchall()
    return [dict(r) for r in rows if name in split_authors(r["author"])]


def series_books(conn, name: str, lang: str = "", include_hidden: bool = False) -> list[dict]:
    extra = ("" if include_hidden else " AND b.hidden=0") + _lang_sql(lang) + f" AND {CONFIG.src_sql()}"
    rows = [dict(r) for r in conn.execute(
        f"SELECT b.*, {TR} FROM books b WHERE b.series=? {extra}", (name,))]
    rows.sort(key=lambda b: (_serno(b), b["title"].lower()))
    return rows


def group_by_series(books: list[dict]) -> list[tuple[str | None, list[dict]]]:
    """Книги автора: сначала по сериям (по алфавиту, внутри — по номеру), затем вне серий."""
    groups: dict[str, list[dict]] = defaultdict(list)
    loose = []
    for b in books:
        (groups[b["series"]] if b.get("series") else loose).append(b)
    out: list[tuple[str | None, list[dict]]] = []
    for s in sorted(groups, key=_sortkey):
        out.append((s, sorted(groups[s], key=lambda b: (_serno(b), b["title"].lower()))))
    if loose:
        out.append((None, sorted(loose, key=lambda b: _sortkey(b["title"]))))
    return out


def decorate_all(books: list[dict]) -> list[dict]:
    return [decorate(b, None, False) for b in books]


# ====================================================================== жанры
def genre_groups(conn, lib: str = "") -> list[tuple[str, list[dict]]]:
    """Группы жанров с подсчётом книг (только жанры, где есть книги)."""
    from .genres import GENRES, GROUPS, OTHER_GROUP
    cnt = _cnt_expr(lib)
    have = {r[0]: r[1] for r in conn.execute(f"SELECT t.code, {cnt} FROM genres t WHERE {cnt} > 0")}
    out = []
    for title, g in GROUPS:
        items = [{"code": c, "name": n, "n": have[c]} for c, n in g.items() if c in have]
        if items:
            items.sort(key=lambda x: _sortkey(x["name"]))
            out.append((title, items, sum(x["n"] for x in items)))
    other = [{"code": c, "name": c, "n": n} for c, n in have.items() if c not in GENRES]
    out.sort(key=lambda g: _sortkey(g[0]))  # разделы — по алфавиту, «Прочие жанры» — в конце
    if other:
        other.sort(key=lambda x: x["name"].lower())
        out.append((OTHER_GROUP, other, sum(x["n"] for x in other)))
    return out


GENRE_SORTS = {"date": "b.pubdate DESC, b.id DESC", "title": "b.title COLLATE NOCASE, b.id"}


def genre_books(conn, code: str, sort: str, page: int, per: int, lang: str = "", lib: str = ""):
    extra = f" AND b.hidden=0 AND {CONFIG.src_sql()}" + _lang_sql(lang)
    if lib in CONFIG.libs:
        extra += f" AND b.src='{lib}'"
    base = f"FROM book_genres g JOIN books b ON b.id=g.book_id WHERE g.code=? {extra}"
    total = conn.execute(f"SELECT count(*) {base}", (code,)).fetchone()[0]
    rows = conn.execute(f"SELECT b.*, {TR} {base} ORDER BY {GENRE_SORTS.get(sort, GENRE_SORTS['date'])} "
                        f"LIMIT ? OFFSET ?", (code, per, (max(page, 1) - 1) * per)).fetchall()
    return decorate_all([dict(r) for r in rows]), total
