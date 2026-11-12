// Fake bindings for the Worker tests: D1 on node:sqlite, a scripted Workers AI, the assets
// binding, the rate limiter, a clock, a scripted global fetch and a console spy. No dependencies.

import { DatabaseSync } from "node:sqlite";
import { PROMPTS } from "../classify/data.js";
import { createApp } from "../app.js";
import { ensureSchema } from "../store.js";

export const SITE = "https://voz-sem-nome.example.test";
export const TURNSTILE_SECRET = "turnstile-secret";
export const SITEVERIFY = "https://challenges.cloudflare.com/turnstile/v0/siteverify";
export const WEBHOOK = "https://n8n.example.test/webhook/critical-alert";
export const NOW = Date.parse("2026-11-11T15:00:00.000Z");
export const MODEL = "@cf/google/gemma-4-26b-a4b-it";

// ------------------------------------------------------------------ D1

/** A value D1 can bind; booleans and undefined are refused here so the code never relies on them. */
function checkBindable(value) {
  const ok = value === null || typeof value === "number" || typeof value === "string" || value instanceof ArrayBuffer;
  if (!ok) throw new TypeError(`D1_TYPE_ERROR: type '${typeof value}' not supported for value '${value}'`);
}

class FakeStatement {
  constructor(d1, sql, values = []) {
    this.d1 = d1;
    this.sql = sql;
    this.values = values;
  }

  bind(...values) {
    values.forEach(checkBindable);
    return new FakeStatement(this.d1, this.sql, values);
  }

  /** Runs now and returns a D1Result: {results, success, meta: {changes, last_row_id}}. */
  execute() {
    this.d1.log.push({ sql: this.sql, values: this.values });
    const statement = this.d1.sqlite.prepare(this.sql);
    if (statement.columns().length) {
      const results = statement.all(...this.values).map((row) => ({ ...row }));
      const changes = /\bRETURNING\b/i.test(this.sql) ? results.length : 0;
      return { results, success: true, meta: { changes, last_row_id: 0, rows_read: results.length } };
    }
    const info = statement.run(...this.values);
    return { results: [], success: true, meta: { changes: info.changes, last_row_id: Number(info.lastInsertRowid) } };
  }

  async first(column) {
    const row = this.execute().results[0] ?? null;
    if (column === undefined) return row;
    if (row && !(column in row)) throw new Error(`D1_COLUMN_NOTFOUND: ${column}`);
    return row ? row[column] : null;
  }

  async all() {
    return this.execute();
  }

  async run() {
    return this.execute();
  }
}

/** In-memory D1 binding: prepare/bind/first/all/run and batch (one transaction). */
export class FakeD1 {
  constructor() {
    this.sqlite = new DatabaseSync(":memory:");
    this.log = [];
    this.failWith = null; // set to an Error to make every call fail
  }

  prepare(sql) {
    if (this.failWith) throw this.failWith;
    return new FakeStatement(this, sql);
  }

  async batch(statements) {
    if (this.failWith) throw this.failWith;
    this.sqlite.exec("BEGIN");
    try {
      const results = statements.map((statement) => statement.execute());
      this.sqlite.exec("COMMIT");
      return results;
    } catch (err) {
      this.sqlite.exec("ROLLBACK");
      throw err;
    }
  }

  /** Direct SQL for test setup and assertions. */
  rows(sql, ...values) {
    return this.sqlite.prepare(sql).all(...values).map((row) => ({ ...row }));
  }

  row(sql, ...values) {
    return this.rows(sql, ...values)[0] ?? null;
  }
}

// ------------------------------------------------------------------ Workers AI

export const stage1 = ({ t = ["inf"], s = "neg", e = "", c = false, k = [] } = {}) =>
  JSON.stringify({ t, s, e, c, k });
export const verdict = ({ e = "", c = false, k = [] } = {}) => JSON.stringify({ e, c, k });

