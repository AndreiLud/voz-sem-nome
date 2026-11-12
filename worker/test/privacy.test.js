import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync, readdirSync } from "node:fs";
import { afterEach, describe, it } from "node:test";
import { CRON_PURGE, CRON_RETRY } from "../app.js";
import { formatTrackingCode, newTrackingCode, normalizeTrackingCode, randomString, trackingHash } from "../ids.js";
import { logEvent } from "../log.js";
import { DASHBOARD_CONFIG } from "../metrics/data.js";
import { DEFAULT_MODEL } from "../processing.js";
import { SETTINGS } from "../settings.js";
import { dedupeKey, normalizeComment } from "../validation.js";
import {
  FakeAI, VALID, WEBHOOK, apiRequest, capacityError, counterBytes, makeApp, makeCtx, makeEnv, postSubmission,
  quotaError, spyConsole, stage1, standardFetch, verdict,
} from "./helpers.js";

const WORKER_DIR = new URL("../", import.meta.url);
const ROOT = new URL("../../", import.meta.url);

let spy;
let fetchMock;
afterEach(() => {
  spy?.restore();
  fetchMock?.restore();
});

/** wrangler.jsonc without comments (string-aware, so "https://" survives). */
function readWrangler() {
  const text = readFileSync(new URL("wrangler.jsonc", ROOT), "utf8");
  let out = "";
  for (let i = 0, inString = false; i < text.length; i += 1) {
    const ch = text[i];
    if (inString) {
      out += ch;
      if (ch === "\\") out += text[++i];
      else if (ch === '"') inString = false;
    } else if (ch === '"') {
      inString = true;
      out += ch;
    } else if (ch === "/" && text[i + 1] === "/") {
      while (i < text.length && text[i] !== "\n") i += 1;
      out += "\n";
    } else if (ch === "/" && text[i + 1] === "*") {
      i = text.indexOf("*/", i + 2) + 1;
    } else {
      out += ch;
    }
  }
  return JSON.parse(out.replace(/,(\s*[}\]])/g, "$1"));
}

describe("privacy: logs", () => {
  it("never passes a comment, an anonymized comment, a body or an IP to the console", async () => {
    const secretWords = ["Joaquina Pereira", "XYZZY-segredo", "203.0.113.7", "token-ok"];
    const comment = `A Joaquina Pereira grita comigo e me humilha, XYZZY-segredo, ligue 11 98765-4321.`;
    spy = spyConsole();
    fetchMock = standardFetch({ webhookStatus: 500 });
    const env = makeEnv({
      EVAL_TOKEN: "eval-secret",
      ALERT_WEBHOOK_URL: WEBHOOK,
      AI: new FakeAI({
        classify: [
          "{ broken XYZZY-segredo", stage1({ t: ["lid"], s: "neg", c: true, k: ["am"], e: "grita comigo" }),
          quotaError(), capacityError(), new Error("model said XYZZY-segredo"), new Error("XYZZY-segredo again"),
        ],
        verify: [verdict({ c: true, k: ["am"], e: "me humilha" })],
      }),
    });
    const { app, now } = makeApp();
    // Accepted and classified (with an invalid first answer, a critical result and a failing webhook).
    let ctx = makeCtx();
    await app.fetch(postSubmission({ ...VALID, comment }), env, ctx);
    await ctx.settle();
    // Quota error, capacity error and other errors in the background and in the cron.
    for (const text of [`${comment} 2`, `${comment} 3`]) {
      ctx = makeCtx();
      await app.fetch(postSubmission({ ...VALID, comment: text }, { "CF-Connecting-IP": "198.51.100.3" }), env, ctx);
      await ctx.settle();
    }
    now.advance(10 * 60_000);
    env.DB.sqlite.exec("UPDATE meta SET value = '2026-11-10' WHERE key = 'quota_exhausted_day'");
    for (const cron of [CRON_RETRY, CRON_PURGE]) {
      ctx = makeCtx();
      await app.scheduled({ cron, scheduledTime: now() }, env, ctx);
      await ctx.settle();
    }
    // Rejections, the evaluate route and a broken database.
    await app.fetch(postSubmission({ ...VALID, comment, score: 99 }), env, makeCtx());
    await app.fetch(postSubmission(`{"comment": "${comment}"`), env, makeCtx());
    await app.fetch(apiRequest("/api/v1/internal/evaluate", {
      method: "POST", headers: { "Content-Type": "application/json", "X-Eval-Token": "eval-secret" },
      body: JSON.stringify({ items: [{ id: "S1", comment }] }),
    }), env, makeCtx());
    env.DB.failWith = new Error(`D1_ERROR near "${comment}"`);
    await app.fetch(postSubmission({ ...VALID, comment }), env, makeCtx());
    spy.restore();

    assert.ok(spy.lines.length >= 4, spy.lines.join("\n"));
    for (const line of spy.lines) {
      for (const word of secretWords) assert.ok(!line.includes(word), line);
      assert.ok(!line.includes("[PESSOA]") && !line.includes("grita"), line);
      assert.match(line, /^[a-z_]+( [a-z_]+=[A-Za-z0-9_.:-]+)*$/, line);
    }
  });

  it("logEvent reduces anything that is not a code to ?", () => {
    spy = spyConsole();
    logEvent("event", { code: 3036, status: "timeout", text: "um comentário inteiro", nested: { a: 1 } });
    spy.restore();
    assert.deepEqual(spy.lines, ["event code=3036 status=timeout text=? nested=?"]);
  });

  it("calls the console only through log.js", () => {
    const files = readdirSync(WORKER_DIR).filter((f) => f.endsWith(".js") && f !== "log.js");
    for (const file of files) {
      assert.ok(!/console\./.test(readFileSync(new URL(file, WORKER_DIR), "utf8")), file);
    }
  });
});

