// Two-stage classification of one anonymized comment with Workers AI: port of
// src/enps/classify/pipeline.py.
//
// 1. Lexicon: regex hits for explicit (strong) and indirect (weak) risk signals.
// 2. Stage 1 ("classify"): themes, sentiment and a first critical flag from the model.
// 3. Verifier ("verify"): a second reading focused on critical cases, run only when the gate
//    holds (stage 1 failed or said critical, any lexicon hit, or a negative comment on a
//    sensitive theme).
//
// Final decision, recall first: critical = stage 1 OR verifier OR a strong lexicon hit, except
// that a strong word is ignored when both models read the comment and said it is not a case.
// An invalid answer (or "JSON Mode couldn't be met") is retried once with the error message; a
// second failure sets the status to "review", and the lexicon still decides on its own.
//
//   const result = await classifyComment(textAnon, env.AI, { model: "@cf/google/gemma-4-26b-a4b-it" });
//
// Workers AI errors 3036 (daily free allocation used) and 3040 (out of capacity) throw
// QuotaExceeded and CapacityError; any other failure of ai.run throws WorkersAIError with the
// numeric code when there is one. None of them carries comment text.

import { GATE_THEMES, LLM, PROMPTS, SCHEMAS, TAXONOMY } from "./data.js";
import { LEXICON_VERSION, STRONG, hitAsDict, matchLexicon } from "./lexicon.js";
import { userMessage } from "./prompts.js";
import { OutputError, outputDict, validateClassify, validateVerify } from "./schema.js";
import { strip } from "./text.js";

export const SOURCE_LLM = "llm";
export const SOURCE_VERIFIER = "verifier";
export const SOURCE_LEXICON = "lexicon";
export const QUOTA_CODE = 3036;
export const CAPACITY_CODE = 3040;

const GATE = new Set(GATE_THEMES);
const CRITICAL_ORDER = new Map(TAXONOMY.critical_types.map((c, i) => [c.code, i]));
const JSON_MODE_FAILED = /json mode couldn.?t be met/i;
const JSON_MODE_ERROR = "JSON Mode couldn't be met";
const JSON_MODE_HINT = "a resposta não era um JSON válido";
const ERROR_CODE = /\b([35]\d{3})\b/;
const FENCED = /```(?:json)?\s*([\s\S]*?)```/i;

export const STAGES = Object.freeze({
  classify: { name: "classify", prompt: PROMPTS.classify, schema: SCHEMAS.classify, schemaName: "classificacao", validate: validateClassify },
  verify: { name: "verify", prompt: PROMPTS.verify, schema: SCHEMAS.verify, schemaName: "verificacao", validate: validateVerify },
});

// ------------------------------------------------------------------ errors

/** A Workers AI call failed. `code` is the Workers AI error code when one was found. */
export class WorkersAIError extends Error {
  constructor(code, cause) {
    super(code ? `Workers AI error ${code}` : "Workers AI error", { cause });
    this.name = "WorkersAIError";
    this.code = code;
  }
}

/** Error 3036: the daily free allocation of neurons is used up. Retry after 00:00 UTC. */
export class QuotaExceeded extends WorkersAIError {
  constructor(cause) {
    super(QUOTA_CODE, cause);
    this.name = "QuotaExceeded";
  }
}

/** Error 3040: Workers AI is out of capacity. Retry later. */
export class CapacityError extends WorkersAIError {
  constructor(cause) {
    super(CAPACITY_CODE, cause);
    this.name = "CapacityError";
  }
}

const messageOf = (err) => String(err?.message ?? err ?? "");

/** The Workers AI error code of a failure: from the error object, else from its message. */
export function errorCode(err) {
  for (const value of [err?.code, err?.errors?.[0]?.code, err?.cause?.code]) {
    const code = typeof value === "string" && /^\d+$/.test(value) ? Number(value) : value;
    if (Number.isInteger(code) && code >= 1000 && code <= 9999) return code;
  }
  const match = ERROR_CODE.exec(messageOf(err)) ?? ERROR_CODE.exec(messageOf(err?.errors?.[0]));
  return match ? Number(match[1]) : null;
}

function typedError(err) {
  const code = errorCode(err);
  if (code === QUOTA_CODE) return new QuotaExceeded(err);
  if (code === CAPACITY_CODE) return new CapacityError(err);
  return new WorkersAIError(code, err);
}

const isJsonModeFailure = (err) =>
  JSON_MODE_FAILED.test(messageOf(err)) || JSON_MODE_FAILED.test(messageOf(err?.errors?.[0]));

