"""Typing noise that makes the composed comments look like survey answers typed on a phone.

Noise is applied to the text pieces around the planted personal data, never inside a planted
value, so the gold offsets stay exact.
"""
from __future__ import annotations

import random
import re
import unicodedata
from dataclasses import dataclass

from .bank import PhraseBank

TOKEN_RE = re.compile(r"\w+|\W+")
LAST_WORD_RE = re.compile(r"[^\W\d_]{3,}")
# "pra" before an article contracts: "pra a" -> "pra", "pra o" -> "pro", "pra os" -> "pros".
CONTRACTION_RE = re.compile(r"\bpra ([ao])(s?)\b")
# A dot after these does not end a sentence.
DOT_ABBREVIATIONS = ("b.o.", "mat.", "sr.", "sra.", "dr.")


@dataclass(frozen=True)
class StyleParams:
    lowercase: bool          # the whole comment typed in lower case
    capitalize_first: float  # chance that the keyboard capitalized the first letter anyway
    accent_drop: float       # per word
    abbreviate: float        # per eligible word
    light_only: bool         # only the light replacements (pra, tá, tô)
    typo: float              # per word of 4 letters or more
    laugh: float             # per comment, when the mood allows it
    elongate: float          # per comment: "demaiiis"
    opener: float
    closer: float


STYLE_PARAMS = {
    "formal": StyleParams(False, 1.0, 0.0, 0.0, True, 0.0, 0.0, 0.0, 0.35, 0.45),
    "standard": StyleParams(False, 1.0, 0.03, 0.2, True, 0.006, 0.02, 0.0, 0.12, 0.1),
    "casual": StyleParams(True, 0.35, 0.35, 0.45, False, 0.015, 0.1, 0.07, 0.15, 0.12),
    "informal": StyleParams(True, 0.1, 0.85, 0.8, False, 0.03, 0.22, 0.2, 0.12, 0.1),
}


def strip_accents(word: str) -> str:
    decomposed = unicodedata.normalize("NFD", word)
    return unicodedata.normalize("NFC", "".join(c for c in decomposed if unicodedata.category(c) != "Mn"))


def make_typo(word: str, rng: random.Random) -> str:
    """Swap two neighbour letters, drop one or double one (never the first letter)."""
    kind = rng.choices(("swap", "drop", "double"), weights=(0.45, 0.4, 0.15))[0]
    i = rng.randrange(1, len(word) - 1)
    if kind == "swap":
        return word[:i] + word[i + 1] + word[i] + word[i + 2:]
    if kind == "drop":
        return word[:i] + word[i + 1:]
    return word[:i] + word[i] + word[i:]


class Noiser:
    """Applies one writing style to the text pieces of a comment and records what it did."""

    def __init__(self, rng: random.Random, bank: PhraseBank, style: str) -> None:
        self.rng = rng
        self.style = style
        self.params = STYLE_PARAMS[style]
        self.typos = 0
        single = {k: v for k, v in bank.abbreviations.items() if " " not in k}
        self._single = single
        self._light = set(bank.light_abbreviations)
        self._multi = [(re.compile(rf"\b{re.escape(k)}\b"), v)
                       for k, v in bank.abbreviations.items()
                       if " " in k and (not self.params.light_only or k in self._light)]

    def apply(self, text: str, *, typos: bool = True, keep_case: bool = False) -> str:
        if self.params.lowercase and not keep_case:
            text = text.lower()
        text = self._multiword(text)
        text = "".join(self._word(tok, typos) if tok.isalpha() else tok for tok in TOKEN_RE.findall(text))
        return CONTRACTION_RE.sub(lambda m: ("pro" if m.group(1) == "o" else "pra") + m.group(2), text)

    def _multiword(self, text: str) -> str:
        for pattern, options in self._multi:
            text = pattern.sub(
                lambda m, opts=options: self.rng.choice(opts)
                if self.rng.random() < self.params.abbreviate else m.group(0),
                text,
            )
        return text

    def _word(self, word: str, typos: bool) -> str:
        p = self.params
        options = self._single.get(word) if word.islower() else None
        if options and (not p.light_only or word in self._light) and self.rng.random() < p.abbreviate:
            return self.rng.choice(options)
        if p.accent_drop and self.rng.random() < p.accent_drop:
            word = strip_accents(word)
        if typos and len(word) >= 4 and p.typo and self.rng.random() < p.typo:
            changed = make_typo(word, self.rng)
            if changed != word:
                self.typos += 1
                word = changed
        return word

    def elongate(self, text: str) -> str | None:
        """Repeat the last letter of the last word ("demaisss"), or None when there is no word."""
        matches = list(LAST_WORD_RE.finditer(text))
        if not matches:
            return None
        m = matches[-1]
        extra = m.group(0)[-1] * self.rng.randint(2, 4)
        return text[:m.end()] + extra + text[m.end():]


def capitalize_sentences(text: str, at_start: bool) -> tuple[str, bool]:
    """Upper-case the first letter of each sentence. Returns the text and whether the next
    piece starts a sentence."""
    chars = list(text)
    start = at_start
    for i, ch in enumerate(chars):
        if ch.isalpha():
            if start:
                chars[i] = ch.upper()
            start = False
        elif ch.isdigit():
            start = False
        elif ch in ".!?":
            following = text[i + 1] if i + 1 < len(text) else " "
            before = text[: i + 1].lower()
            start = following.isspace() and not any(before.endswith(a) for a in DOT_ABBREVIATIONS)
    return "".join(chars), start
