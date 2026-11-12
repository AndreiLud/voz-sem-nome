// The Worker as an object with fetch() and scheduled(), built around a clock and a source of
// randomness so tests can drive both. index.js exports the production instance.

import { handleApi } from "./api.js";
import { randomBytes as platformRandomBytes } from "./ids.js";
import { logEvent } from "./log.js";
import { purgeExpired, retryPending } from "./processing.js";
import { SETTINGS } from "./settings.js";

// Cron strings of wrangler.jsonc (triggers.crons): the daily purge runs at 03:17 in Sao Paulo.
export const CRON_RETRY = "*/10 * * * *";
export const CRON_PURGE = "17 6 * * *";

const isApi = (pathname) => pathname === "/api" || pathname.startsWith("/api/");

/**
 * @param {{now?: () => number, randomBytes?: (n: number) => Uint8Array, settings?: object}} options
 */
export function createApp({ now = Date.now, randomBytes = platformRandomBytes, settings = SETTINGS } = {}) {
  // Per isolate: minute caches of status, dashboard and job time; the IP salt of the day.
  const deps = { now, randomBytes, settings, memo: new Map(), saltMemo: { day: null, salt: null } };

  return {
    async fetch(request, env, ctx) {
      const { pathname } = new URL(request.url);
      if (isApi(pathname)) return handleApi(request, env, ctx, deps);
      return env.ASSETS.fetch(request);
    },

    async scheduled(controller, env, ctx) {
      const job = controller.cron === CRON_PURGE ? purgeExpired(deps, env) : retryPending(deps, env);
      ctx.waitUntil(job.catch((err) => logEvent("cron_error", { cron: controller.cron === CRON_PURGE ? "purge" : "retry", name: err?.name ?? "error" })));
    },
  };
}
