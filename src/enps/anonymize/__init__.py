"""Anonymization of free-text survey comments before anything is stored or sent to a model.

Public contract used by the API, the pipeline and the evaluation:

    result = anonymize("falei com o João, meu cpf é 123.456.789-00")
    result.text    -> "falei com o [PESSOA], meu cpf é [CPF]"
    result.spans   -> PiiSpan items in ORIGINAL text coordinates
    result.counts  -> {"PESSOA": 1, "CPF": 1}
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Every placeholder the anonymizer can write. Types match the gold file of the synthetic data.
PII_TYPES = (
    "PESSOA", "LOCAL", "EMAIL", "TELEFONE", "CPF", "CNPJ", "RG",
    "CEP", "MATRICULA", "PLACA", "DOCUMENTO",
)


@dataclass(frozen=True)
class PiiSpan:
    start: int          # offset in the original text, inclusive
    end: int            # offset in the original text, exclusive
    type: str           # one of PII_TYPES
    source: str         # "regex", "ner", "gazetteer" or "context"


@dataclass(frozen=True)
class AnonymizationResult:
    text: str
    spans: tuple[PiiSpan, ...] = ()
    counts: dict[str, int] = field(default_factory=dict)
    version: str = ""


def anonymize(text: str) -> AnonymizationResult:
    """Replace personal data in `text` with typed placeholders such as [PESSOA]."""
    from .pipeline import anonymize as _impl  # loaded lazily: the NER model takes ~1 s

    return _impl(text)
