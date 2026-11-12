// eNPS (port of src/enps/metrics/enps.py): promoters score 9 or 10, passives 7 or 8,
// detractors 0 to 6.

import { pyRound, roundHalfAway } from "./numbers.js";

export function category(score) {
  if (!(score >= 0 && score <= 10)) throw new RangeError(`score out of range: ${score}`);
  if (score >= 9) return "promoter";
  return score >= 7 ? "passive" : "detractor";
}

/** round(100 * (promoters - detractors) / n), halves away from zero; null for an empty group. */
export function enpsValue(promoters, detractors, n) {
  if (n === 0) return null;
  return roundHalfAway(100 * (promoters - detractors), n);
}

function pct(part, n) {
  return n ? pyRound((100 * part) / n, 1) : null;
}

/** n, eNPS and the three groups with their shares (in percent, one decimal). */
export function summarize(scores) {
  const counts = { promoter: 0, passive: 0, detractor: 0 };
  for (const score of scores) counts[category(score)] += 1;
  const { promoter: promoters, passive: passives, detractor: detractors } = counts;
  const n = promoters + passives + detractors;
  return {
    n,
    enps: enpsValue(promoters, detractors, n),
    promoters,
    passives,
    detractors,
    pct_promoters: pct(promoters, n),
    pct_passives: pct(passives, n),
    pct_detractors: pct(detractors, n),
  };
}
