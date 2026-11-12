import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { ANONYMIZER_VERSION } from "../anonymize/index.js";
import { FakeAI, apiRequest, capacityError, makeApp, makeCtx, makeEnv, quotaError, stage1 } from "./helpers.js";

const PATH = "/api/v1/internal/evaluate";

function evaluateRequest(body, headers = { "X-Eval-Token": "eval-secret" }) {
  return apiRequest(PATH, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...headers },
    body: typeof body === "string" ? body : JSON.stringify(body),
  });
}

async function call(env, body, headers) {
  const { app } = makeApp({ now: (() => { let t = 0; return () => (t += 100); })() });
  const response = await app.fetch(evaluateRequest(body, headers), env, makeCtx());
  return { response, data: await response.json() };
}

const items = (n) => Array.from({ length: n }, (_, i) => ({ id: `S${String(i + 1).padStart(4, "0")}`, comment: `Comentário ${i} sobre a escala.` }));

describe("POST /api/v1/internal/evaluate", () => {
  it("does not exist without the EVAL_TOKEN secret", async () => {
    const env = makeEnv();
    const { response, data } = await call(env, { items: items(1) });
    assert.equal(response.status, 404);
    assert.deepEqual(data, { error: "not_found" });
    assert.equal(env.AI.calls.length, 0);
  });

  it("refuses a missing or wrong token", async () => {
    for (const headers of [{}, { "X-Eval-Token": "wrong" }, { "X-Eval-Token": "" }]) {
      const env = makeEnv({ EVAL_TOKEN: "eval-secret" });
      const { response, data } = await call(env, { items: items(1) }, headers);
      assert.equal(response.status, 403);
      assert.deepEqual(data, { error: "forbidden" });
      assert.equal(env.AI.calls.length, 0);
    }
  });

  it("validates the body: 1 to 10 items with an id and a comment", async () => {
    const env = makeEnv({ EVAL_TOKEN: "eval-secret" });
    const cases = [
      [{ items: [] }, { items: "must be a list of 1 to 10 items" }],
      [{ items: items(11) }, { items: "must be a list of 1 to 10 items" }],
      [{ items: "x" }, { items: "must be a list of 1 to 10 items" }],
      [{ items: [{ comment: "x" }] }, { "items.0.id": "must be a short text" }],
      [{ items: [{ id: "S1", comment: 3 }] }, { "items.0.comment": "must be text" }],
      [{ items: [{ id: "S1", comment: "x".repeat(1001) }] }, { "items.0.comment": "must have at most 1000 characters" }],
      [{ items: items(1), verifier: "yes" }, { verifier: "must be true, false or null" }],
    ];
    for (const [body, fields] of cases) {
      const { response, data } = await call(env, body);
      assert.equal(response.status, 422, JSON.stringify(body).slice(0, 80));
      assert.deepEqual(data, { error: "validation", fields });
    }
    assert.equal((await call(env, "[1]")).response.status, 422);
    assert.equal(env.AI.calls.length, 0);
  });

  it("runs the edge path on each item and stores nothing", async () => {
    const env = makeEnv({
      EVAL_TOKEN: "eval-secret",
      AI: new FakeAI({ classify: [stage1({ t: ["lid"], s: "neg" }), stage1({ t: ["inf"], s: "neu" })] }),
    });
    const { response, data } = await call(env, {
      items: [{ id: "S0001", comment: "  O João Silva não escuta ninguém.  " }, { id: 7, comment: "Escala ok." }, { id: "S0003", comment: "" }],
      verifier: false,
    });
    assert.equal(response.status, 200);
    assert.equal(data.model, "@cf/google/gemma-4-26b-a4b-it");
    assert.equal(data.anonymizer_version, ANONYMIZER_VERSION);
    const [first, second, third] = data.items;
    assert.equal(first.id, "S0001");
    assert.equal(first.comment_anon, "O [PESSOA] não escuta ninguém.");
    assert.deepEqual(first.pii_counts, { PESSOA: 1 });
    assert.deepEqual(Object.keys(first), ["id", "comment_anon", "pii_counts", "anonymize_ms", "result", "total_ms"]);
    assert.equal(first.result.status, "done");
    assert.deepEqual(first.result.themes, ["lid"]);
    assert.equal(first.result.sentiment, "neg");
    assert.equal(first.result.verifier_version, null); // verifier off for this request
    assert.equal(first.result.prompt_tokens, 2100);
    assert.equal(first.result.output_tokens, 40);
    assert.ok(first.result.ai_ms > 0 && first.total_ms >= first.result.ai_ms);
    assert.deepEqual(first.result.stages.map((s) => s.name), ["lexicon", "classify"]);
    assert.equal(second.id, "7");
    assert.deepEqual(second.result.themes, ["inf"]);
    assert.equal(third.result.status, "skipped");
    assert.equal(env.AI.calls.length, 2);
    assert.equal(env.DB.log.length, 0);
  });

  it("uses the verifier by default", async () => {
    const env = makeEnv({ EVAL_TOKEN: "eval-secret", AI: new FakeAI({ classify: [stage1({ t: ["lid"], s: "neg" })] }) });
    const { data } = await call(env, { items: items(1) });
    assert.deepEqual(env.AI.tasks(), ["classify", "verify"]);
    assert.match(data.items[0].result.verifier_version, /^verify_critical\.v1\+/);
  });

  it("stops on quota or capacity errors and returns the items already done", async () => {
    // The clock of call() starts at the epoch: the quota resets about a day later.
    for (const [err, code, retry] of [[quotaError(), "quota", /^86[34]\d\d$/], [capacityError(), "capacity", /^60$/]]) {
      const env = makeEnv({ EVAL_TOKEN: "eval-secret", AI: new FakeAI({ classify: [stage1({ s: "neu" }), err] }) });
      const { response, data } = await call(env, { items: items(3), verifier: false });
      assert.equal(response.status, 503);
      assert.equal(data.error, code);
      assert.equal(data.model, "@cf/google/gemma-4-26b-a4b-it");
      assert.deepEqual(data.items.map((i) => i.id), ["S0001"]);
      assert.match(response.headers.get("Retry-After"), retry);
      assert.equal(env.AI.calls.length, 2);
    }
  });

  it("records another model error per item and goes on", async () => {
    const env = makeEnv({
      EVAL_TOKEN: "eval-secret",
      AI: new FakeAI({ classify: [Object.assign(new Error("5028: deprecated"), { code: 5028 }), stage1({ s: "neu" })] }),
    });
    const { response, data } = await call(env, { items: items(2), verifier: false });
    assert.equal(response.status, 200);
    assert.deepEqual(data.items[0].result, { status: "failed", error_code: 5028 });
    assert.equal(data.items[1].result.status, "done");
  });
});
