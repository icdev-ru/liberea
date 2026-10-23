"""Сканирование библиотек и обновление базы.

Две библиотеки:
  main — папки «код Название (Автор)», внутри txt + обложка;
  rus  — папка со всеми подпапками, каждый .txt/.fb2 — отдельная книга, код rus-00001…
"""
from __future__ import annotations

import hashlib
import os
import re
import threading
import time
from pathlib import Path

from .config import CONFIG
from .db import get_conn
from .fb2read import parse_fb2
from .textproc import decode_bytes, detect_lang, normalize_text, plain_tokens, stem_text

NAME_RE = re.compile(r"^(\S+)\s+(.+?)\s*\(([^()]*)\)\s*$")
NAME_NO_AUTHOR_RE = re.compile(r"^(\S+)\s+(.+)$")
RUS_EXT = (".txt", ".fb2")

STATE: dict = {"running": False}
_lock = threading.Lock()


def parse_dir_name(name: str) -> tuple[str, str, str, str | None]:
    """'ab-421 Well Hung-Up (J. X. Williams)' -> (code, title, author, warning)."""
    m = NAME_RE.match(name)
    if m:
        return m.group(1), m.group(2).strip(), m.group(3).strip(), None
    m = NAME_NO_AUTHOR_RE.match(name)
    if m:
        return m.group(1), m.group(2).strip(), "", "не найден автор в скобках"
    return "", name.strip(), "", "имя папки не по шаблону"


# ====================================================================== чтение текста книги
def book_path(b) -> Path:
    """Полный путь к исходному файлу книги."""
    if b["src"] == "inpx":
        from .inpx import display_path
        return display_path(b)
    if b["src"] == "rus":
        return (CONFIG.rus_root or Path(".")) / b["file_path"]
    return CONFIG.library_root / b["dir_name"] / b["txt_file"]


def read_file_text(path: Path, encoding: str | None = None) -> tuple[str, str, dict]:
    """Прочитать txt или fb2 → (чистый текст, кодировка, метаданные fb2)."""
    data = path.read_bytes()
    if path.suffix.lower() == ".fb2":
        r = parse_fb2(data)
        return r["text"], "fb2/" + r["encoding"], r
    if encoding and not encoding.startswith("fb2"):
        try:
            text = data.decode(encoding, "replace")
        except LookupError:
            text, encoding = decode_bytes(data)
    else:
        text, encoding = decode_bytes(data)
    return normalize_text(text.lstrip("﻿")), encoding, {}


def translation_path(book_id: int) -> Path:
    """Черновой (машинный) перевод."""
    return CONFIG.database.parent / "translations" / f"{book_id}.txt"


def llm_translation_path(book_id: int) -> Path:
    """Качественный перевод языковой моделью."""
    return CONFIG.database.parent / "translations" / f"{book_id}.llm.txt"


def best_translation_path(book_id: int) -> Path | None:
    """Лучший доступный перевод: LLM, иначе черновой."""
    for p in (llm_translation_path(book_id), translation_path(book_id)):
        if p.exists():
            return p
    return None


def load_text(b) -> str:
    """Чистый текст книги (или её перевода, если b['_variant'] == 'ru').
    Для FB2 результат разбора кешируется на диске."""
    if isinstance(b, dict) and b.get("_variant") == "ru":
        return best_translation_path(b["id"]).read_text(encoding="utf-8")
    if b["src"] == "inpx":
        cache = CONFIG.cache / "text" / f"{b['id']}_0.txt"
        if cache.exists():
            return cache.read_text(encoding="utf-8")
        from .inpx import read_bytes
        text = parse_fb2(read_bytes(b))["text"]
        _save_text_cache(b["id"], 0, text)
        return text
    path = book_path(b)
    if b["fmt"] != "fb2":
        return read_file_text(path, b["encoding"])[0]
    cache = CONFIG.cache / "text" / f"{b['id']}_{int(b['txt_mtime'] or 0)}.txt"
    if cache.exists():
        return cache.read_text(encoding="utf-8")
    text = read_file_text(path)[0]
    _save_text_cache(b["id"], b["txt_mtime"], text)
    return text


def _save_text_cache(book_id: int, mtime, text: str) -> None:
    d = CONFIG.cache / "text"
    d.mkdir(parents=True, exist_ok=True)
    for old in d.glob(f"{book_id}_*.txt"):
        old.unlink(missing_ok=True)
    (d / f"{book_id}_{int(mtime or 0)}.txt").write_text(text, encoding="utf-8")


