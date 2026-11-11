// Runtime settings (/data/config.json) and labels (/data/taxonomy.json), fetched once.
import { staticJson } from "./api.js";

const cache = new Map();

function once(url) {
  if (!cache.has(url)) cache.set(url, staticJson(url));
  return cache.get(url);
}

export const loadConfig = (url = "/data/config.json") => once(url);
export const loadTaxonomy = (url = "/data/taxonomy.json") => once(url);

/**
 * Label of a code in the taxonomy: kind is one of themes, sentiments, critical_types,
 * areas, tenure_bands, placeholders. Falls back to the code itself.
 */
export function labelOf(taxonomy, kind, code, lang) {
  const item = (taxonomy && taxonomy[kind] || []).find((entry) => entry.code === code);
  return item ? item.label[lang] || item.label.pt : code;
}
