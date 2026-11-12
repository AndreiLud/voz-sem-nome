// Regex detectors for Brazilian personal data formats. Port of src/enps/anonymize/patterns.py.
//
// Each detector returns candidate spans in original text coordinates. findAll runs them in a
// fixed priority order (cue-based and specific formats first, generic digit runs last) and keeps
// a candidate only when it does not overlap one already accepted.
//
// JavaScript's \w, \W and \b only know ASCII, even with the u flag, so the patterns spell out
// the Unicode classes that Python's re uses for str patterns: a word character is a letter, a
// number or "_". Every quantifier is bounded or separated by a different character class, so no
// pattern backtracks catastrophically.

import * as v from "./validators.js";

const W = String.raw`\p{L}\p{N}_`; // inside a class: Python's \w
const NW = String.raw`[^\p{L}\p{N}_]`; // Python's \W
const END = String.raw`(?![\p{L}\p{N}_])`; // Python's \b after a word character

const STATE_CODES = "AC|AL|AP|AM|BA|CE|DF|ES|GO|MA|MT|MS|MG|PA|PB|PR|PE|PI|RJ|RN|RS|RO|RR|SC|SP|SE|TO";
// Optional words between a cue and its value: "matrícula nº 123", "RG: 12.345.678-9", "cpf é ..."
// ("e" is "é" typed without the accent: "meu rg e 12.345.678-9"). Used in case-insensitive patterns.
const FILLER = String.raw`(?:\s*(?:n[º°o]\.?|n\.|n[úu]mero|num\.?|é|eh|e|:|#|-)(?=[\s\d:#\-]|$))*\s*`;

function re(source, flags = "") {
  return new RegExp(source, `u${flags}`);
}

const EMAIL_RE = re(String.raw`(?<![${W}.+\-])[${W}.+\-]+@[${W}\-]+(?:\.[${W}\-]+)+`, "g");

const CNPJ_FORMATTED_RE = re(
  String.raw`(?<![${W}./\-])[A-Za-z0-9]{2}\.?[A-Za-z0-9]{3}\.?[A-Za-z0-9]{3}/[A-Za-z0-9]{4}-?\d{2}(?![${W}/\-])`,
  "g",
);
const CNPJ_PLAIN_RE = re(String.raw`(?<![${W}./\-])[A-Za-z0-9]{12}\d{2}(?![${W}/\-])`, "g");
const CNPJ_CUE_RE = re(String.raw`(?<![${W}])cnpj${NW}{0,6}(?:[${W}]+${NW}+)?$`, "i");

const RG_RE = re(
  String.raw`(?<![${W}])(?:rg|r\.g\.?|identidade|c[ée]dula\s+de\s+identidade)(?![${W}])`
    + FILLER
    + String.raw`(?<val>(?:(?:${STATE_CODES})[\-\s]?)?\d(?:[.\-]?\d){4,13}(?:-?[Xx])?)(?![${W}])`,
  "dgi",
);

const MATRICULA_RE = re(
  String.raw`(?<![${W}])(?:matr[íi]cula|mat\.|registro|re|crach[áa]|id\s+funcional|chapa|`
    + String.raw`n[úu]mero\s+funcional)(?:\s+funcional)?(?![${W}])`
    + FILLER
    + String.raw`(?<val>[A-Za-z]{0,3}-?\d[${W}./\-]*)`,
  "dgi",
);
const UNIT_AFTER_RE = re(
  String.raw`\s*(?:h${END}|hrs?${END}|horas?${END}|min|dias?${END}|reais${END}|anos?${END}|%|x${END}|º|°)`,
  "iy",
);

const CEP_CUE_RE = re(
  String.raw`(?<![${W}])cep(?![${W}])` + FILLER + String.raw`(?<val>\d{5}-?\d{3}|\d{2}\.\d{3}-?\d{3})(?!\d)`,
  "dgi",
);
const CEP_RE = re(
  String.raw`(?<![${W}.,/\-])(?:\d{5}-\d{3}|\d{2}\.\d{3}-\d{3})(?![${W}/\-]|[.,]\d)`,
  "g",
);