# ====================================================================== индекс
def fts_insert(conn, book_id: int, code: str, title: str, author: str, body_stems: str) -> None:
    # если есть перевод — индексируем и его, чтобы книга находилась русскими словами
    tp = best_translation_path(book_id)
    if tp is not None:
        titles = [r[0] for r in conn.execute(
            """SELECT title FROM translations WHERE book_id=? AND status='done'
               UNION ALL SELECT title FROM translations_llm WHERE book_id=? AND status='done'""",
            (book_id, book_id)) if r[0]]
        if titles or tp.name.endswith(".llm.txt"):
            body_stems += " " + stem_text(tp.read_text(encoding="utf-8"))
            title = " ".join([title, *titles])
    conn.execute("DELETE FROM books_fts WHERE rowid=?", (book_id,))
    conn.execute(
        "INSERT INTO books_fts(rowid, code, title, author, body) VALUES (?,?,?,?,?)",
        (book_id, plain_tokens(code), stem_text(title), stem_text(author), body_stems))


def reindex_book(conn, book_id: int) -> None:
    """Переиндексировать одну книгу (после правки метаданных)."""
    b = conn.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
    if b["src"] == "inpx":  # у коллекции inpx индексируются только метаданные
        from .inpx import fts_body
        fts_insert(conn, book_id, b["code"], f"{b['title']} {b['series'] or ''}", b["author"],
                   fts_body(b["genre"], b["keywords"]))
        return
    fts_insert(conn, book_id, b["code"], b["title"], b["author"], stem_text(load_text(b)))


# ====================================================================== запуск сканирования
def run_scan(force: bool = False, kind: str = "main") -> dict:
    """Синхронно просканировать библиотеку kind ('main' | 'rus'). Прогресс — в STATE."""
    if not _lock.acquire(blocking=False):
        return {"error": "сканирование уже идёт"}
    try:
        if kind == "inpx":
            from . import inpx
            return inpx.run_scan(force, STATE, _Scan, _begin)
        return (_run_scan_rus if kind == "rus" else _run_scan_main)(force)
    finally:
        STATE["running"] = False
        _lock.release()


def start_scan_thread(force: bool = False, kind: str = "main") -> bool:
    if STATE.get("running"):
        return False
    t = threading.Thread(target=run_scan, args=(force, kind), daemon=True)
    t.start()
    time.sleep(0.05)
    return True


def _begin(kind: str, force: bool, root: Path | None) -> bool:
    STATE.clear()
    STATE.update(running=True, kind=kind, label={"main": CONFIG.main_label, "rus": CONFIG.rus_label,
                 "inpx": CONFIG.inpx_label}.get(kind, kind), force=force, total=0, done=0, current="", added=0, updated=0,
                 unchanged=0, removed=0, errors=0, warnings=0, started=time.time(), message="")
    if root is None:
        STATE["message"] = "Папка не задана в config.toml"
        STATE["errors"] = 1
        return False
    if not root.is_dir():
        STATE["message"] = f"Папка не найдена: {root}"
        STATE["errors"] = 1
        return False
    return True


class _Scan:
    """Общая часть: запись в scans/import_log, подсчёт, удаление пропавших книг."""

    def __init__(self, conn, kind: str, force: bool):
        self.conn = conn
        self.kind = kind
        self.scan_id = conn.execute("INSERT INTO scans(force, kind) VALUES (?,?)", (int(force), kind)).lastrowid
        conn.commit()
        STATE["scan_id"] = self.scan_id

    def log(self, level: str, name: str, msg: str):
        self.conn.execute("INSERT INTO import_log(scan_id, level, dir_name, message) VALUES (?,?,?,?)",
                          (self.scan_id, level, name, msg))
        STATE["errors" if level == "error" else "warnings"] += 1

    def remove_missing(self, existing: dict, seen: set):
        gone = [r["id"] for key, r in existing.items() if key not in seen]
        for bid in gone:
            self.conn.execute("DELETE FROM books_fts WHERE rowid=?", (bid,))
            self.conn.execute("DELETE FROM books WHERE id=?", (bid,))
        STATE["removed"] = len(gone)

    def finish(self):
        c = self.conn
        c.execute(
            """UPDATE scans SET finished=datetime('now','localtime'), total=?, added=?, updated=?,
               unchanged=?, removed=?, errors=?, warnings=? WHERE id=?""",
            (STATE["total"], STATE["added"], STATE["updated"], STATE["unchanged"], STATE["removed"],
             STATE["errors"], STATE["warnings"], self.scan_id))
        c.commit()
        if STATE["added"] + STATE["updated"] > 500:
            c.execute("INSERT INTO books_fts(books_fts) VALUES ('optimize')")
        if STATE["added"] + STATE["updated"] + STATE["removed"]:
            from .catalog import rebuild
            STATE["current"] = "обновление списков авторов и серий…"
            rebuild(c)
        STATE["finished"] = time.time()
        STATE["current"] = ""


