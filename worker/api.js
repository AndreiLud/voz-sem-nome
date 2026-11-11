// /api/* routes. Only this allowlist exists; every other path or method under /api
// answers 404 JSON without touching the origin.
//
//   POST /api/v1/public/submissions
//   GET  /api/v1/public/submissions/{code}
//   GET  /api/v1/public/dashboard?dataset=public|synthetic
//   GET  /api/v1/public/status
//   GET  /api/v1/public/health

import { clientIp, json, passText, readLimited } from "./http.js";
import { GET_TIMEOUT_MS, POST_TIMEOUT_MS, isDown, originFetch } from "./origin.js";
import { snapshotResponse } from "./snapshots.js";
import { MAX_TOKEN_CHARS, verifyTurnstile } from "./turnstile.js";

export const PREFIX = "/api/v1/public";
export const MAX_BODY_BYTES = 8 * 1024;
export const DATASETS = ["public", "synthetic"];
const RATE_LIMIT_PERIOD_S = 60; // same period as the RL binding in wrangler.jsonc

// Tracking codes: 8 characters from the API's alphabet (no 0, O, 1, I or L), "-" optional.
const CODE = /^([2-9A-HJKMNP-Z]{4})-?([2-9A-HJKMNP-Z]{4})$/i;

const OFFLINE = { error: "offline" };

export async function handleApi(request, env) {
  const url = new URL(request.url);
  const path = url.pathname;
  if (request.method === "POST" && path === `${PREFIX}/submissions`) {
    return submit(request, env);
  }
  if (request.method === "GET") {
    if (path === `${PREFIX}/dashboard`) return dashboard(request, env, url);
    if (path === `${PREFIX}/status`) return liveOrSnapshot(request, env, "/status", "status");
    if (path === `${PREFIX}/health`) return passThrough(request, env, "/health");
    if (path.startsWith(`${PREFIX}/submissions/`)) {
      const match = CODE.exec(path.slice(PREFIX.length + "/submissions/".length));
      if (match) {
        const code = `${match[1]}-${match[2]}`.toUpperCase();
        return passThrough(request, env, `/submissions/${code}`);
      }
    }
  }
  return json(404, { error: "not_found" });
}

// ----------------------------------------------------------------- submissions

async function submit(request, env) {
  // JSON only: a cross site page cannot send it without a CORS preflight, and the
  // preflight (OPTIONS) gets a 404 without CORS headers.
  const type = (request.headers.get("Content-Type") || "").toLowerCase();
  if (!type.startsWith("application/json")) return json(415, { error: "unsupported_media_type" });

  let payload;
  try {
    const text = await readLimited(request, MAX_BODY_BYTES);
    if (text === null) return json(413, { error: "too_large" });
    payload = JSON.parse(text);
  } catch {
    return invalidBody();
  }
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) return invalidBody();

  const { turnstile: token, ...forward } = payload;
  if (typeof token !== "string" || !token || token.length > MAX_TOKEN_CHARS) {
    return json(403, { error: "turnstile" });
  }
  const ip = clientIp(request);
  const verdict = await verifyTurnstile(env, token, ip);
  if (verdict === "unavailable") return json(503, { error: "turnstile_unavailable" }, { "Retry-After": "30" });
  if (verdict !== "ok") return json(403, { error: "turnstile" });

  if (await overLimit(env, ip)) {
    const retry = String(RATE_LIMIT_PERIOD_S);
    return json(429, { error: "rate_limited", retry_after: RATE_LIMIT_PERIOD_S }, { "Retry-After": retry });
  }

  let response;
  try {
    response = await originFetch(env, "/submissions", {
      method: "POST",
      body: JSON.stringify(forward),
      clientIp: ip,
      timeoutMs: POST_TIMEOUT_MS,
    });
  } catch {
    return json(503, OFFLINE, { "Retry-After": "60" });
  }
  return relay(response);
}

function invalidBody() {
  return json(422, { error: "validation", fields: { body: "must be a JSON object" } });
}

/** Rate limit binding; a missing binding or a failing call never blocks a submission. */
async function overLimit(env, ip) {
  if (!env.RL) return false;
  try {
    const { success } = await env.RL.limit({ key: ip });
    return success === false;
  } catch {
    return false;
  }
}

// ----------------------------------------------------------------- reads

async function dashboard(request, env, url) {
  const dataset = url.searchParams.get("dataset") || "public";
  if (!DATASETS.includes(dataset)) {
    return json(422, { error: "validation", fields: { dataset: "must be public or synthetic" } });
  }
  return liveOrSnapshot(request, env, `/dashboard?dataset=${dataset}`, `dashboard:${dataset}`);
}

/** Origin answer when it is up, else the KV copy (or 503 offline). */
async function liveOrSnapshot(request, env, upstreamPath, key) {
  let response;
  try {
    response = await originFetch(env, upstreamPath, { clientIp: clientIp(request), timeoutMs: GET_TIMEOUT_MS });
  } catch {
    return snapshotResponse(env, key);
  }
  if (isDown(response)) {
    await response.body?.cancel();
    return snapshotResponse(env, key);
  }
  return passText(response.status, await response.text(), {
    contentType: response.headers.get("Content-Type"),
    extra: { "X-Data-Source": "live" },
  });
}

async function passThrough(request, env, upstreamPath) {
  let response;
  try {
    response = await originFetch(env, upstreamPath, { clientIp: clientIp(request), timeoutMs: GET_TIMEOUT_MS });
  } catch {
    return json(503, OFFLINE, { "Retry-After": "30" });
  }
  return relay(response);
}

/**
 * The origin's own answers (202, 404, 409, 422, 429, the API's JSON 503) pass through.
 * Errors from the edge or the tunnel (HTML 502, 530...) become 503 offline.
 */
async function relay(response) {
  const contentType = response.headers.get("Content-Type") || "";
  if (isDown(response) && !(response.status === 503 && contentType.includes("application/json"))) {
    await response.body?.cancel();
    return json(503, OFFLINE, { "Retry-After": "60" });
  }
  return passText(response.status, await response.text(), {
    contentType,
    retryAfter: response.headers.get("Retry-After"),
  });
}
