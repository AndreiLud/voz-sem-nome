// Risk lexicon of the Worker: port of src/enps/classify/lexicon.py. Regex patterns catch explicit
// (strong) and indirect (weak) signs of critical cases on the anonymized text, before and
// independently of the model. Matching is accent- and case-insensitive: the text is folded
// first, like Python. The patterns come converted from config/risk_lexicon.yaml in data.js.
//
// Hit offsets (start, end) count code points, like Python string indices, so they equal the
// offsets of the Python lexicon; `text` is the matched text as written in the input.

import { LEXICON } from "./data.js";
import { WORD_CLASS, codePointIndex, isCombining } from "./text.js";

export const STRONG = "strong";
export const WEAK = "weak";
export const LEXICON_VERSION = LEXICON.version;

const RULES = LEXICON.rules.map((rule) => ({
  type: rule.type,
  strength: rule.strength,
  regex: new RegExp(rule.source, `${rule.flags}g`),
}));
const NEGATION_WORDS = new Set(LEXICON.negation.words);
const NEGATION_WINDOW = LEXICON.negation.window;
const CLAUSE_BREAK = /[.,;:!?\n]/;
const WORD = new RegExp(`[${WORD_CLASS}]+`, "gu");

/**
 * Lower-case and strip accents. Returns the folded text and, per folded code point, the index
 * of the code point of `text` it comes from.
 */
export function fold(text) {
  const chars = [];
  const index = [];
  let i = 0;
  for (const ch of text) {
    let base = "";
    for (const c of ch.normalize("NFKD")) if (!isCombining(c)) base += c;
    for (const c of base.toLowerCase()) {
      chars.push(c);
      index.push(i);
    }
    i += 1;
  }
  return { folded: chars.join(""), index };
}

/** A negation word among the last words of the clause before `start` (a string index). */
function negated(folded, start) {
  const clause = folded.slice(0, start).split(CLAUSE_BREAK).at(-1);
  const words = (clause.match(WORD) ?? []).slice(-NEGATION_WINDOW);
  return words.some((w) => NEGATION_WORDS.has(w));
}

/** Overlapping hits of the same type keep the strongest, then the longest, then the first. */
function dedupe(hits) {
  const rank = (h) => [h.strength !== STRONG ? 1 : 0, -(h.end - h.start), h.start];
  const byRank = (a, b) => {
    const [x, y] = [rank(a), rank(b)];
    return x[0] - y[0] || x[1] - y[1] || x[2] - y[2];
  };
  const kept = [];
  for (const hit of [...hits].sort(byRank)) {
    if (!kept.some((k) => k.type === hit.type && k.start < hit.end && hit.start < k.end)) kept.push(hit);
  }
  return kept.sort((a, b) => a.start - b.start || (a.type < b.type ? -1 : a.type > b.type ? 1 : 0));
}

/** All hits in `text`, sorted by position: [{type, strength, text, start, end, negated}]. */
export function matchLexicon(text) {
  if (!text) return [];
  const { folded, index } = fold(text);
  const original = Array.from(text);
  const toCodePoint = codePointIndex(folded);
  const candidates = [];
  for (const rule of RULES) {
    for (const m of folded.matchAll(rule.regex)) {
      if (m[0].length === 0) continue;
      const start = index[toCodePoint[m.index]];
      const end = index[toCodePoint[m.index + m[0].length] - 1] + 1;
      const isNegated = rule.strength === STRONG && negated(folded, m.index);
      candidates.push({
        type: rule.type,
        strength: isNegated ? WEAK : rule.strength,
        text: original.slice(start, end).join(""),
        start,
        end,
        negated: isNegated,
      });
    }
  }
  return dedupe(candidates);
}

/** The hit as the classification record stores it (LexiconHit.as_dict). */
export function hitAsDict(hit) {
  const data = { type: hit.type, strength: hit.strength, text: hit.text };
  if (hit.negated) data.negated = true;
  return data;
}

export const strongHits = (hits) => hits.filter((h) => h.strength === STRONG);
