"""Vector tiles generated from maps.db.

* ``poi`` tiles – the clickable POI overlay, importance-thinned per zoom and
  optionally clustered when the user filters by category;
* ``fallback`` basemap tiles – streets, settlements and boundaries drawn from
  our own data, used until the full OpenMapTiles basemap has been built.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict

from ..core import categories, geo
from . import mvt

#: zoom at which a POI of a given importance first appears
def display_zoom(importance: float) -> float:
    return max(8.0, min(17.0, 18.5 - importance * 11.0))


def _tile_query_bbox(z: int, x: int, y: int, buffer: float = 0.08) -> tuple[float, float, float, float]:
    minx, miny, maxx, maxy = geo.tile_bounds(z, x, y)
    bx, by = (maxx - minx) * buffer, (maxy - miny) * buffer
    return minx - bx, miny - by, maxx + bx, maxy + by


def poi_tile(con: sqlite3.Connection, z: int, x: int, y: int, cats: list[str] | None = None,
             kosher: bool = False, max_features: int = 1500) -> bytes:
    minx, miny, maxx, maxy = _tile_query_bbox(z, x, y)
    sql = ("SELECT p.id, p.name, p.category, p.sub, p.lat, p.lon, p.importance, p.kosher FROM pois_rtree t "
           "JOIN pois p ON p.id = t.id WHERE t.min_lon <= ? AND t.max_lon >= ? AND t.min_lat <= ? AND t.max_lat >= ?")
    args: list = [maxx, minx, maxy, miny]
    if cats:
        subs = [s for c in cats if c in categories.SUB_BY_ID for s in categories.expand_sub(c)]
        tops = [c for c in cats if c in categories.CATEGORY_BY_ID]
        cond = []
        if subs:
            cond.append(f"p.sub IN ({','.join('?' * len(subs))})")
            args += subs
        if tops:
            cond.append(f"p.category IN ({','.join('?' * len(tops))})")
            args += tops
        if cond:
            sql += " AND (" + " OR ".join(cond) + ")"
    if kosher:
        sql += " AND p.kosher = 1"
    if not cats:
        # unfiltered map: only what is important enough for this zoom
        sql += " AND p.importance >= ?"
        args.append(max(0.0, (18.5 - z) / 11.0) if z < 17 else 0.0)
    sql += " ORDER BY p.importance DESC LIMIT ?"
    args.append(20000 if cats and z < 14 else max_features)
    rows = [r for r in con.execute(sql, args).fetchall() if minx <= r[5] <= maxx and miny <= r[4] <= maxy]
    layer = mvt.Layer("pois")
    filtered_low_zoom = bool(cats) and z < 14
    if filtered_low_zoom:
        # grid clustering, 64 px cells
        cell = mvt.EXTENT // 8
        buckets: dict[tuple[int, int], list] = defaultdict(list)
        for r in rows:
            px, py = geo.lonlat_to_tile_pixel(r[5], r[4], z, x, y)
            buckets[(px // cell, py // cell)].append((px, py, r))
        for items in buckets.values():
            if len(items) == 1:
                px, py, r = items[0]
                _add_poi(layer, px, py, r)
            else:
                cx = sum(i[0] for i in items) // len(items)
                cy = sum(i[1] for i in items) // len(items)
                top = max(items, key=lambda i: i[2][6])[2]
                sub = categories.SUB_BY_ID[top[3]]
                layer.add_point(cx, cy, {"cluster": True, "count": len(items), "cat": top[2], "icon": sub.icon,
                                         "rank": float(len(items))})
    else:
        for r in rows:
            px, py = geo.lonlat_to_tile_pixel(r[5], r[4], z, x, y)
            _add_poi(layer, px, py, r)
    return mvt.encode_tile([layer])


def _add_poi(layer: mvt.Layer, px: int, py: int, r) -> None:
    sub = categories.SUB_BY_ID[r[3]]
    name = r[1] or ""
    layer.add_point(px, py, {
        "id": int(r[0]), "name": name, "cat": r[2], "sub": r[3], "icon": sub.icon,
        "rank": float(round(r[6], 3)), "kosher": bool(r[7]) if r[7] else None,
    }, fid=int(r[0]))


# ------------------------------------------------------------------ fallback basemap

_HIGHWAY_MINZOOM = {"motorway": 6, "trunk": 7, "primary": 9, "secondary": 11, "tertiary": 12}


def _simplify_px(pts: list[tuple[int, int]], tol: int = 8) -> list[tuple[int, int]]:
    if len(pts) <= 2:
        return pts
    out = [pts[0]]
    for p in pts[1:-1]:
        if abs(p[0] - out[-1][0]) + abs(p[1] - out[-1][1]) >= tol:
            out.append(p)
    out.append(pts[-1])
    return out


def fallback_tile(con: sqlite3.Connection, z: int, x: int, y: int) -> bytes:
    minx, miny, maxx, maxy = _tile_query_bbox(z, x, y, 0.05)
    layers = []
    # boundaries of settlements
    if z >= 9:
        bl = mvt.Layer("boundaries")
        for r in con.execute(
            "SELECT a.id, a.name, a.level, a.geom FROM areas_rtree t JOIN areas a ON a.id = t.id "
            "WHERE t.min_lon <= ? AND t.max_lon >= ? AND t.min_lat <= ? AND t.max_lat >= ? AND a.level BETWEEN 6 AND 8",
            (maxx, minx, maxy, miny),
        ):
            lines = []
            for poly in json.loads(r[3]):
                for ring in poly:
                    lines.append(_simplify_px([geo.lonlat_to_tile_pixel(lo, la, z, x, y) for lo, la in ring], 4 if z > 12 else 12))
            bl.add_line(lines, {"level": int(r[2])})
        layers.append(bl)
    # streets
    if z >= 6:
        sl = mvt.Layer("streets")
        for r in con.execute(
            "SELECT s.id, s.name, s.highway, g.geom FROM street_segments_rtree t JOIN street_segments g ON g.id = t.id "
            "JOIN streets s ON s.id = g.street_id "
            "WHERE t.min_lon <= ? AND t.max_lon >= ? AND t.min_lat <= ? AND t.max_lat >= ?",
            (maxx, minx, maxy, miny),
        ):
            hw = r[2] or "residential"
            if z < _HIGHWAY_MINZOOM.get(hw, 13):
                continue
            pts = [geo.lonlat_to_tile_pixel(lo, la, z, x, y) for lo, la in geo.decode_polyline(r[3])]
            sl.add_line([_simplify_px(pts, 2 if z >= 14 else 6)],
                        {"name": r[1], "class": hw if hw in _HIGHWAY_MINZOOM else "minor"})
        layers.append(sl)
    # settlements & neighbourhoods
    pl = mvt.Layer("places")
    min_imp = 0.0 if z >= 12 else (0.45 if z >= 9 else 0.7)
    for r in con.execute(
        "SELECT p.id, p.name, p.kind, p.lat, p.lon, p.importance FROM places_rtree t JOIN places p ON p.id = t.id "
        "WHERE t.min_lon <= ? AND t.max_lon >= ? AND t.min_lat <= ? AND t.max_lat >= ? AND p.importance >= ? "
        "ORDER BY p.importance DESC LIMIT 300",
        (maxx, minx, maxy, miny, min_imp),
    ):
        if not (minx <= r[4] <= maxx and miny <= r[3] <= maxy):
            continue
        if r[2] in ("suburb", "quarter", "neighbourhood") and z < 12:
            continue
        px, py = geo.lonlat_to_tile_pixel(r[4], r[3], z, x, y)
        pl.add_point(px, py, {"name": r[1], "kind": r[2], "rank": float(r[5])})
    layers.append(pl)
    return mvt.encode_tile(layers)
