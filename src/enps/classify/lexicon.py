"""Risk lexicon: regex patterns that catch explicit and indirect signs of critical cases.

It runs on the anonymized text before the model and never depends on it, so an explicit case
("me assediou", "nao quero mais viver") is flagged even if the model fails. Matching is
accent-insensitive and case-insensitive; hit offsets refer to the text that was passed in.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from enps import config

STRONG = "strong"
WEAK = "weak"
LEXICON_PATH = config.CONFIG_DIR / "risk_lexicon.yaml"
_MACRO = re.compile(r"\{\{\s*(\w+)\s*\}\}")
_CLAUSE_BREAK = re.compile(r"[.,;:!?\n]")
_WORD = re.compile(r"\w+")


@dataclass(frozen=True)
class LexiconHit:
    type: str          # critical type code
    strength: str      # "strong" or "weak"
    text: str          # matched text, as written in the input
    start: int
    end: int
    negated: bool = False   # a strong pattern downgraded to weak by a negation word

    def as_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"type": self.type, "strength": self.strength, "text": self.text}
        if self.negated:
            data["negated"] = True
        return data


@dataclass(frozen=True)
class _Rule:
    type: str
    strength: str
    regex: re.Pattern[str]


def fold(text: str) -> tuple[str, list[int]]:
    """Lower-case and strip accents; also return, per folded char, its index in `text`."""
    chars: list[str] = []
    index: list[int] = []
    for i, ch in enumerate(text):
        base = "".join(c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c))
        for c in base.lower():
            chars.append(c)
            index.append(i)
    return "".join(chars), index


def _expand(pattern: str, macros: dict[str, str]) -> str:
    def sub(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in macros:
            raise ValueError(f"unknown lexicon macro {name!r} in {pattern!r}")
        return macros[name]

    return _MACRO.sub(sub, pattern)


class Lexicon:
    def __init__(self, data: dict[str, Any], *, digest: str = ""):
        self.version = f"risk_lexicon.v{data['version']}" + (f"+{digest[:8]}" if digest else "")
        negation = data.get("negation") or {}
        self.negation_words = frozenset(negation.get("words", ()))
        self.negation_window = int(negation.get("window", 3))
        macros = {k: str(v) for k, v in (data.get("macros") or {}).items()}
        known = set(config.critical_codes())
        self.rules: list[_Rule] = []
        for ctype, groups in data["types"].items():
            if ctype not in known:
                raise ValueError(f"lexicon type {ctype!r} is not a critical code")
            for strength in (STRONG, WEAK):
                for pattern in groups.get(strength) or ():
                    body = _expand(pattern, macros)
                    self.rules.append(_Rule(ctype, strength, re.compile(rf"(?<!\w)(?:{body})(?!\w)")))

    def _negated(self, folded: str, start: int) -> bool:
        clause = _CLAUSE_BREAK.split(folded[:start])[-1]
        words = _WORD.findall(clause)[-self.negation_window:]
        return any(w in self.negation_words for w in words)

    def match(self, text: str) -> list[LexiconHit]:
        """All hits, sorted by position; overlapping hits of the same type keep the strongest."""
        if not text:
            return []
        folded, index = fold(text)
        candidates: list[LexiconHit] = []
        for rule in self.rules:
            for m in rule.regex.finditer(folded):
                if m.end() == m.start():
                    continue
                start, end = index[m.start()], index[m.end() - 1] + 1
                negated = rule.strength == STRONG and self._negated(folded, m.start())
                candidates.append(LexiconHit(
                    type=rule.type, strength=WEAK if negated else rule.strength,
                    text=text[start:end], start=start, end=end, negated=negated))
        return _dedupe(candidates)


def _dedupe(hits: list[LexiconHit]) -> list[LexiconHit]:
    ranked = sorted(hits, key=lambda h: (h.strength != STRONG, -(h.end - h.start), h.start))
    kept: list[LexiconHit] = []
    for hit in ranked:
        if not any(k.type == hit.type and k.start < hit.end and hit.start < k.end for k in kept):
            kept.append(hit)
    return sorted(kept, key=lambda h: (h.start, h.type))


def load_lexicon(path: Path = LEXICON_PATH) -> Lexicon:
    raw = path.read_bytes()
    return Lexicon(yaml.safe_load(raw.decode("utf-8")), digest=hashlib.sha256(raw).hexdigest())


@lru_cache(maxsize=1)
def default_lexicon() -> Lexicon:
    return load_lexicon()


def strong_hits(hits: list[LexiconHit]) -> list[LexiconHit]:
    return [h for h in hits if h.strength == STRONG]
