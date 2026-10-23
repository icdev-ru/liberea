"""Либерея — управление из командной строки.

  python manage.py serve            запустить веб-сервер (адрес и порт из config.toml)
  python manage.py scan [--force]   просканировать основную библиотеку
  python manage.py scan --extra [--force] просканировать дополнительную библиотеку (txt/fb2 с подпапками)
  python manage.py scan --inpx [--force]  импорт/обновление коллекции из .inpx (каталог MyHomeLib)
  python manage.py get-model [файл.argosmodel]   скачать/установить модель перевода en→ru (Argos)
  python manage.py get-model --opus-big [файл.zip]   модель OPUS-MT tc-big (качественнее, медленнее)
  python manage.py translate-test книга.txt [--chars 20000] [--model data/models/en_ru_big]
  python manage.py translate-test книга.txt --engine llm [--chars 3000] [--model id] замер качественного перевода
  python manage.py translate --all-en   поставить в очередь перевод всех английских книг
  python manage.py hash-password    получить хеш пароля для config.toml
  python manage.py stats            краткая статистика
"""
from __future__ import annotations

import getpass
import hashlib
import json
import re
import secrets
import sys
import time


def cmd_serve():
    import uvicorn

    import socket

    from app.config import CONFIG
    if CONFIG.host in ("0.0.0.0", "::", ""):
        addrs = ["localhost"]
        try:
            addrs += [ip for ip in socket.gethostbyname_ex(socket.gethostname())[2] if not ip.startswith("127.")]
        except OSError:
            pass
    else:
        addrs = [CONFIG.host]
    print(f"{CONFIG.site_title} запущена. Откройте в браузере:")
    for a in addrs:
        print(f"   http://{a}:{CONFIG.port}")
    print("Админка: /admin   (Ctrl+C или закрыть окно — остановить)\n")
    if not (CONFIG.admin_password_hash or CONFIG.admin_password):
        print(f"ПЕРВЫЙ ЗАПУСК: откройте http://{addrs[0]}:{CONFIG.port}/setup — задайте пароль администратора\n"
              "  и папки с книгами.\n")
    if CONFIG.admin_password_hash and not CONFIG.admin_password_hash.startswith("pbkdf2$"):
        print("ВНИМАНИЕ: в config.toml в password_hash записан обычный пароль, а не хеш.\n"
              "  Он принят как пароль. Лучше перенести его в password = \"...\", а password_hash оставить пустым.\n")
    uvicorn.run("app.main:app", host=CONFIG.host, port=CONFIG.port, log_level="info", workers=1)


def cmd_scan(force: bool, kind: str = "main"):
    import threading

    from app import importer
    from app.db import init_db
    init_db()
    t0 = time.time()
    th = threading.Thread(target=importer.run_scan, args=(force, kind))
    th.start()
    while th.is_alive():
        s = importer.STATE
        if s.get("total"):
            print(f"\r{s['done']}/{s['total']}  новых {s['added']}  обновл. {s['updated']}  "
                  f"ошибок {s['errors']}   ", end="", flush=True)
        th.join(1)
    s = importer.STATE
    print()
    if s.get("message"):
        print(s["message"])
    what = {"rus": "книг (групп файлов)", "inpx": "записей в .inpx"}.get(kind, "папок")
    print(f"Готово за {time.time() - t0:.1f} с: {what} {s.get('total')}, новых {s.get('added')}, "
          f"обновлено {s.get('updated')}, без изменений {s.get('unchanged')}, удалено {s.get('removed')}, "
          f"ошибок {s.get('errors')}, предупреждений {s.get('warnings')}")


def cmd_hash():
    pw = getpass.getpass("Новый пароль: ")
    if pw != getpass.getpass("Ещё раз: "):
        sys.exit("Пароли не совпадают")
    salt = secrets.token_bytes(16)
    it = 200_000
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, it).hex()
    print("\nВставьте в config.toml, раздел [admin]:\n")
    print(f'password_hash = "pbkdf2${it}${salt.hex()}${h}"')
    print('password = ""')


