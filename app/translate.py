"""Перевод книг с английского на русский.

Движки:
  local  — CTranslate2 + модель en→ru от Argos Translate (бесплатно, офлайн);
  yandex — Yandex Cloud Translate API (платно, нужен ключ);
  fake   — для тестов: помечает строки, ничего не переводит.

Переводы хранятся в data/translations/<id книги>.txt и попадают в полнотекстовый индекс,
поэтому английские книги находятся и русскими запросами.
"""
from __future__ import annotations

import json
import os
import queue
import re
import threading
import time
import urllib.request
from pathlib import Path
from typing import Callable

from .config import CONFIG
from .db import get_conn
from .importer import load_text, reindex_book, translation_path

# ====================================================================== разбиение на предложения
_ABBR = {"mr", "mrs", "ms", "dr", "st", "jr", "sr", "vs", "prof", "capt", "col", "gen", "lt", "sgt",
         "rev", "hon", "no", "vol", "ch", "fig", "etc", "e.g", "i.e", "a.m", "p.m", "u.s", "mt"}
_SENT_RE = re.compile(r'(?<=[.!?…])["\'”’»)\]]*\s+')
MAX_CHUNK = 450


def split_sentences(par: str) -> list[str]:
    parts, last = [], 0
    for m in _SENT_RE.finditer(par):
        parts.append(par[last:m.end()].strip())
        last = m.end()
    if last < len(par):
        parts.append(par[last:].strip())
    # склеить ложные разрывы после сокращений (Mr. Smith) и инициалов (J. Smith)
    out: list[str] = []
    for p in parts:
        if not p:
            continue
        if out:
            prev = out[-1]
            w = re.search(r"([A-Za-z.]+)\.\W*$", prev)
            if w and (w.group(1).lower() in _ABBR or len(w.group(1)) == 1):
                out[-1] = prev + " " + p
                continue
        out.append(p)
    # слишком длинные куски — делим по запятым/точкам с запятой
    res: list[str] = []
    for s in out:
        while len(s) > MAX_CHUNK:
            cut = max(s.rfind("; ", 0, MAX_CHUNK), s.rfind(", ", 0, MAX_CHUNK), s.rfind(" — ", 0, MAX_CHUNK))
            if cut < 80:
                cut = s.rfind(" ", 0, MAX_CHUNK)
            if cut < 1:
                break
            res.append(s[:cut + 1].strip())
            s = s[cut + 1:].strip()
        if s:
            res.append(s)
    return res


# ====================================================================== движки
class EngineError(RuntimeError):
    pass


class LocalEngine:
    name = "local"

    def __init__(self, model_dir: Path):
        try:
            import ctranslate2
            import sentencepiece as spm
        except ImportError as e:
            raise EngineError("не установлены ctranslate2/sentencepiece — запустите update.bat") from e
        model_bin = next(model_dir.rglob("model.bin"), None) if model_dir.exists() else None
        if not model_bin:
            raise EngineError(f"модель не найдена в {model_dir} — выполните: python manage.py get-model")
        mdir = model_bin.parent
        # OPUS-MT (Helsinki-NLP): отдельные source.spm / target.spm; Argos: один *.model
        src = next(model_dir.rglob("source.spm"), None)
        tgt = next(model_dir.rglob("target.spm"), None)
        if src and tgt:
            self.kind = "opus-mt"
            self.sp_src = spm.SentencePieceProcessor(model_file=str(src))
            self.sp_tgt = spm.SentencePieceProcessor(model_file=str(tgt))
            vocab = " ".join(p.read_text(encoding="utf-8", errors="ignore")
                             for p in mdir.glob("*vocabulary*") if p.stat().st_size < 50_000_000)
            # групповые модели (eng-zle: рус/укр/бел) требуют метку целевого языка
            self.prefix = [">>rus<<"] if ">>rus<<" in vocab else []
        else:
            sp_file = next((p for p in model_dir.rglob("*.model") if p.is_file()), None)
            if not sp_file:
                raise EngineError(f"в {model_dir} нет токенизатора (*.model или source.spm/target.spm)")
            self.kind = "argos"
            self.sp_src = self.sp_tgt = spm.SentencePieceProcessor(model_file=str(sp_file))
            self.prefix = []
        threads = CONFIG.tr_threads or max(1, (os.cpu_count() or 2))
        self.tr = ctranslate2.Translator(str(mdir), device="cpu", compute_type="int8",
                                         inter_threads=1, intra_threads=threads)
        self.beam = max(1, CONFIG.tr_beam)
        self.name = f"local/{self.kind}"

    def translate(self, sents: list[str]) -> list[str]:
        toks = [self.prefix + self.sp_src.encode(s, out_type=str) for s in sents]
        res = self.tr.translate_batch(toks, beam_size=self.beam, max_batch_size=32, replace_unknowns=True,
                                      max_decoding_length=512)
        return [self.sp_tgt.decode(r.hypotheses[0]) for r in res]


