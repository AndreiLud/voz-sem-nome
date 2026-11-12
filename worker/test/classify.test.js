import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { describe, it } from "node:test";
import { LEXICON, LLM, PROMPTS, SCHEMAS } from "../classify/data.js";
import { LEXICON_VERSION, STRONG, WEAK, fold, matchLexicon } from "../classify/lexicon.js";
import {
  CapacityError, QuotaExceeded, STAGES, WorkersAIError, buildRequest, classifyComment, decide, errorCode,
  extractJson, needsVerification, readAnswer, stageTotals,
} from "../classify/pipeline.js";
import { userMessage } from "../classify/prompts.js";
import { OutputError, outputDict, validateClassify, validateVerify } from "../classify/schema.js";
import { codePointIndex } from "../classify/text.js";

// Cases with expected values computed by the Python classifier (tests/test_classifier_cases.py).
const CASES = JSON.parse(
  readFileSync(new URL("../../tests/fixtures/classifier_cases.json", import.meta.url), "utf8"),
);
const MODEL = "fake-model";
const label = (value) => JSON.stringify(value).slice(0, 70);

// ------------------------------------------------------------------ helpers

/** Fixture dicts (as_dict) -> the shapes pipeline.js works with. */
const stage1From = (d) =>
  d && { themes: d.t, sentiment: d.s, excerpt: d.e, critical: d.c, types: d.k, notes: d.notes ?? [] };
const verdictFrom = (d) => d && { excerpt: d.e, critical: d.c, types: d.k, notes: d.notes ?? [] };

/** Error texts match, except the parser message after "invalid JSON:", which differs by design. */
function assertSameError(actual, expected) {
  if (expected.startsWith("invalid JSON:")) assert.match(actual, /^invalid JSON: /);
  else assert.equal(actual, expected);
}

const taskOf = (input) => (input.messages[0].content === PROMPTS.classify.system ? "classify" : "verify_critical");

/**
 * Fake Workers AI binding. `script` maps a task to a queue: a string is returned as {response},
 * an Error is thrown, a function is called with the input, anything else is returned as is.
 */
function fakeAI(script = {}) {
  const queues = { classify: [...(script.classify ?? [])], verify_critical: [...(script.verify ?? [])] };
  const calls = [];
  return {
    calls,
    tasks: () => calls.map((c) => c.task),
    async run(model, input) {
      const task = taskOf(input);
      calls.push({ model, task, input });
      if (!queues[task].length) throw new Error(`unexpected ${task} call`);
      const next = queues[task].shift();
      if (next instanceof Error) throw next;
      if (typeof next === "function") return next(input);
      return typeof next === "string" ? { response: next } : next;
    },
  };
}

function clock(step = 7) {
  let t = 1000;
  return () => (t += step);
}

const stage1 = ({ t = ["inf"], s = "neg", e = "", c = false, k = [] } = {}) => JSON.stringify({ t, s, e, c, k });
const verdict = ({ e = "", c = false, k = [] } = {}) => JSON.stringify({ e, c, k });

// ------------------------------------------------------------------ data module

describe("data module", () => {
  it("carries the Python prompt versions and schemas", () => {
    assert.match(PROMPTS.classify.prompt_version, /^classify\.v\d+\+[0-9a-f]{8}$/);
    assert.match(PROMPTS.verify.prompt_version, /^verify_critical\.v\d+\+[0-9a-f]{8}$/);
    assert.ok(!PROMPTS.classify.system.includes("{{"));
    assert.deepEqual(SCHEMAS.classify.required, Object.keys(SCHEMAS.classify.properties));
    assert.deepEqual(Object.keys(SCHEMAS.verify.properties), ["e", "c", "k"]);
    assert.deepEqual(LLM, { temperature: 0, seed: 42, max_tokens: 160, verifier_enabled: true });
  });

  it("holds the exact text each prompt_version was computed from", () => {
    // Python: "<name>+" + sha256(rendered system prompt)[:8], so the digest proves the text survived the build.
    for (const prompt of Object.values(PROMPTS)) {
      const digest = createHash("sha256").update(prompt.system, "utf8").digest("hex").slice(0, 8);
      assert.equal(prompt.prompt_version, `${prompt.name}+${digest}`);
    }
  });

  it("matches the fixture it was built with", () => {
    assert.deepEqual(CASES.data_version, { lexicon: LEXICON.version, rules: LEXICON.rules.length });
    assert.equal(LEXICON_VERSION, LEXICON.version);
  });

  it("compiles every lexicon rule", () => {
    for (const rule of LEXICON.rules) {
      assert.doesNotThrow(() => new RegExp(rule.source, rule.flags), rule.source);
      assert.ok([STRONG, WEAK].includes(rule.strength));
    }
  });
});

