"""Демо-библиотека для знакомства с Либереей (и для скриншотов).

    python scripts/make_demo.py <папка>

Создаёт в <папка> три части: main/ (папки «код Название (Автор)» с txt и обложкой),
extra/ (txt и fb2 в подпапках) и collection/ (каталог .inpx с zip-архивом).
Названия и авторы — классика из общественного достояния, тексты — заглушки.
Затем укажите эти папки в мастере первого запуска (или в «Настройках») и запустите сканирование.
"""
from __future__ import annotations

import io
import random
import sys
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
FONT = ROOT / "fonts" / "DejaVuSerif.ttf"
FONT_B = ROOT / "fonts" / "DejaVuSerif-Bold.ttf"

MAIN = [
    ("cl-001", "Капитанская дочка", "Александр Пушкин", "ru"),
    ("cl-002", "Мёртвые души", "Николай Гоголь", "ru"),
    ("cl-003", "Отцы и дети", "Иван Тургенев", "ru"),
    ("cl-004", "Дама с собачкой", "Антон Чехов", "ru"),
    ("cl-005", "Война и мир", "Лев Толстой", "ru"),
    ("cl-006", "Белые ночи", "Фёдор Достоевский", "ru"),
    ("en-001", "Pride and Prejudice", "Jane Austen", "en"),
    ("en-002", "Great Expectations", "Charles Dickens", "en"),
    ("en-003", "The Time Machine", "H. G. Wells", "en"),
    ("en-004", "The Adventures of Tom Sawyer", "Mark Twain", "en"),
    ("en-005", "Frankenstein", "Mary Shelley", "en"),
    ("en-006", "Treasure Island", "Robert Louis Stevenson", "en"),
]
EXTRA = [
    ("Повести Белкина", "Пушкин", "Александр", "fb2"),
    ("Вечера на хуторе близ Диканьки", "Гоголь", "Николай", "fb2"),
    ("Записки охотника", "Тургенев", "Иван", "txt"),
    ("Каштанка", "Чехов", "Антон", "txt"),
]
# (серия, номер, название, автор «Фамилия,Имя,Отчество:», жанр)
INPX = [
    ("Шерлок Холмс", "1", "Этюд в багровых тонах", "Дойл,Артур,Конан:", "det_classic:"),
    ("Шерлок Холмс", "2", "Знак четырёх", "Дойл,Артур,Конан:", "det_classic:"),
    ("Шерлок Холмс", "3", "Собака Баскервилей", "Дойл,Артур,Конан:", "det_classic:"),
    ("Капитан Немо", "1", "Двадцать тысяч льё под водой", "Верн,Жюль,:", "sf:adv_maritime:"),
    ("Капитан Немо", "2", "Таинственный остров", "Верн,Жюль,:", "sf:adventure:"),
    ("", "", "Вокруг света за восемьдесят дней", "Верн,Жюль,:", "adventure:"),
    ("", "", "Война миров", "Уэллс,Герберт,Джордж:", "sf:"),
    ("", "", "Человек-невидимка", "Уэллс,Герберт,Джордж:", "sf:"),
    ("", "", "Остров доктора Моро", "Уэллс,Герберт,Джордж:", "sf_horror:"),
    ("", "", "Три мушкетёра", "Дюма,Александр,:", "adv_history:"),
    ("", "", "Граф Монте-Кристо", "Дюма,Александр,:", "adv_history:"),
    ("", "", "Айвенго", "Скотт,Вальтер,:", "adv_history:prose_classic:"),
    ("", "", "Гордость и предубеждение", "Остин,Джейн,:", "love_history:prose_classic:"),
    ("", "", "Джейн Эйр", "Бронте,Шарлотта,:", "love_history:prose_classic:"),
    ("", "", "Стихотворения", "Лермонтов,Михаил,Юрьевич:", "poetry:"),
    ("", "", "Басни", "Крылов,Иван,Андреевич:", "poetry:child_verse:"),
]

LOREM_RU = ("Это демонстрационный текст. Настоящего произведения здесь нет — только несколько абзацев, "
            "чтобы показать поиск по тексту, сборку PDF и FB2 и перевод. В деревне был тёплый летний вечер, "
            "над рекой поднимался туман, и где-то далеко звучала песня.")
LOREM_EN = ("This is a demo text. The actual work is not included — just a few paragraphs to show full-text "
            "search, PDF and FB2 export and translation. It was a warm summer evening in the village, mist rose "
            "over the river, and a song was heard somewhere far away.")


