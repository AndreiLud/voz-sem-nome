// String lookup with {field} filling. Keys are dotted paths into the page strings.

/** Replace {name} fields present in `values`; unknown fields are left as they are. */
export function fill(template, values = {}) {
  return String(template).replace(/\{(\w+)\}/g, (match, name) =>
    Object.prototype.hasOwnProperty.call(values, name) ? String(values[name]) : match);
}

/** Value at a dotted path, or undefined. */
export function lookup(tree, path) {
  return path.split(".").reduce((node, part) => (node == null ? undefined : node[part]), tree);
}

/**
 * Returns t(key, values): the filled string, or the key itself when it is missing
 * (visible in development, never a crash).
 */
export function createT(strings, defaults = {}) {
  return (key, values = {}) => {
    const value = lookup(strings, key);
    if (typeof value !== "string") {
      console.warn(`missing string: ${key}`);
      return key;
    }
    return fill(value, { ...defaults, ...values });
  };
}
