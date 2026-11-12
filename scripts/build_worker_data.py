"""Write worker/anonymize/data.js, the word lists of the Worker anonymizer.

The Worker port of the anonymizer (worker/anonymize/) cannot read CSV or YAML files, so its lists
are built here from the same sources and with the same code as the Python lexicon
(src/enps/anonymize/lexicon.py):

    .venv/bin/python scripts/build_worker_data.py            # write the file
    .venv/bin/python scripts/build_worker_data.py --check    # exit 1 when the file is stale

Sources: every row of data/reference/ibge_first_names_2022.csv and ibge_surnames_2022.csv (the
Python lexicon reads the whole files, so the cut is the one made by fetch_ibge_names.py), the
place lists data/reference/ibge_municipalities.csv, ibge_districts.csv and ibge_subdistricts.csv
with place_word_classes.csv (fetch_ibge_places.py), config/name_stoplist.txt,
config/anonymizer.yaml (allowlist, name_cues, connectors, nicknames, places) and the company, area
and tenure labels of config/settings.yaml. Settings are read from the file, without ENPS_*
environment overrides, so the output only depends on the repository.
"""
from __future__ import annotations

import argparse
import sys
import textwrap
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from enps.anonymize import lexicon as lx  # noqa: E402

OUT = ROOT / "worker" / "anonymize" / "data.js"
# Paths under this checkout, not under config.ROOT, which ENPS_ROOT can move (the Docker image sets it).
SETTINGS_FILE = ROOT / "config" / "settings.yaml"
ANONYMIZER_FILE = ROOT / "config" / "anonymizer.yaml"
STOPLIST_FILE = ROOT / "config" / "name_stoplist.txt"
REFERENCE = ROOT / "data" / "reference"
FIRST_NAMES_FILE = REFERENCE / "ibge_first_names_2022.csv"
SURNAMES_FILE = REFERENCE / "ibge_surnames_2022.csv"
PLACE_FILES = tuple(REFERENCE / f"ibge_{level}.csv" for level in ("municipalities", "districts", "subdistricts"))
PLACE_WORDS_FILE = REFERENCE / "place_word_classes.csv"
WIDTH = 100

HEADER = """\
// Word lists of the Worker anonymizer. Do not edit: this file is written by
// scripts/build_worker_data.py from data/reference/ (IBGE names and places, place word classes),
// config/name_stoplist.txt, config/anonymizer.yaml and config/settings.yaml, and
// tests/test_anonymizer_cases.py fails when it is stale.
//
// Entries are normalized like the Python lexicon (case folded, accents removed), except
// ALLOW_ACCENTED, which keeps its accents. Word lists are separated by whitespace and phrase
// lists hold one phrase per line; lexicon.js splits them once at load.
"""

# (export name, Lexicon field, kind). "words" never contain spaces; "phrases" do.
FIELDS = (
    ("FIRST_NAMES", "first_names", "words"),
    ("SURNAMES", "surnames", "words"),
    ("STOPLIST", "stoplist", "words"),
    ("ALLOW_TOKENS", "allow_tokens", "words"),
    ("ALLOW_PHRASES", "allow_phrases", "phrases"),
    ("ALLOW_ACCENTED", "allow_accented", "words"),
    ("NAME_CUES", "name_cues", "words"),
    ("ABBREVIATION_CUES", "abbreviation_cues", "words"),
    ("CONNECTORS", "connectors", "words"),
    ("PLACES", "places", "phrases"),
    ("PLACE_COMMON", "place_common", "words"),
    ("PLACE_MIXED", "place_mixed", "words"),
    ("PLACE_STOPLIST", "place_stoplist", "phrases"),
    ("PLACE_CUES", "place_cues", "phrases"),
)
_UNSAFE = ("`", "\\", "${", "\n")


def build() -> lx.Lexicon:
    """The lexicon of the Python anonymizer, from the repository files only."""
    with open(ANONYMIZER_FILE, encoding="utf-8") as fh:
        anonymizer_cfg = yaml.safe_load(fh)
    with open(SETTINGS_FILE, encoding="utf-8") as fh:
        settings = yaml.safe_load(fh)
    return lx.build_lexicon(
        anonymizer_cfg,
        settings,
        lx._read_ranking(FIRST_NAMES_FILE),
        lx._read_ranking(SURNAMES_FILE),
        lx._read_stoplist(STOPLIST_FILE),
        lx.read_places(PLACE_FILES),
        lx.read_place_words(PLACE_WORDS_FILE),
    )


def _check_entries(name: str, entries: list[str], kind: str) -> None:
    for entry in entries:
        if not entry or any(s in entry for s in _UNSAFE) or (kind == "words" and entry.split() != [entry]):
            raise ValueError(f"{name}: entry {entry!r} cannot be written to data.js")


def _export(name: str, entries: list[str], kind: str) -> str:
    _check_entries(name, entries, kind)
    if kind == "phrases":
        lines = entries
    else:
        lines = textwrap.wrap(" ".join(entries), WIDTH, break_long_words=False, break_on_hyphens=False)
    body = "\n".join(lines)
    count = f"{len(entries)} {'entry' if len(entries) == 1 else 'entries'}"
    return f"// {count}\nexport const {name} = `\n{body}\n`;\n"


def render(lexicon: lx.Lexicon | None = None) -> str:
    """Full text of data.js. Sorted entries make the output deterministic."""
    lexicon = lexicon or build()
    parts = [HEADER]
    for name, field, kind in FIELDS:
        parts.append(_export(name, sorted(getattr(lexicon, field)), kind))
    return "\n".join(parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="only report whether data.js is stale")
    args = parser.parse_args(argv)
    content = render()
    current = OUT.read_text(encoding="utf-8") if OUT.exists() else None
    if args.check:
        stale = current != content
        print(f"{OUT.relative_to(ROOT)} is {'stale' if stale else 'up to date'}")
        return 1 if stale else 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(content, encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)} ({len(content.encode('utf-8'))} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
