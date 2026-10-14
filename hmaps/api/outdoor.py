"""Outdoor layers: marked trails (OSM route relations) and nature reserves / INPA sites.

    GET /api/trails/{id}                    trail card (name, colour, length, network, geometry)
    GET /tiles/trails/{z}/{x}/{y}.pbf       vector tiles, layer "trails" (one line per stored segment)
    GET /tiles/parks/{z}/{x}/{y}.pbf        vector tiles, layer "parks" (reserve / national park polygons)

Databases built before schema 4 have no ``trails`` / ``poi_shapes`` tables: the
tiles are then empty and the card answers 404, so an old dataset keeps working.
"""

from __future__ import annotations

import json
import sqlite3
import urllib.parse

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from ..core import categories, geo
from ..etl.trails import BIKE_ROUTES, MARK_HE, NETWORK_HE, ROUTE_HE, display_name
from . import mvt, rich
from .tiles import _simplify_px, _tile_query_bbox

#: colours of the Israeli trail marks (UI swatches; the map style has its own per-theme values)
MARK_HEX = {"red": "#D7261E", "blue": "#1D5FD6", "green": "#15803D", "black": "#1F2937", "orange": "#E46F0A",
            "purple": "#7E3AC9", "yellow": "#B88A04", "other": "#6B7280"}


def _osm_url(ref: str | None) -> str | None:
    if not ref or len(ref) < 2:
        return None
    kind = {"n": "node", "w": "way", "r": "relation"}.get(ref[0])
    return f"https://www.openstreetmap.org/{kind}/{ref[1:]}" if kind else None


def _row(con: sqlite3.Connection, tid: int):
    try:
        return con.execute("SELECT * FROM trails WHERE id = ?", (tid,)).fetchone()
    except sqlite3.OperationalError:
        return None


def _secondary(r) -> str:
    kind = ROUTE_HE.get(r["route"], "שביל")
    km = r["length_m"] / 1000
    length = f"{km:.1f} ק\"מ" if km < 100 else f"{km:,.0f} ק\"מ"
    parts = [kind, length]
    if r["mark"] in MARK_HE and MARK_HE[r["mark"]]:
        parts.insert(1, f"סימון {MARK_HE[r['mark']]}")
    return " · ".join(parts)


def trail_summary(con: sqlite3.Connection, tid: int) -> dict | None:
    """Search-result shape for a trail."""
    r = _row(con, tid)
    if r is None:
        return None
    return {
        "id": f"trail-{r['id']}", "type": "trail", "name": display_name(r["name"], r["route"], r["mark"]),
        "secondary": _secondary(r), "icon": "bike" if r["route"] in BIKE_ROUTES else "hiker",
        "color": MARK_HEX.get(r["mark"], MARK_HEX["other"]), "mark": r["mark"],
        "location": {"lat": r["lat"], "lon": r["lon"]},
    }


def trail_detail(con: sqlite3.Connection, tid: int) -> dict | None:
    r = _row(con, tid)
    if r is None:
        return None
    raw = [geo.decode_polyline(g[0]) for g in con.execute("SELECT geom FROM trail_segments WHERE trail_id = ? ORDER BY id", (tid,))]
    if sum(len(line) for line in raw) > 4000:  # long-distance trails: a lighter outline for highlighting
        raw = [geo.simplify(line, 15.0) for line in raw]
    lines = [[[round(x, 6), round(y, 6)] for x, y in line] for line in raw]
    extra = json.loads(r["extra"]) if r["extra"] else {}
    return {
        "id": f"trail-{r['id']}", "type": "trail",
        "name": display_name(r["name"], r["route"], r["mark"]), "name_he": r["name"], "name_en": r["name_en"],
        "route": r["route"], "route_he": ROUTE_HE.get(r["route"], "שביל"),
        "network": r["network"], "network_he": NETWORK_HE.get(r["network"] or ""),
        "mark": r["mark"], "mark_he": MARK_HE.get(r["mark"]) or None, "color": MARK_HEX.get(r["mark"], MARK_HEX["other"]),
        "colour_tag": r["colour"], "osmc_symbol": r["osmc"], "ref": r["ref"],
        "length_m": r["length_m"], "length_km": round(r["length_m"] / 1000, 1),
        "parts": r["parts"], "continuous": r["parts"] == 1,
        "from": extra.get("from"), "to": extra.get("to"), "roundtrip": extra.get("roundtrip") == "yes",
        "operator": r["operator"], "website": r["website"], "description_he": r["description"],
        "icon": "bike" if r["route"] in BIKE_ROUTES else "hiker",
        "location": {"lat": r["lat"], "lon": r["lon"]},
        "bbox": [r["min_lon"], r["min_lat"], r["max_lon"], r["max_lat"]],
        "geometry": {"type": "MultiLineString", "coordinates": lines},
        "metadata": {"osm_url": _osm_url(r["osm"]), "source": "OpenStreetMap",
                     "wikipedia_url": _wiki_url(extra.get("wikipedia"))},
        **rich.trail_rich(con, r, raw, extra),
    }


def _wiki_url(wp: str | None) -> str | None:
    if not wp or ":" not in wp:
        return None
    lang, title = wp.split(":", 1)
    return f"https://{lang}.wikipedia.org/wiki/{urllib.parse.quote(title.replace(' ', '_'))}"


