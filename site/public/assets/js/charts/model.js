// Data shaping for the dashboard: dashboard JSON (see the contract in the project spec) and
// /status in, rows for the charts out. Pure functions, no DOM: the tests import this file in Node.
import { heatScale } from "./scale.js";

export const DATASETS = ["synthetic", "public"];
export const DEFAULT_DATASET = "synthetic";
export const OTHER_KEY = "__other";
export const SENTIMENT_ORDER = ["neg", "neu", "pos"];
export const SEGMENT_ORDER = ["promoters", "passives", "detractors"];

const num = (value) => (typeof value === "number" && Number.isFinite(value) ? value : null);

// ------------------------------------------------------------------ dataset in the URL

/** Dataset named by the query string: ?base=sintetica|publica (pt), ?dataset=synthetic|public (en). */
export function datasetFromSearch(search, lang, query) {
  const spec = query && query.dataset;
  if (!spec) return DEFAULT_DATASET;
  const params = new URLSearchParams(search);
  const names = [spec.param[lang], ...Object.values(spec.param)];
  const raw = names.map((name) => params.get(name)).find((value) => value !== null);
  if (!raw) return DEFAULT_DATASET;
  const value = raw.trim().toLowerCase();
  const key = DATASETS.find((code) => code === value || Object.values(spec.values[code] || {}).includes(value));
  return key || DEFAULT_DATASET;
}

/** Query string with the dataset set in this language's words (other parameters kept). */
export function searchWithDataset(search, dataset, lang, query) {
  const spec = query.dataset;
  const params = new URLSearchParams(search);
  for (const name of Object.values(spec.param)) params.delete(name);
  params.set(spec.param[lang], spec.values[dataset][lang]);
  return `?${params.toString()}`;
}

// ------------------------------------------------------------------ whole dashboard

/** Loose check that a response follows the dashboard contract. */
export function isDashboard(data) {
  return Boolean(data) && typeof data === "object" && DATASETS.includes(data.dataset)
    && Boolean(data.totals) && typeof data.totals === "object";
}

/** Below the minimum group size the totals are suppressed (null) and nothing is shown. */
export function isSmall(data, k) {
  const n = num(data.totals.responses);
  return n === null || n < (num(data.min_group_size) ?? k);
}

/** Everything except generated_at, to tell whether a refresh changed what is drawn. */
export function signature(data) {
  return JSON.stringify({ ...data, generated_at: null });
}

/** Promoters, passives and detractors of the totals as parts of one whole. */
export function overviewParts(totals) {
  const n = num(totals.responses) || 0;
  return SEGMENT_ORDER.map((key) => {
    const count = num(totals[key]) || 0;
    const pct = num(totals[`pct_${key}`]); // percent with one decimal, per the contract
    return { key, count, share: pct !== null ? pct / 100 : (n ? count / n : 0) };
  });
}

// ------------------------------------------------------------------ eNPS by group

/**
 * Rows of a group chart: visible groups, then the merged group (if any) last.
 * `label(code)` names a group; `otherLabel` names the merged one.
 */
export function groupRows(list, other, { label, otherLabel, sort }) {
  const rows = (list || [])
    .filter((g) => num(g.enps) !== null && num(g.n))
    .map((g) => groupRow(g, g.code, label(g.code), false));
  if (sort) rows.sort((a, b) => b.enps - a.enps || b.n - a.n || a.label.localeCompare(b.label));
  if (other && num(other.n) && num(other.enps) !== null) rows.push(groupRow(other, OTHER_KEY, otherLabel, true));
  return rows;
}

function groupRow(g, key, label, merged) {
  return {
    key, label, merged,
    n: g.n, enps: g.enps,
    promoters: num(g.promoters) || 0, passives: num(g.passives) || 0, detractors: num(g.detractors) || 0,
    critical: num(g.critical) || 0,
  };
}

/** Highest and lowest named group, for the one-line summary; null with fewer than two. */
export function extremes(rows) {
  const named = rows.filter((row) => !row.merged);
  if (named.length < 2) return null;
  const sorted = [...named].sort((a, b) => b.enps - a.enps);
  return { top: sorted[0], bottom: sorted[sorted.length - 1] };
}

