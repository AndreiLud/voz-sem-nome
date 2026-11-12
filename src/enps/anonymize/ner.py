"""spaCy pt_core_news_md entities (PER, LOC) with filters tuned for survey comments.

The model is loaded once per process. Measured behaviour that the filters handle:
it tags "RH" as ORG (ignored) and "Talvana Distribuição" or area names as LOC (allowlist),
tags capitalized words at the start of a sentence as people ("Moro longe...", "Valeu"),
and stretches person names over the words around them ("Clara do Financeiro", "Seu Zé").
"""
from __future__ import annotations

from collections.abc import Iterable
from functools import lru_cache
from typing import TYPE_CHECKING

from . import PiiSpan
from .gazetteer import is_sentence_start, is_title
from .lexicon import Lexicon, lexicon, normalize, normalize_phrase

if TYPE_CHECKING:
    from spacy.language import Language
    from spacy.tokens import Doc, Span, Token

MODEL_NAME = "pt_core_news_md"
EXCLUDED_COMPONENTS = ("parser", "lemmatizer", "morphologizer", "attribute_ruler", "senter")
LABELS = {"PER": "PESSOA", "LOC": "LOCAL"}


@lru_cache(maxsize=1)
def load_model() -> Language:
    import spacy

    return spacy.load(MODEL_NAME, exclude=list(EXCLUDED_COMPONENTS))


def parse(text: str) -> Doc:
    return load_model()(text)


def parse_many(texts: Iterable[str], batch_size: int = 64) -> Iterable[Doc]:
    return load_model().pipe(texts, batch_size=batch_size)


def _drop_at_edge(token: Token, lex: Lexicon) -> bool:
    """Tokens that can be cut from the start or end of an entity."""
    text = token.text
    norm = normalize(text).rstrip(".")
    if not any(ch.isalnum() for ch in text):
        return True
    if norm in lex.connectors or norm in lex.name_cues or lex.is_allowed(text.rstrip(".")):
        return True
    return text == text.lower() and not lex.is_known_name(norm)


def _trim(ent: Span, lex: Lexicon) -> list[Token]:
    tokens = list(ent)
    while tokens and _drop_at_edge(tokens[0], lex):
        tokens.pop(0)
    while tokens and _drop_at_edge(tokens[-1], lex):
        tokens.pop()
    return tokens


def _shouting_without_names(tokens: list[Token], lex: Lexicon) -> bool:
    """All-caps entities ("NA FRENTE", acronyms like URA) are noise unless they hold a known name."""
    words = [t for t in tokens if any(ch.isalpha() for ch in t.text)]
    return all(t.text.isupper() for t in words) and not any(
        lex.is_known_name(normalize(t.text)) for t in words)


def _keep_single(token: Token, label: str, text: str, lex: Lexicon) -> bool:
    word = token.text
    norm = normalize(word)
    at_start = is_sentence_start(text, token.idx)
    if norm in lex.stoplist:
        # "Rosa me ajuda" yes; "Luz do galpão" and "Moro longe" (a surname, not a first name) no
        return not at_start or (label == "PER" and norm in lex.first_names)
    if at_start:
        return lex.is_known_name(norm)  # "Valeu", "Adoro" are not names
    return True


def _span_type(tokens: list[Token], label: str, lex: Lexicon) -> str:
    if label == "LOC" and len(tokens) == 1:
        norm = normalize(tokens[0].text)
        if lex.is_first_name(norm) and norm not in lex.stoplist and is_title(tokens[0].text):
            return "PESSOA"  # the model often calls a lone first name a place
    return LABELS[label]


def entities(doc: Doc, lex: Lexicon | None = None) -> list[PiiSpan]:
    """Filtered PER and LOC entities of a parsed comment, in original text coordinates."""
    lex = lex or lexicon()
    spans: list[PiiSpan] = []
    for ent in doc.ents:
        if ent.label_ not in LABELS:
            continue
        tokens = _trim(ent, lex)
        if not tokens:
            continue
        start, end = tokens[0].idx, tokens[-1].idx + len(tokens[-1].text)
        if lex.is_allowed(doc.text[start:end]):
            continue
        if ent.label_ == "LOC" and normalize_phrase(doc.text[start:end]) in lex.place_stoplist:
            continue  # "Feliz Natal", "Boa Sorte": the place gazetteer decides (places.py)
        if _shouting_without_names(tokens, lex):
            continue
        if len(tokens) == 1 and not _keep_single(tokens[0], ent.label_, doc.text, lex):
            continue
        spans.append(PiiSpan(start, end, _span_type(tokens, ent.label_, lex), "ner"))
    return spans
