"""Bulk point-in-polygon assignment (which city is this point in?).

Points are assigned to the most specific administrative area containing
them. Polygons are simplified first and the ray-casting test is vectorized
with numpy over all candidate points of a polygon, which keeps the whole of
Israel (~1M points, ~300 municipalities) in the order of a minute.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

import numpy as np

from ..core.geo import Coord, haversine

Polygon = list[list[Coord]]  # [outer, hole, ...]


def _perp_dist(p: Coord, a: Coord, b: Coord) -> float:
    (x, y), (x1, y1), (x2, y2) = p, a, b
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return math.hypot(x - x1, y - y1)
    t = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy)))
    return math.hypot(x - (x1 + t * dx), y - (y1 + t * dy))


def simplify(ring: Sequence[Coord], tolerance: float) -> list[Coord]:
    """Iterative Douglas–Peucker; keeps rings closed and valid (>= 4 points)."""
    n = len(ring)
    if n <= 5:
        return list(ring)
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        s, e = stack.pop()
        best, idx = 0.0, -1
        for i in range(s + 1, e):
            d = _perp_dist(ring[i], ring[s], ring[e])
            if d > best:
                best, idx = d, i
        if idx >= 0 and best > tolerance:
            keep[idx] = True
            stack.append((s, idx))
            stack.append((idx, e))
    out = [p for p, k in zip(ring, keep) if k]
    if len(out) < 4:
        step = max(1, n // 4)
        out = list(ring[::step])
        if out[-1] != ring[-1]:
            out.append(ring[-1])
    return out


@dataclass
class Area:
    name: str
    level: int
    polygons: list[Polygon]
    bbox: tuple[float, float, float, float]


def _ring_contains(px: np.ndarray, py: np.ndarray, ring: Sequence[Coord]) -> np.ndarray:
    inside = np.zeros(px.shape, dtype=bool)
    xs = np.fromiter((p[0] for p in ring), dtype=np.float64, count=len(ring))
    ys = np.fromiter((p[1] for p in ring), dtype=np.float64, count=len(ring))
    xj, yj = xs[-1], ys[-1]
    for xi, yi in zip(xs, ys):
        cond = (yi > py) != (yj > py)
        if cond.any():
            denom = (yj - yi) if yj != yi else 1e-30
            xcross = (xj - xi) * (py - yi) / denom + xi
            inside ^= cond & (px < xcross)
        xj, yj = xi, yi
    return inside


def polygons_contain(px: np.ndarray, py: np.ndarray, polygons: Sequence[Polygon]) -> np.ndarray:
    res = np.zeros(px.shape, dtype=bool)
    for rings in polygons:
        if not rings:
            continue
        inside = _ring_contains(px, py, rings[0])
        for hole in rings[1:]:
            if inside.any():
                inside &= ~_ring_contains(px, py, hole)
        res |= inside
    return res


class AreaIndex:
    """Assign names of containing areas to many points at once."""

    def __init__(self, tolerance_deg: float = 0.00015):  # ~15 m
        self.areas: list[Area] = []
        self.tolerance = tolerance_deg

    def add(self, name: str, level: int, polygons: list[Polygon]) -> None:
        simp: list[Polygon] = []
        for rings in polygons:
            r2 = [simplify(r, self.tolerance) for r in rings if len(r) >= 4]
            if r2:
                simp.append(r2)
        if not simp:
            return
        xs = [p[0] for rings in simp for p in rings[0]]
        ys = [p[1] for rings in simp for p in rings[0]]
        self.areas.append(Area(name, level, simp, (min(xs), min(ys), max(xs), max(ys))))

    def assign(self, lons: Sequence[float], lats: Sequence[float]) -> list[str | None]:
        """Name of the most specific (highest level, then smallest) area per point."""
        px = np.asarray(lons, dtype=np.float64)
        py = np.asarray(lats, dtype=np.float64)
        result: list[str | None] = [None] * len(px)
        done = np.zeros(px.shape, dtype=bool)

        def area_size(a: Area) -> float:
            return (a.bbox[2] - a.bbox[0]) * (a.bbox[3] - a.bbox[1])

        for area in sorted(self.areas, key=lambda a: (-a.level, area_size(a))):
            minx, miny, maxx, maxy = area.bbox
            cand = (~done) & (px >= minx) & (px <= maxx) & (py >= miny) & (py <= maxy)
            idx = np.nonzero(cand)[0]
            if idx.size == 0:
                continue
            inside = polygons_contain(px[idx], py[idx], area.polygons)
            hit = idx[inside]
            for i in hit.tolist():
                result[i] = area.name
            done[hit] = True
        return result


class NearestPlace:
    """Fallback: nearest settlement within a radius, using a coarse grid."""

    def __init__(self, places: Sequence[tuple[str, float, float, float]], cell: float = 0.05):
        # places: (name, lat, lon, radius_m)
        self.cell = cell
        self.grid: dict[tuple[int, int], list[tuple[str, float, float, float]]] = {}
        for p in places:
            key = (int(p[2] // cell), int(p[1] // cell))
            self.grid.setdefault(key, []).append(p)

    def find(self, lat: float, lon: float) -> str | None:
        cx, cy = int(lon // self.cell), int(lat // self.cell)
        best: tuple[float, str] | None = None
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for name, plat, plon, radius in self.grid.get((cx + dx, cy + dy), ()):
                    d = haversine(lat, lon, plat, plon)
                    if d <= radius and (best is None or d / radius < best[0]):
                        best = (d / radius, name)
        return best[1] if best else None
