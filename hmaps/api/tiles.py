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

from ..core import categories, geo, logos
from . import mvt

#: zoom at which a POI of a given importance first appears
def display_zoom(importance: float) -> float:
    return max(8.0, min(17.0, 18.5 - importance * 11.0))


def _tile_query_bbox(z: int, x: int, y: int, buffer: float = 0.08) -> tuple[float, float, float, float]:
    minx, miny, maxx, maxy = geo.tile_bounds(z, x, y)
    bx, by = (maxx - minx) * buffer, (maxy - miny) * buffer
    return minx - bx, miny - by, maxx + bx, maxy + by


# ------------------------------------------------------------------ POI decluttering
#
# Like Google Maps, the unfiltered POI layer is decluttered on the server before the
# client ever sees it: candidates are taken in importance order and a POI is kept only
# if no already-kept POI sits within a zoom-dependent spacing.  The thinning is
# hierarchical - it is replayed from a few zooms out, and whatever was visible at a
# coarser zoom is locked in - so icons do not blink out when zooming in.
# The client then does a final collision pass (icon/text padding) on what remains.

#: minimum distance between two POI icons, in screen pixels (512-px tiles)
#: (tuned so a dense downtown keeps an even ~1 place per 50 px from z14 to z18,
#: room for a ~22-px icon plus a label beside it; Google keeps thinning to z17)
_SPACING_PX = {6: 72, 7: 72, 8: 72, 9: 68, 10: 64, 11: 60, 12: 58, 13: 56, 14: 52, 15: 48, 16: 42, 17: 34}
#: deepest zoom that is still thinned; from the next zoom on everything is drawn
_LAST_THINNED_ZOOM = 17
#: how many zooms back the hierarchy is replayed
_HISTORY = 6
#: categories may ask to stay hidden until a deep zoom (bus stops, toilets ...);
#: only values above the default are enforced, so landmarks still show early
_DEFAULT_MIN_ZOOM = 15


def poi_spacing_px(z: int) -> int:
    """Minimum icon spacing in screen pixels at zoom ``z`` (0 = no thinning)."""
    if z > _LAST_THINNED_ZOOM:
        return 0
    return _SPACING_PX.get(z, 72)


def min_importance(z: int) -> float:
    """Importance a POI needs to be drawn on the unfiltered map at zoom ``z``."""
    return max(0.0, (18.5 - z) / 11.0) if z < 17 else 0.0


def _poi_visible_from(r) -> int:
    sub = categories.SUB_BY_ID.get(r[3])
    return sub.min_zoom if sub is not None and sub.min_zoom > _DEFAULT_MIN_ZOOM else 0


