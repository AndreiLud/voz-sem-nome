import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { afterEach, describe, it } from "node:test";
import { ANONYMIZER_VERSION } from "../anonymize/index.js";
import {
  FakeAI, FakeRateLimiter, NOW, SITEVERIFY, TURNSTILE_SECRET, VALID, apiRequest, jsonResponse, makeApp, makeCtx,
  makeEnv, mockFetch, postSubmission, quotaError, spyConsole, stage1, standardFetch,
} from "./helpers.js";

let fetchMock;
afterEach(() => fetchMock?.restore());

const CODE = /^[2-9A-HJKMNP-Z]{4}-[2-9A-HJKMNP-Z]{4}$/;
const sha256 = (text) => createHash("sha256").update(text).digest("hex");

/** POST through a fresh app (or `app`), returning the response, its JSON and the context. */
async function post(body, { env = makeEnv(), app = makeApp().app, headers = {} } = {}) {
  const ctx = makeCtx();
  const response = await app.fetch(postSubmission(body, headers), env, ctx);
  return { response, data: await response.json(), ctx, env };
}

describe("POST /api/v1/public/submissions: Turnstile", () => {
  it("verifies the token with siteverify before touching D1", async () => {
    fetchMock = standardFetch();
    const { response, env } = await post(VALID);
    assert.equal(response.status, 202);
    const [verify] = fetchMock.calls;
    assert.equal(verify.url.href, SITEVERIFY);
    const sent = JSON.parse(verify.body);
    assert.equal(sent.secret, TURNSTILE_SECRET);
    assert.equal(sent.response, "token-ok");
    assert.equal(sent.remoteip, "203.0.113.7");
    assert.match(sent.idempotency_key, /^[0-9a-f-]{36}$/);
    assert.ok(env.DB.log.length > 0);
  });

  it("refuses a missing, empty or oversized token without siteverify or D1", async () => {
    for (const token of [undefined, "", 42, "x".repeat(2049)]) {
      fetchMock = standardFetch();
      const { response, data, env } = await post({ ...VALID, turnstile: token });
      assert.equal(response.status, 403);
      assert.deepEqual(data, { error: "turnstile" });
      assert.equal(fetchMock.calls.length, 0);
      assert.equal(env.DB.log.length, 0);
      fetchMock.restore();
    }
  });

  it("refuses a token that siteverify rejects", async () => {
    fetchMock = standardFetch({ success: false });
    const { response, data, env } = await post(VALID);
    assert.equal(response.status, 403);
    assert.deepEqual(data, { error: "turnstile" });
    assert.equal(env.DB.log.length, 0);
  });

  it("retries siteverify once with the same idempotency key, then answers 503", async () => {
    fetchMock = mockFetch(() => new Response("bad gateway", { status: 502 }));
    const { response, data } = await post(VALID);
    assert.equal(response.status, 503);
    assert.deepEqual(data, { error: "turnstile_unavailable" });
    assert.equal(response.headers.get("Retry-After"), "30");
    const keys = fetchMock.calls.map((c) => JSON.parse(c.body).idempotency_key);
    assert.equal(keys.length, 2);
    assert.equal(keys[0], keys[1]);
  });

  it("succeeds when the second siteverify attempt answers", async () => {
    let calls = 0;
    fetchMock = mockFetch(() => (calls++ === 0 ? Promise.reject(new TypeError("network")) : jsonResponse(200, { success: true })));
    const { response } = await post(VALID);
    assert.equal(response.status, 202);
  });

  it("fails closed when the Turnstile secret is missing", async () => {
    fetchMock = standardFetch();
    const { response, data } = await post(VALID, { env: makeEnv({ TURNSTILE_SECRET: undefined }) });
    assert.equal(response.status, 503);
    assert.deepEqual(data, { error: "turnstile_unavailable" });
    assert.equal(fetchMock.calls.length, 0);
  });

  it("skips Turnstile only with DEV_ALLOW_NO_TURNSTILE=1 and no secret", async () => {
    fetchMock = standardFetch();
    const env = makeEnv({ TURNSTILE_SECRET: undefined, DEV_ALLOW_NO_TURNSTILE: "1" });
    const { response } = await post({ ...VALID, turnstile: undefined }, { env });
    assert.equal(response.status, 202);
    assert.equal(fetchMock.calls.length, 0);
    const withSecret = makeEnv({ DEV_ALLOW_NO_TURNSTILE: "1" });
    assert.equal((await post({ ...VALID, turnstile: undefined }, { env: withSecret })).response.status, 403);
  });
});

