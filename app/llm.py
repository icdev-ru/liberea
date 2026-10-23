"""Качественный (литературный) перевод локальной языковой моделью.

Работает с любым OpenAI-совместимым сервером: LM Studio, Ollama, llama.cpp server.
Модель получает фрагмент оригинала (несколько абзацев) и, если есть, черновой машинный
перевод того же фрагмента, и пишет литературный перевод с тем же числом абзацев.
"""
from __future__ import annotations

import json
import queue
import re
import threading
import time
import urllib.error
import urllib.request
from typing import Callable

from .config import CONFIG
from .db import get_conn
from .importer import llm_translation_path, load_text, reindex_book, translation_path

DEFAULT_PROMPT = (
    "Ты — профессиональный литературный переводчик художественной прозы с английского на русский. "
    "Переводи точно и полностью, живым естественным русским языком, как в изданной книге: "
    "сохраняй смысл, стиль, тон, лексику и интонацию оригинала, ничего не пропускай, не смягчай "
    "и не добавляй от себя. Имена и названия передавай единообразно. Диалоги оформляй по-русски (тире). "
    "Если дан черновой машинный перевод — используй его только как подсказку и исправляй его ошибки. "
    "Выводи только перевод, без пояснений и комментариев."
)


class LLMError(RuntimeError):
    pass


# ====================================================================== клиент
class LLMClient:
    def __init__(self):
        self.url = CONFIG.llm_url.rstrip("/")
        self.model = CONFIG.llm_model or self._first_model()
        self.tokens = 0

    def _req(self, path: str, body: dict | None = None, timeout: int = 30) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, method="POST" if body else "GET",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            raise LLMError(f"сервер модели ответил {e.code}: {e.read()[:300]!r}") from e
        except urllib.error.URLError as e:
            raise LLMError(f"нет связи с сервером модели {self.url} ({e.reason}). "
                           f"Запущен ли LM Studio/Ollama и включён ли сервер?") from e

    def _first_model(self) -> str:
        data = self._req("/models")
        models = [m.get("id") for m in data.get("data", []) if m.get("id")]
        models = [m for m in models if "embed" not in m.lower()]
        if not models:
            raise LLMError("в LM Studio/Ollama не загружено ни одной модели")
        return models[0]

    def chat(self, system: str, user: str, max_tokens: int) -> str:
        body = {"model": self.model, "temperature": CONFIG.llm_temperature, "max_tokens": max_tokens,
                "stream": False, "messages": [{"role": "system", "content": system},
                                              {"role": "user", "content": user}]}
        data = self._req("/chat/completions", body, timeout=CONFIG.llm_timeout)
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError) as e:
            raise LLMError(f"неожиданный ответ сервера: {str(data)[:300]}") from e
        self.tokens += int((data.get("usage") or {}).get("completion_tokens") or 0)
        # «думающие» модели могут вставлять рассуждения
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
        return text.strip()


# ====================================================================== перевод текста
_LABEL_RE = re.compile(r"^\s*(перевод|translation|русский перевод)\s*:\s*", re.I)


def _chunks(idx: list[int], lines: list[str], max_chars: int) -> list[list[int]]:
    out, cur, size = [], [], 0
    for i in idx:
        n = len(lines[i])
        if cur and size + n > max_chars:
            out.append(cur)
            cur, size = [], 0
        cur.append(i)
        size += n
    if cur:
        out.append(cur)
    return out


def _split_answer(text: str, n: int) -> list[str] | None:
    text = _LABEL_RE.sub("", text.strip())
    parts = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if len(parts) == n:
        return parts
    lines = [p.strip() for p in text.split("\n") if p.strip()]
    if len(lines) == n:
        return lines
    return None


