"""Коллекция в формате INPX (каталог MyHomeLib).

Импортируются только метаданные (автор, название, серия, жанр, язык…), текст не индексируется.
Книги читаются прямо из zip-архивов коллекции — ничего не распаковывается заранее.
Повторный импорт (новая версия .inpx) добавляет новые книги, обновляет изменённые и
убирает исчезнувшие/удалённые.
"""
from __future__ import annotations

import base64
import hashlib
import re
import threading
import zipfile
from collections import OrderedDict
from pathlib import Path

from .config import CONFIG
from .db import get_conn
from .genres import genre_names
from .textproc import plain_tokens, stem_text

DEFAULT_FIELDS = ["AUTHOR", "GENRE", "TITLE", "SERIES", "SERNO", "FILE", "SIZE", "LIBID", "DEL", "EXT",
                  "DATE", "LANG", "LIBRATE", "KEYWORDS"]


# ====================================================================== чтение .inpx
def collection_info(path: Path | None = None) -> dict:
    path = path or CONFIG.inpx_file
    info = {"name": "", "id": "", "version": ""}
    try:
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
            if "collection.info" in names:
                lines = z.read("collection.info").decode("utf-8-sig", "replace").splitlines()
                info["name"] = lines[0].strip() if lines else ""
                info["id"] = lines[1].strip() if len(lines) > 1 else ""
            if "version.info" in names:
                info["version"] = z.read("version.info").decode("utf-8-sig", "replace").strip()
    except (OSError, zipfile.BadZipFile):
        pass
    return info


def _fields(z: zipfile.ZipFile) -> list[str]:
    if "structure.info" in z.namelist():
        raw = z.read("structure.info").decode("utf-8-sig", "replace")
        f = [x.strip().upper() for x in re.split(r"[;\r\n]+", raw) if x.strip()]
        if f:
            return f
    return DEFAULT_FIELDS


def format_author(raw: str) -> str:
    """'Громов,Александр,Николаевич:Иванов,Иван,:' → 'Громов Александр Николаевич, Иванов Иван'."""
    out = []
    for a in raw.split(":"):
        parts = [p.strip() for p in a.split(",") if p.strip()]
        if parts:
            out.append(" ".join(parts))
    return ", ".join(out)


def iter_records(path: Path):
    """Перебор книг: (имя .inp, словарь полей, исходная строка)."""
    with zipfile.ZipFile(path) as z:
        fields = _fields(z)
        for name in sorted(n for n in z.namelist() if n.lower().endswith(".inp")):
            data = z.read(name).decode("utf-8", "replace")
            for line in data.split("\n"):
                line = line.rstrip("\r")
                if not line.strip():
                    continue
                vals = line.split("\x04")
                rec = {f: (vals[i] if i < len(vals) else "") for i, f in enumerate(fields)}
                yield name, rec, line


def archive_name(inp_name: str, rec: dict) -> str:
    """Имя zip-архива с книгой: поле FOLDER или имя .inp с расширением .zip."""
    folder = (rec.get("FOLDER") or "").strip()
    if folder:
        return folder if folder.lower().endswith(".zip") else folder + ".zip"
    return inp_name[:-4] + ".zip"


# ====================================================================== импорт
def _wanted(rec: dict) -> bool:
    if CONFIG.inpx_skip_deleted and rec.get("DEL", "").strip() == "1":
        return False
    if CONFIG.inpx_langs and (rec.get("LANG") or "").lower()[:2] not in CONFIG.inpx_langs:
        return False
    genres = [g for g in (rec.get("GENRE") or "").split(":") if g]
    if CONFIG.inpx_genres and not any(g in CONFIG.inpx_genres for g in genres):
        return False
    if CONFIG.inpx_exclude_genres and any(g in CONFIG.inpx_exclude_genres for g in genres):
        return False
    return True


def fts_title(title: str, series: str | None) -> str:
    return stem_text(f"{title} {series or ''}")


def fts_body(genre: str | None, keywords: str | None) -> str:
    return stem_text(" ".join(genre_names(genre)) + " " + (keywords or ""))


