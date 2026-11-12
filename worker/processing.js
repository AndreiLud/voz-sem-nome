// Classification of stored submissions with Workers AI, and the cron jobs around it.
//
// A submission is classified right after the 202 answer (ctx.waitUntil) and its row updated to
// done, review or failed. Each run works on one attempt: the row must still be "processing" with
// the attempt number it claimed, so a late run never overwrites a newer one. When the daily
// allocation is used up (error 3036) the row waits as "queued_quota", the attempt is given back
// and the day is flagged in meta. The cron retries rows stuck in "processing" for more than
// two minutes and, after the 00:00 UTC reset, the rows waiting for quota; a row gets at most
// queue.max_attempts attempts, then it is "failed".

import { QuotaExceeded, classifyComment, errorCode, stageTotals } from "./classify/pipeline.js";
import { DAY_MS, MINUTE_MS, spDay, toIso, utcDay } from "./clock.js";
import { recordAlert } from "./alerts.js";
import { QUOTA_KEY } from "./limits.js";
import { logEvent } from "./log.js";
import { Store } from "./store.js";

// The one place with the model id besides wrangler.jsonc (vars.AI_MODEL, which wins).
export const DEFAULT_MODEL = "@cf/google/gemma-4-26b-a4b-it";
export const STUCK_AFTER_MS = 2 * MINUTE_MS;
export const RETRY_BATCH = 5; // rows per cron run: keeps D1 queries below 50 per invocation
// No new row is claimed after this much time in one cron run, so the last classification still
// ends inside the 30 s that ctx.waitUntil gives the job. Date.now() moves across I/O in Workers.
export const RETRY_TIME_BUDGET_MS = 15_000;

export const modelOf = (env) => env.AI_MODEL || DEFAULT_MODEL;

/** D1 columns of a finished classification. */
export function resultColumns(result, finishedAt) {
  const totals = stageTotals(result.stages);
  return {
    status: result.status,
    finished_at: finishedAt,
    themes: JSON.stringify(result.themes),
    sentiment: result.sentiment,
    critical: result.critical ? 1 : 0,
    critical_types: JSON.stringify(result.critical_types),
    critical_sources: JSON.stringify(result.critical_sources),
    evidence: result.evidence,
    model: result.model,
    prompt_version: result.prompt_version,
    verifier_version: result.verifier_version,
    stages: JSON.stringify(result.stages),
    ai_ms: totals.total_ms,
    prompt_tokens: totals.prompt_tokens,
    output_tokens: totals.output_tokens,
  };
}

/** The Workers AI quota of the current UTC day is known to be used up. */
export const quotaExhausted = (quotaDay, nowMs) => quotaDay === utcDay(nowMs);

/**
 * Classify a row this run claimed: {id, public_id, created_at, area, comment_anon, attempts}
 * with status "processing". Returns the outcome: "done", "review", "skipped", "queued_quota",
 * "retry" (left in processing for the cron), "failed", or "lost" (another run moved the row).
 */
export async function processSubmission(deps, env, row) {
  const store = new Store(env.DB);
  let result;
  try {
    result = await classifyComment(row.comment_anon, env.AI, { model: modelOf(env), now: deps.now });
  } catch (err) {
    return handleFailure(deps, store, row, err);
  }
  const stored = await store.finish(row.id, row.attempts, resultColumns(result, toIso(deps.now())));
  if (!stored) return "lost";
  if (result.status === "skipped") return "skipped";
  await store.bump(spDay(row.created_at), { classified: 1, critical: result.critical ? 1 : 0 });
  if (result.critical) await recordAlert(store, env, deps.settings, row, result, deps.now());
  return result.status;
}

/** processSubmission for ctx.waitUntil: never rejects, so no error message reaches the logs. */
export function processInBackground(deps, env, row) {
  return processSubmission(deps, env, row).catch((err) => {
    logEvent("process_error", { name: err?.name ?? "error" });
    return "error";
  });
}

async function handleFailure(deps, store, row, err) {
  const now = deps.now();
  const code = errorCode(err) ?? err?.name ?? "error";
  if (err instanceof QuotaExceeded) {
    logEvent("ai_quota", { code });
    await store.setMeta(QUOTA_KEY, utcDay(now));
    await store.bump(spDay(now), { quota_hits: 1 });
    return (await store.parkForQuota(row.id, row.attempts)) ? "queued_quota" : "lost";
  }
  logEvent("classify_failed", { code, attempt: row.attempts });
  if (row.attempts < deps.settings.queue.max_attempts) return "retry";
  if (!(await store.fail(row.id, toIso(now), { attempts: row.attempts }))) return "lost";
  await store.bump(spDay(row.created_at), { failed: 1 });
  return "failed";
}

/** Cron, every 10 minutes: retry stuck rows and, after the reset, the rows waiting for quota. */
export async function retryPending(deps, env) {
  const store = new Store(env.DB);
  await store.ready();
  const now = deps.now();
  const quotaDay = await store.meta(QUOTA_KEY);
  const rows = await store.retryCandidates({
    stuckBefore: toIso(now - STUCK_AFTER_MS),
    includeQuota: !quotaExhausted(quotaDay, now),
    limit: RETRY_BATCH,
  });
  const outcomes = {};
  const count = (outcome) => { outcomes[outcome] = (outcomes[outcome] ?? 0) + 1; };
  for (const row of rows) {
    if (deps.now() - now >= RETRY_TIME_BUDGET_MS) break; // the rest waits for the next run
    if (row.attempts >= deps.settings.queue.max_attempts) {
      if (await store.fail(row.id, toIso(deps.now()), { status: row.status, attempts: row.attempts })) {
        await store.bump(spDay(row.created_at), { failed: 1 });
        count("failed");
      }
      continue;
    }
    if (!(await store.claim(row.id, row.status, row.attempts, toIso(deps.now())))) continue;
    const outcome = await processSubmission(deps, env, { ...row, status: "processing", attempts: row.attempts + 1 });
    count(outcome);
    if (outcome === "queued_quota") break; // the allocation ran out again: wait for the next reset
  }
  if (rows.length) logEvent("retry", outcomes);
  return outcomes;
}

/** Cron, once a day: delete expired submissions, alerts, IP hashes and salts. */
export async function purgeExpired(deps, env) {
  const store = new Store(env.DB);
  await store.ready();
  const now = deps.now();
  const privacy = deps.settings.privacy;
  const deleted = await store.purge({
    submissionsBefore: toIso(now - privacy.public_retention_days * DAY_MS),
    alertsBefore: toIso(now - privacy.alert_log_retention_days * DAY_MS),
    today: spDay(now),
  });
  logEvent("purge", deleted);
  return deleted;
}
