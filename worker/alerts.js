// Critical-case alerts of the live demo: port of the parts of src/enps/alerts.py the edge needs.
//
// A critical result records an alert row (ids, types and timestamps only). The mode is
// "immediate" while fewer than alerts.immediate_per_hour were immediate in the last hour, else
// "digest". delivered_at is the moment the alert was recorded: the dashboard lists it right away.
// When the secret ALERT_WEBHOOK_URL is set, immediate alerts are also POSTed to it with the
// JSON the local worker sends to n8n, so the repository's "Critical alert" workflow can take it.
// A webhook problem never fails the request; only its status code is logged.

import { TAXONOMY } from "./classify/data.js";
import { HOUR_MS, spDay, toIso } from "./clock.js";
import { logEvent } from "./log.js";
import { visibleAreaCodes } from "./metrics/aggregates.js";
import { publicExcerpt } from "./metrics/excerpt.js";

export const ALERT_TITLE = "Caso crítico na pesquisa de clima";
export const AREA_HIDDEN = "Área omitida (grupo pequeno)";
export const LINK_TEXT = "Ver os casos críticos no painel";
export const DATASET = "public";
const WEBHOOK_TIMEOUT_MS = 5000;

const labels = (items) => new Map(items.map((item) => [item.code, item.label.pt]));
const TYPE_LABELS = labels(TAXONOMY.critical_types);
const THEME_LABELS = labels(TAXONOMY.themes);

/** Telegram HTML escaping, like html.escape(text, quote=False). */
export function escapeText(text) {
  return String(text).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

/** html.escape(text, quote=True), for attribute values. */
function escapeAttribute(text) {
  return escapeText(text).replace(/"/g, "&quot;").replace(/'/g, "&#x27;");
}

export function dashboardUrl(settings) {
  return settings.site.base_url.replace(/\/+$/, "") + settings.site.dashboard_path.pt + "#criticos";
}

/** dd/mm/yyyy in Sao Paulo. No time of day: it narrows who wrote it. */
function receivedDate(iso) {
  const [year, month, day] = spDay(iso).split("-");
  return `${day}/${month}/${year}`;
}

/**
 * Telegram HTML message for one critical case, in Portuguese (alerts.message_html).
 * view: {types, themes, area (code or null), excerpt, submitted_at}
 */
export function messageHtml(view, settings) {
  const areaLabels = labels(settings.survey.areas);
  const types = view.types.map((t) => TYPE_LABELS.get(t) ?? t).join(", ");
  const lines = [`<b>${escapeText(ALERT_TITLE)}</b>`, `${view.types.length > 1 ? "Tipos" : "Tipo"}: ${escapeText(types)}`];
  if (view.themes.length) {
    lines.push(`Temas: ${escapeText(view.themes.map((t) => THEME_LABELS.get(t) ?? t).join(", "))}`);
  }
  lines.push(view.area ? `Área: ${escapeText(areaLabels.get(view.area) ?? view.area)}` : AREA_HIDDEN);
  lines.push(`Recebido em ${receivedDate(view.submitted_at)}`);
  if (view.excerpt) lines.push(`<i>“${escapeText(view.excerpt)}”</i>`);
  lines.push(`<a href="${escapeAttribute(dashboardUrl(settings))}">${LINK_TEXT}</a>`);
  return lines.join("\n");
}

/** The JSON sent to the webhook (alerts.view_payload). */
export function alertPayload(view, settings) {
  return {
    alert_id: view.id,
    dataset: DATASET,
    mode: view.mode,
    types: view.types,
    themes: view.themes,
    area: view.area,
    excerpt: view.excerpt,
    submitted_at: view.submitted_at,
    detected_at: view.detected_at,
    message_html: messageHtml(view, settings),
    channels: [...(settings.alerts.channels ?? [])].map(String),
  };
}

/**
 * Record the alert of a critical submission and, for an immediate one, send it to the webhook.
 * `row` is the submission ({public_id, created_at, area, comment_anon}); `result` the
 * classification. Returns the alert view, or null when the dataset sends no alerts.
 */
export async function recordAlert(store, env, settings, row, result, nowMs) {
  if (!settings.alerts.datasets.includes(DATASET)) return null;
  const detectedAt = toIso(nowMs);
  const recentImmediate = await store.alertsSince(toIso(nowMs - HOUR_MS), "immediate");
  const mode = recentImmediate < settings.alerts.immediate_per_hour ? "immediate" : "digest";
  const id = await store.insertAlert({
    public_id: row.public_id,
    critical_types: JSON.stringify(result.critical_types),
    submitted_at: row.created_at,
    detected_at: detectedAt,
    delivered_at: detectedAt,
    mode,
  });
  const view = {
    id,
    mode,
    types: result.critical_types,
    themes: result.themes,
    area: null,
    excerpt: publicExcerpt(row.comment_anon ?? "", settings.privacy.display_excerpt_chars),
    submitted_at: row.created_at,
    detected_at: detectedAt,
  };
  if (mode === "immediate" && env.ALERT_WEBHOOK_URL) {
    const visible = visibleAreaCodes(await store.areaCounts(), { cfg: { min_group_size: settings.privacy.min_group_size } });
    view.area = visible.has(row.area) ? row.area : null;
    await sendWebhook(env, alertPayload(view, settings));
  }
  return view;
}

/** POST to ALERT_WEBHOOK_URL with X-Internal-Token. Returns true on a 2xx answer; never throws. */
export async function sendWebhook(env, payload) {
  try {
    const response = await fetch(env.ALERT_WEBHOOK_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Internal-Token": env.ALERT_WEBHOOK_TOKEN || "" },
      body: JSON.stringify(payload),
      signal: AbortSignal.timeout(WEBHOOK_TIMEOUT_MS),
    });
    await response.body?.cancel();
    if (!response.ok) logEvent("alert_webhook_failed", { status: response.status });
    return response.ok;
  } catch (err) {
    logEvent("alert_webhook_failed", { status: err?.name === "TimeoutError" ? "timeout" : "network" });
    return false;
  }
}
