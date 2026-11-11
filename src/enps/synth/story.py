"""The story told by the synthetic data: sizes, target scores, themes by area, quotas.

Everything the generator needs to know about Talvana Distribuição lives here, so the numbers
can be read (and changed) in one place. docs/synthetic-data.md describes the same story.
"""
from __future__ import annotations

from datetime import date

DEFAULT_SEED = 20261111

AREA_SIZES = {
    "operacoes": 110, "transporte": 60, "atendimento": 80, "comercial": 55,
    "tecnologia": 45, "financeiro": 30, "pessoas": 16, "juridico": 4,
}

# Target eNPS by area and the share of passives (7 or 8) used to build the score counts.
TARGET_ENPS = {
    "tecnologia": 40, "pessoas": 20, "comercial": 10, "financeiro": 5,
    "transporte": -15, "operacoes": -25, "atendimento": -35, "juridico": 0,
}
PASSIVE_SHARE = {
    "tecnologia": 0.30, "pessoas": 0.35, "comercial": 0.35, "financeiro": 0.40,
    "transporte": 0.35, "operacoes": 0.30, "atendimento": 0.25, "juridico": 0.50,
}
# Weights of the scores 0..6 for detractors: harsh areas have more very low scores.
DETRACTOR_WEIGHTS_HARSH = (6, 3, 5, 8, 11, 14, 14)
DETRACTOR_WEIGHTS_MILD = (2, 1, 2, 4, 7, 12, 16)
PROMOTER_TEN_SHARE_HARSH = 0.38
PROMOTER_TEN_SHARE_MILD = 0.5
EIGHT_SHARE = 0.55

TENURE_MIX = {  # lt1, y1to3, y3to5, gt5
    "operacoes": (0.22, 0.38, 0.22, 0.18),
    "transporte": (0.15, 0.30, 0.25, 0.30),
    "atendimento": (0.30, 0.40, 0.18, 0.12),
    "comercial": (0.20, 0.35, 0.25, 0.20),
    "tecnologia": (0.25, 0.40, 0.22, 0.13),
    "financeiro": (0.12, 0.28, 0.27, 0.33),
    "pessoas": (0.19, 0.31, 0.25, 0.25),
    "juridico": (0.0, 0.25, 0.25, 0.50),
}
# Added to a standard normal draw; inside each area, higher draws get the higher scores.
TENURE_EFFECT = {"lt1": 0.6, "y1to3": -0.35, "y3to5": 0.0, "gt5": 0.1}

# Share of women by area. Gender is never written to the files: it only keeps the
# grammatical gender of each comment consistent ("cansado" or "cansada").
FEMALE_SHARE = {
    "operacoes": 0.35, "transporte": 0.12, "atendimento": 0.70, "comercial": 0.45,
    "tecnologia": 0.30, "financeiro": 0.55, "pessoas": 0.75, "juridico": 0.50,
}

THEME_WEIGHTS = {
    "operacoes": {"rem": 5, "car": 4.5, "bem": 3.5, "inf": 3.5, "lid": 2.2, "eqp": 1.5, "cre": 1.5,
                  "flx": 0.6, "rec": 0.7, "com": 0.6, "cul": 0.4},
    "transporte": {"car": 4, "flx": 1.3, "bem": 3.5, "rem": 3, "inf": 2.5, "lid": 1.5, "eqp": 1.2,
                   "cre": 0.9, "rec": 0.7, "com": 0.5, "cul": 0.4},
    "atendimento": {"car": 5.5, "lid": 4.5, "rem": 2.5, "bem": 3, "inf": 2.2, "eqp": 1.5, "flx": 0.7,
                    "cre": 1.3, "rec": 0.7, "com": 0.6, "cul": 0.4},
    "comercial": {"rem": 5.5, "car": 4.5, "lid": 2, "cre": 1.8, "rec": 1.2, "eqp": 1.5, "inf": 1.5,
                  "com": 1, "flx": 0.6, "cul": 0.6, "bem": 0.8},
    "tecnologia": {"cre": 4.5, "flx": 1.5, "rec": 2.2, "eqp": 2.5, "lid": 2, "inf": 2, "rem": 1.5,
                   "car": 1.8, "com": 0.8, "cul": 0.8, "bem": 0.8},
    "financeiro": {"inf": 4.5, "com": 3, "car": 3, "lid": 2, "rem": 1.5, "cre": 1.5, "eqp": 1.5,
                   "flx": 0.6, "rec": 0.8, "cul": 0.5, "bem": 0.8},
    "pessoas": {"cul": 3.5, "car": 3.5, "eqp": 2, "lid": 2, "com": 1.5, "rem": 1, "cre": 1, "rec": 1,
                "flx": 0.7, "inf": 1, "bem": 1},
    "juridico": {"inf": 2, "car": 2, "lid": 1.5, "flx": 0.9, "cre": 1.5, "rem": 1.5, "eqp": 1,
                 "com": 1, "cul": 0.8, "rec": 0.8, "bem": 0.8},
}
# Chance that a regular comment says nothing about any theme ("out"), by score group.
OUT_SHARE = {"det": 0.04, "pas": 0.08, "pro": 0.10}

