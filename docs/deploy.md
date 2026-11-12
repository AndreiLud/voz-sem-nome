# Deploy

The project runs in two ways.

* **Live demo (edge).** Everything runs on Cloudflare: a Worker serves the static site and the API,
  anonymizes comments in JavaScript, stores them in D1 and classifies them with Workers AI. No
  server at home. The Worker is in `worker/`, its configuration in `wrangler.jsonc`.
* **Local mode.** The Python stack in `docker compose` (FastAPI, spaCy, Ollama, n8n) for an
  organization that wants nothing to leave its own server. The synthetic dataset behind the
  published dashboard and metrics is analyzed here.

## Live demo on Cloudflare

### One-time setup

1. **D1 database.** Create it once and commit its id:

   ```sh
   npx wrangler d1 create voz-sem-nome
   ```

   Put the printed `database_id` in `wrangler.jsonc` (`d1_databases`, replacing
   `REPLACE_WITH_D1_DATABASE_ID`). The id is not secret. The Worker creates its tables on the first
   request, so there is no migration step. D1 has no location in South America, and the location
   (or a `--jurisdiction`) can only be chosen at creation. Deleted rows stay recoverable for 7 days
   (Time Travel, Free plan).

2. **Turnstile.** In the dashboard, Turnstile, add a widget for `voz-sem-nome.andreiludescher.com`
   (managed mode). The site key goes into the static site at build time:

   ```sh
   PYTHONPATH=src TURNSTILE_SITE_KEY=<site key> .venv/bin/python -m enps.export.site --release
   ```

   Commit `site/public`. The secret key becomes the Worker secret `TURNSTILE_SECRET` (step 4).

3. **Workers Builds.** Workers & Pages, `voz-sem-nome`, Settings, Builds: connect the GitHub
   repository, branch `main`, root directory `/`, no build command, and keep the default deploy
   command `npx wrangler deploy`. Every push to `main` deploys. The deploy applies
   `wrangler.jsonc`: custom domain, bindings (assets, AI, D1, rate limit), crons and vars.

4. **Secrets.** In Settings, Variables and Secrets (type Secret), or with `npx wrangler secret put`:

   | Secret | Required | What it does |
   |---|---|---|
   | `TURNSTILE_SECRET` | yes | Turnstile secret key. Without it every submission is refused. |
   | `EVAL_TOKEN` | for the evaluation | Turns on `POST /api/v1/internal/evaluate`. Without it the route answers 404. |
   | `ALERT_WEBHOOK_URL` | no | n8n webhook that receives immediate critical-case alerts. |
   | `ALERT_WEBHOOK_TOKEN` | no | Sent as `X-Internal-Token` with each alert. |

   Generate tokens with `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`. Build
   variables are not visible to the Worker at runtime; only these secrets and `vars` are.

5. **Check.** After the first deploy:

   ```sh
   curl https://voz-sem-nome.andreiludescher.com/api/v1/public/health   # {"status":"ok"}
   curl https://voz-sem-nome.andreiludescher.com/api/v1/public/status
   ```

### What to know

* **Model.** `vars.AI_MODEL` in `wrangler.jsonc` (default `@cf/google/gemma-4-26b-a4b-it`). The
  account has 10,000 free neurons a day, shared by every Worker of the account, reset at 00:00 UTC
  (21:00 in Brasília). One comment costs about 20 neurons, 40 when the verifier runs. When the
  allocation runs out, new comments get `503 {"error": "quota"}`, rows already stored wait as
  `queued_quota`, and the cron classifies them after the reset.
* **Limits.** 3 submissions per minute per IP (rate limit binding, skipped if the plan does not
  offer it), `limits.per_ip_day_max` per IP per day and `limits.global_hour_max` per hour for
  everyone (both in D1), all from `config/settings.yaml`.
* **Crons.** Every 10 minutes: retry classifications that did not finish. At 06:17 UTC: purge
  submissions after `privacy.public_retention_days`, alerts after
  `privacy.alert_log_retention_days` and the daily IP hashes.
* **Logs.** Workers Logs keep only event names and error codes. Invocation logs are off. The
  Worker never calls AI Gateway, so Cloudflare keeps no prompt or answer.
* **Settings.** The Worker reads a copy of `config/settings.yaml`. After changing that file, run
  `.venv/bin/python scripts/build_worker_settings.py` (the tests fail while the copy is stale).
  The anonymizer, classifier and dashboard data modules have their own build scripts in `scripts/`.

## Worker tests

No npm packages are needed. With Node 24 in Docker:

```sh
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD":/w -w /w node:24-bookworm-slim node --test worker/test/
```

D1 is emulated with `node:sqlite`; Workers AI, Turnstile, the assets binding and the webhook are
fakes. Nothing reaches the network.

## Evaluating the deployed classifier

`src/enps/evaluation/edge_client.py` sends the synthetic comments to the evaluate route, which
runs the same anonymizer and classifier as a live submission and stores nothing. It spends neurons
from the same daily allocation as the demo, so give it a budget:

```sh
export EDGE_EVAL_TOKEN=<the EVAL_TOKEN secret>
PYTHONPATH=src .venv/bin/python -m enps.evaluation.edge_client run --split test --budget-neurons 4000
PYTHONPATH=src .venv/bin/python -m enps.evaluation.edge_client summary --split test
```

Splits: `test`, `dev`, `dev_quick`, `all` and `benchmark300` (the first 300 comments). Answers
are appended to `metrics/edge/predictions_<split>.jsonl`; running the same command the next day
continues where it stopped. `summary` writes `metrics/edge/performance_<split>.json` (latency of
the model calls and end to end, tokens, neurons) and `metrics/edge/classification_<split>.json`
(agreement with the intended labels and, when `data/labels/human_test.csv` exists, with the human
labels).

## Local mode with docker compose

Requirements: Docker with Compose. No GPU is needed (see `docs/gpu.md`).

```sh
cp .env.example .env          # fill the secrets, see the comments in the file
docker compose up -d          # Ollama, the model download, both APIs, the queue worker and n8n
.venv/bin/python scripts/dev_server.py   # the site on http://127.0.0.1:8787, API proxied to port 8001
```

Everything listens on 127.0.0.1 only. The `tunnel` profile (`COMPOSE_PROFILES=tunnel`) can
publish the public API through a Cloudflare Tunnel; the live demo does not use it. Pipeline on the
synthetic data: `docker compose run --rm pipeline`.
