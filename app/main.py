"""Веб-приложение: открытая часть, подборки, админка."""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import secrets
import time
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from PIL import Image

from . import catalog, importer, inpx, jobs, llm, opds, translate
from .config import BASE_DIR, CONFIG, on_reload, raw_settings, save_settings
from .db import get_conn, init_db
from .genres import genre_name, genre_names
from .exporters import book_filename, cover_path, safe_filename
from .importer import book_path, load_text, reindex_book
from .search import decorate, search
from .textproc import build_query, split_paragraphs

APP_DIR = Path(__file__).parent


@asynccontextmanager
async def lifespan(_app):
    init_db()
    with get_conn() as conn:
        catalog.ensure(conn)
    translate.resume_pending()
    llm.resume_pending()
    yield


app = FastAPI(title="Liberea", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
app.include_router(opds.router)
templates = Jinja2Templates(directory=APP_DIR / "templates")
templates.env.globals["site_title"] = CONFIG.site_title

OWNER_COOKIE, ACTIVE_COOKIE, ADMIN_COOKIE = "bl_owner", "bl_coll", "bl_admin"
TEN_YEARS = 10 * 365 * 24 * 3600


# ====================================================================== утилиты
@app.middleware("http")
async def owner_cookie(request: Request, call_next):
    owner = request.cookies.get(OWNER_COOKIE)
    new = None
    if not owner or len(owner) < 16:
        owner = new = secrets.token_urlsafe(18)
    request.state.owner = owner
    response = await call_next(request)
    if new and not getattr(request.state, "owner_set", False):
        response.set_cookie(OWNER_COOKIE, new, max_age=TEN_YEARS, httponly=True, samesite="lax")
    return response


def active_collection(request: Request, conn) -> dict | None:
    cid = request.cookies.get(ACTIVE_COOKIE)
    row = None
    if cid and cid.isdigit():
        row = conn.execute("SELECT * FROM collections WHERE id=? AND owner=?",
                           (int(cid), request.state.owner)).fetchone()
    if row is None:
        row = conn.execute("SELECT * FROM collections WHERE owner=? ORDER BY updated DESC LIMIT 1",
                           (request.state.owner,)).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["count"] = conn.execute("SELECT count(*) FROM collection_items WHERE collection_id=?",
                              (d["id"],)).fetchone()[0]
    return d


def render(request: Request, tpl: str, conn=None, status: int = 200, **ctx) -> HTMLResponse:
    if conn is not None:
        ctx.setdefault("active", active_collection(request, conn))
        ac = ctx["active"]
        ctx["in_active"] = set(r[0] for r in conn.execute(
            "SELECT book_id FROM collection_items WHERE collection_id=?", (ac["id"],))) if ac else set()
    ctx["is_admin"] = is_admin(request)
    return templates.TemplateResponse(request, tpl, ctx, status_code=status)


def get_book(conn, book_id: int, allow_hidden: bool = False) -> dict:
    row = conn.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
    if row is None or ((row["hidden"] or row["src"] not in CONFIG.libs) and not allow_hidden):
        raise HTTPException(404, "Книга не найдена")
    return dict(row)


def ru_variant(conn, b: dict) -> dict | None:
    """Вариант книги «перевод на русский» (для выгрузок), если перевод готов."""
    if importer.best_translation_path(b["id"]) is None:
        return None
    titles = [r[0] for r in conn.execute(
        """SELECT title FROM translations_llm WHERE book_id=? AND status='done'
           UNION ALL SELECT title FROM translations WHERE book_id=? AND status='done'""", (b["id"], b["id"]))]
    if not titles:
        return None
    return dict(b, _variant="ru", title=next((t for t in titles if t), b["title"]), lang="ru")


def can_translate(request: Request) -> bool:
    return CONFIG.tr_engine != "off" and (CONFIG.tr_allow != "admin" or is_admin(request))


def can_llm(request: Request) -> bool:
    return CONFIG.llm_enabled and (CONFIG.tr_allow != "admin" or is_admin(request))


def own_collection(conn, request: Request, cid: int) -> dict:
    row = conn.execute("SELECT * FROM collections WHERE id=? AND owner=?",
                       (cid, request.state.owner)).fetchone()
    if row is None:
        raise HTTPException(404, "Подборка не найдена")
    return dict(row)


def collection_books(conn, cid: int, include_hidden=False) -> list[dict]:
    hid = "" if include_hidden else "AND b.hidden=0"
    return [dict(r) for r in conn.execute(
        f"""SELECT b.*, (SELECT status FROM translations t WHERE t.book_id=b.id) AS tr_status,
                   (SELECT status FROM translations_llm t WHERE t.book_id=b.id) AS llm_status
            FROM collection_items ci JOIN books b ON b.id=ci.book_id
            WHERE ci.collection_id=? {hid} ORDER BY ci.position, b.title""", (cid,))]


def touch(conn, cid: int):
    conn.execute("UPDATE collections SET updated=datetime('now','localtime') WHERE id=?", (cid,))


def ensure_active(conn, request: Request) -> tuple[dict, bool]:
    """Активная подборка; если нет ни одной — создать. Возвращает (подборка, создана_ли)."""
    ac = active_collection(request, conn)
    if ac is not None:
        return ac, False
    cid = conn.execute("INSERT INTO collections(owner, name) VALUES (?, ?)",
                       (request.state.owner, "Новая подборка")).lastrowid
    return {"id": cid, "name": "Новая подборка", "count": 0}, True


def json_with_active(data: dict, cid: int, created: bool) -> JSONResponse:
    r = JSONResponse(data)
    if created:
        r.set_cookie(ACTIVE_COOKIE, str(cid), max_age=TEN_YEARS, samesite="lax")
    return r


def add_books(conn, cid: int, ids: list[int]) -> int:
    pos = conn.execute("SELECT coalesce(max(position), 0) FROM collection_items WHERE collection_id=?",
                       (cid,)).fetchone()[0]
    added = 0
    for bid in ids:
        pos += 1
        cur = conn.execute("INSERT OR IGNORE INTO collection_items(collection_id, book_id, position) "
                           "VALUES (?,?,?)", (cid, bid, pos))
        added += cur.rowcount
    touch(conn, cid)
    return added


def page_url(request: Request, **changes) -> str:
    params = dict(request.query_params)
    params.update({k: v for k, v in changes.items()})
    return "?" + urlencode({k: v for k, v in params.items() if v not in (None, "")})


templates.env.globals["page_url"] = page_url
templates.env.globals["genre_names"] = genre_names
templates.env.globals["genre_name"] = genre_name
templates.env.globals["str_root"] = lambda p: bool(p) and str(p) != "."


@on_reload
def _template_globals():
    """Значения из конфигурации, которые видят шаблоны (обновляются после «Настроек»)."""
    templates.env.globals["site_title"] = CONFIG.site_title
    templates.env.globals["libs"] = CONFIG.libs
    templates.env.globals["lib_names"] = {"main": CONFIG.main_label, "rus": CONFIG.rus_label,
                                          "inpx": CONFIG.inpx_label}


_template_globals()
on_reload(translate.reset_engine)


def fmt_size(n: int | None) -> str:
    if not n:
        return "—"
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "Б" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} ТБ"


