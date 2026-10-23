<p align="center"><img src="app/static/favicon.svg" width="72" alt=""></p>

<h1 align="center">Liberea</h1>

<p align="center"><b>A home e-book library server</b> — search thousands of books, bundle collections into a single PDF/FB2,
browse from e-readers via OPDS and translate English books into Russian. Runs on your local network, no cloud.</p>

<p align="center">
  <a href="../../releases/latest">Download</a> ·
  <a href="docs/guide.md">Guide (Russian)</a> ·
  <a href="README.md">Русский</a> ·
  <a href="CHANGELOG.md">Changelog</a>
</p>

![Search](docs/screenshots/search-cards.png)

> The user interface and the detailed guide are in **Russian**; the program handles both Russian and English books.

## Features

- **Three kinds of sources** in one catalog:
  - folders named `code Title (Author)` with a .txt text and a cover image;
  - a folder tree of .txt / .fb2 files (duplicates are detected);
  - **INPX** collections (MyHomeLib catalogs) — hundreds of thousands of books, metadata import in minutes,
    books are read straight from the zip archives.
- **Search** by title, author, series, code and **full text** — Russian and English with stemming, phrases,
  exclusions, prefix search.
- **Authors, series, genres** with book counts; an author's books are grouped by series.
- **Three result views** (cards, list, covers only), **dark and light themes**, mobile-friendly.
- **Downloads** in TXT / PDF / FB2 (original file for FB2 sources).
- **Collections:** gather books from different searches and build **one PDF or FB2** with a table of contents and covers.
- **OPDS catalog** for FBReader, Moon+ Reader, KOReader, CoolReader, ReadEra and others.
- **Translation** of English books into Russian: local and free (Argos / OPUS-MT via CTranslate2), Yandex Cloud,
  or literary quality with a local LLM (LM Studio, Ollama). Translated books become searchable in Russian.
- **Admin panel:** scanning with progress, import log, editing and hiding books, **settings in the browser**,
  first-run wizard, login brute-force protection.

## Quick start

**Windows** — no Python install needed:
1. Download `Liberea-…-windows-x64.zip` from [Releases](../../releases/latest) and unzip it.
2. Run `run.bat`, open http://localhost:5555/setup, set the admin password and your book folders.
3. Admin → **«Сканировать»** (Scan). Done.

**Linux:**
```bash
tar xzf Liberea-*-linux.tar.gz && cd Liberea-*-linux
./packaging/linux/install.sh --service      # Python venv + systemd service
```

**Docker** (amd64 / arm64):
```bash
docker run -d --name liberea --restart unless-stopped -p 5555:5555 \
  -v "$PWD/data:/data" -v /srv/books:/books:ro ghcr.io/icdev-ru/liberea:latest
```
In the setup wizard use paths like `/books/...`. See also [`docker-compose.yml`](docker-compose.yml).

**Try it without your own books:** `python scripts/make_demo.py demo` creates a demo library of all three kinds.

Other devices: `http://<computer IP>:5555`; e-readers: OPDS catalog `http://<computer IP>:5555/opds`.

> **Home network only.** The public part of Liberea has no login. Do not expose it to the internet directly —
> use a VPN (WireGuard, Tailscale, …) for remote access.

## From source

Python 3.11+:
```bash
git clone https://github.com/icdev-ru/liberea.git && cd liberea
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python manage.py serve
```
Tests: `pip install -r requirements-dev.txt && python -m pytest -q`.

Stack: Python + FastAPI, SQLite with FTS5 full-text index (Snowball stemmer), Jinja2 templates and a little
framework-free JavaScript. Book texts are not copied into the database — the index stores word stems only.

## License

[GPL-3.0](LICENSE). DejaVu fonts — [their own free license](fonts/LICENSE-DejaVu.txt).
Translation models are not included; they are downloaded separately under their authors' terms
(Argos Translate, OPUS-MT / Helsinki-NLP — CC-BY 4.0).

Liberea is software for **your own** books. The repository and releases contain no books or collection catalogs.
