// POST /api/v1/public/submissions and GET /api/v1/public/submissions/{code}.
//
// Submit: Turnstile, limits (RL binding, then the per-IP day and global hour caps in D1),
// validation (same rules and errors as enps.queue.ingest), the quota check, anonymization,
// a duplicate check on the anonymized comment and the insert with status "processing". The
// answer is the FastAPI 202 JSON; the classification runs after it in ctx.waitUntil. The raw
// comment never leaves this request: only the anonymized text is stored and sent to the model.

import { HOUR_MS, nextUtcMidnight, secondsUntil, spDay, toIso } from "./clock.js";
import { clientIp, error, json, notFound, rateLimited, readJsonObject } from "./http.js";
import { formatTrackingCode, newPublicId, newTrackingCode, normalizeTrackingCode, trackingHash } from "./ids.js";
import { QUOTA_KEY, RL_PERIOD_S, checkDailyAndHourly, ipHash, overWindowLimit } from "./limits.js";
import { pyRound } from "./metrics/numbers.js";
import { processInBackground, quotaExhausted } from "./processing.js";
import { Store } from "./store.js";
import { MAX_TOKEN_CHARS, verifyTurnstile } from "./turnstile.js";
import { InvalidSubmission, anonymizeComment, dedupeKey, validateSite } from "./validation.js";

export const MAX_BODY_BYTES = 8 * 1024;
export const DEFAULT_JOB_SECONDS = 15; // ETA until a job has finished, as enps.queue.eta
const ID_ATTEMPTS = 5;
const RETRY_PERIOD_MS = 10 * 60_000; // the retry cron runs every 10 minutes
const WAITING = new Set(["processing", "queued_quota"]);
const FINISHED = new Set(["done", "review"]);

// ------------------------------------------------------------------ submit

/** Turnstile verdict for this request, or null to go on. */
async function turnstileRefusal(env, token, ip) {
  if (!env.TURNSTILE_SECRET && env.DEV_ALLOW_NO_TURNSTILE === "1") return null;
  if (typeof token !== "string" || !token || token.length > MAX_TOKEN_CHARS) return error(403, "turnstile");
  const verdict = await verifyTurnstile(env, token, ip);
  if (verdict === "unavailable") return error(503, "turnstile_unavailable", { headers: { "Retry-After": "30" } });
  return verdict === "ok" ? null : error(403, "turnstile");
}

export async function submit(request, env, ctx, deps) {
  const body = await readJsonObject(request, MAX_BODY_BYTES);
  if (body.response) return body.response;
  const { turnstile: token, ...payload } = body.value;
  const ip = clientIp(request);
  const refusal = await turnstileRefusal(env, token, ip);
  if (refusal) return refusal;

  const store = new Store(env.DB);
  await store.ready();
  const now = deps.now();
  const day = spDay(now);
  // Every limit counts the daily-salted HMAC of the address, never the address itself.
  const ipKey = await ipHash(store, deps.saltMemo, ip, day, deps.randomBytes);
  if (await overWindowLimit(env, ipKey)) {
    await store.bump(day, { rejected_rate_limit: 1 });
    return rateLimited("ip_window", RL_PERIOD_S);
  }
  const { limited, quotaDay } = await checkDailyAndHourly(store, deps.settings, { ipKey, now });
  if (limited) {
    await store.bump(day, { rejected_rate_limit: 1 });
    return rateLimited(limited.scope, limited.retryAfter);
  }

  let sub;
  try {
    sub = validateSite(payload, deps.settings);
  } catch (err) {
    if (err instanceof InvalidSubmission) return error(422, "validation", { fields: err.fields });
    throw err;
  }
  if (sub.comment && quotaExhausted(quotaDay, now)) {
    await store.bump(day, { rejected_quota: 1 });
    return error(503, "quota", { headers: { "Retry-After": String(secondsUntil(nextUtcMidnight(now), now)) } });
  }
  await store.recordSubmitter(day, ipKey);

  const accepted = await ingest(store, deps, sub, now);
  if (!accepted) {
    await store.bump(day, { rejected_duplicate: 1 });
    return error(409, "duplicate");
  }
  await store.bump(day, { submissions: 1 });
  if (await store.firstAccepted(day, ipKey)) await store.bump(day, { distinct_submitters: 1 });

  const { row, code } = accepted;
  if (row.status === "processing") ctx.waitUntil(processInBackground(deps, env, row));
  return json(202, {
    code,
    status: row.status,
    ...(await queueInfo(store, deps, row, now)),
    comment_anon: row.comment_anon,
    pii_counts: JSON.parse(row.pii_counts),
  });
}

