// Validation and coercion of the model answers: port of src/enps/classify/schema.py.
//
// The Python side validates with pydantic in lax mode; this module follows the same rules and
// writes the same messages for the cases a model can produce: unknown or missing fields, wrong
// types, codes outside the taxonomy, list sizes, and booleans written as 0/1 or "true"/"no".
// On a broken answer it throws OutputError, whose `hint` (Portuguese) goes back to the model on
// the retry. The field rules (enums, list sizes, defaults) are read from data.js.

import { EXCERPT_MAX_CHARS, OUT_THEME, SCHEMAS, SCHEMA_DEFAULTS } from "./data.js";
import {
  SPACE_CLASS, WORD_CLASS, codePointAt, codePointBefore, compareCodePoints, escapeRegExp, isAlnum,
  removePrefix, removeSuffix, split, strip,
} from "./text.js";

const QUOTES = "\"'“”‘’«»";
const ELLIPSES = ["...", "…"];
const TRAILING = /[ ,;:]+$/u;
const FALSE_WORDS = new Set(["0", "off", "f", "false", "n", "no"]);
const TRUE_WORDS = new Set(["1", "on", "t", "true", "y", "yes"]);
const I64_LIMIT = 2 ** 63;
// Python's IGNORECASE treats i, I, İ and ı as one letter; the "iu" flags only pair i with I.
// These four are the only characters whose case-insensitive matches differ between the two.
const TURKISH_I = /[iIİı]/gu;
const ANY_I = "[iİı]";

/** The model answer broke a rule. `hint` is the Portuguese message sent back on retry. */
export class OutputError extends Error {
  constructor(message, hint) {
    super(message);
    this.name = "OutputError";
    this.hint = hint;
  }
}

// ------------------------------------------------------------------ field validation

class FieldError {
  constructor(loc, message) {
    this.loc = loc;
    this.message = message;
  }
}

const items = (n) => `${n} item${n === 1 ? "" : "s"}`;

/** pydantic's literal_error text: "Input should be 'a', 'b' or 'c'". */
function literalMessage(values) {
  const quoted = values.map((v) => `'${v}'`);
  const list = quoted.length === 1 ? quoted[0] : `${quoted.slice(0, -1).join(", ")} or ${quoted.at(-1)}`;
  return `Input should be ${list}`;
}

function checkLiteral(value, values, loc, errors) {
  if (values.includes(value)) return value;
  errors.push(new FieldError(loc, literalMessage(values)));
  return undefined;
}

function checkString(value, loc, errors) {
  if (typeof value === "string") return value;
  errors.push(new FieldError(loc, "Input should be a valid string"));
  return undefined;
}

/** pydantic lax bool: true/false, 0/1 (also 0.0/1.0) and a few words, ASCII case-insensitive. */
function checkBool(value, loc, errors) {
  if (typeof value === "boolean") return value;
  const parsing = "Input should be a valid boolean, unable to interpret input";
  let message = "Input should be a valid boolean";
  if (typeof value === "number" && Number.isInteger(value) && Math.abs(value) < I64_LIMIT) {
    if (value === 0 || value === 1) return value === 1;
    message = parsing;
  } else if (typeof value === "string") {
    const word = value.replace(/[A-Z]/g, (c) => c.toLowerCase());
    if (FALSE_WORDS.has(word)) return false;
    if (TRUE_WORDS.has(word)) return true;
    message = parsing;
  }
  errors.push(new FieldError(loc, message));
  return undefined;
}

/** A list of codes: length limits are checked before the items, like pydantic. */
function checkList(value, spec, loc, errors) {
  if (!Array.isArray(value)) {
    errors.push(new FieldError(loc, "Input should be a valid list"));
    return undefined;
  }
  if (spec.maxItems !== undefined && value.length > spec.maxItems) {
    errors.push(new FieldError(loc, `List should have at most ${items(spec.maxItems)} after validation, not ${value.length}`));
    return undefined;
  }
  const before = errors.length;
  const out = value.map((item, i) => checkLiteral(item, spec.items.enum, [...loc, i], errors));
  if (errors.length > before) return undefined;
  if (spec.minItems !== undefined && value.length < spec.minItems) {
    errors.push(new FieldError(loc, `List should have at least ${items(spec.minItems)} after validation, not ${value.length}`));
    return undefined;
  }
  return out;
}

function fieldChecker(name, spec) {
  if (spec.type === "array" && spec.items?.type === "string" && Array.isArray(spec.items.enum)) {
    return (value, errors) => checkList(value, spec, [name], errors);
  }
  if (spec.type === "string" && Array.isArray(spec.enum)) {
    return (value, errors) => checkLiteral(value, spec.enum, [name], errors);
  }
  if (spec.type === "string") return (value, errors) => checkString(value, [name], errors);
  if (spec.type === "boolean") return (value, errors) => checkBool(value, [name], errors);
  throw new Error(`schema field ${name} has a shape the Worker does not validate`);
}

function compile(schema, defaults) {
  return {
    fields: Object.entries(schema.properties).map(([name, spec]) => ({ name, check: fieldChecker(name, spec) })),
    defaults,
    closed: schema.additionalProperties === false,
  };
}

const VALIDATORS = {
  classify: compile(SCHEMAS.classify, SCHEMA_DEFAULTS.classify),
  verify: compile(SCHEMAS.verify, SCHEMA_DEFAULTS.verify),
};

