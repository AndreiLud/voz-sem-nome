// Same-origin calls to the public API (/api/v1/public). Never throws for HTTP errors:
// callers branch on `status` and `error`. Network failures and timeouts come back as
// status 0 with error "network" or "timeout".

export const API_BASE = "/api/v1/public";
const DEFAULT_TIMEOUT_MS = 15000;

/**
 * @returns {Promise<{ok: boolean, status: number, data: any, error: string|null,
 *   retryAfter: number|null, source: string, snapshotAt: string|null}>}
 */
export async function apiFetch(path, { method = "GET", body, timeoutMs = DEFAULT_TIMEOUT_MS, signal } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort("timeout"), timeoutMs);
  if (signal) signal.addEventListener("abort", () => controller.abort(signal.reason), { once: true });
  try {
    const response = await fetch(API_BASE + path, {
      method,
      headers: body === undefined ? { Accept: "application/json" } : { Accept: "application/json", "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
      credentials: "same-origin",
    });
    const data = await response.json().catch(() => null);
    const retry = Number(response.headers.get("Retry-After"));
    return {
      ok: response.ok,
      status: response.status,
      data,
      error: response.ok ? null : (data && data.error) || `http_${response.status}`,
      retryAfter: Number.isFinite(retry) && retry > 0 ? retry : null,
      source: response.headers.get("X-Data-Source") || "live",
      snapshotAt: response.headers.get("X-Snapshot-At"),
    };
  } catch (error) {
    const timedOut = controller.signal.reason === "timeout";
    return { ok: false, status: 0, data: null, error: timedOut ? "timeout" : "network", retryAfter: null, source: "none", snapshotAt: null };
  } finally {
    clearTimeout(timer);
  }
}

/** Static JSON file of the site (e.g. /data/dashboard-synthetic.json); null on failure. */
export async function staticJson(url) {
  try {
    const response = await fetch(url, { headers: { Accept: "application/json" } });
    return response.ok ? await response.json() : null;
  } catch {
    return null;
  }
}
