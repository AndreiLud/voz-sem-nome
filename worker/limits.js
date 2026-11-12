// Submission limits, checked after Turnstile. The per-IP limits are keyed by a daily-salted
// HMAC of the IP (the salt rotates every day, so days cannot be linked); the address itself
// goes nowhere but Turnstile's siteverify.
//   ip_window    the RL binding (per Cloudflare location, approximate), skipped when absent
//   ip_day       limits.per_ip_day_max per Sao Paulo day, counted in D1
//   global_hour  limits.global_hour_max submissions in the last hour, counted in D1
// Each limit answers with the seconds until a slot frees up, like enps.api.ratelimit.

import { HOUR_MS, nextSpMidnight, secondsUntil, spDay, toIso } from "./clock.js";
import { hmacHex, randomHex } from "./ids.js";

export const RL_PERIOD_S = 60; // same period as the RL binding in wrangler.jsonc
export const QUOTA_KEY = "quota_exhausted_day";
const SALT_PREFIX = "ip_salt:";

/** True when the RL binding refuses `key`. A missing binding or a failing call never blocks. */
export async function overWindowLimit(env, key) {
  if (!env.RL) return false;
  try {
    const { success } = await env.RL.limit({ key });
    return success === false;
  } catch {
    return false;
  }
}

/**
 * HMAC of `ip` under the salt of `day`. The salt is created on first use (32 random bytes in
 * meta) and kept in `memo` for the rest of the day; the daily cron deletes older salts.
 */
export async function ipHash(store, memo, ip, day, bytes) {
  if (memo.day !== day) {
    const salt = await store.metaOrInsert(SALT_PREFIX + day, randomHex(32, bytes));
    memo.day = day;
    memo.salt = salt;
  }
  return hmacHex(memo.salt, ip);
}

/**
 * The D1 limits and the quota flag in one round trip. Returns {limited: {scope, retryAfter}
 * | null, quotaDay}.
 */
export async function checkDailyAndHourly(store, settings, { ipKey, now }) {
  const limits = settings.limits;
  const day = spDay(now);
  const state = await store.limitState({
    day,
    ipHash: ipKey,
    hourCutoff: toIso(now - HOUR_MS),
    globalMax: limits.global_hour_max,
    quotaKey: QUOTA_KEY,
  });
  let limited = null;
  if (state.ipSubmissions >= limits.per_ip_day_max) {
    limited = { scope: "ip_day", retryAfter: secondsUntil(nextSpMidnight(now), now) };
  } else if (state.hourFullSince !== null) {
    limited = { scope: "global_hour", retryAfter: secondsUntil(Date.parse(state.hourFullSince) + HOUR_MS, now) };
  }
  return { limited, quotaDay: state.quotaDay };
}
