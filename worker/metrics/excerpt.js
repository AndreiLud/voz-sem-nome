// Public excerpts of anonymized comments (port of src/enps/metrics/excerpt.py): whitespace
// collapsed, profanity masked, length capped.
//
// Lengths and cuts count Unicode code points, like Python strings, so an emoji counts once.
// The whitespace class is Python's str.isspace() set (data.js) and the word class below is
// Python's Unicode \w, which tests/test_dashboard_cases.py checks against the running Python.

import { ELLIPSIS, MASK, PROFANITY, WHITESPACE } from "./data.js";

const WORD = "[\\p{L}\\p{N}_]";
const SPACES = new RegExp(`[${WHITESPACE}]+`, "u");
const SYNTAX = /[\\^$.*+?()[\]{}|/]/g;
const STRIP_END = new Set([" ", ",", ".", ";", ":", "!", "?"]);

function foldChar(ch) {
  const base = String.fromCodePoint(ch.normalize("NFD").codePointAt(0));
  const lower = base.toLowerCase();
  return [...lower].length === 1 ? lower : base;
}

/** Lowercase without accents, exactly one output code point per input code point. */
export function fold(text) {
  let out = "";
  for (const ch of text) out += foldChar(ch);
  return out;
}

/**
 * Regex of whole listed words in folded text. `words` are already folded and ordered like the
 * Python alternation (longest pattern first, so "foda-se" wins over "foda").
 */
function profanityPattern(words = PROFANITY) {
  if (words.length === 0) return null;
  const alternatives = words.map((w) => w.replace(SYNTAX, "\\$&").replaceAll(" ", `[${WHITESPACE}]+`));
  return new RegExp(`(?<!${WORD})(?:${alternatives.join("|")})(?!${WORD})`, "gu");
}

const DEFAULT_PATTERN = profanityPattern();

/** Replace listed words (whole words, accent and case insensitive) with [ofensivo]. */
export function maskProfanity(text, pattern = DEFAULT_PATTERN) {
  if (pattern === null || !text) return text;
  // Fold code point by code point and remember where each folded code point came from, so
  // matches in the folded text map back to the original even if a fold changes UTF-16 length.
  let folded = "";
  const origin = [];
  let at = 0;
  for (const ch of text) {
    const f = foldChar(ch);
    for (let i = 0; i < f.length; i += 1) origin.push(at);
    folded += f;
    at += ch.length;
  }
  origin.push(at);
  let out = "";
  let last = 0;
  for (const match of folded.matchAll(pattern)) {
    const start = origin[match.index];
    out += text.slice(last, start) + MASK;
    last = origin[match.index + match[0].length];
  }
  return out + text.slice(last);
}

/** Cut at a word boundary when possible and end with an ellipsis (limit in code points). */
export function truncate(text, limit) {
  const chars = [...text];
  if (chars.length <= limit) return text;
  let cut = chars.slice(0, limit - 1);
  const space = cut.lastIndexOf(" ");
  if (space >= Math.floor(limit / 2)) cut = cut.slice(0, space);
  let end = cut.length;
  while (end > 0 && STRIP_END.has(cut[end - 1])) end -= 1;
  return cut.slice(0, end).join("") + ELLIPSIS;
}

/** Python's " ".join(text.split()). */
function collapseWhitespace(text) {
  return text.split(SPACES).filter(Boolean).join(" ");
}

export function publicExcerpt(text, limit) {
  return truncate(maskProfanity(collapseWhitespace(text ?? "")), limit);
}