def poi_geometry(con: sqlite3.Connection, poi_id: int) -> dict | None:
    """Stored line / polygon of a POI (stream, ridge, reserve …), or None."""
    try:
        row = con.execute("SELECT geom FROM poi_shapes WHERE poi_id = ?", (poi_id,)).fetchone()
    except sqlite3.OperationalError:
        return None
    return json.loads(row[0]) if row else None


# ------------------------------------------------------------------ tiles

def _trail_min_importance(z: int) -> float:
    if z < 10:
        return 0.7   # national / regional long-distance trails
    if z < 12:
        return 0.55  # + regional bike routes and long local trails
    return 0.0


def trails_tile(con: sqlite3.Connection, z: int, x: int, y: int) -> bytes:
    if z < 7:
        return b""
    minx, miny, maxx, maxy = _tile_query_bbox(z, x, y, 0.05)
    try:
        rows = con.execute(
            "SELECT t.id, t.name, t.route, t.network, t.mark, t.importance, g.geom FROM trail_segments_rtree r "
            "JOIN trail_segments g ON g.id = r.id JOIN trails t ON t.id = g.trail_id "
            "WHERE r.min_lon <= ? AND r.max_lon >= ? AND r.min_lat <= ? AND r.max_lat >= ? AND t.importance >= ? "
            "ORDER BY t.importance DESC LIMIT 6000",
            (maxx, minx, maxy, miny, _trail_min_importance(z)),
        ).fetchall()
    except sqlite3.OperationalError:
        return b""
    layer = mvt.Layer("trails")
    tol = 2 if z >= 14 else (4 if z >= 11 else 8)
    for r in reversed(rows):  # the most important trails last = drawn on top
        pts = [geo.lonlat_to_tile_pixel(lo, la, z, x, y) for lo, la in geo.decode_polyline(r[6])]
        layer.add_line([_simplify_px(pts, tol)], {
            "id": int(r[0]), "name": display_name(r[1], r[2], r[4]) if r[1] else "", "route": r[2],
            "network": r[3] or "", "mark": r[4], "bike": r[2] in BIKE_ROUTES, "rank": float(r[5]),
        }, fid=int(r[0]))
    return mvt.encode_tile([layer])


def parks_tile(con: sqlite3.Connection, z: int, x: int, y: int) -> bytes:
    if z < 8:
        return b""
    minx, miny, maxx, maxy = _tile_query_bbox(z, x, y, 0.02)
    subs = tuple(categories.SHAPE_SUBS)
    try:
        rows = con.execute(
            "SELECT p.id, p.name, p.sub, p.importance, p.extra, s.geom FROM pois_rtree t JOIN pois p ON p.id = t.id "
            "JOIN poi_shapes s ON s.poi_id = p.id "
            f"WHERE t.min_lon <= ? AND t.max_lon >= ? AND t.min_lat <= ? AND t.max_lat >= ? AND p.sub IN ({','.join('?' * len(subs))}) "
            "ORDER BY p.importance DESC LIMIT 400",
            (maxx, minx, maxy, miny, *subs),
        ).fetchall()
    except sqlite3.OperationalError:
        return b""
    layer = mvt.Layer("parks")
    tol = 2 if z >= 14 else (4 if z >= 11 else 6)
    for r in rows:
        geom = json.loads(r[5])
        if geom.get("type") != "MultiPolygon":
            continue
        polys = [[_simplify_px([geo.lonlat_to_tile_pixel(lo, la, z, x, y) for lo, la in ring], tol) for ring in poly]
                 for poly in geom["coordinates"]]
        extra = json.loads(r[4]) if r[4] else {}
        layer.add_polygon(polys, {"id": int(r[0]), "name": r[1] or "", "sub": r[2], "inpa": r[2] == "inpa_site",
                                  "inpa_source": extra.get("inpa") or "", "rank": float(r[3])}, fid=int(r[0]))
    return mvt.encode_tile([layer])


# ------------------------------------------------------------------ endpoints

async def trail_get(req: Request) -> Response:
    from .app import ApiError, _need_data, _parse_id, cached

    st = _need_data()
    tid = _parse_id(req.path_params["id"], "trail")
    res = await run_in_threadpool(lambda: trail_detail(st.ds.conn(), tid))
    if res is None:
        raise ApiError(404, "not_found", "השביל לא נמצא")
    return cached(JSONResponse(res), 3600)


async def tile_trails(req: Request) -> Response:
    from .app import _need_data, _pbf, _zxy

    st = _need_data()
    z, x, y = _zxy(req)
    data = await run_in_threadpool(lambda: trails_tile(st.ds.conn(), z, x, y))
    return _pbf(data, 3600)


async def tile_parks(req: Request) -> Response:
    from .app import _need_data, _pbf, _zxy

    st = _need_data()
    z, x, y = _zxy(req)
    data = await run_in_threadpool(lambda: parks_tile(st.ds.conn(), z, x, y))
    return _pbf(data, 3600)


#: mounted by app.build_app() before the /api catch-all
ROUTES = [
    Route("/api/trails/{id}", trail_get),
    Route("/tiles/trails/{z}/{x}/{y}.pbf", tile_trails),
    Route("/tiles/parks/{z}/{x}/{y}.pbf", tile_parks),
]