// ------------------------------------------------------------------ parity with Python

describe("parity: fold", () => {
  for (const c of CASES.fold) {
    it(label(c.text), () => assert.deepEqual(fold(c.text), { folded: c.folded, index: c.index }));
  }
});

describe("parity: user message", () => {
  for (const c of CASES.user_message) {
    it(label([c.text, c.hint]), () => assert.equal(userMessage(c.text, c.hint), c.expected));
  }
});

describe("parity: validation", () => {
  for (const c of CASES.validate) {
    it(`${c.stage} ${label(c.content)}`, () => {
      const validate = c.stage === "classify" ? validateClassify : validateVerify;
      if (c.expected.output) {
        assert.deepEqual(outputDict(validate(c.content, c.text)), c.expected.output);
        return;
      }
      assert.throws(() => validate(c.content, c.text), (err) => {
        assert.ok(err instanceof OutputError);
        assert.equal(err.hint, c.expected.hint);
        assertSameError(err.message, c.expected.error);
        return true;
      });
    });
  }
});

describe("parity: lexicon", () => {
  for (const c of [...CASES.lexicon, ...CASES.lexicon_corpus]) {
    it(label(c.text), () => assert.deepEqual(matchLexicon(c.text), c.hits));
  }
});

describe("parity: every lexicon pattern on generated probes", () => {
  const rules = LEXICON.rules.map((r) => new RegExp(r.source, `${r.flags}g`));
  it(`${CASES.lexicon_rules.length} probes x ${rules.length} rules`, () => {
    for (const c of CASES.lexicon_rules) {
      const cp = codePointIndex(c.text);
      const matches = [];
      rules.forEach((regex, i) => {
        for (const m of c.text.matchAll(regex)) {
          if (m[0].length) matches.push([i, cp[m.index], cp[m.index + m[0].length]]);
        }
      });
      assert.deepEqual(matches, c.matches, c.text);
    }
  });
});

describe("parity: verifier gate", () => {
  for (const c of CASES.gate) {
    it(label([c.stage1, c.hits]), () => assert.equal(needsVerification(stage1From(c.stage1), c.hits), c.expected));
  }
});

describe("parity: decision", () => {
  for (const c of CASES.decide) {
    it(label([c.stage1, c.verdict, c.hits]), () =>
      assert.deepEqual(decide(stage1From(c.stage1), verdictFrom(c.verdict), c.hits), c.expected));
  }
});

describe("parity: whole classification with scripted answers", () => {
  for (const c of CASES.pipeline) {
    it(label([c.text, c.classify]), async () => {
      const ai = fakeAI({ classify: c.classify, verify: c.verify });
      const result = await classifyComment(c.text, ai, { model: MODEL, verifier: c.verifier });
      const { stages: expectedStages, calls: expectedCalls, ...expected } = c.expected;
      const { stages, ...rest } = result;
      assert.deepEqual(rest, expected);
      assert.equal(stages.length, expectedStages.length);
      stages.forEach((stage, i) => {
        const want = expectedStages[i];
        assert.deepEqual([stage.name, stage.prompt_version, stage.ok, stage.output],
          [want.name, want.prompt_version, want.ok, want.output]);
        if (want.error === null) assert.equal(stage.error, null);
        else assertSameError(stage.error, want.error);
      });
      assert.deepEqual(ai.calls.map((x) => ({ task: x.task, user: x.input.messages[1].content })), expectedCalls);
    });
  }
});

// ------------------------------------------------------------------ classifyComment