templates.env.filters["size"] = fmt_size


# ====================================================================== открытая часть
@app.get("/", response_class=HTMLResponse)
def index(request: Request, q: str = "", scope: str = "all", sort: str = "rank", page: int = 1,
          view: str = "", lib: str = "", lang: str = ""):
    # вид (карточки / только обложки) запоминается в cookie
    if view not in VIEWS:
        view = request.cookies.get("bl_view", "cards")
        view = view if view in VIEWS else "cards"
    covers = view == "covers"
    plain = view != "cards"   # сниппеты из текста — только в карточках
    q = q.strip()
    ctx = dict(q=q, scope=scope, sort=sort, page=page, view=view, lib=lib, lang=lang,
               has_inpx="inpx" in CONFIG.libs, inpx_label=CONFIG.inpx_label, tr_on=CONFIG.tr_engine != "off",
               can_tr=can_translate(request), can_llm=can_llm(request), books=[], names=[], total=0, pages=1)
    with get_conn() as conn:
        if not q:  # стартовая страница: краткая инструкция и статистика
            st = {r[0]: r[1] for r in conn.execute(
                f"SELECT src, count(*) FROM books b WHERE hidden=0 AND {CONFIG.src_sql()} GROUP BY src")}
            cnt = catalog._cnt_expr("")
            st["authors"] = conn.execute(f"SELECT count(*) FROM authors t WHERE {cnt} > 0").fetchone()[0]
            st["series"] = conn.execute(f"SELECT count(*) FROM series t WHERE {cnt} > 0").fetchone()[0]
            resp = render(request, "index.html", conn, start=True, st=st, **ctx)
        elif scope in ("author", "series"):
            per = 150
            names, total = catalog.find(conn, scope, q, page, per, lib)
            ctx.update(names=names, total=total, pages=max(1, math.ceil(total / per)))
            resp = render(request, "index.html", conn, **ctx)
        else:
            per = _per_page(view)
            books, total, _query = search(conn, q, scope, page, per, sort, with_snippets=not plain, lib=lib,
                                          lang=lang)
            ctx.update(books=books, total=total, pages=max(1, math.ceil(total / per)))
            resp = render(request, "index.html", conn, **ctx)
    if request.query_params.get("view") in VIEWS:
        resp.set_cookie("bl_view", view, max_age=TEN_YEARS, samesite="lax")
    return resp


AUTHOR_PER_PAGE = 200
VIEWS = ("cards", "covers", "list")


def _per_page(view: str) -> int:
    return {"covers": CONFIG.covers_page_size, "list": CONFIG.list_page_size}.get(view, CONFIG.page_size)


def _list_view(request: Request, view: str) -> str:
    if view in VIEWS:
        return view
    v = request.cookies.get("bl_view", "cards")
    return v if v in VIEWS else "cards"


def _remember_view(request: Request, resp):
    v = request.query_params.get("view")
    if v in VIEWS:
        resp.set_cookie("bl_view", v, max_age=TEN_YEARS, samesite="lax")
    return resp


@app.get("/author", response_class=HTMLResponse)
def author_page(request: Request, name: str, lang: str = "", lib: str = "", page: int = 1, view: str = ""):
    with get_conn() as conn:
        books = catalog.author_books(conn, name, lib, lang, include_hidden=is_admin(request))
        groups = catalog.group_by_series(books)
        # постранично, не разрывая порядок: серии, затем книги вне серий
        flat = [(s, b) for s, bs in groups for b in bs]
        pages = max(1, math.ceil(len(flat) / AUTHOR_PER_PAGE))
        chunk = flat[(page - 1) * AUTHOR_PER_PAGE: page * AUTHOR_PER_PAGE]
        shown: list[tuple] = []
        for s, b in chunk:
            if not shown or shown[-1][0] != s:
                shown.append((s, []))
            shown[-1][1].append(catalog.decorate_all([b])[0])
        nser = sum(1 for s, _ in groups if s)
        return _remember_view(request, render(
            request, "author.html", conn, name=name, groups=shown, total=len(books), nser=nser,
            page=page, pages=pages, lang=lang, lib=lib, can_tr=can_translate(request),
            view=_list_view(request, view)))


@app.get("/genres", response_class=HTMLResponse)
def genres_page(request: Request, lib: str = ""):
    with get_conn() as conn:
        groups = catalog.genre_groups(conn, lib)
        return render(request, "genres.html", conn, groups=groups, lib=lib)


@app.get("/genre/{code}", response_class=HTMLResponse)
def genre_page(request: Request, code: str, sort: str = "date", page: int = 1, lang: str = "", lib: str = "",
               view: str = ""):
    view = _list_view(request, view)
    per = _per_page(view)
    with get_conn() as conn:
        books, total = catalog.genre_books(conn, code, sort, page, per, lang, lib)
        return _remember_view(request, render(
            request, "genre.html", conn, code=code, name=genre_name(code), books=books, total=total,
            sort=sort if sort in catalog.GENRE_SORTS else "date", page=page,
            pages=max(1, math.ceil(total / per)), lang=lang, lib=lib, view=view, can_tr=can_translate(request)))


