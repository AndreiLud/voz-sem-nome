"""Public excerpts of anonymized comments: profanity masked, whitespace collapsed, length capped."""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from pathlib import Path

from enps import config

PROFANITY_FILE = config.CONFIG_DIR / "profanity.txt"
MASK = "[ofensivo]"
ELLIPSIS = "…"


def _fold_char(ch: str) -> str:
    base = unicodedata.normalize("NFD", ch)[0]
    lower = base.lower()
    return lower if len(lower) == 1 else base


def fold(text: str) -> str:
    """Lowercase without accents, exactly one output character per input character."""
    return "".join(_fold_char(ch) for ch in text)


def load_words(path: str | Path = PROFANITY_FILE) -> list[str]:
    with open(path, encoding="utf-8") as fh:
        return [line.strip() for line in fh if line.strip() and not line.startswith("#")]


@lru_cache(maxsize=4)
def _pattern(path: str) -> re.Pattern[str] | None:
    words = load_words(path)
    if not words:
        return None
    alternatives = {re.escape(fold(word)).replace(r"\ ", r"\s+") for word in words}
    ordered = sorted(alternatives, key=len, reverse=True)  # "foda-se" before "foda"
    return re.compile(r"(?<!\w)(?:" + "|".join(ordered) + r")(?!\w)")


def mask_profanity(text: str, path: str | Path = PROFANITY_FILE) -> str:
    """Replace listed words (whole words, accent and case insensitive) with [ofensivo]."""
    pattern = _pattern(str(path))
    if pattern is None or not text:
        return text
    parts: list[str] = []
    last = 0
    for match in pattern.finditer(fold(text)):
        parts.extend((text[last:match.start()], MASK))
        last = match.end()
    parts.append(text[last:])
    return "".join(parts)


def truncate(text: str, limit: int) -> str:
    """Cut at a word boundary when possible and end with an ellipsis."""
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    space = cut.rfind(" ")
    if space >= limit // 2:
        cut = cut[:space]
    return cut.rstrip(" ,.;:!?") + ELLIPSIS


def public_excerpt(text: str, limit: int) -> str:
    return truncate(mask_profanity(" ".join(text.split())), limit)
