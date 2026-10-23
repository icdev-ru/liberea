"""Кеширование экспортов и фоновая сборка подборок."""
from __future__ import annotations

import hashlib
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .config import CONFIG
from .exporters import export_fb2, export_pdf, export_txt
from .importer import best_translation_path, book_path

EXPORTERS = {"pdf": export_pdf, "fb2": export_fb2}
_executor = ThreadPoolExecutor(max_workers=2)
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()
JOBS: dict[str, dict] = {}


def _lock_for(key: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def _book_sig(b: dict) -> str:
    if b.get("_variant") == "ru":
        tp = best_translation_path(b["id"])
        v = f"ru:{tp.name if tp else ''}:{tp.stat().st_mtime if tp else 0}:"
    else:
        v = ""
    return v + f"{b['id']}:{b.get('fmt')}:{b['txt_mtime']}:{b['txt_size']}:{b['cover_file']}:{b['cover_mtime']}:" \
           f"{b['title']}:{b['author']}:{b['code']}"


def _settings_sig() -> str:
    return f"{CONFIG.page_format}:{CONFIG.font_size}:{CONFIG.font_regular.name}:{CONFIG.fb2_genre}"


def book_file(b: dict, fmt: str) -> Path:
    """Готовый файл книги в нужном формате (генерируется при первом запросе)."""
    if fmt == "fb2" and b.get("fmt") == "fb2" and not b.get("_variant"):
        if b.get("src") == "inpx":
            from .inpx import original_file
            return original_file(b)  # FB2 из архива коллекции
        return book_path(b)  # исходник уже FB2 — отдаём оригинал
    d = CONFIG.cache / "books"
    d.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha1((_book_sig(b) + _settings_sig()).encode()).hexdigest()[:12]
    out = d / f"{b['id']}_{h}.{fmt}"
    if out.exists():
        return out
    with _lock_for(str(out)):
        if out.exists():
            return out
        tmp = out.with_suffix(f".{fmt}.tmp")
        if fmt == "txt":
            export_txt(b, tmp)
        else:
            EXPORTERS[fmt]([b], tmp)
        tmp.replace(out)
    return out


def collection_key(name: str, books: list[dict], fmt: str) -> str:
    sig = name + "|" + fmt + "|" + _settings_sig() + "|" + "|".join(_book_sig(b) for b in books)
    return hashlib.sha1(sig.encode()).hexdigest()[:16]


def submit_collection(name: str, books: list[dict], fmt: str) -> str:
    d = CONFIG.cache / "collections"
    d.mkdir(parents=True, exist_ok=True)
    key = collection_key(name, books, fmt)
    out = d / f"{key}.{fmt}"
    # та же сборка уже идёт — вернуть её
    for jid, j in JOBS.items():
        if j["key"] == key and j["status"] in ("queued", "running"):
            return jid
    jid = uuid.uuid4().hex[:12]
    job = {"id": jid, "key": key, "name": name, "fmt": fmt, "status": "queued", "done": 0,
           "total": len(books), "file": str(out), "error": None, "created": time.time()}
    JOBS[jid] = job
    if out.exists():
        job.update(status="done", done=len(books))
        return jid

    def progress(i, n):
        job["done"], job["total"] = i, n

    def work():
        job["status"] = "running"
        tmp = out.with_suffix(f".{fmt}.tmp")
        try:
            EXPORTERS[fmt](books, tmp, title=name, progress=progress)
            tmp.replace(out)
            job["status"] = "done"
        except Exception as e:  # noqa: BLE001
            job.update(status="error", error=repr(e))
            tmp.unlink(missing_ok=True)

    _executor.submit(work)
    _cleanup()
    return jid


def _cleanup():
    now = time.time()
    for jid in [k for k, j in JOBS.items() if now - j["created"] > 6 * 3600]:
        JOBS.pop(jid, None)


def clear_cache() -> int:
    n = 0
    for sub in ("books", "collections", "thumbs"):
        d = CONFIG.cache / sub
        if d.exists():
            for f in d.iterdir():
                if f.is_file():
                    f.unlink(missing_ok=True)
                    n += 1
    return n
