"""Axis-aligned rectangle geometry used by screening-frame evaluation."""

from __future__ import annotations

from bisect import bisect_left, bisect_right

Rect = tuple[float, float, float, float]

EPS = 1e-9


def normalize(rect: Rect) -> Rect | None:
    x0, y0, x1, y1 = rect
    if x1 - x0 <= EPS or y1 - y0 <= EPS:
        return None
    return x0, y0, x1, y1


def contains(outer: Rect, inner: Rect) -> bool:
    return (
        outer[0] <= inner[0] + EPS
        and outer[1] <= inner[1] + EPS
        and inner[2] <= outer[2] + EPS
        and inner[3] <= outer[3] + EPS
    )


def inter_area(a: Rect, b: Rect) -> float:
    width = min(a[2], b[2]) - max(a[0], b[0])
    height = min(a[3], b[3]) - max(a[1], b[1])
    if width <= EPS or height <= EPS:
        return 0.0
    return width * height


def _grid(rects: list[Rect]) -> tuple[list[float], list[float]]:
    xs = sorted({value for rect in rects for value in (rect[0], rect[2])})
    ys = sorted({value for rect in rects for value in (rect[1], rect[3])})
    return xs, ys


def _covered(rects: list[Rect], xs: list[float], ys: list[float]) -> set[tuple[int, int]]:
    cells: set[tuple[int, int]] = set()
    for i in range(len(xs) - 1):
        cx = (xs[i] + xs[i + 1]) / 2
        for j in range(len(ys) - 1):
            cy = (ys[j] + ys[j + 1]) / 2
            if any(rect[0] < cx < rect[2] and rect[1] < cy < rect[3] for rect in rects):
                cells.add((i, j))
    return cells


def _emit(cells: set[tuple[int, int]], xs: list[float], ys: list[float]) -> list[Rect]:
    """Merge grid cells into disjoint rectangles (no area counted twice)."""
    runs: dict[tuple[int, int], list[int]] = {}
    for j in range(len(ys) - 1):
        i = 0
        while i < len(xs) - 1:
            if (i, j) not in cells:
                i += 1
                continue
            end = i
            while (end + 1, j) in cells:
                end += 1
            runs.setdefault((i, end), []).append(j)
            i = end + 1
    result: list[Rect] = []
    for (start, end), bands in sorted(runs.items()):
        bands.sort()
        first = last = bands[0]
        for band in bands[1:]:
            if band == last + 1:
                last = band
                continue
            result.append((xs[start], ys[first], xs[end + 1], ys[last + 1]))
            first = last = band
        result.append((xs[start], ys[first], xs[end + 1], ys[last + 1]))
    return result


def union_cells(rects: list[Rect]) -> list[Rect]:
    """Disjoint rectangles covering exactly the union of *rects*."""
    usable = [rect for rect in (normalize(rect) for rect in rects) if rect is not None]
    if not usable:
        return []
    xs, ys = _grid(usable)
    return _emit(_covered(usable, xs, ys), xs, ys)


def subtract(base: list[Rect], cuts: list[Rect]) -> list[Rect]:
    """Part of the union of *base* not covered by the union of *cuts*."""
    usable = [rect for rect in (normalize(rect) for rect in base) if rect is not None]
    if not usable:
        return []
    usable_cuts = [rect for rect in (normalize(rect) for rect in cuts) if rect is not None]
    xs, ys = _grid(usable + usable_cuts)
    cells = _covered(usable, xs, ys)
    if usable_cuts:
        cells -= _covered(usable_cuts, xs, ys)
    return _emit(cells, xs, ys)