/** An OpenAI-shaped answer, as gemma-4-26b-a4b-it returns it. */
export function chatAnswer(content, usage = { prompt_tokens: 2100, completion_tokens: 40 }) {
  return { choices: [{ message: { content } }], usage: { ...usage, total_tokens: usage.prompt_tokens + usage.completion_tokens } };
}

export function quotaError() {
  return Object.assign(new Error("3036: Account limited: daily free allocation of 10,000 neurons used up"), { code: 3036 });
}

export function capacityError() {
  return Object.assign(new Error("3040: Out of capacity"), { code: 3040 });
}

/**
 * Scripted AI binding. `script` maps "classify" and "verify" to queues; a string is returned as
 * a chat answer, an Error is thrown, a function is called with the input. `fallback` answers
 * when a queue is empty (default: a valid non critical answer).
 */
export class FakeAI {
  constructor(script = {}, { fallback = true, hold = false } = {}) {
    this.queues = { classify: [...(script.classify ?? [])], verify: [...(script.verify ?? [])] };
    this.fallback = fallback;
    this.calls = [];
    // With hold, every call waits for release(): the row stays "processing" until the test says so.
    this.gate = hold ? new Promise((resolve) => { this.release = resolve; }) : null;
  }

  static taskOf(input) {
    return input.messages[0].content === PROMPTS.classify.system ? "classify" : "verify";
  }

  async run(model, input) {
    const task = FakeAI.taskOf(input);
    this.calls.push({ model, task, input });
    if (this.gate) await this.gate;
    let next = this.queues[task].shift();
    if (next === undefined) {
      if (!this.fallback) throw new Error(`unexpected ${task} call`);
      next = task === "classify" ? stage1() : verdict();
    }
    if (next instanceof Error) throw next;
    if (typeof next === "function") return next(input);
    return typeof next === "string" ? chatAnswer(next) : next;
  }

  tasks() {
    return this.calls.map((c) => c.task);
  }
}

// ------------------------------------------------------------------ other bindings

/** Rate limit binding that allows `allowed` calls per key. */
export class FakeRateLimiter {
  constructor(allowed = 3) {
    this.allowed = allowed;
    this.calls = [];
  }

  async limit({ key }) {
    this.calls.push(key);
    return { success: this.calls.filter((k) => k === key).length <= this.allowed };
  }
}

/** Assets binding serving `files` ({path: body}); anything else is a 404. */
export class FakeAssets {
  constructor(files = {}) {
    this.files = files;
    this.requests = [];
  }

  async fetch(input) {
    const url = new URL(input instanceof Request ? input.url : input);
    this.requests.push(url.pathname);
    if (url.pathname in this.files) {
      return new Response(this.files[url.pathname], { headers: { "Content-Type": "application/json" } });
    }
    return new Response(`asset ${url.pathname}`, { status: url.pathname.startsWith("/data/") ? 404 : 200 });
  }
}

export function makeEnv(overrides = {}) {
  return {
    DB: new FakeD1(),
    AI: new FakeAI(),
    RL: new FakeRateLimiter(),
    ASSETS: new FakeAssets({ "/data/dashboard-synthetic.json": JSON.stringify({ dataset: "synthetic", sample: true }) }),
    AI_MODEL: MODEL,
    TURNSTILE_SECRET,
    ...overrides,
  };
}

/** Context with waitUntil, collecting the promises so a test can await them. */
export function makeCtx() {
  const pending = [];
  return {
    pending,
    waitUntil: (promise) => pending.push(promise),
    settle: () => Promise.all(pending.splice(0)),
  };
}

/** Clock in milliseconds that tests move by hand; every read also adds `step` ms. */
export function makeClock(start = NOW, step = 0) {
  let t = start;
  const now = () => (t += step);
  now.advance = (ms) => { t += ms; };
  now.set = (ms) => { t = ms; };
  return now;
}

