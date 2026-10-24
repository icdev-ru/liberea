"""Модели перевода en→ru: установка, конвертация OPUS-MT, список установленных.

Модели лежат в data/models/<имя>/ (формат CTranslate2: model.bin + словари sentencepiece).
  en_ru      — Argos Translate (быстрая), скачивается с серверов Argos;
  en_ru_big  — Helsinki-NLP OPUS-MT tc-big (качественнее, медленнее), CC-BY 4.0;
               готовый архив выкладывается в релизе Либереи «models-1».
"""
from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

from .config import _resolve

MODELS_DIR = "data/models"
OPUS_BIG_SRC = ("https://object.pouta.csc.fi/Tatoeba-MT-models/eng-zle/"
                "opusTCv20210807+bt_transformer-big_2022-03-13.zip")
RELEASE_REPO = "icdev-ru/liberea"
RELEASE_TAG = "models-1"
OPUS_BIG_ASSET = "model-en_ru-opus-big.zip"
OPUS_BIG_RELEASE_URL = f"https://github.com/{RELEASE_REPO}/releases/download/{RELEASE_TAG}/{OPUS_BIG_ASSET}"

ATTRIBUTION = """# OPUS-MT tc-big English → East Slavic (eng-zle), CTranslate2 int8

Converted for Liberea (https://github.com/icdev-ru/liberea) from the original Marian model:
  {src}

Original model: Helsinki-NLP, Tatoeba Translation Challenge / OPUS-MT
  https://github.com/Helsinki-NLP/Tatoeba-Challenge
  https://github.com/Helsinki-NLP/OPUS-MT-train
Authors: Jörg Tiedemann and contributors, University of Helsinki.
License: Creative Commons Attribution 4.0 International (CC-BY 4.0)
  https://creativecommons.org/licenses/by/4.0/

Changes: converted to CTranslate2 format with int8 quantization (ctranslate2.converters.OpusMTConverter).

Please cite:
  Jörg Tiedemann and Santhosh Thottingal. 2020. OPUS-MT — Building open translation services for the World.
  In Proceedings of the 22nd Annual Conference of the European Association for Machine Translation (EAMT).
"""


def models_dir() -> Path:
    d = _resolve(MODELS_DIR)
    d.mkdir(parents=True, exist_ok=True)
    return d


def describe(path: Path) -> str:
    """Человеческое название модели по её папке."""
    meta = path / "metadata.json"
    if meta.exists():
        try:
            m = json.loads(meta.read_text(encoding="utf-8"))
            return f"Argos Translate {m.get('package_version', '')} — быстрая".replace("  ", " ")
        except ValueError:
            pass
    if (path / "source.spm").exists() or (path / "ATTRIBUTION.md").exists():
        return "OPUS-MT tc-big — качественная, медленнее"
    return path.name


def installed() -> list[tuple[str, str]]:
    """[(относительный путь для config, описание)] — модели с model.bin."""
    out = []
    base = models_dir()
    for d in sorted(p for p in base.iterdir() if p.is_dir() and not p.name.startswith("_")):
        if next(d.rglob("model.bin"), None):
            out.append((f"{MODELS_DIR}/{d.name}", describe(d)))
    return out


def install_prepared(zpath: Path, out: Path) -> None:
    """Готовый архив CTranslate2 (model.bin внутри) → out."""
    work = out.parent / "_unpack"
    shutil.rmtree(work, ignore_errors=True)
    with zipfile.ZipFile(zpath) as z:
        z.extractall(work)
    mb = next(work.rglob("model.bin"), None)
    if not mb:
        shutil.rmtree(work, ignore_errors=True)
        raise ValueError("в архиве нет model.bin — это не модель CTranslate2")
    shutil.rmtree(out, ignore_errors=True)
    shutil.move(str(mb.parent), str(out))
    shutil.rmtree(work, ignore_errors=True)


def convert_opus(zpath: Path, out: Path) -> None:
    """Исходный архив OPUS-MT (Marian, decoder.yml) → CTranslate2 int8 в out."""
    import ctranslate2.converters as conv
    work = out.parent / "_opus_src"
    shutil.rmtree(work, ignore_errors=True)
    with zipfile.ZipFile(zpath) as z:
        z.extractall(work)
    dec = next(work.rglob("decoder.yml"), None)
    if not dec:
        shutil.rmtree(work, ignore_errors=True)
        raise ValueError("в архиве нет decoder.yml — это не модель OPUS-MT")
    mdir = dec.parent
    shutil.rmtree(out, ignore_errors=True)
    conv.OpusMTConverter(str(mdir)).convert(str(out), quantization="int8", force=True)
    for name in ("source.spm", "target.spm", "README.md", "LICENSE"):
        if (mdir / name).exists():
            shutil.copy(mdir / name, out / name)
    (out / "ATTRIBUTION.md").write_text(ATTRIBUTION.format(src=OPUS_BIG_SRC), encoding="utf-8")
    shutil.rmtree(work, ignore_errors=True)


def is_opus_source(zpath: Path) -> bool:
    with zipfile.ZipFile(zpath) as z:
        return any(n.endswith("decoder.yml") for n in z.namelist())
