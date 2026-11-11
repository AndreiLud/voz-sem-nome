"""Plain-text summary of a generated dataset, printed by the CLI."""
from __future__ import annotations

from collections import Counter

from enps import config
from enps.anonymize import PII_TYPES

from .generator import Dataset, Record
from .story import AREA_SIZES, TARGET_ENPS, enps


def _line(label: str, counts: Counter[str], order: list[str]) -> str:
    return f"{label:<14}" + "  ".join(f"{k} {counts[k]}" for k in order if counts[k])


def _group(records: list[Record], key: str) -> dict[str, list[Record]]:
    groups: dict[str, list[Record]] = {}
    for rec in records:
        groups.setdefault(getattr(rec.respondent, key), []).append(rec)
    return groups


def summarize(ds: Dataset) -> str:
    recs = ds.records
    with_comment = [r for r in recs if r.comment.text]
    lines = [f"Synthetic survey, seed {ds.seed}: {len(recs)} answers, {len(with_comment)} with a comment, "
             f"eNPS {enps([r.respondent.score for r in recs])}"]

    lines.append("\nArea          n    eNPS  target  comments  critical")
    by_area = _group(recs, "area")
    for area in AREA_SIZES:
        group = by_area.get(area, [])
        crit = sum(1 for r in group if r.comment.critical_types)
        lines.append(f"{area:<12} {len(group):>3}  {enps([r.respondent.score for r in group]):>+5}  "
                     f"{TARGET_ENPS[area]:>+6}  {sum(1 for r in group if r.comment.text):>8}  {crit:>8}")

    lines.append("\nTenure        n    eNPS")
    by_tenure = _group(recs, "tenure")
    for tenure in config.tenure_codes():
        group = by_tenure.get(tenure, [])
        lines.append(f"{tenure:<12} {len(group):>3}  {enps([r.respondent.score for r in group]):>+5}")

    kinds = Counter(r.respondent.kind for r in recs)
    lines.append("\n" + _line("Kinds", kinds, ["regular", "empty", "short", "critical", "pii", "decoy",
                                               "near_miss", "rant"]))
    lines.append(_line("Themes", Counter(t for r in recs for t in r.comment.themes), config.theme_codes()))
    lines.append(_line("Sentiment", Counter(r.comment.sentiment for r in with_comment),
                       config.sentiment_codes()))
    critical = [r for r in recs if r.comment.critical_types]
    subtle = sum(1 for r in critical if r.comment.subtle)
    lines.append(_line("Critical", Counter(c for r in critical for c in r.comment.critical_types),
                       config.critical_codes())
                 + f"  | comments {len(critical)}, subtle {subtle} ({subtle / max(len(critical), 1):.0%})")
    spans = Counter(str(s["type"]) for r in recs for s in r.comment.spans)
    with_pii = sum(1 for r in recs if r.comment.spans)
    lines.append(_line("PII spans", spans, [t for t in PII_TYPES if spans[t]])
                 + f"  | {sum(spans.values())} spans in {with_pii} comments ({with_pii / len(recs):.0%})")
    lines.append(_line("Traits", Counter(t for r in recs for t in r.comment.traits),
                       sorted({t for r in recs for t in r.comment.traits})))

    used = sum(1 for n in ds.reuse.values() if n)
    distinct = len({r.comment.text for r in with_comment})
    lines.append(f"\nFragments used {used}; distinct comments {distinct} of {len(with_comment)}")

    lines.append("\nSplit        n  critical  near_miss  pii  short  min theme")
    by_id = {r.id: r for r in recs}
    for name in ("test", "dev", "dev_quick"):
        group = [by_id[i] for i in ds.splits[name]]
        themes = Counter(t for r in group for t in r.comment.themes)
        lines.append(f"{name:<10} {len(group):>3}  {sum(1 for r in group if r.comment.critical_types):>8}  "
                     f"{sum(1 for r in group if r.respondent.kind == 'near_miss'):>9}  "
                     f"{sum(1 for r in group if r.comment.spans):>3}  "
                     f"{sum(1 for r in group if r.respondent.kind == 'short'):>5}  "
                     f"{min(themes[t] for t in config.theme_codes()):>9}")
    return "\n".join(lines)

