// Python string semantics the classifier relies on, so the Worker answers like
// src/enps/classify/. Python's \w, \s, str.strip(), str.split() and str.isalnum() are
// Unicode-aware; the JavaScript built-ins are not, or use another whitespace list.

import { PY_COMBINING, PY_SPACE } from "./data.js";

/** Body of a character class equal to Python's \w (checked by the data build). */
export const WORD_CLASS = "\\p{L}\\p{N}_";
/** Body of a character class equal to Python's \s and str.isspace(). */
export const SPACE_CLASS = PY_SPACE;

const SPACE = new RegExp(`^[${SPACE_CLASS}]$`, "u");
const SPACE_RUN = new RegExp(`[${SPACE_CLASS}]+`, "u");
const ALNUM = /^[\p{L}\p{N}]$/u;
const COMBINING = new RegExp(`^[${PY_COMBINING}]$`, "u");

export const isSpace = (ch) => SPACE.test(ch);
export const isAlnum = (ch) => ALNUM.test(ch);
export const isCombining = (ch) => COMBINING.test(ch);

/**
 * Python str.strip(): without `chars`, strips Python whitespace; with `chars`, strips any of
 * those characters. Every character involved is in the BMP, so code units are enough.
 */
export function strip(text, chars = null) {
  const drop = chars === null ? isSpace : (ch) => chars.includes(ch);
  let start = 0;
  let end = text.length;
  while (start < end && drop(text[start])) start += 1;
  while (end > start && drop(text[end - 1])) end -= 1;
  return text.slice(start, end);
}

/** Python str.split() without arguments. */
export function split(text) {
  const stripped = strip(text);
  return stripped ? stripped.split(SPACE_RUN) : [];
}

/** Python str.removeprefix(). */
export const removePrefix = (text, prefix) => (prefix && text.startsWith(prefix) ? text.slice(prefix.length) : text);

/** Python str.removesuffix(). */
export const removeSuffix = (text, suffix) => (suffix && text.endsWith(suffix) ? text.slice(0, -suffix.length) : text);

/** The code point that starts at string index `i`, or "" at the end. */
export function codePointAt(text, i) {
  const cp = text.codePointAt(i);
  return cp === undefined ? "" : String.fromCodePoint(cp);
}

/** The code point that ends at string index `i`, or "" at the start. */
export function codePointBefore(text, i) {
  if (i <= 0) return "";
  const low = text.charCodeAt(i - 1);
  if (i >= 2 && low >= 0xdc00 && low <= 0xdfff) {
    const high = text.charCodeAt(i - 2);
    if (high >= 0xd800 && high <= 0xdbff) return text.slice(i - 2, i);
  }
  return text[i - 1];
}

/** Map from each string index of `text` (and its end) to the code point index Python would use. */
export function codePointIndex(text) {
  const map = new Array(text.length + 1);
  let cp = 0;
  for (let i = 0; i < text.length; cp += 1) {
    const width = text.codePointAt(i) > 0xffff ? 2 : 1;
    for (let k = 0; k < width; k += 1) map[i + k] = cp;
    i += width;
  }
  map[text.length] = cp;
  return map;
}

/** Order strings by code point, like Python's sorted(). */
export function compareCodePoints(a, b) {
  const x = Array.from(a);
  const y = Array.from(b);
  for (let i = 0; i < Math.min(x.length, y.length); i += 1) {
    const d = x[i].codePointAt(0) - y[i].codePointAt(0);
    if (d !== 0) return d;
  }
  return x.length - y.length;
}

/** Escape text for a regex with the "u" flag (which refuses escapes of other characters). */
export const escapeRegExp = (text) => text.replace(/[\\^$.*+?()[\]{}|/]/g, "\\$&");