const CPF_RE = re(
  String.raw`(?<![${W}.,/\-])\d{3}[.\s]?\d{3}[.\s]?\d{3}\s?[\-./\s]?\s?\d{2}(?![${W}/\-]|[.,]\d)`,
  "g",
);
const CPF_CUE_RE = re(String.raw`(?<![${W}])cpf${NW}{0,6}(?:[${W}]+${NW}+)?$`, "i");

const PHONE_CUE_RE = re(
  String.raw`(?<![${W}])(?:tel|telefone|fone|cel|celular|whats|whatsapp|zap|wpp|contato|liga(?:r)?|n[úu]mero)`
    + String.raw`${NW}{0,6}(?:[${W}]+${NW}+)?$`,
  "i",
);
const PHONE_FORMATTED_RE = re(
  String.raw`(?<![${W}+])(?:\+\s?55[\s.\-]?)?(?:\(\s?0?\d{2}\s?\)|0?\d{2})[\s.\-]?(?:9[\s.]?)?`
    + String.raw`(?<a>\d{4})[\s.\-]?(?<b>\d{4})(?![${W}/\-]|[.,]\d)`,
  "g",
);
const PHONE_LOCAL_RE = re(
  String.raw`(?<![${W}.,/+\-])(?:9[\s.]?)?(?<a>\d{4})[\s.\-](?<b>\d{4})(?![${W}/\-]|[.,]\d)`,
  "g",
);
const PHONE_CUE_DIGITS_RE = re(
  String.raw`(?<![${W}])(?:tel|telefone|fone|cel|celular|whats|whatsapp|zap|wpp|contato)(?![${W}])`
    + FILLER + String.raw`(?<val>\d{8,9})(?!\d)`,
  "dgi",
);
const RAMAL_RE = re(String.raw`(?<![${W}])ramal(?![${W}])` + FILLER + String.raw`(?<val>\d{3,5})(?!\d)`, "dgi");
const DIGIT_RUN_RE = re(String.raw`(?<!\d)\d{8,}(?!\d)`, "g");

const PLATE_MERCOSUL_RE = re(String.raw`(?<![${W}\-])[A-Za-z]{3}-?\d[A-Za-z]\d{2}(?![${W}\-])`, "g");
const PLATE_OLD_RE = re(
  String.raw`(?<![${W}\-])(?<letters>[A-Za-z]{3})(?<sep>[\-\s]?)(?<digits>\d{4})(?![${W}\-])`,
  "g",
);
const PLATE_CUE_RE = re(
  String.raw`(?<![${W}])(?:placa|carro|moto|ve[íi]culo|caminh[ãa]o|van|furg[ãa]o|truck|carreta)`
    + String.raw`[\s:]{0,4}(?:[${W}]+[\s:]+){0,2}$`,
  "i",
);
// Three-letter words and acronyms that precede a 4-digit number in ordinary text ("ano 2024").
const PLATE_STOPWORDS = new Set((
  "ano dia mes jan fev mar abr mai jun jul ago set out nov dez das dos nas nos com sem por pra "
  + "pro que era foi mil iso nbr plr ppr clt sap crm ura erp wms tms the and top cep cpf tel bus "
  + "seg ter qua qui sex sab dom lei art cap vol nro ref cod num pag ped nfe"
).split(" "));

/**
 * Case-insensitive source for plain words, for patterns that are otherwise case sensitive:
 * "rua" -> "[rR][uU][aA]". Python uses an inline case-insensitive group there, but RegExp
 * modifiers need V8 12.5 or later, and Node 22 (allowed by package.json) has V8 12.4.
 */
