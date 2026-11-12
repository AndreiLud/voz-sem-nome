// Time helpers matching src/enps/storage/timeutil.py: timestamps are ISO 8601 strings, values
// without an offset are UTC, calendar days follow America/Sao_Paulo.

const OFFSET = /(?:[zZ]|[+-]\d{2}(?::?\d{2})?)$/;
const SAO_PAULO_OFFSET_MS = -3 * 3600 * 1000; // fallback only: Brazil has had no DST since 2019

let dayFormat;

function saoPauloFormat() {
  if (dayFormat === undefined) {
    try {
      dayFormat = new Intl.DateTimeFormat("en-CA", {
        timeZone: "America/Sao_Paulo", year: "numeric", month: "2-digit", day: "2-digit",
      });
    } catch {
      dayFormat = null; // runtime without time zone data, like timeutil._sao_paulo()
    }
  }
  return dayFormat;
}

/** Milliseconds since the epoch of an ISO 8601 timestamp, a Date or a number. */
export function parseIso(value) {
  if (value instanceof Date) return value.getTime();
  if (typeof value === "number") return value;
  let text = String(value).trim().replace(" ", "T").replace(/(\.\d{3})\d+/, "$1");
  if (text.includes("T") && !OFFSET.test(text)) text += "Z";
  const ms = Date.parse(text);
  if (Number.isNaN(ms)) throw new RangeError(`invalid timestamp: ${value}`);
  return ms;
}

/** UTC ISO 8601 with milliseconds, like timeutil.to_iso: 2026-11-11T13:05:00.123+00:00. */
export function toIso(value) {
  return new Date(parseIso(value)).toISOString().replace(/Z$/, "+00:00");
}

/** Calendar day in America/Sao_Paulo as YYYY-MM-DD. */
export function spDay(value) {
  const ms = parseIso(value);
  const format = saoPauloFormat();
  if (format === null) return new Date(ms + SAO_PAULO_OFFSET_MS).toISOString().slice(0, 10);
  const parts = Object.fromEntries(format.formatToParts(ms).map((p) => [p.type, p.value]));
  return `${parts.year}-${parts.month}-${parts.day}`;
}

/** Milliseconds from `start` to `end`. */
export function msBetween(start, end) {
  return parseIso(end) - parseIso(start);
}
