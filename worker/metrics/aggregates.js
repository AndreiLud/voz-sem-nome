// Dashboard JSON of the public dataset, built from D1 rows under the minimum group rule
// (port of src/enps/metrics/aggregates.py; same keys, numbers, order and suppression).
//
// Rows are submissions as the Worker reads them from D1, ordered by id:
//   SELECT public_id, created_at, score, area, tenure, comment_anon, status, themes, sentiment,
//          critical, critical_types, finished_at, model, prompt_version, anonymizer_version
//     FROM submissions ORDER BY id
// themes and critical_types are JSON text (arrays are accepted too). A row counts as classified
// when its status is "done" or "review" and themes is set, which is the Python rule "status done
// or review with a classification row". finished_at, model and prompt_version feed the pipeline
// block (the latest classification), anonymizer_version the anonymizer of the latest row.

import { DASHBOARD_CONFIG, MAX_CRITICAL_CASES } from "./data.js";
import { summarize } from "./enps.js";
import { publicExcerpt } from "./excerpt.js";
import { cellKey, partitionGroups, suppressCells } from "./suppression.js";
import { spDay, toIso } from "./time.js";

const FINISHED = new Set(["done", "review"]);
const TOTAL_KEYS = [
  "responses", "with_comment", "enps", "promoters", "passives", "detractors",
  "pct_promoters", "pct_passives", "pct_detractors", "critical",
];

function parseList(value) {
  if (value == null) return [];
  return Array.isArray(value) ? value : JSON.parse(value);
}

/** One D1 row as the record the aggregation works on (aggregates.load_records). */
function toRecord(row) {
  const classified = FINISHED.has(row.status) && row.themes != null;
  return {
    publicId: row.public_id,
    score: row.score,
    area: row.area,
    tenure: row.tenure,
    comment: row.comment_anon ?? "",
    submittedAt: row.created_at,
    classified,
    themes: classified ? parseList(row.themes) : [],
    sentiment: classified ? (row.sentiment ?? null) : null,
    critical: classified && Boolean(row.critical),
    criticalTypes: classified ? parseList(row.critical_types) : [],
  };
}

/** Configured codes first, then any unexpected code present in the data. */
function codes(configured, found) {
  return [...new Set([...configured, ...found])];
}

function tally(values) {
  const counts = new Map();
  for (const value of values) counts.set(value, (counts.get(value) ?? 0) + 1);
  return counts;
}

function pick(counts, keys) {
  return Object.fromEntries(keys.map((key) => [key, counts.get(key) ?? 0]));
}

const criticalCount = (records) => records.filter((r) => r.critical).length;

function groupStats(records, cfg) {
  const summary = summarize(records.map((r) => r.score));
  const classified = records.filter((r) => r.classified);
  return {
    n: summary.n,
    enps: summary.enps,
    promoters: summary.promoters,
    passives: summary.passives,
    detractors: summary.detractors,
    themes: pick(tally(classified.flatMap((r) => r.themes)), cfg.themes),
    sentiment: pick(tally(classified.map((r) => r.sentiment).filter(Boolean)), cfg.sentiments),
    critical: criticalCount(records),
  };
}

function dimension(records, attr, dimensionCodes, cfg) {
  const groups = new Map(dimensionCodes.map((code) => [code, []]));
  for (const r of records) groups.get(r[attr]).push(r);
  const part = partitionGroups(
    new Map([...groups].map(([code, group]) => [code, group.length])), cfg.min_group_size,
  );
  const listed = part.visible.map((code) => ({ code, ...groupStats(groups.get(code), cfg) }));
  let other = null;
  if (part.merged.length > 0 && part.otherShown) {
    other = { codes: null, ...groupStats(part.merged.flatMap((code) => groups.get(code)), cfg) };
  }
  return { listed, other, part };
}

function areaTenure(records, areas, tenures, areaPart, tenurePart, k) {
  const cells = new Map();
  for (const r of records) {
    const key = cellKey(r.area, r.tenure);
    if (!cells.has(key)) cells.set(key, []);
    cells.get(key).push(r.score);
  }
  const sizes = new Map([...cells].map(([key, scores]) => [key, scores.length]));
  const suppressed = suppressCells(sizes, areas, tenures, k, {
    hiddenRows: areas.filter((a) => !areaPart.visible.includes(a)),
    hiddenCols: tenures.filter((t) => !tenurePart.visible.includes(t)),
  });
  const out = [];
  for (const area of areas) {
    for (const tenure of tenures) {
      const key = cellKey(area, tenure);
      if (suppressed.has(key)) {
        out.push({ area, tenure, suppressed: true });
      } else {
        const summary = summarize(cells.get(key) ?? []);
        out.push({ area, tenure, n: summary.n, enps: summary.enps });
      }
    }
  }
  return out;
}

