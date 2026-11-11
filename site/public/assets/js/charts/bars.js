// Bar charts built from HTML (no library): diverging bars around zero, 100% stacked bars and
// the single segmented bar. Positions go in CSS custom properties (CSSOM, allowed by the CSP).
import { h } from "../lib/dom.js";
import { barExtent, divergingTicks, position } from "./scale.js";
import { setTip } from "./interact.js";

const pct = (value) => `${Number(value.toFixed(3))}%`;

function mark(tag, { cls, key, label, tip }, ...children) {
  const node = h(tag, { class: cls, "data-mark": true, "data-key": key, role: "img", "aria-label": label }, ...children);
  setTip(node, tip);
  return node;
}

function chartRoot(cls, { name, hintId }, ...children) {
  return h("div", { class: cls, role: "group", "aria-label": name, "aria-describedby": hintId }, ...children);
}

/**
 * Horizontal bars that grow left (negative) or right (positive) from zero on [-domain, domain].
 * text: {value(row), sub(row), label(row), tip(row), tick(value)}.
 */
export function divergingBars({ rows, domain, name, hintId, text }) {
  const zero = pct(position(0, domain));
  const rowNodes = rows.map((row) => {
    const { start, size } = barExtent(row.enps, domain);
    const bar = h("span", { class: `divbars__bar divbars__bar--${row.enps < 0 ? "neg" : "pos"}`, "data-anchor": true });
    bar.style.setProperty("--start", pct(start));
    bar.style.setProperty("--size", pct(size));
    const plot = h("span", { class: "divbars__plot" }, size > 0 ? bar : null);
    plot.style.setProperty("--zero", zero);
    return mark("div", {
      cls: `divbars__row${row.merged ? " divbars__row--merged" : ""}`,
      key: row.key, label: text.label(row), tip: text.tip(row),
    },
    h("span", { class: "divbars__label" },
      h("span", { class: "divbars__name" }, row.label),
      h("span", { class: "divbars__sub" }, text.sub(row))),
    h("span", { class: "divbars__value" }, text.value(row)),
    plot);
  });
  const ticks = h("span", { class: "divbars__ticks" }, divergingTicks(domain).map((value) => {
    const tick = h("span", { class: "divbars__tick" }, text.tick(value));
    tick.style.setProperty("--at", pct(position(value, domain)));
    return tick;
  }));
  const axis = h("div", { class: "divbars__axis", "aria-hidden": "true" },
    h("span", { class: "divbars__axis-pad" }), ticks);
  return chartRoot("divbars", { name, hintId }, rowNodes, axis);
}

/** Segments of one 100% bar: flex-grow carries each count, zero parts draw nothing. */
function segments(parts, cls) {
  return parts.filter((part) => part.count > 0).map((part) => {
    const seg = h("span", { class: `${cls} viz-fill--${part.key}` });
    seg.style.flexGrow = String(part.count);
    return seg;
  });
}

/**
 * One 100% stacked bar per row (sentiment of each theme), all on the same width.
 * Each row: {key, label, parts: [{key, count}]}; text: {sub(row), label(row), tip(row)}.
 */
export function stackedBars({ rows, name, hintId, text }) {
  const rowNodes = rows.map((row) => mark("div", {
    cls: "stackbars__row", key: row.key, label: text.label(row), tip: text.tip(row),
  },
  h("span", { class: "stackbars__label" },
    h("span", { class: "stackbars__name" }, row.label),
    h("span", { class: "stackbars__sub" }, text.sub(row))),
  h("span", { class: "stackbars__bar", "data-anchor": true }, segments(row.parts, "stackbars__seg"))));
  return chartRoot("stackbars", { name, hintId }, rowNodes);
}

/**
 * One whole split into parts (promoters, passives, detractors). Every part is a mark.
 * parts: [{key, count, share}]; text: {label(part), tip(part)}.
 */
export function segmentedBar({ parts, name, hintId, text }) {
  const nodes = parts.filter((part) => part.count > 0).map((part) => {
    const seg = mark("span", {
      cls: `segbar__seg viz-fill--${part.key}`, key: part.key, label: text.label(part), tip: text.tip(part),
    });
    seg.style.flexGrow = String(part.count);
    return seg;
  });
  return chartRoot("segbar", { name, hintId }, nodes);
}
