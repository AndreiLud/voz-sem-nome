"""Regex detectors for Brazilian personal data formats.

Each detector yields candidate spans in original text coordinates. `find_all` runs them in a
fixed priority order (cue-based and specific formats first, generic digit runs last) and keeps
a candidate only when it does not overlap one already accepted.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterator

from . import PiiSpan
from . import validators as v

Detector = Callable[[str], Iterator[PiiSpan]]

_STATE_CODES = (
    "AC|AL|AP|AM|BA|CE|DF|ES|GO|MA|MT|MS|MG|PA|PB|PR|PE|PI|RJ|RN|RS|RO|RR|SC|SP|SE|TO"
)
# Optional words between a cue and its value: "matrícula nº 123", "RG: 12.345.678-9", "cpf é ..."
# ("e" is "é" typed without the accent: "meu rg e 12.345.678-9")
_FILLER = r"(?i:\s*(?:n[º°o]\.?|n\.|n[úu]mero|num\.?|é|eh|e|:|#|-)(?=[\s\d:#-]|$))*\s*"

EMAIL_RE = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+")

CNPJ_FORMATTED_RE = re.compile(
    r"(?<![\w./-])[A-Za-z0-9]{2}\.?[A-Za-z0-9]{3}\.?[A-Za-z0-9]{3}/[A-Za-z0-9]{4}-?\d{2}(?![\w/-])"
)
CNPJ_PLAIN_RE = re.compile(r"(?<![\w./-])[A-Za-z0-9]{12}\d{2}(?![\w/-])")
CNPJ_CUE_RE = re.compile(r"(?<!\w)cnpj\W{0,6}(?:\w+\W+)?$", re.IGNORECASE)

RG_RE = re.compile(
    r"(?i:(?<!\w)(?:rg|r\.g\.?|identidade|c[ée]dula\s+de\s+identidade)(?!\w))"
    + _FILLER
    + rf"(?P<val>(?:(?i:{_STATE_CODES})[-\s]?)?\d(?:[.\-]?\d){{4,13}}(?:-?[Xx])?)(?![\w])"
)

MATRICULA_RE = re.compile(
    r"(?i:(?<!\w)(?:matr[íi]cula|mat\.|registro|re|crach[áa]|id\s+funcional|chapa|"
    r"n[úu]mero\s+funcional)(?:\s+funcional)?(?!\w))"
    + _FILLER
    + r"(?P<val>[A-Za-z]{0,3}-?\d[\w./-]*)"
)
_UNIT_AFTER_RE = re.compile(r"\s*(?:h\b|hrs?\b|horas?\b|min|dias?\b|reais\b|anos?\b|%|x\b|º|°)",
                            re.IGNORECASE)

CEP_CUE_RE = re.compile(
    r"(?i:(?<!\w)cep(?!\w))" + _FILLER + r"(?P<val>\d{5}-?\d{3}|\d{2}\.\d{3}-?\d{3})(?!\d)"
)
CEP_RE = re.compile(r"(?<![\w.,/-])(?:\d{5}-\d{3}|\d{2}\.\d{3}-\d{3})(?![\w/-]|[.,]\d)")

CPF_RE = re.compile(
    r"(?<![\w.,/-])\d{3}[.\s]?\d{3}[.\s]?\d{3}\s?[-./\s]?\s?\d{2}"
    r"(?![\w/-]|[.,]\d)"
)
CPF_CUE_RE = re.compile(r"(?<!\w)cpf\W{0,6}(?:\w+\W+)?$", re.IGNORECASE)

PHONE_CUE_RE = re.compile(
    r"(?<!\w)(?:tel|telefone|fone|cel|celular|whats|whatsapp|zap|wpp|contato|liga(?:r)?|n[úu]mero)"
    r"\W{0,6}(?:\w+\W+)?$",
    re.IGNORECASE,
)
PHONE_FORMATTED_RE = re.compile(
    r"(?<![\w+])(?:\+\s?55[\s.-]?)?(?:\(\s?0?\d{2}\s?\)|0?\d{2})[\s.-]?(?:9[\s.]?)?"
    r"(?P<a>\d{4})[\s.-]?(?P<b>\d{4})"
    r"(?![\w/-]|[.,]\d)"
)
PHONE_LOCAL_RE = re.compile(
    r"(?<![\w.,/+-])(?:9[\s.]?)?(?P<a>\d{4})[\s.-](?P<b>\d{4})(?![\w/-]|[.,]\d)"
)
PHONE_CUE_DIGITS_RE = re.compile(
    r"(?i:(?<!\w)(?:tel|telefone|fone|cel|celular|whats|whatsapp|zap|wpp|contato)(?!\w))"
    + _FILLER + r"(?P<val>\d{8,9})(?!\d)"
)
RAMAL_RE = re.compile(r"(?i:(?<!\w)ramal(?!\w))" + _FILLER + r"(?P<val>\d{3,5})(?!\d)")
DIGIT_RUN_RE = re.compile(r"(?<!\d)\d{8,}(?!\d)")

PLATE_MERCOSUL_RE = re.compile(r"(?<![\w-])[A-Za-z]{3}-?\d[A-Za-z]\d{2}(?![\w-])")
PLATE_OLD_RE = re.compile(r"(?<![\w-])(?P<letters>[A-Za-z]{3})(?P<sep>[-\s]?)(?P<digits>\d{4})(?![\w-])")
PLATE_CUE_RE = re.compile(
    r"(?<!\w)(?:placa|carro|moto|ve[íi]culo|caminh[ãa]o|van|furg[ãa]o|truck|carreta)"
    r"[\s:]{0,4}(?:\w+[\s:]+){0,2}$",
    re.IGNORECASE,
)
# Three-letter words and acronyms that precede a 4-digit number in ordinary text ("ano 2024").
PLATE_STOPWORDS = frozenset(
    "ano dia mes jan fev mar abr mai jun jul ago set out nov dez das dos nas nos com sem por pra "
    "pro que era foi mil iso nbr plr ppr clt sap crm ura erp wms tms the and top cep cpf tel bus "
    "seg ter qua qui sex sab dom lei art cap vol nro ref cod num pag ped nfe".split()
)

_STREET_CUE = (
    r"(?i:(?<!\w)(?:rua|r\.|av\.|av|avenida|travessa|tv\.|alameda|al\.|estrada|estr\.|rodovia|"
    r"rod\.|pra[çc]a|p[çc]a\.))"
)
_CAP_WORD = r"(?:[A-ZÀ-Ý][\wÀ-ÿ'.]*|\d{1,2}(?=\s+de\s))"
_CONNECTOR = r"(?:d[aeo]s?|e)"
_HOUSE_NUMBER = r"(?:\s*,?\s*(?:n[º°o]\.?\s*)?\d{1,5}[A-Za-z]?(?!\d|[.,]\d)|\s*,?\s*km\s*\d{1,4})"
ADDRESS_CAP_RE = re.compile(
    _STREET_CUE
    + rf"\s+(?:{_CONNECTOR}\s+)?{_CAP_WORD}(?:[ \t]+(?:{_CONNECTOR}[ \t]+)?{_CAP_WORD}){{0,5}}"
    + rf"{_HOUSE_NUMBER}?"
)
# Lowercase street names need a house number, and the number must not count something
# ("a rua tem 30 buracos").
ADDRESS_LOWER_RE = re.compile(
    _STREET_CUE + r"\s+(?:[a-zà-ÿ]+\s+){0,4}?[a-zà-ÿ]{3,}" + rf"{_HOUSE_NUMBER}"
    + r"(?!\s+[a-zà-ÿ]+s\b|\s*(?:%|h\b|min\b|m\b))"
)
NEIGHBORHOOD_RE = re.compile(
    rf"(?i:(?<!\w)bairro)\s+(?:{_CONNECTOR}\s+)?{_CAP_WORD}(?:[ \t]+(?:{_CONNECTOR}[ \t]+)?{_CAP_WORD}){{0,3}}"
)
FORMATTED_DOC_RE = re.compile(r"(?<![\w.,/-])\d{1,3}(?:\.\d{3}){2,}-[\dXx]{1,2}(?![\w/-])")
_MONEY_BEFORE_RE = re.compile(r"R\$\s*$")


def _span(start: int, end: int, pii_type: str) -> PiiSpan:
    return PiiSpan(start=start, end=end, type=pii_type, source="regex")


def _cue_before(text: str, start: int, cue_re: re.Pattern[str], window: int = 30) -> bool:
    return bool(cue_re.search(text[max(0, start - window):start]))


def _trim(text: str, start: int, end: int, chars: str = ".-/") -> tuple[int, int]:
    while end > start and text[end - 1] in chars:
        end -= 1
    return start, end


def find_emails(text: str) -> Iterator[PiiSpan]:
    for m in EMAIL_RE.finditer(text):
        yield _span(m.start(), m.end(), "EMAIL")


def find_cnpjs(text: str) -> Iterator[PiiSpan]:
    for m in CNPJ_FORMATTED_RE.finditer(text):
        if sum(ch.isdigit() for ch in m.group()) >= 4:
            yield _span(m.start(), m.end(), "CNPJ")
    for m in CNPJ_PLAIN_RE.finditer(text):
        value = m.group()
        has_digit_root = any(ch.isdigit() for ch in value[:12])
        if (has_digit_root and v.cnpj_is_valid(value)) or _cue_before(text, m.start(), CNPJ_CUE_RE):
            yield _span(m.start(), m.end(), "CNPJ")


def _cue_value(pattern: re.Pattern[str], pii_type: str, min_digits: int) -> Detector:
    def detect(text: str) -> Iterator[PiiSpan]:
        for m in pattern.finditer(text):
            start, end = _trim(text, *m.span("val"))
            if sum(ch.isdigit() for ch in text[start:end]) >= min_digits:
                yield _span(start, end, pii_type)
    return detect


find_rgs = _cue_value(RG_RE, "RG", 5)
find_ceps_with_cue = _cue_value(CEP_CUE_RE, "CEP", 8)
find_ramal = _cue_value(RAMAL_RE, "TELEFONE", 3)
find_phones_with_cue = _cue_value(PHONE_CUE_DIGITS_RE, "TELEFONE", 8)


def find_matriculas(text: str) -> Iterator[PiiSpan]:
    for m in MATRICULA_RE.finditer(text):
        start, end = _trim(text, *m.span("val"))
        if sum(ch.isdigit() for ch in text[start:end]) < 3:
            continue
        if _UNIT_AFTER_RE.match(text, end):
            continue  # "registro 100 horas", "chapa 300%"
        yield _span(start, end, "MATRICULA")


def find_ceps(text: str) -> Iterator[PiiSpan]:
    for m in CEP_RE.finditer(text):
        yield _span(m.start(), m.end(), "CEP")


def find_formatted_cpfs(text: str) -> Iterator[PiiSpan]:
    for m in CPF_RE.finditer(text):
        if not m.group().isdigit():  # plain 11-digit runs are handled by find_plain_numbers
            yield _span(m.start(), m.end(), "CPF")


def _classify_eleven_digits(text: str, start: int, digits: str) -> str:
    """Decide between CPF and mobile phone for an unformatted 11-digit run."""
    if _cue_before(text, start, CPF_CUE_RE) or v.cpf_is_valid(digits):
        return "CPF"
    if _cue_before(text, start, PHONE_CUE_RE) or v.looks_like_mobile(digits):
        return "TELEFONE"
    return "CPF"  # recall first: a bare 11-digit run is most likely a mistyped CPF


def find_plain_numbers(text: str) -> Iterator[PiiSpan]:
    """Unformatted digit runs with a clear reading: CPF or phone (11), phone (10, or 12-13 with 55)."""
    for m in DIGIT_RUN_RE.finditer(text):
        digits = m.group()
        if len(digits) == 11:
            yield _span(m.start(), m.end(), _classify_eleven_digits(text, m.start(), digits))
        elif len(digits) == 10 and v.looks_like_landline_or_old_mobile(digits):
            yield _span(m.start(), m.end(), "TELEFONE")
        elif len(digits) in (12, 13) and digits.startswith("55") and v.is_valid_ddd(digits[2:4]):
            start = m.start() - 1 if text[max(0, m.start() - 1):m.start()] == "+" else m.start()
            yield _span(start, m.end(), "TELEFONE")


def _is_formatted(value: str) -> bool:
    return any(ch in value for ch in "+()-. ")


def find_formatted_phones(text: str) -> Iterator[PiiSpan]:
    for m in PHONE_FORMATTED_RE.finditer(text):
        if _is_formatted(m.group()) and not _looks_like_two_numbers(m.group("a"), m.group("b")):
            yield _span(m.start(), m.end(), "TELEFONE")


def _looks_like_two_numbers(left: str, right: str) -> bool:
    """'2023-2024' or '5000 6000' are year or value ranges, not a phone number."""
    years = all(len(p) == 4 and p[:2] in ("19", "20") for p in (left, right))
    round_values = left.endswith("00") and right.endswith("00")
    return years or round_values


def find_local_phones(text: str) -> Iterator[PiiSpan]:
    """Numbers without area code: 9 digits starting with 9, or 8 digits starting with 2 to 9."""
    for m in PHONE_LOCAL_RE.finditer(text):
        digits = v.only_digits(m.group())
        if len(digits) == 8 and digits[0] not in "23456789":
            continue
        if _looks_like_two_numbers(m.group("a"), m.group("b")):
            continue
        yield _span(m.start(), m.end(), "TELEFONE")


def find_plates(text: str) -> Iterator[PiiSpan]:
    for m in PLATE_MERCOSUL_RE.finditer(text):
        yield _span(m.start(), m.end(), "PLACA")
    for m in PLATE_OLD_RE.finditer(text):
        letters, sep, digits = m.group("letters"), m.group("sep"), m.group("digits")
        if letters.lower() in PLATE_STOPWORDS:
            continue
        if _cue_before(text, m.start(), PLATE_CUE_RE):
            yield _span(m.start(), m.end(), "PLACA")
            continue
        year_like = digits[:2] in ("19", "20")
        if sep == "-":
            yield _span(m.start(), m.end(), "PLACA")
        elif sep == "" and (letters.isupper() or not year_like):
            yield _span(m.start(), m.end(), "PLACA")
        elif sep and letters.isupper() and not year_like:  # "ABC 1234", never "ano 2024"
            yield _span(m.start(), m.end(), "PLACA")


def find_addresses(text: str) -> Iterator[PiiSpan]:
    for pattern in (ADDRESS_CAP_RE, ADDRESS_LOWER_RE, NEIGHBORHOOD_RE):
        for m in pattern.finditer(text):
            start, end = _trim(text, m.start(), m.end(), chars=".,- ")
            yield _span(start, end, "LOCAL")


def find_documents(text: str) -> Iterator[PiiSpan]:
    """Leftover identifiers: dotted numbers with a check digit, and runs of 8 or more digits."""
    for m in FORMATTED_DOC_RE.finditer(text):
        yield _span(m.start(), m.end(), "DOCUMENTO")
    for m in DIGIT_RUN_RE.finditer(text):
        if not _MONEY_BEFORE_RE.search(text[max(0, m.start() - 4):m.start()]):
            yield _span(m.start(), m.end(), "DOCUMENTO")


# Priority order: earlier detectors win on overlap.
DETECTORS: tuple[Detector, ...] = (
    find_emails,
    find_cnpjs,
    find_rgs,
    find_matriculas,
    find_ceps_with_cue,
    find_ceps,
    find_formatted_cpfs,
    find_plain_numbers,
    find_formatted_phones,
    find_ramal,
    find_phones_with_cue,
    find_local_phones,
    find_plates,
    find_addresses,
    find_documents,
)


def overlaps(a: PiiSpan, b: PiiSpan) -> bool:
    return a.start < b.end and b.start < a.end


def find_all(text: str, detectors: tuple[Detector, ...] = DETECTORS) -> list[PiiSpan]:
    """All regex spans, non-overlapping and sorted by position."""
    accepted: list[PiiSpan] = []
    for detect in detectors:
        for candidate in sorted(detect(text), key=lambda s: (s.start, -(s.end - s.start))):
            if candidate.end > candidate.start and not any(overlaps(candidate, s) for s in accepted):
                accepted.append(candidate)
    return sorted(accepted, key=lambda s: s.start)
