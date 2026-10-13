"""Elevation lookups from the terrain-RGB tiles (data/tiles/terrain*.mbtiles).

The same Terrarium PNG tiles that drive the hillshade are decoded here (pure
numpy, see hmaps.core.dem), so no separate elevation dataset is needed.
"""

from __future__ import annotations

import math
import threading
from collections import OrderedDict

import numpy as np

from ..core import dem, geo
from .store import Basemap

ATTRIBUTION = "Copernicus DEM © DLR/Airbus, ESA"
#: elevation of an absent tile inside the covered area (open sea)
SEA_LEVEL = 0.0
MAX_POINTS = 1000
MAX_SAMPLES = 500


class Elevation:
    def __init__(self, tiles: Basemap, cache_tiles: int = 96):
        self.tiles = tiles
        self._cache: OrderedDict[tuple, np.ndarray | None] = OrderedDict()
        self._lock = threading.Lock()
        self._cache_size = cache_tiles
        self._mtime = None

    @property
    def available(self) -> bool:
        return self.tiles.available

    @property
    def zoom(self) -> int:
        try:
            return int(self.tiles.metadata.get("maxzoom", 12))
        except ValueError:
            return 12

    def _tile(self, z: int, x: int, y: int) -> np.ndarray | None:
        key = (z, x, y)
        with self._lock:
            if self._mtime != self.tiles._mtime:
                self._cache.clear()
                self._mtime = self.tiles._mtime
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
        data = self.tiles.tile(z, x, y)
        arr = dem.terrarium_decode(dem.png_decode(data)) if data else None
        with self._lock:
            self._cache[key] = arr
            while len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)
        return arr

    def _pixel(self, z: int, px: int, py: int) -> float:
        tx, ix = divmod(px, dem.TILE_SIZE)
        ty, iy = divmod(py, dem.TILE_SIZE)
        t = self._tile(z, tx, ty)
        return SEA_LEVEL if t is None else float(t[iy, ix])

    def at(self, lat: float, lon: float) -> float:
        """Bilinear elevation (m) at a point."""
        z = self.zoom
        fx, fy = dem.lonlat_to_tile_f(lon, lat, z)
        gx, gy = fx * dem.TILE_SIZE - 0.5, fy * dem.TILE_SIZE - 0.5
        x0, y0 = math.floor(gx), math.floor(gy)
        ax, ay = gx - x0, gy - y0
        v00, v10 = self._pixel(z, x0, y0), self._pixel(z, x0 + 1, y0)
        v01, v11 = self._pixel(z, x0, y0 + 1), self._pixel(z, x0 + 1, y0 + 1)
        return (v00 * (1 - ax) + v10 * ax) * (1 - ay) + (v01 * (1 - ax) + v11 * ax) * ay

    def points(self, pts: list[tuple[float, float]]) -> list[float]:
        return [round(self.at(lat, lon), 1) for lat, lon in pts]

    def profile(self, coords: list[tuple[float, float]], samples: int = 200) -> dict:
        """Elevation profile along a line of (lon, lat): equally spaced samples + ascent/descent."""
        pts = resample(coords, samples)
        dist = [d for d, _, _ in pts]
        raw = [self.at(lat, lon) for _, lon, lat in pts]
        ele = smooth(raw)
        up, down = climb(ele)
        return {
            "distance": round(dist[-1], 1) if dist else 0.0,
            "ascent": round(up), "descent": round(down),
            "min": round(min(ele)) if ele else None, "max": round(max(ele)) if ele else None,
            "start": round(ele[0]) if ele else None, "end": round(ele[-1]) if ele else None,
            "profile": [[round(d, 1), round(e, 1), round(lon, 6), round(lat, 6)] for (d, lon, lat), e in zip(pts, ele)],
        }


def resample(coords: list[tuple[float, float]], samples: int) -> list[tuple[float, float, float]]:
    """(distance_m, lon, lat) at ``samples`` equally spaced positions along the line."""
    if not coords:
        return []
    cum = [0.0]
    for (lo0, la0), (lo1, la1) in zip(coords, coords[1:]):
        cum.append(cum[-1] + geo.haversine(la0, lo0, la1, lo1))
    total = cum[-1]
    if total == 0 or len(coords) == 1:
        return [(0.0, coords[0][0], coords[0][1])]
    n = max(2, min(samples, int(total / 10) + 2))  # no finer than ~10 m
    out = []
    j = 0
    for i in range(n):
        d = total * i / (n - 1)
        while j < len(cum) - 2 and cum[j + 1] < d:
            j += 1
        seg = cum[j + 1] - cum[j]
        t = 0.0 if seg == 0 else (d - cum[j]) / seg
        lo = coords[j][0] + (coords[j + 1][0] - coords[j][0]) * t
        la = coords[j][1] + (coords[j + 1][1] - coords[j][1]) * t
        out.append((d, lo, la))
    return out


def smooth(values: list[float], window: int = 5) -> list[float]:
    """Median then mean filter: removes single-sample spikes (bridges, buildings in the DSM)."""
    if len(values) < 3:
        return list(values)
    a = np.asarray(values, dtype=np.float64)
    h = window // 2
    p = np.pad(a, h, mode="edge")
    med = np.median(np.lib.stride_tricks.sliding_window_view(p, window), axis=1)
    p = np.pad(med, 1, mode="edge")
    return list((p[:-2] + p[1:-1] + p[2:]) / 3.0)


def climb(values: list[float], threshold: float = 2.0) -> tuple[float, float]:
    """Total ascent/descent, ignoring wiggles smaller than ``threshold`` metres (hysteresis)."""
    if not values:
        return 0.0, 0.0
    up = down = 0.0
    ref = values[0]
    for v in values[1:]:
        if v - ref >= threshold:
            up += v - ref
            ref = v
        elif ref - v >= threshold:
            down += ref - v
            ref = v
    return up, down