def llm_translate(text: str, draft: str | None = None, progress: Callable[[int, int], None] | None = None,
                  client: LLMClient | None = None) -> str:
    """Перевести текст, сохранив абзацы (строки). draft — черновой перевод с тем же числом строк."""
    client = client or LLMClient()
    system = CONFIG.llm_prompt or DEFAULT_PROMPT
    lines = text.split("\n")
    dlines = draft.split("\n") if draft else None
    if dlines is not None and len(dlines) != len(lines):
        dlines = None
    idx = [i for i, ln in enumerate(lines) if ln.strip()]
    chunks = _chunks(idx, lines, CONFIG.llm_chunk_chars)
    total = sum(len(lines[i]) for i in idx) or 1
    out = list(lines)
    prev_tail = ""
    done = 0
    for ch in chunks:
        n = len(ch)
        en = "\n\n".join(lines[i].strip() for i in ch)
        parts = [f"Переведи на русский фрагмент книги. В нём {n} абз. (разделены пустой строкой). "
                 f"В ответе должно быть ровно {n} абз. в том же порядке, тоже через пустую строку."]
        if prev_tail:
            parts.append("Конец предыдущего переведённого фрагмента (для связности, повторно не выводить):\n"
                         + prev_tail)
        parts.append("ОРИГИНАЛ:\n" + en)
        if dlines and CONFIG.llm_use_draft:
            parts.append("ЧЕРНОВОЙ МАШИННЫЙ ПЕРЕВОД (с ошибками, только как подсказка):\n"
                         + "\n\n".join(dlines[i].strip() for i in ch))
        user = "\n\n".join(parts)
        max_tok = int(len(en) * 1.0) + 400
        ans = client.chat(system, user, max_tok)
        res = _split_answer(ans, n)
        if res is None:  # одна повторная попытка — с напоминанием про абзацы
            ans = client.chat(system, user + f"\n\nВАЖНО: ровно {n} абзацев через пустую строку.", max_tok)
            res = _split_answer(ans, n)
        if res is None:  # не удалось выровнять — весь фрагмент целиком в первый абзац
            res = [re.sub(r"\s*\n+\s*", " ", _LABEL_RE.sub("", ans.strip()))] + [""] * (n - 1)
        for i, r in zip(ch, res):
            out[i] = re.sub(r"\s*\n+\s*", " ", r)
        prev_tail = res[-1][-300:] if res[-1] else prev_tail
        done += sum(len(lines[i]) for i in ch)
        if progress:
            progress(done, total)
    # строки, оставшиеся пустыми после склейки, убираем
    return "\n".join(ln for i, ln in enumerate(out) if ln or not lines[i].strip())


def llm_status_text() -> str:
    if not CONFIG.llm_enabled:
        return "выключен ([llm] enabled = false)"
    try:
        c = LLMClient()
        return f"подключено: {c.model} ({CONFIG.llm_url})"
    except LLMError as e:
        return f"нет связи: {e}"


# ====================================================================== очередь
STATUS: dict[int, dict] = {}
_q: "queue.Queue[int]" = queue.Queue()
_worker: threading.Thread | None = None


def _ensure_worker():
    global _worker
    if _worker is None or not _worker.is_alive():
        _worker = threading.Thread(target=_work, daemon=True, name="llm-translator")
        _worker.start()


def enqueue(book_ids: list[int], start: bool = True) -> int:
    added = []
    with get_conn() as conn:
        for bid in book_ids:
            row = conn.execute("SELECT status FROM translations_llm WHERE book_id=?", (bid,)).fetchone()
            if row and row["status"] in ("queued", "running", "done"):
                continue
            conn.execute("""INSERT INTO translations_llm(book_id, status) VALUES (?, 'queued')
                            ON CONFLICT(book_id) DO UPDATE SET status='queued', error=NULL""", (bid,))
            added.append(bid)
    if start:
        for bid in added:
            _q.put(bid)
        _ensure_worker()
    return len(added)


