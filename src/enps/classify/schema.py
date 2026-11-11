"""Output schema v1 of the two model stages, plus the validation and coercion rules.

Field names are one letter on purpose: every output token costs time on a CPU.
    t  themes, 1 to 3 codes ("out" only alone)
    s  sentiment: pos | neu | neg
    e  short excerpt copied from the comment that justifies a critical flag, or ""
    c  critical flag
    k  critical type codes, 0 to 3
Stage 1 ("classify") answers t, s, e, c, k. The verifier answers e, c, k. The order matters:
the model copies the evidence before it decides.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model

from enps import config

SCHEMA_VERSION = 1
EXCERPT_MAX_CHARS = 100
OUT_THEME = "out"
_QUOTES = "\"'“”‘’«»"


class OutputError(ValueError):
    """The model answer broke a rule. `hint` is the Portuguese message sent back on retry."""

    def __init__(self, message: str, hint: str):
        super().__init__(message)
        self.hint = hint


@dataclass(frozen=True)
class ClassifyOutput:
    themes: tuple[str, ...]
    sentiment: str
    excerpt: str
    critical: bool
    types: tuple[str, ...]
    notes: tuple[str, ...] = field(default=())

    def as_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"t": list(self.themes), "s": self.sentiment, "e": self.excerpt,
                                "c": self.critical, "k": list(self.types)}
        if self.notes:
            data["notes"] = list(self.notes)
        return data


@dataclass(frozen=True)
class VerifyOutput:
    excerpt: str
    critical: bool
    types: tuple[str, ...]
    notes: tuple[str, ...] = field(default=())

    def as_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"e": self.excerpt, "c": self.critical, "k": list(self.types)}
        if self.notes:
            data["notes"] = list(self.notes)
        return data


def _excerpt_field() -> Any:
    # maxLength goes to the grammar only; a longer excerpt is cut, not rejected.
    return Field(default="", json_schema_extra={"maxLength": EXCERPT_MAX_CHARS})


@lru_cache(maxsize=None)
def _models(themes: tuple[str, ...], sentiments: tuple[str, ...],
            criticals: tuple[str, ...], version: int = SCHEMA_VERSION) -> tuple[type[BaseModel], type[BaseModel]]:
    theme = Literal[themes]  # type: ignore[valid-type]
    sentiment = Literal[sentiments]  # type: ignore[valid-type]
    critical = Literal[criticals]  # type: ignore[valid-type]
    strict = ConfigDict(extra="forbid")
    fields = {
        "t": (list[theme], Field(min_length=1, max_length=4)),
        "s": (sentiment, ...),
        "e": (str, _excerpt_field()),
        "c": (bool, ...),
        "k": (list[critical], Field(default_factory=list, max_length=3)),
    }
    # v1 asks for the excerpt before the decision; v2 decides first and copies an excerpt only for
    # critical comments, which saves output tokens on the common, non-critical case.
    order = ("t", "s", "e", "c", "k") if version == 1 else ("t", "s", "c", "k", "e")
    classify = create_model(f"ClassifyV{version}", __config__=strict, **{k: fields[k] for k in order})
    verify = create_model(
        "VerifyV1", __config__=strict,
        e=(str, _excerpt_field()),
        c=(bool, ...),
        k=(list[critical], Field(default_factory=list, max_length=3)),
    )
    return classify, verify


def schema_version() -> int:
    """Stage 1 schema version, taken from the classify prompt name (classify.v2 -> 2)."""
    name = config.settings()["llm"]["classify_prompt"]
    match = re.search(r"\.v(\d+)$", name)
    return int(match.group(1)) if match else SCHEMA_VERSION


def _current_models() -> tuple[type[BaseModel], type[BaseModel]]:
    return _models(tuple(config.theme_codes()), tuple(config.sentiment_codes()),
                   tuple(config.critical_codes()), schema_version())


def _clean(node: Any) -> Any:
    """Drop pydantic titles and defaults: they only add noise to the grammar."""
    if isinstance(node, dict):
        return {k: _clean(v) for k, v in node.items() if k not in ("title", "default")}
    if isinstance(node, list):
        return [_clean(v) for v in node]
    return node


def _json_schema(model: type[BaseModel]) -> dict[str, Any]:
    schema = _clean(model.model_json_schema())
    schema["required"] = list(schema["properties"])  # every field, in declaration order
    return schema


def classify_schema() -> dict[str, Any]:
    """JSON schema passed as `format` to the stage 1 call."""
    return _json_schema(_current_models()[0])


def verify_schema() -> dict[str, Any]:
    """JSON schema passed as `format` to the verifier call."""
    return _json_schema(_current_models()[1])


# --------------------------------------------------------------------- validation

def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _parse(content: str, model: type[BaseModel]) -> dict[str, Any]:
    try:
        raw = json.loads(content)
    except ValueError as exc:
        raise OutputError(f"invalid JSON: {exc}", "a resposta não era um JSON válido") from exc
    if not isinstance(raw, dict):
        raise OutputError("JSON is not an object", "a resposta precisa ser um objeto JSON")
    try:
        return model.model_validate(raw).model_dump()
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(map(str, e['loc'])) or 'objeto'}: {e['msg']}"
                             for e in exc.errors())
        fields_ = ", ".join(sorted({str(e["loc"][0]) for e in exc.errors() if e["loc"]})) or "objeto"
        raise OutputError(f"schema: {problems}",
                          f"o JSON não seguiu o formato pedido no campo {fields_}") from exc


def _find_in_text(excerpt: str, text: str) -> re.Match[str] | None:
    """Locate the excerpt in the comment, ignoring case and differences in whitespace.

    An occurrence that ends at a word boundary wins over an earlier one inside a longer word.
    """
    pattern = r"\s+".join(re.escape(word) for word in excerpt.split())
    return (re.search(pattern + r"(?!\w)", text, re.IGNORECASE)
            or re.search(pattern, text, re.IGNORECASE))


def _clean_excerpt(excerpt: str, text: str, notes: list[str]) -> str:
    """Return the excerpt as written in the comment, or "" when it was not copied from it."""
    candidate = excerpt.strip().strip(_QUOTES).strip()
    for mark in ("...", "…"):
        candidate = candidate.removeprefix(mark).removesuffix(mark).strip()
    if not candidate:
        return ""
    match = _find_in_text(candidate, text)
    if match is None:
        notes.append("excerpt not found in the comment, cleared")
        return ""
    found, end = match.group(0), match.end()
    if end < len(text) and text[end].isalnum() and text[end - 1].isalnum() and " " in found:
        found = found.rsplit(" ", 1)[0]  # the grammar length limit stopped it inside a word
        notes.append("excerpt ended inside a word, last word dropped")
    if len(found) > EXCERPT_MAX_CHARS:
        cut = found[:EXCERPT_MAX_CHARS + 1]
        found = cut.rsplit(" ", 1)[0] if " " in cut else found[:EXCERPT_MAX_CHARS]
        notes.append(f"excerpt longer than {EXCERPT_MAX_CHARS} characters, cut")
    return found.rstrip(" ,;:")


def _check_critical(critical: bool, types: list[str], notes: list[str]) -> bool:
    if types and not critical:
        notes.append("k was not empty with c=false: c set to true")
        return True
    if critical and not types:
        raise OutputError("c is true but k is empty",
                          "quando c=true, k precisa ter pelo menos um código de tipo crítico")
    return critical


def validate_classify(content: str, text: str) -> ClassifyOutput:
    """Parse and check a stage 1 answer. Raises OutputError when a retry is needed."""
    data = _parse(content, _current_models()[0])
    notes: list[str] = []
    themes = _dedupe(data["t"])
    if OUT_THEME in themes and len(themes) > 1:
        # A retry at temperature 0 repeats the same answer, so repair it: the other themes win.
        themes = [t for t in themes if t != OUT_THEME]
        notes.append('"out" mixed with other themes: "out" dropped')
    types = _dedupe(data["k"])
    critical = _check_critical(data["c"], types, notes)
    return ClassifyOutput(
        themes=tuple(themes), sentiment=data["s"],
        excerpt=_clean_excerpt(data["e"], text, notes),
        critical=critical, types=tuple(types), notes=tuple(notes),
    )


def validate_verify(content: str, text: str) -> VerifyOutput:
    """Parse and check a verifier answer. Raises OutputError when a retry is needed."""
    data = _parse(content, _current_models()[1])
    notes: list[str] = []
    types = _dedupe(data["k"])
    critical = _check_critical(data["c"], types, notes)
    return VerifyOutput(excerpt=_clean_excerpt(data["e"], text, notes),
                        critical=critical, types=tuple(types), notes=tuple(notes))