@app.get("/series", response_class=HTMLResponse)
def series_page(request: Request, name: str, lang: str = "", view: str = ""):
    with get_conn() as conn:
        books = catalog.decorate_all(catalog.series_books(conn, name, lang, include_hidden=is_admin(request)))
        authors: dict[str, int] = {}
        for b in books:
            for a in catalog.split_authors(b["author"]):
                authors[a] = authors.get(a, 0) + 1
        authors = sorted(authors, key=lambda a: -authors[a])[:5]
        return _remember_view(request, render(
            request, "series.html", conn, name=name, books=books, authors=authors, lang=lang,
            can_tr=can_translate(request), view=_list_view(request, view)))


@app.get("/book/{book_id}", response_class=HTMLResponse)
def book_page(request: Request, book_id: int, q: str = ""):
    with get_conn() as conn:
        b = get_book(conn, book_id, allow_hidden=is_admin(request))
        query = build_query(q) if q else None
        if query:
            b = decorate(b, query, snippet=False)
        try:
            text = load_text(b)
        except (OSError, zipfile.BadZipFile):
            text = ""
        preview = split_paragraphs(text[:6000])[:-1] if len(text) > 6000 else split_paragraphs(text)
        colls = [dict(r) for r in conn.execute(
            """SELECT c.id, c.name, EXISTS(SELECT 1 FROM collection_items i WHERE i.collection_id=c.id
               AND i.book_id=?) AS has FROM collections c WHERE owner=? ORDER BY name""",
            (book_id, request.state.owner))]
        tr = translate.get_status(book_id)
        lt = llm.get_status(book_id)
        tr_preview = []
        bp = importer.best_translation_path(book_id)
        if bp is not None and "done" in (tr.get("status"), lt.get("status")):
            try:
                rt = bp.read_text(encoding="utf-8")
                tr_preview = split_paragraphs(rt[:6000])[:-1] if len(rt) > 6000 else split_paragraphs(rt)
            except OSError:
                pass
        return render(request, "book.html", conn, b=b, preview=preview, colls=colls, q=q,
                      has_cover=cover_path(b) is not None, tr=tr, lt=lt, tr_preview=tr_preview,
                      can_tr=can_translate(request), can_llm=can_llm(request),
                      has_ru=ru_variant(conn, b) is not None, genres=genre_names(b.get("genre")))


MIME = {"txt": "text/plain; charset=utf-8", "pdf": "application/pdf", "fb2": "application/x-fictionbook+xml"}


@app.get("/book/{book_id}/open/{name}")
def open_inline(request: Request, book_id: int, name: str):
    """Файл «для открытия» (inline). Имя файла в URL заканчивается на .txt/.pdf/.fb2 —
    так читалки на Android правильно определяют формат."""
    fmt = name.rsplit(".", 1)[-1].lower()
    if fmt not in MIME:
        raise HTTPException(404)
    with get_conn() as conn:
        b = pick_variant(conn, get_book(conn, book_id, allow_hidden=is_admin(request)), request)
    try:
        path = jobs.book_file(b, fmt)
    except (OSError, zipfile.BadZipFile) as e:
        raise HTTPException(500, f"Не удалось прочитать файл книги: {e}") from e
    return FileResponse(path, media_type=MIME[fmt], filename=book_filename(b, fmt),
                        content_disposition_type="inline")


def pick_variant(conn, b: dict, request: Request) -> dict:
    if request.query_params.get("ru") == "1":
        v = ru_variant(conn, b)
        if v is None:
            raise HTTPException(404, "Перевод ещё не готов")
        return v
    return b


@app.get("/book/{book_id}/download.{fmt}")
def download(request: Request, book_id: int, fmt: str):
    if fmt not in ("txt", "pdf", "fb2"):
        raise HTTPException(404)
    with get_conn() as conn:
        b = pick_variant(conn, get_book(conn, book_id, allow_hidden=is_admin(request)), request)
    try:
        path = jobs.book_file(b, fmt)
    except (OSError, zipfile.BadZipFile) as e:
        raise HTTPException(500, f"Не удалось прочитать файл книги: {e}") from e
    media = {"txt": "text/plain; charset=utf-8", "pdf": "application/pdf",
             "fb2": "application/x-fictionbook+xml"}[fmt]
    inline = request.query_params.get("view") == "1"
    return FileResponse(path, media_type=media, filename=book_filename(b, fmt),
                        content_disposition_type="inline" if inline else "attachment")


@app.get("/cover/{book_id}")
def cover(book_id: int, thumb: int = 0):
    with get_conn() as conn:
        b = get_book(conn, book_id, allow_hidden=True)
    p = cover_path(b)
    if p is None:
        raise HTTPException(404)
    if not thumb:
        return FileResponse(p, headers={"Cache-Control": "max-age=86400"})
    d = CONFIG.cache / "thumbs"
    d.mkdir(parents=True, exist_ok=True)
    t = d / f"{book_id}_{int(b['cover_mtime'] or 0)}.jpg"
    if not t.exists():
        try:
            with Image.open(p) as im:
                im = im.convert("RGB")
                im.thumbnail((360, 540))
                im.save(t, "JPEG", quality=82, optimize=True)
        except Exception:  # noqa: BLE001
            raise HTTPException(404) from None
    return FileResponse(t, media_type="image/jpeg", headers={"Cache-Control": "max-age=604800"})


# ====================================================================== подборки (API)
@app.post("/api/collect/add")
def api_add(request: Request, book_id: int = Form(...), collection_id: int | None = Form(None)):
    with get_conn() as conn:
        get_book(conn, book_id)
        created = False
        if collection_id:
            ac = own_collection(conn, request, collection_id)
        else:
            ac, created = ensure_active(conn, request)
        add_books(conn, ac["id"], [book_id])
        count = conn.execute("SELECT count(*) FROM collection_items WHERE collection_id=?",
                             (ac["id"],)).fetchone()[0]
    return json_with_active({"ok": True, "id": ac["id"], "name": ac["name"], "count": count},
                            ac["id"], created)


