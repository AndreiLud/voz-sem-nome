// Dashboard page: the dataset toggle (kept in the URL), loading with fallbacks, the refresh of
// the public view every 30 s while the tab is visible, and the panels (built in ./charts/).
//
// Sources, in order: GET /api/v1/public/dashboard (live, or the Worker's snapshot, which comes
// with X-Data-Source: snapshot and X-Snapshot-At), then, for the synthetic dataset only, the
// static file published with the site. The public view also reads GET /api/v1/public/status
// (the live demo's numbers of the day and the model quota).
import { apiFetch, staticJson } from "./lib/api.js";
import { labelOf, loadConfig, loadTaxonomy } from "./lib/config.js";
import { h, icon, slot } from "./lib/dom.js";
import { fmtDateTime } from "./lib/format.js";
import { notifyUrlChange } from "./lib/lang.js";
import { announce } from "./lib/live.js";
import { bindChart, hide as hideTip } from "./charts/interact.js";
import {
  datasetFromSearch, isDashboard, isSmall, normalizeStatus, searchWithDataset, signature,
} from "./charts/model.js";
import { symmetricDomain } from "./charts/scale.js";
import * as sections from "./charts/sections.js";

export const REFRESH_MS = 30000;
const STATIC_SYNTHETIC = "/data/dashboard-synthetic.json";
const HINT_ID = "viz-keys-hint";
const CHART_SLOTS = ["overview", "by-area", "by-tenure", "themes", "heatmap"];

/** @param {object} page data from lib/page.js */
export async function init(page) {
  const root = document.querySelector("[data-dashboard]");
  if (!root) return;
  await new Dashboard(page, root).start();
}

class Dashboard {
  constructor(page, root) {
    this.page = page;
    this.t = page.t;
    this.lang = page.lang;
    this.root = root;
    this.dataset = datasetFromSearch(window.location.search, this.lang, page.query);
    this.taxonomy = null;
    this.staticUrl = STATIC_SYNTHETIC;
    this.shown = null; // {dataset, sig, statusSig, data, source, quota}
    this.loadedAt = 0;
    this.timer = 0;
    this.request = 0;
    this.controller = null;
    this.casesExpanded = false;
  }

  async start() {
    this.bindToggle();
    document.addEventListener("visibilitychange", () => this.onVisibility());
    const [taxonomy, config] = await Promise.all([loadTaxonomy(this.page.taxonomy), loadConfig(this.page.config)]);
    this.taxonomy = taxonomy;
    if (config && config.data && config.data.dashboard_synthetic) this.staticUrl = config.data.dashboard_synthetic;
    await this.load({ user: true });
  }

  // ---------------------------------------------------------------- dataset toggle and URL

  bindToggle() {
    const group = slot("dataset-toggle", this.root);
    this.syncToggle();
    group.addEventListener("click", (event) => {
      const button = event.target.closest("[data-dataset]");
      if (!button || button.dataset.dataset === this.dataset) return;
      this.dataset = button.dataset.dataset;
      this.casesExpanded = false;
      this.syncToggle();
      const url = new URL(window.location.href);
      url.search = searchWithDataset(url.search, this.dataset, this.lang, this.page.query);
      window.history.replaceState(window.history.state, "", url);
      notifyUrlChange();
      this.load({ user: true, switched: true });
    });
  }

  syncToggle() {
    for (const button of this.root.querySelectorAll("[data-dataset]")) {
      button.setAttribute("aria-pressed", String(button.dataset.dataset === this.dataset));
    }
  }

  // ---------------------------------------------------------------- loading

  /** user: a person asked (first load, toggle, retry); switched: the dataset changed. */
  async load({ user = false, switched = false } = {}) {
    clearTimeout(this.timer);
    if (this.controller) this.controller.abort();
    this.controller = new AbortController();
    const token = ++this.request;
    const dataset = this.dataset;
    const hasContent = Boolean(this.shown);
    if (user && hasContent) this.root.setAttribute("aria-busy", "true");
    if (user && !hasContent) this.setStatus(h("p", { class: "muted" }, this.t("dashboard.loading")));

    const signal = this.controller.signal;
    const [result, status] = await Promise.all([
      this.fetchDashboard(dataset, signal),
      dataset === "public" ? apiFetch("/status", { signal }) : Promise.resolve(null),
    ]);
    if (token !== this.request) return;
    this.root.removeAttribute("aria-busy");
    this.loadedAt = Date.now();

    if (!result) {
      this.showFailure(dataset);
    } else {
      this.render(dataset, result, status && status.ok ? normalizeStatus(status.data) : null);
      if (switched) announce(this.t("dashboard.showing", { dataset: this.t(`dashboard.datasets.${dataset}`) }));
    }
    this.schedule(REFRESH_MS);
  }

