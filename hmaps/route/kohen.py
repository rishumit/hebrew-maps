"""אזורי קברים שכהנים נמנעים מהם – פוליגונים (GeoJSON) במקום מעגלים.

המודול עצמאי (numpy בלבד) ואינו תלוי ב-avoid.py, כדי שיהיה קל לחבר אותו.

קשת בגרף נחשבת "באזור" אם אחד הקטעים בצורה המלאה שלה (geom) נוגע בפוליגון:
קצה בתוכו, או חיתוך עם גבולו. בדיקה של אמצע הקשת בלבד הייתה מפספסת קשתות ארוכות
שחוצות פוליגון צר.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

# רמות החומרה ב-API: "א" ודאי, "ב" ספק, "ג" חשש קל; "A" נחשב כ-"א".
LEVEL_ALIASES = {"A": "א"}
ALL_LEVELS = frozenset({"א", "ב", "ג"})
#: קטע קצר = רוחב lon עד ~1.1 ק"מ; ארוכים יותר נבדקים בנפרד
SHORT_SEG_DEG = 0.01
CHUNK = 1_000_000


def _norm_level(v) -> str:
    v = str(v or "").strip()
    return LEVEL_ALIASES.get(v, v)


def _ring_arrays(ring) -> tuple[np.ndarray, np.ndarray]:
    a = np.asarray(ring, dtype=np.float64)
    return a[:, 0], a[:, 1]


def _points_in_ring(px: np.ndarray, py: np.ndarray, rx: np.ndarray, ry: np.ndarray) -> np.ndarray:
    """ray-casting לטבעת אחת (סגורה או לא)."""
    inside = np.zeros(px.shape, dtype=bool)
    n = len(rx)
    j = n - 1
    for i in range(n):
        yi, yj = ry[i], ry[j]
        if yi != yj:
            cond = (yi > py) != (yj > py)
            if cond.any():
                xcross = (rx[j] - rx[i]) * (py - yi) / (yj - yi) + rx[i]
                inside ^= cond & (px < xcross)
        j = i
    return inside


def _segments_cross_ring(ax, ay, bx, by, rx, ry) -> np.ndarray:
    """האם הקטע A-B חותך אחד מקטעי הטבעת. ax..by הם וקטורים באורך k."""
    k = len(ax)
    hit = np.zeros(k, dtype=bool)
    cx, cy = rx, ry
    dx, dy = np.roll(rx, -1), np.roll(ry, -1)
    m = len(cx)
    # עיבוד בחתיכות כדי לא לנפח זיכרון (k × m)
    step = max(1, 2_000_000 // max(m, 1))
    for s in range(0, k, step):
        sl = slice(s, s + step)
        x1, y1 = ax[sl, None], ay[sl, None]
        x2, y2 = bx[sl, None], by[sl, None]
        d1 = (x2 - x1) * (cy - y1) - (y2 - y1) * (cx - x1)
        d2 = (x2 - x1) * (dy - y1) - (y2 - y1) * (dx - x1)
        d3 = (dx - cx) * (y1 - cy) - (dy - cy) * (x1 - cx)
        d4 = (dx - cx) * (y2 - cy) - (dy - cy) * (x2 - cx)
        crossing = (d1 * d2 < 0) & (d3 * d4 < 0)
        hit[sl] = crossing.any(axis=1)
    return hit


class KohenAreas:
    """אוסף פוליגונים אסורים. כל פוליגון: טבעת חיצונית + חורים (אופציונלי)."""

    def __init__(self, polygons: list[list[list[list[float]]]]):
        # polygons[i] = [outer_ring, hole1, ...]; כל טבעת = [[lon, lat], ...]
        self.polygons = [p for p in polygons if p and len(p[0]) >= 4]
        self._rings = [[_ring_arrays(r) for r in poly] for poly in self.polygons]
        self.bboxes = np.array(
            [[rx.min(), ry.min(), rx.max(), ry.max()] for (rx, ry), *_ in self._rings],
            dtype=np.float64,
        ).reshape(-1, 4)

    def __len__(self) -> int:
        return len(self.polygons)

    # ---- טעינה ----

    @classmethod
    def from_geojson(cls, fc: dict, levels: frozenset[str] | None = None) -> "KohenAreas":
        levels = levels or ALL_LEVELS
        polys = []
        for f in (fc or {}).get("features", []):
            g = f.get("geometry") or {}
            lvl = _norm_level((f.get("properties") or {}).get("level"))
            if lvl and lvl not in levels:
                continue
            if g.get("type") == "Polygon":
                polys.append(g["coordinates"])
            elif g.get("type") == "MultiPolygon":
                polys.extend(g["coordinates"])
        return cls(polys)

    @classmethod
    def load(cls, path: Path, levels: frozenset[str] | None = None) -> "KohenAreas | None":
        """קורא את המפתח "kohen" מתוך avoid_areas.json. None אם אין."""
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        fc = data.get("kohen")
        if not fc or not fc.get("features"):
            return None
        areas = cls.from_geojson(fc, levels)
        return areas if len(areas) else None

    # ---- בדיקות ----

    def contains(self, lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
        """מסכה: האם כל נקודה בתוך פוליגון כלשהו."""
        lon = np.asarray(lon, dtype=np.float64)
        lat = np.asarray(lat, dtype=np.float64)
        out = np.zeros(lon.shape, dtype=bool)
        if not len(self) or not lon.size:
            return out
        order = np.argsort(lon, kind="stable")
        slon = lon[order]
        for (x0, y0, x1, y1), rings in zip(self.bboxes, self._rings):
            lo = np.searchsorted(slon, x0, "left")
            hi = np.searchsorted(slon, x1, "right")
            if hi <= lo:
                continue
            idx = order[lo:hi]
            idx = idx[(lat[idx] >= y0) & (lat[idx] <= y1)]
            if not idx.size:
                continue
            px, py = lon[idx], lat[idx]
            ins = _points_in_ring(px, py, *rings[0])
            for hole in rings[1:]:
                ins &= ~_points_in_ring(px, py, *hole)
            out[idx[ins]] = True
        return out

    def hits_segments(self, lon1, lat1, lon2, lat2) -> np.ndarray:
        """מסכה לקטעים: קצה בתוך פוליגון, או חיתוך עם גבול פוליגון.

        קטעים קצרים (רוב הגרף) נבחרים לכל פוליגון בחיפוש בינארי על lon המינימלי;
        קטעים ארוכים (מעט מאוד) נבדקים מול ה-bounding box ישירות.
        """
        lon1, lat1, lon2, lat2 = (np.asarray(a, dtype=np.float64) for a in (lon1, lat1, lon2, lat2))
        out = np.zeros(lon1.shape, dtype=bool)
        if not len(self) or not lon1.size:
            return out
        sxmin, sxmax = np.minimum(lon1, lon2), np.maximum(lon1, lon2)
        symin, symax = np.minimum(lat1, lat2), np.maximum(lat1, lat2)
        wide = (sxmax - sxmin) > SHORT_SEG_DEG
        short_idx = np.flatnonzero(~wide)
        long_idx = np.flatnonzero(wide)
        order = short_idx[np.argsort(sxmin[short_idx], kind="stable")]
        smin_sorted = sxmin[order]
        for (x0, y0, x1, y1), rings in zip(self.bboxes, self._rings):
            lo = np.searchsorted(smin_sorted, x0 - SHORT_SEG_DEG, "left")
            hi = np.searchsorted(smin_sorted, x1, "right")
            cand = order[lo:hi]
            if long_idx.size:
                cand = np.concatenate([cand, long_idx])
            cand = cand[(sxmax[cand] >= x0) & (sxmin[cand] <= x1) & (symax[cand] >= y0) & (symin[cand] <= y1)]
            cand = cand[~out[cand]]
            if not cand.size:
                continue
            ax, ay, bx, by = lon1[cand], lat1[cand], lon2[cand], lat2[cand]
            ins = _points_in_ring(ax, ay, *rings[0]) | _points_in_ring(bx, by, *rings[0])
            for hole in rings[1:]:
                ins &= ~(_points_in_ring(ax, ay, *hole) & _points_in_ring(bx, by, *hole))
            rest = ~ins
            if rest.any():
                ins[rest] = _segments_cross_ring(ax[rest], ay[rest], bx[rest], by[rest], *rings[0])
            out[cand[ins]] = True
        return out

    def edge_mask(self, g: dict) -> np.ndarray:
        """מסכה לכל קשתות הגרף: האם הצורה המלאה של הקשת (geom) נוגעת באזור."""
        gs = np.asarray(g["geom_start"], dtype=np.int64)
        geom = g["geom"]
        n_edges = len(gs) - 1
        mask = np.zeros(n_edges, dtype=bool)
        if not len(self) or len(geom) < 2:
            return mask
        idx = np.arange(len(geom) - 1, dtype=np.int64)
        valid = np.ones(len(idx), dtype=bool)
        last = gs[1:] - 1  # the last point of every edge: no segment starts there
        valid[last[(last >= 0) & (last < len(idx))]] = False
        idx = idx[valid]
        # in chunks: a national graph has millions of segments (float64 copies would be hundreds of MB)
        for s in range(0, len(idx), CHUNK):
            ci = idx[s:s + CHUNK]
            hit = self.hits_segments(geom[ci, 0].astype(np.float64), geom[ci, 1].astype(np.float64),
                                     geom[ci + 1, 0].astype(np.float64), geom[ci + 1, 1].astype(np.float64))
            mask[np.searchsorted(gs, ci[hit], side="right") - 1] = True
        return mask