function anyCase(words) {
  const letter = (ch) => {
    if (ch === ".") return String.raw`\.`;
    const lower = ch.toLowerCase();
    const upper = ch.toUpperCase();
    return lower === upper ? ch : `[${lower}${upper}]`;
  };
  return words.map((word) => [...word].map(letter).join("")).join("|");
}

// The street cue ignores case; the rest of the address patterns does not.
const STREET_CUE = String.raw`(?<![${W}])(?:` + anyCase([
  "rua", "r.", "av.", "av", "avenida", "travessa", "tv.", "alameda", "al.", "estrada", "estr.",
  "rodovia", "rod.", "praça", "praca", "pça.", "pca.",
]) + ")";
const CAP_WORD = String.raw`(?:[A-ZÀ-Ý][${W}À-ÿ'.]*|\d{1,2}(?=\s+de\s))`;
const CONNECTOR = String.raw`(?:d[aeo]s?|e)`;
const HOUSE_NUMBER = String.raw`(?:\s*,?\s*(?:n[º°o]\.?\s*)?\d{1,5}[A-Za-z]?(?!\d|[.,]\d)|\s*,?\s*km\s*\d{1,4})`;
const ADDRESS_CAP_RE = re(
  STREET_CUE
    + String.raw`\s+(?:${CONNECTOR}\s+)?${CAP_WORD}(?:[ \t]+(?:${CONNECTOR}[ \t]+)?${CAP_WORD}){0,5}`
    + `${HOUSE_NUMBER}?`,
  "g",
);
// Lowercase street names need a house number, and the number must not count something
// ("a rua tem 30 buracos").
const ADDRESS_LOWER_RE = re(
  STREET_CUE + String.raw`\s+(?:[a-zà-ÿ]+\s+){0,4}?[a-zà-ÿ]{3,}` + HOUSE_NUMBER
    + String.raw`(?!\s+[a-zà-ÿ]+s${END}|\s*(?:%|h${END}|min${END}|m${END}))`,
  "g",
);
const NEIGHBORHOOD_RE = re(
  String.raw`(?<![${W}])(?:${anyCase(["bairro"])})\s+(?:${CONNECTOR}\s+)?${CAP_WORD}`
    + String.raw`(?:[ \t]+(?:${CONNECTOR}[ \t]+)?${CAP_WORD}){0,3}`,
  "g",
);
const FORMATTED_DOC_RE = re(String.raw`(?<![${W}.,/\-])\d{1,3}(?:\.\d{3}){2,}-[\dXx]{1,2}(?![${W}/\-])`, "g");
const MONEY_BEFORE_RE = re(String.raw`R\$\s*$`);
const DIGIT = /[0-9]/;

function span(start, end, type) {
  return { start, end, type, source: "regex" };
}

function countDigits(value) {
  let n = 0;
  for (const ch of value) if (DIGIT.test(ch)) n += 1;
  return n;
}

function cueBefore(text, start, cueRe, window = 30) {
  return cueRe.test(text.slice(Math.max(0, start - window), start));
}

/** Drop trailing `chars` from [start, end). */
function trimEnd(text, start, end, chars = ".-/") {
  while (end > start && chars.includes(text[end - 1])) end -= 1;
  return [start, end];
}

function findEmails(text) {
  return [...text.matchAll(EMAIL_RE)].map((m) => span(m.index, m.index + m[0].length, "EMAIL"));
}

function findCnpjs(text) {
  const out = [];
  for (const m of text.matchAll(CNPJ_FORMATTED_RE)) {
    if (countDigits(m[0]) >= 4) out.push(span(m.index, m.index + m[0].length, "CNPJ"));
  }
  for (const m of text.matchAll(CNPJ_PLAIN_RE)) {
    const value = m[0];
    const hasDigitRoot = DIGIT.test(value.slice(0, 12));
    if ((hasDigitRoot && v.cnpjIsValid(value)) || cueBefore(text, m.index, CNPJ_CUE_RE)) {
      out.push(span(m.index, m.index + value.length, "CNPJ"));
    }
  }
  return out;
}