def declutter(points: list[tuple[int, int, object]], spacing: int,
              locked: list[tuple[int, int, object]] = ()) -> list[tuple[int, int, object]]:
    """Greedy importance-ordered thinning.

    ``points`` must be sorted most-important first; ``locked`` points are always kept
    and block their neighbours.  Returns locked + newly accepted points.  A hash grid
    keeps it O(n) for thousands of candidates.
    """
    kept = list(locked)
    if spacing <= 0:
        seen = {id(p[2]) for p in kept}
        return kept + [p for p in points if id(p[2]) not in seen]
    grid: dict[tuple[int, int], list[tuple[int, int]]] = defaultdict(list)
    for px, py, _ in kept:
        grid[(px // spacing, py // spacing)].append((px, py))
    seen = {id(p[2]) for p in kept}
    sq = spacing * spacing
    for px, py, r in points:
        if id(r) in seen:
            continue
        gx, gy = px // spacing, py // spacing
        if any((qx - px) ** 2 + (qy - py) ** 2 < sq
               for dx in (-1, 0, 1) for dy in (-1, 0, 1) for qx, qy in grid.get((gx + dx, gy + dy), ())):
            continue
        grid[(gx, gy)].append((px, py))
        kept.append((px, py, r))
        seen.add(id(r))
    return kept


def declutter_hierarchical(pts: list[tuple[int, int, object]], z: int) -> list[tuple[int, int, object]]:
    """Thin the candidates of a zoom-``z`` tile so that zooming in only ever adds POIs."""
    kept: list[tuple[int, int, object]] = []
    for k in range(max(6, z - _HISTORY), z + 1):
        thr = min_importance(k)
        cand = [p for p in pts if p[2][6] >= thr and k >= _poi_visible_from(p[2])]
        # spacing of zoom k measured in this tile's pixel units
        spacing = poi_spacing_px(k) * (mvt.EXTENT // 512) * (1 << (z - k))
        kept = declutter(cand, spacing, kept)
    return kept


def compute_poi_zooms(con: sqlite3.Connection) -> dict[int, int]:
    """Zoom from which every POI is drawn on the unfiltered map (18 = only when fully zoomed in).

    One global greedy pass per zoom over the whole dataset, in world-pixel units, so
    the result is identical on both sides of every tile edge and a POI that appears at
    zoom k stays visible at every deeper zoom - the way Google Maps behaves.
    """
    import math
    zmax = _LAST_THINNED_ZOOM
    world = 512 * (1 << zmax)  # world size in pixels of the deepest thinned zoom
    pts = []
    for pid, sub, lat, lon, imp in con.execute(
            "SELECT id, sub, lat, lon, importance FROM pois ORDER BY importance DESC, id"):
        if lat is None or lon is None:
            continue
        s = math.sin(math.radians(max(-85.0, min(85.0, lat))))
        px = (lon + 180.0) / 360.0 * world
        py = (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * world
        sub_ = categories.SUB_BY_ID.get(sub)
        vis = sub_.min_zoom if sub_ is not None and sub_.min_zoom > _DEFAULT_MIN_ZOOM else 0
        pts.append((pid, px, py, imp or 0.0, vis))
    zooms: dict[int, int] = {}
    kept: list[tuple[float, float]] = []
    for k in range(6, zmax + 1):
        thr = min_importance(k)
        spacing = poi_spacing_px(k) * (1 << (zmax - k))
        sq = spacing * spacing
        grid: dict[tuple[int, int], list[tuple[float, float]]] = defaultdict(list)
        for px, py in kept:
            grid[(int(px // spacing), int(py // spacing))].append((px, py))
        for pid, px, py, imp, vis in pts:
            if imp < thr:
                break  # sorted by importance: nothing further qualifies at this zoom
            if pid in zooms or k < vis:
                continue
            gx, gy = int(px // spacing), int(py // spacing)
            if any((qx - px) ** 2 + (qy - py) ** 2 < sq
                   for dx in (-1, 0, 1) for dy in (-1, 0, 1) for qx, qy in grid.get((gx + dx, gy + dy), ())):
                continue
            grid[(gx, gy)].append((px, py))
            kept.append((px, py))
            zooms[pid] = k
    return zooms


def poi_tile(con: sqlite3.Connection, z: int, x: int, y: int, cats: list[str] | None = None,
             kosher: bool = False, max_features: int = 1500, zooms: dict[int, int] | None = None) -> bytes:
    """POI overlay tile.  ``zooms`` (from :func:`compute_poi_zooms`) gives a globally
    consistent declutter; without it each tile is thinned on its own."""
    minx, miny, maxx, maxy = _tile_query_bbox(z, x, y)
    sql = ("SELECT p.id, p.name, p.category, p.sub, p.lat, p.lon, p.importance, p.kosher, p.extra FROM pois_rtree t "
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
        args.append(min_importance(z))
    sql += " ORDER BY p.importance DESC, p.id LIMIT ?"
    # unfiltered: the importance threshold bounds the set; a count cap would make the
    # candidate set depend on the tile size and break zoom-to-zoom consistency
    args.append(max_features if cats and z >= 14 else 20000)
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
        pts = []
        for r in rows:
            if not cats and z < _poi_visible_from(r):
                continue
            px, py = geo.lonlat_to_tile_pixel(r[5], r[4], z, x, y)
            pts.append((px, py, r))
        if cats:
            # the user asked for these: thin only enough to keep icons from piling up
            pts = declutter(pts, poi_spacing_px(z) // 2 * (mvt.EXTENT // 512))
        elif zooms is not None:
            pts = [p for p in pts if zooms.get(p[2][0], _LAST_THINNED_ZOOM + 1) <= z]
        else:
            pts = declutter_hierarchical(pts, z)
        # draw order = importance order (client sorts by rank as well)
        pts.sort(key=lambda p: -p[2][6])
        for px, py, r in pts:
            _add_poi(layer, px, py, r)
    return mvt.encode_tile([layer])


# operators whose official logo replaces the category icon on the map (web/logos/<key>.svg)
MAP_LOGO_BRANDS = ("inpa", "kkl")


def _map_brand(extra_json: str | None) -> str | None:
    """'inpa' / 'kkl' for sites run by the Nature and Parks Authority / KKL, else None.
    Cheap pre-check first – the JSON is only parsed for the few rows that can match."""
    if not extra_json or ("inpa" not in extra_json and "operator" not in extra_json):
        return None
    try:
        extra = json.loads(extra_json)
    except ValueError:
        return None
    b = logos.poi_brand(extra) if isinstance(extra, dict) else None
    key = b and b.get("key")
    return key if key in MAP_LOGO_BRANDS else None


def _add_poi(layer: mvt.Layer, px: int, py: int, r) -> None:
    sub = categories.SUB_BY_ID[r[3]]
    name = r[1] or ""
    props = {
        "id": int(r[0]), "name": name, "cat": r[2], "sub": r[3], "icon": sub.icon,
        "rank": float(round(r[6], 3)), "kosher": bool(r[7]) if r[7] else None,
    }
    brand = _map_brand(r[8]) if len(r) > 8 else None
    if brand:
        props["brand"] = brand
    layer.add_point(px, py, props, fid=int(r[0]))


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