def cover(title: str, author: str, seed: int) -> bytes:
    rnd = random.Random(seed)
    w, h = 400, 600
    c1 = tuple(rnd.randint(30, 160) for _ in range(3))
    c2 = tuple(min(255, x + rnd.randint(40, 90)) for x in c1)
    im = Image.new("RGB", (w, h), c1)
    d = ImageDraw.Draw(im)
    for y in range(h):
        t = y / h
        d.line([(0, y), (w, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(c1, c2)))
    d.rectangle([24, 24, w - 24, h - 24], outline=(255, 255, 255), width=2)
    fb, fr = ImageFont.truetype(str(FONT_B), 34), ImageFont.truetype(str(FONT), 22)
    words, lines, cur = title.split(), [], ""
    for wd in words:
        if d.textlength((cur + " " + wd).strip(), font=fb) > w - 80:
            lines.append(cur)
            cur = wd
        else:
            cur = (cur + " " + wd).strip()
    lines.append(cur)
    y = 200 - len(lines) * 22
    for ln in lines:
        d.text(((w - d.textlength(ln, font=fb)) / 2, y), ln, font=fb, fill="white")
        y += 46
    d.text(((w - d.textlength(author, font=fr)) / 2, h - 110), author, font=fr, fill=(240, 240, 240))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def fb2(title: str, last: str, first: str, genre: str, text: str, img: bytes | None) -> str:
    import base64
    cov = '<coverpage><image l:href="#cover.jpg"/></coverpage>' if img else ""
    binary = f'<binary id="cover.jpg" content-type="image/jpeg">{base64.b64encode(img).decode()}</binary>' if img else ""
    paras = "".join(f"<p>{text}</p>" for _ in range(30))
    return (f'<?xml version="1.0" encoding="utf-8"?><FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0" '
            f'xmlns:l="http://www.w3.org/1999/xlink"><description><title-info><genre>{genre}</genre>'
            f"<author><first-name>{first}</first-name><last-name>{last}</last-name></author>"
            f"<book-title>{title}</book-title><lang>ru</lang>{cov}</title-info></description>"
            f"<body><title><p>{title}</p></title><section>{paras}</section></body>{binary}</FictionBook>")


def main(dest: Path) -> None:
    main_dir, extra_dir, col_dir = dest / "main", dest / "extra", dest / "collection"
    for i, (code, title, author, lang) in enumerate(MAIN):
        d = main_dir / f"{code} {title} ({author})"
        d.mkdir(parents=True, exist_ok=True)
        text = "\n\n".join([LOREM_RU if lang == "ru" else LOREM_EN] * 40)
        (d / f"{title}.txt").write_text(f"{title}\n\n{author}\n\n{text}", encoding="utf-8")
        (d / "cover.jpg").write_bytes(cover(title, author, i))
    for i, (title, last, first, fmt) in enumerate(EXTRA):
        d = extra_dir / ("Проза" if i % 2 == 0 else "Рассказы")
        d.mkdir(parents=True, exist_ok=True)
        if fmt == "fb2":
            (d / f"{title}.fb2").write_text(fb2(title, last, first, "prose_rus_classic", LOREM_RU, None), encoding="utf-8")
        else:
            (d / f"{title}.txt").write_text("\n\n".join([LOREM_RU] * 30), encoding="utf-8")
    col_dir.mkdir(parents=True, exist_ok=True)
    lines = []
    with zipfile.ZipFile(col_dir / "fb2-000001-000100.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for i, (series, serno, title, author, genre) in enumerate(INPX, 1):
            parts = author.strip(":").split(",")
            img = cover(title, f"{parts[1]} {parts[0]}".strip(), 100 + i)
            data = fb2(title, parts[0], parts[1], genre.split(":")[0], LOREM_RU, img).encode()
            z.writestr(f"{i}.fb2", data)
            lines.append("\x04".join([author, genre, title, series, serno, str(i), str(len(data)), str(i), "0",
                                      "fb2", f"2026-0{1 + i % 9}-1{i % 10}", "ru", "", "", ""]))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("collection.info", "Demo collection\ndemo\n65536\nДемо-коллекция Либереи")
        z.writestr("version.info", "20260101")
        z.writestr("fb2-000001-000100.inp", "\r\n".join(lines) + "\r\n")
    (col_dir / "demo.inpx").write_bytes(buf.getvalue())
    print(f"Готово. Укажите в мастере первого запуска или в «Настройках»:\n"
          f"  основная библиотека:      {main_dir}\n"
          f"  дополнительная:           {extra_dir}\n"
          f"  файл коллекции .inpx:     {col_dir / 'demo.inpx'}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(Path(sys.argv[1]).resolve())
