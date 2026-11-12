// Anonymizer of the Worker: replaces personal data in a survey comment with typed placeholders
// such as [PESSOA] before the comment leaves the Worker. Port of src/enps/anonymize/ without the
// spaCy NER layer, which cannot run here; names come from the IBGE gazetteer, @mentions and a
// context rule (names.js), places from the official IBGE lists (places.js). No dependencies and
// no I/O: the word lists are in data.js.
//
//   anonymize("falei com o João, meu cpf é 123.456.789-00")
//   -> { text: "falei com o [PESSOA], meu cpf é [CPF]",
//        spans: [{ start: 12, end: 16, type: "PESSOA", source: "gazetteer" }, ...],
//        counts: { CPF: 1, PESSOA: 1 }, version: "edge-1.1.0" }
//
// Spans are in original text coordinates (JavaScript string indices), sorted and
// non-overlapping. Places inside a regex span are dropped (the address already covers them) and
// overlaps between names and places follow resolvePlaces. Regex spans win over name and place
// spans: a name span is cut where it overlaps one.
// Text with decomposed accents ("Joa\u0303o") is searched in its composed (NFC) form, and the
// spans are mapped back to the text as given.

import { lexicon as defaultLexicon, normalize } from "./lexicon.js";
import { findNames, isTitle } from "./names.js";
import { byStart, findAll, firstEndingAfter } from "./patterns.js";
import { findPlaces, resolvePlaces } from "./places.js";

export const ANONYMIZER_VERSION = "edge-1.1.0";

// Every placeholder the anonymizer can write (same list as src/enps/anonymize/__init__.py).
export const PII_TYPES = Object.freeze([
  "PESSOA", "LOCAL", "EMAIL", "TELEFONE", "CPF", "CNPJ", "RG",
  "CEP", "MATRICULA", "PLACA", "DOCUMENTO",
]);

const ALNUM = /[\p{L}\p{N}]/u;
const LETTER = /\p{L}/u;
const CLUSTER = /\P{M}\p{M}*|\p{M}+/gu; // a character and the combining marks after it

function strip(text, start, end) {
  while (start < end && !ALNUM.test(text[start])) start += 1;
  while (end > start && !ALNUM.test(text[end - 1])) end -= 1;
  return [start, end];
}

/** A leftover piece of a cut name span is kept when it still looks like a name. */
function worthKeeping(piece, lex) {
  const words = piece.split(/\s+/u).filter((w) => LETTER.test(w));
  return words.some((w) => isTitle(w) || lex.isKnownName(normalize(w)));
}

function subtract(span, blockers, text, lex) {
  let pieces = [[span.start, span.end]];
  for (const b of blockers) {
    const next = [];
    for (const [a, z] of pieces) {
      if (b.end <= a || b.start >= z) {
        next.push([a, z]);
        continue;
      }
      for (const p of [[a, b.start], [b.end, z]]) if (p[1] > p[0]) next.push(p);
    }
    pieces = next;
  }
  const kept = [];
  for (const piece of pieces) {
    const [a, z] = strip(text, ...piece);
    if (z - a >= 2 && worthKeeping(text.slice(a, z), lex)) {
      kept.push({ start: a, end: z, type: span.type, source: span.source });
    }
  }
  return kept;
}

/** Regex spans that overlap `span`; `regexSpans` is sorted and disjoint. */
function blockersOf(span, regexSpans) {
  const out = [];
  let k = firstEndingAfter(regexSpans, span.start);
  while (k < regexSpans.length && regexSpans[k].start < span.end) {
    out.push(regexSpans[k]);
    k += 1;
  }
  return out;
}

/** Regex spans (sorted, disjoint) as they are, plus name spans cut around them. Sorted, non-overlapping. */
export function mergeSpans(text, regexSpans, nameSpans, lex = defaultLexicon) {
  const merged = [...regexSpans];
  for (const span of nameSpans) {
    const blockers = blockersOf(span, regexSpans);
    merged.push(...(blockers.length ? subtract(span, blockers, text, lex) : [span]));
  }
  return merged.sort(byStart);
}

