"""Place gazetteer: official IBGE municipalities, districts and subdistricts masked as LOCAL.

Rules (the lists and classes come from lexicon.py):
* A place name of several words must match fully ("São José dos Campos", "Embu das Artes"),
  without regard to accents; the longest name that starts at a word wins.
* Capitalized names match anywhere: every word but connectors ("da", "de", "dos"...) starts
  with a capital letter. A one-word name that is also a common word ("Saúde", "Contagem")
  matches only right after a cue, and one used about as often in both cases ("Perus") only
  inside a sentence. A name made only of common words ("Campo Grande") does not match at the
  start of a sentence either.
* After a place cue ("moro em", "sou de", "filial de", "em"...), a name also matches in
  lowercase or all caps ("moro em pirituba"), unless it is made only of common words
  ("vim de carro"), or is one word that is also a first name or a surname ("em marco").
* Stoplist names (config/anonymizer.yaml: "Natal", "Bom Jesus", "Itaú"...) match only
  capitalized right after a cue.

`resolve` then settles overlaps with person spans: a longer name holds the place ("Ana
Carolina"), a cue before the place makes it a place ("moro em Marília"), a longer place holds
the name ("São José dos Campos" over "José dos Campos"), and otherwise a known first name or
surname stays a person ("a Marília do RH"). A place made of known names right after a name
cue is a person too, even when no person span covers it ("falei com o Barbosa"). In the
Worker, spans of the context rule with no known name in them give way to the official list
("liguei pra Sorocaba"); here the NER spans are joined afterwards (pipeline.py), and a place
with no cue that `reads_as_name` gives way to a NER person ("Mendes me ajudou").
"""
from __future__ import annotations

from dataclasses import dataclass

from . import PiiSpan
from .gazetteer import Token, has_name_cue, is_sentence_start, is_title, tokenize
from .lexicon import Lexicon, lexicon

# Words inside place names that need no capital letter: "Embu das Artes", "Pau D'Arco".
CONNECTORS = frozenset({"a", "d", "da", "das", "de", "do", "dos", "e"})
_APOSTROPHES = ("'", "\u2019")
_MAX_CUE_WORDS = 2


@dataclass(frozen=True)
class PlaceSpan:
    start: int
    end: int
    cued: bool         # a place cue comes right before it: "moro em"
    person_cued: bool  # a name cue comes right before it and all its words are names: "o Barbosa"


def _space_gap(gap: str) -> bool:
    return gap != "" and gap.strip() == "" and "\n" not in gap


def _place_gap(gap: str) -> bool:
    """Separators allowed inside a place name: spaces, a hyphen or an apostrophe."""
    return _space_gap(gap) or gap == "-" or gap in _APOSTROPHES


def _has_cue(tokens: list[Token], i: int, text: str, lex: Lexicon) -> bool:
    for size in range(_MAX_CUE_WORDS, 0, -1):
        if i < size:
            continue
        words = tokens[i - size:i + 1]
        if all(_space_gap(text[a.end:b.start]) for a, b in zip(words, words[1:])) and (
                " ".join(t.norm for t in words[:-1]) in lex.place_cues):
            return True
    return False


def _phrase(tokens: list[Token], i: int, size: int, text: str) -> str | None:
    """Normalized phrase of tokens i .. i+size-1, or None when they are not one name."""
    words = tokens[i:i + size]
    if len(words) < size or not all(_place_gap(text[a.end:b.start]) for a, b in zip(words, words[1:])):
        return None
    return " ".join(t.norm for t in words)


def _significant(words: list[Token]) -> list[Token]:
    return [t for t in words if t.norm not in CONNECTORS] or words


def _all_names(words: list[Token], lex: Lexicon) -> bool:
    return all(lex.is_known_name(t.norm) for t in _significant(words))


def reads_as_name(text: str, lex: Lexicon | None = None) -> bool:
    """Whether a place name is one word that is also a known first name or surname ("Mendes",
    "Leme", "Guimarães"; not "Jundiaí"). Longer names read as places even when every word is
    also a name: "Vila Mariana" ("vila" and "mariana" are both IBGE surnames)."""
    words = _significant(tokenize(text))
    return len(words) == 1 and _all_names(words, lex or lexicon())


def _accepts(words: list[Token], key: str, cued: bool, text: str, lex: Lexicon) -> bool:
    if lex.is_allowed(text[words[0].start:words[-1].end]):
        return False
    significant = _significant(words)
    capitalized = all(is_title(t.text) for t in significant)
    if key in lex.place_stoplist:
        return cued and capitalized
    classes = [lex.place_class(t.norm) for t in significant]
    all_common = "proper" not in classes
    if capitalized:
        if cued:
            return True
        if len(classes) == 1 and classes[0] != "mixed":
            return classes[0] == "proper"
        return not all_common or not is_sentence_start(text, words[0].start)
    if not cued or all_common:
        return False
    return len(words) > 1 or not (lex.is_first_name(key) or key in lex.surnames)


def find_places(text: str, lex: Lexicon | None = None) -> list[PlaceSpan]:
    """Place names in `text`, in order and without overlaps."""
    lex = lex or lexicon()
    tokens = tokenize(text)
    spans: list[PlaceSpan] = []
    i = 0
    while i < len(tokens):
        size = 0
        for n in lex.place_index.get(tokens[i].norm, ()):
            key = _phrase(tokens, i, n, text)
            if key is None or key not in lex.places:
                continue
            cued = _has_cue(tokens, i, text, lex)
            words = tokens[i:i + n]
            if _accepts(words, key, cued, text, lex):
                person_cued = has_name_cue(tokens, i, text, lex) and _all_names(words, lex)
                spans.append(PlaceSpan(words[0].start, words[-1].end, cued, person_cued))
                size = n
                break
        i += size or 1
    return spans


def _has_known_name(span: PiiSpan, text: str, lex: Lexicon) -> bool:
    return any(lex.is_known_name(t.norm) for t in tokenize(text[span.start:span.end]))


def _place_wins(place: PlaceSpan, people: list[PiiSpan], text: str, lex: Lexicon) -> bool | None:
    """True: the place wins; False: the person spans win; None: keep the person spans as they are."""
    if not people:
        return not place.person_cued or place.cued
    size = place.end - place.start
    if any(p.start <= place.start and place.end <= p.end and p.end - p.start > size for p in people):
        return None  # a longer name holds the place: "Ana Carolina", "Paulo Souza"
    if place.cued:
        return True
    if all(place.start <= p.start and p.end <= place.end and p.end - p.start < size for p in people):
        return True  # the place holds the name: "São José dos Campos" over "José dos Campos"
    return all(p.source == "context" and not _has_known_name(p, text, lex) for p in people)


def resolve(names: list[PiiSpan], places: list[PlaceSpan], text: str,
            lex: Lexicon | None = None) -> list[PiiSpan]:
    """Name spans plus place spans, overlaps settled as described above. Sorted, non-overlapping."""
    lex = lex or lexicon()
    spans = list(names)
    for place in places:
        hits = [s for s in spans if s.start < place.end and place.start < s.end]
        people = [s for s in hits if s.type == "PESSOA"]
        wins = _place_wins(place, people, text, lex)
        if wins is None:
            continue
        start = min([place.start] + [s.start for s in hits])
        end = max([place.end] + [s.end for s in hits])
        source = "gazetteer" if wins or not people else people[0].source
        merged = PiiSpan(start, end, "LOCAL" if wins else "PESSOA", source)
        spans = [s for s in spans if s not in hits] + [merged]
    return sorted(spans, key=lambda s: s.start)

