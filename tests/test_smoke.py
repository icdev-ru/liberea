"""Сквозная проверка: сканирование всех видов библиотек, поиск, страницы, выгрузки, OPDS, первый запуск."""
import xml.dom.minidom


def test_start_page(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Либерея" in r.text


def test_search_all_kinds(client):
    for q, expect in [("деревня", "Летний сад"), ("winter", "Winter Road"), ("корабль", "Звёздный путь"),
                      ("полёт", "Первый полёт")]:
        r = client.get("/", params={"q": q})
        assert expect in r.text, q
    # удалённая книга коллекции не импортируется
    assert "Удалённая" not in client.get("/", params={"q": "Удалённая"}).text.split("results-head")[-1]


def test_authors_series_genres(client):
    r = client.get("/", params={"q": "Орлов", "scope": "author"})
    assert "Орлов Пётр Ильич" in r.text
    r = client.get("/author", params={"name": "Орлов Пётр Ильич"})
    assert r.text.index("Первый полёт") < r.text.index("Второй полёт")
    r = client.get("/", params={"q": "Неб", "scope": "series"})
    assert "Небо" in r.text
    assert "Космическая фантастика" in client.get("/genres").text
    assert "Первый полёт" in client.get("/genre/sf_space").text


def test_book_downloads(client):
    import re
    r = client.get("/", params={"q": "Летний"})
    bid = re.search(r'href="/book/(\d+)', r.text).group(1)
    assert client.get(f"/book/{bid}").status_code == 200
    for fmt, magic in [("txt", None), ("pdf", b"%PDF"), ("fb2", b"<?xml")]:
        d = client.get(f"/book/{bid}/download.{fmt}")
        assert d.status_code == 200, fmt
        if magic:
            assert d.content.startswith(magic)
    r = client.get("/", params={"q": "Первый полёт"})
    bid = re.search(r'href="/book/(\d+)', r.text).group(1)
    d = client.get(f"/book/{bid}/download.fb2")
    assert d.status_code == 200 and b"FictionBook" in d.content


def test_opds(client):
    for path in ["/opds", "/opds/authors", "/opds/series-index", "/opds/genres", "/opds/new",
                 "/opds/search/books?q=полёт", "/opds/series?name=Небо",
                 "/opds/author?name=Орлов Пётр Ильич", "/opds/opensearch.xml"]:
        r = client.get(path)
        assert r.status_code == 200, path
        xml.dom.minidom.parseString(r.content)


def test_first_run_setup(client):
    assert client.get("/admin", follow_redirects=False).headers["location"].endswith("/setup")
    r = client.post("/setup", data={"password": "123", "password2": "123"}, follow_redirects=False)
    assert "err=short" in r.headers["location"]
    r = client.post("/setup", data={"password": "secret12", "password2": "secret12"}, follow_redirects=False)
    assert r.headers["location"].startswith("/admin")
    assert client.get("/admin").status_code == 200
    assert client.get("/admin/settings").status_code == 200
    # мастер больше не открывается
    assert client.get("/setup", follow_redirects=False).status_code == 303


def test_opds_in_browser(client):
    """Браузер получает HTML-страницу с рабочими ссылками, читалка — XML."""
    html = client.get("/opds/authors", headers={"Accept": "text/html,application/xhtml+xml,*/*"})
    assert html.headers["content-type"].startswith("text/html")
    assert 'href="/opds/' in html.text
    xml_ = client.get("/opds/authors")
    assert "opds-catalog" in xml_.headers["content-type"]
