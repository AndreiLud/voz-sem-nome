// Word lists shared by the name and place detectors. Port of src/enps/anonymize/lexicon.py: the
// lists come from data.js (built by scripts/build_worker_data.py) and every lookup goes through
// normalize(), so "João" matches the IBGE entry "joao".

import * as data from "./data.js";

const NON_WORD = /[^\p{L}\p{N}]+/gu; // Python [\W_]+
const MARKS = /\p{Mn}/gu;
const DIMINUTIVE_SUFFIXES = ["zinho", "zinha", "inho", "inha"];

function caseFold(word) {
  return word.toLowerCase().replaceAll("ß", "ss");
}

/** Case fold and strip accents: "Vitória" -> "vitoria". */
export function normalize(word) {
  return caseFold(word).normalize("NFKD").replace(MARKS, "");
}

/** Normalize a multi-word expression: "Segunda-feira" -> "segunda feira". */
export function normalizePhrase(text) {
  return normalize(text).replace(NON_WORD, " ").trim();
}

/** Case fold but keep accents: "Março" -> "março" (normalize would give "marco"). */
export function fold(word) {
  return caseFold(word.trim()).normalize("NFC");
}

export class Lexicon {
  /**
   * @param {object} lists Sets of normalized entries: firstNames, surnames, stoplist,
   *   allowTokens, allowPhrases, nameCues, abbreviationCues, connectors, allowAccented
   *   (folded, with accents), and for places: places (official names), placeCommon and
   *   placeMixed (words of place names that are common words, or used about as often in both
   *   cases), placeStoplist and placeCues ("moro em").
   */
  constructor(lists) {
    this.firstNames = lists.firstNames;
    this.surnames = lists.surnames;
    this.stoplist = lists.stoplist;
    this.allowTokens = lists.allowTokens;
    this.allowPhrases = lists.allowPhrases;
    this.nameCues = lists.nameCues;
    this.abbreviationCues = lists.abbreviationCues;
    this.connectors = lists.connectors;
    this.allowAccented = lists.allowAccented;
    this.places = lists.places ?? new Set();
    this.placeCommon = lists.placeCommon ?? new Set();
    this.placeMixed = lists.placeMixed ?? new Set();
    this.placeStoplist = lists.placeStoplist ?? new Set();
    this.placeCues = lists.placeCues ?? new Set();
    this.placeIndex = placeIndex(this.places);
  }

  /** "common", "mixed" or "proper" for a normalized word of a place name. */
  placeClass(norm) {
    if (this.placeCommon.has(norm)) return "common";
    return this.placeMixed.has(norm) ? "mixed" : "proper";
  }

  /** True for an IBGE first name, a configured nickname or a diminutive of a first name. */
  isFirstName(norm) {
    if (this.firstNames.has(norm)) return true;
    for (const suffix of DIMINUTIVE_SUFFIXES) {
      if (!norm.endsWith(suffix)) continue;
      const stem = norm.slice(0, -suffix.length);
      const candidates = suffix.startsWith("z") ? [stem] : [stem + "o", stem + "a", stem];
      if (candidates.some((c) => c.length >= 3 && this.firstNames.has(c) && !this.stoplist.has(c))) {
        return true;
      }
    }
    return false;
  }

  /** First name or surname that is not also a common word. */
  isKnownName(norm) {
    if (this.stoplist.has(norm)) return false;
    return this.isFirstName(norm) || this.surnames.has(norm);
  }

  /** Whether a word or expression, as written or normalized, is on the allowlist. */
  isAllowed(text) {
    const phrase = normalizePhrase(text);
    return this.allowTokens.has(phrase) || this.allowPhrases.has(phrase)
      || this.allowAccented.has(fold(text));
  }
}

/** First word of a place name -> its lengths in words, longest first. */
function placeIndex(places) {
  const lengths = new Map();
  for (const place of places) {
    const parts = place.split(" ");
    if (!lengths.has(parts[0])) lengths.set(parts[0], new Set());
    lengths.get(parts[0]).add(parts.length);
  }
  return new Map([...lengths].map(([first, sizes]) => [first, [...sizes].sort((a, b) => b - a)]));
}

function words(list) {
  return new Set(list.trim().split(/\s+/u).filter(Boolean));
}

function lines(list) {
  return new Set(list.split("\n").map((line) => line.trim()).filter(Boolean));
}

/** The lexicon of data.js, built once when the module loads. */
export const lexicon = new Lexicon({
  firstNames: words(data.FIRST_NAMES),
  surnames: words(data.SURNAMES),
  stoplist: words(data.STOPLIST),
  allowTokens: words(data.ALLOW_TOKENS),
  allowPhrases: lines(data.ALLOW_PHRASES),
  nameCues: words(data.NAME_CUES),
  abbreviationCues: words(data.ABBREVIATION_CUES),
  connectors: words(data.CONNECTORS),
  allowAccented: words(data.ALLOW_ACCENTED),
  places: lines(data.PLACES),
  placeCommon: words(data.PLACE_COMMON),
  placeMixed: words(data.PLACE_MIXED),
  placeStoplist: lines(data.PLACE_STOPLIST),
  placeCues: lines(data.PLACE_CUES),
});