def _upsert(conn, old, fields: dict) -> int:
    if old:
        sets = ", ".join(f"{k}=?" for k in fields)
        conn.execute(f"UPDATE books SET {sets}, updated_at=datetime('now','localtime') WHERE id=?",
                     (*fields.values(), old["id"]))
        STATE["updated"] += 1
        return old["id"]
    cols = ", ".join(fields)
    qs = ", ".join("?" for _ in fields)
    STATE["added"] += 1
    return conn.execute(f"INSERT INTO books({cols}) VALUES ({qs})", tuple(fields.values())).lastrowid


# ====================================================================== основная библиотека
def _pick(files: list[os.DirEntry], exts: list[str]) -> tuple[os.DirEntry | None, int]:
    cands = [f for f in files if os.path.splitext(f.name)[1].lower() in exts]
    if not cands:
        return None, 0
    cands.sort(key=lambda f: f.stat().st_size, reverse=True)
    return cands[0], len(cands)


def _run_scan_main(force: bool) -> dict:
    root: Path = CONFIG.library_root
    if not _begin("main", force, root):
        return dict(STATE)
    dirs = sorted((e for e in os.scandir(root) if e.is_dir()), key=lambda e: e.name)
    STATE["total"] = len(dirs)

    with get_conn() as conn:
        sc = _Scan(conn, "main", force)
        existing = {r["dir_name"]: r for r in conn.execute("SELECT * FROM books WHERE src='main'")}
        seen: set[str] = set()

        for i, d in enumerate(dirs, 1):
            STATE["done"], STATE["current"] = i, d.name
            try:
                files = [f for f in os.scandir(d.path) if f.is_file()]
                txt, n_txt = _pick(files, CONFIG.text_ext)
                cover, _ = _pick(files, CONFIG.cover_ext)
                if txt is None:
                    sc.log("error", d.name, "нет текстового файла — книга пропущена")
                    continue
                seen.add(d.name)
                if n_txt > 1:
                    sc.log("warning", d.name, f"несколько txt ({n_txt}), взят самый большой: {txt.name}")
                code, title, author, warn = parse_dir_name(d.name)
                if warn:
                    sc.log("warning", d.name, warn)
                st = txt.stat()
                cover_name = cover.name if cover else None
                cover_mtime = cover.stat().st_mtime if cover else None
                old = existing.get(d.name)
                if (old and not force and old["txt_file"] == txt.name and old["txt_size"] == st.st_size
                        and old["txt_mtime"] == st.st_mtime and old["cover_file"] == cover_name
                        and old["cover_mtime"] == cover_mtime):
                    STATE["unchanged"] += 1
                    continue
                text, enc, _ = read_file_text(Path(txt.path))
                if not text.strip():
                    sc.log("warning", d.name, "текстовый файл пустой")
                if old and old["meta_locked"]:
                    code, title, author = old["code"], old["title"], old["author"]
                book_id = _upsert(conn, old, dict(
                    dir_name=d.name, src="main", fmt="txt", code=code, title=title, author=author,
                    txt_file=txt.name, cover_file=cover_name, encoding=enc, lang=detect_lang(text),
                    txt_size=st.st_size, txt_mtime=st.st_mtime, cover_mtime=cover_mtime, chars=len(text),
                    scan_id=sc.scan_id))
                fts_insert(conn, book_id, code, title, author, stem_text(text))
            except Exception as e:  # noqa: BLE001
                sc.log("error", d.name, f"ошибка: {e!r}")
            if i % 50 == 0:
                conn.commit()

        sc.remove_missing(existing, seen)
        sc.finish()
    return dict(STATE)


# ====================================================================== библиотека rus
def _norm_name(s: str) -> str:
    s = s.lower().replace("ё", "е")
    s = re.sub(r"[\s_]+", " ", s)
    return s.strip(" .")


