// Check digit and format helpers for Brazilian identifiers. Port of
// src/enps/anonymize/validators.py. The detectors mask formatted documents even when the check
// digits are wrong (recall first); these helpers only choose between readings of ambiguous digit
// runs, such as an unformatted 11-digit number that could be a CPF or a mobile phone.

// Area codes in use by Anatel.
const VALID_DDDS = new Set([
  11, 12, 13, 14, 15, 16, 17, 18, 19, 21, 22, 24, 27, 28,
  31, 32, 33, 34, 35, 37, 38, 41, 42, 43, 44, 45, 46, 47, 48, 49,
  51, 53, 54, 55, 61, 62, 63, 64, 65, 66, 67, 68, 69,
  71, 73, 74, 75, 77, 79, 81, 82, 83, 84, 85, 86, 87, 88, 89,
  91, 92, 93, 94, 95, 96, 97, 98, 99,
]);

const CNPJ_WEIGHTS_1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2];
const CNPJ_WEIGHTS_2 = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2];

export function onlyDigits(value) {
  return value.replace(/[^0-9]/g, "");
}

function allSame(chars) {
  return new Set(chars).size === 1;
}

/** True when the 11 digits of `value` carry correct CPF check digits. */
export function cpfIsValid(value) {
  const digits = onlyDigits(value);
  if (digits.length !== 11 || allSame(digits)) return false;
  const numbers = [...digits].map(Number);
  for (const size of [9, 10]) {
    let total = 0;
    for (let k = 0; k < size; k += 1) total += numbers[k] * (size + 1 - k);
    if (((total * 10) % 11) % 10 !== numbers[size]) return false;
  }
  return true;
}

// The 2026 alphanumeric CNPJ uses the ASCII code minus 48 for every character, which gives the
// same values as before for digits.
function cnpjCheckDigit(chars, weights) {
  let total = 0;
  for (let k = 0; k < weights.length && k < chars.length; k += 1) {
    total += (chars.charCodeAt(k) - 48) * weights[k];
  }
  const remainder = total % 11;
  return remainder < 2 ? 0 : 11 - remainder;
}

/** True for a numeric or alphanumeric (2026 format) CNPJ with correct check digits. */
export function cnpjIsValid(value) {
  const chars = value.toUpperCase().replace(/[^\p{L}\p{N}]/gu, "");
  if (chars.length !== 14 || !/^[0-9]{2}$/.test(chars.slice(12)) || !/^[\x00-\x7f]*$/.test(chars)) {
    return false;
  }
  if (allSame(chars)) return false;
  const first = cnpjCheckDigit(chars.slice(0, 12), CNPJ_WEIGHTS_1);
  const second = cnpjCheckDigit(chars.slice(0, 12) + String(first), CNPJ_WEIGHTS_2);
  return chars.slice(12) === `${first}${second}`;
}

export function isValidDdd(value) {
  return /^[0-9]{2}$/.test(value) && VALID_DDDS.has(Number(value));
}

/** 11 digits with a valid DDD followed by the mobile prefix 9. */
export function looksLikeMobile(digits) {
  return digits.length === 11 && isValidDdd(digits.slice(0, 2)) && digits[2] === "9";
}

/** 10 digits with a valid DDD and a subscriber number starting with 2 to 9. */
export function looksLikeLandlineOrOldMobile(digits) {
  return digits.length === 10 && isValidDdd(digits.slice(0, 2)) && "23456789".includes(digits[2]);
}
