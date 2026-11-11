"""Load and validate the curated phrase bank (data/synthetic/phrasebank.yaml)."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from enps import config

DEFAULT_BANK_PATH = config.ROOT / "data" / "synthetic" / "phrasebank.yaml"

SLOT_RE = re.compile(r"\{([A-Z_]+)(?::([a-z]+))?\}")
SLOT_PII_TYPE = {
    "PESSOA_F": "PESSOA", "PESSOA_M": "PESSOA", "PESSOA_WN": "PESSOA",
    "CIDADE": "LOCAL", "BAIRRO": "LOCAL", "RUA": "LOCAL",
    "EMAIL": "EMAIL", "TELEFONE": "TELEFONE", "CPF": "CPF", "RG": "RG", "CEP": "CEP",
    "MATRICULA": "MATRICULA", "PLACA": "PLACA",
}
NAME_VARIANTS = ("first", "full", "lower")
REGISTERS = ("n", "f", "c")
TAGS = ("irony", "decoy_num", "decoy_name")
POSITIONS = ("start", "end")
GENDERS = ("f", "m")

DEFAULT_WEIGHT = {"fragments": 1, "rants": 2, "critical": 3}
DEFAULT_REGISTER = {"rants": "c"}


def fold(text: str) -> str:
    """Lower case without accents, used for keyword and duplicate checks."""
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


@dataclass(frozen=True)
class Fragment:
    """One curated sentence (or template with slots) and its intended labels."""

    key: str
    text: str
    themes: tuple[str, ...]
    polarity: str
    main_theme: str = ""
    weight: int = 1
    areas: tuple[str, ...] = ()
    tenures: tuple[str, ...] = ()
    gender: str | None = None
    register: str = "n"
    tags: tuple[str, ...] = ()
    critical: tuple[str, ...] = ()
    subtle: bool = False
    self_harm: bool = False
    position: str | None = None

    def slots(self) -> list[tuple[str, str | None]]:
        return [(m.group(1), m.group(2)) for m in SLOT_RE.finditer(self.text)]

    def pii_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for slot, _ in self.slots():
            pii_type = SLOT_PII_TYPE[slot]
            counts[pii_type] = counts.get(pii_type, 0) + 1
        return counts

    def fits(self, area: str, tenure: str, gender: str) -> bool:
        return (
            (not self.areas or area in self.areas)
            and (not self.tenures or tenure in self.tenures)
            and (self.gender is None or self.gender == gender)
        )


@dataclass
class PhraseBank:
    fragments: tuple[Fragment, ...]
    rants: tuple[Fragment, ...]
    short: tuple[Fragment, ...]
    near_miss: tuple[Fragment, ...]
    decoys: tuple[Fragment, ...]
    critical: tuple[Fragment, ...]
    pii: tuple[Fragment, ...]
    openers: dict[str, tuple[str, ...]]
    closers: dict[str, tuple[str, ...]]
    abbreviations: dict[str, tuple[str, ...]]
    light_abbreviations: tuple[str, ...]
    contractions: tuple[str, ...]
    slang: tuple[str, ...]
    laughter: tuple[str, ...]
    explicit_keywords: tuple[str, ...]
    people: dict[str, tuple[str, ...]]
    places: dict[str, tuple[str, ...]]
    _pools: dict[tuple[str, str], tuple[Fragment, ...]] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        pools: dict[tuple[str, str], list[Fragment]] = {}
        for frag in self.fragments:
            pools.setdefault((frag.main_theme, frag.polarity), []).append(frag)
        self._pools = {key: tuple(items) for key, items in pools.items()}

    def pool(self, theme: str, polarity: str) -> tuple[Fragment, ...]:
        """Regular fragments whose main theme and polarity match."""
        return self._pools.get((theme, polarity), ())

    def is_explicit(self, text: str) -> bool:
        """True when the text contains an obvious keyword for a critical topic."""
        words = re.findall(r"\w+", fold(text))
        return any(word.startswith(kw) for word in words for kw in self.explicit_keywords)

    def abbreviation_outputs(self) -> frozenset[str]:
        outputs = {fold(o) for options in self.abbreviations.values() for o in options}
        outputs.update(fold(c) for c in self.contractions)
        outputs.update(o for options in self.abbreviations.values() for o in options)
        return frozenset(outputs)


def _ordered(codes: list[str], reference: list[str]) -> tuple[str, ...]:
    unique = list(dict.fromkeys(codes))
    return tuple(sorted(unique, key=reference.index))


def _as_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(v) for v in value)


class _Builder:
    """Turns raw YAML entries into validated Fragment objects."""

    def __init__(self) -> None:
        self.themes = config.theme_codes()
        self.sentiments = config.sentiment_codes()
        self.criticals = config.critical_codes()
        self.areas = config.area_codes()
        self.tenures = config.tenure_codes()

    def build(self, key: str, entry: Any, section: str, themes: list[str] | None = None,
              polarity: str | None = None) -> Fragment:
        data = {"t": entry} if isinstance(entry, str) else dict(entry)
        if themes is None:
            themes = list(_as_tuple(data.get("th")))
        themes = themes + list(_as_tuple(data.get("also")))
        polarity = polarity or data.get("s") or "neg"
        frag = Fragment(
            key=key,
            text=str(data["t"]),
            themes=_ordered(themes, self.themes) if themes else (),
            polarity=polarity,
            main_theme=themes[0] if section == "fragments" else "",
            weight=int(data.get("w", DEFAULT_WEIGHT.get(section, 1))),
            areas=_ordered(list(_as_tuple(data.get("a"))), self.areas),
            tenures=_ordered(list(_as_tuple(data.get("ten"))), self.tenures),
            gender=data.get("g"),
            register=data.get("r", DEFAULT_REGISTER.get(section, "n")),
            tags=_as_tuple(data.get("tags")),
            critical=_ordered(list(_as_tuple(data.get("types"))), self.criticals),
            subtle=bool(data.get("subtle", False)),
            self_harm=bool(data.get("self_harm", False)),
            position=data.get("pos"),
        )
        self.validate(frag, section, data)
        return frag

    def validate(self, frag: Fragment, section: str, data: dict[str, Any]) -> None:
        problems = []
        known = {"t", "th", "s", "also", "a", "ten", "g", "r", "tags", "w", "types", "subtle",
                 "self_harm", "pos"}
        problems += [f"unknown key {k!r}" for k in data if k not in known]
        problems += [f"unknown theme {t!r}" for t in frag.themes if t not in self.themes]
        if "out" in frag.themes and len(frag.themes) > 1:
            problems.append("'out' must be alone")
        if not frag.themes and not (section == "pii" and frag.position):
            problems.append("no theme (only identification templates with pos may have none)")
        if frag.polarity not in self.sentiments:
            problems.append(f"unknown polarity {frag.polarity!r}")
        problems += [f"unknown area {a!r}" for a in frag.areas if a not in self.areas]
        problems += [f"unknown tenure {t!r}" for t in frag.tenures if t not in self.tenures]
        problems += [f"unknown critical type {c!r}" for c in frag.critical if c not in self.criticals]
        problems += [f"unknown tag {t!r}" for t in frag.tags if t not in TAGS]
        if frag.gender not in (None, *GENDERS):
            problems.append(f"unknown gender {frag.gender!r}")
        if frag.register not in REGISTERS:
            problems.append(f"unknown register {frag.register!r}")
        if frag.position not in (None, *POSITIONS):
            problems.append(f"unknown position {frag.position!r}")
        for slot, variant in frag.slots():
            if slot not in SLOT_PII_TYPE:
                problems.append(f"unknown slot {slot!r}")
            if variant is not None and (variant not in NAME_VARIANTS or not slot.startswith("PESSOA")):
                problems.append(f"bad slot variant {slot}:{variant}")
        if frag.slots() and section not in ("pii", "critical"):
            problems.append("slots are only allowed in pii and critical templates")
        if section == "pii" and not frag.slots():
            problems.append("pii template without slots")
        if section == "critical" and not frag.critical:
            problems.append("critical template without types")
        if frag.text != frag.text.strip() or "\n" in frag.text:
            problems.append("text has surrounding spaces or a line break")
        if problems:
            raise ValueError(f"phrase bank entry {frag.key}: " + "; ".join(problems))


def _glue(raw: dict[str, Any]) -> dict[str, tuple[str, ...]]:
    return {register: _as_tuple(items) for register, items in raw.items()}


def load_bank(path: Path | str = DEFAULT_BANK_PATH) -> PhraseBank:
    """Read the phrase bank and fail early on any unknown code or malformed template."""
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    builder = _Builder()
    fragments = []
    for theme, groups in raw["fragments"].items():
        for polarity, items in groups.items():
            for i, entry in enumerate(items):
                fragments.append(builder.build(f"{theme}.{polarity}.{i}", entry, "fragments",
                                               themes=[theme], polarity=polarity))
    lists = {
        section: tuple(builder.build(f"{section}.{i}", entry, section)
                       for i, entry in enumerate(raw[section]))
        for section in ("rants", "short", "near_miss", "decoys", "critical", "pii")
    }
    noise = raw["noise"]
    bank = PhraseBank(
        fragments=tuple(fragments),
        openers=_glue(raw["glue"]["openers"]),
        closers=_glue(raw["glue"]["closers"]),
        abbreviations={str(k): _as_tuple(v) for k, v in noise["abbreviations"].items()},
        light_abbreviations=_as_tuple(noise["light"]),
        contractions=_as_tuple(noise["contractions"]),
        slang=_as_tuple(noise["slang"]),
        laughter=_as_tuple(noise["laughter"]),
        explicit_keywords=_as_tuple(noise["explicit_keywords"]),
        people={k: _as_tuple(v) for k, v in raw["fake_people"].items()},
        places={k: _as_tuple(v) for k, v in raw["fake_places"].items()},
        **lists,
    )
    _check_critical(bank)
    return bank


def _check_critical(bank: PhraseBank) -> None:
    for frag in bank.critical:
        if frag.subtle == bank.is_explicit(frag.text):
            state = "subtle but has" if frag.subtle else "explicit but has no"
            raise ValueError(f"phrase bank entry {frag.key}: marked {state} explicit keyword")
    for frag in (*bank.fragments, *bank.rants, *bank.decoys, *bank.short, *bank.pii):
        if bank.is_explicit(frag.text):
            raise ValueError(f"phrase bank entry {frag.key}: explicit critical keyword outside "
                             "the critical and near_miss sections")
