// Production numbers of the public demo for GET /api/v1/public/status (port of
// production_stats in src/enps/metrics/production.py, same keys and the same rounding).
//
// Inputs are plain rows the Worker reads from D1:
//   daily:     SELECT day, key, value FROM daily
//   latencies: SELECT created_at, finished_at FROM submissions
//                WHERE status IN ('done', 'review') AND finished_at IS NOT NULL
//              (numbers are taken as milliseconds from submission to result)
//   alerts:    rows {submitted_at, delivered_at}: when the answer was submitted and when the
//              alert was recorded or delivered (alias the columns in SQL); a null delivered_at
//              is not counted

import { pyRound, pySum } from "./numbers.js";
import { msBetween } from "./time.js";

/** Nearest-rank percentile (q in 0..100); null for no values. */
export function percentile(values, q) {
  if (values.length === 0) return null;
  const ordered = [...values].sort((a, b) => a - b);
  const rank = Math.max(1, Math.ceil((q / 100) * ordered.length));
  return ordered[rank - 1];
}

function seconds(ms) {
  return ms === null ? null : pyRound(ms / 1000, 1);
}

function dailySum(daily, key) {
  return daily.reduce((sum, row) => (row.key === key ? sum + Number(row.value ?? 0) : sum), 0);
}

function firstDay(daily) {
  const days = daily.filter((row) => row.key === "submissions" && row.value > 0).map((row) => row.day);
  return days.length > 0 ? days.reduce((a, b) => (b < a ? b : a)) : null;
}

export function productionStats({ daily = [], latencies = [], alerts = [] } = {}) {
  const latencyMs = latencies.map((x) => (typeof x === "number" ? x : msBetween(x.created_at, x.finished_at)));
  const sent = alerts.filter((a) => a.delivered_at != null);
  const delays = sent.map((a) => msBetween(a.submitted_at, a.delivered_at) / 1000);
  return {
    total_submissions: dailySum(daily, "submissions"),
    distinct_submitters: dailySum(daily, "distinct_submitters"),
    classified: dailySum(daily, "classified"),
    latency_p50_seconds: seconds(percentile(latencyMs, 50)),
    latency_p95_seconds: seconds(percentile(latencyMs, 95)),
    alerts_sent: sent.length,
    mean_submission_to_alert_seconds: delays.length > 0 ? pyRound(pySum(delays) / delays.length, 1) : null,
    first_public_day: firstDay(daily),
  };
}
