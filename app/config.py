"""Загрузка конфигурации из config.toml."""
from __future__ import annotations

import os
import secrets
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


DATA_ENV = os.environ.get("LIBEREA_DATA")  # Docker и т. п.: куда класть data/ (база, кеш, модели)


def _resolve(p: str) -> Path:
    p = str(p).replace("\\", "/")
    path = Path(p)
    if path.is_absolute():
        return path
    if DATA_ENV and (p == "data" or p.startswith("data/")):
        return Path(DATA_ENV) / p[5:]
    return BASE_DIR / path


@dataclass
class Config:
    host: str = "127.0.0.1"
    port: int = 5555
    site_title: str = "Либерея"
    page_size: int = 24
    covers_page_size: int = 120
    list_page_size: int = 100
    library_root: Path = Path(".")
    main_enabled: bool = True
    main_label: str = "Основная"
    rus_enabled: bool = True
    rus_label: str = "Дополнительная"
    inpx_enabled: bool = True
    text_ext: list[str] = field(default_factory=lambda: [".txt"])
    rus_root: Path | None = None
    rus_prefix: str = "rus"
    rus_dedup: str = "tree"
    inpx_file: Path | None = None       # файл .inpx (каталог коллекции MyHomeLib)
    inpx_root: Path | None = None       # папка с zip-архивами книг (по умолчанию — папка .inpx)
    inpx_prefix: str = "ix"
    inpx_label: str = "Коллекция INPX"
    inpx_skip_deleted: bool = True
    inpx_langs: list[str] = field(default_factory=list)
    inpx_genres: list[str] = field(default_factory=list)
    inpx_exclude_genres: list[str] = field(default_factory=list)
    cover_ext: list[str] = field(default_factory=lambda: [".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"])
    database: Path = BASE_DIR / "data/library.db"
    cache: Path = BASE_DIR / "data/cache"
    font_regular: Path = BASE_DIR / "fonts/DejaVuSerif.ttf"
    font_bold: Path = BASE_DIR / "fonts/DejaVuSerif-Bold.ttf"
    font_italic: Path = BASE_DIR / "fonts/DejaVuSerif-Italic.ttf"
    font_size: float = 11
    page_format: str = "A5"
    fb2_genre: str = "prose"
    tr_engine: str = "local"            # local | yandex | off
    tr_model_dir: Path = BASE_DIR / "data/models/en_ru"
    tr_threads: int = 0
    tr_beam: int = 2
    tr_allow: str = "all"               # all | admin — кто может запускать перевод
    tr_yandex_key: str = ""
    tr_yandex_folder: str = ""
    llm_enabled: bool = False
    llm_url: str = "http://127.0.0.1:1234/v1"
    llm_model: str = ""
    llm_chunk_chars: int = 1500
    llm_temperature: float = 0.3
    llm_use_draft: bool = True
    llm_timeout: int = 900
    llm_prompt: str = ""
    admin_password: str = ""
    admin_password_hash: str = ""
    secret: str = ""


def _libs(self) -> dict[str, str]:
    """Включённые части библиотеки: код → название (в порядке показа)."""
    out = {}
    if self.main_enabled:
        out["main"] = self.main_label
    if self.rus_enabled:
        out["rus"] = self.rus_label
    if self.inpx_enabled:
        out["inpx"] = self.inpx_label
    return out


def _src_sql(self, alias: str = "b") -> str:
    """SQL-условие «книга из включённой части»."""
    libs = self.libs
    if not libs:
        return "0"
    return f"{alias}.src IN ({', '.join(repr(k) for k in libs)})"


Config.libs = property(_libs)
Config.src_sql = _src_sql


def config_path() -> Path:
    p = os.environ.get("LIBEREA_CONFIG") or os.environ.get("BOOKLIB_CONFIG")
    if p:
        return Path(p)
    if DATA_ENV and (Path(DATA_ENV) / "config.toml").exists():
        return Path(DATA_ENV) / "config.toml"
    return BASE_DIR / "config.toml"


def _read_toml(path: Path) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def settings_path(raw: dict | None = None) -> Path:
    """Файл настроек, изменённых через админку (накладывается поверх config.toml)."""
    raw = raw if raw is not None else _base_raw()
    db = _resolve(raw.get("storage", {}).get("database", "data/library.db"))
    return db.parent / "settings.toml"


