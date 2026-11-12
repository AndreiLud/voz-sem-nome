// Small request and response helpers shared by the API routes.

const JSON_TYPE = "application/json; charset=utf-8";

/** JSON response that no cache keeps. */
export function json(status, data, headers = {}) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "Content-Type": JSON_TYPE,
      "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff",
      ...headers,
    },
  });
}

/** The JSON error shape of the FastAPI apps: {"error": code, ...extra}. */
export function error(status, code, { headers = {}, ...extra } = {}) {
  return json(status, { error: code, ...extra }, headers);
}

export const notFound = () => error(404, "not_found");

/** 429 with the scope and the wait, like enps.api.public. */
export function rateLimited(scope, retryAfter) {
  return error(429, "rate_limited", {
    scope,
    retry_after: retryAfter,
    headers: { "Retry-After": String(retryAfter) },
  });
}

/** Address of the visitor as Cloudflare saw it. */
export function clientIp(request) {
  return request.headers.get("CF-Connecting-IP") || "unknown";
}

/** True when the request declares a JSON body. */
export function isJson(request) {
  return (request.headers.get("Content-Type") || "").toLowerCase().startsWith("application/json");
}

/**
 * Request body as text, reading at most `limit` bytes. Returns null when the body is
 * larger than that (declared or real) and throws TypeError when it is not valid UTF-8.
 */
export async function readLimited(request, limit) {
  const declared = Number(request.headers.get("Content-Length"));
  if (Number.isFinite(declared) && declared > limit) return null;
  if (!request.body) return "";
  const reader = request.body.getReader();
  const chunks = [];
  let size = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > limit) {
      await reader.cancel();
      return null;
    }
    chunks.push(value);
  }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
}

/**
 * The JSON object of a request: {value} or {response} with the error to send (415, 413 or
 * 422 with the same body as the FastAPI apps).
 */
export async function readJsonObject(request, limit) {
  if (!isJson(request)) return { response: error(415, "unsupported_media_type") };
  let value;
  try {
    const text = await readLimited(request, limit);
    if (text === null) return { response: error(413, "too_large") };
    value = JSON.parse(text);
  } catch {
    return { response: invalidBody() };
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) return { response: invalidBody() };
  return { value };
}

function invalidBody() {
  return error(422, "validation", { fields: { body: "must be a JSON object" } });
}