# Probabilities of neg, neu, pos for each fragment, by score. Noisy on purpose.
POLARITY_BY_SCORE = {
    0: (0.90, 0.07, 0.03), 1: (0.90, 0.07, 0.03), 2: (0.90, 0.07, 0.03), 3: (0.88, 0.08, 0.04),
    4: (0.80, 0.12, 0.08), 5: (0.78, 0.12, 0.10), 6: (0.58, 0.25, 0.17), 7: (0.45, 0.22, 0.33),
    8: (0.33, 0.20, 0.47), 9: (0.20, 0.13, 0.67), 10: (0.08, 0.10, 0.82),
}
# Number of fragments (1 to 4 sentences) by score group.
FRAGMENT_COUNTS = {"det": (0.25, 0.35, 0.25, 0.15), "pas": (0.35, 0.38, 0.20, 0.07),
                   "pro": (0.40, 0.35, 0.18, 0.07)}

STYLES = ("formal", "standard", "casual", "informal")
STYLE_WEIGHTS = {  # formal, standard, casual, informal
    "operacoes": (0.06, 0.24, 0.42, 0.28), "transporte": (0.06, 0.24, 0.42, 0.28),
    "atendimento": (0.08, 0.27, 0.40, 0.25), "comercial": (0.12, 0.38, 0.35, 0.15),
    "tecnologia": (0.10, 0.35, 0.40, 0.15), "financeiro": (0.25, 0.42, 0.25, 0.08),
    "pessoas": (0.25, 0.42, 0.25, 0.08), "juridico": (0.40, 0.45, 0.15, 0.0),
}

EMPTY_SHARE = 0.15
SHORT_SHARE = 0.08
PII_SHARE = 0.15
NEAR_MISS_CASES = 12

CRITICAL_TYPES = {"am": 9, "as": 6, "di": 6, "bo": 8, "av": 4}
CRITICAL_EXPLICIT = {"am": 3, "as": 2, "di": 2, "bo": 3, "av": 1}
CRITICAL_MULTI = 1          # comments with two critical types
SELF_HARM_CASES = 2
CRITICAL_AREAS = {
    "atendimento": 11, "operacoes": 10, "transporte": 4, "comercial": 3,
    "tecnologia": 1, "financeiro": 1, "pessoas": 1, "juridico": 1,
}

# Minimum planted spans by type.
PII_TARGETS = {
    "PESSOA": 40, "LOCAL": 12, "EMAIL": 8, "TELEFONE": 8, "CPF": 6, "RG": 4, "CEP": 4,
    "MATRICULA": 6, "PLACA": 4,
}

SURVEY_START = date(2026, 10, 19)
SURVEY_END = date(2026, 10, 30)
UTC_OFFSET_HOURS = -3
# Relative volume per business day (launch peak, reminder on the second Monday) and per hour.
DAY_WEIGHTS = (0.17, 0.13, 0.11, 0.09, 0.08, 0.10, 0.08, 0.07, 0.07, 0.10)
HOUR_WEIGHTS = {8: 6, 9: 10, 10: 9, 11: 8, 12: 7, 13: 9, 14: 8, 15: 7, 16: 7, 17: 8, 18: 6}

SPLIT_TEST_SIZE = 100
SPLIT_QUICK_SIZE = 40


def score_group(score: int) -> str:
    """eNPS group: promoter (9-10), passive (7-8) or detractor (0-6)."""
    if score >= 9:
        return "pro"
    if score >= 7:
        return "pas"
    return "det"


def enps(scores: list[int]) -> int | None:
    """round(100 * (promoters - detractors) / n), or None for an empty group."""
    if not scores:
        return None
    promoters = sum(1 for s in scores if s >= 9)
    detractors = sum(1 for s in scores if s <= 6)
    return round(100 * (promoters - detractors) / len(scores))