describe("classifyComment", () => {
  it("sends the Workers AI request of the edge spec", async () => {
    const ai = fakeAI({ classify: [stage1()] });
    await classifyComment("O sistema trava.", ai, { model: MODEL });
    const [{ model, input }] = ai.calls;
    assert.equal(model, MODEL);
    assert.deepEqual(input, {
      messages: [
        { role: "system", content: PROMPTS.classify.system },
        { role: "user", content: 'Comentário: """O sistema trava."""' },
      ],
      max_tokens: 160,
      temperature: 0,
      seed: 42,
      response_format: { type: "json_schema", json_schema: { name: "classificacao", schema: SCHEMAS.classify, strict: true } },
      chat_template_kwargs: { enable_thinking: false },
    });
    const verify = buildRequest(STAGES.verify, "x");
    assert.equal(verify.messages[0].content, PROMPTS.verify.system);
    assert.equal(verify.response_format.json_schema.schema, SCHEMAS.verify);
  });

  it("records tokens and time per stage and sums them", async () => {
    const ai = fakeAI({ classify: [{ response: stage1(), usage: { prompt_tokens: 2100, completion_tokens: 31 } }] });
    const result = await classifyComment("O sistema trava.", ai, { model: MODEL, now: clock(7) });
    assert.equal(result.status, "done");
    assert.deepEqual(result.stages.map((s) => s.name), ["lexicon", "classify"]);
    assert.deepEqual(result.stages[1], {
      name: "classify",
      prompt_version: PROMPTS.classify.prompt_version,
      ok: true,
      error: null,
      output: { t: ["inf"], s: "neg", e: "", c: false, k: [] },
      prompt_tokens: 2100,
      output_tokens: 31,
      total_ms: 7,
    });
    assert.deepEqual(stageTotals(result.stages), { total_ms: 7, prompt_tokens: 2100, output_tokens: 31 });
    assert.equal(result.model, MODEL);
    assert.equal(result.verifier_version, null);
  });

  describe("reads the answer", () => {
    const answer = { t: ["inf"], s: "neu", e: "", c: false, k: [] };
    const shapes = {
      "response as object": { response: answer },
      "response as string": { response: JSON.stringify(answer) },
      "choices[0].message.content": { choices: [{ message: { content: JSON.stringify(answer) } }] },
      "a bare string": JSON.stringify(answer),
      "a code fence": { response: "```json\n" + JSON.stringify(answer) + "\n```" },
      "text around the object": { response: "<|channel>thought\n<channel|>" + JSON.stringify(answer) + " ok" },
      "a JSON string holding the object": { response: JSON.stringify(JSON.stringify(answer)) },
    };
    for (const [name, reply] of Object.entries(shapes)) {
      it(name, async () => {
        const ai = fakeAI({ classify: [() => reply] });
        const result = await classifyComment("O sistema trava.", ai, { model: MODEL });
        assert.equal(result.status, "done");
        assert.equal(result.sentiment, "neu");
        assert.equal(ai.calls.length, 1);
      });
    }

    it("keeps an answer that parses as it is", () => {
      assert.equal(extractJson("[1]"), "[1]");
      assert.equal(extractJson('{"a": 1}'), '{"a": 1}');
      assert.equal(extractJson("nada"), "nada");
      assert.deepEqual(readAnswer({ response: null, usage: { prompt_tokens: -1 } }), { content: "", promptTokens: 0, outputTokens: 0 });
    });
  });

  it("retries once with the error hint, then succeeds", async () => {
    const ai = fakeAI({ classify: ["not json", stage1({ s: "neu" })] });
    const result = await classifyComment("O sistema é lento.", ai, { model: MODEL, verifier: false });
    assert.equal(result.status, "done");
    assert.deepEqual(result.stages.map((s) => [s.name, s.ok]), [["lexicon", true], ["classify", false], ["classify_retry", true]]);
    assert.match(result.stages[1].error, /^invalid JSON/);
    assert.deepEqual(result.stages[1].output, { raw: "not json" });
    const retry = ai.calls[1].input.messages[1].content;
    assert.ok(retry.startsWith('Comentário: """O sistema é lento."""'));
    assert.ok(retry.includes("Sua resposta anterior foi recusada: a resposta não era um JSON válido."));
  });

  it('treats "JSON Mode couldn\'t be met" as an invalid answer', async () => {
    const failure = new Error("AiError: JSON Mode couldn't be met (5021)");
    const ai = fakeAI({ classify: [failure, stage1({ t: ["lid"], s: "pos" })] });
    const result = await classifyComment("Gosto do meu líder.", ai, { model: MODEL, now: clock(3) });
    assert.equal(result.status, "done");
    assert.deepEqual(result.stages[1], {
      name: "classify",
      prompt_version: PROMPTS.classify.prompt_version,
      ok: false,
      error: "JSON Mode couldn't be met",
      output: null,
      prompt_tokens: 0,
      output_tokens: 0,
      total_ms: 3,
    });
    assert.ok(ai.calls[1].input.messages[1].content.includes("a resposta não era um JSON válido"));
  });

  it("gives review after two failures, and the lexicon still flags the case", async () => {
    const jsonMode = new Error("JSON Mode couldn't be met");
    const ai = fakeAI({ classify: [jsonMode, jsonMode], verify: [verdict()] });
    const result = await classifyComment("Meu supervisor me assediou no depósito.", ai, { model: MODEL });
    assert.equal(result.status, "review");
    assert.deepEqual([result.themes, result.sentiment], [[], null]);
    assert.deepEqual([result.critical, result.critical_types, result.critical_sources], [true, ["am"], ["lexicon"]]);
    assert.equal(result.evidence, "assediou");
    assert.deepEqual(ai.tasks(), ["classify", "classify", "verify_critical"]);
  });

  it("runs the verifier when the gate opens and records its version", async () => {
    const text = "Meu líder vive dizendo que qualquer um faz meu trabalho e ri de mim.";
    const ai = fakeAI({
      classify: [stage1({ t: ["lid"], s: "neg" })],
      verify: [{ response: { e: "qualquer um faz meu trabalho", c: true, k: ["am"] } }],
    });
    const result = await classifyComment(text, ai, { model: MODEL });
    assert.deepEqual(ai.tasks(), ["classify", "verify_critical"]);
    assert.equal(ai.calls[1].input.response_format.json_schema.name, "verificacao");
    assert.deepEqual([result.critical, result.critical_types, result.critical_sources], [true, ["am"], ["verifier"]]);
    assert.equal(result.evidence, "qualquer um faz meu trabalho");
    assert.equal(result.verifier_version, PROMPTS.verify.prompt_version);
    assert.equal(result.stages.at(-1).name, "verify");
  });

  it("gives review when the verifier fails twice", async () => {
    const ai = fakeAI({ classify: [stage1({ t: ["lid"], c: true, k: ["am"], e: "gritou comigo" })], verify: ["{}", "{}"] });
    const result = await classifyComment("O gestor gritou comigo.", ai, { model: MODEL });
    assert.equal(result.status, "review");
    assert.equal(result.critical, true);
    assert.deepEqual(result.stages.map((s) => s.name), ["lexicon", "classify", "verify", "verify_retry"]);
  });

  it("skips the verifier when turned off", async () => {
    const ai = fakeAI({ classify: [stage1({ t: ["lid"] })] });
    const result = await classifyComment("Meu líder me humilha.", ai, { model: MODEL, verifier: false });
    assert.deepEqual(ai.tasks(), ["classify"]);
    assert.equal(result.verifier_version, null);
    assert.equal(result.critical, false);
  });

  for (const text of ["", "   \n\t ", "  ", null, undefined]) {
    it(`skips ${JSON.stringify(text)} without calling the model`, async () => {
      const ai = fakeAI();
      const result = await classifyComment(text, ai, { model: MODEL });
      assert.deepEqual(result, {
        status: "skipped", themes: [], sentiment: null, critical: false, critical_types: [], critical_sources: [],
        evidence: null, stages: [], model: null, prompt_version: null, verifier_version: null,
      });
      assert.equal(ai.calls.length, 0);
    });
  }

  it("needs a model id", async () => {
    await assert.rejects(classifyComment("texto", fakeAI(), {}), TypeError);
  });
});

