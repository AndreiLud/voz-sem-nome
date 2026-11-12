// Tracking codes, public ids and hashes, as in src/enps/queue/ingest.py.
//
// A tracking code (XXXX-XXXX, no 0, O, 1, I or L) is shown once to the person who submitted;
// only its sha256 is stored. A public id names a submission in alerts and on the dashboard.

export const TRACKING_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ";
export const TRACKING_LENGTH = 8;
export const PUBLIC_ID_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_";
export const PUBLIC_ID_LENGTH = 10;

const encoder = new TextEncoder();

/** Default source of randomness: n bytes from the platform CSPRNG. */
export const randomBytes = (n) => crypto.getRandomValues(new Uint8Array(n));

/** Uniform random string over `alphabet` (rejection sampling, so no character is favoured). */
export function randomString(alphabet, length, bytes = randomBytes) {
  const limit = 256 - (256 % alphabet.length);
  let out = "";
  while (out.length < length) {
    for (const byte of bytes(length * 2)) {
      if (byte < limit) out += alphabet[byte % alphabet.length];
      if (out.length === length) break;
    }
  }
  return out;
}

export function newTrackingCode(bytes = randomBytes) {
  const raw = randomString(TRACKING_ALPHABET, TRACKING_LENGTH, bytes);
  return formatTrackingCode(raw);
}

/** Canonical form (8 characters, no separator) or null when it cannot be a code. */
export function normalizeTrackingCode(code) {
  const raw = String(code ?? "").replace(/[\s-]/gu, "").toUpperCase();
  if (raw.length !== TRACKING_LENGTH || [...raw].some((ch) => !TRACKING_ALPHABET.includes(ch))) return null;
  return raw;
}

export function formatTrackingCode(code) {
  const raw = normalizeTrackingCode(code) ?? code;
  return `${raw.slice(0, 4)}-${raw.slice(4)}`;
}

export function newPublicId(bytes = randomBytes) {
  return randomString(PUBLIC_ID_ALPHABET, PUBLIC_ID_LENGTH, bytes);
}

function hex(buffer) {
  return [...new Uint8Array(buffer)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export async function sha256Hex(text) {
  return hex(await crypto.subtle.digest("SHA-256", encoder.encode(text)));
}

/** sha256 of the canonical code, the only form stored; null when it cannot be a code. */
export async function trackingHash(code) {
  const raw = normalizeTrackingCode(code);
  return raw ? sha256Hex(raw) : null;
}

/** HMAC-SHA256 of `message` under a hex key, as hex. */
export async function hmacHex(keyHex, message) {
  const key = await crypto.subtle.importKey(
    "raw", encoder.encode(keyHex), { name: "HMAC", hash: "SHA-256" }, false, ["sign"],
  );
  return hex(await crypto.subtle.sign("HMAC", key, encoder.encode(message)));
}

export function randomHex(nBytes, bytes = randomBytes) {
  return hex(bytes(nBytes));
}
