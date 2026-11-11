// Server side check of the Turnstile token sent by the form.

export const SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify";
export const MAX_TOKEN_CHARS = 2048;
const ATTEMPTS = 2;
const TIMEOUT_MS = 5000;

/**
 * "ok", "rejected" or "unavailable" (siteverify did not answer, or the secret is missing).
 * Both attempts share one idempotency key, so a retry cannot be counted as a second use
 * of the same token.
 */
export async function verifyTurnstile(env, token, ip) {
  if (!env.TURNSTILE_SECRET) return "unavailable";
  const payload = {
    secret: env.TURNSTILE_SECRET,
    response: token,
    idempotency_key: crypto.randomUUID(),
  };
  if (ip && ip !== "unknown") payload.remoteip = ip;
  for (let attempt = 0; attempt < ATTEMPTS; attempt += 1) {
    try {
      const response = await fetch(SITEVERIFY_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        signal: AbortSignal.timeout(TIMEOUT_MS),
      });
      if (response.status >= 500) continue;
      const outcome = await response.json();
      return outcome && outcome.success === true ? "ok" : "rejected";
    } catch {
      // network error, timeout or a body that is not JSON: try once more
    }
  }
  return "unavailable";
}
