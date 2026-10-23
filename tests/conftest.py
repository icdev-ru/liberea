"""Тестовая среда: временные папки с маленькими библиотеками всех трёх видов."""
import io
import os
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FB2 = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0" xmlns:l="http://www.w3.org/1999/xlink">
<description><title-info><genre>sf</genre><author><first-name>{first}</first-name><last-name>{last}</last-name></author>
<book-title>{title}</book-title><lang>ru</lang></title-info></description>
<body><section><p>{text}</p><p>Второй абзац про звёздный корабль.</p></section></body></FictionBook>"""


def _make_env(tmp: Path) -> None:
    main = tmp / "main"
    for code, title, author, text in [
        ("ab-001", "Летний сад", "Иван Петров", "Тёплый вечер в деревне у реки."),
        ("ab-002", "Winter Road", "John Smith", "A cold morning on the long winter road."),
    ]:
        d = main / f"{code} {title} ({author})"
        d.mkdir(parents=True)
        (d / f"{title}.txt").write_text(text * 20, encoding="cp1251" if code == "ab-001" else "utf-8")
    extra = tmp / "extra" / "sub"
    extra.mkdir(parents=True)
    (extra / "Звёздный путь.fb2").write_text(
        FB2.format(first="Анна", last="Лунная", title="Звёздный путь", text="Корабль летел к звёздам."), encoding="utf-8")
    (extra / "Звёздный путь.txt").write_text("коротко", encoding="utf-8")
    # коллекция INPX: 3 книги, одна удалённая
    col = tmp / "collection"
    col.mkdir()
    lines = []
    with zipfile.ZipFile(col / "fb2-000001-000003.zip", "w") as z:
        for i, (title, author, series, serno, genre, deleted) in enumerate([
            ("Первый полёт", "Орлов,Пётр,Ильич:", "Небо", "1", "sf_space:", "0"),
            ("Второй полёт", "Орлов,Пётр,Ильич:", "Небо", "2", "sf_space:", "0"),
            ("Удалённая", "Кто-то,Некто,:", "", "", "prose:", "1"),
        ], 1):
            z.writestr(f"{i}.fb2", FB2.format(first="Пётр", last="Орлов", title=title, text="Полёт над облаками."))
            lines.append("\x04".join([author, genre, title, series, serno, str(i), "1000", str(i), deleted,
                                      "fb2", "2026-01-0" + str(i), "ru", "", "", ""]))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("collection.info", "Test collection\ntest\n65536\nТестовая коллекция")
        z.writestr("version.info", "20260101")
        z.writestr("fb2-000001-000003.inp", "\r\n".join(lines) + "\r\n")
    (col / "test.inpx").write_bytes(buf.getvalue())
    (tmp / "config.toml").write_text(f"""
[server]
host = "127.0.0.1"
port = 5599
[library]
root = '{main}'
[extra]
root = '{tmp / "extra"}'
[inpx]
file = '{col / "test.inpx"}'
[translate]
engine = "off"
""", encoding="utf-8")


@pytest.fixture(scope="session")
def env(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("liberea")
    _make_env(tmp)
    os.environ["LIBEREA_CONFIG"] = str(tmp / "config.toml")
    os.environ["LIBEREA_DATA"] = str(tmp / "data")
    return tmp


@pytest.fixture(scope="session")
def client(env):
    from fastapi.testclient import TestClient

    from app import importer
    from app.db import init_db
    from app.main import app
    init_db()
    for kind in ("main", "rus", "inpx"):
        st = importer.run_scan(False, kind)
        assert not st.get("errors"), st
    with TestClient(app) as c:
        yield c