def _rus_key(rel: str) -> str:
    """Ключ группы дубликатов: имя файла без расширения (и папка, если dedup = folder)."""
    p = Path(rel)
    name = _norm_name(p.stem)
    if CONFIG.rus_dedup == "folder":
        return _norm_name(str(p.parent).replace("\\", "/")) + "/" + name
    return name


def _run_scan_rus(force: bool) -> dict:
    root = CONFIG.rus_root
    if not _begin("rus", force, root):
        return dict(STATE)

    # 1. собрать все файлы и сгруппировать дубликаты
    groups: dict[str, list[tuple[str, int, float]]] = {}
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            if os.path.splitext(fn)[1].lower() not in RUS_EXT:
                continue
            full = os.path.join(dirpath, fn)
            try:
                st = os.stat(full)
            except OSError:
                continue
            rel = os.path.relpath(full, root)
            groups.setdefault(_rus_key(rel), []).append((rel, st.st_size, st.st_mtime))
    keys = sorted(groups)
    STATE["total"] = len(keys)
    prefix = CONFIG.rus_prefix

    with get_conn() as conn:
        sc = _Scan(conn, "rus", force)
        existing = {r["dir_name"]: r for r in conn.execute("SELECT * FROM books WHERE src='rus'")}
        nums = [int(m.group(1)) for r in existing.values()
                if (m := re.fullmatch(re.escape(prefix) + r"-(\d+)", r["code"] or ""))]
        next_num = max(nums, default=0) + 1
        seen: set[str] = set()

        for i, key in enumerate(keys, 1):
            cands = sorted(groups[key])
            dkey = "rus:" + key
            seen.add(dkey)
            STATE["done"], STATE["current"] = i, cands[0][0]
            sig = hashlib.sha1(repr(cands).encode()).hexdigest()[:16]
            old = existing.get(dkey)
            if old and not force and old["sig"] == sig:
                STATE["unchanged"] += 1
                continue
            try:
                # 2. прочитать всех кандидатов, выбрать самый объёмный чистый текст
                read = []
                for rel, size, mtime in cands:
                    try:
                        text, enc, meta = read_file_text(root / rel)
                        read.append((len(text.strip()), rel.lower().endswith(".fb2"), rel, size, mtime, text, enc, meta))
                    except Exception as e:  # noqa: BLE001
                        sc.log("error", rel, f"не удалось прочитать: {e!r}")
                if not read:
                    seen.discard(dkey)
                    continue
                read.sort(key=lambda r: (r[0], r[1]), reverse=True)  # больше текста; при равенстве — fb2
                n, is_fb2, rel, size, mtime, text, enc, meta = read[0]
                if len(read) > 1:
                    others = ", ".join(f"{r[2]} ({r[0]} зн.)" for r in read[1:])
                    sc.log("warning", rel, f"дубликаты — взят этот файл ({n} зн.), отброшены: {others}")
                if not text.strip():
                    sc.log("warning", rel, "файл без текста")
                # автор: из выбранного fb2, иначе из любого fb2-дубликата
                author = ", ".join(meta.get("authors", [])) if meta else ""
                if not author:
                    author = next((", ".join(r[7]["authors"]) for r in read if r[7].get("authors")), "")
                title = re.sub(r"\s+", " ", Path(rel).stem.replace("_", " ")).strip()
                lang = (meta.get("lang") or "")[:2] if meta else ""
                lang = lang if lang in ("ru", "en") else detect_lang(text)
                code = old["code"] if old else f"{prefix}-{next_num:05d}"
                if not old:
                    next_num += 1
                if old and old["meta_locked"]:
                    code, title, author = old["code"], old["title"], old["author"]
                book_id = _upsert(conn, old, dict(
                    dir_name=dkey, src="rus", fmt="fb2" if is_fb2 else "txt", file_path=rel,
                    code=code, title=title, author=author, txt_file=Path(rel).name, cover_file=None,
                    encoding=enc, lang=lang, txt_size=size, txt_mtime=mtime, cover_mtime=None,
                    chars=len(text), sig=sig, scan_id=sc.scan_id))
                if is_fb2:
                    _save_text_cache(book_id, mtime, text)
                fts_insert(conn, book_id, code, title, author, stem_text(text))
            except Exception as e:  # noqa: BLE001
                sc.log("error", cands[0][0], f"ошибка: {e!r}")
            if i % 50 == 0:
                conn.commit()

        sc.remove_missing(existing, seen)
        sc.finish()
    return dict(STATE)