def _base_raw() -> dict:
    path = config_path()
    if not path.exists():
        path = BASE_DIR / "config.example.toml"
    return _read_toml(path)


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = _merge(out.get(k, {}), v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def raw_settings() -> dict:
    """Только то, что сохранено через админку."""
    p = settings_path()
    return _read_toml(p) if p.exists() else {}


def merged_raw() -> dict:
    """config.toml + settings.toml, как их видит программа."""
    raw = _base_raw()
    sp = settings_path(raw)
    return _merge(raw, _read_toml(sp)) if sp.exists() else raw


def load_config(path: str | os.PathLike | None = None) -> Config:
    raw = _read_toml(Path(path)) if path else _base_raw()
    sp = settings_path(raw)
    if sp.exists():
        raw = _merge(raw, _read_toml(sp))

    # дополнительная библиотека: раздел [extra] (в старых конфигах — [rus])
    legacy_rus = "extra" not in raw and "rus" in raw
    s, site, lib, st, pdf, adm, fb2, tr, llm, ix = (raw.get(k, {}) for k in (
        "server", "site", "library", "storage", "pdf", "admin", "fb2", "translate", "llm", "inpx"))
    rus = raw.get("extra") or raw.get("rus") or {}
    cfg = Config(
        host=s.get("host", "127.0.0.1"),
        port=int(s.get("port", 5555)),
        site_title=site.get("title", "Либерея"),
        page_size=int(site.get("page_size", 24)),
        covers_page_size=int(site.get("covers_page_size", 120)),
        list_page_size=int(site.get("list_page_size", 100)),
        rus_root=Path(rus["root"]) if rus.get("root") else None,
        rus_prefix=str(rus.get("prefix", "rus" if legacy_rus else "ex")),
        rus_dedup=str(rus.get("dedup", "tree")),
        inpx_file=Path(ix["file"]) if ix.get("file") else None,
        inpx_root=Path(ix["root"]) if ix.get("root") else None,
        inpx_prefix=str(ix.get("prefix", "ix")),
        inpx_label=str(ix.get("label", "Коллекция INPX")),
        inpx_skip_deleted=bool(ix.get("skip_deleted", True)),
        inpx_langs=[str(x).lower() for x in ix.get("langs", [])],
        inpx_genres=[str(x) for x in ix.get("genres", [])],
        inpx_exclude_genres=[str(x) for x in ix.get("exclude_genres", [])],
        main_enabled=bool(lib.get("enabled", True)) and bool(lib.get("root")),
        main_label=str(lib.get("label", "Основная")),
        rus_enabled=bool(rus.get("enabled", True)) and bool(rus.get("root")),
        rus_label=str(rus.get("label", "Rus" if legacy_rus else "Дополнительная")),
        inpx_enabled=bool(ix.get("enabled", True)) and bool(ix.get("file")),
        library_root=Path(lib["root"]) if lib.get("root") else Path("."),
        text_ext=[e.lower() for e in lib.get("text_ext", [".txt"])],
        cover_ext=[e.lower() for e in lib.get("cover_ext", Config().cover_ext)],
        database=_resolve(st.get("database", "data/library.db")),
        cache=_resolve(st.get("cache", "data/cache")),
        font_regular=_resolve(pdf.get("font_regular", "fonts/DejaVuSerif.ttf")),
        font_bold=_resolve(pdf.get("font_bold", "fonts/DejaVuSerif-Bold.ttf")),
        font_italic=_resolve(pdf.get("font_italic", "fonts/DejaVuSerif-Italic.ttf")),
        font_size=float(pdf.get("font_size", 11)),
        page_format=pdf.get("page_format", "A5"),
        fb2_genre=fb2.get("genre", "prose"),
        tr_engine=str(tr.get("engine", "local")),
        tr_model_dir=_resolve(tr.get("model_dir", "data/models/en_ru")),
        tr_threads=int(tr.get("threads", 0)),
        tr_beam=int(tr.get("beam", 2)),
        tr_allow=str(tr.get("allow", "all")),
        tr_yandex_key=str(tr.get("yandex_api_key", "")),
        tr_yandex_folder=str(tr.get("yandex_folder_id", "")),
        llm_enabled=bool(llm.get("enabled", False)),
        llm_url=str(llm.get("url", "http://127.0.0.1:1234/v1")).rstrip("/"),
        llm_model=str(llm.get("model", "")),
        llm_chunk_chars=int(llm.get("chunk_chars", 1500)),
        llm_temperature=float(llm.get("temperature", 0.3)),
        llm_use_draft=bool(llm.get("use_draft", True)),
        llm_timeout=int(llm.get("timeout", 900)),
        llm_prompt=str(llm.get("system_prompt", "")),
        admin_password=str(adm.get("password", "")),
        admin_password_hash=str(adm.get("password_hash", "")),
        secret=str(adm.get("secret", "")),
    )
    if cfg.inpx_file and not cfg.inpx_root:
        cfg.inpx_root = cfg.inpx_file.parent
    cfg.database.parent.mkdir(parents=True, exist_ok=True)
    cfg.cache.mkdir(parents=True, exist_ok=True)
    if not cfg.secret:
        key_file = cfg.database.parent / "secret.key"
        if not key_file.exists():
            key_file.write_text(secrets.token_hex(32), encoding="ascii")
        cfg.secret = key_file.read_text(encoding="ascii").strip()
    return cfg


# ====================================================================== настройки из админки
def _toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    s = str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{s}"'


def save_settings(changes: dict[str, dict]) -> None:
    """Сохранить изменения (раздел → {ключ: значение}) в settings.toml и применить их сразу.
    Значение None удаляет ключ (возвращается значение из config.toml)."""
    cur = raw_settings()
    for sec, kv in changes.items():
        d = cur.setdefault(sec, {})
        for k, v in kv.items():
            if v is None:
                d.pop(k, None)
            else:
                d[k] = v
        if not d:
            cur.pop(sec)
    lines = ["# Настройки, изменённые через админку Либереи. Они накладываются поверх config.toml.",
             "# Можно править вручную или удалить файл, чтобы вернуться к config.toml.", ""]
    for sec, kv in cur.items():
        lines.append(f"[{sec}]")
        lines += [f"{k} = {_toml_value(v)}" for k, v in kv.items()]
        lines.append("")
    p = settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text("\n".join(lines), encoding="utf-8")
    tmp.replace(p)
    reload_config()


_reload_hooks: list = []


def on_reload(fn):
    _reload_hooks.append(fn)
    return fn


def reload_config() -> None:
    """Перечитать конфигурацию и обновить объект CONFIG на месте (его импортируют все модули)."""
    new = load_config()
    for k, v in vars(new).items():
        setattr(CONFIG, k, v)
    for fn in _reload_hooks:
        fn()


CONFIG = load_config()
