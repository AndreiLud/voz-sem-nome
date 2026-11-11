"""Minimum group rule (privacy.min_group_size, k).

One dimension (areas or tenure bands): groups below k are never listed. They are merged
into one unnamed "other" bucket. When that bucket would itself be below k (but above 0),
the smallest visible group is merged into it until it reaches k. The bucket therefore
holds either nothing or at least two non-empty groups, so subtracting the visible groups
from the total never gives the size of a single hidden group.

Cross table (area x tenure): cells below k are suppressed, every cell in the row of a
hidden area or the column of a hidden tenure band is suppressed, and complementary
suppression then makes sure no row or column has exactly one suppressed cell (the next
smallest cell of that line is suppressed too).
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

Cell = tuple[str, str]


@dataclass(frozen=True)
class Partition:
    visible: tuple[str, ...]  # groups listed by name, in input order
    merged: tuple[str, ...]   # non-empty groups inside the "other" bucket, in input order
    other_n: int
    k: int

    @property
    def other_shown(self) -> bool:
        """The bucket is published only when it reaches k (never when the total is below k)."""
        return self.other_n >= self.k


def partition_groups(counts: Mapping[str, int], k: int) -> Partition:
    """Split groups into visible ones and the merged "other" bucket."""
    if k < 1:
        raise ValueError("k must be at least 1")
    order = list(counts)
    visible = [code for code in order if counts[code] >= k]
    merged = [code for code in order if 0 < counts[code] < k]
    other = sum(counts[code] for code in merged)
    while 0 < other < k and visible:
        smallest = min(visible, key=lambda code: (counts[code], order.index(code)))
        visible.remove(smallest)
        merged.append(smallest)
        other += counts[smallest]
    merged.sort(key=order.index)
    return Partition(tuple(visible), tuple(merged), other, k)


def suppress_cells(
    counts: Mapping[Cell, int],
    rows: Sequence[str],
    cols: Sequence[str],
    k: int,
    *,
    hidden_rows: Iterable[str] = (),
    hidden_cols: Iterable[str] = (),
) -> set[Cell]:
    """Cells of the rows x cols table that must not be shown."""
    hidden_r, hidden_c = set(hidden_rows), set(hidden_cols)

    def size(cell: Cell) -> int:
        return counts.get(cell, 0)

    suppressed = {
        (r, c) for r in rows for c in cols
        if size((r, c)) < k or r in hidden_r or c in hidden_c
    }
    lines = [[(r, c) for c in cols] for r in rows] + [[(r, c) for r in rows] for c in cols]
    changed = True
    while changed:
        changed = False
        for line in lines:
            if sum(cell in suppressed for cell in line) != 1:
                continue
            candidates = [cell for cell in line if cell not in suppressed]
            if candidates:
                suppressed.add(min(candidates, key=lambda cell: (size(cell), line.index(cell))))
                changed = True
    return suppressed
