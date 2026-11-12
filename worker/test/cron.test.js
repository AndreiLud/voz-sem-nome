import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { CRON_PURGE, CRON_RETRY } from "../app.js";
import { RETRY_TIME_BUDGET_MS } from "../processing.js";
import { ensureSchema } from "../store.js";
import { FakeAI, NOW, chatAnswer, makeApp, makeCtx, makeEnv, quotaError, seedSubmissions, stage1 } from "./helpers.js";

const iso = (ms) => new Date(ms).toISOString().replace("Z", "+00:00");
const MINUTE = 60_000;
const COMMENT = "O sistema de escala trava toda vez que tento trocar folga.";

async function runCron(app, env, cron) {
  const ctx = makeCtx();
  await app.scheduled({ cron, scheduledTime: NOW }, env, ctx);
  await ctx.settle();
}

const statusOf = (env, id) => env.DB.row("SELECT status, attempts FROM submissions WHERE id = ?", id);

describe("cron: retry", () => {
  it("retries rows stuck in processing for more than two minutes, at most twice", async () => {
    const env = makeEnv({ AI: new FakeAI({ classify: [stage1({ t: ["inf"] })] }) });
    const { app } = makeApp();
    const [stuck, recent, lastTry] = await seedSubmissions(env.DB, [
      { status: "processing", attempts: 1, comment_anon: COMMENT, started_at: iso(NOW - 3 * MINUTE), created_at: iso(NOW - 3 * MINUTE) },
      { status: "processing", attempts: 1, comment_anon: COMMENT, started_at: iso(NOW - MINUTE), created_at: iso(NOW - MINUTE) },
      { status: "processing", attempts: 2, comment_anon: COMMENT, started_at: iso(NOW - 5 * MINUTE), created_at: iso(NOW - 9 * MINUTE) },
    ]);
    await runCron(app, env, CRON_RETRY);
    assert.deepEqual(statusOf(env, stuck), { status: "done", attempts: 2 });
    assert.deepEqual(statusOf(env, recent), { status: "processing", attempts: 1 });
    assert.deepEqual(statusOf(env, lastTry), { status: "failed", attempts: 2 });
    assert.equal(env.DB.row("SELECT finished_at FROM submissions WHERE id = ?", lastTry).finished_at, iso(NOW));
    assert.equal(env.AI.calls.length, 1);
    const daily = Object.fromEntries(env.DB.rows("SELECT key, value FROM daily").map((r) => [r.key, r.value]));
    assert.deepEqual(daily, { classified: 1, failed: 1 });
  });

  it("fails a row whose second attempt errors", async () => {
    const env = makeEnv({ AI: new FakeAI({ classify: [Object.assign(new Error("capacity"), { code: 3040 })] }) });
    const { app } = makeApp();
    const [id] = await seedSubmissions(env.DB, [
      { status: "processing", attempts: 1, comment_anon: COMMENT, started_at: iso(NOW - 3 * MINUTE) },
    ]);
    await runCron(app, env, CRON_RETRY);
    assert.deepEqual(statusOf(env, id), { status: "failed", attempts: 2 });
  });

  it("retries rows waiting for quota only after the 00:00 UTC reset", async () => {
    const env = makeEnv({ AI: new FakeAI({ classify: [stage1(), stage1()] }) });
    const { app, now } = makeApp();
    await ensureSchema(env.DB);
    env.DB.sqlite.exec("INSERT INTO meta (key, value) VALUES ('quota_exhausted_day', '2026-11-11')");
    const ids = await seedSubmissions(env.DB, [
      { status: "queued_quota", attempts: 0, comment_anon: COMMENT },
      { status: "queued_quota", attempts: 0, comment_anon: `${COMMENT} De novo.` },
    ]);
    await runCron(app, env, CRON_RETRY);
    assert.deepEqual(ids.map((id) => statusOf(env, id).status), ["queued_quota", "queued_quota"]);
    assert.equal(env.AI.calls.length, 0);
    now.set(Date.parse("2026-11-12T00:00:30Z"));
    await runCron(app, env, CRON_RETRY);
    assert.deepEqual(ids.map((id) => statusOf(env, id)), [{ status: "done", attempts: 1 }, { status: "done", attempts: 1 }]);
  });

  it("stops at the first quota error and keeps the rest waiting", async () => {
    const env = makeEnv({ AI: new FakeAI({ classify: [quotaError()] }) });
    const { app } = makeApp();
    const ids = await seedSubmissions(env.DB, [
      { status: "queued_quota", attempts: 0, comment_anon: COMMENT },
      { status: "queued_quota", attempts: 0, comment_anon: `${COMMENT} De novo.` },
    ]);
    await runCron(app, env, CRON_RETRY);
    assert.deepEqual(ids.map((id) => statusOf(env, id)), [{ status: "queued_quota", attempts: 0 }, { status: "queued_quota", attempts: 0 }]);
    assert.equal(env.AI.calls.length, 1);
    assert.equal(env.DB.row("SELECT value FROM meta WHERE key = 'quota_exhausted_day'").value, "2026-11-11");
  });

  it("handles at most five rows per run", async () => {
    const env = makeEnv();
    const { app } = makeApp();
    await seedSubmissions(env.DB, Array.from({ length: 7 }, (_, i) => ({
      status: "processing", attempts: 1, comment_anon: `${COMMENT} ${i}`, started_at: iso(NOW - 10 * MINUTE),
    })));
    await runCron(app, env, CRON_RETRY);
    assert.equal(env.DB.row("SELECT count(*) AS n FROM submissions WHERE status = 'done'").n, 5);
  });

  it("claims no new row once the run has used its time budget", async () => {
    const { app, now } = makeApp();
    const slow = () => { now.advance(RETRY_TIME_BUDGET_MS); return chatAnswer(stage1()); };
    const env = makeEnv({ AI: new FakeAI({ classify: [slow, slow, slow] }) });
    const ids = await seedSubmissions(env.DB, [0, 1, 2].map((i) => ({
      status: "processing", attempts: 1, comment_anon: `${COMMENT} ${i}`, started_at: iso(NOW - 10 * MINUTE),
    })));
    await runCron(app, env, CRON_RETRY);
    assert.deepEqual(ids.map((id) => statusOf(env, id)), [
      { status: "done", attempts: 2 }, { status: "processing", attempts: 1 }, { status: "processing", attempts: 1 },
    ]);
    assert.equal(env.AI.calls.length, 1);
  });
});