@app.post("/api/collect/remove")
def api_remove(request: Request, book_id: int = Form(...), collection_id: int | None = Form(None)):
    with get_conn() as conn:
        if collection_id:
            ac = own_collection(conn, request, collection_id)
        else:
            ac = active_collection(request, conn)
            if ac is None:
                return {"ok": False}
        conn.execute("DELETE FROM collection_items WHERE collection_id=? AND book_id=?", (ac["id"], book_id))
        touch(conn, ac["id"])
        count = conn.execute("SELECT count(*) FROM collection_items WHERE collection_id=?",
                             (ac["id"],)).fetchone()[0]
    return {"ok": True, "id": ac["id"], "name": ac["name"], "count": count}


@app.post("/api/collect/add-search")
def api_add_search(request: Request, q: str = Form(""), scope: str = Form("all"), lib: str = Form(""),
                   lang: str = Form(""), author: str = Form(""), series: str = Form("")):
    if not (q.strip() or author or series):
        return {"ok": False, "error": "пустой запрос"}
    with get_conn() as conn:
        if author:
            books = [b for _s, bs in catalog.group_by_series(catalog.author_books(conn, author, lib, lang))
                     for b in bs][:2000]
            total = len(books)
        elif series:
            books = catalog.series_books(conn, series, lang)[:2000]
            total = len(books)
        else:
            books, total, _ = search(conn, q, scope, with_snippets=False, limit_all=2000, lib=lib, lang=lang)
        ac, created = ensure_active(conn, request)
        added = add_books(conn, ac["id"], [b["id"] for b in books])
        count = conn.execute("SELECT count(*) FROM collection_items WHERE collection_id=?",
                             (ac["id"],)).fetchone()[0]
    return json_with_active({"ok": True, "id": ac["id"], "name": ac["name"], "count": count,
                             "added": added, "found": total}, ac["id"], created)


@app.post("/api/my/{cid}/order")
async def api_order(request: Request, cid: int):
    ids = (await request.json()).get("ids", [])
    with get_conn() as conn:
        own_collection(conn, request, cid)
        for pos, bid in enumerate(ids, 1):
            conn.execute("UPDATE collection_items SET position=? WHERE collection_id=? AND book_id=?",
                         (pos, cid, int(bid)))
        touch(conn, cid)
    return {"ok": True}


@app.post("/api/my/{cid}/build")
def api_build(request: Request, cid: int, fmt: str = Form(...), ru: int = Form(0)):
    if fmt not in ("pdf", "fb2"):
        raise HTTPException(400)
    with get_conn() as conn:
        c = own_collection(conn, request, cid)
        books = collection_books(conn, cid)
        if ru:
            books = [ru_variant(conn, b) or b for b in books]
            c["name"] = c["name"] + " [RU]"
    if not books:
        return {"ok": False, "error": "Подборка пуста"}
    jid = jobs.submit_collection(c["name"], books, fmt)
    return {"ok": True, "job": jid}


@app.post("/api/translate/{book_id}")
def api_translate(request: Request, book_id: int):
    if not can_translate(request):
        raise HTTPException(403, "Перевод недоступен")
    with get_conn() as conn:
        get_book(conn, book_id, allow_hidden=is_admin(request))
    try:
        if CONFIG.tr_engine == "local":
            translate.get_engine()  # сразу сообщить, если модель не установлена
    except translate.EngineError as e:
        return {"ok": False, "error": str(e)}
    translate.enqueue([book_id])
    return {"ok": True, **_tr_public(translate.get_status(book_id))}


@app.post("/api/my/{cid}/translate")
def api_collection_translate(request: Request, cid: int):
    """Поставить в очередь перевода все английские книги подборки, у которых ещё нет перевода."""
    if not can_translate(request):
        raise HTTPException(403, "Перевод недоступен")
    try:
        translate.get_engine()
    except translate.EngineError as e:
        return {"ok": False, "error": str(e)}
    with get_conn() as conn:
        own_collection(conn, request, cid)
        ids = [b["id"] for b in collection_books(conn, cid)
               if b["lang"] == "en" and b["tr_status"] not in ("done", "queued", "running")]
    n = translate.enqueue(ids)
    return {"ok": True, "queued": n}


@app.post("/api/llm/{book_id}")
def api_llm(request: Request, book_id: int):
    if not can_llm(request):
        raise HTTPException(403, "Качественный перевод выключен")
    with get_conn() as conn:
        get_book(conn, book_id, allow_hidden=is_admin(request))
    try:
        llm.LLMClient()  # проверить связь с LM Studio/Ollama сразу
    except llm.LLMError as e:
        return {"ok": False, "error": str(e)}
    llm.enqueue([book_id])
    return {"ok": True}


@app.get("/api/llm/{book_id}")
def api_llm_status(book_id: int):
    s = llm.get_status(book_id)
    return {k: s.get(k) for k in ("status", "done", "total", "error", "position", "eta_min", "model", "engine")
            if k in s}


@app.post("/api/my/{cid}/llm")
def api_collection_llm(request: Request, cid: int):
    if not can_llm(request):
        raise HTTPException(403, "Качественный перевод выключен")
    try:
        llm.LLMClient()
    except llm.LLMError as e:
        return {"ok": False, "error": str(e)}
    with get_conn() as conn:
        own_collection(conn, request, cid)
        ids = [b["id"] for b in collection_books(conn, cid)
               if b["lang"] == "en" and b["llm_status"] not in ("done", "queued", "running")]
    return {"ok": True, "queued": llm.enqueue(ids)}


@app.post("/admin/llm/{book_id}/delete")
def admin_llm_delete(request: Request, book_id: int):
    require_admin(request)
    llm.delete_translation(book_id)
    return RedirectResponse(f"/book/{book_id}", 303)


@app.get("/api/translate/{book_id}")
def api_translate_status(book_id: int):
    return _tr_public(translate.get_status(book_id))


def _tr_public(s: dict) -> dict:
    keys = ("status", "done", "total", "error", "position", "chars", "seconds", "title")
    return {k: s.get(k) for k in keys if k in s}


@app.get("/api/jobs/{jid}")
def api_job(jid: str):
    j = jobs.JOBS.get(jid)
    if not j:
        raise HTTPException(404)
    return {k: j[k] for k in ("id", "status", "done", "total", "error", "fmt", "name")}


