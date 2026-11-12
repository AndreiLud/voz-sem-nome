import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { buildDashboard } from "../metrics/aggregates.js";
import { ensureSchema } from "../store.js";
import { apiRequest, makeApp, makeCtx, makeEnv, seedSubmissions } from "./helpers.js";

async function getJson(app, env, path) {
  const response = await app.fetch(apiRequest(path), env, makeCtx());
  return { response, data: await response.json() };
}

const done = (fields) => ({
  status: "done", themes: '["car"]', sentiment: "neg", critical: 0, critical_types: "[]",
  model: "@cf/google/gemma-4-26b-a4b-it", prompt_version: "classify.v3+708a3315", comment_anon: "texto anonimizado",
  ...fields,
});

describe("GET /api/v1/public/status", () => {
  it("answers the flat fields the site reads", async () => {
    const env = makeEnv();
    const { app } = makeApp();
    await ensureSchema(env.DB);
    // Today in Sao Paulo started at 03:00 UTC; the clock says 15:00 UTC.
    await seedSubmissions(env.DB, [
      done({ created_at: "2026-11-11T14:00:00.000+00:00", started_at: "2026-11-11T14:00:00.000+00:00", finished_at: "2026-11-11T14:00:04.000+00:00" }),
      done({ created_at: "2026-11-11T14:10:00.000+00:00", started_at: "2026-11-11T14:10:00.000+00:00", finished_at: "2026-11-11T14:10:10.000+00:00", critical: 1, critical_types: '["am"]' }),
      done({ created_at: "2026-11-10T14:00:00.000+00:00", started_at: "2026-11-10T14:00:00.000+00:00", finished_at: "2026-11-10T14:00:30.000+00:00" }),
      { status: "processing", created_at: "2026-11-11T14:59:00.000+00:00", started_at: "2026-11-11T14:59:00.000+00:00", attempts: 1 },
      { status: "queued_quota", created_at: "2026-11-11T14:58:00.000+00:00" },
    ]);
    env.DB.sqlite.exec(`
      INSERT INTO daily (day, key, value) VALUES
        ('2026-11-10', 'submissions', 4), ('2026-11-10', 'distinct_submitters', 3), ('2026-11-10', 'classified', 1),
        ('2026-11-11', 'submissions', 4), ('2026-11-11', 'distinct_submitters', 2), ('2026-11-11', 'classified', 2),
        ('2026-11-11', 'critical', 1);
      INSERT INTO alerts (public_id, critical_types, submitted_at, detected_at, delivered_at, mode) VALUES
        ('x', '["am"]', '2026-11-11T14:10:00.000+00:00', '2026-11-11T14:10:10.000+00:00', '2026-11-11T14:10:10.000+00:00', 'immediate'),
        ('y', '["bo"]', '2026-11-10T14:00:00.000+00:00', '2026-11-10T14:00:06.000+00:00', '2026-11-10T14:00:06.000+00:00', 'immediate');
    `);
    const { response, data } = await getJson(app, env, "/api/v1/public/status");
    assert.equal(response.status, 200);
    assert.deepEqual(data, {
      generated_at: "2026-11-11T15:00:00.000+00:00",
      pending: 1,
      processing: 1,
      eta_seconds: 15, // mean of the last jobs: (4 + 10 + 30) / 3 = 14.7
      analyzed_today: 2,
      latency_p50_s: 4,
      latency_p95_s: 10,
      critical_today: 1,
      alerts_recorded: 1,
      quota_exhausted_today: false,
      production: {
        total_submissions: 8,
        distinct_submitters: 5,
        classified: 3,
        latency_p50_seconds: 10,
        latency_p95_seconds: 30,
        alerts_sent: 2,
        mean_submission_to_alert_seconds: 8,
        first_public_day: "2026-11-10",
      },
    });
  });

  it("reports the quota of today and no ETA while it lasts", async () => {
    const env = makeEnv();
    const { app } = makeApp();
    await ensureSchema(env.DB);
    env.DB.sqlite.exec("INSERT INTO meta (key, value) VALUES ('quota_exhausted_day', '2026-11-11')");
    const { data } = await getJson(app, env, "/api/v1/public/status");
    assert.equal(data.quota_exhausted_today, true);
    assert.equal(data.eta_seconds, null);
    assert.equal(data.analyzed_today, 0);
    assert.equal(data.latency_p50_s, null);
    assert.equal(data.production.total_submissions, 0);
  });

  it("is computed once a minute per isolate", async () => {
    const env = makeEnv();
    const { app, now } = makeApp();
    const first = (await getJson(app, env, "/api/v1/public/status")).data;
    await seedSubmissions(env.DB, [{ status: "processing", attempts: 1, started_at: "2026-11-11T14:59:00.000+00:00" }]);
    now.advance(59_000);
    assert.deepEqual((await getJson(app, env, "/api/v1/public/status")).data, first);
    now.advance(1_000);
    const later = (await getJson(app, env, "/api/v1/public/status")).data;
    assert.equal(later.processing, 1);
    assert.equal(later.generated_at, "2026-11-11T15:01:00.000+00:00");
  });
});