describe("cron: daily purge", () => {
  it("deletes old submissions, alerts, IP hashes and salts, and nothing else", async () => {
    const env = makeEnv();
    const { app } = makeApp();
    const DAY = 86_400_000;
    await seedSubmissions(env.DB, [
      { created_at: iso(NOW - 31 * DAY), public_id: "old" },
      { created_at: iso(NOW - 29 * DAY), public_id: "kept" },
    ]);
    env.DB.sqlite.exec(`
      INSERT INTO alerts (public_id, critical_types, submitted_at, detected_at, delivered_at, mode) VALUES
        ('a', '[]', '${iso(NOW - 91 * DAY)}', '${iso(NOW - 91 * DAY)}', '${iso(NOW - 91 * DAY)}', 'immediate'),
        ('b', '[]', '${iso(NOW - 89 * DAY)}', '${iso(NOW - 89 * DAY)}', '${iso(NOW - 89 * DAY)}', 'immediate');
      INSERT INTO submitters_today (day, ip_hash, submissions) VALUES ('2026-11-10', 'h1', 1), ('2026-11-11', 'h2', 1);
      INSERT INTO meta (key, value) VALUES ('ip_salt:2026-11-10', 's1'), ('ip_salt:2026-11-11', 's2'),
        ('quota_exhausted_day', '2026-11-10');
      INSERT INTO daily (day, key, value) VALUES ('2026-09-01', 'submissions', 3);
    `);
    await runCron(app, env, CRON_PURGE);
    assert.deepEqual(env.DB.rows("SELECT public_id FROM submissions").map((r) => r.public_id), ["kept"]);
    assert.deepEqual(env.DB.rows("SELECT public_id FROM alerts").map((r) => r.public_id), ["b"]);
    assert.deepEqual(env.DB.rows("SELECT day FROM submitters_today").map((r) => r.day), ["2026-11-11"]);
    assert.deepEqual(env.DB.rows("SELECT key FROM meta ORDER BY key").map((r) => r.key), ["ip_salt:2026-11-11", "quota_exhausted_day"]);
    assert.equal(env.DB.rows("SELECT * FROM daily").length, 1);
  });

  it("is chosen by the cron string; the retry cron purges nothing", async () => {
    const env = makeEnv();
    const { app } = makeApp();
    await seedSubmissions(env.DB, [{ created_at: iso(NOW - 40 * 86_400_000) }]);
    await runCron(app, env, CRON_RETRY);
    assert.equal(env.DB.rows("SELECT id FROM submissions").length, 1);
    await runCron(app, env, CRON_PURGE);
    assert.equal(env.DB.rows("SELECT id FROM submissions").length, 0);
  });

  it("logs a failing job by name only", async () => {
    const env = makeEnv();
    env.DB.failWith = new Error("D1_ERROR: details");
    const { app } = makeApp();
    const lines = [];
    const real = console.warn;
    console.warn = (line) => lines.push(line);
    try {
      await runCron(app, env, CRON_PURGE);
    } finally {
      console.warn = real;
    }
    assert.deepEqual(lines, ["cron_error cron=purge name=Error"]);
  });
});
