// Minimum group rule (port of src/enps/metrics/suppression.py), with k = privacy.min_group_size.
//
// One dimension (areas or tenure bands): groups below k are never listed. They are merged into
// one unnamed "other" bucket. When that bucket would itself be below k (but above 0), the
// smallest visible group is merged into it until it reaches k. The bucket therefore holds either
// nothing or at least two non-empty groups, so subtracting the visible groups from the total
// never gives the size of a single hidden group.
//
// Cross table (area x tenure): cells below k are suppressed, every cell in the row of a hidden
// area or the column of a hidden tenure band is suppressed, and complementary suppression then
// makes sure no row or column has exactly one suppressed cell (the next smallest cell of that
// line is suppressed too).

/** Key of a cross table cell in the Map and Set arguments of suppressCells. */
export const cellKey = (row, col) => `${row}\u0000${col}`;

/**
 * Split groups into visible ones and the merged "other" bucket.
 * @param {Map<string, number>} counts group sizes, in display order
 * @returns {{visible: string[], merged: string[], otherN: number, k: number, otherShown: boolean}}
 *   otherShown: the bucket is published only when it reaches k (never when the total is below k)
 */
export function partitionGroups(counts, k) {
  if (!(k >= 1)) throw new RangeError("k must be at least 1");
  const order = [...counts.keys()];
  const size = (code) => counts.get(code);
  const visible = order.filter((code) => size(code) >= k);
  const merged = order.filter((code) => size(code) > 0 && size(code) < k);
  let other = merged.reduce((sum, code) => sum + size(code), 0);
  while (other > 0 && other < k && visible.length > 0) {
    // First visible group with the smallest size (ties go to the earlier one in display order).
    const smallest = visible.reduce((best, code) => (size(code) < size(best) ? code : best));
    visible.splice(visible.indexOf(smallest), 1);
    merged.push(smallest);
    other += size(smallest);
  }
  merged.sort((a, b) => order.indexOf(a) - order.indexOf(b));
  return { visible, merged, otherN: other, k, otherShown: other >= k };
}

/**
 * Cells of the rows x cols table that must not be shown, as a Set of cellKey values.
 * @param {Map<string, number>} counts cell sizes keyed by cellKey (missing cells count 0)
 */
export function suppressCells(counts, rows, cols, k, { hiddenRows = [], hiddenCols = [] } = {}) {
  const hiddenR = new Set(hiddenRows);
  const hiddenC = new Set(hiddenCols);
  const size = (cell) => counts.get(cell) ?? 0;
  const suppressed = new Set();
  for (const r of rows) {
    for (const c of cols) {
      const cell = cellKey(r, c);
      if (size(cell) < k || hiddenR.has(r) || hiddenC.has(c)) suppressed.add(cell);
    }
  }
  const lines = [
    ...rows.map((r) => cols.map((c) => cellKey(r, c))),
    ...cols.map((c) => rows.map((r) => cellKey(r, c))),
  ];
  let changed = true;
  while (changed) {
    changed = false;
    for (const line of lines) {
      if (line.filter((cell) => suppressed.has(cell)).length !== 1) continue;
      const candidates = line.filter((cell) => !suppressed.has(cell));
      if (candidates.length > 0) {
        // Smallest open cell; ties go to the earlier one in the line.
        suppressed.add(candidates.reduce((best, cell) => (size(cell) < size(best) ? cell : best)));
        changed = true;
      }
    }
  }
  return suppressed;
}