  async fetchDashboard(dataset, signal) {
    const res = await apiFetch(`/dashboard?dataset=${dataset}`, { signal });
    if (res.ok && isDashboard(res.data) && res.data.dataset === dataset) {
      return { data: res.data, source: res.source === "snapshot" ? "snapshot" : "live", snapshotAt: res.snapshotAt };
    }
    if (dataset === "synthetic" && !signal.aborted) {
      const data = await staticJson(this.staticUrl);
      if (isDashboard(data) && data.dataset === "synthetic") return { data, source: "static", snapshotAt: null };
    }
    return null;
  }

  // ---------------------------------------------------------------- refresh of the public view

  schedule(delay) {
    clearTimeout(this.timer);
    if (this.dataset !== "public" || document.visibilityState !== "visible") return;
    this.timer = setTimeout(() => this.load(), Math.max(0, delay));
  }

  onVisibility() {
    if (document.visibilityState !== "visible") {
      clearTimeout(this.timer);
      return;
    }
    this.schedule(REFRESH_MS - (Date.now() - this.loadedAt));
  }

  // ---------------------------------------------------------------- states

  setStatus(...nodes) {
    slot("status", this.root).replaceChildren(...nodes);
  }

  showFailure(dataset) {
    const retry = h("button", { class: "btn btn--secondary btn--small", type: "button" }, this.t("dashboard.retry"));
    retry.addEventListener("click", () => this.load({ user: true }));
    const sameData = this.shown && this.shown.dataset === dataset;
    const message = sameData
      ? this.t("dashboard.refresh_failed", { datetime: fmtDateTime(this.shown.data.generated_at, this.lang) })
      : this.t("dashboard.error");
    this.setStatus(h("div", { class: "alert alert--error" }, icon("error"),
      h("div", { class: "dash-status__body" }, h("p", {}, message), h("p", {}, retry))));
    if (!sameData) {
      this.shown = null;
      for (const name of ["content", "small-state", "production-section", "sample-badge", "snapshot-banner", "static-banner"]) {
        slot(name, this.root).hidden = true;
      }
      slot("freshness", this.root).textContent = "";
    }
  }

  render(dataset, result, status) {
    const { data, source } = result;
    const k = typeof data.min_group_size === "number" ? data.min_group_size : this.page.vars.k;
    const small = isSmall(data, k);
    this.setStatus();
    this.renderFreshness(result);

    const sig = `${dataset}|${small}|${signature(data)}`;
    const statusSig = JSON.stringify(status);
    const previous = this.shown;
    const ctx = this.context(k, data);
    if (!previous || previous.sig !== sig) this.renderData(data, ctx, small, dataset);
    if (!previous || previous.statusSig !== statusSig || previous.sig !== sig) this.renderLive(dataset, status, ctx, small);
    const quota = Boolean(status && status.quotaExhausted);
    if (previous && previous.dataset === dataset && quota && !previous.quota) announce(this.t("dashboard.live.quota"));
    this.shown = { dataset, sig, statusSig, data, source, quota };
  }

  renderFreshness({ data, source, snapshotAt }) {
    const generated = fmtDateTime(data.generated_at, this.lang);
    const key = source === "live" ? "dashboard.freshness_live" : "dashboard.freshness_static";
    slot("freshness", this.root).textContent = generated ? this.t(key, { datetime: generated }) : "";
    slot("sample-badge", this.root).hidden = data.sample !== true;
    const snapshot = slot("snapshot-banner", this.root);
    snapshot.hidden = source !== "snapshot";
    if (source === "snapshot") {
      const when = fmtDateTime(snapshotAt || data.generated_at, this.lang);
      slot("snapshot-text", this.root).textContent = this.t("dashboard.snapshot_banner", { datetime: when });
    }
    slot("static-banner", this.root).hidden = source !== "static";
  }

