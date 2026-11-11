import assert from "node:assert/strict";
import { afterEach, describe, it } from "node:test";
import worker from "../index.js";
import { refreshSnapshots, stableView } from "../snapshots.js";
import {
  ORIGIN, SECRET, apiRequest, jsonResponse, makeCtx, makeEnv, mockFetch, timeoutError,
} from "./helpers.js";

let fetchMock;

afterEach(() => fetchMock?.restore());

const DASHBOARD = '{"dataset":"public","generated_at":"2026-11-10T15:00:00-03:00","totals":{"responses":12}}';
const STATUS = '{"generated_at":"2026-11-10T18:00:00Z","queue":{"queued":1},"worker":{"heartbeat_age_seconds":3.2}}';

describe("dashboard and status: live answers", () => {
  it("passes the body through as text, marked live and not cacheable", async () => {
    fetchMock = mockFetch(() => new Response(DASHBOARD, { headers: { "Content-Type": "application/json" } }));
    const response = await worker.fetch(apiRequest("/api/v1/public/dashboard?dataset=public"), makeEnv());
    assert.equal(response.status, 200);
    assert.equal(await response.text(), DASHBOARD);
    assert.equal(response.headers.get("Cache-Control"), "no-store");
    assert.equal(response.headers.get("X-Data-Source"), "live");
    assert.equal(response.headers.get("X-Snapshot-At"), null);
  });

  it("keeps a 4xx from the origin instead of falling back", async () => {
    fetchMock = mockFetch(() => jsonResponse(422, { error: "validation" }));
    const env = makeEnv();
    env.SNAPSHOTS.seed("status", STATUS);
    const response = await worker.fetch(apiRequest("/api/v1/public/status"), env);
    assert.equal(response.status, 422);
  });
});

describe("dashboard and status: snapshot fallback", () => {
  const failures = {
    timeout: () => { throw timeoutError(); },
    "network error": () => { throw new TypeError("fetch failed"); },
    500: () => jsonResponse(500, { error: "internal" }),
    502: () => new Response("<html>bad gateway</html>", { status: 502 }),
    530: () => new Response("<html>error 1033</html>", { status: 530 }),
  };
  for (const [name, failure] of Object.entries(failures)) {
    it(`answers from KV on ${name}`, async () => {
      fetchMock = mockFetch(failure);
      const env = makeEnv();
      env.SNAPSHOTS.seed("dashboard:synthetic", DASHBOARD, "2026-11-10T17:30:00.000Z");
      const response = await worker.fetch(apiRequest("/api/v1/public/dashboard?dataset=synthetic"), env);
      assert.equal(response.status, 200);
      assert.equal(await response.text(), DASHBOARD);
      assert.equal(response.headers.get("X-Data-Source"), "snapshot");
      assert.equal(response.headers.get("X-Snapshot-At"), "2026-11-10T17:30:00.000Z");
      assert.equal(response.headers.get("Cache-Control"), "no-store");
    });
  }

  it("uses the snapshot of the requested dataset only", async () => {
    fetchMock = mockFetch(failures.timeout);
    const env = makeEnv();
    env.SNAPSHOTS.seed("dashboard:synthetic", DASHBOARD);
    const response = await worker.fetch(apiRequest("/api/v1/public/dashboard?dataset=public"), env);
    assert.equal(response.status, 503);
    assert.deepEqual(await response.json(), { error: "offline" });
  });

  it("falls back for status too", async () => {
    fetchMock = mockFetch(failures[530]);
    const env = makeEnv();
    env.SNAPSHOTS.seed("status", STATUS, "2026-11-10T18:00:05.000Z");
    const response = await worker.fetch(apiRequest("/api/v1/public/status"), env);
    assert.equal(await response.text(), STATUS);
    assert.equal(response.headers.get("X-Snapshot-At"), "2026-11-10T18:00:05.000Z");
  });

  it("answers 503 offline when there is no snapshot or no KV binding", async () => {
    fetchMock = mockFetch(failures["network error"]);
    for (const env of [makeEnv(), makeEnv({ SNAPSHOTS: undefined })]) {
      const response = await worker.fetch(apiRequest("/api/v1/public/status"), env);
      assert.equal(response.status, 503);
      assert.deepEqual(await response.json(), { error: "offline" });
    }
  });

  it("passes a timeout signal to the origin request", async () => {
    fetchMock = mockFetch(() => new Response(STATUS));
    await worker.fetch(apiRequest("/api/v1/public/status"), makeEnv());
    assert.ok(fetchMock.calls[0].signal instanceof AbortSignal);
  });
});