describe("POST /api/v1/public/submissions: body and validation", () => {
  it("accepts JSON only", async () => {
    fetchMock = standardFetch();
    const { response, data } = await post(VALID, { headers: { "Content-Type": "text/plain" } });
    assert.equal(response.status, 415);
    assert.deepEqual(data, { error: "unsupported_media_type" });
  });

  it("refuses bodies over 8 KB, declared or streamed", async () => {
    fetchMock = standardFetch();
    const big = JSON.stringify({ ...VALID, comment: "x".repeat(9000) });
    assert.equal((await post(big)).response.status, 413);
    const { app } = makeApp();
    const stream = new ReadableStream({ start(c) { c.enqueue(new TextEncoder().encode(big)); c.close(); } });
    const request = apiRequest("/api/v1/public/submissions", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: stream, duplex: "half",
    });
    const response = await app.fetch(request, makeEnv(), makeCtx());
    assert.equal(response.status, 413);
    assert.deepEqual(await response.json(), { error: "too_large" });
  });

  it("answers 422 to a body that is not a JSON object", async () => {
    fetchMock = standardFetch();
    for (const body of ["{not json", "[]", "null", "42", "\"text\""]) {
      const { response, data } = await post(body);
      assert.equal(response.status, 422);
      assert.deepEqual(data, { error: "validation", fields: { body: "must be a JSON object" } });
    }
  });

  it("reports every invalid field with the messages of enps.queue.ingest", async () => {
    fetchMock = standardFetch();
    const cases = [
      [{ ...VALID, score: 11, area: "Operações", consent: false },
        { score: "must be an integer from 0 to 10", area: "unknown value", consent: "must be true" }],
      [{ ...VALID, score: true, tenure: "x", comment: 5, lang: "es", consent: "true" },
        { score: "must be an integer from 0 to 10", tenure: "unknown value", comment: "must be text",
          lang: "must be pt or en", consent: "must be true" }],
      [{ ...VALID, comment: "x".repeat(1001) }, { comment: "must have at most 1000 characters" }],
      [{ ...VALID, score: 4.5 }, { score: "must be an integer from 0 to 10" }],
      [{ turnstile: "token-ok" }, { score: "must be an integer from 0 to 10", area: "unknown value",
        tenure: "unknown value", consent: "must be true" }],
    ];
    for (const [body, fields] of cases) {
      const { response, data } = await post(body);
      assert.equal(response.status, 422, JSON.stringify(body).slice(0, 60));
      assert.deepEqual(data, { error: "validation", fields });
    }
  });

  it("counts characters after NFC and trimming, like the Python side", async () => {
    fetchMock = standardFetch();
    const decomposed = "é".repeat(1000); // 2000 code units, 1000 characters in NFC
    assert.equal((await post({ ...VALID, comment: `  ${decomposed}\n` })).response.status, 202);
    assert.equal((await post({ ...VALID, comment: "😀".repeat(1000) })).response.status, 202);
  });
});

