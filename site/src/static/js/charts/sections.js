// Content of each dashboard panel, built from the dashboard JSON with the chart modules.
// ctx: {t, lang, k, hintId, label: {area, tenure, theme, sentiment, critical}, placeholders,
//       openTables: Set of table keys to render open, casesExpanded: boolean,
//       excerptLang: lang attribute for the excerpts, or null for the page language}.
import { anonymizedText, criticalBadge, h, icon } from "../lib/dom.js";
import { fmtDuration, fmtNumber, fmtPercent, fmtSigned } from "../lib/format.js";
import { divergingBars, segmentedBar, stackedBars } from "./bars.js";
import { heatLegend, heatmap } from "./heatmap.js";
import {
  SENTIMENT_ORDER, criticalByType, extremes, groupRows, heatMatrix, heatPeak,
  overviewParts, themeRows, unmentionedThemes,
} from "./model.js";
import { legend, summaryLine, tableView } from "./parts.js";

const CASES_SHOWN = 4;

/** "1 resposta" / "12 respostas", "1 comment" / "3 comments". */
export function counted(ctx, unit, n) {
  return ctx.t(`dashboard.units.${unit}_${n === 1 ? "one" : "other"}`, { n: fmtNumber(n, ctx.lang) });
}

const signed = (ctx, value) => fmtSigned(value, ctx.lang);
const share0 = (ctx, value) => fmtPercent(value, ctx.lang);
const share1 = (ctx, value) => fmtPercent(value, ctx.lang, 1);

function viz(...children) {
  return h("div", { class: "viz" }, ...children);
}

function table(ctx, key, caption, columns, rows) {
  return tableView({
    key, summary: ctx.t("dashboard.show_table"), caption, columns, rows, open: ctx.openTables.has(key),
  });
}

// ------------------------------------------------------------------ overview

export function overview(data, ctx) {
  const totals = data.totals;
  const parts = overviewParts(totals).map((part) => ({ ...part, label: ctx.t(`dashboard.labels.${part.key}`) }));
  const bar = segmentedBar({
    parts,
    name: ctx.t("dashboard.charts.overview"),
    hintId: ctx.hintId,
    text: {
      label: (p) => ctx.t("dashboard.marks.part", { name: p.label, count: counted(ctx, "answers", p.count), share: share1(ctx, p.share) }),
      tip: (p) => ({ title: p.label, value: share1(ctx, p.share), unit: ctx.t("dashboard.tips.of_answers"), note: counted(ctx, "answers", p.count) }),
    },
  });
  const key = parts.map((p) => ({ key: p.key, label: p.label, extra: `${share1(ctx, p.share)} (${fmtNumber(p.count, ctx.lang)})` }));
  const kpis = h("dl", { class: "ov__kpis" },
    kpi(ctx.t("dashboard.labels.with_comment"), fmtNumber(totals.with_comment ?? 0, ctx.lang)),
    kpi(ctx.t("dashboard.labels.critical"), fmtNumber(totals.critical ?? 0, ctx.lang)));
  return [
    h("div", { class: "ov" },
      h("div", { class: "ov__hero" },
        h("p", { class: "ov__label" }, ctx.t("dashboard.labels.enps")),
        h("p", { class: "hero-number" }, signed(ctx, totals.enps)),
        h("p", { class: "muted" }, counted(ctx, "answers", totals.responses))),
      h("div", { class: "ov__split" }, viz(bar), legend(key, ctx.t("dashboard.overview_legend"))),
      kpis),
    table(ctx, "overview", ctx.t("dashboard.sections.overview"),
      [{ label: ctx.t("dashboard.labels.group") }, { label: ctx.t("dashboard.labels.responses"), num: true },
        { label: ctx.t("dashboard.labels.share"), num: true }],
      parts.map((p) => ({ cells: [p.label, fmtNumber(p.count, ctx.lang), share1(ctx, p.share)] }))),
  ];
}

function kpi(label, value) {
  return h("div", { class: "ov__kpi" }, h("dt", {}, label), h("dd", {}, value));
}

// ------------------------------------------------------------------ eNPS by group

/** Rows of the area and tenure charts, so both can share one axis. */
export function groupData(data, ctx) {
  return {
    areas: groupRows(data.by_area, data.other_areas, {
      label: ctx.label.area, otherLabel: ctx.t("dashboard.other_areas"), sort: true,
    }),
    tenures: groupRows(data.by_tenure, data.other_tenures, {
      label: ctx.label.tenure, otherLabel: ctx.t("dashboard.other_tenures"), sort: false,
    }),
  };
}