/** Deterministic random bytes (a counter), so ids differ but tests repeat. */
export function counterBytes(seed = 1) {
  let state = seed;
  return (n) => Uint8Array.from({ length: n }, () => (state = (state * 1103515245 + 12345) % 2147483648) % 256);
}

export function makeApp(options = {}) {
  const now = options.now ?? makeClock();
  return { app: createApp({ randomBytes: counterBytes(), ...options, now }), now };
}

// ------------------------------------------------------------------ requests

export function apiRequest(path, init = {}) {
  return new Request(SITE + path, init);
}

export function postSubmission(body, headers = {}) {
  const text = typeof body === "string" ? body : JSON.stringify(body);
  return apiRequest("/api/v1/public/submissions", {
    method: "POST",
    headers: { "Content-Type": "application/json", "CF-Connecting-IP": "203.0.113.7", ...headers },
    body: text,
  });
}

export const VALID = {
  score: 6,
  area: "operacoes",
  tenure: "y1to3",
  comment: "Falta gente na equipe e o sistema de escala trava toda semana.",
  consent: true,
  lang: "pt",
  turnstile: "token-ok",
};

// ------------------------------------------------------------------ global fetch and console

export function jsonResponse(status, data, headers = {}) {
  return new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json", ...headers } });
}

/**
 * Replaces globalThis.fetch with `handler(call)`; every call is recorded with its parsed URL,
 * headers and body text. Returns { calls, restore }.
 */
export function mockFetch(handler) {
  const real = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (input, init = {}) => {
    const request = new Request(input, init);
    const call = {
      url: new URL(request.url),
      method: request.method,
      headers: request.headers,
      body: request.body ? await request.text() : "",
    };
    calls.push(call);
    return handler(call);
  };
  return { calls, restore: () => { globalThis.fetch = real; } };
}

/** Siteverify says yes; the webhook (when called) answers `webhookStatus`. */
export function standardFetch({ success = true, webhookStatus = 200 } = {}) {
  return mockFetch((call) => {
    if (call.url.href === SITEVERIFY) return jsonResponse(200, { success });
    if (call.url.href === WEBHOOK) return new Response("ok", { status: webhookStatus });
    throw new Error(`unexpected fetch ${call.url.href}`);
  });
}

const CONSOLE_METHODS = ["log", "info", "warn", "error", "debug", "trace"];

/** Records every console call as one string; restore() puts the real methods back. */
export function spyConsole() {
  const lines = [];
  const real = Object.fromEntries(CONSOLE_METHODS.map((m) => [m, console[m]]));
  for (const method of CONSOLE_METHODS) {
    console[method] = (...args) => lines.push(args.map((a) => (typeof a === "string" ? a : JSON.stringify(a) ?? String(a))).join(" "));
  }
  return { lines, restore: () => Object.assign(console, real) };
}

// ------------------------------------------------------------------ seeding D1

let seeded = 0;

/** Insert submissions straight into D1 (after creating the schema); returns their ids. */
export async function seedSubmissions(db, rows) {
  await ensureSchema(db);
  const ids = [];
  for (const fields of rows) {
    seeded += 1;
    const row = {
      public_id: `seed${String(seeded).padStart(6, "0")}`,
      tracking_hash: null,
      created_at: "2026-11-11T12:00:00.000+00:00",
      score: 7,
      area: "operacoes",
      tenure: "y1to3",
      lang: "pt",
      comment_anon: "",
      pii_counts: "{}",
      anonymizer_version: "edge-1.0.0",
      status: "skipped",
      attempts: 0,
      ...fields,
    };
    const columns = Object.keys(row);
    const info = db.sqlite
      .prepare(`INSERT INTO submissions (${columns.join(", ")}) VALUES (${columns.map(() => "?").join(", ")})`)
      .run(...Object.values(row));
    ids.push(Number(info.lastInsertRowid));
  }
  return ids;
}
