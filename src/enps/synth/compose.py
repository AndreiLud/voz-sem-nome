"""Compose one comment from phrase bank fragments, with exact personal-data offsets and labels.

A comment is built as a list of pieces. Text pieces get the writing style (case, accents,
abbreviations, typos); pieces that hold a planted value are copied verbatim. Offsets are
computed while joining, so `comment[start:end]` is always the planted value.
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass, field

from enps import config

from .bank import SLOT_PII_TYPE, SLOT_RE, Fragment, PhraseBank, fold
from .noise import STYLE_PARAMS, Noiser, capitalize_sentences
from .pii import FakeData
from .population import Respondent
from .story import (FRAGMENT_COUNTS, OUT_SHARE, POLARITY_BY_SCORE, STYLE_WEIGHTS, STYLES,
                    THEME_WEIGHTS, score_group)

POLARITIES = ("neg", "neu", "pos")
TRAIT_ORDER = ("empty", "one_word", "short", "formal", "rant", "irony", "mixed", "slang",
               "abbreviations", "typos", "repeated_letters", "laughter", "pii", "pii_decoy",
               "decoy", "self_harm")
SHOUT_RE = re.compile(r"\b[^\W\d_]{4,}\b")


@dataclass
class Piece:
    text: str
    pii: str | None = None      # PII type of a planted value (copied verbatim)
    typos: bool = True
    keep_case: bool = False


@dataclass
class Composed:
    """A finished comment with its intended labels."""

    text: str
    spans: list[dict[str, object]] = field(default_factory=list)
    themes: list[str] = field(default_factory=list)
    sentiment: str = ""
    critical_types: list[str] = field(default_factory=list)
    subtle: bool = False
    traits: list[str] = field(default_factory=list)


def overall_sentiment(fragments: list[Fragment]) -> str:
    """Weighted sum of polarities: pos counts +w, neg -w, neu 0. Irony fragments are neg."""
    total = sum(f.weight if f.polarity == "pos" else -f.weight if f.polarity == "neg" else 0
                for f in fragments)
    return "pos" if total > 0 else "neg" if total < 0 else "neu"


def _is_shout(text: str) -> bool:
    return any(w.isupper() for w in SHOUT_RE.findall(text))


class Composer:
    """Builds comments for respondents. One instance per dataset (it tracks fragment reuse)."""

    def __init__(self, bank: PhraseBank, rng: random.Random, fake: FakeData) -> None:
        self.bank = bank
        self.rng = rng
        self.fake = fake
        self.usage: dict[str, int] = {}
        self.theme_order = config.theme_codes()
        self.critical_order = config.critical_codes()
        self._abbreviations = bank.abbreviation_outputs()
        self._slang = [re.compile(rf"(?<!\w){re.escape(fold(s))}(?!\w)") for s in bank.slang]
        self._laughter = re.compile(r"\b(k{3,}|(rs){2,}|(ha){2,})\b")
        word_names = "|".join(re.escape(fold(n)) for n in bank.people.get("word_names", ()))
        self._word_names = re.compile(rf"(?<!\w)({word_names})(?!\w)") if word_names else None

    # ----- choices -------------------------------------------------------------------------

    def choose_style(self, area: str, special: Fragment | None) -> str:
        style = self.rng.choices(STYLES, weights=STYLE_WEIGHTS[area])[0]
        register = special.register if special else "n"
        if register == "f":
            return "formal"
        if register == "c" and style in ("formal", "standard"):
            return "casual"
        return style

    def _polarity(self, score: int) -> str:
        return self.rng.choices(POLARITIES, weights=POLARITY_BY_SCORE[score])[0]

    def _register_ok(self, frag: Fragment, style: str) -> bool:
        if style == "formal" and "irony" in frag.tags:
            return False
        if frag.register == "f":
            return style == "formal"
        if frag.register == "c":
            return style in ("casual", "informal")
        return True

    def _pick(self, theme: str, polarity: str, resp: Respondent, style: str,
              used: set[str], serious: bool = False) -> Fragment | None:
        """A fragment for a theme, least used first. `serious` (next to a critical case)
        rules out irony."""
        order = [polarity] + [p for p in POLARITIES if p != polarity]
        for pol in order:
            for strict in (True, False):
                cands = [f for f in self.bank.pool(theme, pol)
                         if f.key not in used and f.fits(resp.area, resp.tenure, resp.gender)
                         and (not strict or self._register_ok(f, style))
                         and not (serious and "irony" in f.tags)]
                if cands:
                    weights = [(2.0 if f.areas else 1.0) / (1 + self.usage.get(f.key, 0)) ** 2
                               for f in cands]
                    return self.rng.choices(cands, weights=weights)[0]
        return None

    def _themes(self, area: str, k: int, exclude: set[str]) -> list[str]:
        weights = {t: w for t, w in THEME_WEIGHTS[area].items() if t not in exclude}
        chosen: list[str] = []
        while weights and len(chosen) < k:
            keys = list(weights)
            theme = self.rng.choices(keys, weights=[weights[t] for t in keys])[0]
            chosen.append(theme)
            del weights[theme]
        return chosen

    def _extras(self, resp: Respondent, style: str, k: int, present: list[Fragment]) -> list[Fragment]:
        exclude = {t for f in present for t in f.themes}
        used = {f.key for f in present}
        serious = any(f.critical for f in present)
        # Next to a critical case the rest of the comment reads like a low score.
        score = min(resp.score, 5) if serious else resp.score
        extras = []
        for theme in self._themes(resp.area, k, exclude):
            frag = self._pick(theme, self._polarity(score), resp, style, used, serious)
            if frag:
                extras.append(frag)
                used.add(frag.key)
        return extras

    def _count(self, weights: tuple[float, ...]) -> int:
        return self.rng.choices(range(len(weights)), weights=weights)[0]

    def fragments_for(self, resp: Respondent, style: str) -> list[Fragment]:
        """The fragments of one comment, special template included, in reading order."""
        group = score_group(resp.score)
        special = resp.special
        if special is None:
            if self.rng.random() < OUT_SHARE[group]:
                first = self._pick("out", self._polarity(resp.score), resp, style, set())
                return [first] if first else []
            n = 1 + self._count(FRAGMENT_COUNTS[group])
            return self._extras(resp, style, n, [])
        if "out" in special.themes:
            return [special]
        extra_weights = {
            "rant": (0.7, 0.3), "critical": (0.35, 0.45, 0.2), "near_miss": (0.45, 0.4, 0.15),
            "decoy": (0.45, 0.4, 0.15), "pii": (0.35, 0.45, 0.2),
        }[resp.kind]
        k = self._count(extra_weights)
        if not special.themes:
            k = max(k, 1)
        extras = self._extras(resp, style, k, [special])
        if special.position == "start":
            return [special, *extras]
        if special.position == "end":
            return [*extras, special]
        if extras and resp.kind in ("critical", "pii") and self.rng.random() < 0.4:
            return [extras[0], special, *extras[1:]]
        return [special, *extras]

    # ----- assembly ------------------------------------------------------------------------

    def _render(self, frag: Fragment, style: str) -> list[Piece]:
        protect = bool(frag.critical or frag.slots())
        keep_case = _is_shout(frag.text)
        self.fake.forget_person()
        pieces: list[Piece] = []
        pos = 0
        for m in SLOT_RE.finditer(frag.text):
            if m.start() > pos:
                pieces.append(Piece(frag.text[pos:m.start()], typos=not protect, keep_case=keep_case))
            slot, variant = m.group(1), m.group(2)
            pieces.append(Piece(self.fake.value(slot, variant, style), pii=SLOT_PII_TYPE[slot]))
            pos = m.end()
        if pos < len(frag.text):
            pieces.append(Piece(frag.text[pos:], typos=not protect, keep_case=keep_case))
        return pieces

    def _connector(self, prev: Fragment, nxt: Fragment, style: str) -> str:
        if prev.text[-1] in "?!.":
            return " "
        contrast = {prev.polarity, nxt.polarity} == {"pos", "neg"}
        if style == "formal":
            return self.rng.choice([". no entanto, ", ". por outro lado, ", ", porém, "]) if contrast else ". "
        if contrast:
            return self.rng.choice([", mas ", " mas ", ". mas ", ", só que "])
        options = {
            "standard": [". ", ". ", ". ", ", ", "; "],
            "casual": [", ", ". ", " ", "... ", " e ", ", e "],
            "informal": [" ", ", ", " e ", "... ", ". "],
        }[style]
        return self.rng.choice(options)

    def _end(self, style: str, rant: bool) -> str:
        if rant:
            return self.rng.choice(["!!", ".", "", "!!!"])
        options = {
            "formal": (["."], [1]),
            "standard": ([".", "", "!"], [8, 2, 1]),
            "casual": (["", ".", "!", "..."], [4, 3, 1, 1]),
            "informal": (["", "!!", "...", "."], [5, 1, 1, 1]),
        }[style]
        return self.rng.choices(options[0], weights=options[1])[0]

    def _glue(self, kind: str, style: str) -> str:
        table = self.bank.openers if kind == "opener" else self.bank.closers
        registers = {"formal": ["f"], "standard": ["n"], "casual": ["n", "c"], "informal": ["c", "n"]}[style]
        return self.rng.choice([g for r in registers for g in table.get(r, ())])

    def _pieces(self, frags: list[Fragment], style: str, critical: bool, rant: bool) -> list[Piece]:
        params = STYLE_PARAMS[style]
        pieces: list[Piece] = []
        if self.rng.random() < params.opener and not rant:
            pieces.append(Piece(self._glue("opener", style)))
        for i, frag in enumerate(frags):
            if i:
                pieces.append(Piece(self._connector(frags[i - 1], frag, style)))
            pieces.extend(self._render(frag, style))
        ends_with_mark = frags[-1].text[-1] in "?!."
        if not critical and not rant and self.rng.random() < params.closer:
            if not ends_with_mark:
                pieces.append(Piece(". " if style in ("formal", "standard") else self.rng.choice([". ", ", ", " "])))
            else:
                pieces.append(Piece(" "))
            pieces.append(Piece(self._glue("closer", style)))
            ends_with_mark = False
        if not ends_with_mark:
            pieces.append(Piece(self._end(style, rant)))
        return pieces

    def _style_pieces(self, pieces: list[Piece], style: str, critical: bool) -> tuple[list[Piece], set[str]]:
        noiser = Noiser(self.rng, self.bank, style)
        params = noiser.params
        traits: set[str] = set()
        for piece in pieces:
            if piece.pii is None:
                piece.text = noiser.apply(piece.text, typos=piece.typos, keep_case=piece.keep_case)
        if noiser.typos:
            traits.add("typos")
        if params.lowercase:
            if self.rng.random() < params.capitalize_first:
                self._capitalize(pieces, only_first=True)
        else:
            self._capitalize(pieces, only_first=False)
        if not critical and params.elongate and self.rng.random() < params.elongate:
            for piece in reversed(pieces):
                if piece.pii is None and (longer := noiser.elongate(piece.text)):
                    piece.text = longer
                    traits.add("repeated_letters")
                    break
        if not critical and params.laugh and self.rng.random() < params.laugh:
            last = pieces[-1]
            if last.pii is None and last.text in ("", ".", "!", "!!", "..."):
                last.text = ""
            pieces.append(Piece(" " + self.rng.choice(self.bank.laughter)))
        return pieces, traits

    @staticmethod
    def _capitalize(pieces: list[Piece], only_first: bool) -> None:
        start = True
        for piece in pieces:
            if piece.pii is not None:
                if piece.text.strip():
                    if only_first:
                        return
                    start = False
                continue
            if only_first:
                for i, ch in enumerate(piece.text):
                    if ch.isalpha():
                        piece.text = piece.text[:i] + ch.upper() + piece.text[i + 1:]
                        return
                    if not ch.isspace():
                        return
                continue
            piece.text, start = capitalize_sentences(piece.text, start)

    @staticmethod
    def _join(pieces: list[Piece]) -> tuple[str, list[dict[str, object]]]:
        parts: list[str] = []
        spans: list[dict[str, object]] = []
        pos = 0
        for piece in pieces:
            if piece.pii:
                spans.append({"start": pos, "end": pos + len(piece.text), "type": piece.pii,
                              "text": piece.text})
            parts.append(piece.text)
            pos += len(piece.text)
        return "".join(parts), spans

    # ----- labels --------------------------------------------------------------------------

    def _text_traits(self, text: str, spans: list[dict[str, object]]) -> set[str]:
        plain = list(text)
        for span in spans:
            for i in range(int(span["start"]), int(span["end"])):  # type: ignore[arg-type]
                plain[i] = " "
        folded = fold("".join(plain))
        traits = set()
        tokens = set(re.findall(r"\w+", "".join(plain).lower()))
        if tokens & self._abbreviations:
            traits.add("abbreviations")
        if any(rx.search(folded) for rx in self._slang):
            traits.add("slang")
        if self._laughter.search(folded):
            traits.add("laughter")
        # Numbers and name-like words outside the planted values are personal-data decoys,
        # whether or not the fragment that brought them carries a decoy tag.
        if re.search(r"\d", folded) or (self._word_names and self._word_names.search(folded)):
            traits.add("pii_decoy")
        return traits

    def _labels(self, frags: list[Fragment], resp: Respondent, style: str) -> Composed:
        themes = sorted({t for f in frags for t in f.themes}, key=self.theme_order.index)
        types = sorted({c for f in frags for c in f.critical}, key=self.critical_order.index)
        traits: set[str] = set()
        if style == "formal":
            traits.add("formal")
        if resp.kind == "rant":
            traits.add("rant")
        if any("irony" in f.tags for f in frags):
            traits.add("irony")
        polarities = {f.polarity for f in frags}
        if {"pos", "neg"} <= polarities:
            traits.add("mixed")
        if any({"decoy_num", "decoy_name"} & set(f.tags) for f in frags) or resp.kind == "decoy":
            traits.add("pii_decoy")
        if resp.kind == "near_miss":
            traits.add("decoy")
        if any(f.self_harm for f in frags):
            traits.add("self_harm")
        return Composed(
            text="",
            themes=themes,
            sentiment=overall_sentiment(frags),
            critical_types=types,
            subtle=bool(types) and all(f.subtle for f in frags if f.critical),
            traits=sorted(traits, key=TRAIT_ORDER.index),
        )

    # ----- public --------------------------------------------------------------------------

    def compose(self, resp: Respondent) -> Composed:
        if resp.kind == "empty":
            return Composed(text="", traits=["empty"])
        style = self.choose_style(resp.area, resp.special)
        if resp.kind == "short":
            return self._short(resp, style)
        frags = self.fragments_for(resp, style)
        if not frags:
            raise ValueError(f"no fragment fits respondent {resp.index}")
        for frag in frags:
            self.usage[frag.key] = self.usage.get(frag.key, 0) + 1
        critical = any(f.critical for f in frags)
        pieces = self._pieces(frags, style, critical, rant=resp.kind == "rant")
        pieces, noise_traits = self._style_pieces(pieces, style, critical)
        text, spans = self._join(pieces)
        if text != text.strip():
            raise ValueError(f"comment of respondent {resp.index} has surrounding spaces")
        result = self._labels(frags, resp, style)
        result.text, result.spans = text, spans
        traits = set(result.traits) | noise_traits | self._text_traits(text, spans)
        if spans:
            traits.add("pii")
        result.traits = sorted(traits, key=TRAIT_ORDER.index)
        return result

    def _short(self, resp: Respondent, style: str) -> Composed:
        frag = resp.special
        assert frag is not None
        self.usage[frag.key] = self.usage.get(frag.key, 0) + 1
        pieces = [Piece(frag.text)]
        pieces, noise_traits = self._style_pieces(pieces, style, critical=False)
        text, _ = self._join(pieces)
        text = text.strip()
        traits = noise_traits | self._text_traits(text, [])
        traits.add("one_word" if len(frag.text.split()) == 1 else "short")
        return Composed(text=text, themes=list(frag.themes), sentiment=frag.polarity,
                        traits=sorted(traits, key=TRAIT_ORDER.index))
