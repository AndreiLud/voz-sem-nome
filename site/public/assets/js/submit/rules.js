// Pure rules of the submit page: field validation, tracking codes and API answers.
// No DOM here, so the same rules hold wherever they are called from.

export const FIELDS = ["score", "area", "tenure", "comment", "consent", "turnstile"];

/** Characters as the API counts them (code points after NFC normalization). */
export function charCount(text) {
  return [...String(text || "").normalize("NFC")].length;
}

/**
 * Error key per field (relative to submit.errors), only for invalid fields.
 * values: {score, area, tenure, comment, consent, turnstile}; `only` limits the check.
 */
export function validate(values, { maxChars, only = FIELDS }) {
  const checks = {
    score: () => /^(10|[0-9])$/.test(String(values.score ?? "")) ? null : "score",
    area: () => (values.area ? null : "area"),
    tenure: () => (values.tenure ? null : "tenure"),
    comment: () => (charCount(String(values.comment || "").trim()) > maxChars ? "comment_long" : null),
    consent: () => (values.consent ? null : "consent"),
    turnstile: () => (values.turnstile ? null : "turnstile"),
  };
  const errors = {};
  for (const name of only) {
    const key = checks[name]?.();
    if (key) errors[name] = key;
  }
  return errors;
}

// Tracking codes: 8 characters from the API's alphabet (no 0, O, 1, I or L).
const CODE = /^([2-9A-HJKMNP-Z]{4})([2-9A-HJKMNP-Z]{4})$/;

/** "ab2c de3f" -> "AB2C-DE3F"; null when it cannot be a code. */
export function normalizeCode(text) {
  const raw = String(text || "").replace(/[\s-]/g, "").toUpperCase();
  const match = CODE.exec(raw);
  return match ? `${match[1]}-${match[2]}` : null;
}

export const TERMINAL = new Set(["done", "review", "skipped", "failed"]);
export const FINISHED = new Set(["done", "review"]);
export const STAGES = { queued: 1, processing: 2, done: 3, review: 3, skipped: 3, failed: 3 };

/**
 * One shape for the submission answers. The API puts the queue position in
 * `queue_position` and the result fields at the top level; the older shape with
 * `position` and a nested `result` is still accepted.
 */
export function normalizeSubmission(data) {
  const source = data.result || (FINISHED.has(data.status) ? data : null);
  return {
    code: data.code || null,
    status: data.status,
    position: data.queue_position ?? data.position ?? null,
    eta: data.eta_seconds ?? null,
    comment: data.comment_anon || "",
    pii: data.pii_counts || {},
    lang: data.lang || null,
    result: source && {
      themes: source.themes || [],
      sentiment: source.sentiment || null,
      critical: Boolean(source.critical),
      criticalTypes: source.critical_types || [],
      seconds: source.processing_seconds ?? null,
      model: source.model || null,
    },
  };
}

/** Field name -> error key for the API's 422 "fields" object. */
export function fieldErrorsFromApi(fields) {
  const map = { score: "score", area: "area", tenure: "tenure", comment: "comment_long", consent: "consent" };
  const errors = {};
  for (const name of Object.keys(fields || {})) {
    if (map[name]) errors[name] = map[name];
  }
  return errors;
}

/**
 * Message key (relative to submit.api_errors) and values for a failed POST.
 * `result` is the object returned by lib/api.js apiFetch.
 */
export function apiErrorMessage(result) {
  const code = result.error;
  if (result.status === 0) return { key: "network" };
  if (result.status === 409 || code === "duplicate") return { key: "duplicate" };
  if (result.status === 429 || code === "rate_limited") {
    const seconds = result.retryAfter ?? result.data?.retry_after ?? 60;
    return { key: "rate_limited", values: { minutes: Math.max(1, Math.ceil(seconds / 60)) } };
  }
  if (code === "queue_full") return { key: "queue_full" };
  if (code === "turnstile") return { key: "turnstile" };
  if (code === "turnstile_unavailable") return { key: "turnstile_unavailable" };
  if (result.status === 413 || code === "too_large") return { key: "too_large" };
  if (result.status === 422 || code === "validation") return { key: "validation" };
  if (result.status === 503 || code === "offline") return { key: "offline" };
  return { key: "unknown" };
}
