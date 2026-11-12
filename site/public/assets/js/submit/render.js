// Drawing of one submission state: status line, progress, queue numbers and the result
// card. `ui` holds the page slots, `ctx` is {t, lang, taxonomy}.

import { labelOf } from "../lib/config.js";
import { anonymizedText, chip, criticalBadge, h, icon } from "../lib/dom.js";
import { fmtDuration, fmtNumber } from "../lib/format.js";
import { FINISHED, STAGES, TERMINAL } from "./rules.js";

const HTML_LANG = { pt: "pt-BR", en: "en" };

export function knownStatus(status) {
  return Object.prototype.hasOwnProperty.call(STAGES, status) ? status : "queued";
}

/** Waiting for the first answer: indeterminate progress, no result yet. */
export function renderChecking(ui, { t }) {
  delete ui.progressBlock.dataset.status;
  ui.statusText.textContent = t("submit.track.checking");
  ui.progress.removeAttribute("value");
  ui.queueInfo.hidden = true;
  ui.result.hidden = true;
}

export function renderStatus(ui, state, { t, lang }) {
  const status = knownStatus(state.status);
  ui.progressBlock.dataset.status = status;
  ui.statusText.textContent = t(`submit.track.status.${status}`);
  ui.progress.value = STAGES[status];
  ui.queueInfo.textContent = queueText(state, { t, lang });
  ui.queueInfo.hidden = !ui.queueInfo.textContent;
}

export function queueText(state, { t, lang }) {
  const parts = [];
  if (state.status === "queued_quota") parts.push(t("submit.track.quota"));
  if (state.status === "queued" && state.position != null) {
    parts.push(t("submit.track.position", { position: fmtNumber(state.position, lang) }));
  }
  if (!TERMINAL.has(state.status) && state.eta != null) {
    parts.push(t("submit.track.eta", { time: fmtDuration(state.eta, lang) }));
  }
  return parts.length ? `${parts.join(". ")}.` : "";
}

// ------------------------------------------------------------------ result card

function pendingText(t) {
  return h("span", { class: "pending-text" }, icon("pending"), h("span", {}, t("submit.result.pending")));
}

function quiet(text) {
  return h("span", { class: "result-card__none" }, text);
}

function knownPlaceholders(taxonomy, pii) {
  const codes = (taxonomy && taxonomy.placeholders || []).map((item) => item.code);
  return new Set([...codes, ...Object.keys(pii)]);
}

function removedList(pii, { t, lang, taxonomy }) {
  const entries = Object.entries(pii)
    .filter(([, count]) => count > 0)
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  if (!entries.length) return quiet(t("submit.result.removed_none"));
  return h("ul", { class: "removed-list" }, entries.map(([code, count]) => {
    const mark = chip(code);
    mark.setAttribute("aria-hidden", "true");
    const text = t("submit.result.removed_item", {
      label: labelOf(taxonomy, "placeholders", code, lang),
      count: fmtNumber(count, lang),
    });
    return h("li", {}, mark, h("span", {}, text));
  }));
}

function themeList(codes, { t, lang, taxonomy }) {
  if (!codes.length) return quiet(t("submit.result.themes_none"));
  return h("ul", { class: "tag-list" }, codes.map((code) =>
    h("li", { class: "tag" }, labelOf(taxonomy, "themes", code, lang))));
}

function criticalValue(result, { t, lang, taxonomy }) {
  if (!result.critical) return document.createTextNode(t("submit.result.critical_no"));
  const badges = result.criticalTypes.map((code) =>
    h("li", {}, criticalBadge(labelOf(taxonomy, "critical_types", code, lang))));
  return h("div", { class: "critical-value" },
    h("span", {}, t("submit.result.critical_yes")),
    badges.length ? h("ul", { class: "badge-list" }, badges) : null);
}

function statusNote(state, hasComment, t) {
  if (state.status === "review") return t("submit.result.review");
  if (state.status === "failed") return t("submit.result.failed");
  if (TERMINAL.has(state.status) && !hasComment) return t("submit.result.no_comment");
  return "";
}

export function renderResult(ui, state, ctx) {
  const { t, lang } = ctx;
  const hasComment = Boolean(state.comment);
  const pending = !TERMINAL.has(state.status);
  const result = FINISHED.has(state.status) ? state.result : null;
  ui.result.hidden = false;

  ui.commentBlock.hidden = !hasComment;
  ui.rowRemoved.hidden = !hasComment;
  if (hasComment) {
    ui.comment.replaceChildren(anonymizedText(state.comment, knownPlaceholders(ctx.taxonomy, state.pii)));
    if (state.lang && HTML_LANG[state.lang]) ui.quote.setAttribute("lang", HTML_LANG[state.lang]);
    else ui.quote.removeAttribute("lang");
    ui.removed.replaceChildren(removedList(state.pii, ctx));
  }

  const showLabels = hasComment && (pending || Boolean(result));
  for (const row of [ui.rowThemes, ui.rowSentiment, ui.rowCritical]) row.hidden = !showLabels;
  if (pending) {
    ui.themes.replaceChildren(pendingText(t));
    ui.sentiment.replaceChildren(pendingText(t));
    ui.critical.replaceChildren(pendingText(t));
  } else if (result) {
    ui.themes.replaceChildren(themeList(result.themes, ctx));
    ui.sentiment.replaceChildren(result.sentiment
      ? document.createTextNode(labelOf(ctx.taxonomy, "sentiments", result.sentiment, lang))
      : quiet(t("submit.result.sentiment_none")));
    ui.critical.replaceChildren(criticalValue(result, ctx));
  }

  const meta = result && result.seconds != null && result.model
    ? t("submit.result.processing_time", { seconds: fmtNumber(result.seconds, lang, 1), model: result.model })
    : "";
  ui.meta.textContent = meta;
  ui.meta.hidden = !meta;

  const note = statusNote(state, hasComment, t);
  ui.statusNoteText.textContent = note;
  ui.statusNote.hidden = !note;

  ui.alertNote.hidden = !(result && result.critical);
  ui.cvv.hidden = !(result && result.critical && result.criticalTypes.includes("bo"));
}