def resume_pending():
    if not CONFIG.llm_enabled:
        return
    with get_conn() as conn:
        conn.execute("UPDATE translations_llm SET status='queued' WHERE status='running'")
        ids = [r[0] for r in conn.execute(
            "SELECT book_id FROM translations_llm WHERE status='queued' ORDER BY created")]
    for bid in ids:
        _q.put(bid)
    _ensure_worker()


def queue_info() -> dict:
    with get_conn() as conn:
        rows = dict(conn.execute("SELECT status, count(*) FROM translations_llm GROUP BY status").fetchall())
    cur = next((dict(v, book_id=k) for k, v in STATUS.items() if v.get("status") == "running"), None)
    return {"queued": rows.get("queued", 0), "done": rows.get("done", 0), "error": rows.get("error", 0),
            "current": cur}


def get_status(book_id: int) -> dict:
    if book_id in STATUS and STATUS[book_id].get("status") == "running":
        return dict(STATUS[book_id])
    with get_conn() as conn:
        r = conn.execute("SELECT * FROM translations_llm WHERE book_id=?", (book_id,)).fetchone()
        if not r:
            return {"status": "none"}
        d = dict(r)
        if d["status"] == "queued":
            ids = [x[0] for x in conn.execute(
                "SELECT book_id FROM translations_llm WHERE status='queued' ORDER BY created")]
            d["position"] = ids.index(book_id) + 1 if book_id in ids else 0
    return d


def _work():
    while True:
        try:
            bid = _q.get(timeout=30)
        except queue.Empty:
            with get_conn() as conn:
                for r in conn.execute("SELECT book_id FROM translations_llm WHERE status='queued' ORDER BY created"):
                    _q.put(r[0])
            continue
        try:
            _translate_book(bid)
        except Exception as e:  # noqa: BLE001
            with get_conn() as conn:
                conn.execute("UPDATE translations_llm SET status='error', error=? WHERE book_id=?",
                             (str(e)[:500], bid))
            STATUS.pop(bid, None)
        finally:
            _q.task_done()


def _translate_book(bid: int):
    with get_conn() as conn:
        row = conn.execute("SELECT status FROM translations_llm WHERE book_id=?", (bid,)).fetchone()
        if not row or row["status"] != "queued":
            return
        b = conn.execute("SELECT * FROM books WHERE id=?", (bid,)).fetchone()
        if b is None:
            conn.execute("DELETE FROM translations_llm WHERE book_id=?", (bid,))
            return
        b = dict(b)
        conn.execute("UPDATE translations_llm SET status='running', started=datetime('now','localtime') "
                     "WHERE book_id=?", (bid,))
    client = LLMClient()
    st = STATUS[bid] = {"status": "running", "done": 0, "total": 1, "started": time.time(),
                        "title": b["title"], "model": client.model}

    def progress(d, t):
        st["done"], st["total"] = d, t
        el = time.time() - st["started"]
        st["eta_min"] = round(el / d * (t - d) / 60) if d else None

    text = load_text(b)
    dp = translation_path(bid)
    draft = dp.read_text(encoding="utf-8") if dp.exists() else None
    t0 = time.time()
    title_ru = llm_translate(b["title"], client=client) if b["title"] else ""
    ru = llm_translate(text, draft, progress, client)
    path = llm_translation_path(bid)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(ru, encoding="utf-8")
    tmp.replace(path)
    with get_conn() as conn:
        conn.execute("""UPDATE translations_llm SET status='done', title=?, chars=?, seconds=?, tokens=?,
                        engine=?, error=NULL, finished=datetime('now','localtime') WHERE book_id=?""",
                     (title_ru.split("\n")[0][:300], len(ru), round(time.time() - t0, 1), client.tokens,
                      client.model, bid))
        reindex_book(conn, bid)
    STATUS.pop(bid, None)


def delete_translation(bid: int):
    llm_translation_path(bid).unlink(missing_ok=True)
    with get_conn() as conn:
        conn.execute("DELETE FROM translations_llm WHERE book_id=?", (bid,))
        reindex_book(conn, bid)