/** Detector for "cue + value" patterns whose value is the named group "val". */
function cueValue(pattern, type, minDigits) {
  return (text) => {
    const out = [];
    for (const m of text.matchAll(pattern)) {
      const [start, end] = trimEnd(text, ...m.indices.groups.val);
      if (countDigits(text.slice(start, end)) >= minDigits) out.push(span(start, end, type));
    }
    return out;
  };
}

const findRgs = cueValue(RG_RE, "RG", 5);
const findCepsWithCue = cueValue(CEP_CUE_RE, "CEP", 8);
const findRamal = cueValue(RAMAL_RE, "TELEFONE", 3);
const findPhonesWithCue = cueValue(PHONE_CUE_DIGITS_RE, "TELEFONE", 8);

function unitAfter(text, end) {
  UNIT_AFTER_RE.lastIndex = end;
  return UNIT_AFTER_RE.test(text);
}

function findMatriculas(text) {
  const out = [];
  for (const m of text.matchAll(MATRICULA_RE)) {
    const [start, end] = trimEnd(text, ...m.indices.groups.val);
    if (countDigits(text.slice(start, end)) < 3) continue;
    if (unitAfter(text, end)) continue; // "registro 100 horas", "chapa 300%"
    out.push(span(start, end, "MATRICULA"));
  }
  return out;
}

function findCeps(text) {
  return [...text.matchAll(CEP_RE)].map((m) => span(m.index, m.index + m[0].length, "CEP"));
}

function findFormattedCpfs(text) {
  const out = [];
  for (const m of text.matchAll(CPF_RE)) {
    // Plain 11-digit runs are handled by findPlainNumbers.
    if (!/^[0-9]+$/.test(m[0])) out.push(span(m.index, m.index + m[0].length, "CPF"));
  }
  return out;
}

/** Decide between CPF and mobile phone for an unformatted 11-digit run. */
function classifyElevenDigits(text, start, digits) {
  if (cueBefore(text, start, CPF_CUE_RE) || v.cpfIsValid(digits)) return "CPF";
  if (cueBefore(text, start, PHONE_CUE_RE) || v.looksLikeMobile(digits)) return "TELEFONE";
  return "CPF"; // recall first: a bare 11-digit run is most likely a mistyped CPF
}

/** Unformatted digit runs with a clear reading: CPF or phone (11), phone (10, or 12-13 with 55). */
function findPlainNumbers(text) {
  const out = [];
  for (const m of text.matchAll(DIGIT_RUN_RE)) {
    const digits = m[0];
    const end = m.index + digits.length;
    if (digits.length === 11) {
      out.push(span(m.index, end, classifyElevenDigits(text, m.index, digits)));
    } else if (digits.length === 10 && v.looksLikeLandlineOrOldMobile(digits)) {
      out.push(span(m.index, end, "TELEFONE"));
    } else if ((digits.length === 12 || digits.length === 13) && digits.startsWith("55")
               && v.isValidDdd(digits.slice(2, 4))) {
      const start = m.index > 0 && text[m.index - 1] === "+" ? m.index - 1 : m.index;
      out.push(span(start, end, "TELEFONE"));
    }
  }
  return out;
}

function isFormatted(value) {
  return /[+()\-. ]/.test(value);
}

/** "2023-2024" or "5000 6000" are year or value ranges, not a phone number. */
function looksLikeTwoNumbers(left, right) {
  const years = [left, right].every((p) => p.length === 4 && (p.startsWith("19") || p.startsWith("20")));
  const roundValues = left.endsWith("00") && right.endsWith("00");
  return years || roundValues;
}

function findFormattedPhones(text) {
  const out = [];
  for (const m of text.matchAll(PHONE_FORMATTED_RE)) {
    if (isFormatted(m[0]) && !looksLikeTwoNumbers(m.groups.a, m.groups.b)) {
      out.push(span(m.index, m.index + m[0].length, "TELEFONE"));
    }
  }
  return out;
}

