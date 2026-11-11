import assert from "node:assert/strict";
import { afterEach, describe, it } from "node:test";
import worker from "../index.js";
import {
  ORIGIN, SECRET, SITEVERIFY, TURNSTILE_SECRET, VALID, FakeRateLimiter,
  apiRequest, jsonResponse, makeEnv, mockFetch, originCalls, postSubmission,
} from "./helpers.js";

let fetchMock;

afterEach(() => fetchMock?.restore());

const ACCEPTED = { code: "AB2C-DE3F", status: "queued", queue_position: 1, eta_seconds: 8 };

/** siteverify answers `success`; the origin answers `origin` (a Response or a function). */
function scripted({ success = true, siteverify, origin = () => jsonResponse(202, ACCEPTED) } = {}) {
  fetchMock = mockFetch((call) => {
    if (call.url.href === SITEVERIFY) {
      return siteverify ? siteverify(call) : jsonResponse(200, { success, "error-codes": success ? [] : ["invalid-input-response"] });
    }
    if (call.url.origin === ORIGIN) return typeof origin === "function" ? origin(call) : origin;
    throw new Error(`unexpected fetch ${call.url.href}`);
  });
  return fetchMock;
}

describe("POST /api/v1/public/submissions", () => {
  it("verifies Turnstile, then forwards the answer without the token", async () => {
    const { calls } = scripted();
    const response = await worker.fetch(postSubmission(VALID), makeEnv());
    assert.equal(response.status, 202);
    assert.deepEqual(await response.json(), ACCEPTED);
    assert.equal(response.headers.get("Cache-Control"), "no-store");

    const [verify, forward] = calls;
    assert.equal(verify.url.href, SITEVERIFY);
    const sent = JSON.parse(verify.body);
    assert.equal(sent.secret, TURNSTILE_SECRET);
    assert.equal(sent.response, "token-ok");
    assert.equal(sent.remoteip, "203.0.113.7");
    assert.match(sent.idempotency_key, /^[0-9a-f-]{36}$/);

    assert.equal(forward.url.href, `${ORIGIN}/v1/public/submissions`);
    assert.equal(forward.method, "POST");
    assert.equal(forward.headers.get("X-Origin-Secret"), SECRET);
    assert.equal(forward.headers.get("X-Client-IP"), "203.0.113.7");
    assert.match(forward.headers.get("Content-Type"), /^application\/json/);
    const { turnstile, ...expected } = VALID;
    assert.equal(turnstile, "token-ok");
    assert.deepEqual(JSON.parse(forward.body), expected);
  });

  it("refuses a missing or empty token without calling siteverify", async () => {
    for (const token of [undefined, "", 42, "x".repeat(2049)]) {
      const { calls } = scripted();
      const response = await worker.fetch(postSubmission({ ...VALID, turnstile: token }), makeEnv());
      assert.equal(response.status, 403);
      assert.deepEqual(await response.json(), { error: "turnstile" });
      assert.equal(calls.length, 0);
      fetchMock.restore();
    }
  });

  it("refuses a token that siteverify rejects and never reaches the origin", async () => {
    const { calls } = scripted({ success: false });
    const response = await worker.fetch(postSubmission(VALID), makeEnv());
    assert.equal(response.status, 403);
    assert.deepEqual(await response.json(), { error: "turnstile" });
    assert.equal(originCalls(calls).length, 0);
  });

  it("retries siteverify once with the same idempotency key, then answers 503", async () => {
    const { calls } = scripted({ siteverify: () => { throw new TypeError("network"); } });
    const response = await worker.fetch(postSubmission(VALID), makeEnv());
    assert.equal(response.status, 503);
    assert.equal((await response.json()).error, "turnstile_unavailable");
    assert.equal(calls.length, 2);
    const keys = calls.map((call) => JSON.parse(call.body).idempotency_key);
    assert.equal(keys[0], keys[1]);
  });

  it("fails closed when the Turnstile secret is missing", async () => {
    const { calls } = scripted();
    const response = await worker.fetch(postSubmission(VALID), makeEnv({ TURNSTILE_SECRET: "" }));
    assert.equal(response.status, 503);
    assert.equal(calls.length, 0);
  });

  it("applies the rate limit per IP after Turnstile", async () => {
    const { calls } = scripted();
    const env = makeEnv({ RL: new FakeRateLimiter(2) });
    const statuses = [];
    for (let i = 0; i < 3; i += 1) {
      statuses.push((await worker.fetch(postSubmission(VALID), env)).status);
    }
    assert.deepEqual(statuses, [202, 202, 429]);
    assert.deepEqual(env.RL.calls, ["203.0.113.7", "203.0.113.7", "203.0.113.7"]);
    assert.equal(originCalls(calls).length, 2);

    const other = await worker.fetch(postSubmission(VALID, { "CF-Connecting-IP": "203.0.113.8" }), env);
    assert.equal(other.status, 202);
  });

  it("sends Retry-After with a 429 from the rate limit", async () => {
    scripted();
    const env = makeEnv({ RL: new FakeRateLimiter(0) });
    const response = await worker.fetch(postSubmission(VALID), env);
    assert.equal(response.status, 429);
    assert.equal(response.headers.get("Retry-After"), "60");
    assert.equal((await response.json()).error, "rate_limited");
  });

  it("works without the rate limit binding, and when it throws", async () => {
    scripted();
    assert.equal((await worker.fetch(postSubmission(VALID), makeEnv({ RL: undefined }))).status, 202);
    const broken = { limit: async () => { throw new Error("binding down"); } };
    assert.equal((await worker.fetch(postSubmission(VALID), makeEnv({ RL: broken }))).status, 202);
  });

  it("refuses bodies over 8 KB, declared or streamed, before Turnstile", async () => {
    const { calls } = scripted();
    const big = { ...VALID, comment: "a".repeat(9000) };
    const declared = await worker.fetch(postSubmission(big), makeEnv());
    assert.equal(declared.status, 413);

    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(JSON.stringify(big)));
        controller.close();
      },
    });
    const streamed = await worker.fetch(apiRequest("/api/v1/public/submissions", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: stream, duplex: "half",
    }), makeEnv());
    assert.equal(streamed.status, 413);
    assert.equal(calls.length, 0);
  });

  it("answers 422 to a body that is not a JSON object", async () => {
    scripted();
    for (const body of ["not json", "[1,2]", "null", "\"text\""]) {
      const response = await worker.fetch(postSubmission(body), makeEnv());
      assert.equal(response.status, 422, body);
      assert.equal((await response.json()).error, "validation");
    }
  });

  it("accepts JSON only and answers no CORS preflight", async () => {
    const { calls } = scripted();
    for (const type of ["application/x-www-form-urlencoded", "text/plain", ""]) {
      const response = await worker.fetch(postSubmission("score=1", { "Content-Type": type }), makeEnv());
      assert.equal(response.status, 415, type);
    }
    const preflight = await worker.fetch(apiRequest("/api/v1/public/submissions", {
      method: "OPTIONS", headers: { Origin: "https://evil.example", "Access-Control-Request-Method": "POST" },
    }), makeEnv());
    assert.equal(preflight.status, 404);
    assert.equal(preflight.headers.get("Access-Control-Allow-Origin"), null);
    assert.equal(calls.length, 0);
  });

  it("passes the API's own errors through with Retry-After", async () => {
    const answers = [
      [409, { error: "duplicate" }, {}],
      [422, { error: "validation", fields: { area: "unknown value" } }, {}],
      [429, { error: "rate_limited", retry_after: 240 }, { "Retry-After": "240" }],
      [503, { error: "queue_full" }, { "Retry-After": "60" }],
    ];
    for (const [status, body, headers] of answers) {
      scripted({ origin: jsonResponse(status, body, headers) });
      const response = await worker.fetch(postSubmission(VALID), makeEnv());
      assert.equal(response.status, status);
      assert.deepEqual(await response.json(), body);
      assert.equal(response.headers.get("Retry-After"), headers["Retry-After"] ?? null);
      fetchMock.restore();
    }
  });

  it("answers 503 offline when the origin is down", async () => {
    for (const origin of [
      () => { throw new TypeError("network"); },
      () => new Response("Bad gateway", { status: 502, headers: { "Content-Type": "text/html" } }),
      () => new Response("", { status: 530 }),
    ]) {
      scripted({ origin });
      const response = await worker.fetch(postSubmission(VALID), makeEnv());
      assert.equal(response.status, 503);
      assert.deepEqual(await response.json(), { error: "offline" });
      fetchMock.restore();
    }
  });

  it("never logs the request body", async () => {
    scripted();
    const logged = [];
    const original = { log: console.log, error: console.error, warn: console.warn, info: console.info };
    for (const name of Object.keys(original)) console[name] = (...args) => logged.push(args.join(" "));
    try {
      await worker.fetch(postSubmission({ ...VALID, comment: "segredo do formulário" }), makeEnv());
    } finally {
      Object.assign(console, original);
    }
    assert.ok(!logged.some((line) => line.includes("segredo")));
  });
});
