// Number and date formatting. Mirrors enps/export/site_content.py so the numbers rendered
// by the build and the numbers updated in the browser look the same.

const LOCALES = { pt: "pt-BR", en: "en" };
const MINUS = "−";
const TIME_ZONE = "America/Sao_Paulo";

function locale(lang) {
  return LOCALES[lang] || "pt-BR";
}

export function fmtNumber(value, lang, digits = 0) {
  const text = new Intl.NumberFormat(locale(lang), {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(value);
  return text.replace("-", MINUS);
}

/** 0.873 -> "87%" (digits 0) or "87,3%" / "87.3%" (digits 1). */
export function fmtPercent(share, lang, digits = 0) {
  return `${fmtNumber(share * 100, lang, digits)}%`;
}

/** eNPS style: +24, 0, -8 (typographic minus). */
export function fmtSigned(value, lang) {
  const text = fmtNumber(value, lang);
  return Math.round(value) > 0 ? `+${text}` : text;
}

/** Whole hours from 100 up, one decimal below. */
export function fmtHours(hours, lang) {
  return fmtNumber(hours, lang, Math.abs(hours) >= 100 ? 0 : 1);
}

/** "10/11/2026 12:00" or "Nov 10, 2026, 12:00 PM", in Sao Paulo time. */
export function fmtDateTime(iso, lang) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat(locale(lang), {
    dateStyle: "short",
    timeStyle: "short",
    timeZone: TIME_ZONE,
  }).format(date);
}

/** Short duration: "45 s", "3 min", "1 h 10 min". */
export function fmtDuration(seconds, lang) {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${fmtNumber(s, lang)} s`;
  const minutes = Math.round(s / 60);
  if (minutes < 60) return `${fmtNumber(minutes, lang)} min`;
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return m ? `${fmtNumber(h, lang)} h ${fmtNumber(m, lang)} min` : `${fmtNumber(h, lang)} h`;
}

function separators(lang) {
  return lang === "en" ? [",", "."] : [".", ","];
}

function cleaned(text) {
  return String(text).trim().replace(/\s/g, "").replace(MINUS, "-");
}

/** True for "1.000" or "12.345.678" (pt, group "."): first group 1 to 3 digits not starting with 0, then groups of 3. */
function isGrouped(raw, group) {
  const g = group === "." ? "\\." : group;
  return new RegExp(`^-?[1-9]\\d{0,2}(${g}\\d{3})+$`).test(raw);
}

/** Integer part: plain digits or correctly grouped digits, returned without the group separators. Null otherwise. */
function integerDigits(raw, group) {
  if (/^-?\d+$/.test(raw)) return raw;
  if (isGrouped(raw, group)) return raw.split(group).join("");
  return null;
}

/**
 * Parse "1.234,5" (pt) or "1,234.5" (en). A group separator counts only in a correct grouping
 * ("1.000", "12.345"); a single one anywhere else is read as a decimal point ("1.5" or "0.100" typed
 * in Portuguese), and any other use makes the text invalid. Null when invalid.
 */
export function parseNumber(text, lang) {
  const raw = cleaned(text);
  if (!raw) return null;
  const [group, decimal] = separators(lang);
  if (raw.includes(decimal)) {
    const parts = raw.split(decimal);
    if (parts.length !== 2 || !/^\d+$/.test(parts[1])) return null;
    const whole = integerDigits(parts[0], group);
    return whole === null ? null : Number(`${whole}.${parts[1]}`);
  }
  const whole = integerDigits(raw, group);
  if (whole !== null) return Number(whole);
  const parts = raw.split(group);
  if (parts.length === 2 && /^-?\d+$/.test(parts[0]) && /^\d+$/.test(parts[1])) {
    return Number(`${parts[0]}.${parts[1]}`);
  }
  return null;
}

/**
 * Parse a whole number: plain digits ("10000") or correct grouping ("10.000" in Portuguese,
 * "10,000" in English). Anything else is invalid, never a different magnitude: "2.5" or "1.0000"
 * in Portuguese is not read as 25 or 10000, and a decimal separator is never read as a fraction.
 * Spaces are ignored wherever they are, since a space is never a decimal separator. Null when invalid.
 */
export function parseInteger(text, lang) {
  const raw = cleaned(text);
  const [group] = separators(lang);
  const whole = integerDigits(raw, group);
  return whole === null ? null : Number(whole);
}