@app.get("/jobs/{jid}/file")
def job_file(jid: str):
    j = jobs.JOBS.get(jid)
    if not j or j["status"] != "done":
        raise HTTPException(404)
    media = "application/pdf" if j["fmt"] == "pdf" else "application/x-fictionbook+xml"
    return FileResponse(j["file"], media_type=media, filename=safe_filename(j["name"]) + "." + j["fmt"])


# ====================================================================== подборки (страницы)
@app.get("/my", response_class=HTMLResponse)
def my(request: Request, msg: str = ""):
    with get_conn() as conn:
        colls = [dict(r) for r in conn.execute(
            """SELECT c.*, (SELECT count(*) FROM collection_items i WHERE i.collection_id=c.id) AS n
               FROM collections c WHERE owner=? ORDER BY updated DESC""", (request.state.owner,))]
        return render(request, "my.html", conn, colls=colls, owner=request.state.owner, msg=msg)


@app.post("/my/new")
def my_new(request: Request, name: str = Form("Новая подборка")):
    with get_conn() as conn:
        cid = conn.execute("INSERT INTO collections(owner, name) VALUES (?,?)",
                           (request.state.owner, name.strip() or "Новая подборка")).lastrowid
    r = RedirectResponse(f"/my/{cid}", 303)
    r.set_cookie(ACTIVE_COOKIE, str(cid), max_age=TEN_YEARS, samesite="lax")
    return r


@app.get("/my/{cid}", response_class=HTMLResponse)
def my_collection(request: Request, cid: int):
    with get_conn() as conn:
        c = own_collection(conn, request, cid)
        books = collection_books(conn, cid)
        untr = sum(1 for b in books if b["lang"] == "en" and b["tr_status"] not in ("done", "queued", "running"))
        unllm = sum(1 for b in books if b["lang"] == "en" and b["llm_status"] not in ("done", "queued", "running"))
        return render(request, "collection.html", conn, c=c, books=books, untr=untr, unllm=unllm,
                      can_tr=can_translate(request), can_llm=can_llm(request))


@app.post("/my/{cid}/activate")
def my_activate(request: Request, cid: int, back: str = Form("/my")):
    with get_conn() as conn:
        own_collection(conn, request, cid)
        touch(conn, cid)
    r = RedirectResponse(back if back.startswith("/") else "/my", 303)
    r.set_cookie(ACTIVE_COOKIE, str(cid), max_age=TEN_YEARS, samesite="lax")
    return r


@app.post("/my/{cid}/rename")
def my_rename(request: Request, cid: int, name: str = Form(...), back: str = Form("")):
    with get_conn() as conn:
        own_collection(conn, request, cid)
        conn.execute("UPDATE collections SET name=?, updated=datetime('now','localtime') WHERE id=?",
                     ((name.strip() or "Без названия")[:200], cid))
    return RedirectResponse(back if back.startswith("/my") else f"/my/{cid}", 303)


@app.post("/my/{cid}/copy")
def my_copy(request: Request, cid: int):
    with get_conn() as conn:
        c = own_collection(conn, request, cid)
        new = conn.execute("INSERT INTO collections(owner, name) VALUES (?,?)",
                           (request.state.owner, c["name"] + " (копия)")).lastrowid
        conn.execute("""INSERT INTO collection_items(collection_id, book_id, position)
                        SELECT ?, book_id, position FROM collection_items WHERE collection_id=?""", (new, cid))
    return RedirectResponse(f"/my/{new}", 303)


@app.post("/my/{cid}/clear")
def my_clear(request: Request, cid: int):
    with get_conn() as conn:
        own_collection(conn, request, cid)
        conn.execute("DELETE FROM collection_items WHERE collection_id=?", (cid,))
        touch(conn, cid)
    return RedirectResponse(f"/my/{cid}", 303)


@app.post("/my/{cid}/delete")
def my_delete(request: Request, cid: int):
    with get_conn() as conn:
        own_collection(conn, request, cid)
        conn.execute("DELETE FROM collections WHERE id=?", (cid,))
    return RedirectResponse("/my", 303)


@app.get("/my/{cid}/export.json")
def my_export(request: Request, cid: int):
    with get_conn() as conn:
        c = own_collection(conn, request, cid)
        books = collection_books(conn, cid, include_hidden=True)
    data = {"format": "liberea-collection", "version": 1, "name": c["name"],
            "books": [{"dir_name": b["dir_name"], "code": b["code"], "title": b["title"],
                       "author": b["author"]} for b in books]}
    body = json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8")
    fname = safe_filename(c["name"]) + ".json"
    from urllib.parse import quote
    return Response(body, media_type="application/json",
                    headers={"Content-Disposition": f"attachment; filename*=utf-8''{quote(fname)}"})


@app.post("/my/import")
async def my_import(request: Request, file: UploadFile = File(...)):
    try:
        data = json.loads((await file.read()).decode("utf-8-sig"))
        items = data["books"]
        name = str(data.get("name") or Path(file.filename or "Импорт").stem)
    except Exception:  # noqa: BLE001
        return RedirectResponse("/my?" + urlencode({"msg": "Не удалось прочитать файл подборки"}), 303)
    with get_conn() as conn:
        ids, missing = [], 0
        for it in items:
            row = conn.execute("SELECT id FROM books WHERE dir_name=?", (it.get("dir_name", ""),)).fetchone()
            if row is None and it.get("code"):
                row = conn.execute("SELECT id FROM books WHERE code=? COLLATE NOCASE", (it["code"],)).fetchone()
            if row:
                ids.append(row[0])
            else:
                missing += 1
        cid = conn.execute("INSERT INTO collections(owner, name) VALUES (?,?)",
                           (request.state.owner, name)).lastrowid
        add_books(conn, cid, ids)
    msg = f"Импортировано книг: {len(ids)}" + (f", не найдено в библиотеке: {missing}" if missing else "")
    r = RedirectResponse(f"/my?{urlencode({'msg': msg})}", 303)
    r.set_cookie(ACTIVE_COOKIE, str(cid), max_age=TEN_YEARS, samesite="lax")
    return r


