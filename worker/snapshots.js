// Last good copy of the dashboard and status answers, kept in Workers KV.
//
// The cron trigger copies them from the origin every 10 minutes. A copy is written only
// when its content changed, so KV writes stay far below the free daily limit. When the
// origin is down, the API routes answer from these copies and say so in headers.
// Bodies are handled as text: the dashboard is never parsed here.

import { json, passText } from "./http.js";
import { originFetch } from "./origin.js";

export const SNAPSHOT_SOURCES = [
  { key: "dashboard:public", path: "/dashboard?dataset=public" },
  { key: "dashboard:synthetic", path: "/dashboard?dataset=synthetic" },
  { key: "status", path: "/status" },
];

const SCHEDULED_TIMEOUT_MS = 10000;

// Fields that change on every request without the data changing. They are blanked
// before hashing, so a new timestamp alone does not cost a KV write.
const VOLATILE = /"(generated_at|now|heartbeat_age_seconds|last_heartbeat)"\s*:\s*("(?:[^"\\]|\\.)*"|-?[0-9.eE+-]+|null)/g;

export function stableView(body) {
  return body.replace(VOLATILE, '"$1":null');
}

export async function sha256Hex(text) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

/** Answer from the stored copy, or 503 {"error": "offline"} when there is none. */
export async function snapshotResponse(env, key) {
  let record = null;
  try {
    record = env.SNAPSHOTS ? await env.SNAPSHOTS.get(key, "json") : null;
  } catch {
    record = null;
  }
  if (!record || typeof record.body !== "string") {
    return json(503, { error: "offline" }, { "Retry-After": "60" });
  }
  return passText(200, record.body, {
    extra: { "X-Data-Source": "snapshot", "X-Snapshot-At": String(record.fetched_at || "") },
  });
}

/** Cron job: refresh every copy whose content changed. Returns one outcome per key. */
export async function refreshSnapshots(env, now = () => new Date()) {
  if (!env.SNAPSHOTS) return [];
  const outcomes = await Promise.all(SNAPSHOT_SOURCES.map((source) => refreshOne(env, source, now)));
  console.log(`snapshots ${outcomes.map((o) => `${o.key}=${o.result}`).join(" ")}`);
  return outcomes;
}

async function refreshOne(env, { key, path }, now) {
  let body;
  try {
    const response = await originFetch(env, path, { timeoutMs: SCHEDULED_TIMEOUT_MS });
    if (response.status !== 200) {
      await response.body?.cancel();
      return { key, result: `http_${response.status}` };
    }
    body = await response.text();
  } catch {
    return { key, result: "unreachable" };
  }
  const sha256 = await sha256Hex(stableView(body));
  const stored = await env.SNAPSHOTS.getWithMetadata(key, "text");
  if (stored && stored.metadata && stored.metadata.sha256 === sha256) {
    return { key, result: "unchanged" };
  }
  const fetchedAt = now().toISOString();
  await env.SNAPSHOTS.put(key, JSON.stringify({ body, fetched_at: fetchedAt, sha256 }), {
    metadata: { sha256, fetched_at: fetchedAt },
  });
  return { key, result: "written" };
}