export function replaceSpans(text, spans) {
  const parts = [];
  let cursor = 0;
  for (const span of spans) {
    parts.push(text.slice(cursor, span.start), `[${span.type}]`);
    cursor = span.end;
  }
  parts.push(text.slice(cursor));
  return parts.join("");
}

function countTypes(spans) {
  const counts = {};
  for (const type of spans.map((s) => s.type).sort()) counts[type] = (counts[type] ?? 0) + 1;
  return counts;
}

/**
 * NFC form of `text` and the way back, or null when `text` is already in that form. Each
 * character is composed with its own combining marks, so every position of the composed text
 * falls on a position of the original: `starts` rounds down and `ends` rounds up inside a
 * character whose form changed.
 */
function composed(text) {
  if (text.normalize("NFC") === text) return null;
  const parts = [];
  const starts = [];
  const ends = [];
  for (const m of text.matchAll(CLUSTER)) {
    const piece = m[0].normalize("NFC");
    const same = piece === m[0];
    for (let k = 0; k < piece.length; k += 1) {
      starts.push(same ? m.index + k : m.index);
      ends.push(same || k === 0 ? m.index + k : m.index + m[0].length);
    }
    parts.push(piece);
  }
  starts.push(text.length);
  ends.push(text.length);
  return { text: parts.join(""), starts, ends };
}

/** Spans of the composed text in coordinates of the original, still sorted and disjoint. */
function toOriginal(spans, map) {
  const out = [];
  for (const span of spans) {
    const start = Math.max(map.starts[span.start], out.length ? out[out.length - 1].end : 0);
    const end = map.ends[span.end];
    if (end > start) out.push({ ...span, start, end });
  }
  return out;
}

/**
 * Replace personal data in `text` with typed placeholders such as [PESSOA].
 * @param {string} text
 * @returns {{text: string, spans: {start: number, end: number, type: string, source: string}[],
 *            counts: Object<string, number>, version: string}}
 */
export function anonymize(text) {
  const value = typeof text === "string" ? text : "";
  if (!value.trim()) return { text: value, spans: [], counts: {}, version: ANONYMIZER_VERSION };
  const map = composed(value);
  const work = map ? map.text : value;
  const regexSpans = findAll(work);
  const places = findPlaces(work).filter((p) => !blockersOf(p, regexSpans).length);
  const found = mergeSpans(work, regexSpans, resolvePlaces(findNames(work), places, work));
  const spans = map ? toOriginal(found, map) : found;
  return { text: replaceSpans(value, spans), spans, counts: countTypes(spans), version: ANONYMIZER_VERSION };
}

// V8 compiles a regular expression on its first run and again to machine code on its second,
// about 1 ms per pattern with Unicode classes. Left alone, that cost (20 ms or more) would fall on
// the first requests of a fresh isolate, against the 10 ms CPU limit of the Free plan. Two runs
// over a sample that reaches every detector move it to module load, which Workers count as startup.
const WARM_UP_TEXT = [
  "cpf 123.456.789-00, cnpj 11.222.333/0001-81 e 12ABC34501DE30, rg 12.345.678-9,",
  "matrícula 45821 e registro 100 horas, cep 01310-100 e 04567-000, ramal 4521,",
  "tel (11) 98765-4321 e 3456-7890, whats 987654321, placa ABC1234, carro abc 1234 e BRA2E19,",
  "Rua Augusta, 1200, rua das flores, 123, bairro Jardim Ângela, 98.765.432-1,",
  "protocolo 12345678 de R$ 12345678, a@b.com e @ana.",
  "número 11987654321 e 1134567890, +5511987654321 e 52998224725.",
  "O gerente Antunes e a Sra. Lúcia da Silva, falei com a Kowalski e o paulinho.",
  "Moro em São José dos Campos, sou de embu das artes e a Marília mora em Marília.",
].join("\n");
for (let run = 0; run < 2; run += 1) anonymize(WARM_UP_TEXT);