// ------------------------------------------------------------------ themes

/** Themes mentioned at least once, most mentioned first, with the sentiment split. */
export function themeRows(themes, label) {
  return (themes || [])
    .filter((t) => num(t.n))
    .map((t, index) => {
      const sentiment = Object.fromEntries(SENTIMENT_ORDER.map((s) => [s, num(t.sentiment && t.sentiment[s]) || 0]));
      const classified = SENTIMENT_ORDER.reduce((sum, s) => sum + sentiment[s], 0);
      return { key: t.code, label: label(t.code), n: t.n, sentiment, classified, critical: num(t.critical) || 0, index };
    })
    .sort((a, b) => b.n - a.n || a.index - b.index);
}

/** Codes of themes nobody mentioned. */
export function unmentionedThemes(themes) {
  return (themes || []).filter((t) => !num(t.n)).map((t) => t.code);
}

// ------------------------------------------------------------------ themes by area

/**
 * Share of each area's classified comments that mention each theme.
 * Columns are the visible areas (in the order of the data) and the merged group; an area
 * without classified comments has no denominator and is listed in `empty` instead.
 */
export function heatMatrix(data, themeCodes, { label, otherLabel }) {
  const groups = (data.by_area || []).map((a) => ({ key: a.code, label: label(a.code), group: a }));
  if (data.other_areas && num(data.other_areas.n)) {
    groups.push({ key: OTHER_KEY, label: otherLabel, group: data.other_areas });
  }
  const columns = [];
  const empty = [];
  for (const { key, label: name, group } of groups) {
    const total = SENTIMENT_ORDER.reduce((sum, s) => sum + (num(group.sentiment && group.sentiment[s]) || 0), 0);
    if (total > 0) columns.push({ key, label: name, total, themes: group.themes || {} });
    else empty.push(name);
  }
  let max = 0;
  const rows = themeCodes.map((code) => ({
    key: code,
    cells: columns.map((col) => {
      const n = num(col.themes[code]) || 0;
      const share = n / col.total;
      max = Math.max(max, share);
      return { column: col.key, n, total: col.total, share };
    }),
  }));
  return { columns, rows, empty, max, scale: heatScale(max) };
}

/** Cell with the highest share (first one on ties), or null when every cell is zero. */
export function heatPeak(matrix) {
  let best = null;
  matrix.rows.forEach((row, r) => row.cells.forEach((cell, c) => {
    if (cell.share > 0 && (!best || cell.share > best.cell.share)) best = { row: r, column: c, cell };
  }));
  return best;
}

// ------------------------------------------------------------------ critical cases

/** Count per critical type in the configured order, zeros included. */
export function criticalByType(critical, codes) {
  const byType = (critical && critical.by_type) || {};
  return codes.map((code) => ({ code, n: num(byType[code]) || 0 }));
}

// ------------------------------------------------------------------ live demo status

const STATUS_KEYS = ["analyzed_today", "latency_p50_s", "latency_p95_s", "critical_today", "alerts_recorded",
  "quota_exhausted_today", "pending", "queue", "production"];

/**
 * GET /status in one shape. The live demo's Worker sends flat fields: {pending, processing,
 * eta_seconds, analyzed_today, latency_p50_s, latency_p95_s, critical_today, alerts_recorded,
 * quota_exhausted_today, production}. The local mode's API nests a queue and has no daily
 * numbers; those come out as null and the page says "no data".
 */
export function normalizeStatus(raw) {
  if (!raw || typeof raw !== "object" || !STATUS_KEYS.some((key) => key in raw)) return null;
  return {
    analyzedToday: num(raw.analyzed_today),
    latencyP50: num(raw.latency_p50_s),
    latencyP95: num(raw.latency_p95_s),
    criticalToday: num(raw.critical_today),
    alertsRecorded: num(raw.alerts_recorded),
    quotaExhausted: raw.quota_exhausted_today === true,
    production: raw.production && typeof raw.production === "object" ? raw.production : null,
  };
}
