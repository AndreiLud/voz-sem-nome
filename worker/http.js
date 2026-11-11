// Small response helpers shared by the API routes.

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

/** Body passed through as text, with only the headers the browser needs. */
export function passText(status, text, { contentType, retryAfter, extra = {} } = {}) {
  const headers = {
    "Content-Type": contentType || JSON_TYPE,
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    ...extra,
  };
  if (retryAfter) headers["Retry-After"] = retryAfter;
  return new Response(text, { status, headers });
}

/** Address of the visitor as Cloudflare saw it. */
export function clientIp(request) {
  return request.headers.get("CF-Connecting-IP") || "unknown";
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