@app.post("/my/key")
def my_key(request: Request, key: str = Form(...)):
    key = key.strip()
    if len(key) < 16:
        return RedirectResponse("/my?" + urlencode({"msg": "Ключ слишком короткий"}), 303)
    r = RedirectResponse("/my?" + urlencode({"msg": "Ключ применён — показаны подборки этого ключа"}), 303)
    r.set_cookie(OWNER_COOKIE, key, max_age=TEN_YEARS, httponly=True, samesite="lax")
    r.delete_cookie(ACTIVE_COOKIE)
    request.state.owner_set = True
    return r


# ====================================================================== админка
def _pw_ok(pw: str) -> bool:
    h = CONFIG.admin_password_hash
    if h and not h.startswith("pbkdf2$"):
        # в password_hash записан обычный пароль — принимаем его как есть
        return hmac.compare_digest(pw, h)
    if h:
        try:
            _, it, salt, digest = h.split("$")
            calc = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), int(it)).hex()
            return hmac.compare_digest(calc, digest)
        except ValueError:
            return False
    return bool(CONFIG.admin_password) and hmac.compare_digest(pw, CONFIG.admin_password)


def _admin_token() -> str:
    basis = "admin|" + (CONFIG.admin_password_hash or CONFIG.admin_password)
    return hmac.new(CONFIG.secret.encode(), basis.encode(), hashlib.sha256).hexdigest()


def is_admin(request: Request) -> bool:
    t = request.cookies.get(ADMIN_COOKIE)
    return bool(t) and hmac.compare_digest(t, _admin_token())


def require_admin(request: Request):
    if not admin_configured():
        raise HTTPException(303, headers={"Location": "/setup"})
    if not is_admin(request):
        raise HTTPException(303, headers={"Location": "/admin/login"})


def admin_configured() -> bool:
    return bool(CONFIG.admin_password_hash or CONFIG.admin_password)


def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    it = 200_000
    return f"pbkdf2${it}${salt.hex()}${hashlib.pbkdf2_hmac('sha256', pw.encode(), salt, it).hex()}"


# защита от подбора пароля: после 5 неудачных попыток подряд — пауза, растущая до 15 минут
_fails: dict[str, list[float]] = {}


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"


def _locked(ip: str) -> int:
    rec = _fails.get(ip)
    if not rec:
        return 0
    return max(0, int(rec[1] - time.time()))


def _fail(ip: str) -> None:
    n, _until = _fails.get(ip, [0, 0])
    n += 1
    until = time.time() + min(900, 30 * 2 ** (n - 5)) if n >= 5 else 0
    _fails[ip] = [n, until]


@app.get("/admin/login", response_class=HTMLResponse)
def admin_login_form(request: Request, err: int = 0):
    if not admin_configured():
        return RedirectResponse("/setup", 303)
    return render(request, "admin_login.html", err=err, wait=_locked(_client_ip(request)))


@app.post("/admin/login")
def admin_login(request: Request, password: str = Form(...)):
    ip = _client_ip(request)
    if _locked(ip):
        return RedirectResponse("/admin/login?err=2", 303)
    if not _pw_ok(password):
        _fail(ip)
        return RedirectResponse("/admin/login?err=1", 303)
    _fails.pop(ip, None)
    r = RedirectResponse("/admin", 303)
    r.set_cookie(ADMIN_COOKIE, _admin_token(), httponly=True, samesite="lax", max_age=30 * 24 * 3600)
    return r


# ====================================================================== первый запуск
@app.get("/setup", response_class=HTMLResponse)
def setup_form(request: Request, err: str = ""):
    if admin_configured():
        return RedirectResponse("/admin", 303)
    return render(request, "setup.html", err=err, cfg=CONFIG)


@app.post("/setup")
def setup_save(request: Request, password: str = Form(...), password2: str = Form(...),
               title: str = Form(""), main_root: str = Form(""), extra_root: str = Form(""),
               inpx_file: str = Form("")):
    if admin_configured():
        return RedirectResponse("/admin", 303)
    if len(password) < 6:
        return RedirectResponse("/setup?err=short", 303)
    if password != password2:
        return RedirectResponse("/setup?err=mismatch", 303)
    changes: dict = {"admin": {"password_hash": hash_password(password), "password": ""}}
    if title.strip():
        changes["site"] = {"title": title.strip()}
    for sec, key, val in (("library", "root", main_root), ("extra", "root", extra_root), ("inpx", "file", inpx_file)):
        if val.strip():
            changes.setdefault(sec, {})[key] = val.strip().strip('"')
    save_settings(changes)
    r = RedirectResponse("/admin?welcome=1", 303)
    r.set_cookie(ADMIN_COOKIE, _admin_token(), httponly=True, samesite="lax", max_age=30 * 24 * 3600)
    return r


# ====================================================================== настройки
SETTINGS_FIELDS = [
    # (раздел, ключ, тип, подпись, подсказка)
    ("site", "title", "str", "Название сайта", "Показывается в шапке и в каталоге OPDS"),
    ("library", "enabled", "bool", "Основная библиотека включена", ""),
    ("library", "label", "str", "Название основной библиотеки", ""),
    ("library", "root", "path", "Папка основной библиотеки", "Внутри — папки «код Название (Автор)» с .txt и обложкой"),
    ("extra", "enabled", "bool", "Дополнительная библиотека включена", ""),
    ("extra", "label", "str", "Название дополнительной библиотеки", ""),
    ("extra", "root", "path", "Папка дополнительной библиотеки", "Все подпапки; каждый .txt/.fb2 — книга"),
    ("inpx", "enabled", "bool", "Коллекция INPX включена", ""),
    ("inpx", "label", "str", "Название коллекции INPX", ""),
    ("inpx", "file", "path", "Файл .inpx", "Каталог MyHomeLib; архивы с книгами — рядом с ним"),
    ("inpx", "root", "path", "Папка с архивами коллекции", "Пусто — папка файла .inpx"),
    ("translate", "model", "model", "Модель перевода (англ. → рус.)",
     "Установка моделей: get-model.bat (Argos, быстрая) и get-model.bat --opus-big (OPUS-MT, качественная)"),
]