describe("POST /api/v1/public/submissions: accepted", () => {
  it("answers 202 with the FastAPI fields and stores only the anonymized comment", async () => {
    fetchMock = standardFetch();
    const held = makeEnv({ AI: new FakeAI({}, { hold: true }) });
    const { response, data, env, ctx } = await post({ ...VALID, comment: "O João Silva grita com todo mundo, meu cpf é 123.456.789-09" }, { env: held });
    assert.equal(response.status, 202);
    assert.equal(response.headers.get("Cache-Control"), "no-store");
    assert.deepEqual(Object.keys(data), ["code", "status", "queue_position", "eta_seconds", "comment_anon", "pii_counts"]);
    assert.match(data.code, CODE);
    assert.equal(data.status, "processing");
    assert.equal(data.queue_position, 1);
    assert.equal(data.eta_seconds, 15);
    assert.equal(data.comment_anon, "O [PESSOA] grita com todo mundo, meu cpf é [CPF]");
    assert.deepEqual(data.pii_counts, { CPF: 1, PESSOA: 1 });

    const row = env.DB.row("SELECT * FROM submissions");
    assert.equal(row.tracking_hash, sha256(data.code.replace("-", "")));
    assert.match(row.public_id, /^[A-Za-z0-9_-]{10}$/);
    assert.equal(row.created_at, "2026-11-11T15:00:00.000+00:00");
    assert.equal(row.comment_anon, data.comment_anon);
    assert.equal(row.comment_chars, 59);
    assert.equal(row.pii_counts, '{"CPF":1,"PESSOA":1}');
    assert.equal(row.anonymizer_version, ANONYMIZER_VERSION);
    assert.equal(row.status, "processing");
    assert.equal(row.attempts, 1);
    assert.equal(row.started_at, row.created_at);
    const stored = JSON.stringify(env.DB.rows("SELECT * FROM submissions")) + JSON.stringify(env.DB.rows("SELECT * FROM submitters_today"));
    assert.ok(!stored.includes("João") && !stored.includes("123.456.789-09") && !stored.includes("203.0.113.7"));
    assert.equal(ctx.pending.length, 1);
  });

  it("skips an empty comment right away, without the model", async () => {
    fetchMock = standardFetch();
    const env = makeEnv({ RL: undefined });
    for (const comment of [undefined, null, "", "   \n"]) {
      const { response, data, ctx } = await post({ ...VALID, comment }, { env });
      assert.equal(response.status, 202);
      assert.equal(data.status, "skipped");
      assert.equal(data.queue_position, null);
      assert.equal(data.eta_seconds, null);
      assert.equal(data.comment_anon, "");
      assert.deepEqual(data.pii_counts, {});
      assert.equal(ctx.pending.length, 0);
    }
    assert.equal(env.AI.calls.length, 0);
    const rows = env.DB.rows("SELECT status, finished_at, started_at, attempts, anonymizer_version, dedupe_key FROM submissions");
    assert.equal(rows.length, 4);
    for (const row of rows) {
      assert.deepEqual(row, { status: "skipped", finished_at: "2026-11-11T15:00:00.000+00:00", started_at: null, attempts: 0, anonymizer_version: "", dedupe_key: null });
    }
  });

  it("counts submissions and distinct submitters per Sao Paulo day without storing IPs", async () => {
    fetchMock = standardFetch();
    const env = makeEnv({ RL: undefined });
    const { app } = makeApp();
    await post({ ...VALID, comment: "primeiro comentário bem longo aqui" }, { env, app });
    await post({ ...VALID, comment: "segundo comentário bem longo aqui" }, { env, app });
    await post({ ...VALID, comment: "terceiro comentário bem longo aqui" }, { env, app, headers: { "CF-Connecting-IP": "198.51.100.20" } });
    const daily = Object.fromEntries(env.DB.rows("SELECT key, value FROM daily WHERE day = '2026-11-11' AND key IN ('submissions', 'distinct_submitters')").map((r) => [r.key, r.value]));
    assert.deepEqual(daily, { submissions: 3, distinct_submitters: 2 });
    const hashes = env.DB.rows("SELECT ip_hash, submissions, accepted FROM submitters_today");
    assert.equal(hashes.length, 2);
    assert.ok(hashes.every((h) => /^[0-9a-f]{64}$/.test(h.ip_hash)));
    assert.equal(env.DB.rows("SELECT key FROM meta WHERE key LIKE 'ip_salt:%'").length, 1);
  });

  it("answers 409 to the same anonymized comment within 24 hours", async () => {
    fetchMock = standardFetch();
    const env = makeEnv({ RL: undefined });
    const { app, now } = makeApp();
    assert.equal((await post({ ...VALID, comment: "Minha gestora é ótima comigo" }, { env, app })).response.status, 202);
    const dup = await post({ ...VALID, comment: "minha gestora é ÓTIMA comigo!" }, { env, app, headers: { "CF-Connecting-IP": "198.51.100.9" } });
    assert.equal(dup.response.status, 409);
    assert.deepEqual(dup.data, { error: "duplicate" });
    assert.equal((await post({ ...VALID, comment: "ok" }, { env, app })).response.status, 202);
    assert.equal((await post({ ...VALID, comment: "ok" }, { env, app })).response.status, 202); // too short to dedupe
    now.advance(24 * 3600_000 + 1);
    assert.equal((await post({ ...VALID, comment: "minha gestora é ótima comigo" }, { env, app })).response.status, 202);
    assert.equal(env.DB.row("SELECT value FROM daily WHERE key = 'rejected_duplicate'").value, 1);
    assert.equal(env.DB.row("SELECT dedupe_key FROM submissions WHERE id = 1").dedupe_key,
      "a8daa5679b61fcdbb0f235013f681b068c2bf38573071f29d55884543d879101");
  });
});

