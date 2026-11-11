// Fake bindings and a scripted global fetch for the Worker tests. No dependencies.

export const ORIGIN = "https://api.example.test";
export const SITE = "https://voz-sem-nome.example.test";
export const SECRET = "origin-secret";
export const TURNSTILE_SECRET = "turnstile-secret";
export const SITEVERIFY = "https://challenges.cloudflare.com/turnstile/v0/siteverify";

/** In-memory Workers KV with the calls the Worker uses, recording every write. */
export class FakeKV {
  constructor() {
    this.store = new Map();
    this.writes = [];
  }

  async get(key, type = "text") {
    const entry = this.store.get(key);
    if (!entry) return null;
    return type === "json" ? JSON.parse(entry.value) : entry.value;
  }

  async getWithMetadata(key, type = "text") {
    const entry = this.store.get(key);
    if (!entry) return { value: null, metadata: null };
    return { value: await this.get(key, type), metadata: entry.metadata ?? null };
  }

  async put(key, value, options = {}) {
    this.writes.push(key);
    this.store.set(key, { value, metadata: options.metadata ?? null });
  }

  seed(key, body, fetchedAt = "2026-11-10T12:00:00.000Z") {
    this.store.set(key, {
      value: JSON.stringify({ body, fetched_at: fetchedAt, sha256: "seeded" }),
      metadata: { sha256: "seeded", fetched_at: fetchedAt },
    });
  }
}

/** Rate limit binding that allows `allowed` calls per key. */
export class FakeRateLimiter {
  constructor(allowed = 5) {
    this.allowed = allowed;
    this.calls = [];
  }

  async limit({ key }) {
    this.calls.push(key);
    return { success: this.calls.filter((k) => k === key).length <= this.allowed };
  }
}

export function makeEnv(overrides = {}) {
  return {
    API_ORIGIN: ORIGIN,
    ORIGIN_SHARED_SECRET: SECRET,
    TURNSTILE_SECRET,
    SNAPSHOTS: new FakeKV(),
    RL: new FakeRateLimiter(),
    ASSETS: { fetch: async (request) => new Response(`asset ${new URL(request.url).pathname}`) },
    ...overrides,
  };
}

/**
 * Replaces globalThis.fetch with `handler(url, init, call)`; every call is recorded with
 * its parsed URL, headers and body text. Returns { calls, restore }.
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
      signal: init.signal,
      body: request.body ? await request.text() : "",
    };
    calls.push(call);
    return handler(call);
  };
  return { calls, restore: () => { globalThis.fetch = real; } };
}

export function jsonResponse(status, data, headers = {}) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

export function timeoutError() {
  return new DOMException("The operation was aborted due to timeout", "TimeoutError");
}

/** Origin calls only (siteverify excluded). */
export function originCalls(calls) {
  return calls.filter((call) => call.url.origin === ORIGIN);
}

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
  comment: "Falta gente na equipe.",
  consent: true,
  lang: "pt",
  turnstile: "token-ok",
};

/** Context with waitUntil, collecting the promises so a test can await them. */
export function makeCtx() {
  const pending = [];
  return { pending, waitUntil: (promise) => pending.push(promise) };
}
