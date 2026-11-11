// One polite live region per page for status messages that are not visible elsewhere.

const ID = "live-polite";

export function ensureLiveRegion(doc = document) {
  let node = doc.getElementById(ID);
  if (!node) {
    node = doc.createElement("div");
    node.id = ID;
    node.className = "visually-hidden";
    node.setAttribute("aria-live", "polite");
    node.setAttribute("aria-atomic", "true");
    doc.body.append(node);
  }
  return node;
}

let timer = 0;

/** Announce `message` politely. Repeated calls within 150 ms keep only the last one. */
export function announce(message, doc = document) {
  const node = ensureLiveRegion(doc);
  clearTimeout(timer);
  timer = setTimeout(() => {
    node.textContent = "";
    // A new text node on the next frame makes screen readers read repeated messages too.
    requestAnimationFrame(() => { node.textContent = message; });
  }, 150);
}
