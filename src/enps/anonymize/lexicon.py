"""Word lists shared by the name gazetteer and the NER filter.

Every lookup goes through `normalize` (case folded, accents removed), so "João" matches the
IBGE entry "joao". The lists are read once from config/ and data/reference/; nothing here
touches the network.
"""
from __future__ import annotations

import csv
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from enps import config

REFERENCE_DIR = config.ROOT / "data" / "reference"
FIRST_NAMES_FILE = REFERENCE_DIR / "ibge_first_names_2022.csv"
SURNAMES_FILE = REFERENCE_DIR / "ibge_surnames_2022.csv"
ANONYMIZER_FILE = config.CONFIG_DIR / "anonymizer.yaml"
STOPLIST_FILE = config.CONFIG_DIR / "name_stoplist.txt"

_NON_WORD = re.compile(r"[\W_]+")
_DIMINUTIVE_SUFFIXES = ("zinho", "zinha", "inho", "inha")


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
    )
