// Submit page: the demo form (submit/form.js) and "follow your answer"
// (submit/tracking.js). The form handler is attached first, before any data is fetched,
// so a quick submit never falls back to a plain HTML post.
import { loadConfig, loadTaxonomy } from "./lib/config.js";
import { initForm } from "./submit/form.js";
import { initTracking } from "./submit/tracking.js";

const DEFAULT_POLL_MS = 4000;

/** @param {object} page data from lib/page.js */
export async function init(page) {
  let tracking = null;
  const waiting = [];
  initForm(page, {
    onAccepted: (data) => (tracking ? tracking.follow(data) : waiting.push(data)),
  });
  const [config, taxonomy] = await Promise.all([loadConfig(page.config), loadTaxonomy(page.taxonomy)]);
  tracking = initTracking(page, {
    taxonomy,
    pollMs: (config && config.poll_interval_ms) || DEFAULT_POLL_MS,
  });
  waiting.splice(0).forEach((data) => tracking.follow(data));
}