  context(k, data) {
    const label = (kind) => (code) => labelOf(this.taxonomy, kind, code, this.lang);
    const tax = this.taxonomy || {};
    return {
      t: (key, values = {}) => this.t(key, { k, ...values }),
      lang: this.lang,
      k,
      hintId: HINT_ID,
      label: {
        area: label("areas"), tenure: label("tenure_bands"), theme: label("themes"),
        sentiment: label("sentiments"), critical: label("critical_types"),
      },
      criticalCodes: tax.critical_types
        ? tax.critical_types.map((item) => item.code)
        : Object.keys((data.critical && data.critical.by_type) || {}),
      placeholders: new Set((tax.placeholders || []).map((item) => item.code)),
      // The synthetic comments are Portuguese whatever the page language.
      excerptLang: data.dataset === "synthetic" && this.lang !== "pt" ? "pt-BR" : null,
      openTables: new Set([...this.root.querySelectorAll("details[data-table][open]")].map((d) => d.dataset.table)),
      casesExpanded: this.casesExpanded,
    };
  }

  // ---------------------------------------------------------------- panels

  renderData(data, ctx, small, dataset) {
    hideTip();
    const content = slot("content", this.root);
    const smallState = slot("small-state", this.root);
    content.hidden = small;
    smallState.hidden = !small;
    if (small) {
      slot("small-text", this.root).textContent = ctx.t(dataset === "public" ? "dashboard.small_public" : "dashboard.small_synthetic");
      slot("small-cta", this.root).hidden = dataset !== "public";
      return;
    }
    const focus = this.focusedMark();
    const { areas, tenures } = sections.groupData(data, ctx);
    const domain = symmetricDomain([...areas, ...tenures].map((row) => row.enps));
    const fill = (name, nodes) => slot(name, this.root).replaceChildren(...nodes.filter(Boolean));

    fill("overview", sections.overview(data, ctx));
    fill("by-area", sections.groups(areas, domain, ctx, {
      key: "by-area", chartName: ctx.t("dashboard.charts.by_area"), caption: ctx.t("dashboard.sections.by_area"),
      firstColumn: { label: ctx.t("dashboard.labels.area") },
      mergedNote: ctx.t("dashboard.merged_note.areas", { other: ctx.t("dashboard.other_areas") }),
    }));
    fill("by-tenure", sections.groups(tenures, domain, ctx, {
      key: "by-tenure", chartName: ctx.t("dashboard.charts.by_tenure"), caption: ctx.t("dashboard.sections.by_tenure"),
      firstColumn: { label: ctx.t("dashboard.labels.tenure") },
      mergedNote: ctx.t("dashboard.merged_note.tenures", { other: ctx.t("dashboard.other_tenures") }),
    }));
    fill("themes", sections.themes(data, ctx));
    fill("heatmap", sections.heat(data, ctx));
    this.renderCritical(data, ctx);

    for (const name of CHART_SLOTS) {
      const chart = slot(name, this.root).querySelector("[role='group'][aria-label]");
      if (!chart) continue;
      const columns = Number(chart.style.getPropertyValue("--cols")) || 1;
      bindChart(chart, { columns, focusKey: focus && focus.slot === name ? focus.key : null });
    }
  }

  renderCritical(data, ctx) {
    const toggle = () => {
      this.casesExpanded = !this.casesExpanded;
      this.renderCritical(data, this.context(ctx.k, data));
      slot("critical", this.root).querySelector("button[aria-expanded]")?.focus();
    };
    slot("critical", this.root).replaceChildren(...sections.critical(data, ctx, toggle).filter(Boolean));
  }

  renderLive(dataset, status, ctx, small) {
    const section = slot("production-section", this.root);
    section.hidden = dataset !== "public";
    if (dataset !== "public") return;
    slot("production", this.root).replaceChildren(
      ...sections.liveStatus(status, ctx, { withProduction: !small }).filter(Boolean));
  }

  /** The chart mark that has focus now, to give focus back after a redraw. */
  focusedMark() {
    const mark = document.activeElement && document.activeElement.closest && document.activeElement.closest("[data-mark]");
    if (!mark || !this.root.contains(mark)) return null;
    const holder = mark.closest("[data-slot]");
    return holder ? { slot: holder.dataset.slot, key: mark.dataset.key } : null;
  }
}