describe("POST /api/v1/public/submissions: limits", () => {
  it("applies the RL binding per IP with Retry-After", async () => {
    fetchMock = standardFetch();
    const env = makeEnv({ RL: new FakeRateLimiter(1) });
    const { app } = makeApp();
    assert.equal((await post({ ...VALID, comment: "um comentário qualquer de teste" }, { env, app })).response.status, 202);
    const blocked = await post({ ...VALID, comment: "outro comentário qualquer de teste" }, { env, app });
    assert.equal(blocked.response.status, 429);
    assert.deepEqual(blocked.data, { error: "rate_limited", scope: "ip_window", retry_after: 60 });
    assert.equal(blocked.response.headers.get("Retry-After"), "60");
    // Keyed by the daily-salted HMAC of the address, never the address.
    assert.equal(env.RL.calls.length, 2);
    assert.equal(env.RL.calls[0], env.RL.calls[1]);
    assert.match(env.RL.calls[0], /^[0-9a-f]{64}$/);
    assert.equal(env.RL.calls[0], env.DB.row("SELECT ip_hash FROM submitters_today").ip_hash);
    assert.equal(env.DB.row("SELECT value FROM daily WHERE key = 'rejected_rate_limit'").value, 1);
  });

  it("works without the RL binding, and when it throws", async () => {
    fetchMock = standardFetch();
    assert.equal((await post(VALID, { env: makeEnv({ RL: undefined }) })).response.status, 202);
    const failing = { limit: async () => { throw new Error("binding down"); } };
    assert.equal((await post(VALID, { env: makeEnv({ RL: failing }) })).response.status, 202);
  });

  it("caps submissions per IP per Sao Paulo day", async () => {
    fetchMock = standardFetch();
    const settings = structuredClone((await import("../settings.js")).SETTINGS);
    settings.limits.per_ip_day_max = 2;
    const { app } = makeApp({ settings });
    const env = makeEnv({ RL: undefined });
    for (let i = 0; i < 2; i += 1) {
      assert.equal((await post({ ...VALID, comment: `comentário número ${i} do mesmo endereço` }, { env, app })).response.status, 202);
    }
    const blocked = await post({ ...VALID, comment: "comentário número 3 do mesmo endereço" }, { env, app });
    assert.equal(blocked.response.status, 429);
    assert.equal(blocked.data.scope, "ip_day");
    // 15:00 UTC is 12:00 in Sao Paulo: 12 hours until the next day.
    assert.equal(blocked.data.retry_after, 12 * 3600);
    assert.equal(blocked.response.headers.get("Retry-After"), String(12 * 3600));
    const other = await post({ ...VALID, comment: "comentário de outro endereço" }, { env, app, headers: { "CF-Connecting-IP": "198.51.100.1" } });
    assert.equal(other.response.status, 202);
  });

  it("counts a duplicate against the IP, but not a validation error", async () => {
    fetchMock = standardFetch();
    const settings = structuredClone((await import("../settings.js")).SETTINGS);
    settings.limits.per_ip_day_max = 2;
    const { app } = makeApp({ settings });
    const env = makeEnv({ RL: undefined });
    await post({ ...VALID, score: 99 }, { env, app });
    await post({ ...VALID, comment: "o mesmo comentário repetido aqui" }, { env, app });
    assert.equal((await post({ ...VALID, comment: "o mesmo comentário repetido aqui" }, { env, app })).response.status, 409);
    assert.equal((await post({ ...VALID, comment: "mais um comentário diferente" }, { env, app })).data.scope, "ip_day");
  });

  it("caps submissions per hour for everyone, freeing a slot an hour after it was taken", async () => {
    fetchMock = standardFetch();
    const settings = structuredClone((await import("../settings.js")).SETTINGS);
    settings.limits.global_hour_max = 3;
    const { app, now } = makeApp({ settings });
    const env = makeEnv({ RL: undefined });
    for (let i = 0; i < 3; i += 1) {
      const response = (await post({ ...VALID, comment: `comentário global número ${i}` }, { env, app, headers: { "CF-Connecting-IP": `198.51.100.${i}` } })).response;
      assert.equal(response.status, 202);
      now.advance(60_000);
    }
    const blocked = await post({ ...VALID, comment: "comentário global número 4" }, { env, app, headers: { "CF-Connecting-IP": "198.51.100.50" } });
    assert.equal(blocked.response.status, 429);
    assert.equal(blocked.data.scope, "global_hour");
    assert.equal(blocked.data.retry_after, 3600 - 180);
    now.advance((3600 - 180) * 1000 + 1);
    const later = await post({ ...VALID, comment: "comentário global número 5" }, { env, app, headers: { "CF-Connecting-IP": "198.51.100.51" } });
    assert.equal(later.response.status, 202);
  });

  it("answers 503 quota while today's Workers AI allocation is used up, but takes answers without a comment", async () => {
    fetchMock = standardFetch();
    const env = makeEnv({ RL: undefined });
    const { app } = makeApp();
    await post({ ...VALID, comment: "" }, { env, app }); // creates the schema
    env.DB.sqlite.prepare("INSERT INTO meta (key, value) VALUES ('quota_exhausted_day', '2026-11-11')").run();
    const refused = await post(VALID, { env, app });
    assert.equal(refused.response.status, 503);
    assert.deepEqual(refused.data, { error: "quota" });
    assert.equal(refused.response.headers.get("Retry-After"), String(9 * 3600));
    assert.equal((await post({ ...VALID, comment: "" }, { env, app })).response.status, 202);
    assert.equal(env.DB.row("SELECT value FROM daily WHERE key = 'rejected_quota'").value, 1);
    env.DB.sqlite.prepare("UPDATE meta SET value = '2026-11-10' WHERE key = 'quota_exhausted_day'").run();
    assert.equal((await post(VALID, { env, app })).response.status, 202);
  });
});

