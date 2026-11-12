// Place gazetteer: official IBGE municipalities, districts and subdistricts masked as LOCAL.
// Port of src/enps/anonymize/places.py; the lists are in data.js.
//
// * A place name of several words must match fully ("São José dos Campos", "Embu das Artes"),
//   without regard to accents; the longest name that starts at a word wins.
// * Capitalized names match anywhere: every word but connectors ("da", "de", "dos"...) starts
//   with a capital letter. A one-word name that is also a common word ("Saúde", "Contagem")
//   matches only right after a cue, and one used about as often in both cases ("Perus") only
//   inside a sentence. A name made only of common words ("Campo Grande") does not match at the
//   start of a sentence either.
// * After a place cue ("moro em", "sou de", "filial de", "em"...), a name also matches in
//   lowercase or all caps ("moro em pirituba"), unless it is made only of common words
//   ("vim de carro"), or is one word that is also a first name or a surname ("em marco").
// * Stoplist names ("Natal", "Bom Jesus", "Itaú"...) match only capitalized right after a cue.
//
// resolvePlaces then settles overlaps with person spans: a longer name holds the place ("Ana
// Carolina"), a cue before the place makes it a place ("moro em Marília"), a longer place holds
// the name ("São José dos Campos" over "José dos Campos"), and otherwise a known first name or
// surname stays a person ("a Marília do RH"). Context-rule spans with no known name in them give
// way to the official list ("liguei pra Sorocaba"). A place made of known names right after a
// name cue is a person too, even when no person span covers it ("falei com o Barbosa").

import { lexicon as defaultLexicon, normalize } from "./lexicon.js";
import { cueBefore, isTitle } from "./names.js";

const TOKEN_RE = /[\p{L}\p{Nl}\p{No}]+/gu; // Python [^\W\d_]+
const BLANK = /^\s+$/u;
const SPACE = /\s/u;
const CONNECTORS = new Set(["a", "d", "da", "das", "de", "do", "dos", "e"]);
const APOSTROPHES = new Set(["'", "\u2019"]);
const OPENERS = new Set("\"'\u201c\u201d\u2018\u2019\u00ab\u00bb([{*-\u2013\u2014");
const TERMINATORS = new Set(".!?\u2026");
const MAX_CUE_WORDS = 2;

function tokenize(text) {
  return [...text.matchAll(TOKEN_RE)].map((m) => ({
    start: m.index, end: m.index + m[0].length, text: m[0], norm: normalize(m[0]),
  }));
}

/** True when only spaces, quotes or brackets separate `start` from a sentence boundary. */
export function isSentenceStart(text, start) {
  let i = start - 1;
  while (i >= 0 && (SPACE.test(text[i]) || OPENERS.has(text[i]))) {
    if (text[i] === "\n") return true;
    i -= 1;
  }
  return i < 0 || TERMINATORS.has(text[i]);
}

function spaceGap(value) {
  return value !== "" && BLANK.test(value) && !value.includes("\n");
}

/** Separators allowed inside a place name: spaces, a hyphen or an apostrophe. */
function placeGap(value) {
  return spaceGap(value) || value === "-" || APOSTROPHES.has(value);
}

function joined(words, text, gapOk) {
  for (let k = 1; k < words.length; k += 1) {
    if (!gapOk(text.slice(words[k - 1].end, words[k].start))) return false;
  }
  return true;
}

function hasPlaceCue(tokens, i, text, lex) {
  for (let size = MAX_CUE_WORDS; size >= 1; size -= 1) {
    if (i < size) continue;
    const words = tokens.slice(i - size, i + 1);
    const cue = words.slice(0, -1).map((t) => t.norm).join(" ");
    if (lex.placeCues.has(cue) && joined(words, text, spaceGap)) return true;
  }
  return false;
}

function significant(words) {
  const out = words.filter((t) => !CONNECTORS.has(t.norm));
  return out.length ? out : words;
}

function accepts(words, key, cued, text, lex) {
  if (lex.isAllowed(text.slice(words[0].start, words.at(-1).end))) return false;
  const main = significant(words);
  const capitalized = main.every((t) => isTitle(t.text));
  if (lex.placeStoplist.has(key)) return cued && capitalized;
  const classes = main.map((t) => lex.placeClass(t.norm));
  const allCommon = !classes.includes("proper");
  if (capitalized) {
    if (cued) return true;
    if (classes.length === 1 && classes[0] !== "mixed") return classes[0] === "proper";
    return !allCommon || !isSentenceStart(text, words[0].start);
  }
  if (!cued || allCommon) return false;
  return words.length > 1 || !(lex.isFirstName(key) || lex.surnames.has(key));
}

/**
 * Place names in `text`, in order and without overlaps.
 * @returns {{start: number, end: number, cued: boolean, personCued: boolean}[]}
 */
export function findPlaces(text, lex = defaultLexicon) {
  const tokens = tokenize(text);
  const spans = [];
  let i = 0;
  while (i < tokens.length) {
    let size = 0;
    for (const n of lex.placeIndex.get(tokens[i].norm) ?? []) {
      const words = tokens.slice(i, i + n);
      if (words.length < n || !joined(words, text, placeGap)) continue;
      const key = words.map((t) => t.norm).join(" ");
      if (!lex.places.has(key)) continue;
      const cued = hasPlaceCue(tokens, i, text, lex);
      if (!accepts(words, key, cued, text, lex)) continue;
      const personCued = cueBefore(tokens, i, text, lex.nameCues, lex)
        && significant(words).every((t) => lex.isKnownName(t.norm));
      spans.push({ start: words[0].start, end: words.at(-1).end, cued, personCued });
      size = n;
      break;
    }
    i += size || 1;
  }
  return spans;
}

function hasKnownName(span, text, lex) {
  return tokenize(text.slice(span.start, span.end)).some((t) => lex.isKnownName(t.norm));
}

/** true: the place wins; false: the person spans win; null: keep the person spans as they are. */
function placeWins(place, people, text, lex) {
  if (!people.length) return !place.personCued || place.cued;
  const size = place.end - place.start;
  if (people.some((p) => p.start <= place.start && place.end <= p.end && p.end - p.start > size)) {
    return null; // a longer name holds the place: "Ana Carolina", "Paulo Souza"
  }
  if (place.cued) return true;
  if (people.every((p) => place.start <= p.start && p.end <= place.end && p.end - p.start < size)) {
    return true; // the place holds the name: "São José dos Campos" over "José dos Campos"
  }
  return people.every((p) => p.source === "context" && !hasKnownName(p, text, lex));
}

/** Name spans plus place spans, overlaps settled as described above. Sorted, non-overlapping. */
export function resolvePlaces(names, places, text, lex = defaultLexicon) {
  let spans = [...names];
  for (const place of places) {
    const hits = spans.filter((s) => s.start < place.end && place.start < s.end);
    const people = hits.filter((s) => s.type === "PESSOA");
    const wins = placeWins(place, people, text, lex);
    if (wins === null) continue;
    const start = Math.min(place.start, ...hits.map((s) => s.start));
    const end = Math.max(place.end, ...hits.map((s) => s.end));
    const source = wins || !people.length ? "gazetteer" : people[0].source;
    spans = spans.filter((s) => !hits.includes(s));
    spans.push({ start, end, type: wins ? "LOCAL" : "PESSOA", source });
  }
  return spans.sort((a, b) => a.start - b.start);
}
