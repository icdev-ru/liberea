"""SQLite: соединения и схема."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager

from .config import CONFIG

SCHEMA = """
CREATE TABLE IF NOT EXISTS books (
    id          INTEGER PRIMARY KEY,
    dir_name    TEXT NOT NULL UNIQUE,
    code        TEXT NOT NULL DEFAULT '',
    title       TEXT NOT NULL DEFAULT '',
    author      TEXT NOT NULL DEFAULT '',
    txt_file    TEXT NOT NULL,
    cover_file  TEXT,
    encoding    TEXT,
    lang        TEXT,
    txt_size    INTEGER,
    txt_mtime   REAL,
    cover_mtime REAL,
    chars       INTEGER,
    meta_locked INTEGER NOT NULL DEFAULT 0,
    hidden      INTEGER NOT NULL DEFAULT 0,
    added_at    TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    scan_id     INTEGER
);
CREATE INDEX IF NOT EXISTS ix_books_title  ON books(title COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS ix_books_author ON books(author COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS ix_books_code   ON books(code COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS scans (
    id        INTEGER PRIMARY KEY,
    started   TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    finished  TEXT,
    force     INTEGER,
    total     INTEGER, added INTEGER, updated INTEGER, unchanged INTEGER,
    removed   INTEGER, errors INTEGER, warnings INTEGER
);

CREATE TABLE IF NOT EXISTS import_log (
    id       INTEGER PRIMARY KEY,
    scan_id  INTEGER,
    level    TEXT,          -- error / warning
    dir_name TEXT,
    message  TEXT
);
CREATE INDEX IF NOT EXISTS ix_log_scan ON import_log(scan_id);

CREATE TABLE IF NOT EXISTS collections (
    id      INTEGER PRIMARY KEY,
    owner   TEXT NOT NULL,
    name    TEXT NOT NULL,
    created TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS ix_coll_owner ON collections(owner);

CREATE TABLE IF NOT EXISTS translations (
    book_id  INTEGER PRIMARY KEY REFERENCES books(id) ON DELETE CASCADE,
    status   TEXT NOT NULL,        -- queued / running / done / error
    engine   TEXT,
    title    TEXT,                 -- переведённое название
    chars    INTEGER,
    seconds  REAL,
    error    TEXT,
    created  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    started  TEXT,
    finished TEXT
);

CREATE TABLE IF NOT EXISTS translations_llm (
    book_id  INTEGER PRIMARY KEY REFERENCES books(id) ON DELETE CASCADE,
    status   TEXT NOT NULL,        -- queued / running / done / error
    engine   TEXT,                 -- имя модели
    title    TEXT,
    chars    INTEGER,
    seconds  REAL,
    tokens   INTEGER,
    error    TEXT,
    created  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    started  TEXT,
    finished TEXT
);

CREATE TABLE IF NOT EXISTS collection_items (
    collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    book_id       INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    position      INTEGER NOT NULL,
    PRIMARY KEY (collection_id, book_id)
);
"""

# Полнотекстовый индекс. В нём хранятся только основы слов (стеммы),
# сам текст остаётся на диске. Колонки: код, название, автор, текст.
FTS_CONTENTLESS = """CREATE VIRTUAL TABLE books_fts USING fts5(
    code, title, author, body,
    content='', contentless_delete=1,
    tokenize="unicode61 remove_diacritics 0")"""
FTS_STORED = """CREATE VIRTUAL TABLE books_fts USING fts5(
    code, title, author, body,
    tokenize="unicode61 remove_diacritics 0")"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(CONFIG.database, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


@contextmanager
def get_conn():
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        # миграции: новые колонки для второй библиотеки (rus)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(books)")}
        for col, ddl in (("src", "TEXT NOT NULL DEFAULT 'main'"),   # main | rus
                         ("file_path", "TEXT"),                    # путь файла относительно корня rus
                         ("fmt", "TEXT NOT NULL DEFAULT 'txt'"),   # txt | fb2 — формат исходника
                         ("sig", "TEXT"),                          # подпись набора файлов-кандидатов
                         ("series", "TEXT"),                       # inpx: серия
                         ("serno", "TEXT"),                        # inpx: номер в серии
                         ("genre", "TEXT"),                        # inpx: коды жанров через «:»
                         ("keywords", "TEXT"),                     # inpx: ключевые слова
                         ("pubdate", "TEXT")):                     # inpx: дата добавления в коллекцию
            if col not in cols:
                conn.execute(f"ALTER TABLE books ADD COLUMN {col} {ddl}")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_books_src ON books(src)")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_books_lang ON books(lang)")
        scols = {r[1] for r in conn.execute("PRAGMA table_info(scans)")}
        if "kind" not in scols:
            conn.execute("ALTER TABLE scans ADD COLUMN kind TEXT NOT NULL DEFAULT 'main'")
        from .catalog import SCHEMA as CATALOG_SCHEMA
        conn.executescript(CATALOG_SCHEMA)
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='books_fts'").fetchone()
        if not exists:
            try:
                conn.execute(FTS_CONTENTLESS)
            except sqlite3.OperationalError:
                # старый SQLite (< 3.43) — индекс с хранением содержимого
                conn.execute(FTS_STORED)
