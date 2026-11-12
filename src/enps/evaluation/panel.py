"""Check the blind model panel from its stored readings (data/labels/panel/readings.json).

The panel labels were made outside this code: three independent readings (A, B, C) of the blind labeling sheet by a
language model, see docs/evaluation.md. This module recomputes from the stored readings what the README and the docs
report about them: how often the readings agreed (unanimity and Fleiss' kappa), and that the final labels are the
majority vote (2 of 3) for themes, sentiment, the critical flag and the critical types. The doubt flag is not voted:
a comment is marked as doubtful when any reading had a doubt.
"""
from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


def fleiss_kappa(rows: Sequence[Sequence[str]]) -> float | None:
    """Fleiss' kappa for items rated by the same number of readers; None when it is undefined."""
    if not rows:
        return None
    n = len(rows[0])
    if n < 2 or any(len(r) != n for r in rows):
        raise ValueError("every item needs the same number of ratings, at least 2")
    categories = sorted({c for r in rows for c in r})
    total = len(rows) * n
    p_j = {c: sum(r.count(c) for r in rows) / total for c in categories}
    p_i = [(sum(r.count(c) ** 2 for c in categories) - n) / (n * (n - 1)) for r in rows]
    p_bar = sum(p_i) / len(p_i)
    p_e = sum(v * v for v in p_j.values())
    if p_e >= 1:
        return None
    return (p_bar - p_e) / (1 - p_e)


def majority(readings: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Majority vote of one comment's readings: label sets and values chosen by more than half of them."""
    need = len(readings) // 2 + 1
    sentiments = Counter(r["sentimento"] for r in readings)
    sentiment = next((s for s, c in sentiments.items() if c >= need), None)
    critical = sum(bool(r["critico"]) for r in readings) >= need
    themes = Counter(t for r in readings for t in set(r["temas"]))
    chosen = {t for t, c in themes.items() if c >= need}
    if "Outro" in chosen and len(chosen) > 1:
        chosen.discard("Outro")
    types = Counter(t for r in readings if r["critico"] for t in set(r["tipos"]))
    return {
        "temas": chosen,
        "sentimento": sentiment,
        "critico": critical,
        "tipos": {t for t, c in types.items() if c >= need} if critical else set(),
        "duvida": any(r.get("duvida") for r in readings),
    }


def summary(path: Path) -> dict[str, Any]:
    """Agreement between the readings and a check of the final labels against the vote."""
    data = json.loads(path.read_text(encoding="utf-8"))
    readers = sorted(data["readings"])
    final = data["final"]
    ids = sorted(final)
    for reader in readers:
        if sorted(data["readings"][reader]) != ids:
            raise ValueError(f"reader {reader} does not cover the same comments as the final labels")
    rows = {i: [data["readings"][r][i] for r in readers] for i in ids}
    sentiment_rows = [[x["sentimento"] for x in rows[i]] for i in ids]
    critical_rows = [[str(bool(x["critico"])) for x in rows[i]] for i in ids]
    mismatches = []
    for i in ids:
        vote, label = majority(rows[i]), final[i]
        if label.get("settled_by", "majority") != "majority":
            continue  # decided by the extra reading, not by the vote
        same = (vote["temas"] == set(label["temas"]) and vote["sentimento"] == label["sentimento"]
                and vote["critico"] == bool(label["critico"]) and vote["tipos"] == set(label["tipos"])
                and vote["duvida"] == bool(label["duvida"]))
        if not same:
            mismatches.append(i)

    def kappa(values: Sequence[Sequence[str]]) -> float | None:
        k = fleiss_kappa(values)
        return None if k is None else round(k, 3)

    return {
        "n": len(ids),
        "readers": readers,
        "unanimous": {"sentiment": sum(len(set(r)) == 1 for r in sentiment_rows),
                      "critical": sum(len(set(r)) == 1 for r in critical_rows)},
        "fleiss_kappa": {"sentiment": kappa(sentiment_rows), "critical": kappa(critical_rows)},
        "settled_by": dict(Counter(final[i].get("settled_by", "majority") for i in ids)),
        "final_matches_vote": not mismatches,
        "vote_mismatches": mismatches,
    }
