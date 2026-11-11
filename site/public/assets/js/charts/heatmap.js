// Heatmap of themes (rows) by area (columns): sequential blue, the share printed in each cell.
// Wide grids scroll sideways inside their own container with the theme names kept in view.
import { h } from "../lib/dom.js";
import { heatBounds, heatClass, heatLevel } from "./scale.js";
import { setTip } from "./interact.js";

/**
 * matrix: from model.heatMatrix; rowLabel(code) names a theme.
 * text: {cell(cell), label(row, column, cell), tip(row, column, cell)}.
 */
export function heatmap({ matrix, rowLabel, name, hintId, text }) {
  const { columns, rows, scale } = matrix;
  const grid = h("div", { class: "heat", role: "group", "aria-label": name, "aria-describedby": hintId });
  grid.style.setProperty("--cols", String(columns.length));
  grid.append(h("span", { class: "heat__corner", "aria-hidden": "true" }));
  for (const col of columns) {
    grid.append(h("span", { class: "heat__colhead", "aria-hidden": "true" }, col.label));
  }
  for (const row of rows) {
    const theme = rowLabel(row.key);
    grid.append(h("span", { class: "heat__rowhead", "aria-hidden": "true" }, theme));
    row.cells.forEach((cell, i) => {
      const level = heatLevel(heatClass(cell.share, scale), scale.count);
      const node = h("span", {
        class: `heat__cell heat__cell--l${level}`, "data-mark": true, "data-anchor": true,
        "data-key": `${row.key}:${columns[i].key}`, role: "img",
        "aria-label": text.label(theme, columns[i], cell),
      }, text.cell(cell));
      setTip(node, text.tip(theme, columns[i], cell));
      grid.append(node);
    });
  }
  return h("div", { class: "heat-scroll" }, grid);
}

/** Scale legend: "none", then one swatch per class with its range. */
export function heatLegend(scale, { title, zero, range }) {
  const items = [h("li", {}, h("span", { class: "viz-swatch heat__cell--l0", "aria-hidden": "true" }), h("span", {}, zero))];
  heatBounds(scale).forEach(([lo, hi], i) => {
    const level = heatLevel(i + 1, scale.count);
    items.push(h("li", {}, h("span", { class: `viz-swatch heat__cell--l${level}`, "aria-hidden": "true" }),
      h("span", {}, range(lo, hi, i === 0))));
  });
  return h("div", { class: "heat-legend" },
    h("p", { class: "heat-legend__title" }, title),
    h("ul", { class: "viz-legend", "aria-label": title }, items));
}
