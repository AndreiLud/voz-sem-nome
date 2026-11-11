// Interaction shared by every chart: one tooltip and roving keyboard focus.
//
// Each mark ([data-mark]) is focusable and carries its full text in aria-label. The tooltip is
// visual only (aria-hidden): it follows the pointer, sits next to a mark that has keyboard
// focus, opens on tap, and closes with Escape. It never gates a value: every chart also has
// a table view. A chart is one tab stop; the arrow keys move between its marks.
import { h } from "../lib/dom.js";

const GAP = 12;
const EDGE = 8;
const contents = new WeakMap();
let tip = null;
let anchored = null; // mark the tooltip is attached to when shown by focus or tap

/**
 * Register what the tooltip shows for a mark:
 * {title, value, unit?, rows?: [{key?, text}], note?}. Text only, never HTML.
 */
export function setTip(mark, content) {
  contents.set(mark, content);
}

function ensureTip() {
  if (tip && tip.isConnected) return tip;
  tip = h("div", { class: "viz-tip", "aria-hidden": "true", hidden: true });
  document.body.append(tip);
  window.addEventListener("scroll", () => (anchored ? place(anchored) : hide()), { passive: true });
  window.addEventListener("resize", hide, { passive: true });
  document.addEventListener("pointerdown", (event) => {
    if (!event.target.closest || !event.target.closest("[data-mark]")) hide();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") hide();
  });
  return tip;
}

function render(content) {
  const node = ensureTip();
  const parts = [];
  if (content.title) parts.push(h("p", { class: "viz-tip__title" }, content.title));
  if (content.value !== undefined) {
    parts.push(h("p", { class: "viz-tip__value" }, h("strong", {}, content.value),
      content.unit ? h("span", {}, content.unit) : null));
  }
  if (content.rows && content.rows.length) {
    parts.push(h("ul", { class: "viz-tip__rows" }, content.rows.map((row) => h("li", {},
      row.key ? h("span", { class: `viz-tip__key viz-fill--${row.key}`, "aria-hidden": "true" }) : null,
      h("span", {}, row.text)))));
  }
  if (content.note) parts.push(h("p", { class: "viz-tip__note" }, content.note));
  node.replaceChildren(...parts);
  // Measure at the origin so the width never shrinks against the right edge of the viewport.
  node.style.left = "0px";
  node.style.top = "0px";
  node.hidden = false;
  return node;
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(value, max));
}

/** Next to the pointer, flipped to stay inside the viewport. */
function placeAtPoint(node, x, y) {
  const { width, height } = node.getBoundingClientRect();
  const left = x + GAP + width > window.innerWidth - EDGE ? x - GAP - width : x + GAP;
  const top = y + GAP + height > window.innerHeight - EDGE ? y - GAP - height : y + GAP;
  node.style.left = `${clamp(left, EDGE, window.innerWidth - width - EDGE)}px`;
  node.style.top = `${clamp(top, EDGE, window.innerHeight - height - EDGE)}px`;
}

/** Above the mark's anchor (its bar or cell), or below when there is no room above. */
function place(mark) {
  const node = ensureTip();
  const target = mark.querySelector("[data-anchor]") || mark;
  const rect = target.getBoundingClientRect();
  const { width, height } = node.getBoundingClientRect();
  const left = clamp(rect.left + rect.width / 2 - width / 2, EDGE, window.innerWidth - width - EDGE);
  let top = rect.top - height - GAP;
  if (top < EDGE) top = rect.bottom + GAP;
  node.style.left = `${left}px`;
  node.style.top = `${clamp(top, EDGE, window.innerHeight - height - EDGE)}px`;
}

function show(mark, point) {
  const content = contents.get(mark);
  if (!content) return;
  const node = render(content);
  setActive(mark);
  if (point) {
    anchored = null;
    placeAtPoint(node, point.x, point.y);
  } else {
    anchored = mark;
    place(mark);
  }
}

export function hide() {
  anchored = null;
  if (tip) tip.hidden = true;
  setActive(null);
}

let active = null;
function setActive(mark) {
  if (active && active !== mark) delete active.dataset.active;
  active = mark;
  if (mark) mark.dataset.active = "";
}

// ------------------------------------------------------------------ roving focus

function marksOf(root) {
  return [...root.querySelectorAll("[data-mark]")];
}

function focusMark(marks, index) {
  const target = marks[clamp(index, 0, marks.length - 1)];
  if (!target) return;
  for (const mark of marks) mark.tabIndex = mark === target ? 0 : -1;
  target.focus();
}

/** Index after a key press, or null when the key is not a navigation key. */
export function nextIndex(key, index, count, columns = 1) {
  const last = count - 1;
  const rowStart = index - (index % columns);
  switch (key) {
    case "ArrowRight": return columns > 1 ? Math.min(index + 1, rowStart + columns - 1, last) : Math.min(index + 1, last);
    case "ArrowLeft": return columns > 1 ? Math.max(index - 1, rowStart) : Math.max(index - 1, 0);
    case "ArrowDown": return index + columns <= last ? index + columns : index;
    case "ArrowUp": return index - columns >= 0 ? index - columns : index;
    case "Home": return columns > 1 ? rowStart : 0;
    case "End": return columns > 1 ? Math.min(rowStart + columns - 1, last) : last;
    default: return null;
  }
}

/**
 * Make `root` one tab stop whose arrow keys move between its marks, and wire the tooltip.
 * `columns` > 1 makes it a grid (heatmap): left/right move in a row, up/down in a column.
 * `focusKey` restores focus to the mark with that data-key after a redraw.
 */
export function bindChart(root, { columns = 1, focusKey = null } = {}) {
  ensureTip();
  const marks = marksOf(root);
  marks.forEach((mark, i) => { mark.tabIndex = i === 0 ? 0 : -1; });

  root.addEventListener("keydown", (event) => {
    const current = event.target.closest("[data-mark]");
    if (!current) return;
    const list = marksOf(root);
    const index = nextIndex(event.key, list.indexOf(current), list.length, columns);
    if (index === null) return;
    event.preventDefault();
    focusMark(list, index);
  });
  root.addEventListener("focusin", (event) => {
    const mark = event.target.closest("[data-mark]");
    if (!mark) return;
    for (const other of marksOf(root)) other.tabIndex = other === mark ? 0 : -1;
    show(mark, null);
  });
  root.addEventListener("focusout", (event) => {
    if (!root.contains(event.relatedTarget)) hide();
  });
  root.addEventListener("pointermove", (event) => {
    if (event.pointerType === "touch") return;
    const mark = event.target.closest("[data-mark]");
    if (mark && root.contains(mark)) show(mark, { x: event.clientX, y: event.clientY });
    else if (!anchored) hide();
  });
  root.addEventListener("pointerleave", (event) => {
    if (event.pointerType !== "touch" && !anchored) hide();
  });
  root.addEventListener("pointerdown", (event) => {
    if (event.pointerType !== "touch") return;
    const mark = event.target.closest("[data-mark]");
    if (mark) show(mark, null);
  });

  if (focusKey !== null) {
    const again = marks.find((mark) => mark.dataset.key === focusKey);
    if (again) focusMark(marks, marks.indexOf(again));
  }
}
