// Settings, taxonomy codes and word lists of the Worker dashboard. Do not edit: this file is
// written by scripts/build_dashboard_fixtures.py from config/settings.yaml, config/taxonomy.yaml,
// config/profanity.txt and src/enps/metrics, and tests/test_dashboard_cases.py fails when it is
// stale.

// Defaults of buildDashboard: privacy settings, survey codes and taxonomy codes.
export const DASHBOARD_CONFIG = Object.freeze({
  min_group_size: 5,
  display_excerpt_chars: 280,
  areas: ["operacoes", "transporte", "atendimento", "comercial", "tecnologia", "financeiro", "pessoas", "juridico"],
  tenures: ["lt1", "y1to3", "y3to5", "gt5"],
  themes: ["lid", "rem", "car", "cre", "rec", "com", "eqp", "flx", "inf", "bem", "cul", "out"],
  sentiments: ["pos", "neu", "neg"],
  critical_types: ["am", "as", "di", "bo", "av"],
});

export const MAX_CRITICAL_CASES = 100;
export const MASK = "[ofensivo]";
export const ELLIPSIS = "…";

// Body of a regex character class: the characters Python's str.isspace() accepts.
export const WHITESPACE = "\\u0009-\\u000d\\u001c-\\u0020\\u0085\\u00a0\\u1680\\u2000-\\u200a\\u2028-\\u2029\\u202f\\u205f\\u3000";

// 90 profanity words, folded (lowercase, no accents), longest pattern first.
export const PROFANITY = Object.freeze([
  "filha da puta", "filho da puta", "pau no cu", "desgracadas", "desgracados", "mongoloides",
  "arrombadas", "arrombados", "desgracada", "desgracado", "mongoloide", "retardadas",
  "retardados", "vagabundas", "vagabundos", "arrombada", "arrombado", "retardada", "retardado",
  "vagabunda", "vagabundo", "bichinha", "caralhos", "escrotas", "escrotos", "foda-se", "sapatona",
  "travecos", "viadinho", "babacas", "bucetas", "caralho", "crioula", "crioulo", "escrota",
  "escroto", "fodidas", "fodidos", "idiotas", "imbecil", "imbecis", "otarias", "otarios",
  "putaria", "sapatao", "traveco", "babaca", "bichas", "bostas", "buceta", "cacete", "cornos",
  "cuzoes", "fodase", "fodida", "fodido", "idiota", "merdas", "otaria", "otario", "porras",
  "safada", "safado", "vadias", "viados", "bicha", "bosta", "corno", "cuzao", "fodas", "foder",
  "fodeu", "merda", "porra", "putas", "putos", "vadia", "viado", "foda", "fode", "puta", "puto",
  "vtnc", "crl", "fdp", "krl", "pqp", "tnc", "vsf", "cu"
]);