describe("tracking codes and hashes (enps.queue.ingest)", () => {
  it("draws codes from the alphabet without 0, O, 1, I or L", () => {
    const bytes = counterBytes(7);
    for (let i = 0; i < 200; i += 1) assert.match(newTrackingCode(bytes), /^[2-9A-HJKMNP-Z]{4}-[2-9A-HJKMNP-Z]{4}$/);
    // 255 is refused for a 3-letter alphabet (256 % 3 = 1), so no letter is favoured.
    assert.equal(randomString("abc", 3, () => Uint8Array.from([0, 1, 255, 2, 3, 4])), "abc");
  });

  it("normalizes and hashes like the Python side", async () => {
    assert.equal(normalizeTrackingCode(" ab2c-de3f "), "AB2CDE3F");
    assert.equal(normalizeTrackingCode("AB2C DE3F"), "AB2CDE3F");
    assert.equal(normalizeTrackingCode("AB0C-DE3F"), null);
    assert.equal(normalizeTrackingCode("AB2C-DE3"), null);
    assert.equal(formatTrackingCode("ab2cde3f"), "AB2C-DE3F");
    const expected = "5bc229457f40c6a9abe716d2c9f1651a8e26bbcf8236e341ac2a2b1b246291b0"; // tracking_hash("ab2c-de3f")
    assert.equal(await trackingHash("ab2c-de3f"), expected);
    assert.equal(await trackingHash("AB2CDE3F"), createHash("sha256").update("AB2CDE3F").digest("hex"));
    assert.equal(await trackingHash("nope"), null);
  });
});

describe("duplicate key (enps.queue.ingest.normalize_comment)", () => {
  // [text, normalize_comment(text), dedupe_key(text)] computed by the Python code.
  const CASES = [
    ["Minha gestora é ÓTIMA comigo!", "minha gestora é ótima comigo", "a8daa5679b61fcdbb0f235013f681b068c2bf38573071f29d55884543d879101"],
    ["ok", "ok", null],
    ["  O_[PESSOA]   grita\t\tcom  todos!!! ", "o pessoa grita com todos", "7e8d672a1aac13d497d7f924684fa16c7ffe4ed9b871e57a825e0edebdd40a02"],
    ["Ação—reação: 100% café… é_isso", "ação reação 100 café é isso", "e18b937cc9c705f553e4c3740e78be408f17b90892a5bfe663b89f458ec6f363"],
    ["\u001cÉ   ÇA  va bem", "é ça va bem", null],
    ["água é boa demais pra todo mundo", "a gua e boa demais pra todo mundo", "f2ae3190442ca03c42ad6f7405f9841a452ba91ef11e16ee3c64c40f71ba6222"],
  ];
  for (const [text, normalized, key] of CASES) {
    it(JSON.stringify(text), async () => {
      assert.equal(normalizeComment(text), normalized);
      assert.equal(await dedupeKey(text), key);
    });
  }
});

describe("configuration", () => {
  const wrangler = readWrangler();

  it("wrangler.jsonc declares the bindings, crons and model the code uses", () => {
    assert.equal(wrangler.name, "voz-sem-nome");
    assert.equal(wrangler.main, "worker/index.js");
    assert.equal(wrangler.vars.AI_MODEL, DEFAULT_MODEL);
    assert.deepEqual(wrangler.ai, { binding: "AI" });
    assert.equal(wrangler.d1_databases[0].binding, "DB");
    assert.equal(wrangler.d1_databases[0].database_name, "voz-sem-nome");
    assert.deepEqual(wrangler.triggers.crons, [CRON_RETRY, CRON_PURGE]);
    assert.equal(wrangler.ratelimits[0].name, "RL");
    assert.equal(wrangler.ratelimits[0].simple.period, 60);
    assert.deepEqual(wrangler.observability, { enabled: true, logs: { invocation_logs: false } });
    assert.deepEqual(wrangler.assets.run_worker_first, ["/api/*"]);
    assert.equal(wrangler.workers_dev, false);
    assert.equal(wrangler.preview_urls, false);
    for (const secret of ["TURNSTILE_SECRET", "EVAL_TOKEN", "ALERT_WEBHOOK_URL", "ALERT_WEBHOOK_TOKEN"]) {
      assert.ok(!(secret in wrangler.vars), secret);
    }
  });

  it("settings.js agrees with the dashboard port built from the same YAML", () => {
    assert.equal(SETTINGS.privacy.min_group_size, DASHBOARD_CONFIG.min_group_size);
    assert.equal(SETTINGS.privacy.display_excerpt_chars, DASHBOARD_CONFIG.display_excerpt_chars);
    assert.deepEqual(SETTINGS.survey.areas.map((a) => a.code), DASHBOARD_CONFIG.areas);
    assert.deepEqual(SETTINGS.survey.tenure_bands, DASHBOARD_CONFIG.tenures);
    assert.ok(Object.isFrozen(SETTINGS.limits));
  });
});
