// Cloudflare Worker of voz-sem-nome.andreiludescher.com.
//
// The static site in site/public is served by the assets binding without running this
// script (run_worker_first covers /api/* only). For /api/* the Worker keeps a short
// allowlist in front of the public API that runs at home behind a Cloudflare Tunnel:
// Turnstile and a per-IP rate limit before submissions, a shared secret on every origin
// request, and KV snapshots of the dashboard and status for when the origin is down.
// Request bodies are never logged.
//
// Bindings and secrets (see wrangler.jsonc): ASSETS, SNAPSHOTS (KV), RL (rate limit),
// API_ORIGIN (var), ORIGIN_SHARED_SECRET and TURNSTILE_SECRET (secrets).

import { handleApi } from "./api.js";
import { refreshSnapshots } from "./snapshots.js";

export default {
  async fetch(request, env) {
    const { pathname } = new URL(request.url);
    if (pathname === "/api" || pathname.startsWith("/api/")) {
      return handleApi(request, env);
    }
    return env.ASSETS.fetch(request);
  },

  async scheduled(controller, env, ctx) {
    ctx.waitUntil(refreshSnapshots(env));
  },
};
