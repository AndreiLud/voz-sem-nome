// Scales for the dashboard charts. Pure functions, no DOM: the tests import this file in Node.

/** Half widths tried for the eNPS axis, smallest first. eNPS lives in -100..+100. */
const ENPS_HALF_WIDTHS = [10, 25, 50, 75, 100];

/** Class widths tried for the heatmap (shares), smallest first. */
const HEAT_STEPS = [0.01, 0.02, 0.05, 0.1, 0.15, 0.2, 0.25, 0.5];
export const HEAT_MAX_CLASSES = 6;
const EPSILON = 1e-9;

/** Smallest symmetric domain [-d, d] from the list above that holds every value. */
export function symmetricDomain(values) {
  const maxAbs = values.reduce((max, v) => (Number.isFinite(v) ? Math.max(max, Math.abs(v)) : max), 0);
  return ENPS_HALF_WIDTHS.find((d) => d >= maxAbs) ?? 100;
}

/** Position (0..100, percent of the plot) of `value` on the domain [-d, d]. */
export function position(value, d) {
  const clamped = Math.max(-d, Math.min(d, value));
  return ((clamped + d) / (2 * d)) * 100;
}

/** Start and size (percent of the plot) of a bar that grows from zero to `value`. */
export function barExtent(value, d) {
  const zero = position(0, d);
  const end = position(value, d);
  return { start: Math.min(zero, end), size: Math.abs(end - zero) };
}

/** Ticks of the eNPS axis: the two ends and zero. */
export function divergingTicks(d) {
  return [-d, 0, d];
}

/**
 * Heatmap classes for shares in 0..max: equal steps from HEAT_STEPS, at most
 * HEAT_MAX_CLASSES classes above zero. Returns {step, count}; count is 0 when max is 0.
 */
export function heatScale(max) {
  if (!(max > 0)) return { step: HEAT_STEPS[0], count: 0 };
  const step = HEAT_STEPS.find((s) => Math.ceil(max / s - EPSILON) <= HEAT_MAX_CLASSES) ?? 1 / HEAT_MAX_CLASSES;
  return { step, count: Math.max(1, Math.ceil(max / step - EPSILON)) };
}

/** Class of a share: 0 for zero, then 1..count, where class i holds ((i-1)*step, i*step]. */
export function heatClass(share, scale) {
  if (!(share > 0) || scale.count === 0) return 0;
  return Math.min(scale.count, Math.max(1, Math.ceil(share / scale.step - EPSILON)));
}

/**
 * Ramp level 1..6 (the CSS fills) for class 1..count, spread over the whole ramp so a scale
 * with few classes still ends on the darkest step. A single class takes the middle step.
 */
export function heatLevel(cls, count) {
  if (cls <= 0) return 0;
  if (count <= 1) return 4;
  return 1 + Math.round(((cls - 1) * (HEAT_MAX_CLASSES - 1)) / (count - 1));
}

/** Bounds [lo, hi] of every class, for the legend. */
export function heatBounds(scale) {
  return Array.from({ length: scale.count }, (_, i) => [i * scale.step, (i + 1) * scale.step]);
}

/** Shares of parts that add up to 1; all zero when the total is zero. */
export function shares(counts) {
  const total = counts.reduce((sum, c) => sum + (c || 0), 0);
  return counts.map((c) => (total ? (c || 0) / total : 0));
}