class WeightGrid:
    """Piecewise-constant weight density with fast rectangle integrals.

    *rects* are disjoint rectangles from :func:`union_cells`, each carrying a weight
    per unit area. ``integrate`` returns the weighted area inside a query rectangle.
    """

    def __init__(self, rects: list[tuple[Rect, float]]) -> None:
        self.xs = sorted({value for rect, _ in rects for value in (rect[0], rect[2])})
        self.ys = sorted({value for rect, _ in rects for value in (rect[1], rect[3])})
        nx, ny = max(len(self.xs) - 1, 0), max(len(self.ys) - 1, 0)
        self.dx = [self.xs[i + 1] - self.xs[i] for i in range(nx)]
        self.dy = [self.ys[j + 1] - self.ys[j] for j in range(ny)]
        density = [[0.0] * ny for _ in range(nx)]
        for rect, weight in rects:
            i0 = bisect_left(self.xs, rect[0] - EPS)
            i1 = bisect_right(self.xs, rect[2] + EPS) - 1
            j0 = bisect_left(self.ys, rect[1] - EPS)
            j1 = bisect_right(self.ys, rect[3] + EPS) - 1
            for i in range(i0, min(i1, nx)):
                row = density[i]
                for j in range(j0, min(j1, ny)):
                    row[j] += weight
        self.density = density
        # 2-D prefix of weighted areas.
        self.prefix = [[0.0] * (ny + 1) for _ in range(nx + 1)]
        for i in range(nx):
            for j in range(ny):
                self.prefix[i + 1][j + 1] = (
                    self.prefix[i][j + 1]
                    + self.prefix[i + 1][j]
                    - self.prefix[i][j]
                    + density[i][j] * self.dx[i] * self.dy[j]
                )
        # Column/row prefixes for partially covered boundary cells.
        self.col_prefix = [[0.0] * (ny + 1) for _ in range(nx)]
        for i in range(nx):
            for j in range(ny):
                self.col_prefix[i][j + 1] = (
                    self.col_prefix[i][j] + density[i][j] * self.dy[j]
                )
        self.row_prefix = [[0.0] * (nx + 1) for _ in range(ny)]
        for j in range(ny):
            for i in range(nx):
                self.row_prefix[j][i + 1] = (
                    self.row_prefix[j][i] + density[i][j] * self.dx[i]
                )
        # Contiguous row masks of non-empty cells make ``occupied`` a quick range test.
        self.masks = [0] * nx
        for i in range(nx):
            mask = 0
            row = density[i]
            for j in range(ny):
                if row[j] > EPS:
                    mask |= 1 << j
            self.masks[i] = mask
        self._range_cache: dict[tuple[int, int], int] = {}

    def _cumulative(self, x: float, y: float) -> float:
        nx, ny = len(self.dx), len(self.dy)
        if nx == 0 or ny == 0:
            return 0.0
        x = min(max(x, self.xs[0]), self.xs[-1])
        y = min(max(y, self.ys[0]), self.ys[-1])
        i = min(bisect_right(self.xs, x) - 1, nx - 1) if x > self.xs[0] else -1
        j = min(bisect_right(self.ys, y) - 1, ny - 1) if y > self.ys[0] else -1
        if i < 0 or j < 0:
            return 0.0
        total = self.prefix[i][j]
        total += (x - self.xs[i]) * self.col_prefix[i][j]
        total += (y - self.ys[j]) * self.row_prefix[j][i]
        total += self.density[i][j] * (x - self.xs[i]) * (y - self.ys[j])
        return total

    def integrate(self, rect: Rect) -> float:
        x0, y0, x1, y1 = rect
        if x1 - x0 <= EPS or y1 - y0 <= EPS:
            return 0.0
        xs, ys, dx, dy = self.xs, self.ys, self.dx, self.dy
        nx, ny = len(dx), len(dy)
        if nx == 0 or ny == 0:
            return 0.0
        x1c = min(max(x1, xs[0]), xs[-1])
        x0c = min(max(x0, xs[0]), xs[-1])
        y1c = min(max(y1, ys[0]), ys[-1])
        y0c = min(max(y0, ys[0]), ys[-1])
        i1 = min(bisect_right(xs, x1c) - 1, nx - 1)
        i0 = min(bisect_right(xs, x0c) - 1, nx - 1)
        j1 = min(bisect_right(ys, y1c) - 1, ny - 1)
        j0 = min(bisect_right(ys, y0c) - 1, ny - 1)
        prefix, col, row, density = self.prefix, self.col_prefix, self.row_prefix, self.density

        def cum(i: int, j: int, x: float, y: float) -> float:
            if i < 0 or j < 0:
                return 0.0
            return (
                prefix[i][j]
                + (x - xs[i]) * col[i][j]
                + (y - ys[j]) * row[j][i]
                + density[i][j] * (x - xs[i]) * (y - ys[j])
            )

        return (
            cum(i1, j1, x1c, y1c)
            - cum(i0, j1, x0c, y1c)
            - cum(i1, j0, x1c, y0c)
            + cum(i0, j0, x0c, y0c)
        )

    def occupied(self, rect: Rect) -> bool:
        """Whether any positive weight lies inside *rect* (touching edges does not count)."""
        x0, y0, x1, y1 = rect
        if x1 - x0 <= EPS or y1 - y0 <= EPS:
            return False
        nx, ny = len(self.dx), len(self.dy)
        if nx == 0 or ny == 0:
            return False
        xs, ys = self.xs, self.ys
        i0 = max(bisect_right(xs, x0 + EPS) - 1, 0)
        i1 = min(bisect_left(xs, x1 - EPS) - 1, nx - 1)
        if i0 > i1:
            return False
        j0 = max(bisect_right(ys, y0 + EPS) - 1, 0)
        j1 = min(bisect_left(ys, y1 - EPS) - 1, ny - 1)
        if j0 > j1:
            return False
        band = ((1 << (j1 + 1)) - 1) ^ ((1 << j0) - 1)
        key = (i0, i1)
        cached = self._range_cache.get(key)
        if cached is None:
            cached = 0
            for i in range(i0, i1 + 1):
                cached |= self.masks[i]
            self._range_cache[key] = cached
        return bool(cached & band)


