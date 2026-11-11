"""First-name gazetteer with cue words, built on the IBGE Censo 2022 name rankings.

A token that is an IBGE first name is masked when it is capitalized ("Paulo", anywhere in the
sentence) or follows a cue word ("o joao", "gestora ana"). Names that are also common words
(the stoplist: rosa, clara, luz...) need both a cue and a capital letter. A masked name extends
over the surnames that follow it ("Maria das Graças Souza", "joao da silva").
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import PiiSpan
from .lexicon import Lexicon, lexicon, normalize

TOKEN_RE = re.compile(r"[^\W\d_]+")
MENTION_RE = re.compile(r"(?<![\w@.])@[^\W_][\w.]*[^\W_.]|(?<![\w@.])@[^\W_]")
_OPENERS = "\"'\u201c\u201d\u2018\u2019\u00ab\u00bb([{*-\u2013\u2014"
_TERMINATORS = ".!?\u2026"


@dataclass(frozen=True)
class Token:
    start: int
    end: int
    text: str
    norm: str


def tokenize(text: str) -> list[Token]:
    return [Token(m.start(), m.end(), m.group(), normalize(m.group())) for m in TOKEN_RE.finditer(text)]


def is_title(word: str) -> bool:
    """Capitalized but not all caps: "João" yes, "JOÃO" and "joão" no."""
    return word[:1].isupper() and not (len(word) > 1 and word.isupper())


def is_sentence_start(text: str, start: int) -> bool:
    """True when only spaces, quotes or brackets separate `start` from a sentence boundary."""
    i = start - 1
    while i >= 0 and (text[i].isspace() or text[i] in _OPENERS):
        if text[i] == "\n":
            return True
        i -= 1
    return i < 0 or text[i] in _TERMINATORS


def _gap(text: str, left: Token, right: Token) -> str:
    return text[left.end:right.start]


def _has_cue(tokens: list[Token], i: int, text: str, lex: Lexicon) -> bool:
    if i == 0:
        return False
    prev = tokens[i - 1]
    gap = _gap(text, prev, tokens[i])
    if prev.norm not in lex.name_cues:
        return False
    if gap.strip() == "" and gap and "\n" not in gap:
        return True
    return prev.norm in lex.abbreviation_cues and re.fullmatch(r"\.\s+", gap) is not None


def _starts_name(tokens: list[Token], i: int, text: str, lex: Lexicon) -> bool:
    tok = tokens[i]
    if len(tok.norm) < 2 or lex.is_allowed(tok.text) or not lex.is_first_name(tok.norm):
        return False
    title = is_title(tok.text)
    cue = _has_cue(tokens, i, text, lex)
    if tok.norm in lex.stoplist:
        return cue and title
    return cue or title


def _continues_name(tok: Token, after_connector: bool, lex: Lexicon) -> bool:
    """Whether `tok` is a surname (or second name) that belongs to the name before it."""
    if len(tok.norm) < 2:
        return False
    if lex.is_allowed(tok.text):
        # A capitalized surname right after a name stays in it even when the word is on the
        # allowlist ("Carla Sales", "Paulo Vale"); "da Frota" is still the team.
        return not after_connector and is_title(tok.text) and tok.norm in lex.surnames
    if is_title(tok.text):
        if after_connector:  # "da Silva" yes, "da Logística" no
            return tok.norm in lex.surnames or tok.norm in lex.first_names
        return True
    return lex.is_known_name(tok.norm)  # lowercase or all caps: only names that are not words


def _single_line_gap(gap: str) -> bool:
    return gap == "-" or (gap != "" and gap.strip() == "" and "\n" not in gap)


def _extend(tokens: list[Token], i: int, text: str, lex: Lexicon) -> int:
    """Index of the last token of the full name that starts at token i."""
    j = i
    while j + 1 < len(tokens):
        nxt = tokens[j + 1]
        if not _single_line_gap(_gap(text, tokens[j], nxt)):
            break
        if _continues_name(nxt, after_connector=False, lex=lex):
            j += 1
            continue
        if (
            nxt.norm in lex.connectors
            and j + 2 < len(tokens)
            and _single_line_gap(_gap(text, nxt, tokens[j + 2]))
            and _continues_name(tokens[j + 2], after_connector=True, lex=lex)
        ):
            j += 2
            continue
        break
    return j


def find_mentions(text: str, lex: Lexicon | None = None) -> list[PiiSpan]:
    """@handles, which point to a person: "@joao.silva"."""
    lex = lex or lexicon()
    spans = []
    for m in MENTION_RE.finditer(text):
        if not lex.is_allowed(normalize(m.group()[1:])):
            spans.append(PiiSpan(m.start(), m.end(), "PESSOA", "context"))
    return spans


def find_names(text: str, lex: Lexicon | None = None) -> list[PiiSpan]:
    """Person name spans found by the gazetteer, plus @mentions. Sorted, non-overlapping."""
    lex = lex or lexicon()
    spans = find_mentions(text, lex)
    covered = [(s.start, s.end) for s in spans]
    tokens = tokenize(text)
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        inside_mention = any(a <= tok.start < b for a, b in covered)
        if inside_mention or not _starts_name(tokens, i, text, lex):
            i += 1
            continue
        j = _extend(tokens, i, text, lex)
        spans.append(PiiSpan(tok.start, tokens[j].end, "PESSOA", "gazetteer"))
        i = j + 1
    return sorted(spans, key=lambda s: s.start)