def _settings_values() -> dict:
    from .config import merged_raw
    raw = merged_raw()
    raw["extra"] = raw.get("extra") or raw.get("rus") or {}
    defaults = {("site", "title"): "Либерея", ("library", "label"): "Основная",
                ("extra", "label"): CONFIG.rus_label, ("inpx", "label"): "Коллекция INPX"}
    out = {}
    tr = raw.get("translate", {})
    out[("translate", "model")] = ("off" if tr.get("engine") == "off" else
                                   "yandex" if tr.get("engine") == "yandex" else
                                   str(tr.get("model_dir", "data/models/en_ru")).replace("\\", "/"))
    for sec, key, typ, _l, _h in SETTINGS_FIELDS:
        if typ == "model":
            continue
        v = raw.get(sec, {}).get(key)
        if typ == "bool":
            out[(sec, key)] = True if v is None else bool(v)
        else:
            out[(sec, key)] = str(v) if v not in (None, "") else ("" if typ == "path" else defaults.get((sec, key), ""))
    return out


def _legacy_section(sec: str) -> str:
    """Старые конфиги используют раздел [rus] вместо [extra] — сохраняем туда же."""
    if sec != "extra":
        return sec
    from .config import _base_raw
    raw = _base_raw()
    return "rus" if ("extra" not in raw and "rus" in raw and "extra" not in raw_settings()) else "extra"


@app.get("/admin/settings", response_class=HTMLResponse)
def admin_settings(request: Request, saved: int = 0, err: str = ""):
    require_admin(request)
    vals = _settings_values()
    from . import models
    choices = list(models.installed())
    if vals[("translate", "model")] not in [c[0] for c in choices] + ["off", "yandex"]:
        choices.append((vals[("translate", "model")], vals[("translate", "model")] + " — не установлена"))
    if CONFIG.tr_yandex_key or vals[("translate", "model")] == "yandex":
        choices.append(("yandex", "Яндекс Переводчик (Yandex Cloud, ключ в config.toml)"))
    choices.append(("off", "Выключен"))
    fields = [dict(sec=sec, key=key, typ=typ, label=label, hint=hint, value=vals[(sec, key)], choices=choices,
                   exists=(Path(vals[(sec, key)]).exists() if typ == "path" and vals[(sec, key)] else None))
              for sec, key, typ, label, hint in SETTINGS_FIELDS]
    from .config import settings_path
    return render(request, "admin_settings.html", fields=fields, saved=saved, err=err,
                  settings_file=settings_path(), cfg=CONFIG)


@app.post("/admin/settings")
async def admin_settings_save(request: Request):
    require_admin(request)
    form = await request.form()
    changes: dict = {}
    for sec, key, typ, _label, _hint in SETTINGS_FIELDS:
        name = f"{sec}.{key}"
        if typ == "model":
            m = str(form.get(name, ""))
            if m == "off":
                changes["translate"] = {"engine": "off"}
            elif m == "yandex":
                changes["translate"] = {"engine": "yandex"}
            elif m.startswith("data/models/"):
                changes["translate"] = {"engine": "local", "model_dir": m}
            continue
        if typ == "bool":
            val = form.get(name) == "1"
        else:
            val = str(form.get(name, "")).strip().strip('"')
        changes.setdefault(_legacy_section(sec), {})[key] = val
    pw, pw2 = str(form.get("password", "")), str(form.get("password2", ""))
    if pw or pw2:
        if len(pw) < 6:
            return RedirectResponse("/admin/settings?err=short", 303)
        if pw != pw2:
            return RedirectResponse("/admin/settings?err=mismatch", 303)
        changes["admin"] = {"password_hash": hash_password(pw), "password": ""}
    save_settings(changes)
    r = RedirectResponse("/admin/settings?saved=1", 303)
    if "admin" in changes:  # новый пароль — новый токен входа
        r.set_cookie(ADMIN_COOKIE, _admin_token(), httponly=True, samesite="lax", max_age=30 * 24 * 3600)
    return r


@app.get("/admin/logout")
def admin_logout():
    r = RedirectResponse("/", 303)
    r.delete_cookie(ADMIN_COOKIE)
    return r


@app.get("/admin", response_class=HTMLResponse)
def admin_home(request: Request):
    require_admin(request)
    with get_conn() as conn:
        st = dict(conn.execute(
            """SELECT count(*) AS books, sum(hidden) AS hidden,
                      sum(cover_file IS NOT NULL AND cover_file != '?') AS covers,
                      sum(txt_size) AS size, sum(meta_locked) AS locked FROM books""").fetchone())
        for src in ("main", "rus", "inpx"):
            st[src] = conn.execute("SELECT count(*) FROM books WHERE src=?", (src,)).fetchone()[0]
        st["collections"] = conn.execute("SELECT count(*) FROM collections").fetchone()[0]
        st["owners"] = conn.execute("SELECT count(DISTINCT owner) FROM collections").fetchone()[0]
        scans = [dict(r) for r in conn.execute("SELECT * FROM scans ORDER BY id DESC LIMIT 10")]
        db_size = sum(p.stat().st_size for p in CONFIG.database.parent.glob(CONFIG.database.name + "*"))
        cache_size = sum(f.stat().st_size for f in CONFIG.cache.rglob("*") if f.is_file())
        encs = conn.execute("SELECT encoding, count(*) FROM books GROUP BY encoding ORDER BY 2 DESC").fetchall()
        st["en"] = conn.execute("SELECT count(*) FROM books WHERE lang='en'").fetchone()[0]
        return render(request, "admin_home.html", conn, st=st, scans=scans, db_size=db_size,
                      cache_size=cache_size, cfg=CONFIG, scan=importer.STATE, encs=encs,
                      inpx_info=inpx.collection_info() if CONFIG.inpx_file else None,
                      tr_engine=translate.engine_status(), trq=translate.queue_info(),
                      llm_state=llm.llm_status_text(), llmq=llm.queue_info())


@app.post("/admin/scan")
def admin_scan(request: Request, force: int = Form(0), kind: str = Form("main")):
    require_admin(request)
    if kind not in CONFIG.libs:
        return {"ok": False, "error": "эта часть библиотеки отключена в config.toml"}
    started = importer.start_scan_thread(bool(force), kind)
    return {"ok": started}


@app.get("/admin/scan/status")
def admin_scan_status(request: Request):
    require_admin(request)
    s = dict(importer.STATE)
    return s