def run_scan(force: bool, STATE: dict, Scan, begin) -> dict:
    """Импорт/обновление коллекции. Вызывается из importer.run_scan (под его блокировкой)."""
    from .importer import fts_insert

    path = CONFIG.inpx_file
    if not begin("inpx", force, CONFIG.inpx_root):
        return dict(STATE)
    if path is None or not path.is_file():
        STATE["message"] = f"Файл .inpx не найден: {path}"
        STATE["errors"] = 1
        return dict(STATE)
    info = collection_info(path)
    STATE["info"] = f"{info['name']} {info['version']}".strip()
    STATE["current"] = "чтение .inpx…"
    try:
        with zipfile.ZipFile(path) as z:  # предварительный подсчёт строк — для индикатора
            STATE["total"] = sum(z.read(n).count(b"\n") for n in z.namelist() if n.lower().endswith(".inp"))
    except (OSError, zipfile.BadZipFile) as e:
        STATE["message"] = f"Не удалось прочитать {path}: {e}"
        STATE["errors"] = 1
        return dict(STATE)
    prefix = CONFIG.inpx_prefix
    root = CONFIG.inpx_root

    with get_conn() as conn:
        sc = Scan(conn, "inpx", force)
        existing = {r[0]: (r[1], r[2], r[3]) for r in conn.execute(
            "SELECT dir_name, id, sig, meta_locked FROM books WHERE src='inpx'").fetchall()}
        seen: set[str] = set()
        checked_archives: dict[str, bool] = {}
        skipped = 0

        i = 0
        for i, (inp, rec, line) in enumerate(iter_records(path), 1):
            if i % 1000 == 0:
                STATE["done"], STATE["current"] = i, inp
            if not _wanted(rec):
                skipped += 1
                continue
            arc = archive_name(inp, rec)
            ext = (rec.get("EXT") or "fb2").strip().lstrip(".") or "fb2"
            fname = f"{rec.get('FILE', '').strip()}.{ext}"
            key = f"inpx:{arc}/{fname}"
            if key in seen:
                continue
            seen.add(key)
            if arc not in checked_archives:
                ok = (root / arc).is_file()
                checked_archives[arc] = ok
                if not ok:
                    sc.log("warning", arc, "архив не найден — книги из него в базе есть, но открыть их нельзя")
            sig = hashlib.md5(f"{arc}\x04{line}".encode()).hexdigest()[:16]
            old = existing.get(key)
            if old and not force and old[1] == sig:
                STATE["unchanged"] += 1
                continue
            try:
                title = (rec.get("TITLE") or "").strip() or rec.get("FILE", "")
                author = format_author(rec.get("AUTHOR") or "")
                series = (rec.get("SERIES") or "").strip() or None
                serno = (rec.get("SERNO") or "").strip()
                serno = None if serno in ("", "0") else serno
                genre = (rec.get("GENRE") or "").strip(":") or None
                keywords = (rec.get("KEYWORDS") or "").strip() or None
                libid = (rec.get("LIBID") or "").strip() or rec.get("FILE", "")
                code = f"{prefix}-{libid}"
                try:
                    size = int(rec.get("SIZE") or 0)
                except ValueError:
                    size = 0
                fields = dict(
                    dir_name=key, src="inpx", fmt=ext.lower(), file_path=f"{arc}/{fname}", txt_file=fname,
                    code=code, title=title, author=author, series=series, serno=serno, genre=genre,
                    keywords=keywords, pubdate=(rec.get("DATE") or "").strip() or None,
                    lang=(rec.get("LANG") or "").strip().lower()[:5] or None, encoding="fb2",
                    txt_size=size, txt_mtime=0, sig=sig, scan_id=sc.scan_id, cover_file="?", cover_mtime=None,
                    chars=None)
                if old:
                    bid, _sig, locked = old
                    if locked:
                        for k in ("code", "title", "author"):
                            fields.pop(k)
                    sets = ", ".join(f"{k}=?" for k in fields)
                    conn.execute(f"UPDATE books SET {sets}, updated_at=datetime('now','localtime') WHERE id=?",
                                 (*fields.values(), bid))
                    STATE["updated"] += 1
                    _drop_cached(bid)
                    b = conn.execute("SELECT code, title, author FROM books WHERE id=?", (bid,)).fetchone()
                    fts_insert(conn, bid, b[0], f"{b[1]} {series or ''}", b[2], fts_body(genre, keywords))
                else:
                    cols = ", ".join(fields)
                    qs = ", ".join("?" for _ in fields)
                    bid = conn.execute(f"INSERT INTO books({cols}) VALUES ({qs})", tuple(fields.values())).lastrowid
                    STATE["added"] += 1
                    conn.execute(
                        "INSERT INTO books_fts(rowid, code, title, author, body) VALUES (?,?,?,?,?)",
                        (bid, plain_tokens(code), fts_title(title, series), stem_text(author),
                         fts_body(genre, keywords)))
            except Exception as e:  # noqa: BLE001
                sc.log("error", key, f"ошибка: {e!r}")
            if i % 5000 == 0:
                conn.commit()

        STATE["done"] = STATE["total"] = i
        if not seen:
            sc.log("error", str(path), "в .inpx не найдено ни одной подходящей книги — база не изменена")
        else:
            STATE["current"] = "удаление исчезнувших книг…"
            gone = [v[0] for k, v in existing.items() if k not in seen]
            for n, bid in enumerate(gone, 1):
                conn.execute("DELETE FROM books_fts WHERE rowid=?", (bid,))
                conn.execute("DELETE FROM books WHERE id=?", (bid,))
                _drop_cached(bid)
                if n % 5000 == 0:
                    conn.commit()
            STATE["removed"] = len(gone)
        if skipped:
            sc.log("warning", str(path), f"пропущено по настройкам [inpx] (удалённые, язык, жанр): {skipped}")
        STATE["current"] = "оптимизация индекса…"
        sc.finish()
    return dict(STATE)


