// Validation, anonymization and the duplicate key of a site submission: port of the site rules
// of src/enps/queue/ingest.py (validate_site, anonymize_comment, normalize_comment, dedupe_key),
// with the same field names and error messages.

import { anonymize } from "./anonymize/index.js";
import { SPACE_CLASS, WORD_CLASS, strip } from "./classify/text.js";
import { sha256Hex } from "./ids.js";

export const LANGS = ["pt", "en"];
export const MIN_DEDUPE_CHARS = 15;

const PUNCTUATION = new RegExp(`[^${WORD_CLASS}${SPACE_CLASS}]|_`, "gu");
const SPACES = new RegExp(`[${SPACE_CLASS}]+`, "gu");

/** Field errors of a submission that was not stored ({field: message}). */
export class InvalidSubmission extends Error {
  constructor(fields) {
    super("invalid submission");
    this.name = "InvalidSubmission";
    this.fields = fields;
  }
}

const codePoints = (text) => [...text].length;

function parseScore(value) {
  if (typeof value !== "number" || !Number.isInteger(value) || value < 0 || value > 10) {
    throw new RangeError("must be an integer from 0 to 10");
  }
  return value;
}

function parseChoice(value, codes) {
  if (typeof value === "string" && codes.includes(value)) return value;
  throw new RangeError("unknown value");
}

function parseComment(value, maxChars) {
  if (value === null || value === undefined) return "";
  if (typeof value !== "string") throw new RangeError("must be text");
  const text = strip(value.normalize("NFC"));
  if (codePoints(text) > maxChars) throw new RangeError(`must have at most ${maxChars} characters`);
  return text;
}

function parseLang(value) {
  if (value === null || value === undefined) return "pt";
  if (!LANGS.includes(value)) throw new RangeError("must be pt or en");
  return value;
}

function parseConsent(value) {
  if (value !== true) throw new RangeError("must be true");
  return true;
}

function collect(errors, name, parse) {
  try {
    return parse();
  } catch (err) {
    if (!(err instanceof RangeError)) throw err;
    errors[name] = err.message;
    return null;
  }
}

/**
 * Site form: {score, area, tenure, comment, lang, consent} with codes only. Returns
 * {score, area, tenure, comment, lang}; throws InvalidSubmission with every field error.
 */
export function validateSite(payload, settings) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw new InvalidSubmission({ body: "must be a JSON object" });
  }
  const areas = settings.survey.areas.map((a) => a.code);
  const tenures = settings.survey.tenure_bands;
  const maxChars = settings.privacy.max_comment_chars;
  const errors = {};
  const values = {
    score: collect(errors, "score", () => parseScore(payload.score)),
    area: collect(errors, "area", () => parseChoice(payload.area, areas)),
    tenure: collect(errors, "tenure", () => parseChoice(payload.tenure, tenures)),
    comment: collect(errors, "comment", () => parseComment(payload.comment, maxChars)),
    lang: collect(errors, "lang", () => parseLang(payload.lang)),
  };
  collect(errors, "consent", () => parseConsent(payload.consent));
  if (Object.keys(errors).length) throw new InvalidSubmission(errors);
  return values;
}

/** Counts by type with sorted keys, like json.dumps(..., sort_keys=True). */
export function sortedCounts(counts) {
  return Object.fromEntries(Object.entries(counts).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)));
}

/** {text, counts, version} of the anonymized comment; an empty comment skips the anonymizer. */
export function anonymizeComment(comment) {
  if (!comment) return { text: "", counts: {}, version: "" };
  const result = anonymize(comment);
  return { text: strip(result.text), counts: sortedCounts(result.counts), version: result.version };
}

/** Lowercase, accents kept, punctuation removed, spaces collapsed. */
export function normalizeComment(text) {
  return strip(text.toLowerCase().replace(PUNCTUATION, " ").replace(SPACES, " "));
}

/** sha256 of the normalized anonymized comment; null for short comments. */
export async function dedupeKey(commentAnon) {
  const normalized = normalizeComment(commentAnon);
  if (codePoints(normalized) < MIN_DEDUPE_CHARS) return null;
  return sha256Hex(normalized);
}
