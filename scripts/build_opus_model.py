"""Собрать готовый архив модели OPUS-MT tc-big (en→ru) для релиза «models-1».

    python scripts/build_opus_model.py [исходный.zip] [--out dist]

Скачивает исходную модель Helsinki-NLP (если не указан файл), конвертирует в CTranslate2 int8
и упаковывает в dist/model-en_ru-opus-big.zip вместе с ATTRIBUTION.md (лицензия CC-BY 4.0).
Запускается в GitHub Actions (.github/workflows/models.yml).
"""
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import models  # noqa: E402

args = [a for a in sys.argv[1:]]
out_dir = Path(args[args.index("--out") + 1]) if "--out" in args else ROOT / "dist"
if "--out" in args:
    i = args.index("--out")
    del args[i:i + 2]
out_dir.mkdir(parents=True, exist_ok=True)
work = out_dir / "_build"
shutil.rmtree(work, ignore_errors=True)
work.mkdir()

src = Path(args[0]) if args else work / "opus_src.zip"
if not args:
    print("Скачиваю", models.OPUS_BIG_SRC)
    urllib.request.urlretrieve(models.OPUS_BIG_SRC, src)

model = work / "en_ru_big"
print("Конвертирую…")
models.convert_opus(src, model)
dest = out_dir / models.OPUS_BIG_ASSET
with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
    for p in sorted(model.rglob("*")):
        if p.is_file():
            z.write(p, Path("en_ru_big") / p.relative_to(model))
shutil.rmtree(work, ignore_errors=True)
print(f"Готово: {dest} ({dest.stat().st_size >> 20} МБ)")
