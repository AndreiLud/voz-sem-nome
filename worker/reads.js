// Read routes for the site: GET /status, /dashboard and /health under /api/v1/public.
//
// Status and the public dashboard are computed from D1 and kept for a minute per isolate, so a
// page that polls them costs one computation per minute and stays inside the CPU budget of the
// Free plan. The synthetic dashboard is the static file published with the site.
//
// vars.PUBLIC_START_DAY (a Sao Paulo day, see publicStart in clock.js) leaves the answers sent
// before it (setup tests) out of the public dashboard and of the usage numbers, alerts included.
// The queue counts and the ETA still see every row, since they describe work the Worker is really
// doing.

import { publicStart, spDay, spDayStart, toIso } from "./clock.js";
import { error, json } from "./http.js";
import { QUOTA_KEY } from "./limits.js";
import { buildDashboard } from "./metrics/aggregates.js";
import { pyRound } from "./metrics/numbers.js";
import { percentile, productionStats } from "./metrics/production.js";
import { quotaExhausted } from "./processing.js";
import { Store } from "./store.js";
import { DEFAULT_JOB_SECONDS } from "./submissions.js";

export const CACHE_TTL_MS = 60_000;
export const DATASETS = ["public", "synthetic"];
export const SYNTHETIC_PATH = "/data/dashboard-synthetic.json";
const LATENCY_SAMPLE = 2000; // newest finished jobs used for the overall percentiles
/** The later of two ISO instants written by toIso (same format, so they compare as strings). */
const laterIso = (a, b) => (b !== null && b > a ? b : a);

/** `compute()` once per CACHE_TTL_MS per isolate. */
async function memoized(deps, key, compute) {
  const now = deps.now();
  const hit = deps.memo.get(key);
  if (hit && now - hit.at < CACHE_TTL_MS) return hit.value;
  const value = await compute(now);
  deps.memo.set(key, { at: now, value });
  return value;
}

const seconds = (ms) => (ms === null ? null : pyRound(ms / 1000, 1));
const latencyMs = (row) => Date.parse(row.finished_at) - Date.parse(row.created_at);

async function computeStatus(env, deps, now) {
  const store = new Store(env.DB);
  await store.ready();
  const day = spDay(now);
  const start = publicStart(env);
  const startIso = start ? start.iso : null;
  const beforeStart = start !== null && day < start.day;
  const since = laterIso(toIso(spDayStart(day)), startIso);
  const [counts, today, quotaDay, average, todayRows, alertsToday, daily, recent, alerts] = await Promise.all([
    store.statusCounts(),
    beforeStart ? {} : store.dailyValues(day),
    store.meta(QUOTA_KEY),
    store.averageJobSeconds(deps.settings.queue.eta_window),
    store.latencies({ since, limit: LATENCY_SAMPLE }),
    store.alertsSince(since, null, startIso),
    store.dailyRows(start ? start.day : null),
    store.latencies({ since: startIso, limit: LATENCY_SAMPLE }),
    store.alertTimes(startIso),
  ]);
  const exhausted = quotaExhausted(quotaDay, now);
  const todayMs = todayRows.map(latencyMs);
  return {
    generated_at: toIso(now),
    // As in enps.api.public: pending waits (here for the quota reset), processing is running.
    pending: counts.queued_quota ?? 0,
    processing: counts.processing ?? 0,
    eta_seconds: exhausted ? null : Math.ceil(average ?? DEFAULT_JOB_SECONDS),
    analyzed_today: today.classified ?? 0,
    latency_p50_s: seconds(percentile(todayMs, 50)),
    latency_p95_s: seconds(percentile(todayMs, 95)),
    critical_today: today.critical ?? 0,
    alerts_recorded: alertsToday,
    quota_exhausted_today: exhausted,
    production: productionStats({ daily, latencies: recent, alerts }),
  };
}

export async function status(env, deps) {
  return json(200, await memoized(deps, "status", (now) => computeStatus(env, deps, now)));
}

async function computePublicDashboard(env, deps, now) {
  const store = new Store(env.DB);
  await store.ready();
  const privacy = deps.settings.privacy;
  const start = publicStart(env);
  return buildDashboard(await store.dashboardRows(start ? start.iso : null), {
    dataset: "public",
    now,
    cfg: { min_group_size: privacy.min_group_size, display_excerpt_chars: privacy.display_excerpt_chars },
  });
}

export async function dashboard(request, env, deps) {
  const dataset = new URL(request.url).searchParams.get("dataset") || "public";
  if (!DATASETS.includes(dataset)) {
    return error(422, "validation", { fields: { dataset: "must be public or synthetic" } });
  }
  if (dataset === "public") {
    return json(200, await memoized(deps, "dashboard:public", (now) => computePublicDashboard(env, deps, now)));
  }
  const asset = await env.ASSETS.fetch(new Request(new URL(SYNTHETIC_PATH, request.url)));
  if (!asset.ok) {
    await asset.body?.cancel();
    return error(503, "unavailable");
  }
  return new Response(asset.body, {
    status: 200,
    headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" },
  });
}

export async function health(env) {
  const store = new Store(env.DB);
  try {
    await store.ready();
    await store.ping();
  } catch {
    return error(503, "unavailable");
  }
  return json(200, { status: "ok" });
}
