// Requests to the public API (enps.api.public) behind the Cloudflare Tunnel.
//
// The browser calls /api/v1/public/...; the API serves the same routes under /v1/public/...
// Every request carries X-Origin-Secret, so the API can refuse anything that did not come
// through this Worker, and X-Client-IP, the only place the API learns the visitor's address
// (used for rate limits in memory, never stored).

export const UPSTREAM_PREFIX = "/v1/public";
export const GET_TIMEOUT_MS = 4000;
export const POST_TIMEOUT_MS = 15000;

/** fetch() to the origin. Throws on network errors and timeouts, like fetch itself. */
export function originFetch(env, path, { method = "GET", body, clientIp, timeoutMs = GET_TIMEOUT_MS } = {}) {
  const headers = {
    Accept: "application/json",
    "X-Origin-Secret": env.ORIGIN_SHARED_SECRET || "",
  };
  if (clientIp) headers["X-Client-IP"] = clientIp;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const base = String(env.API_ORIGIN || "").replace(/\/+$/, "");
  return fetch(base + UPSTREAM_PREFIX + path, {
    method,
    headers,
    body,
    redirect: "manual",
    signal: AbortSignal.timeout(timeoutMs),
  });
}

/**
 * True when the origin is effectively down: 5xx from the API or from Cloudflare
 * (502/504 from the edge, 530 when the tunnel has no connection).
 */
export function isDown(response) {
  return response.status >= 500;
}
