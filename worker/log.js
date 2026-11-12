// The only logging of the Worker. Workers Logs keep console output, so a log line carries an
// event name and short codes (error codes, status codes, counts), never a comment, a request
// or an address. Values that do not look like a code are replaced by "?".

const CODE = /^[A-Za-z0-9_.:-]{1,40}$/;

function safe(value) {
  if (typeof value === "number" || typeof value === "boolean" || value === null) return String(value);
  return typeof value === "string" && CODE.test(value) ? value : "?";
}

/** console.warn("event key=value ...") with every value reduced to a code. */
export function logEvent(event, fields = {}) {
  const parts = [safe(event), ...Object.entries(fields).map(([key, value]) => `${safe(key)}=${safe(value)}`)];
  console.warn(parts.join(" "));
}
