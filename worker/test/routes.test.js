import assert from "node:assert/strict";
import { afterEach, describe, it } from "node:test";
import worker from "../index.js";
import {
  ORIGIN, SECRET, apiRequest, jsonResponse, makeEnv, mockFetch, originCalls,
} from "./helpers.js";

let fetchMock;

afterEach(() => fetchMock?.restore());

function okOrigin() {
  fetchMock = mockFetch(() => jsonResponse(200, { ok: true }));
  return fetchMock;
}

describe("allowlist", () => {
  it("serves non API paths from the assets binding", async () => {
    const { calls } = okOrigin();
    const response = await worker.fetch(apiRequest("/painel/"), makeEnv());
    assert.equal(await response.text(), "asset /painel/");
    assert.equal(calls.length, 0);
  });

  const allowed = [
    ["/api/v1/public/status", "/v1/public/status"],
    ["/api/v1/public/health", "/v1/public/health"],
    ["/api/v1/public/dashboard?dataset=public", "/v1/public/dashboard?dataset=public"],
    ["/api/v1/public/dashboard?dataset=synthetic", "/v1/public/dashboard?dataset=synthetic"],
    ["/api/v1/public/submissions/ab2c-de3f", "/v1/public/submissions/AB2C-DE3F"],
    ["/api/v1/public/submissions/AB2CDE3F", "/v1/public/submissions/AB2C-DE3F"],
  ];
  for (const [path, upstream] of allowed) {
    it(`forwards GET ${path}`, async () => {
      const { calls } = okOrigin();
      const response = await worker.fetch(apiRequest(path), makeEnv());
      assert.equal(response.status, 200);
      assert.equal(response.headers.get("Cache-Control"), "no-store");
      const [call] = originCalls(calls);
      assert.equal(call.url.href, ORIGIN + upstream);
      assert.equal(call.method, "GET");
    });
  }

  it("drops extra query parameters of the dashboard and defaults to public", async () => {
    const { calls } = okOrigin();
    await worker.fetch(apiRequest("/api/v1/public/dashboard?dataset=synthetic&x=1"), makeEnv());
    await worker.fetch(apiRequest("/api/v1/public/dashboard"), makeEnv());
    assert.deepEqual(originCalls(calls).map((c) => c.url.search), ["?dataset=synthetic", "?dataset=public"]);
  });

  it("rejects an unknown dataset without calling the origin", async () => {
    const { calls } = okOrigin();
    const response = await worker.fetch(apiRequest("/api/v1/public/dashboard?dataset=all"), makeEnv());
    assert.equal(response.status, 422);
    assert.equal((await response.json()).error, "validation");
    assert.equal(calls.length, 0);
  });

  const blocked = [
    ["GET", "/api"],
    ["GET", "/api/"],
    ["GET", "/api/v1/public"],
    ["GET", "/api/v1/public/status/"],
    ["GET", "/api/v1/internal/alerts"],
    ["GET", "/api/v1/public/submissions"],
    ["GET", "/api/v1/public/submissions/not-a-code"],
    ["GET", "/api/v1/public/submissions/OOOO-IIII"],
    ["GET", "/api/v1/public/submissions/AB2C-DE3F/extra"],
    ["GET", "/api/v1/public/submissions/..%2F..%2Finternal"],
    ["DELETE", "/api/v1/public/submissions/AB2C-DE3F"],
    ["POST", "/api/v1/public/status"],
    ["PUT", "/api/v1/public/submissions"],
    ["HEAD", "/api/v1/public/status"],
    ["GET", "/api/v1/public/docs"],
  ];
  for (const [method, path] of blocked) {
    it(`answers 404 JSON to ${method} ${path} without calling the origin`, async () => {
      const { calls } = okOrigin();
      const response = await worker.fetch(apiRequest(path, { method }), makeEnv());
      assert.equal(response.status, 404);
      assert.match(response.headers.get("Content-Type"), /^application\/json/);
      if (method !== "HEAD") assert.deepEqual(await response.json(), { error: "not_found" });
      assert.equal(calls.length, 0);
    });
  }
});

describe("forwarding headers", () => {
  it("sends the shared secret and the visitor IP on GET routes", async () => {
    const { calls } = okOrigin();
    await worker.fetch(apiRequest("/api/v1/public/submissions/AB2C-DE3F", {
      headers: { "CF-Connecting-IP": "198.51.100.4", Cookie: "a=b", "X-Origin-Secret": "forged" },
    }), makeEnv());
    const [call] = calls;
    assert.equal(call.headers.get("X-Origin-Secret"), SECRET);
    assert.equal(call.headers.get("X-Client-IP"), "198.51.100.4");
    assert.equal(call.headers.get("Cookie"), null);
    assert.ok(call.signal instanceof AbortSignal, "GET routes have a timeout");
  });

  it("passes a 404 for an unknown code through", async () => {
    fetchMock = mockFetch(() => jsonResponse(404, { error: "not_found" }));
    const response = await worker.fetch(apiRequest("/api/v1/public/submissions/AB2C-DE3F"), makeEnv());
    assert.equal(response.status, 404);
    assert.deepEqual(await response.json(), { error: "not_found" });
  });

  it("answers 503 offline when the origin is unreachable for a submission lookup", async () => {
    fetchMock = mockFetch(() => { throw new TypeError("network"); });
    const response = await worker.fetch(apiRequest("/api/v1/public/submissions/AB2C-DE3F"), makeEnv());
    assert.equal(response.status, 503);
    assert.deepEqual(await response.json(), { error: "offline" });
  });

  it("turns a tunnel error page into 503 offline", async () => {
    fetchMock = mockFetch(() => new Response("<html>error 1033</html>", {
      status: 530, headers: { "Content-Type": "text/html" },
    }));
    const response = await worker.fetch(apiRequest("/api/v1/public/health"), makeEnv());
    assert.equal(response.status, 503);
    assert.deepEqual(await response.json(), { error: "offline" });
  });
});
