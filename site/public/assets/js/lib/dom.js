// Small DOM helpers. Text always goes in as text nodes, never as HTML.

/** h("p", {class: "note"}, "text", child) -> element. Attributes set to null/false are skipped. */
export function h(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (name === "class") node.className = value;
    else if (name === "dataset") Object.assign(node.dataset, value);
    else node.setAttribute(name, value === true ? "" : String(value));
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

/** Element with data-slot="name" inside root. */
export function slot(name, root = document) {
  return root.querySelector(`[data-slot="${name}"]`);
}

/** <svg class="icon"><use href="/assets/icons/sprite.svg#name"></svg>, hidden from AT. */
export function icon(name) {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("class", "icon");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  const use = document.createElementNS(ns, "use");
  use.setAttribute("href", `/assets/icons/sprite.svg#${name}`);
  svg.append(use);
  return svg;
}

const PLACEHOLDER = /\[([A-Za-z]+)\]/g;

/** One chip, same markup as site_content.chip() in the build. */
export function chip(code) {
  return h("span", { class: "chip", "data-placeholder": code },
    h("span", { class: "chip__bracket" }, "["), code, h("span", { class: "chip__bracket" }, "]"));
}

/**
 * Anonymized text as a fragment, with known placeholders ([PESSOA], [CPF]...) as chips.
 * `known` is a Set of codes (from taxonomy.placeholders).
 */
export function anonymizedText(text, known) {
  const fragment = document.createDocumentFragment();
  let last = 0;
  for (const match of String(text).matchAll(PLACEHOLDER)) {
    if (!known.has(match[1])) continue;
    fragment.append(text.slice(last, match.index), chip(match[1]));
    last = match.index + match[0].length;
  }
  fragment.append(String(text).slice(last));
  return fragment;
}

/** Badge for a critical type: icon + text, never color alone. */
export function criticalBadge(label) {
  return h("span", { class: "badge badge--critical" }, icon("alert"), h("span", {}, label));
}

/** "Sample data" badge. */
export function sampleBadge(label) {
  return h("p", { class: "badge badge--sample" }, icon("sample"), h("span", {}, label));
}