// ------------------------------------------------------------------ gate and decision

/** Gate for the verifier. A failed stage 1 (null) always opens it; an excerpt alone does not. */
export function needsVerification(stage1, hits) {
  if (stage1 == null || stage1.critical || hits.length) return true;
  return stage1.sentiment === "neg" && stage1.themes.some((t) => GATE.has(t));
}

/** Union of the sources that said critical. Weak hits add their type only when a model agrees. */
export function decide(stage1, verdict, hits) {
  const said = new Map();
  for (const [name, out] of [[SOURCE_LLM, stage1], [SOURCE_VERIFIER, verdict]]) {
    if (out != null && out.critical) said.set(name, out);
  }
  // A strong word counts on its own unless both models read the comment and said it is not a
  // case. A failed stage 1 never vetoes the lexicon.
  const rejected = stage1 != null && !stage1.critical && verdict != null && !verdict.critical;
  const strong = rejected ? [] : hits.filter((h) => h.strength === STRONG);
  const sources = [...said.keys(), ...(strong.length ? [SOURCE_LEXICON] : [])];
  if (!sources.length) return { critical: false, types: [], sources: [], evidence: null };

  const found = [...said.values()].flatMap((out) => out.types);
  found.push(...(said.size ? hits : strong).map((h) => h.type));
  const rank = (code) => CRITICAL_ORDER.get(code) ?? CRITICAL_ORDER.size;
  const types = [...new Set(found)].sort((a, b) => rank(a) - rank(b));

  const excerpts = [SOURCE_VERIFIER, SOURCE_LLM].filter((n) => said.has(n)).map((n) => said.get(n).excerpt);
  const weakLast = [...hits].sort((a, b) => (a.strength !== STRONG) - (b.strength !== STRONG));
  excerpts.push(...weakLast.map((h) => h.text));
  return { critical: true, types, sources, evidence: excerpts.find((e) => e) ?? null };
}

// ------------------------------------------------------------------ model calls

/** The env.AI.run() input of one attempt. */
export function buildRequest(stage, text, hint = null) {
  return {
    messages: [
      { role: "system", content: stage.prompt.system },
      { role: "user", content: userMessage(text, hint) },
    ],
    max_tokens: LLM.max_tokens,
    temperature: LLM.temperature,
    seed: LLM.seed,
    response_format: {
      type: "json_schema",
      json_schema: { name: stage.schemaName, schema: stage.schema, strict: true },
    },
    chat_template_kwargs: { enable_thinking: false },
  };
}

const tokens = (value) => (Number.isFinite(value) && value >= 0 ? Math.round(value) : 0);

/** Answer text and token counts of an env.AI.run() result, whatever its shape. */
export function readAnswer(result) {
  let value = result;
  if (result !== null && typeof result === "object") {
    value = result.response ?? result.choices?.[0]?.message?.content ?? null;
  }
  const content = typeof value === "string" ? value : value == null ? "" : JSON.stringify(value);
  const usage = result?.usage ?? {};
  return { content, promptTokens: tokens(usage.prompt_tokens), outputTokens: tokens(usage.completion_tokens) };
}

function parses(text) {
  try {
    return { value: JSON.parse(text) };
  } catch {
    return null;
  }
}

/**
 * The JSON text to validate. An answer that already parses goes through unchanged, so the
 * validation sees what the Python side would. Otherwise this recovers an object wrapped in a
 * code fence, in a JSON string, or between other text (such as an empty thought block).
 */
export function extractJson(content) {
  const whole = parses(content);
  if (whole) {
    const inner = typeof whole.value === "string" ? parses(whole.value) : null;
    return inner && inner.value !== null && typeof inner.value === "object" ? whole.value : content;
  }
  const fenced = FENCED.exec(content);
  if (fenced && parses(fenced[1])) return fenced[1];
  const start = content.indexOf("{");
  const end = content.lastIndexOf("}");
  if (start !== -1 && end > start && parses(content.slice(start, end + 1))) return content.slice(start, end + 1);
  return content;
}

/** One call. Returns the answer, or {failed} for "JSON Mode couldn't be met"; throws typed errors. */
async function callModel(ai, model, request, now) {
  const started = now();
  let result;
  try {
    result = await ai.run(model, request);
  } catch (err) {
    if (isJsonModeFailure(err)) return { failed: JSON_MODE_ERROR, totalMs: now() - started };
    throw typedError(err);
  }
  const totalMs = now() - started;
  if (result && typeof result === "object" && result.success === false) {
    if (isJsonModeFailure(result)) return { failed: JSON_MODE_ERROR, totalMs };
    throw typedError(result);
  }
  return { ...readAnswer(result), totalMs };
}