describe("Workers AI errors", () => {
  const text = "O [PESSOA] me humilha todo dia.";
  const failures = {
    "3036 in the message": [new Error("AiError: 3036: Account limited"), QuotaExceeded, 3036],
    "3036 as a code": [Object.assign(new Error("Account limited"), { code: 3036 }), QuotaExceeded, 3036],
    "3036 in errors[0]": [{ success: false, errors: [{ code: 3036, message: "Account limited" }] }, QuotaExceeded, 3036],
    "429 with 3036": [new Error("HTTP 429: {\"errors\":[{\"code\":3036}]}"), QuotaExceeded, 3036],
    "3040 capacity": [new Error("InferenceUpstreamError: 3040: Out of capacity"), CapacityError, 3040],
    "5028 deprecated": [new Error("5028: Model deprecated"), WorkersAIError, 5028],
    "no code": [new TypeError("Network connection lost"), WorkersAIError, null],
  };
  for (const [name, [failure, type, code]] of Object.entries(failures)) {
    it(name, async () => {
      const reply = failure instanceof Error ? failure : () => failure;
      const ai = fakeAI({ classify: [reply] });
      await assert.rejects(classifyComment(text, ai, { model: MODEL }), (err) => {
        assert.ok(err instanceof type && err instanceof WorkersAIError);
        assert.equal(err.code, code);
        assert.ok(!err.message.includes("humilha"));
        return true;
      });
    });
  }

  it("stops at quota in the verifier too", async () => {
    const ai = fakeAI({ classify: [stage1({ t: ["lid"], c: true, k: ["am"] })], verify: [new Error("3036")] });
    await assert.rejects(classifyComment(text, ai, { model: MODEL }), QuotaExceeded);
  });

  it("finds codes where Workers AI puts them", () => {
    assert.equal(errorCode({ code: "3036" }), 3036);
    assert.equal(errorCode({ cause: { code: 3040 } }), 3040);
    assert.equal(errorCode(new Error("timeout after 30 s")), null);
    assert.equal(errorCode("5028"), 5028);
  });
});