export function groups(rows, domain, ctx, { key, chartName, caption, firstColumn, mergedNote }) {
  if (!rows.length) return [h("p", { class: "muted" }, ctx.t("dashboard.groups_none"))];
  const ends = extremes(rows);
  const summary = ends && ctx.t("dashboard.summaries.groups", {
    top: ends.top.label, top_value: signed(ctx, ends.top.enps),
    bottom: ends.bottom.label, bottom_value: signed(ctx, ends.bottom.enps),
  });
  const chart = divergingBars({
    rows, domain, name: chartName, hintId: ctx.hintId,
    text: {
      value: (r) => signed(ctx, r.enps),
      sub: (r) => counted(ctx, "answers", r.n),
      tick: (v) => signed(ctx, v),
      label: (r) => ctx.t("dashboard.marks.group", { name: r.label, value: signed(ctx, r.enps), answers: counted(ctx, "answers", r.n) }),
      tip: (r) => ({
        title: r.label, value: signed(ctx, r.enps), unit: ctx.t("dashboard.labels.enps"),
        note: `${counted(ctx, "answers", r.n)}. ${ctx.t("dashboard.tips.split", {
          promoters: fmtNumber(r.promoters, ctx.lang), passives: fmtNumber(r.passives, ctx.lang),
          detractors: fmtNumber(r.detractors, ctx.lang),
        })}.`,
      }),
    },
  });
  const merged = rows.some((r) => r.merged) ? h("p", { class: "note viz-note" }, mergedNote) : null;
  return [
    summaryLine(summary),
    viz(chart),
    merged,
    table(ctx, key, caption,
      [firstColumn, { label: ctx.t("dashboard.labels.enps"), num: true }, { label: ctx.t("dashboard.labels.responses"), num: true },
        { label: ctx.t("dashboard.labels.promoters"), num: true }, { label: ctx.t("dashboard.labels.passives"), num: true },
        { label: ctx.t("dashboard.labels.detractors"), num: true }],
      rows.map((r) => ({
        cells: [r.label, signed(ctx, r.enps), fmtNumber(r.n, ctx.lang), fmtNumber(r.promoters, ctx.lang),
          fmtNumber(r.passives, ctx.lang), fmtNumber(r.detractors, ctx.lang)],
      }))),
  ];
}

// ------------------------------------------------------------------ themes

function sentimentSplit(ctx, row) {
  return SENTIMENT_ORDER.map((s) => ({
    key: s, label: ctx.label.sentiment(s), count: row.sentiment[s],
    share: row.classified ? row.sentiment[s] / row.classified : 0,
  }));
}

export function themes(data, ctx) {
  const rows = themeRows(data.themes, ctx.label.theme);
  if (!rows.length) return [h("p", { class: "muted" }, ctx.t("dashboard.themes_none"))];
  const top = rows[0];
  const chart = stackedBars({
    rows: rows.map((row) => ({ ...row, parts: sentimentSplit(ctx, row) })),
    name: ctx.t("dashboard.charts.themes"),
    hintId: ctx.hintId,
    text: {
      sub: (r) => counted(ctx, "comments", r.n),
      label: (r) => ctx.t("dashboard.marks.theme", {
        name: r.label, count: counted(ctx, "comments", r.n),
        split: r.parts.map((p) => `${p.label} ${share0(ctx, p.share)}`).join(", "),
      }),
      tip: (r) => ({
        title: r.label, value: fmtNumber(r.n, ctx.lang), unit: ctx.t("dashboard.labels.count").toLowerCase(),
        rows: r.parts.map((p) => ({ key: p.key, text: `${share0(ctx, p.share)} ${p.label.toLowerCase()} (${fmtNumber(p.count, ctx.lang)})` })),
      }),
    },
  });
  const unmentioned = unmentionedThemes(data.themes).map(ctx.label.theme);
  const sentimentNames = SENTIMENT_ORDER.map((s) => ({ key: s, label: ctx.label.sentiment(s) }));
  return [
    summaryLine(ctx.t("dashboard.summaries.themes", { theme: top.label, count: counted(ctx, "comments", top.n) })),
    legend(sentimentNames, ctx.t("dashboard.sentiment_legend")),
    viz(chart),
    unmentioned.length ? h("p", { class: "note viz-note" }, ctx.t("dashboard.themes_zero", { themes: unmentioned.join(", ") })) : null,
    table(ctx, "themes", ctx.t("dashboard.sections.themes"),
      [{ label: ctx.t("dashboard.labels.theme") }, { label: ctx.t("dashboard.labels.count"), num: true },
        ...sentimentNames.map((s) => ({ label: s.label, num: true })), { label: ctx.t("dashboard.labels.critical"), num: true }],
      rows.map((r) => ({
        cells: [r.label, fmtNumber(r.n, ctx.lang),
          ...sentimentSplit(ctx, r).map((p) => `${fmtNumber(p.count, ctx.lang)} (${share0(ctx, p.share)})`),
          fmtNumber(r.critical, ctx.lang)],
      }))),
  ];
}

