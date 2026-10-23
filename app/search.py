"""Поиск по базе."""
from __future__ import annotations

import sqlite3

from .config import CONFIG
from .importer import best_translation_path, load_text
from .textproc import Query, build_query, highlight, make_snippet

SORTS = {
    "rank": "rank",
    "title": "b.title COLLATE NOCASE",
    "author": "b.author COLLATE NOCASE, b.title COLLATE NOCASE",
    "code": "b.code COLLATE NOCASE",
    "new": "b.added_at DESC, b.id DESC",
}
# веса bm25 для колонок: code, title, author, body
BM25 = "bm25(books_fts, 4.0, 10.0, 6.0, 1.0)"
TR = ("(SELECT status FROM translations t WHERE t.book_id=b.id) AS tr_status, "
      "(SELECT status FROM translations_llm t WHERE t.book_id=b.id) AS llm_status")


def search(conn: sqlite3.Connection, q: str, scope: str = "all", page: int = 1, per_page: int = 24,
           sort: str = "rank", with_snippets: bool = True, include_hidden: bool = False,
           limit_all: int | None = None, lib: str = "", lang: str = ""):
    """Возвращает (список книг-словарей, всего найдено, Query).
    lib: '' | 'main' | 'rus' | 'inpx'; lang: '' | 'ru' | 'en' | 'other'."""
    query = build_query(q, scope)
    hidden_sql = ("" if include_hidden else "AND b.hidden=0") + f" AND {CONFIG.src_sql()}"
    if lib in CONFIG.libs:
        hidden_sql += f" AND b.src='{lib}'"
    if lang in ("ru", "en"):
        hidden_sql += f" AND b.lang='{lang}'"
    elif lang == "other":
        hidden_sql += " AND coalesce(b.lang,'') NOT IN ('ru','en')"
    offset = (max(page, 1) - 1) * per_page
    lim = limit_all if limit_all else per_page
    off = 0 if limit_all else offset

    if query.fts:
        order = SORTS.get(sort, "rank")
        try:
            total = conn.execute(
                f"SELECT count(*) FROM books_fts JOIN books b ON b.id=books_fts.rowid "
                f"WHERE books_fts MATCH ? {hidden_sql}", (query.fts,)).fetchone()[0]
            rows = conn.execute(
                f"SELECT b.*, {BM25} AS rank, {TR} FROM books_fts JOIN books b ON b.id=books_fts.rowid "
                f"WHERE books_fts MATCH ? {hidden_sql} ORDER BY {order} LIMIT ? OFFSET ?",
                (query.fts, lim, off)).fetchall()
        except sqlite3.OperationalError:
            return [], 0, query
    elif (q or "").strip():
        return [], 0, query
    else:
        order = SORTS.get(sort if sort != "rank" else "title")
        where = "WHERE 1=1 " + hidden_sql
        total = conn.execute(f"SELECT count(*) FROM books b {where}").fetchone()[0]
        rows = conn.execute(f"SELECT b.*, {TR} FROM books b {where} ORDER BY {order} LIMIT ? OFFSET ?",
                            (lim, off)).fetchall()

    books = [decorate(dict(r), query, with_snippets and scope in ("all", "text")) for r in rows]
    return books, total, query


def decorate(b: dict, query: Query | None, snippet: bool) -> dict:
    if query and (query.stems or query.prefixes):
        b["title_html"] = highlight(b["title"], query)
        b["author_html"] = highlight(b["author"], query)
        if snippet and b.get("src") != "inpx":  # тексты коллекции inpx не индексируются
            try:
                text = load_text(b)
                b["snippet"] = make_snippet(text, query)
                if not b["snippet"]:
                    tp = best_translation_path(b["id"])
                    if tp is not None:
                        b["snippet"] = make_snippet(tp.read_text(encoding="utf-8"), query)
                        b["snippet_ru"] = bool(b["snippet"])
            except OSError:
                b["snippet"] = None
    return b
