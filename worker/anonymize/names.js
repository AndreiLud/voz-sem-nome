// Person names: the first-name gazetteer of src/enps/anonymize/gazetteer.py, @mentions, and a
// context rule that stands in for the spaCy NER layer, which cannot run in a Worker.
//
// Gazetteer: a token that is an IBGE first name is masked when it is capitalized ("Paulo",
// anywhere in the sentence) or follows a cue word ("o joao", "gestora ana"). Names that are also
// common words (the stoplist: rosa, clara, luz...) need both a cue and a capital letter. A masked
// name extends over the surnames that follow it ("Maria das Graças Souza", "joao da silva").
//
// Context rule: a capitalized word that no list knows is a person when it comes right after a
// role or title ("o gerente Antunes", "Sr. Kowalski", "meu colega Deyvison") or after a verb of
// talking and a preposition ("falei com a Kowalski", "reclamei pro Uelinton"). The span takes at
// most one more capitalized word that no list knows, and then only surnames and first names from
// the lists ("o gerente Antunes Kowalski Souza"). Allowlisted words (areas, RH, TI, Talvana,
// systems, months, weekdays) and adjectives that follow a role ("o gerente Geral") are never taken.
//
// A word with an elided prefix ("D'Ávila", "Sant'Anna", "Dall'Agnol") is one token: the lists and
// the capital letter checks look at the word after the apostrophe, and the span covers both.

import { lexicon as defaultLexicon, normalize } from "./lexicon.js";