/** Anonymize and insert. Returns {row, code}, or null for a duplicate in the window. */
async function ingest(store, deps, sub, now) {
  const anon = anonymizeComment(sub.comment);
  const nowIso = toIso(now);
  const queued = Boolean(anon.text);
  const base = {
    created_at: nowIso,
    score: sub.score,
    area: sub.area,
    tenure: sub.tenure,
    lang: sub.lang,
    comment_anon: anon.text,
    comment_chars: [...sub.comment].length,
    pii_counts: JSON.stringify(anon.counts),
    anonymizer_version: anon.version,
    dedupe_key: await dedupeKey(anon.text),
    status: queued ? "processing" : "skipped",
    attempts: queued ? 1 : 0,
    started_at: queued ? nowIso : null,
    finished_at: queued ? null : nowIso,
  };
  const since = toIso(now - deps.settings.limits.duplicate_window_hours * HOUR_MS);
  for (let attempt = 1; ; attempt += 1) {
    const code = newTrackingCode(deps.randomBytes);
    const row = { ...base, public_id: newPublicId(deps.randomBytes), tracking_hash: await trackingHash(code) };
    try {
      const id = await store.insertSubmission(row, since);
      return id === null ? null : { row: { ...row, id }, code };
    } catch (err) {
      if (attempt >= ID_ATTEMPTS || !/UNIQUE/i.test(String(err?.message))) throw err;
    }
  }
}

// ------------------------------------------------------------------ queue numbers

/** Mean job time of the last queue.eta_window jobs, kept for a minute per isolate. */
async function averageJobSeconds(store, deps) {
  const now = deps.now();
  const memo = deps.memo.get("job_seconds");
  if (memo && now - memo.at < 60_000) return memo.value;
  const value = (await store.averageJobSeconds(deps.settings.queue.eta_window)) ?? DEFAULT_JOB_SECONDS;
  deps.memo.set("job_seconds", { at: now, value });
  return value;
}

/**
 * {queue_position, eta_seconds} like enps.api.public: a running job is position 1. A job waiting
 * for quota counts the ones ahead of it and waits for the first retry run after the reset.
 */
async function queueInfo(store, deps, row, now) {
  if (!WAITING.has(row.status)) return { queue_position: null, eta_seconds: null };
  const average = await averageJobSeconds(store, deps);
  if (row.status === "processing") return { queue_position: 1, eta_seconds: Math.ceil(average) };
  const position = (await store.waitingAhead("queued_quota", row.id)) + 1;
  const quotaDay = await store.meta(QUOTA_KEY);
  const resumeAt = quotaExhausted(quotaDay, now)
    ? nextUtcMidnight(now)
    : Math.ceil(now / RETRY_PERIOD_MS) * RETRY_PERIOD_MS;
  return { queue_position: position, eta_seconds: Math.ceil((resumeAt - now) / 1000 + position * average) };
}

// ------------------------------------------------------------------ lookup

function resultFields(row) {
  const processing = row.started_at && row.finished_at
    ? (Date.parse(row.finished_at) - Date.parse(row.started_at)) / 1000
    : null;
  return {
    themes: JSON.parse(row.themes ?? "[]"),
    sentiment: row.sentiment,
    critical: Boolean(row.critical),
    critical_types: JSON.parse(row.critical_types ?? "[]"),
    evidence: row.evidence,
    model: row.model,
    processing_seconds: processing === null ? null : pyRound(processing, 1),
  };
}

export async function lookup(rawCode, env, deps) {
  let decoded;
  try {
    decoded = decodeURIComponent(rawCode);
  } catch {
    return notFound();
  }
  const raw = normalizeTrackingCode(decoded);
  if (!raw) return notFound();
  const store = new Store(env.DB);
  await store.ready();
  const row = await store.submissionByTracking(await trackingHash(raw));
  if (!row) return notFound();
  const body = {
    code: formatTrackingCode(raw),
    status: row.status,
    comment_anon: row.comment_anon,
    pii_counts: JSON.parse(row.pii_counts),
  };
  if (WAITING.has(row.status)) Object.assign(body, await queueInfo(store, deps, row, deps.now()));
  else if (FINISHED.has(row.status)) Object.assign(body, resultFields(row));
  return json(200, body);
}