@app.post("/admin/translate-search")
def admin_translate_search(request: Request, q: str = Form(""), scope: str = Form("all"), lib: str = Form(""),
                           lang: str = Form("")):
    require_admin(request)
    try:
        translate.get_engine()
    except translate.EngineError as e:
        return {"ok": False, "error": str(e)}
    with get_conn() as conn:
        books, total, _ = search(conn, q, scope, with_snippets=False, limit_all=5000, lib=lib, lang=lang)
        done = {r[0] for r in conn.execute("SELECT book_id FROM translations WHERE status IN ('done','queued','running')")}
    ids = [b["id"] for b in books if b["lang"] == "en" and b["id"] not in done]
    n = translate.enqueue(ids)
    return {"ok": True, "queued": n, "found": total}


@app.post("/admin/translation/{book_id}/delete")
def admin_translation_delete(request: Request, book_id: int):
    require_admin(request)
    translate.delete_translation(book_id)
    return RedirectResponse(f"/book/{book_id}", 303)


@app.get("/admin/translate/status")
def admin_translate_status(request: Request):
    require_admin(request)
    return translate.queue_info()


@app.post("/admin/cache/clear")
def admin_clear_cache(request: Request):
    require_admin(request)
    n = jobs.clear_cache()
    return {"ok": True, "removed": n}


@app.get("/admin/log", response_class=HTMLResponse)
def admin_log(request: Request, scan: int | None = None, level: str = ""):
    require_admin(request)
    with get_conn() as conn:
        if scan is None:
            r = conn.execute("SELECT max(id) FROM scans").fetchone()
            scan = r[0] or 0
        sql = "SELECT * FROM import_log WHERE scan_id=?"
        args: list = [scan]
        if level:
            sql += " AND level=?"
            args.append(level)
        rows = [dict(r) for r in conn.execute(sql + " ORDER BY level, dir_name LIMIT 5000", args)]
        scans = [dict(r) for r in conn.execute("SELECT id, started FROM scans ORDER BY id DESC LIMIT 30")]
        return render(request, "admin_log.html", conn, rows=rows, scan=scan, scans=scans, level=level)


@app.get("/admin/books", response_class=HTMLResponse)
def admin_books(request: Request, q: str = "", filt: str = "", page: int = 1):
    require_admin(request)
    per = 100
    with get_conn() as conn:
        where, args = [], []
        if q:
            where.append("(title LIKE ? OR author LIKE ? OR code LIKE ? OR dir_name LIKE ?)")
            args += [f"%{q}%"] * 4
        if filt in ("main", "rus", "inpx"):
            where.append(f"src='{filt}'")
        elif filt == "hidden":
            where.append("hidden=1")
        elif filt == "nocover":
            where.append("(cover_file IS NULL OR cover_file='?')")
        elif filt == "noauthor":
            where.append("author=''")
        elif filt == "locked":
            where.append("meta_locked=1")
        w = ("WHERE " + " AND ".join(where)) if where else ""
        total = conn.execute(f"SELECT count(*) FROM books {w}", args).fetchone()[0]
        rows = [dict(r) for r in conn.execute(
            f"SELECT * FROM books {w} ORDER BY title COLLATE NOCASE LIMIT ? OFFSET ?",
            args + [per, (page - 1) * per])]
        return render(request, "admin_books.html", conn, rows=rows, q=q, filt=filt, page=page,
                      pages=max(1, math.ceil(total / per)), total=total)


@app.get("/admin/book/{book_id}", response_class=HTMLResponse)
def admin_book(request: Request, book_id: int, saved: int = 0):
    require_admin(request)
    with get_conn() as conn:
        b = get_book(conn, book_id, allow_hidden=True)
        return render(request, "admin_book.html", conn, b=b, saved=saved, path=str(book_path(b)))


@app.post("/admin/book/{book_id}")
def admin_book_save(request: Request, book_id: int, title: str = Form(""), author: str = Form(""),
                    code: str = Form(""), hidden: int = Form(0), meta_locked: int = Form(0),
                    reset: int = Form(0)):
    require_admin(request)
    with get_conn() as conn:
        b = get_book(conn, book_id, allow_hidden=True)
        if reset and b["src"] == "inpx":
            # исходные данные вернутся при следующем импорте .inpx
            code, title, author = b["code"], b["title"], b["author"]
            meta_locked = 0
            conn.execute("UPDATE books SET sig=NULL WHERE id=?", (book_id,))
        elif reset and b["src"] == "rus":
            code, author = b["code"], b["author"]
            title = " ".join(Path(b["file_path"]).stem.replace("_", " ").split())
            meta = importer.read_file_text(book_path(b))[2] if b["fmt"] == "fb2" else {}
            author = ", ".join(meta.get("authors", [])) or author
            meta_locked = 0
        elif reset:
            code, title, author, _ = importer.parse_dir_name(b["dir_name"])
            meta_locked = 0
        else:
            meta_locked = 1 if (title, author, code) != (b["title"], b["author"], b["code"]) else meta_locked
        conn.execute("""UPDATE books SET title=?, author=?, code=?, hidden=?, meta_locked=?,
                        updated_at=datetime('now','localtime') WHERE id=?""",
                     (title.strip(), author.strip(), code.strip(), int(bool(hidden)), int(bool(meta_locked)),
                      book_id))
        if (title.strip(), author.strip(), code.strip()) != (b["title"], b["author"], b["code"]):
            reindex_book(conn, book_id)
        names_changed = (author.strip(), int(bool(hidden))) != (b["author"], b["hidden"])
    if names_changed:
        catalog.rebuild_async()
    return RedirectResponse(f"/admin/book/{book_id}?saved=1", 303)


@app.exception_handler(HTTPException)
async def http_exc(request: Request, exc: HTTPException):
    if exc.status_code in (301, 302, 303) and exc.headers and "Location" in exc.headers:
        return RedirectResponse(exc.headers["Location"], exc.status_code)
    if request.url.path.startswith("/api/"):
        return JSONResponse({"ok": False, "error": exc.detail}, exc.status_code)
    return render(request, "error.html", status=exc.status_code, code=exc.status_code, detail=exc.detail)


__all__ = ["app", "BASE_DIR"]
