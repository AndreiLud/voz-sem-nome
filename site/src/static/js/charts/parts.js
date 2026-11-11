// Pieces every chart shares: the table view, the legend and the text summary.
import { h } from "../lib/dom.js";

let uid = 0;
/** Unique id for aria references inside generated charts. */
export function nextId(prefix) {
  uid += 1;
  return `${prefix}-${uid}`;
}

/**
 * Table view of a chart, inside <details> ("Show table"). `columns` are {label, num?};
 * every row is {cells: [first, ...rest]} where the first cell is the row header.
 * `key` lets the dashboard keep the open state across redraws.
 */
export function tableView({ key, summary, caption, columns, rows, open = false, stack = true }) {
  const captionId = nextId("tcap");
  const head = h("tr", {}, columns.map((col) => h("th", { scope: "col", class: col.num ? "num" : null }, col.label)));
  const body = rows.map((row) => h("tr", {}, row.cells.map((cell, i) => (i === 0
    ? h("th", { scope: "row" }, cell)
    : h("td", { class: columns[i].num ? "num" : null, "data-label": columns[i].label }, cell)))));
  const table = h("table", { class: stack ? "table table--stack" : "table" },
    h("caption", { id: captionId }, caption),
    h("thead", {}, head),
    h("tbody", {}, body));
  return h("details", { class: "details viz-table", "data-table": key, open },
    h("summary", {}, summary),
    h("div", { class: "table-wrap", role: "region", tabindex: "0", "aria-labelledby": captionId }, table));
}

/** Legend for series drawn as filled marks: a swatch beside each name, in drawing order. */
export function legend(items, label) {
  return h("ul", { class: "viz-legend", "aria-label": label },
    items.map((item) => h("li", {},
      h("span", { class: `viz-swatch viz-fill--${item.key}`, "aria-hidden": "true" }),
      h("span", {}, item.label),
      item.extra ? h("span", { class: "viz-legend__extra" }, item.extra) : null)));
}

/** One sentence that says what the chart shows, above it. */
export function summaryLine(text) {
  return text ? h("p", { class: "viz-summary" }, text) : null;
}

/** Visually hidden hint about the arrow keys, referenced by every chart's aria-describedby. */
export function keysHint(id, text) {
  return h("p", { id, class: "visually-hidden" }, text);
}