class YandexEngine:
    name = "yandex"
    URL = "https://translate.api.cloud.yandex.net/translate/v2/translate"

    def __init__(self):
        if not CONFIG.tr_yandex_key:
            raise EngineError("не задан yandex_api_key в config.toml")

    def _call(self, texts: list[str]) -> list[str]:
        body = {"texts": texts, "sourceLanguageCode": "en", "targetLanguageCode": "ru", "format": "PLAIN_TEXT"}
        if CONFIG.tr_yandex_folder:
            body["folderId"] = CONFIG.tr_yandex_folder
        req = urllib.request.Request(self.URL, data=json.dumps(body).encode(), method="POST", headers={
            "Content-Type": "application/json", "Authorization": f"Api-Key {CONFIG.tr_yandex_key}"})
        for attempt in range(5):
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    data = json.load(r)
                return [t.get("text", "") for t in data.get("translations", [])]
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < 4:
                    time.sleep(2 + attempt * 3)
                    continue
                raise EngineError(f"Яндекс ответил {e.code}: {e.read()[:300]!r}") from e
        return []

    def translate(self, sents: list[str]) -> list[str]:
        out, batch, size = [], [], 0
        for s in sents:  # лимит API — 10 000 символов на запрос
            if batch and size + len(s) > 9000:
                out += self._call(batch)
                batch, size = [], 0
            batch.append(s)
            size += len(s)
        if batch:
            out += self._call(batch)
        return out


class FakeEngine:
    name = "fake"

    def translate(self, sents: list[str]) -> list[str]:
        time.sleep(0.001 * len(sents))
        return ["[перевод] " + s for s in sents]


_engine = None
_engine_lock = threading.Lock()


def get_engine():
    global _engine
    with _engine_lock:
        if _engine is None:
            kind = CONFIG.tr_engine
            if kind == "off":
                raise EngineError("перевод выключен в config.toml")
            _engine = {"local": lambda: LocalEngine(CONFIG.tr_model_dir), "yandex": YandexEngine,
                       "fake": FakeEngine}.get(kind, lambda: (_ for _ in ()).throw(
                           EngineError(f"неизвестный движок {kind}")))()
        return _engine


def reset_engine() -> None:
    """Сбросить загруженную модель (после смены модели в «Настройках»)."""
    global _engine
    with _engine_lock:
        _engine = None


def engine_status() -> str:
    """Короткая строка для админки."""
    if CONFIG.tr_engine == "off":
        return "выключен"
    if CONFIG.tr_engine == "local":
        ok = CONFIG.tr_model_dir.exists() and next(CONFIG.tr_model_dir.rglob("model.bin"), None)
        if not ok:
            return "локальный, модель НЕ установлена (get-model.bat или manage.py get-model)"
        from .models import describe
        return f"локальный: {describe(CONFIG.tr_model_dir)}"
    if CONFIG.tr_engine == "yandex":
        return "Яндекс" + ("" if CONFIG.tr_yandex_key else ", ключ не задан")
    return CONFIG.tr_engine


# ====================================================================== перевод текста
def translate_text(text: str, progress: Callable[[int, int], None] | None = None,
                   batch_chars: int = 6000) -> str:
    """Перевести текст, сохранив деление на абзацы (строки)."""
    eng = get_engine()
    lines = text.split("\n")
    plan: list[list[str]] = [split_sentences(ln) if ln.strip() else [] for ln in lines]
    flat = [s for p in plan for s in p]
    total = sum(len(s) for s in flat) or 1
    out: list[str] = []
    done, i = 0, 0
    while i < len(flat):
        j, size = i, 0
        while j < len(flat) and (size < batch_chars or j == i):
            size += len(flat[j])
            j += 1
        out += eng.translate(flat[i:j])
        done += size
        i = j
        if progress:
            progress(done, total)
    it = iter(out)
    return "\n".join(" ".join(next(it) for _ in p) for p in plan)


# ====================================================================== очередь переводов
STATUS: dict[int, dict] = {}   # прогресс текущих задач (в памяти)
_q: "queue.Queue[int]" = queue.Queue()
_worker: threading.Thread | None = None


def _ensure_worker():
    global _worker
    if _worker is None or not _worker.is_alive():
        _worker = threading.Thread(target=_work, daemon=True, name="translator")
        _worker.start()