describe("GET /api/v1/public/dashboard", () => {
  it("builds the public dashboard from the D1 rows with the metrics port", async () => {
    const env = makeEnv();
    const { app } = makeApp();
    const rows = Array.from({ length: 6 }, (_, i) => done({ score: i + 4, created_at: `2026-11-1${i % 2}T12:00:00.000+00:00`, finished_at: "2026-11-11T12:00:05.000+00:00" }));
    await seedSubmissions(env.DB, rows);
    const { response, data } = await getJson(app, env, "/api/v1/public/dashboard?dataset=public");
    assert.equal(response.status, 200);
    const stored = env.DB.rows(
      "SELECT public_id, created_at, score, area, tenure, comment_anon, status, themes, sentiment, critical, " +
        "critical_types, finished_at, model, prompt_version, anonymizer_version FROM submissions ORDER BY id",
    );
    assert.deepEqual(data, buildDashboard(stored, { dataset: "public", now: Date.parse("2026-11-11T15:00:00Z") }));
    assert.equal(data.dataset, "public");
    assert.equal(data.totals.responses, 6);
    assert.equal(data.pipeline.model, "@cf/google/gemma-4-26b-a4b-it");
    // The default dataset is public, and the answer is kept for a minute.
    await seedSubmissions(env.DB, [done({})]);
    assert.deepEqual((await getJson(app, env, "/api/v1/public/dashboard")).data, data);
  });

  it("serves the synthetic dashboard from the static file", async () => {
    const env = makeEnv();
    const { app } = makeApp();
    const response = await app.fetch(apiRequest("/api/v1/public/dashboard?dataset=synthetic"), env, makeCtx());
    assert.equal(response.status, 200);
    assert.match(response.headers.get("Content-Type"), /^application\/json/);
    assert.deepEqual(await response.json(), { dataset: "synthetic", sample: true });
    assert.deepEqual(env.ASSETS.requests, ["/data/dashboard-synthetic.json"]);
    assert.equal(env.DB.log.length, 0);
  });

  it("answers 503 when the static file is missing", async () => {
    const env = makeEnv();
    env.ASSETS.files = {};
    const { app } = makeApp();
    const { response, data } = await getJson(app, env, "/api/v1/public/dashboard?dataset=synthetic");
    assert.equal(response.status, 503);
    assert.deepEqual(data, { error: "unavailable" });
  });

  it("answers 422 to an unknown dataset", async () => {
    const { app } = makeApp();
    const { response, data } = await getJson(app, makeEnv(), "/api/v1/public/dashboard?dataset=private");
    assert.equal(response.status, 422);
    assert.deepEqual(data, { error: "validation", fields: { dataset: "must be public or synthetic" } });
  });
});
