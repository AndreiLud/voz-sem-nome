import assert from "node:assert/strict";
import { afterEach, describe, it } from "node:test";
import worker from "../index.js";
import { apiRequest, makeApp, makeCtx, makeEnv, spyConsole } from "./helpers.js";

let spy;
afterEach(() => spy?.restore());

describe("routing", () => {
  it("serves non API paths from the assets binding", async () => {
    const env = makeEnv();
    const response = await worker.fetch(apiRequest("/painel/"), env, makeCtx());
    assert.equal(await response.text(), "asset /painel/");
    assert.equal(env.DB.log.length, 0);
  });

  const missing = [
    ["GET", "/api"],
    ["GET", "/api/"],
    ["GET", "/api/v1/public"],
    ["GET", "/api/v1/public/unknown"],
    ["GET", "/api/v1/public/submissions"],
    ["GET", "/api/v1/public/submissions/AB2C-DE3F/extra"],
    ["GET", "/api/v1/internal/alerts/pending"],
    ["GET", "/api/v1/internal/evaluate"],
    ["DELETE", "/api/v1/public/submissions/AB2C-DE3F"],
    ["PUT", "/api/v1/public/submissions"],
    ["OPTIONS", "/api/v1/public/submissions"],
    ["POST", "/api/v1/public/status"],
    ["GET", "/api/v2/public/status"],
  ];
  for (const [method, path] of missing) {
    it(`answers ${method} ${path} with a JSON 404`, async () => {
      const env = makeEnv();
      const response = await worker.fetch(apiRequest(path, { method }), env, makeCtx());
      assert.equal(response.status, 404);
      assert.deepEqual(await response.json(), { error: "not_found" });
      assert.match(response.headers.get("Content-Type"), /^application\/json/);
      assert.equal(response.headers.get("Cache-Control"), "no-store");
      assert.equal(response.headers.get("Access-Control-Allow-Origin"), null);
      assert.equal(env.DB.log.length, 0);
    });
  }

  it("answers GET /health with ok once D1 answers", async () => {
    const env = makeEnv();
    const response = await worker.fetch(apiRequest("/api/v1/public/health"), env, makeCtx());
    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), { status: "ok" });
    assert.ok(env.DB.row("SELECT name FROM sqlite_master WHERE name = 'submissions'"));
  });

  it("answers 503 on /health when D1 fails", async () => {
    const env = makeEnv();
    env.DB.failWith = new Error("D1_ERROR: storage down");
    const response = await worker.fetch(apiRequest("/api/v1/public/health"), env, makeCtx());
    assert.equal(response.status, 503);
    assert.deepEqual(await response.json(), { error: "unavailable" });
  });

  it("turns an unexpected failure into a JSON 500 and logs only the error name", async () => {
    spy = spyConsole();
    const env = makeEnv();
    env.DB.failWith = new Error("D1_ERROR: storage down, secret detail");
    const response = await worker.fetch(apiRequest("/api/v1/public/status"), env, makeCtx());
    assert.equal(response.status, 500);
    assert.deepEqual(await response.json(), { error: "internal" });
    assert.deepEqual(spy.lines, ["api_error name=Error"]);
  });

  it("creates the schema once per database binding", async () => {
    const { app } = makeApp();
    const env = makeEnv();
    for (let i = 0; i < 3; i += 1) await app.fetch(apiRequest("/api/v1/public/health"), env, makeCtx());
    const creates = env.DB.log.filter((q) => q.sql.includes("CREATE TABLE IF NOT EXISTS submissions"));
    assert.equal(creates.length, 1);
    const indexes = env.DB.rows("SELECT name FROM sqlite_master WHERE type = 'index' AND name NOT LIKE 'sqlite_%'").map((r) => r.name);
    assert.deepEqual(indexes.sort(), ["alerts_detected_at", "submissions_created_at", "submissions_dedupe_key", "submissions_status"]);
  });
});