function totals(records, k) {
  if (records.length < k) return Object.fromEntries(TOTAL_KEYS.map((key) => [key, null]));
  const summary = summarize(records.map((r) => r.score));
  return {
    responses: summary.n,
    with_comment: records.filter((r) => r.comment).length,
    ...Object.fromEntries(TOTAL_KEYS.slice(2, -1).map((key) => [key, summary[key]])),
    critical: criticalCount(records),
  };
}

function themes(records, cfg) {
  const classified = records.filter((r) => r.classified);
  return cfg.themes.map((code) => {
    const withTheme = classified.filter((r) => r.themes.includes(code));
    return {
      code,
      n: withTheme.length,
      sentiment: pick(tally(withTheme.map((r) => r.sentiment).filter(Boolean)), cfg.sentiments),
      critical: criticalCount(withTheme),
    };
  });
}

function byTextDesc(a, b) {
  if (a === b) return 0;
  return a < b ? 1 : -1;
}

function critical(records, visibleAreas, cfg) {
  const visible = new Set(visibleAreas);
  const cases = records.filter((r) => r.critical);
  // Most recent first; Array.prototype.sort is stable, so equal timestamps keep row order,
  // like Python's sorted(..., reverse=True).
  const recent = [...cases]
    .sort((a, b) => byTextDesc(a.submittedAt, b.submittedAt))
    .slice(0, MAX_CRITICAL_CASES);
  return {
    total: cases.length,
    by_type: pick(tally(cases.flatMap((r) => r.criticalTypes)), cfg.critical_types),
    cases: recent.map((r) => ({
      id: r.publicId,
      types: [...r.criticalTypes],
      themes: [...r.themes],
      area: visible.has(r.area) ? r.area : null,
      excerpt: publicExcerpt(r.comment, cfg.display_excerpt_chars),
      date: spDay(r.submittedAt),
    })),
  };
}

/** Model and versions of the latest classification and anonymization (aggregates.pipeline_info). */
export function pipelineInfo(rows) {
  let latest = null;
  let latestAt = null;
  let anonymizer = null;
  for (const row of rows) {
    // ORDER BY classification time DESC, id DESC: the last row with the greatest time wins.
    if (row.themes != null && row.model != null) {
      const at = row.finished_at ?? "";
      if (latest === null || at >= latestAt) {
        latest = row;
        latestAt = at;
      }
    }
    if (row.anonymizer_version) anonymizer = row.anonymizer_version;
  }
  return {
    model: latest ? latest.model : null,
    quantization: latest ? (latest.quantization ?? null) : null,
    prompt_version: latest ? (latest.prompt_version ?? null) : null,
    anonymizer_version: anonymizer,
  };
}

function settings(cfg) {
  return { ...DASHBOARD_CONFIG, ...cfg };
}

/**
 * Areas listed by name on the dashboard right now (aggregates.visible_area_codes), for the
 * area of an alert. `areaCounts` are rows of SELECT area, count(*) AS n ... GROUP BY area.
 */
export function visibleAreaCodes(areaCounts, { cfg = {} } = {}) {
  const conf = settings(cfg);
  const found = new Map(areaCounts.map((row) => [row.area, row.n]));
  const all = codes(conf.areas, found.keys());
  const part = partitionGroups(new Map(all.map((code) => [code, found.get(code) ?? 0])), conf.min_group_size);
  return new Set(part.visible);
}

/**
 * The dashboard JSON of the contract in the build spec.
 * @param {object[]} rows D1 submissions, ordered by id
 * @param {{dataset?: string, now?: Date|string|number, cfg?: object}} options cfg overrides
 *   fields of DASHBOARD_CONFIG (min_group_size, display_excerpt_chars, code lists)
 */
export function buildDashboard(rows, { dataset = "public", now = new Date(), cfg = {} } = {}) {
  const conf = settings(cfg);
  const k = conf.min_group_size;
  const records = rows.map(toRecord);
  const areas = codes(conf.areas, records.map((r) => r.area));
  const tenures = codes(conf.tenures, records.map((r) => r.tenure));
  const byArea = dimension(records, "area", areas, conf);
  const byTenure = dimension(records, "tenure", tenures, conf);
  return {
    dataset,
    generated_at: toIso(now),
    min_group_size: k,
    totals: totals(records, k),
    by_area: byArea.listed,
    other_areas: byArea.other,
    by_tenure: byTenure.listed,
    other_tenures: byTenure.other,
    area_tenure: areaTenure(records, areas, tenures, byArea.part, byTenure.part, k),
    themes: themes(records, conf),
    critical: critical(records, byArea.part.visible, conf),
    pipeline: pipelineInfo(rows),
  };
}
