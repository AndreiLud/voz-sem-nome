"""Word lists shared by the name gazetteer, the place gazetteer and the NER filter.

Every lookup goes through `normalize` (case folded, accents removed), so "João" matches the
IBGE entry "joao". The lists are read once from config/ and data/reference/; nothing here
touches the network.
"""
from __future__ import annotations

import csv
import re
import unicodedata
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from enps import config

REFERENCE_DIR = config.ROOT / "data" / "reference"
FIRST_NAMES_FILE = REFERENCE_DIR / "ibge_first_names_2022.csv"
SURNAMES_FILE = REFERENCE_DIR / "ibge_surnames_2022.csv"
MUNICIPALITIES_FILE = REFERENCE_DIR / "ibge_municipalities.csv"
DISTRICTS_FILE = REFERENCE_DIR / "ibge_districts.csv"
SUBDISTRICTS_FILE = REFERENCE_DIR / "ibge_subdistricts.csv"
PLACE_WORDS_FILE = REFERENCE_DIR / "place_word_classes.csv"
ANONYMIZER_FILE = config.CONFIG_DIR / "anonymizer.yaml"
STOPLIST_FILE = config.CONFIG_DIR / "name_stoplist.txt"

_NON_WORD = re.compile(r"[\W_]+")
_DIMINUTIVE_SUFFIXES = ("zinho", "zinha", "inho", "inha")

# Official place names the gazetteer can match: words of letters joined by spaces, hyphens or an
# apostrophe ("Embu-Guaçu", "Pau D'Arco"). Names with digits, dots, slashes, quotes or brackets
# are administrative labels of subdistricts ("Zona 01", "U.T.P. Central", "RA 02 - Santa Lúcia").
PLACE_WORD_RE = re.compile(r"[^\W\d_]+")
_PLACE_NAME_RE = re.compile(r"[^\W\d_]+(?:(?: +|-|['\u2019])[^\W\d_]+)*")
# Labels of administrative divisions, and directions or centers that point to no place in particular.
_ADMIN_WORDS = frozenset(
    "administracao administrativa censitaria distrito nucleo regiao regional "
    "ra rpa setor sub subdistrito territorial unidade uplans zona".split()
)
_GENERIC_PLACES = frozenset({
    "centro", "norte", "sul", "leste", "oeste", "nordeste", "noroeste", "sudeste", "sudoeste",
    "centro norte", "centro sul", "centro leste", "centro oeste", "centro urbano", "sede",
    "central", "industrial",
})


def normalize(word: str) -> str:
    """Case fold and strip accents: "Vitória" -> "vitoria"."""
    decomposed = unicodedata.normalize("NFKD", word.casefold())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize_phrase(text: str) -> str:
    """Normalize a multi-word expression: "Segunda-feira" -> "segunda feira"."""
    return _NON_WORD.sub(" ", normalize(text)).strip()


def fold(word: str) -> str:
    """Case fold but keep accents: "Março" -> "março" (normalize would give "marco")."""
    return unicodedata.normalize("NFC", word.casefold().strip())


@dataclass(frozen=True)
class Lexicon:
    first_names: frozenset[str]
    surnames: frozenset[str]
    stoplist: frozenset[str]          # names that are also common words
    allow_tokens: frozenset[str]      # single words that are never masked by NER or gazetteer
    allow_phrases: frozenset[str]     # multi-word expressions never masked as a whole
    name_cues: frozenset[str]         # words that make the next first name a name, even lowercase
    abbreviation_cues: frozenset[str]  # cues written with a period: "Sr. Antunes"
    connectors: frozenset[str]        # "da", "de", "dos" inside full names
    # Allowlist words that read as a name once accents are dropped ("março" vs Marco), kept with
    # their accents: allowed only when written with them.
    allow_accented: frozenset[str] = frozenset()
    places: frozenset[str] = frozenset()          # official place names, normalized phrases
    place_common: frozenset[str] = frozenset()    # words of place names that are common words
    place_mixed: frozenset[str] = frozenset()     # words used about as often in both cases
    place_stoplist: frozenset[str] = frozenset()  # places that usually mean something else
    place_cues: frozenset[str] = frozenset()      # phrases right before a place: "moro em"
    # First word of a place name -> its lengths in words, longest first (built from `places`).
    place_index: Mapping[str, tuple[int, ...]] = field(default_factory=dict, compare=False)

    def is_first_name(self, norm: str) -> bool:
        """True for an IBGE first name, a configured nickname or a diminutive of a first name."""
        if norm in self.first_names:
            return True
        for suffix in _DIMINUTIVE_SUFFIXES:
            if norm.endswith(suffix):
                stem = norm[: -len(suffix)]
                candidates = (stem,) if suffix.startswith("z") else (stem + "o", stem + "a", stem)
                if any(len(c) >= 3 and c in self.first_names and c not in self.stoplist
                       for c in candidates):
                    return True
        return False

    def is_known_name(self, norm: str) -> bool:
        """First name or surname that is not also a common word."""
        if norm in self.stoplist:
            return False
        return self.is_first_name(norm) or norm in self.surnames

    def place_class(self, norm: str) -> str:
        """"common", "mixed" or "proper" for a normalized word of a place name."""
        if norm in self.place_common:
            return "common"
        return "mixed" if norm in self.place_mixed else "proper"

    def is_allowed(self, text: str) -> bool:
        """Whether a word or expression, as written or normalized, is on the allowlist."""
        phrase = normalize_phrase(text)
        return (phrase in self.allow_tokens or phrase in self.allow_phrases
                or fold(text) in self.allow_accented)