// ------------------------------------------------------------------ themes by area

export function heat(data, ctx) {
  const codes = themeRows(data.themes, ctx.label.theme).map((r) => r.key);
  const matrix = heatMatrix(data, codes, { label: ctx.label.area, otherLabel: ctx.t("dashboard.other_areas") });
  if (!matrix.columns.length || !codes.length) return [h("p", { class: "muted" }, ctx.t("dashboard.themes_none"))];
  const peak = heatPeak(matrix);
  const summary = peak && ctx.t("dashboard.summaries.heatmap", {
    theme: ctx.label.theme(matrix.rows[peak.row].key), area: matrix.columns[peak.column].label,
    share: share0(ctx, peak.cell.share),
  });
  const ofComments = (cell) => ctx.t("dashboard.tips.heat", { n: fmtNumber(cell.n, ctx.lang), total: fmtNumber(cell.total, ctx.lang) });
  const chart = heatmap({
    matrix, rowLabel: ctx.label.theme, name: ctx.t("dashboard.charts.heatmap"), hintId: ctx.hintId,
    text: {
      cell: (cell) => share0(ctx, cell.share),
      label: (theme, col, cell) => ctx.t("dashboard.marks.heat", {
        theme, area: col.label, share: share0(ctx, cell.share), n: fmtNumber(cell.n, ctx.lang), total: fmtNumber(cell.total, ctx.lang),
      }),
      tip: (theme, col, cell) => ({ title: `${theme}, ${col.label}`, value: share0(ctx, cell.share), note: ofComments(cell) }),
    },
  });
  const scaleLegend = heatLegend(matrix.scale, {
    title: ctx.t("dashboard.heat_legend.title"),
    zero: ctx.t("dashboard.heat_legend.zero"),
    range: (lo, hi, first) => (first
      ? ctx.t("dashboard.heat_legend.first", { hi: share0(ctx, hi) })
      : ctx.t("dashboard.heat_legend.range", { lo: share0(ctx, lo), hi: share0(ctx, hi) })),
  });
  return [
    summaryLine(summary),
    scaleLegend,
    viz(chart),
    matrix.empty.length ? h("p", { class: "note viz-note" }, ctx.t("dashboard.heat_empty", { areas: matrix.empty.join(", ") })) : null,
    table(ctx, "heatmap", ctx.t("dashboard.sections.heatmap"),
      [{ label: ctx.t("dashboard.labels.theme") }, ...matrix.columns.map((col) => ({ label: col.label, num: true }))],
      matrix.rows.map((row) => ({
        cells: [ctx.label.theme(row.key), ...row.cells.map((cell) => `${share0(ctx, cell.share)} (${fmtNumber(cell.n, ctx.lang)})`)],
      }))),
  ];
}

// ------------------------------------------------------------------ critical cases

const dayFormats = new Map();
/** "2026-10-17" -> "17 de out. de 2026" / "Oct 17, 2026" (a calendar day, no time zone shift). */
export function fmtDay(day, lang) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(day || ""));
  if (!match) return "";
  if (!dayFormats.has(lang)) {
    dayFormats.set(lang, new Intl.DateTimeFormat(lang === "en" ? "en" : "pt-BR", { dateStyle: "medium", timeZone: "UTC" }));
  }
  return dayFormats.get(lang).format(new Date(Date.UTC(+match[1], +match[2] - 1, +match[3])));
}

function caseCard(item, ctx, visibleAreas) {
  const area = item.area && visibleAreas.has(item.area) ? ctx.label.area(item.area) : ctx.t("dashboard.critical_area_hidden");
  const themesText = (item.themes || []).map(ctx.label.theme).join(", ");
  return h("li", {}, h("article", { class: "crit-case" },
    h("div", { class: "crit-case__types" }, (item.types || []).map((code) => criticalBadge(ctx.label.critical(code)))),
    h("blockquote", { class: "crit-case__text", lang: ctx.excerptLang },
      h("p", {}, anonymizedText(item.excerpt || "", ctx.placeholders))),
    h("p", { class: "crit-case__meta" },
      h("span", {}, fmtDay(item.date, ctx.lang)),
      h("span", {}, area),
      themesText ? h("span", {}, ctx.t("dashboard.critical_themes", { themes: themesText })) : null)));
}