OPUS_BIG_URL = ("https://object.pouta.csc.fi/Tatoeba-MT-models/eng-zle/"
                "opusTCv20210807+bt_transformer-big_2022-03-13.zip")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/129.0 Safari/537.36", "Accept": "*/*"}


def _download(url, dest):
    import urllib.error
    import urllib.request
    print(f"Скачиваю {url}")
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120)
    except urllib.error.URLError as e:
        sys.exit(f"\nНе удалось скачать: {e}\nСкачайте файл вручную в браузере:\n  {url}\n"
                 f"и укажите путь к нему в команде.")
    with r, open(dest, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        got = 0
        while chunk := r.read(1 << 20):
            f.write(chunk)
            got += len(chunk)
            print(f"\r  {got >> 20} МБ" + (f" из {total >> 20} МБ" if total else ""), end="", flush=True)
    print()


def cmd_get_opus_big(src: str | None):
    """Модель Helsinki-NLP OPUS-MT tc-big (англ. → рус./укр./бел.), конвертация в CTranslate2 int8."""
    import shutil
    import zipfile
    from pathlib import Path

    import ctranslate2.converters as conv

    from app.config import BASE_DIR
    models = BASE_DIR / "data" / "models"
    models.mkdir(parents=True, exist_ok=True)
    out = models / "en_ru_big"
    work = models / "_opus_src"
    zpath = Path(src) if src else models / "opus_big.zip"
    if not src:
        _download(OPUS_BIG_URL, zpath)
    print("Распаковываю…")
    shutil.rmtree(work, ignore_errors=True)
    with zipfile.ZipFile(zpath) as z:
        z.extractall(work)
    dec = next(work.rglob("decoder.yml"), None)
    if not dec:
        sys.exit("В архиве нет decoder.yml — это не модель OPUS-MT")
    mdir = dec.parent
    print("Конвертирую в CTranslate2 (int8), это займёт 1–3 минуты…")
    shutil.rmtree(out, ignore_errors=True)
    conv.OpusMTConverter(str(mdir)).convert(str(out), quantization="int8", force=True)
    for name in ("source.spm", "target.spm", "README.md"):
        if (mdir / name).exists():
            shutil.copy(mdir / name, out / name)
    shutil.rmtree(work, ignore_errors=True)
    if not src:
        zpath.unlink(missing_ok=True)
    size = sum(p.stat().st_size for p in out.rglob("*")) >> 20
    print(f"\nГотово: {out} ({size} МБ)")
    print('Чтобы включить, в config.toml в разделе [translate] укажите:\n  model_dir = "data/models/en_ru_big"')
    print("и перезапустите сервер. Сравнить с текущей моделью:\n"
          "  manage.py translate-test книга.txt --model data/models/en_ru_big")


ARGOS_INDEX = "https://raw.githubusercontent.com/argosopentech/argospm-index/main/index.json"


def cmd_get_model(src: str | None):
    import shutil
    import urllib.error
    import urllib.request
    import zipfile
    from pathlib import Path

    from app.config import CONFIG
    dest: Path = CONFIG.tr_model_dir
    tmp = dest.parent / "en_ru.argosmodel"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src:
        tmp = Path(src)
    else:
        print("Ищу модель en→ru в каталоге Argos Translate…")
        with urllib.request.urlopen(ARGOS_INDEX, timeout=60) as r:
            index = json.load(r)
        pkg = max((p for p in index if p.get("from_code") == "en" and p.get("to_code") == "ru"),
                  key=lambda p: [int(x) for x in str(p.get("package_version", "0")).split(".") if x.isdigit()])
        url = pkg["links"][0]
        print(f"Скачиваю {url}")
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/129.0 Safari/537.36",
            "Accept": "*/*"})
        try:
            r = urllib.request.urlopen(req, timeout=120)
        except urllib.error.URLError as e:
            sys.exit(f"\nНе удалось скачать модель: {e}\n\n"
                     f"Скачайте файл вручную в браузере:\n  {url}\n"
                     f"и выполните:\n  get-model.bat \"путь\\к\\{url.rsplit('/', 1)[-1]}\"")
        with r, open(tmp, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            got = 0
            while chunk := r.read(1 << 20):
                f.write(chunk)
                got += len(chunk)
                print(f"\r  {got >> 20} МБ" + (f" из {total >> 20} МБ" if total else ""), end="", flush=True)
        print()
    if dest.exists():
        shutil.rmtree(dest)
    with zipfile.ZipFile(tmp) as z:
        z.extractall(dest)
    ok = next(dest.rglob("model.bin"), None) and next(dest.rglob("*.model"), None)
    if not ok:
        sys.exit(f"В архиве не найдена модель CTranslate2 (model.bin) и sentencepiece (*.model): {tmp}")
    if not src:
        tmp.unlink(missing_ok=True)
    print(f"Модель установлена: {dest}")


def cmd_translate_test(path: str, chars: int, model: str | None = None):
    from pathlib import Path

    from app import translate
    from app.config import CONFIG, _resolve
    if model:
        CONFIG.tr_model_dir = _resolve(model)
        CONFIG.tr_engine = "local"
    from app.importer import read_file_text
    text = read_file_text(Path(path))[0]
    sample = text[:chars]
    cut = sample.rfind("\n")
    if cut > chars // 2:
        sample = sample[:cut]
    t0 = time.time()
    eng = translate.get_engine()
    t_load = time.time() - t0
    t0 = time.time()

    def progress(d, t):
        print(f"\r  {100 * d // t}%", end="", flush=True)
    ru = translate.translate_text(sample, progress)
    secs = time.time() - t0
    print()
    speed = len(sample) / secs if secs else 0
    full_min = len(text) / speed / 60 if speed else 0
    from app.config import CONFIG
    out_dir = CONFIG.database.parent / "translate-test"  # не в папку библиотеки — иначе сканер увидит лишний txt
    out_dir.mkdir(parents=True, exist_ok=True)
    tag = "" if not model else "." + Path(model).name
    out = out_dir / (Path(path).stem + tag + ".ru-test.txt")
    with open(out, "w", encoding="utf-8-sig") as f:
        for en_p, ru_p in zip(sample.split("\n"), ru.split("\n")):
            if en_p.strip():
                f.write(en_p.strip() + "\n" + ru_p.strip() + "\n\n")
    print(f"Движок: {eng.name}. Загрузка модели: {t_load:.1f} с")
    print(f"Переведено {len(sample):,} знаков за {secs:.1f} с — {speed:,.0f} знаков/с".replace(",", " "))
    print(f"Вся книга ({len(text):,} знаков) займёт примерно {full_min:.1f} мин".replace(",", " "))
    print(f"Оригинал и перевод по абзацам: {out}")


def cmd_translate_test_llm(path: str, chars: int, model: str | None = None):
    """Замер качественного перевода (LLM) на начале книги: оригинал / черновик / LLM."""
    from pathlib import Path

    from app import llm, translate
    from app.config import CONFIG
    from app.importer import read_file_text
    text = read_file_text(Path(path))[0]
    sample = text[:chars]
    cut = sample.rfind("\n")
    if cut > chars // 2:
        sample = sample[:cut]
    draft = None
    if CONFIG.tr_engine != "off":
        try:
            print("Черновой перевод (для подсказки модели)…")
            draft = translate.translate_text(sample)
        except Exception as e:  # noqa: BLE001
            print(f"  черновик недоступен: {e}")
    try:
        client = llm.LLMClient()
    except llm.LLMError as e:
        sys.exit(str(e))
    if model:
        client.model = model
    print(f"Модель: {client.model} ({CONFIG.llm_url})")
    t0 = time.time()

    def progress(d, t):
        el = time.time() - t0
        print(f"\r  {100 * d // t}%  {el:.0f} с  ({client.tokens} токенов, {client.tokens / el:.1f} ток/с)",
              end="", flush=True)
    ru = llm.llm_translate(sample, draft, progress, client)
    secs = time.time() - t0
    print()
    speed = len(sample) / secs if secs else 0
    out_dir = CONFIG.database.parent / "translate-test"
    out_dir.mkdir(parents=True, exist_ok=True)
    tag = re.sub(r"[^\w.-]+", "_", client.model.split("/")[-1])[:60]
    out = out_dir / (Path(path).stem + f".llm.{tag}.ru-test.txt")
    en_l, ru_l = sample.split("\n"), ru.split("\n")
    dr_l = draft.split("\n") if draft else [""] * len(en_l)
    with open(out, "w", encoding="utf-8-sig") as f:
        for i, en_p in enumerate(en_l):
            if en_p.strip():
                f.write("EN:  " + en_p.strip() + "\n")
                if draft:
                    f.write("ЧЕР: " + (dr_l[i] if i < len(dr_l) else "").strip() + "\n")
                f.write("LLM: " + (ru_l[i] if i < len(ru_l) else "").strip() + "\n\n")
    print(f"Переведено {len(sample):,} знаков за {secs:.0f} с — {speed:,.0f} знаков/с, "
          f"{client.tokens / secs:.1f} токенов/с".replace(",", " "))
    print(f"Вся книга ({len(text):,} знаков) займёт примерно {len(text) / speed / 60:.0f} мин".replace(",", " "))
    print(f"Оригинал / черновик / LLM по абзацам: {out}")


def cmd_translate_all_en():
    from app import translate
    from app.db import get_conn, init_db
    init_db()
    with get_conn() as c:
        ids = [r[0] for r in c.execute("""SELECT id FROM books WHERE lang='en' AND hidden=0 AND src!='inpx' AND id NOT IN
                    (SELECT book_id FROM translations WHERE status IN ('done','queued','running')) ORDER BY id""")]
    n = translate.enqueue(ids, start=False)
    print(f"В очередь добавлено книг: {n}. Работающий сервер начнёт переводить в течение минуты.")


def cmd_stats():
    from app.db import get_conn, init_db
    init_db()
    with get_conn() as c:
        r = c.execute("SELECT count(*), sum(txt_size), sum(cover_file IS NOT NULL) FROM books").fetchone()
        print(f"Книг: {r[0]}, текстов: {(r[1] or 0) / 1048576:.1f} МБ, с обложкой: {r[2] or 0}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] == "serve":
        cmd_serve()
    elif args[0] == "scan":
        cmd_scan("--force" in args, "rus" if ("--extra" in args or "--rus" in args) else "inpx" if "--inpx" in args else "main")
    elif args[0] == "get-model" and "--opus-big" in args:
        rest = [a for a in args[1:] if a != "--opus-big"]
        cmd_get_opus_big(rest[0] if rest else None)
    elif args[0] == "get-model":
        cmd_get_model(args[1] if len(args) > 1 else None)
    elif args[0] == "translate-test" and len(args) > 1:
        n = int(args[args.index("--chars") + 1]) if "--chars" in args else 20000
        mdl = args[args.index("--model") + 1] if "--model" in args else None
        if "--engine" in args and args[args.index("--engine") + 1] == "llm":
            cmd_translate_test_llm(args[1], int(args[args.index("--chars") + 1]) if "--chars" in args else 3000, mdl)
        else:
            cmd_translate_test(args[1], n, mdl)
    elif args[0] == "translate" and "--all-en" in args:
        cmd_translate_all_en()
    elif args[0] == "hash-password":
        cmd_hash()
    elif args[0] == "stats":
        cmd_stats()
    else:
        print(__doc__)
