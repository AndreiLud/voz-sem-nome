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

/** Parse "1.234,5" (pt) or "1,234.5" (en); also accepts a plain "1234.5". Null when invalid. */
export function parseNumber(text, lang) {
  let raw = String(text).trim().replace(/\s/g, "").replace(MINUS, "-");
  if (!raw) return null;
  const [group, decimal] = lang === "en" ? [",", "."] : [".", ","];
  if (raw.includes(decimal)) {
    raw = raw.split(group).join("").replace(decimal, ".");
  } else if (raw.split(group).length === 2 && raw.split(group)[1].length !== 3) {
    raw = raw.replace(group, "."); // "1.5" typed in Portuguese, "1,5" typed in English
  } else {
    raw = raw.split(group).join("");
  }
  if (!/^-?\d+(\.\d+)?$/.test(raw)) return null;
  return Number(raw);
}