function schemaError(errors) {
  const problems = errors.map((e) => `${e.loc.join(".") || "objeto"}: ${e.message}`).join("; ");
  const names = [...new Set(errors.filter((e) => e.loc.length).map((e) => String(e.loc[0])))];
  const fields = names.sort(compareCodePoints).join(", ") || "objeto";
  return new OutputError(`schema: ${problems}`, `o JSON não seguiu o formato pedido no campo ${fields}`);
}

/** JSON text -> object with every field checked and defaults filled in, or OutputError. */
function parse(content, validator) {
  let raw;
  try {
    raw = JSON.parse(content);
  } catch (err) {
    throw new OutputError(`invalid JSON: ${err.message}`, "a resposta não era um JSON válido");
  }
  if (raw === null || typeof raw !== "object" || Array.isArray(raw)) {
    throw new OutputError("JSON is not an object", "a resposta precisa ser um objeto JSON");
  }
  const errors = [];
  const data = {};
  for (const { name, check } of validator.fields) {
    if (Object.hasOwn(raw, name)) data[name] = check(raw[name], errors);
    else if (Object.hasOwn(validator.defaults, name)) data[name] = structuredClone(validator.defaults[name]);
    else errors.push(new FieldError([name], "Field required"));
  }
  if (validator.closed) {
    for (const key of Object.keys(raw)) {
      if (!validator.fields.some((f) => f.name === key)) errors.push(new FieldError([key], "Extra inputs are not permitted"));
    }
  }
  if (errors.length) throw schemaError(errors);
  return data;
}

// ------------------------------------------------------------------ coercion

const unique = (values) => [...new Set(values)];

/** Locate the excerpt ignoring case and whitespace; a match that ends a word wins. */
function findInText(excerpt, text) {
  const pattern = split(excerpt)
    .map((word) => escapeRegExp(word).replace(TURKISH_I, ANY_I))
    .join(`[${SPACE_CLASS}]+`);
  return new RegExp(`${pattern}(?![${WORD_CLASS}])`, "iu").exec(text) ?? new RegExp(pattern, "iu").exec(text);
}

/** The excerpt as written in the comment, or "" when it was not copied from it. */
function cleanExcerpt(excerpt, text, notes) {
  let candidate = strip(strip(strip(excerpt), QUOTES));
  for (const mark of ELLIPSES) candidate = strip(removeSuffix(removePrefix(candidate, mark), mark));
  if (!candidate) return "";
  const match = findInText(candidate, text);
  if (match === null) {
    notes.push("excerpt not found in the comment, cleared");
    return "";
  }
  let found = match[0];
  const end = match.index + found.length;
  const next = codePointAt(text, end);
  if (next && isAlnum(next) && isAlnum(codePointBefore(text, end)) && found.includes(" ")) {
    found = found.slice(0, found.lastIndexOf(" ")); // the grammar length limit stopped it inside a word
    notes.push("excerpt ended inside a word, last word dropped");
  }
  const chars = Array.from(found);
  if (chars.length > EXCERPT_MAX_CHARS) {
    const cut = chars.slice(0, EXCERPT_MAX_CHARS + 1).join("");
    found = cut.includes(" ") ? cut.slice(0, cut.lastIndexOf(" ")) : chars.slice(0, EXCERPT_MAX_CHARS).join("");
    notes.push(`excerpt longer than ${EXCERPT_MAX_CHARS} characters, cut`);
  }
  return found.replace(TRAILING, "");
}

function checkCritical(critical, types, notes) {
  if (types.length && !critical) {
    notes.push("k was not empty with c=false: c set to true");
    return true;
  }
  if (critical && !types.length) {
    throw new OutputError("c is true but k is empty", "quando c=true, k precisa ter pelo menos um código de tipo crítico");
  }
  return critical;
}

/**
 * Parse and check a stage 1 answer against the comment `text`.
 * Returns {themes, sentiment, excerpt, critical, types, notes}; throws OutputError for a retry.
 */
export function validateClassify(content, text) {
  const data = parse(content, VALIDATORS.classify);
  const notes = [];
  let themes = unique(data.t);
  if (themes.includes(OUT_THEME) && themes.length > 1) {
    themes = themes.filter((t) => t !== OUT_THEME); // a retry at temperature 0 would repeat it
    notes.push('"out" mixed with other themes: "out" dropped');
  }
  const types = unique(data.k);
  const critical = checkCritical(data.c, types, notes);
  const excerpt = cleanExcerpt(data.e, text, notes);
  return { themes, sentiment: data.s, excerpt, critical, types, notes };
}

/** Parse and check a verifier answer. Returns {excerpt, critical, types, notes}. */
export function validateVerify(content, text) {
  const data = parse(content, VALIDATORS.verify);
  const notes = [];
  const types = unique(data.k);
  const critical = checkCritical(data.c, types, notes);
  const excerpt = cleanExcerpt(data.e, text, notes);
  return { excerpt, critical, types, notes };
}

/** The output as a stage record stores it (as_dict): {t, s, e, c, k, notes?} or {e, c, k, notes?}. */
export function outputDict(out) {
  const data = out.themes === undefined ? {} : { t: [...out.themes], s: out.sentiment };
  Object.assign(data, { e: out.excerpt, c: out.critical, k: [...out.types] });
  if (out.notes?.length) data.notes = [...out.notes];
  return data;
}
