// "Follow your answer": the tracking code with copy buttons, the status of the job,
// polled every few seconds while the tab is visible, and the result card.
// The code lives in the URL (?c=XXXX-XXXX) so the page can be reloaded or shared.

import { apiFetch } from "../lib/api.js";
import { slot } from "../lib/dom.js";
import { notifyUrlChange } from "../lib/lang.js";
import { announce } from "../lib/live.js";
import { FieldErrors } from "./fields.js";
import { Poller } from "./poller.js";
import { renderChecking, renderResult, renderStatus } from "./render.js";
import { FINISHED, TERMINAL, normalizeCode, normalizeSubmission } from "./rules.js";

const LOOKUP_TIMEOUT_MS = 10000;
const CODE_PARAM = "c";

function slots() {
  const names = {
    layout: "layout", formSection: "form-section", trackSection: "track-section",
    lookup: "lookup-form", trackError: "track-error", trackErrorText: "track-error-text",
    tracking: "tracking", codeValue: "code-value", codeLink: "code-link",
    progressBlock: "progress-block", statusText: "status-text", progress: "progress",
    queueInfo: "queue-info", pollNote: "poll-note", pollNoteText: "poll-note-text",
    result: "result", commentBlock: "result-comment-block", quote: "result-quote",
    comment: "result-comment", removed: "result-removed", themes: "result-themes",
    sentiment: "result-sentiment", critical: "result-critical", meta: "result-meta",
    rowRemoved: "row-removed", rowThemes: "row-themes", rowSentiment: "row-sentiment",
    rowCritical: "row-critical", statusNote: "result-status-note",
    statusNoteText: "result-status-text", alertNote: "result-alert-note", cvv: "result-cvv",
  };
  const ui = {};
  for (const [key, name] of Object.entries(names)) ui[key] = slot(name);
  ui.title = document.getElementById("tracking-title");
  return ui;
}

export function initTracking(page, { taxonomy, pollMs }) {
  const ui = slots();
  if (!ui.tracking || !ui.lookup) return { follow() {} };
  const t = page.t;
  const ctx = { t, lang: page.lang, taxonomy };
  const codeInput = ui.lookup.elements.namedItem(CODE_PARAM);
  const lookupErrors = new FieldErrors(ui.lookup);
  let poller = null;
  let code = null;
  let lastStatus = null;
  let failure = "network";

  // ---------------------------------------------------------------- URL

  function shareUrl(value) {
    const path = page.routes.submit[page.lang];
    return `${window.location.origin}${path}?${CODE_PARAM}=${encodeURIComponent(value)}`;
  }

  function setUrl(value) {
    const url = new URL(window.location.href);
    url.search = `?${CODE_PARAM}=${encodeURIComponent(value)}`;
    url.hash = "";
    window.history.replaceState(window.history.state, "", url);
    notifyUrlChange();
  }

  // ---------------------------------------------------------------- messages

  function setPollNote(text) {
    ui.pollNoteText.textContent = text || "";
    ui.pollNote.hidden = !text;
  }

  function statusMessage(state) {
    const parts = [t(`submit.track.status.${state.status}`)];
    if (state.status === "queued" && state.position != null) {
      parts.push(t("submit.track.position", { position: state.position }));
    }
    if (FINISHED.has(state.status) || (TERMINAL.has(state.status) && state.comment)) {
      parts.push(t("submit.track.result_ready"));
    }
    return `${parts.map((part) => part.replace(/\.$/, "")).join(". ")}.`;
  }

  function showTrackError(text) {
    ui.trackErrorText.textContent = text;
    ui.trackError.hidden = false;
    announce(text);
  }

  // ---------------------------------------------------------------- state

  function apply(data, { quiet = false } = {}) {
    const state = normalizeSubmission(data);
    renderStatus(ui, state, ctx);
    renderResult(ui, state, ctx);
    if (!quiet && state.status !== lastStatus) announce(statusMessage(state));
    lastStatus = state.status;
    return TERMINAL.has(state.status);
  }

  async function fetchOnce(signal) {
    const result = await apiFetch(`/submissions/${encodeURIComponent(code)}`, { timeoutMs: LOOKUP_TIMEOUT_MS, signal });
    if (signal.aborted) return "stop";
    if (result.ok && result.data) {
      setPollNote(null);
      return apply(result.data) ? "stop" : "continue";
    }
    if (result.status === 404) {
      ui.tracking.hidden = true;
      showTrackError(t("submit.track.not_found"));
      return "stop";
    }
    failure = result.status === 0 ? "network" : "offline";
    return "retry";
  }

  function start(value, { initial = null, focus = false } = {}) {
    poller?.stop();
    code = value;
    lastStatus = null;
    failure = "network";
    ui.trackError.hidden = true;
    ui.tracking.hidden = false;
    ui.codeValue.textContent = value;
    ui.codeLink.href = shareUrl(value);
    ui.codeLink.textContent = shareUrl(value);
    codeInput.value = value;
    lookupErrors.clear("code");
    setPollNote(null);
    setUrl(value);
    const finished = initial ? apply(initial, { quiet: true }) : false;
    if (!initial) renderChecking(ui, ctx);
    if (focus) ui.title.focus();
    if (finished) return;
    poller = new Poller({
      fetchOnce,
      intervalMs: pollMs,
      onPause: (paused) => setPollNote(paused ? t("submit.track.paused") : null),
      onRetry: (delay) => setPollNote(t(`submit.track.${failure === "offline" ? "offline" : "reconnecting"}`, {
        seconds: Math.round(delay / 1000),
      })),
    });
    poller.start({ immediate: !initial });
  }

  // ---------------------------------------------------------------- lookup form

  ui.lookup.addEventListener("submit", (event) => {
    event.preventDefault();
    const value = normalizeCode(codeInput.value);
    if (!value) {
      lookupErrors.show("code", t("submit.errors.code"));
      codeInput.focus();
      return;
    }
    start(value);
  });

  codeInput.addEventListener("input", () => {
    if (lookupErrors.has("code") && normalizeCode(codeInput.value)) lookupErrors.clear("code");
  });

  // A shared link opens with the tracking card first, so the result is not below the form.
  const fromUrl = new URLSearchParams(window.location.search).get(CODE_PARAM);
  if (fromUrl !== null) {
    ui.layout.insertBefore(ui.trackSection, ui.formSection);
    ui.layout.classList.add("submit-layout--tracking");
    const value = normalizeCode(fromUrl);
    if (value) start(value);
    else {
      codeInput.value = fromUrl;
      lookupErrors.show("code", t("submit.errors.code"));
    }
  }

  return {
    /** Called by the form with the 202 answer. */
    follow(data) {
      const value = normalizeCode(data.code);
      if (!value) return;
      start(value, { initial: data, focus: true });
      announce(t("submit.sent", { code: value }));
    },
  };
}