export function critical(data, ctx, onToggle) {
  const crit = data.critical || { total: 0, by_type: {}, cases: [] };
  const types = criticalByType(crit, ctx.criticalCodes);
  const typeList = h("ul", { class: "crit-types" }, types.map((item) => h("li", { class: item.n ? null : "is-zero" },
    item.n ? criticalBadge(ctx.label.critical(item.code))
      : h("span", { class: "badge badge--quiet" }, icon("alert"), h("span", {}, ctx.label.critical(item.code))),
    h("strong", { class: "crit-types__n" }, fmtNumber(item.n, ctx.lang)))));
  const out = [h("h3", { class: "viz-subhead" }, ctx.t("dashboard.critical_types")), typeList];
  const cases = crit.cases || [];
  if (!cases.length) {
    out.push(h("p", { class: "muted" }, ctx.t("dashboard.critical_empty")));
    return out;
  }
  const visibleAreas = new Set((data.by_area || []).map((a) => a.code));
  const shown = ctx.casesExpanded ? cases : cases.slice(0, CASES_SHOWN);
  out.push(h("h3", { class: "viz-subhead" }, ctx.t("dashboard.critical_recent")),
    h("ol", { class: "crit-cases", id: "crit-cases" }, shown.map((item) => caseCard(item, ctx, visibleAreas))));
  if (cases.length > CASES_SHOWN) {
    const button = h("button", { class: "btn btn--ghost btn--small", type: "button", "aria-expanded": String(ctx.casesExpanded), "aria-controls": "crit-cases" },
      ctx.casesExpanded ? ctx.t("dashboard.critical_show_less") : ctx.t("dashboard.critical_show_all", { n: fmtNumber(cases.length, ctx.lang) }));
    button.addEventListener("click", onToggle);
    out.push(h("p", {}, button));
  }
  return out;
}

// ------------------------------------------------------------------ live demo status

function tile(label, value) {
  return h("div", { class: "stat dash-stat" }, h("p", { class: "stat__label" }, label), h("p", { class: "stat__value" }, value));
}

const seconds = (ctx, value) => (typeof value === "number" ? fmtDuration(value, ctx.lang) : ctx.t("dashboard.live.none"));
const count = (ctx, value) => (typeof value === "number" ? fmtNumber(value, ctx.lang) : ctx.t("dashboard.live.none"));

/** Today's numbers and the quota notice always; production tiles only when `withProduction`. */
export function liveStatus(status, ctx, { withProduction }) {
  if (!status) return [h("p", { class: "muted" }, ctx.t("dashboard.live.error"))];
  const out = [];
  if (status.quotaExhausted) {
    out.push(h("div", { class: "alert alert--warning" }, icon("clock"), h("p", {}, ctx.t("dashboard.live.quota"))));
  }
  out.push(h("div", { class: "dash-stats" },
    tile(ctx.t("dashboard.live.analyzed_today"), count(ctx, status.analyzedToday)),
    tile(ctx.t("dashboard.live.median"), seconds(ctx, status.latencyP50)),
    tile(ctx.t("dashboard.live.p95"), seconds(ctx, status.latencyP95)),
    tile(ctx.t("dashboard.live.critical_today"), count(ctx, status.criticalToday)),
    tile(ctx.t("dashboard.live.alerts"), count(ctx, status.alertsRecorded))));
  const p = status.production;
  if (withProduction && p) {
    const since = p.first_public_day ? ctx.t("dashboard.production.since", { date: fmtDay(p.first_public_day, ctx.lang) }) : ctx.t("dashboard.production.title");
    out.push(h("h3", { class: "viz-subhead" }, since),
      h("div", { class: "dash-stats" },
        tile(ctx.t("dashboard.production.submissions"), count(ctx, p.total_submissions)),
        tile(ctx.t("dashboard.production.distinct"), count(ctx, p.distinct_submitters)),
        tile(ctx.t("dashboard.production.classified"), count(ctx, p.classified)),
        tile(ctx.t("dashboard.production.to_alert"), seconds(ctx, p.mean_submission_to_alert_seconds))));
  }
  return out;
}