describe("scheduled refresh", () => {
  function origin(bodies) {
    fetchMock = mockFetch((call) => {
      const key = call.url.pathname.endsWith("/status") ? "status" : `dashboard:${call.url.searchParams.get("dataset")}`;
      const body = bodies[key];
      if (body instanceof Error) throw body;
      return new Response(body, { headers: { "Content-Type": "application/json" } });
    });
    return fetchMock;
  }

  const bodies = {
    "dashboard:public": DASHBOARD,
    "dashboard:synthetic": DASHBOARD.replace("public", "synthetic"),
    status: STATUS,
  };

  async function runCron(env) {
    const ctx = makeCtx();
    await worker.scheduled({ cron: "*/10 * * * *", scheduledTime: Date.now() }, env, ctx);
    await Promise.all(ctx.pending);
  }

  it("fetches the three sources with the shared secret and stores them", async () => {
    const { calls } = origin(bodies);
    const env = makeEnv();
    await runCron(env);
    assert.deepEqual(calls.map((c) => c.url.href).sort(), [
      `${ORIGIN}/v1/public/dashboard?dataset=public`,
      `${ORIGIN}/v1/public/dashboard?dataset=synthetic`,
      `${ORIGIN}/v1/public/status`,
    ]);
    assert.ok(calls.every((c) => c.headers.get("X-Origin-Secret") === SECRET));
    assert.deepEqual(env.SNAPSHOTS.writes.sort(), ["dashboard:public", "dashboard:synthetic", "status"]);
    const record = await env.SNAPSHOTS.get("dashboard:public", "json");
    assert.equal(record.body, DASHBOARD);
    assert.match(record.sha256, /^[0-9a-f]{64}$/);
    assert.ok(!Number.isNaN(Date.parse(record.fetched_at)));
  });

  it("writes only when the content changed", async () => {
    const env = makeEnv();
    origin(bodies);
    await runCron(env);
    fetchMock.restore();
    assert.equal(env.SNAPSHOTS.writes.length, 3);

    // Same data, new timestamps: nothing to write.
    origin({
      ...bodies,
      "dashboard:public": DASHBOARD.replace("15:00:00", "15:10:00"),
      status: STATUS.replace("3.2", "7.9").replace("18:00:00", "18:10:00"),
    });
    await runCron(env);
    fetchMock.restore();
    assert.equal(env.SNAPSHOTS.writes.length, 3);

    // Real change in one source: one write.
    origin({ ...bodies, "dashboard:public": DASHBOARD.replace("12", "13") });
    await runCron(env);
    assert.deepEqual(env.SNAPSHOTS.writes.slice(3), ["dashboard:public"]);
    const record = await env.SNAPSHOTS.get("dashboard:public", "json");
    assert.match(record.body, /"responses":13/);
  });

  it("keeps the last copy when the origin fails", async () => {
    const env = makeEnv();
    env.SNAPSHOTS.seed("status", STATUS);
    origin({ ...bodies, status: new TypeError("down"), "dashboard:public": new TypeError("down") });
    const outcomes = await refreshSnapshots(env);
    assert.deepEqual(env.SNAPSHOTS.writes, ["dashboard:synthetic"]);
    assert.equal(outcomes.find((o) => o.key === "status").result, "unreachable");
    assert.equal(JSON.parse(env.SNAPSHOTS.store.get("status").value).body, STATUS);
  });

  it("does not store error answers", async () => {
    fetchMock = mockFetch(() => new Response("<html>error</html>", { status: 530 }));
    const env = makeEnv();
    await refreshSnapshots(env);
    assert.equal(env.SNAPSHOTS.writes.length, 0);
  });

  it("does nothing without the KV binding", async () => {
    const { calls } = origin(bodies);
    assert.deepEqual(await refreshSnapshots(makeEnv({ SNAPSHOTS: undefined })), []);
    assert.equal(calls.length, 0);
  });

  it("blanks only the volatile fields before hashing", () => {
    assert.equal(stableView('{"generated_at":"x","now":null,"n":1}'), '{"generated_at":null,"now":null,"n":1}');
    assert.equal(stableView('{"worker":{"heartbeat_age_seconds":12.5}}'), '{"worker":{"heartbeat_age_seconds":null}}');
    assert.equal(stableView('{"label":"generated_at"}'), '{"label":"generated_at"}');
  });
});
