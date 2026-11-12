// Calendar helpers. Timestamps are stored as UTC ISO 8601 strings (toIso), counters and the
// daily IP salt follow the America/Sao_Paulo day (spDay, like the Python side), and the Workers
// AI allocation resets at 00:00 UTC.

import { spDay, toIso } from "./metrics/time.js";

export { spDay, toIso };

export const MINUTE_MS = 60_000;
export const HOUR_MS = 3_600_000;
export const DAY_MS = 86_400_000;

// Brazil has had no daylight saving time since 2019, so a Sao Paulo day starts at 03:00 UTC.
const SAO_PAULO_OFFSET = "-03:00";

/** UTC calendar day of a time in milliseconds, as YYYY-MM-DD. */
export const utcDay = (ms) => new Date(ms).toISOString().slice(0, 10);

/** Milliseconds of 00:00 in Sao Paulo on `day` (YYYY-MM-DD). */
export const spDayStart = (day) => Date.parse(`${day}T00:00:00${SAO_PAULO_OFFSET}`);

/** Start of the next Sao Paulo day after `ms`. */
export const nextSpMidnight = (ms) => spDayStart(spDay(ms)) + DAY_MS;

/** Next 00:00 UTC after `ms`, when the Workers AI allocation resets. */
export const nextUtcMidnight = (ms) => Date.parse(`${utcDay(ms)}T00:00:00Z`) + DAY_MS;

/** Whole seconds from `now` to `target`, at least 1. */
export const secondsUntil = (target, now) => Math.max(1, Math.ceil((target - now) / 1000));