describe("classification after the answer (waitUntil)", () => {
  it("stores a valid answer as done with tokens, times and versions", async () => {
    fetchMock = standardFetch();
    const env = makeEnv({ AI: new FakeAI({ classify: [stage1({ t: ["rem", "inf"], s: "neg" })] }) });
    const { app } = makeApp({ now: (() => { let t = NOW; return () => (t += 250); })() });
    const { ctx } = await post(VALID, { env, app });
    const outcomes = await ctx.settle();
    assert.deepEqual(outcomes, ["done"]);
    const row = env.DB.row("SELECT * FROM submissions");
    assert.equal(row.status, "done");
    assert.equal(row.themes, '["rem","inf"]');
    assert.equal(row.sentiment, "neg");
    assert.equal(row.critical, 0);
    assert.equal(row.critical_types, "[]");
    assert.equal(row.model, "@cf/google/gemma-4-26b-a4b-it");
    assert.match(row.prompt_version, /^classify\.v3\+[0-9a-f]{8}$/);
    assert.equal(row.verifier_version, null);
    assert.equal(row.prompt_tokens, 2100);
    assert.equal(row.output_tokens, 40);
    const stages = JSON.parse(row.stages);
    assert.ok(row.ai_ms > 0);
    assert.equal(row.ai_ms, stages.reduce((sum, s) => sum + s.total_ms, 0));
    assert.deepEqual(stages.map((s) => s.name), ["lexicon", "classify"]);
    assert.ok(row.finished_at > row.started_at);
    const daily = Object.fromEntries(env.DB.rows("SELECT key, value FROM daily").map((r) => [r.key, r.value]));
    assert.equal(daily.classified, 1);
    assert.equal(daily.critical, undefined);
    const [call] = env.AI.calls;
    assert.equal(call.model, "@cf/google/gemma-4-26b-a4b-it");
    assert.ok(call.input.messages[1].content.includes("Falta gente na equipe"));
    assert.deepEqual(call.input.chat_template_kwargs, { enable_thinking: false });
  });

  it("retries an invalid answer once, then stores the valid one", async () => {
    fetchMock = standardFetch();
    const env = makeEnv({ AI: new FakeAI({ classify: ["{not json", stage1({ t: ["inf"] })] }) });
    const { ctx } = await post(VALID, { env });
    assert.deepEqual(await ctx.settle(), ["done"]);
    const stages = JSON.parse(env.DB.row("SELECT stages FROM submissions").stages);
    assert.deepEqual(stages.map((s) => [s.name, s.ok]), [["lexicon", true], ["classify", false], ["classify_retry", true]]);
  });

  it("marks the row review after two invalid answers", async () => {
    fetchMock = standardFetch();
    const env = makeEnv({ AI: new FakeAI({ classify: [new Error("JSON Mode couldn't be met"), "{}"] }) });
    const { ctx } = await post(VALID, { env });
    assert.deepEqual(await ctx.settle(), ["review"]);
    const row = env.DB.row("SELECT status, themes, sentiment FROM submissions");
    assert.deepEqual(row, { status: "review", themes: "[]", sentiment: null });
  });

  it("parks the row on quota error 3036 and flags the day", async () => {
    fetchMock = standardFetch();
    const env = makeEnv({ AI: new FakeAI({ classify: [quotaError()] }) });
    const { ctx } = await post(VALID, { env });
    assert.deepEqual(await ctx.settle(), ["queued_quota"]);
    assert.deepEqual(env.DB.row("SELECT status, attempts, started_at FROM submissions"), { status: "queued_quota", attempts: 0, started_at: null });
    assert.equal(env.DB.row("SELECT value FROM meta WHERE key = 'quota_exhausted_day'").value, "2026-11-11");
    assert.equal(env.DB.row("SELECT value FROM daily WHERE key = 'quota_hits'").value, 1);
  });

  it("leaves the row for the cron after another error, and fails it on the last attempt", async () => {
    fetchMock = standardFetch();
    const env = makeEnv({ AI: new FakeAI({ classify: [Object.assign(new Error("5028: model deprecated"), { code: 5028 })] }) });
    const { ctx } = await post(VALID, { env });
    assert.deepEqual(await ctx.settle(), ["retry"]);
    assert.equal(env.DB.row("SELECT status FROM submissions").status, "processing");
  });
});

