// Number helpers that reproduce Python's arithmetic exactly, so the dashboard and the production
// statistics of the Worker give the same numbers as src/enps/metrics.

/**
 * round(numerator / denominator) with halves away from zero, in integer math
 * (enps.metrics.enps.round_half_away). Never returns -0.
 */
export function roundHalfAway(numerator, denominator) {
  const sign = numerator < 0 ? -1 : 1;
  const abs = Math.abs(numerator);
  const quotient = Math.floor(abs / denominator);
  const remainder = abs - quotient * denominator;
  const value = quotient + (2 * remainder >= denominator ? 1 : 0);
  return value === 0 ? 0 : sign * value;
}

/**
 * Python's round(x, ndigits) for floats: the decimal closest to the exact binary value of x,
 * with exact ties to the even digit. Number.prototype.toFixed gives the closest decimal too but
 * breaks ties upwards, so ties are found and settled here. A tie means x * 10^d ends in exactly
 * .5, which for a binary float happens only when x * 2^(d+1) is an odd integer.
 */
export function pyRound(x, ndigits = 0) {
  if (!Number.isFinite(x)) return x;
  const odd = x * 2 ** (ndigits + 1); // exact: scaling by a power of two
  const scaledOdd = odd * 5 ** ndigits;
  if (Number.isInteger(odd) && Math.abs(odd) % 2 === 1 && Number.isSafeInteger(scaledOdd)) {
    const floor = (scaledOdd - 1) / 2; // floor(x * 10^d), exact
    const digits = floor % 2 === 0 ? floor : floor + 1;
    if (digits === 0) return x < 0 ? -0 : 0;
    return digits / 10 ** ndigits;
  }
  return Number(x.toFixed(ndigits));
}

/**
 * Python 3.12 sum() of floats: Neumaier compensated summation, the compensation added once at
 * the end (CPython builtin_sum_impl). Plain left to right addition can differ in the last bit.
 */
export function pySum(values) {
  if (values.length === 0) return 0;
  let total = 0 + values[0];
  let compensation = 0;
  for (let i = 1; i < values.length; i += 1) {
    const x = values[i];
    const t = total + x;
    compensation += Math.abs(total) >= Math.abs(x) ? total - t + x : x - t + total;
    total = t;
  }
  if (compensation && Number.isFinite(compensation)) total += compensation;
  return total;
}