function stageRecord(name, stage, reply, { ok, error, output }) {
  return {
    name,
    prompt_version: stage.prompt.prompt_version,
    ok,
    error,
    output,
    prompt_tokens: reply.promptTokens ?? 0,
    output_tokens: reply.outputTokens ?? 0,
    total_ms: reply.totalMs,
  };
}

/** Call the model, validate, and retry once with the error message. Returns {output, records}. */
export async function runStage(ai, model, stage, text, now = Date.now) {
  const records = [];
  let hint = null;
  for (const attempt of [stage.name, `${stage.name}_retry`]) {
    const reply = await callModel(ai, model, buildRequest(stage, text, hint), now);
    if (reply.failed) {
      records.push(stageRecord(attempt, stage, reply, { ok: false, error: reply.failed, output: null }));
      hint = JSON_MODE_HINT;
      continue;
    }
    let output;
    try {
      output = stage.validate(extractJson(reply.content), text);
    } catch (err) {
      if (!(err instanceof OutputError)) throw err;
      records.push(stageRecord(attempt, stage, reply, { ok: false, error: err.message, output: { raw: reply.content } }));
      hint = err.hint;
      continue;
    }
    records.push(stageRecord(attempt, stage, reply, { ok: true, error: null, output: outputDict(output) }));
    return { output, records };
  }
  return { output: null, records };
}

// ------------------------------------------------------------------ entry point

function lexiconRecord(hits) {
  return {
    name: "lexicon",
    prompt_version: LEXICON_VERSION,
    ok: true,
    error: null,
    output: { hits: hits.map(hitAsDict) },
    prompt_tokens: 0,
    output_tokens: 0,
    total_ms: 0,
  };
}

function result(status, fields = {}) {
  return {
    status,
    themes: [],
    sentiment: null,
    critical: false,
    critical_types: [],
    critical_sources: [],
    evidence: null,
    stages: [],
    model: null,
    prompt_version: null,
    verifier_version: null,
    ...fields,
  };
}

/**
 * Classify one anonymized comment with `ai` (the Workers AI binding, or anything with the same
 * run(model, input) method). Empty text is skipped without calling the model.
 *
 * Options: model (required), verifier (defaults to llm.verifier_enabled of the settings),
 * now (clock in milliseconds, for tests).
 * Returns the shape of ClassificationResult: {status: "done" | "review" | "skipped", themes,
 * sentiment, critical, critical_types, critical_sources, evidence, stages, model,
 * prompt_version, verifier_version}.
 */
export async function classifyComment(textAnon, ai, { model, verifier = null, now = Date.now } = {}) {
  if (typeof textAnon !== "string" || !strip(textAnon)) return result("skipped");
  if (!model) throw new TypeError("classifyComment needs options.model");
  const useVerifier = verifier ?? LLM.verifier_enabled;

  const hits = matchLexicon(textAnon);
  const stages = [lexiconRecord(hits)];
  const first = await runStage(ai, model, STAGES.classify, textAnon, now);
  stages.push(...first.records);

  let verdict = null;
  let verifierRan = false;
  if (useVerifier && needsVerification(first.output, hits)) {
    const second = await runStage(ai, model, STAGES.verify, textAnon, now);
    stages.push(...second.records);
    verdict = second.output;
    verifierRan = true;
  }

  const decision = decide(first.output, verdict, hits);
  const stage1 = first.output;
  return result(stage1 === null || (verifierRan && verdict === null) ? "review" : "done", {
    themes: stage1 ? [...stage1.themes] : [],
    sentiment: stage1 ? stage1.sentiment : null,
    critical: decision.critical,
    critical_types: decision.types,
    critical_sources: decision.sources,
    evidence: decision.evidence,
    stages,
    model,
    prompt_version: PROMPTS.classify.prompt_version,
    verifier_version: verifierRan ? PROMPTS.verify.prompt_version : null,
  });
}

/** Sums over the stages, for the ai_ms, prompt_tokens and output_tokens columns. */
export function stageTotals(stages) {
  return stages.reduce(
    (sum, s) => ({
      total_ms: sum.total_ms + s.total_ms,
      prompt_tokens: sum.prompt_tokens + s.prompt_tokens,
      output_tokens: sum.output_tokens + s.output_tokens,
    }),
    { total_ms: 0, prompt_tokens: 0, output_tokens: 0 },
  );
}
