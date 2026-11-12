// POST /api/v1/internal/evaluate: the edge path (anonymize, classify) on up to 10 comments,
// for src/enps/evaluation/edge_client.py. Nothing is stored and nothing is logged.
//
// The route exists only when the secret EVAL_TOKEN is set (404 otherwise) and the request
// carries it in X-Eval-Token. Body: {items: [{id, comment}], verifier?: bool}. Answer:
// {model, anonymizer_version, items: [{id, comment_anon, pii_counts, result, anonymize_ms,
// total_ms}]}. When Workers AI runs out of quota (3036) or capacity (3040) the answer is 503
// {"error": "quota" | "capacity", model, anonymizer_version, items} with the items finished so
// far, so none is paid twice.
//
// Times come from Date.now(), which in Workers only moves forward across I/O: total_ms holds
// the model calls, while the anonymizer's own CPU time reads as 0 there.

import { ANONYMIZER_VERSION } from "./anonymize/index.js";
import { CapacityError, QuotaExceeded, classifyComment, errorCode, stageTotals } from "./classify/pipeline.js";
import { nextUtcMidnight, secondsUntil } from "./clock.js";
import { error, json, notFound, readJsonObject } from "./http.js";
import { sha256Hex } from "./ids.js";
import { modelOf } from "./processing.js";
import { anonymizeComment } from "./validation.js";
import { strip } from "./classify/text.js";

export const MAX_ITEMS = 10;
export const MAX_BODY_BYTES = 64 * 1024;
const MAX_ID_CHARS = 64;

/** Constant-time comparison of two secrets (through their digests, so lengths do not leak). */
export async function secretMatches(given, expected) {
  if (typeof given !== "string" || !given) return false;
  const [a, b] = await Promise.all([sha256Hex(given), sha256Hex(expected)]);
  let diff = 0;
  for (let i = 0; i < a.length; i += 1) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

/** {items, verifier} or {fields} with the errors, like a 422 of the APIs. */
export function parseEvaluateBody(body, settings) {
  const fields = {};
  const { items, verifier = null } = body;
  if (verifier !== null && typeof verifier !== "boolean") fields.verifier = "must be true, false or null";
  if (!Array.isArray(items) || items.length < 1 || items.length > MAX_ITEMS) {
    fields.items = `must be a list of 1 to ${MAX_ITEMS} items`;
    return { fields };
  }
  const maxChars = settings.privacy.max_comment_chars;
  const parsed = items.map((item, i) => {
    const id = typeof item?.id === "number" && Number.isInteger(item.id) ? String(item.id) : item?.id;
    if (typeof id !== "string" || !id || id.length > MAX_ID_CHARS) fields[`items.${i}.id`] = "must be a short text";
    const comment = item?.comment ?? "";
    if (typeof comment !== "string") fields[`items.${i}.comment`] = "must be text";
    else if ([...comment].length > maxChars) fields[`items.${i}.comment`] = `must have at most ${maxChars} characters`;
    return { id, comment: typeof comment === "string" ? strip(comment.normalize("NFC")) : "" };
  });
  return Object.keys(fields).length ? { fields } : { items: parsed, verifier };
}

function resultBody(result) {
  const totals = stageTotals(result.stages);
  return {
    status: result.status,
    themes: result.themes,
    sentiment: result.sentiment,
    critical: result.critical,
    critical_types: result.critical_types,
    critical_sources: result.critical_sources,
    evidence: result.evidence,
    model: result.model,
    prompt_version: result.prompt_version,
    verifier_version: result.verifier_version,
    stages: result.stages,
    ai_ms: totals.total_ms,
    prompt_tokens: totals.prompt_tokens,
    output_tokens: totals.output_tokens,
  };
}

export async function evaluate(request, env, deps) {
  if (!env.EVAL_TOKEN) return notFound();
  if (!(await secretMatches(request.headers.get("X-Eval-Token"), env.EVAL_TOKEN))) return error(403, "forbidden");
  const body = await readJsonObject(request, MAX_BODY_BYTES);
  if (body.response) return body.response;
  const parsed = parseEvaluateBody(body.value, deps.settings);
  if (parsed.fields) return error(422, "validation", { fields: parsed.fields });

  const model = modelOf(env);
  const done = [];
  for (const item of parsed.items) {
    const started = deps.now();
    const anon = anonymizeComment(item.comment);
    const anonymized = deps.now();
    const record = { id: item.id, comment_anon: anon.text, pii_counts: anon.counts, anonymize_ms: anonymized - started };
    try {
      const result = await classifyComment(anon.text, env.AI, { model, verifier: parsed.verifier, now: deps.now });
      record.result = resultBody(result);
    } catch (err) {
      if (err instanceof QuotaExceeded) {
        const retry = String(secondsUntil(nextUtcMidnight(deps.now()), deps.now()));
        return error(503, "quota", { model, anonymizer_version: ANONYMIZER_VERSION, items: done, headers: { "Retry-After": retry } });
      }
      if (err instanceof CapacityError) {
        return error(503, "capacity", { model, anonymizer_version: ANONYMIZER_VERSION, items: done, headers: { "Retry-After": "60" } });
      }
      record.result = { status: "failed", error_code: errorCode(err) };
    }
    record.total_ms = deps.now() - started;
    done.push(record);
  }
  return json(200, { model, anonymizer_version: ANONYMIZER_VERSION, items: done });
}