# ====================================================================== чтение книг из архивов
_zips: "OrderedDict[str, zipfile.ZipFile]" = OrderedDict()
_zlock = threading.Lock()


def _read_member(arc: str, member: str) -> bytes:
    path = str((CONFIG.inpx_root or Path(".")) / arc)
    with _zlock:
        z = _zips.get(path)
        if z is None:
            z = zipfile.ZipFile(path)
            _zips[path] = z
            while len(_zips) > 8:
                _zips.popitem(last=False)[1].close()
        else:
            _zips.move_to_end(path)
        try:
            return z.read(member)
        except KeyError:
            # регистр имён в архиве может отличаться
            low = member.lower()
            real = next((n for n in z.namelist() if n.lower() == low), None)
            if real is None:
                raise FileNotFoundError(f"{member} нет в архиве {arc}") from None
            return z.read(real)


def read_bytes(b) -> bytes:
    arc, member = b["file_path"].rsplit("/", 1)
    return _read_member(arc, member)


def display_path(b) -> Path:
    return (CONFIG.inpx_root or Path(".")) / b["file_path"]


def original_file(b) -> Path:
    """Исходный файл книги, извлечённый из архива в кеш."""
    d = CONFIG.cache / "books"
    d.mkdir(parents=True, exist_ok=True)
    out = d / f"{b['id']}_orig_{b['sig'] or ''}.{b['fmt'] or 'fb2'}"
    if not out.exists():
        tmp = out.with_suffix(".tmp")
        tmp.write_bytes(read_bytes(b))
        tmp.replace(out)
    return out


_COVER_RE = re.compile(rb"<(?:\w+:)?coverpage>(.*?)</(?:\w+:)?coverpage>", re.S)
_HREF_RE = re.compile(rb'href\s*=\s*["\']#?([^"\']+)["\']')


def _extract_cover(data: bytes) -> tuple[bytes, str] | None:
    m = _COVER_RE.search(data[:200000])
    if not m:
        return None
    h = _HREF_RE.search(m.group(1))
    if not h:
        return None
    cid = re.escape(h.group(1))
    bm = re.search(rb"<binary\b[^>]*\bid\s*=\s*[\"']" + cid + rb"[\"'][^>]*>(.*?)</binary>", data, re.S)
    if not bm:
        return None
    ct = re.search(rb'content-type\s*=\s*["\']([^"\']+)', bm.group(0)[:300])
    ext = {b"image/png": "png", b"image/gif": "gif"}.get(ct.group(1).lower() if ct else b"", "jpg")
    try:
        return base64.b64decode(re.sub(rb"\s+", b"", bm.group(1))), ext
    except ValueError:
        return None


def cover_path(b) -> Path | None:
    """Обложка из FB2 (извлекается при первом обращении и кешируется)."""
    if not b.get("cover_file"):
        return None
    d = CONFIG.cache / "covers"
    if b["cover_file"] != "?":
        p = d / b["cover_file"]
        if p.exists():
            return p
    try:
        res = _extract_cover(read_bytes(b))
    except (OSError, zipfile.BadZipFile):
        return None  # архив недоступен — не запоминаем, попробуем позже
    name = None
    if res:
        d.mkdir(parents=True, exist_ok=True)
        name = f"{b['id']}.{res[1]}"
        (d / name).write_bytes(res[0])
    try:
        with get_conn() as conn:
            conn.execute("UPDATE books SET cover_file=? WHERE id=?", (name, b["id"]))
    except Exception:  # noqa: BLE001 — база занята, запишем в другой раз
        pass
    b["cover_file"] = name
    return d / name if name else None


def _drop_cached(bid: int) -> None:
    for ext in ("jpg", "png", "gif"):
        (CONFIG.cache / "covers" / f"{bid}.{ext}").unlink(missing_ok=True)
    (CONFIG.cache / "text" / f"{bid}_0.txt").unlink(missing_ok=True)
