"""Layered anonymizer: regex detectors, spaCy NER, name and place gazetteers, then merge and replace.

Layers and conflict rules:
1. Regex detectors (patterns.py) find documents, contacts, plates and street addresses.
2. spaCy NER (ner.py) finds people (PER) and places (LOC); ORG and MISC are ignored.
3. The name gazetteer (gazetteer.py) finds first names, including lowercase ones after cue words.
4. The place gazetteer (places.py) finds official IBGE place names. Places inside a regex span
   are dropped (the address already covers them); overlaps with the gazetteer names follow
   places.resolve ("moro em Marília" is a place, "a Marília do RH" a person).
5. NER and gazetteer spans that overlap are joined into one span. The joined span is a place
   when it holds a place of the official lists, or when a longer NER place contains the names
   ("Jardim Ângela"); otherwise it is a person. Two exceptions, settled before the join: a
   one-word place with no place cue that is also a first name or surname is a person when the
   model says so ("Mendes me ajudou", "o Uelinton Leme"), and a NER span that adds no word to the
   gazetteer spans it covers leaves them as they are ("a Natália de Jundiaí").
6. Regex spans win over everything else: a name span is cut where it overlaps a regex span.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

from . import AnonymizationResult, PiiSpan
from . import gazetteer, ner, patterns, places
from .lexicon import Lexicon, lexicon, normalize

ANONYMIZER_VERSION = "1.1.0"


def _overlapping(span: PiiSpan, spans: list[PiiSpan]) -> list[PiiSpan]:
    return [s for s in spans if patterns.overlaps(span, s)]


def join_name_spans(gazetteer_spans: list[PiiSpan], ner_spans: list[PiiSpan]) -> list[PiiSpan]:
    """Union of gazetteer (names and places) and NER spans; overlapping spans become one."""
    spans = list(gazetteer_spans)
    for ent in ner_spans:
        hits = _overlapping(ent, spans)
        if not hits:
            spans.append(ent)
            continue
        hit_start, hit_end = min(s.start for s in hits), max(s.end for s in hits)
        start, end = min(ent.start, hit_start), max(ent.end, hit_end)
        if any(s.type == "LOCAL" for s in hits):  # an official place beats the model
            span_type, source = "LOCAL", "gazetteer"
        elif ent.type == "LOCAL" and (ent.start < hit_start or ent.end > hit_end):
            span_type, source = "LOCAL", "ner"  # a longer NER place contains the name
        else:
            span_type = "PESSOA"
            source = next((s.source for s in hits if s.source != "ner"), "ner")
        spans = [s for s in spans if s not in hits]
        spans.append(PiiSpan(start, end, span_type, source))
    return sorted(spans, key=lambda s: s.start)


def surnames_to_people(spans: list[PiiSpan], ner_spans: list[PiiSpan],
                       found: list[places.PlaceSpan], text: str, lex: Lexicon) -> list[PiiSpan]:
    """A LOCAL span that reads as a name (places.reads_as_name) and has no place cue becomes
    PESSOA under a NER person: "Mendes me ajudou", "o Uelinton Leme" (NER "Uelinton Leme")."""
    people = [e for e in ner_spans if e.type == "PESSOA"]
    cued = [PiiSpan(p.start, p.end, "LOCAL", "gazetteer") for p in found if p.cued]
    out = []
    for span in spans:
        if (span.type == "LOCAL" and _overlapping(span, people) and not _overlapping(span, cued)
                and places.reads_as_name(text[span.start:span.end], lex)):
            span = PiiSpan(span.start, span.end, "PESSOA", span.source)
        out.append(span)
    return out


def _adds_words(ent: PiiSpan, spans: list[PiiSpan], text: str, lex: Lexicon) -> bool:
    """Whether `ent` covers a word, other than a connector, that none of `spans` covers."""
    for tok in gazetteer.tokenize(text[ent.start:ent.end]):
        start = ent.start + tok.start
        if tok.norm not in lex.connectors and not any(s.start <= start < s.end for s in spans):
            return True
    return False


def ner_spans_to_join(spans: list[PiiSpan], ner_spans: list[PiiSpan], text: str,
                      lex: Lexicon) -> list[PiiSpan]:
    """NER spans worth joining: one that touches a place and adds no word to the gazetteer spans
    is dropped, so "a Natália de Jundiaí" stays a name and a place, not one place."""
    kept = []
    for ent in ner_spans:
        hits = _overlapping(ent, spans)
        if any(s.type == "LOCAL" for s in hits) and not _adds_words(ent, hits, text, lex):
            continue
        kept.append(ent)
    return kept


def _strip(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and not text[start].isalnum():
        start += 1
    while end > start and not text[end - 1].isalnum():
        end -= 1
    return start, end


def _worth_keeping(piece: str, lex: Lexicon) -> bool:
    """A leftover piece of a cut name span is kept when it still looks like a name."""
    words = [w for w in piece.split() if any(ch.isalpha() for ch in w)]
    return any(gazetteer.is_title(w) or lex.is_known_name(normalize(w)) for w in words)


def _subtract(span: PiiSpan, blockers: list[PiiSpan], text: str, lex: Lexicon) -> list[PiiSpan]:
    pieces = [(span.start, span.end)]
    for b in blockers:
        next_pieces = []
        for a, z in pieces:
            if b.end <= a or b.start >= z:
                next_pieces.append((a, z))
                continue
            next_pieces.extend(p for p in ((a, b.start), (b.end, z)) if p[1] > p[0])
        pieces = next_pieces
    kept = []
    for a, z in pieces:
        a, z = _strip(text, a, z)
        if z - a >= 2 and _worth_keeping(text[a:z], lex):
            kept.append(PiiSpan(a, z, span.type, span.source))
    return kept


def merge_spans(
    text: str,
    regex_spans: list[PiiSpan],
    name_spans: list[PiiSpan],
    lex: Lexicon | None = None,
) -> list[PiiSpan]:
    """Regex spans as they are, plus name spans cut around them. Sorted, non-overlapping."""
    lex = lex or lexicon()
    merged = list(regex_spans)
    for span in name_spans:
        blockers = _overlapping(span, regex_spans)
        merged.extend(_subtract(span, blockers, text, lex) if blockers else [span])
    return sorted(merged, key=lambda s: s.start)


def replace_spans(text: str, spans: list[PiiSpan]) -> str:
    parts, cursor = [], 0
    for span in spans:
        parts.append(text[cursor:span.start])
        parts.append(f"[{span.type}]")
        cursor = span.end
    parts.append(text[cursor:])
    return "".join(parts)


def _result(text: str, spans: list[PiiSpan]) -> AnonymizationResult:
    counts = dict(sorted(Counter(s.type for s in spans).items()))
    return AnonymizationResult(
        text=replace_spans(text, spans),
        spans=tuple(spans),
        counts=counts,
        version=ANONYMIZER_VERSION,
    )


def _anonymize_parsed(text: str, doc) -> AnonymizationResult:
    lex = lexicon()
    regex_spans = patterns.find_all(text)
    found = [p for p in places.find_places(text, lex)
             if not any(p.start < r.end and r.start < p.end for r in regex_spans)]
    names = places.resolve(gazetteer.find_names(text, lex), found, text, lex)
    entities = ner.entities(doc, lex)
    names = surnames_to_people(names, entities, found, text, lex)
    names = join_name_spans(names, ner_spans_to_join(names, entities, text, lex))
    return _result(text, merge_spans(text, regex_spans, names, lex))


def _is_blank(text: str | None) -> bool:
    return not text or not text.strip()


def anonymize(text: str) -> AnonymizationResult:
    """Replace personal data in `text` with typed placeholders such as [PESSOA]."""
    if _is_blank(text):
        return AnonymizationResult(text=text or "", version=ANONYMIZER_VERSION)
    return _anonymize_parsed(text, ner.parse(text))


def anonymize_many(texts: Iterable[str], batch_size: int = 64) -> list[AnonymizationResult]:
    """Batch version of `anonymize`; runs the NER model with nlp.pipe (about 3x faster)."""
    texts = [t or "" for t in texts]
    filled = [t for t in texts if not _is_blank(t)]
    parsed = iter(ner.parse_many(filled, batch_size=batch_size))
    results = []
    for text in texts:
        if _is_blank(text):
            results.append(AnonymizationResult(text=text, version=ANONYMIZER_VERSION))
        else:
            results.append(_anonymize_parsed(text, next(parsed)))
    return results
