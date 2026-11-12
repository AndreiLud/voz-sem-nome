"""Download the IBGE lists of places used by the anonymizer's place gazetteer.

Usage:
    python scripts/fetch_ibge_places.py                 # download the lists, then derive the word classes
    python scripts/fetch_ibge_places.py --classes-only  # only derive the word classes from the files here

Writes to data/reference/:

* ibge_municipalities.csv, ibge_districts.csv and ibge_subdistricts.csv: the official lists of the
  IBGE localities API, as published (one row per locality, names untouched).
* place_word_classes.csv: the words of those names that are also ordinary Portuguese words.
  A word's class compares how often it is written in lowercase and capitalized, by its frequency
  rank in the vector table of the spaCy model pt_core_news_md (500,000 forms, most frequent
  first, case and accents kept). "common": the capitalized form is less than half as frequent as
  the lowercase one ("saúde", "liberdade", "contagem"); "mixed": between half and twice as
  frequent ("palmas", "perus"). Words with no lowercase form in the table, or a capitalized form more
  than twice as frequent ("guarulhos", "campinas"), are proper names and are not written.

This and fetch_ibge_names.py are the only places in the project that call the IBGE API. The
anonymizer reads the files written here and never touches the network.
"""
from __future__ import annotations

import argparse
import csv
import sys
import unicodedata
from collections.abc import Iterable
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from enps.anonymize import lexicon as lx  # noqa: E402

BASE_URL = "https://servicodados.ibge.gov.br/api/v1/localidades/{level}"
OUT = ROOT / "data" / "reference"
MODEL_NAME = "pt_core_news_md"
COMMON_MAX_RATIO = 0.5   # capitalized frequency / lowercase frequency at or below this: common
MIXED_MAX_RATIO = 2.0    # above COMMON_MAX_RATIO and up to this: mixed; above: proper name

# (file, API level, columns: (CSV column, key of the flat "nivelado" view))
LISTS = (
    ("ibge_municipalities.csv", "municipios", (
        ("id", "municipio-id"), ("name", "municipio-nome"), ("uf", "UF-sigla"),
    )),
    ("ibge_districts.csv", "distritos", (
        ("id", "distrito-id"), ("name", "distrito-nome"), ("municipality_id", "municipio-id"),
        ("municipality", "municipio-nome"), ("uf", "UF-sigla"),
    )),
    ("ibge_subdistricts.csv", "subdistritos", (
        ("id", "subdistrito-id"), ("name", "subdistrito-nome"), ("district_id", "distrito-id"),
        ("district", "distrito-nome"), ("municipality_id", "municipio-id"),
        ("municipality", "municipio-nome"), ("uf", "UF-sigla"),
    )),
)
CLASS_COLUMNS = ("word", "class", "lower_rank", "capitalized_rank")


def fetch_list(client: httpx.Client, level: str, columns: tuple[tuple[str, str], ...]) -> list[dict]:
    """One locality list, flattened to the given columns and sorted by IBGE code."""
    response = client.get(BASE_URL.format(level=level), params={"view": "nivelado"})
    response.raise_for_status()
    rows = [{column: item[key] for column, key in columns} for item in response.json()]
    return sorted(rows, key=lambda row: row["id"])


def write_csv(path: Path, rows: Iterable[dict], fieldnames: Iterable[str]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(fieldnames))
        writer.writeheader()
        writer.writerows(rows)


def frequency_ranks() -> dict[str, int]:
    """Form -> position in the model's vector table (0 is the most frequent form)."""
    import spacy

    nlp = spacy.load(MODEL_NAME, exclude=["tok2vec", "morphologizer", "parser", "lemmatizer",
                                          "attribute_ruler", "ner", "senter"])
    strings = nlp.vocab.strings
    ranks: dict[str, int] = {}
    for position, key in enumerate(nlp.vocab.vectors.key2row):
        ranks.setdefault(strings[key], position)
    return ranks


def _strip_accents(word: str) -> str:
    decomposed = unicodedata.normalize("NFKD", word)
    return unicodedata.normalize("NFC", "".join(ch for ch in decomposed if not unicodedata.combining(ch)))


def classify_spelling(spelling: str, ranks: dict[str, int]) -> tuple[str, int | None, int | None]:
    """Class of one spelling of a word, with the best ranks of its lowercase and capitalized forms.

    Forms with and without accents both count, so the place "Sé" is judged with the word "se".
    """
    lower = spelling.lower()
    lowers = {lower, _strip_accents(lower)}
    capitals = {w[:1].upper() + w[1:] for w in lowers}
    lower_rank = min((ranks[w] for w in lowers if w in ranks), default=None)
    capital_rank = min((ranks[w] for w in capitals if w in ranks), default=None)
    if lower_rank is None:
        return "proper", None, capital_rank
    if capital_rank is None:
        return "common", lower_rank, None
    # Frequency falls roughly as 1/rank, so this ratio estimates capitalized / lowercase uses.
    ratio = (lower_rank + 1) / (capital_rank + 1)
    if ratio <= COMMON_MAX_RATIO:
        return "common", lower_rank, capital_rank
    if ratio <= MIXED_MAX_RATIO:
        return "mixed", lower_rank, capital_rank
    return "proper", lower_rank, capital_rank


def place_spellings(paths: Iterable[Path]) -> dict[str, set[str]]:
    """Normalized word -> spellings, over the words of the place names the anonymizer uses."""
    spellings: dict[str, set[str]] = {}
    for name in lx.read_place_names(paths):
        for word in lx.PLACE_WORD_RE.findall(name):
            spellings.setdefault(lx.normalize(word), set()).add(word)
    return spellings


def word_classes(paths: Iterable[Path], ranks: dict[str, int]) -> list[dict]:
    """Rows of place_word_classes.csv. A word with several spellings takes its most common reading."""
    order = {"common": 0, "mixed": 1, "proper": 2}
    rows = []
    for word, spellings in sorted(place_spellings(paths).items()):
        best = min((classify_spelling(s, ranks) for s in sorted(spellings)), key=lambda c: order[c[0]])
        if best[0] != "proper":
            rows.append(dict(zip(CLASS_COLUMNS, (word, *best))))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--classes-only", action="store_true",
                        help="skip the download and only rewrite place_word_classes.csv")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    if not args.classes_only:
        with httpx.Client(timeout=60.0) as client:
            for filename, level, columns in LISTS:
                rows = fetch_list(client, level, columns)
                write_csv(args.out / filename, rows, (c for c, _ in columns))
                print(f"{level}: {len(rows)} -> {filename}")
    paths = [args.out / filename for filename, _, _ in LISTS]
    rows = word_classes(paths, frequency_ranks())
    write_csv(args.out / "place_word_classes.csv", rows, CLASS_COLUMNS)
    counts = {c: sum(r["class"] == c for r in rows) for c in ("common", "mixed")}
    print(f"word classes: {counts} -> place_word_classes.csv")


if __name__ == "__main__":
    main()