def _read_ranking(path: Path) -> frozenset[str]:
    with open(path, encoding="utf-8", newline="") as fh:
        return frozenset(normalize(row["name"]) for row in csv.DictReader(fh))


def _read_stoplist(path: Path) -> frozenset[str]:
    words: set[str] = set()
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            word = line.split("#", 1)[0].strip()
            if word:
                words.add(normalize(word))
    return frozenset(words)


def place_name_ok(name: str) -> bool:
    """Whether an official place name can be matched in text (see _PLACE_NAME_RE, _ADMIN_WORDS)."""
    name = name.strip()
    if not _PLACE_NAME_RE.fullmatch(name) or (len(name) > 1 and name.isupper()):  # "PTB", "SIA"
        return False
    key = normalize_phrase(name)
    return key not in _GENERIC_PLACES and not any(w in _ADMIN_WORDS for w in key.split())


def read_place_names(paths: Iterable[Path]) -> Iterator[str]:
    """Official place names, as spelled by IBGE, that pass `place_name_ok`."""
    for path in paths:
        with open(path, encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                if place_name_ok(row["name"]):
                    yield row["name"].strip()


def read_places(paths: Iterable[Path]) -> frozenset[str]:
    return frozenset(normalize_phrase(name) for name in read_place_names(paths))


def read_place_words(path: Path) -> dict[str, str]:
    """Normalized word -> "common" or "mixed" (data/reference/place_word_classes.csv)."""
    with open(path, encoding="utf-8", newline="") as fh:
        return {row["word"]: row["class"] for row in csv.DictReader(fh)}


def _place_index(places: Iterable[str]) -> dict[str, tuple[int, ...]]:
    lengths: dict[str, set[int]] = {}
    for place in places:
        words = place.split()
        lengths.setdefault(words[0], set()).add(len(words))
    return {first: tuple(sorted(n, reverse=True)) for first, n in lengths.items()}


def _place_cues(cfg: list[dict[str, Any]]) -> frozenset[str]:
    """Cue phrases from `places.cues`: every word of `first`, followed by every word of `then`."""
    cues: set[str] = set()
    for entry in cfg:
        firsts = [normalize_phrase(str(w)) for w in entry.get("first", [])]
        thens = [normalize_phrase(str(w)) for w in entry.get("then", [])] or [""]
        cues.update(f"{a} {b}".strip() for a in firsts for b in thens)
    return frozenset(cues)


def _settings_terms(settings: dict[str, Any]) -> list[str]:
    """Area and tenure labels, area codes and the company name: never personal data."""
    survey = settings.get("survey", {})
    terms = [survey.get("company", "")]
    for group in ("areas", "tenure_bands"):
        for item in survey.get(group, []):
            terms.append(item.get("code", ""))
            terms.extend((item.get("label") or {}).values())
    return [t for t in terms if t]


def build_lexicon(
    anonymizer_cfg: dict[str, Any],
    settings: dict[str, Any],
    first_names: frozenset[str],
    surnames: frozenset[str],
    stoplist: frozenset[str],
    places: frozenset[str] = frozenset(),
    place_words: Mapping[str, str] | None = None,
) -> Lexicon:
    connectors = frozenset(normalize(w) for w in anonymizer_cfg.get("connectors", []))
    nicknames = frozenset(normalize(w) for w in anonymizer_cfg.get("nicknames", []))
    tokens: set[str] = set()
    phrases: set[str] = set()
    accented: set[str] = set()
    for term in list(anonymizer_cfg.get("allowlist", [])) + _settings_terms(settings):
        phrase = normalize_phrase(str(term))
        if not phrase:
            continue
        if " " in phrase:
            phrases.add(phrase)
            # Words of a phrase ("Pessoas e Cultura") are safe alone too, unless they are names.
            tokens.update(w for w in phrase.split()
                          if w not in connectors and w not in first_names and len(w) > 1)
        elif phrase != fold(str(term)) and phrase in (first_names | surnames) - stoplist:
            accented.add(fold(str(term)))  # "março": allowing "marco" would hide the name Marco
        else:
            tokens.add(phrase)
    cues = [normalize(w) for w in anonymizer_cfg.get("name_cues", [])]
    places_cfg = anonymizer_cfg.get("places") or {}
    place_words = place_words or {}
    return Lexicon(
        first_names=first_names | nicknames,
        surnames=surnames,
        stoplist=stoplist,
        allow_tokens=frozenset(tokens),
        allow_phrases=frozenset(phrases),
        name_cues=frozenset(c.rstrip(".") for c in cues),
        abbreviation_cues=frozenset(c.rstrip(".") for c in cues if c.endswith(".")),
        connectors=connectors,
        allow_accented=frozenset(accented),
        places=places,
        place_common=frozenset(w for w, c in place_words.items() if c == "common"),
        place_mixed=frozenset(w for w, c in place_words.items() if c == "mixed"),
        place_stoplist=frozenset(normalize_phrase(str(w)) for w in places_cfg.get("stoplist", [])),
        place_cues=_place_cues(places_cfg.get("cues", [])),
        place_index=_place_index(places),
    )


@lru_cache(maxsize=1)
def lexicon() -> Lexicon:
    with open(ANONYMIZER_FILE, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    return build_lexicon(
        cfg,
        config.settings(),
        _read_ranking(FIRST_NAMES_FILE),
        _read_ranking(SURNAMES_FILE),
        _read_stoplist(STOPLIST_FILE),
        read_places((MUNICIPALITIES_FILE, DISTRICTS_FILE, SUBDISTRICTS_FILE)),
        read_place_words(PLACE_WORDS_FILE),
    )