/** Numbers without area code: 9 digits starting with 9, or 8 digits starting with 2 to 9. */
function findLocalPhones(text) {
  const out = [];
  for (const m of text.matchAll(PHONE_LOCAL_RE)) {
    const digits = v.onlyDigits(m[0]);
    if (digits.length === 8 && !"23456789".includes(digits[0])) continue;
    if (looksLikeTwoNumbers(m.groups.a, m.groups.b)) continue;
    out.push(span(m.index, m.index + m[0].length, "TELEFONE"));
  }
  return out;
}

function findPlates(text) {
  const out = [];
  for (const m of text.matchAll(PLATE_MERCOSUL_RE)) {
    out.push(span(m.index, m.index + m[0].length, "PLACA"));
  }
  for (const m of text.matchAll(PLATE_OLD_RE)) {
    const { letters, sep, digits } = m.groups;
    const plate = span(m.index, m.index + m[0].length, "PLACA");
    if (PLATE_STOPWORDS.has(letters.toLowerCase())) continue;
    if (cueBefore(text, m.index, PLATE_CUE_RE)) {
      out.push(plate);
      continue;
    }
    const upper = /^[A-Z]+$/.test(letters);
    const yearLike = digits.startsWith("19") || digits.startsWith("20");
    if (sep === "-") out.push(plate);
    else if (sep === "" && (upper || !yearLike)) out.push(plate);
    else if (sep && upper && !yearLike) out.push(plate); // "ABC 1234", never "ano 2024"
  }
  return out;
}

function findAddresses(text) {
  const out = [];
  for (const pattern of [ADDRESS_CAP_RE, ADDRESS_LOWER_RE, NEIGHBORHOOD_RE]) {
    for (const m of text.matchAll(pattern)) {
      const [start, end] = trimEnd(text, m.index, m.index + m[0].length, ".,- ");
      out.push(span(start, end, "LOCAL"));
    }
  }
  return out;
}

/** Leftover identifiers: dotted numbers with a check digit, and runs of 8 or more digits. */
function findDocuments(text) {
  const out = [];
  for (const m of text.matchAll(FORMATTED_DOC_RE)) {
    out.push(span(m.index, m.index + m[0].length, "DOCUMENTO"));
  }
  for (const m of text.matchAll(DIGIT_RUN_RE)) {
    if (!MONEY_BEFORE_RE.test(text.slice(Math.max(0, m.index - 4), m.index))) {
      out.push(span(m.index, m.index + m[0].length, "DOCUMENTO"));
    }
  }
  return out;
}

// Priority order: earlier detectors win on overlap.
export const DETECTORS = [
  findEmails,
  findCnpjs,
  findRgs,
  findMatriculas,
  findCepsWithCue,
  findCeps,
  findFormattedCpfs,
  findPlainNumbers,
  findFormattedPhones,
  findRamal,
  findPhonesWithCue,
  findLocalPhones,
  findPlates,
  findAddresses,
  findDocuments,
];

export function byStart(a, b) {
  return a.start - b.start;
}

/** Index of the first span of `spans` (sorted and disjoint, so sorted by end too) ending after `pos`. */
export function firstEndingAfter(spans, pos) {
  let lo = 0;
  let hi = spans.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (spans[mid].end <= pos) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

/** All regex spans, non-overlapping and sorted by position. */
export function findAll(text, detectors = DETECTORS) {
  const accepted = []; // kept sorted, so an overlap check is a binary search
  for (const detect of detectors) {
    const candidates = detect(text).sort((a, b) => a.start - b.start || (b.end - b.start) - (a.end - a.start));
    for (const candidate of candidates) {
      if (candidate.end <= candidate.start) continue;
      const k = firstEndingAfter(accepted, candidate.start);
      if (k < accepted.length && accepted[k].start < candidate.end) continue;
      accepted.splice(k, 0, candidate);
    }
  }
  return accepted;
}
