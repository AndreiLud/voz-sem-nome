// Cloudflare Worker of voz-sem-nome.andreiludescher.com: the whole live demo, with no server
// at home.
//
// The static site in site/public is served by the assets binding without running this script
// (run_worker_first covers /api/* only). For /api/* the Worker is the API: Turnstile and rate
// limits, validation, anonymization in JavaScript, storage in D1 and classification with Workers
// AI after the answer is sent (see api.js for the routes). Two cron triggers retry unfinished
// classifications and purge expired data. Comments and request bodies are never logged.
//
// Bindings, vars and secrets: see wrangler.jsonc. Tests: node --test worker/test/

import { createApp } from "./app.js";

export default createApp();