describe("classification after the answer: storage failure", () => {
  it("never rejects and logs only the error name", async () => {
    fetchMock = standardFetch();
    const env = makeEnv({ AI: new FakeAI({}, { hold: true }) });
    const { ctx } = await post(VALID, { env });
    env.DB.failWith = new Error(`D1_ERROR near "${VALID.comment}"`);
    const spy = spyConsole();
    env.AI.release();
    const outcomes = await ctx.settle();
    spy.restore();
    assert.deepEqual(outcomes, ["error"]);
    assert.deepEqual(spy.lines, ["process_error name=Error"]);
  });
});

describe("GET /api/v1/public/submissions/{code}", () => {
  async function submitted(env, app, body = VALID) {
    const { data, ctx } = await post(body, { env, app });
    return { code: data.code, ctx };
  }

  it("follows a submission from processing to done, in the FastAPI shape", async () => {
    fetchMock = standardFetch();
    const env = makeEnv({ AI: new FakeAI({ classify: [stage1({ t: ["lid"], s: "neg", e: "Falta gente" })] }, { hold: true }) });
    const { app, now } = makeApp();
    const { code, ctx } = await submitted(env, app);
    const waiting = await (await app.fetch(apiRequest(`/api/v1/public/submissions/${code}`), env, makeCtx())).json();
    assert.deepEqual(waiting, {
      code, status: "processing", comment_anon: VALID.comment, pii_counts: {}, queue_position: 1, eta_seconds: 15,
    });
    now.advance(4000);
    env.AI.release();
    await ctx.settle();
    const lower = code.replace("-", "").toLowerCase();
    const response = await app.fetch(apiRequest(`/api/v1/public/submissions/${lower}`), env, makeCtx());
    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), {
      code, status: "done", comment_anon: VALID.comment, pii_counts: {}, themes: ["lid"], sentiment: "neg",
      critical: false, critical_types: [], evidence: null, model: "@cf/google/gemma-4-26b-a4b-it", processing_seconds: 4,
    });
  });

  it("answers skipped and failed rows without queue or result fields", async () => {
    fetchMock = standardFetch();
    const env = makeEnv();
    const { app } = makeApp();
    const { code } = await submitted(env, app, { ...VALID, comment: "" });
    const skipped = await (await app.fetch(apiRequest(`/api/v1/public/submissions/${code}`), env, makeCtx())).json();
    assert.deepEqual(skipped, { code, status: "skipped", comment_anon: "", pii_counts: {} });
    env.DB.sqlite.prepare("UPDATE submissions SET status = 'failed'").run();
    const failed = await (await app.fetch(apiRequest(`/api/v1/public/submissions/${code}`), env, makeCtx())).json();
    assert.equal(failed.status, "failed");
    assert.equal(failed.themes, undefined);
  });

  it("gives a row waiting for quota its place and the wait until the reset", async () => {
    fetchMock = standardFetch();
    const env = makeEnv({ AI: new FakeAI({ classify: [quotaError(), quotaError()] }) });
    const { app } = makeApp();
    const first = await submitted(env, app, { ...VALID, comment: "primeiro comentário que fica esperando" });
    await first.ctx.settle();
    const second = await submitted(env, app, { ...VALID, comment: "" });
    assert.ok(second.code);
    env.DB.sqlite.prepare("UPDATE meta SET value = '2026-11-11' WHERE key = 'quota_exhausted_day'").run();
    const body = await (await app.fetch(apiRequest(`/api/v1/public/submissions/${first.code}`), env, makeCtx())).json();
    assert.equal(body.status, "queued_quota");
    assert.equal(body.queue_position, 1);
    assert.equal(body.eta_seconds, 9 * 3600 + 15); // 00:00 UTC, then one job
  });

  it("answers 404 to unknown and malformed codes", async () => {
    fetchMock = standardFetch();
    const env = makeEnv();
    const { app } = makeApp();
    for (const code of ["ABCD-EFGH", "not-a-code", "AB2C-DE3", "AB2C-DE3FG", "%E0%A4%A", "AB2C%20DE3F"]) {
      const response = await app.fetch(apiRequest(`/api/v1/public/submissions/${code}`), env, makeCtx());
      assert.equal(response.status, 404, code);
      assert.deepEqual(await response.json(), { error: "not_found" });
    }
  });
});