def enqueue(book_ids: list[int], start: bool = True) -> int:
    """Поставить книги в очередь. Возвращает число новых задач.
    start=False — только записать в базу (сервер подхватит сам)."""
    added: list[int] = []
    with get_conn() as conn:
        for bid in book_ids:
            row = conn.execute("SELECT status FROM translations WHERE book_id=?", (bid,)).fetchone()
            if row and row["status"] in ("queued", "running", "done"):
                continue
            conn.execute("""INSERT INTO translations(book_id, status, engine) VALUES (?, 'queued', ?)
                            ON CONFLICT(book_id) DO UPDATE SET status='queued', error=NULL, engine=excluded.engine""",
                         (bid, CONFIG.tr_engine))
            added.append(bid)
    # в очередь — только после записи в базу, иначе поток-переводчик не увидит задачу
    if start:
        for bid in added:
            _q.put(bid)
        _ensure_worker()
    return len(added)


def resume_pending():
    """После перезапуска сервера — продолжить незавершённые переводы."""
    with get_conn() as conn:
        ids = [r[0] for r in conn.execute(
            "SELECT book_id FROM translations WHERE status IN ('queued','running') ORDER BY created")]
        conn.execute("UPDATE translations SET status='queued' WHERE status='running'")
    for bid in ids:
        _q.put(bid)
    _ensure_worker()


def queue_info() -> dict:
    with get_conn() as conn:
        rows = dict(conn.execute("SELECT status, count(*) FROM translations GROUP BY status").fetchall())
    cur = next((dict(v, book_id=k) for k, v in STATUS.items() if v.get("status") == "running"), None)
    return {"queued": rows.get("queued", 0), "running": rows.get("running", 0), "done": rows.get("done", 0),
            "error": rows.get("error", 0), "current": cur}


def get_status(book_id: int) -> dict:
    if book_id in STATUS and STATUS[book_id].get("status") == "running":
        return dict(STATUS[book_id])
    with get_conn() as conn:
        r = conn.execute("SELECT * FROM translations WHERE book_id=?", (book_id,)).fetchone()
    if not r:
        return {"status": "none"}
    d = dict(r)
    if d["status"] == "queued":
        d["position"] = _position(book_id)
    return d


def _position(book_id: int) -> int:
    with get_conn() as conn:
        ids = [r[0] for r in conn.execute("SELECT book_id FROM translations WHERE status='queued' ORDER BY created")]
    return ids.index(book_id) + 1 if book_id in ids else 0


def _work():
    while True:
        try:
            bid = _q.get(timeout=20)
        except queue.Empty:
            # задачи могли добавить из консоли (manage.py translate --all-en)
            with get_conn() as conn:
                for r in conn.execute("SELECT book_id FROM translations WHERE status='queued' ORDER BY created"):
                    _q.put(r[0])
            continue
        try:
            _translate_book(bid)
        except Exception as e:  # noqa: BLE001
            with get_conn() as conn:
                conn.execute("UPDATE translations SET status='error', error=? WHERE book_id=?", (str(e)[:500], bid))
            STATUS[bid] = {"status": "error", "error": str(e)}
        finally:
            _q.task_done()


def _translate_book(bid: int):
    with get_conn() as conn:
        row = conn.execute("SELECT status FROM translations WHERE book_id=?", (bid,)).fetchone()
        if not row or row["status"] != "queued":
            return
        b = conn.execute("SELECT * FROM books WHERE id=?", (bid,)).fetchone()
        if b is None:
            conn.execute("DELETE FROM translations WHERE book_id=?", (bid,))
            return
        b = dict(b)
        conn.execute("UPDATE translations SET status='running', started=datetime('now','localtime') WHERE book_id=?",
                     (bid,))
    st = STATUS[bid] = {"status": "running", "done": 0, "total": 1, "started": time.time(), "title": b["title"]}

    def progress(d, t):
        st["done"], st["total"] = d, t

    text = load_text(b)
    t0 = time.time()
    title_ru = translate_text(b["title"]) if b["title"] else ""
    ru = translate_text(text, progress)
    path = translation_path(bid)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(ru, encoding="utf-8")
    tmp.replace(path)
    secs = time.time() - t0
    with get_conn() as conn:
        conn.execute("""UPDATE translations SET status='done', title=?, chars=?, seconds=?, error=NULL,
                        finished=datetime('now','localtime'), engine=? WHERE book_id=?""",
                     (title_ru, len(ru), round(secs, 1), get_engine().name, bid))
        reindex_book(conn, bid)
    STATUS.pop(bid, None)


def delete_translation(bid: int):
    translation_path(bid).unlink(missing_ok=True)
    with get_conn() as conn:
        conn.execute("DELETE FROM translations WHERE book_id=?", (bid,))
        reindex_book(conn, bid)