// Python [^\W\d_]+, plus an optional elided prefix of one to four letters that does not start
// inside an @mention or another word.
const TOKEN_RE = /(?:(?<![\p{L}\p{N}_@.])\p{L}{1,4}['\u2019](?=\p{L}{2}))?[\p{L}\p{Nl}\p{No}]+/gu;
const ELISION = /^\p{L}{1,4}['\u2019]/u;
const MENTION_RE = /(?<![\p{L}\p{N}_@.])@[\p{L}\p{N}][\p{L}\p{N}_.]*[\p{L}\p{N}]|(?<![\p{L}\p{N}_@.])@[\p{L}\p{N}]/gu;
const UPPER_FIRST = /^\p{Uppercase}/u;
const LOWER_OR_TITLE = /[\p{Lowercase}\p{Lt}]/u;
const CASED = /[\p{Uppercase}\p{Lowercase}\p{Lt}]/u;
const BLANK = /^\s+$/u;
const ABBREVIATION_GAP = /^\.\s+$/u;

// Roles and titles beyond the name cues of config/anonymizer.yaml (whose roles and titles are
// used too). Normalized.
const EXTRA_ROLE_CUES = [
  "analista", "assistente", "auxiliar", "operador", "operadora", "conferente", "motorista",
  "vendedor", "vendedora", "consultor", "consultora", "estagiario", "estagiaria", "presidente",
  "tecnico", "tecnica", "colegas", "amigo", "amiga",
];
// Name cues that are articles or prepositions: they make a known first name a name ("o joao"),
// but a capitalized word after them is often a place, a brand or a team ("o Bradesco").
const FUNCTION_CUES = new Set(["o", "a", "do", "da", "dos", "das", "ao", "pro", "pra", "pelo", "pela", "com", "para"]);
// Words between a verb of talking and a name: "falei com o", "liguei pra", "reclamei ao".
const SPEECH_LINKS = new Set(["com", "pro", "pra", "para", "ao", "a", "o"]);
const SPEECH_VERBS = new Set([
  "falei", "falar", "fala", "falou", "fale", "falamos", "falaram",
  "conversei", "conversar", "conversa", "conversou", "converse", "conversamos",
  "reclamei", "reclamar", "reclama", "reclamou", "reclame", "reclamamos",
  "perguntei", "perguntar", "pergunta", "perguntou", "pergunte",
  "pedi", "pedir", "pede", "pediu",
  "avisei", "avisar", "avisa", "avisou", "avise",
  "liguei", "ligar", "liga", "ligou", "ligue",
  "contei", "contar", "conta", "contou", "conte",
  "reportei", "reportar", "reportou", "denunciei", "denunciar", "denunciou",
  "mandei", "mandar", "mandou", "escrevi", "escrever", "escreveu",
]);
// Adjectives that follow a role and are often capitalized with it: "o Gerente Geral".
const ROLE_QUALIFIERS = new Set([
  "geral", "tecnico", "tecnica", "administrativo", "administrativa", "operacional", "executivo",
  "executiva", "interino", "interina", "adjunto", "adjunta", "substituto", "substituta", "novo",
  "nova", "antigo", "antiga", "atual", "anterior", "imediato", "imediata", "direto", "direta",
  "responsavel", "junior", "senior", "master",
]);

function roleCues(lex) {
  return new Set([...[...lex.nameCues].filter((c) => !FUNCTION_CUES.has(c)), ...EXTRA_ROLE_CUES]);
}

const DEFAULT_ROLE_CUES = roleCues(defaultLexicon);

/** Tokens with their offsets; `word` drops an elided prefix and `norm` is `word` normalized. */
export function tokenize(text) {
  return [...text.matchAll(TOKEN_RE)].map((m) => {
    const word = m[0].replace(ELISION, "");
    return { start: m.index, end: m.index + m[0].length, text: m[0], word, norm: normalize(word) };
  });
}

/** Python str.isupper(): at least one cased character and no lowercase or titlecase one. */
function isUpper(word) {
  return CASED.test(word) && !LOWER_OR_TITLE.test(word);
}

/** Capitalized but not all caps: "João" yes, "JOÃO" and "joão" no. */
export function isTitle(word) {
  return UPPER_FIRST.test(word) && !([...word].length > 1 && isUpper(word));
}

function gap(text, left, right) {
  return text.slice(left.end, right.start);
}

function spaceGap(value) {
  return value !== "" && BLANK.test(value) && !value.includes("\n");
}

/** Whether the token before `i` is one of `cues`, right before it on the same line. */
export function cueBefore(tokens, i, text, cues, lex) {
  if (i === 0) return false;
  const prev = tokens[i - 1];
  if (!cues.has(prev.norm)) return false;
  const between = gap(text, prev, tokens[i]);
  return spaceGap(between) || (lex.abbreviationCues.has(prev.norm) && ABBREVIATION_GAP.test(between));
}

function startsName(tokens, i, text, lex) {
  const tok = tokens[i];
  if (tok.norm.length < 2 || lex.isAllowed(tok.word) || !lex.isFirstName(tok.norm)) return false;
  const title = isTitle(tok.word);
  const cue = cueBefore(tokens, i, text, lex.nameCues, lex);
  if (lex.stoplist.has(tok.norm)) return cue && title;
  return cue || title;
}

/** "falei com o", "liguei pra", "reclamei ao" right before token `i`. */
function speechBefore(tokens, i, text) {
  let k = i - 1;
  let links = 0;
  while (k >= 0 && links < 2 && SPEECH_LINKS.has(tokens[k].norm) && spaceGap(gap(text, tokens[k], tokens[k + 1]))) {
    k -= 1;
    links += 1;
  }
  return links > 0 && k >= 0 && SPEECH_VERBS.has(tokens[k].norm) && spaceGap(gap(text, tokens[k], tokens[k + 1]));
}

function startsContextName(tokens, i, text, lex, roles) {
  const tok = tokens[i];
  if (tok.norm.length < 2 || !isTitle(tok.word) || lex.isAllowed(tok.word)) return false;
  if (ROLE_QUALIFIERS.has(tok.norm) || roles.has(tok.norm)) return false;
  return cueBefore(tokens, i, text, roles, lex) || speechBefore(tokens, i, text);
}

/** Whether `tok` is a surname (or second name) that belongs to the name before it. */
function continuesName(tok, afterConnector, lex) {
  if (tok.norm.length < 2) return false;
  if (lex.isAllowed(tok.word)) {
    // A capitalized surname right after a name stays in it even when the word is on the
    // allowlist ("Carla Sales", "Paulo Vale"); "da Frota" is still the team.
    return !afterConnector && isTitle(tok.word) && lex.surnames.has(tok.norm);
  }
  if (isTitle(tok.word)) {
    if (afterConnector) return lex.surnames.has(tok.norm) || lex.firstNames.has(tok.norm); // "da Silva", not "da Logística"
    return true;
  }
  return lex.isKnownName(tok.norm); // lowercase or all caps: only names that are not words
}

function singleLineGap(value) {
  return value === "-" || spaceGap(value);
}

/**
 * Index of the last token of the full name that starts at token i. After `maxWords` words, only
 * surnames and first names from the lists extend it.
 */
function extend(tokens, i, text, lex, maxWords = Infinity) {
  let j = i;
  let words = 1;
  const fits = (tok) => words < maxWords || lex.isKnownName(tok.norm);
  while (j + 1 < tokens.length) {
    const next = tokens[j + 1];
    if (!singleLineGap(gap(text, tokens[j], next))) break;
    if (continuesName(next, false, lex) && fits(next)) {
      j += 1;
      words += 1;
      continue;
    }
    if (
      lex.connectors.has(next.norm)
      && j + 2 < tokens.length
      && singleLineGap(gap(text, next, tokens[j + 2]))
      && continuesName(tokens[j + 2], true, lex)
      && fits(tokens[j + 2])
    ) {
      j += 2;
      words += 1;
      continue;
    }
    break;
  }
  return j;
}

/** @handles, which point to a person: "@joao.silva". */
export function findMentions(text, lex = defaultLexicon) {
  const spans = [];
  for (const m of text.matchAll(MENTION_RE)) {
    if (!lex.isAllowed(normalize(m[0].slice(1)))) {
      spans.push({ start: m.index, end: m.index + m[0].length, type: "PESSOA", source: "context" });
    }
  }
  return spans;
}

/** Person spans from @mentions, the gazetteer and the context rule. Sorted, non-overlapping. */
export function findNames(text, lex = defaultLexicon) {
  const roles = lex === defaultLexicon ? DEFAULT_ROLE_CUES : roleCues(lex);
  const mentions = findMentions(text, lex);
  const spans = [...mentions];
  const tokens = tokenize(text);
  let m = 0; // mentions and tokens are both in text order: one pass over each
  let i = 0;
  while (i < tokens.length) {
    const tok = tokens[i];
    while (m < mentions.length && mentions[m].end <= tok.start) m += 1;
    const insideMention = m < mentions.length && mentions[m].start <= tok.start;
    let j = -1;
    let source = "gazetteer";
    if (!insideMention && startsName(tokens, i, text, lex)) {
      j = extend(tokens, i, text, lex);
    } else if (!insideMention && startsContextName(tokens, i, text, lex, roles)) {
      j = extend(tokens, i, text, lex, 2);
      source = "context";
    }
    if (j < 0) {
      i += 1;
      continue;
    }
    spans.push({ start: tok.start, end: tokens[j].end, type: "PESSOA", source });
    i = j + 1;
  }
  return spans.sort((a, b) => a.start - b.start);
}
