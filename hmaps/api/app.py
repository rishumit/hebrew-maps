"""HTTP API + static web app (Starlette / ASGI).

Endpoints (see docs/api.md and /api/openapi.json):

    GET  /api/health                     liveness/readiness
    GET  /api/meta                       dataset & services info for the client
    GET  /api/categories                 category tree
    GET  /api/search/autocomplete?q=     suggestions while typing
    GET  /api/search?q= | POST /api/search   full search
    GET  /api/poi/{id}                   POI card
    GET  /api/poi?lat=&lon=&radius=&category=   nearby POIs
    GET  /api/place/{id}  /api/street/{id}
    GET  /api/geocode?address=           forward geocoding
    GET  /api/reverse?lat=&lon=          reverse geocoding
    GET  /api/route?from=lat,lon&to=lat,lon&mode=car|foot|bike
    GET  /api/trails/{id}                trail card  (+ /tiles/trails, /tiles/parks – see outdoor.py)
    GET  /api/minyanim/near?lat=&lon=&radius=&prayer=&after=&day=   next minyanim nearby (synagogue prayer times)
    GET  /api/hikes, /api/hikes/{id}[/guide], /api/trails/{id}/guide, POST /api/guide   hiking guide (guide.py)
    GET  /api/transit?from=&to=&time=&mode=depart|arrive   public transport (+ /stop/{id}[/routes], /route/{id}, /stops, /status)
    GET  /tiles/base.json  /tiles/base/{z}/{x}/{y}.pbf      OpenMapTiles basemap
    GET  /tiles/fallback/{z}/{x}/{y}.pbf                    basemap from our data
    GET  /tiles/poi/{z}/{x}/{y}.pbf?cats=                   POI overlay
    GET  /tiles/contours/{z}/{x}/{y}.pbf                    contour lines (Copernicus DEM)
    GET  /tiles/terrain/{z}/{x}/{y}.png                     terrain-RGB (Terrarium) for hillshade
    GET  /api/elevation?points=lat,lon|… | ?path=polyline   elevations / profile
    GET  /api/updates, /api/updates/status, POST /api/updates/{component}   online data updates (updates.py)
    GET  /fonts/{fontstack}/{range}.pbf                     map label glyphs
    GET  /metrics                                           Prometheus metrics
"""

from __future__ import annotations

import json
import logging
import mimetypes
import re
import sys
import threading
import time
import uuid
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.middleware import Middleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from .. import __version__
from ..core import categories
from . import guide as guide_api
from . import outdoor
from . import pois as poi_mod
from . import elevation as elev_mod
from . import routing, tiles
from . import satellite as satellite_api
from . import rich as rich_api
from . import transit as transit_api
from . import updates as updates_api
from . import minyanim as minyanim_api
from .search import SearchEngine, nearby, reverse
from .settings import settings
from .store import Basemap, Dataset

log = logging.getLogger("hmaps.api")
mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("text/javascript", ".js")

ATTRIBUTION = ('© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> '
               '· <a href="https://data.gov.il" target="_blank" rel="noopener">data.gov.il</a> '
               '· <a href="https://www.wikidata.org" target="_blank" rel="noopener">Wikidata</a>')


# ----------------------------------------------------------------------------- state

class State:
    def __init__(self) -> None:
        self.ds = Dataset(settings.db_path, settings.sample_db)
        self.search = SearchEngine(self.ds)
        self.basemap = Basemap(settings.basemap_path)
        self.contours = Basemap(settings.data_dir / "tiles" / "contours.mbtiles")
        self.terrain = Basemap(settings.data_dir / "tiles" / "terrain.mbtiles")
        self.elevation = elev_mod.Elevation(self.terrain)
        self._routing_cache: tuple[float, dict] = (0.0, {})
        self._poi_zooms: tuple[str, dict[int, int]] | None = None
        self._poi_zooms_building: str | None = None
        self._poi_zooms_lock = threading.Lock()

    def poi_zooms(self) -> dict[int, int] | None:
        """Global POI declutter (zoom from which each POI shows), per dataset version.

        Built once in a background thread; until it is ready, tiles fall back to
        per-tile thinning (and are cached only briefly)."""
        ver = self.ds.version
        cur = self._poi_zooms
        if cur is not None and cur[0] == ver:
            return cur[1]
        with self._poi_zooms_lock:
            if self._poi_zooms_building == ver:
                return None
            self._poi_zooms_building = ver

        def build() -> None:
            try:
                t0 = time.monotonic()
                zooms = tiles.compute_poi_zooms(self.ds.conn())
                self._poi_zooms = (ver, zooms)
                log.info("POI declutter ready: %d POIs placed in %.1fs", len(zooms), time.monotonic() - t0)
            except Exception:  # noqa: BLE001 - tiles keep working with the per-tile fallback
                log.exception("POI declutter failed")
                with self._poi_zooms_lock:
                    self._poi_zooms_building = None  # retry on a later tile request

        threading.Thread(target=build, name="poi-zooms", daemon=True).start()
        return None

    def routing_status(self) -> dict:
        ts, val = self._routing_cache
        if time.monotonic() - ts > 30:
            val = routing.status(settings)
            self._routing_cache = (time.monotonic(), val)
        return val


STATE: State | None = None


def state() -> State:
    global STATE
    if STATE is None:
        STATE = State()
    return STATE


# ----------------------------------------------------------------------------- helpers

class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message


def error_response(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


def _float(req: Request, name: str, lo: float, hi: float, required: bool = False) -> float | None:
    v = req.query_params.get(name)
    if v in (None, ""):
        if required:
            raise ApiError(400, "missing_parameter", f"חסר פרמטר: {name}")
        return None
    try:
        f = float(v)
    except ValueError:
        raise ApiError(400, "invalid_parameter", f"ערך לא תקין עבור {name}")
    if not lo <= f <= hi:
        raise ApiError(400, "invalid_parameter", f"ערך מחוץ לטווח עבור {name}")
    return f


def _int(req: Request, name: str, default: int, lo: int, hi: int) -> int:
    v = req.query_params.get(name)
    if v in (None, ""):
        return default
    try:
        return max(lo, min(hi, int(v)))
    except ValueError:
        raise ApiError(400, "invalid_parameter", f"ערך לא תקין עבור {name}")


def _latlon(text: str | None, name: str) -> tuple[float, float]:
    if not text:
        raise ApiError(400, "missing_parameter", f"חסר פרמטר: {name}")
    m = re.match(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$", text)
    if not m:
        raise ApiError(400, "invalid_parameter", f"פורמט נקודה לא תקין ({name}) – צפוי lat,lon")
    lat, lon = float(m.group(1)), float(m.group(2))
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ApiError(400, "invalid_parameter", f"קואורדינטות מחוץ לטווח ({name})")
    return lat, lon


def _query(req: Request, max_len: int = 200) -> str:
    q = (req.query_params.get("q") or req.query_params.get("query") or "").strip()
    if len(q) > max_len:
        raise ApiError(400, "query_too_long", "השאילתה ארוכה מדי")
    return q


def _need_data() -> State:
    st = state()
    if not st.ds.available:
        raise ApiError(503, "data_not_ready", "הנתונים עדיין בהכנה. נסו שוב בעוד מספר דקות.")
    return st


def cached(resp: Response, seconds: int) -> Response:
    resp.headers["Cache-Control"] = f"public, max-age={seconds}"
    return resp


def _parse_id(raw: str, prefix: str) -> int:
    s = raw[len(prefix) + 1:] if raw.startswith(prefix + "-") else raw
    if not s.isdigit():
        raise ApiError(404, "not_found", "הפריט לא נמצא")
    return int(s)


# ----------------------------------------------------------------------------- endpoints

async def health(req: Request) -> Response:
    st = state()
    ok = st.ds.available
    body = {"status": "ok" if ok else "degraded", "version": __version__, "dataset": ok, "demo": st.ds.demo,
            "basemap": st.basemap.available}
    return JSONResponse(body, status_code=200 if ok else 503)


async def meta(req: Request) -> Response:
    st = state()
    rs = await run_in_threadpool(st.routing_status)
    m = st.ds.meta
    base = str(req.base_url).rstrip("/")
    return JSONResponse({
        "name": "מפות ישראל", "version": __version__,
        "dataset": {"available": st.ds.available, "demo": st.ds.demo, "built": m.get("built"), "osm_date": m.get("osm_date"),
                    "version": st.ds.version},
        "basemap": {"type": "openmaptiles" if st.basemap.available else "fallback",
                    "tilejson": f"{base}/tiles/base.json" if st.basemap.available else None,
                    "fallback_tiles": f"{base}/tiles/fallback/{{z}}/{{x}}/{{y}}.pbf?v={st.ds.version}",
                    "glyphs": f"{base}/fonts/{{fontstack}}/{{range}}.pbf"},
        "poi_tiles": f"{base}/tiles/poi/{{z}}/{{x}}/{{y}}.pbf?v={st.ds.version}-d4",
        "routing": rs,
        "attribution": ATTRIBUTION + (" · " + elev_mod.ATTRIBUTION if st.terrain.available else ""),
        "bounds": [34.15, 29.40, 35.95, 33.45],
        "terrain": _terrain_meta(st, base),
    })


def _terrain_meta(st: State, base: str) -> dict:
    """Topographic layers (built by ``python -m tools.terrain build``); null URLs when absent."""
    out: dict[str, Any] = {"contours": None, "dem": None, "elevation": st.terrain.available}
    for key, ts, ext in (("contours", st.contours, "pbf"), ("dem", st.terrain, "png")):
        if ts.available:
            md = ts.metadata
            v = str(ts.path.stat().st_mtime_ns % 10**8)
            out[key] = {"tiles": f"{base}/tiles/{'terrain' if key == 'dem' else key}/{{z}}/{{x}}/{{y}}.{ext}?v={v}",
                        "minzoom": int(md.get("minzoom", 0)), "maxzoom": int(md.get("maxzoom", 12)),
                        "bounds": [float(b) for b in md.get("bounds", "34.1,29.35,36,33.5").split(",")]}
            if key == "dem":
                out[key]["encoding"] = md.get("encoding", "terrarium")
    return out


async def categories_ep(req: Request) -> Response:
    return cached(JSONResponse({"categories": categories.as_json()}), 3600)


async def autocomplete(req: Request) -> Response:
    st = _need_data()
    q = _query(req)
    lat = _float(req, "lat", -90, 90)
    lon = _float(req, "lon", -180, 180)
    limit = _int(req, "limit", 8, 1, 20)
    t0 = time.perf_counter()
    res = await run_in_threadpool(_autocomplete_cached, st.ds.version, q, _round(lat), _round(lon), limit)
    res = {**res, "query_time_ms": round((time.perf_counter() - t0) * 1000, 1)}
    return cached(JSONResponse(res), 120)


def _round(v: float | None) -> float | None:
    return None if v is None else round(v, 2)  # ~1 km: good for caching, fine for ranking


_ac_cache: dict[tuple, dict] = {}
_ac_lock = threading.Lock()


def _autocomplete_cached(version: str, q: str, lat, lon, limit: int) -> dict:
    key = (version, q, lat, lon, limit)
    with _ac_lock:
        hit = _ac_cache.get(key)
    if hit is not None:
        return hit
    res = state().search.autocomplete(q, lat, lon, limit)
    with _ac_lock:
        if len(_ac_cache) > 5000:
            _ac_cache.clear()
        _ac_cache[key] = res
    return res


async def search(req: Request) -> Response:
    st = _need_data()
    if req.method == "POST":
        try:
            body = await req.json()
        except Exception:  # noqa: BLE001
            raise ApiError(400, "invalid_json", "גוף הבקשה אינו JSON תקין")
        if not isinstance(body, dict):
            raise ApiError(400, "invalid_json", "גוף הבקשה אינו JSON תקין")
        q = str(body.get("query") or "")[:200]
        loc = body.get("location") or {}
        user = body.get("user_location") or {}
        filters = body.get("filters") or {}
        args = dict(
            lat=_opt_float(loc.get("lat")), lon=_opt_float(loc.get("lon")),
            limit=max(1, min(100, int(body.get("limit") or 20))), offset=max(0, int(body.get("offset") or 0)),
            category=filters.get("category"), kosher=bool(filters.get("kosher")), open_now=bool(filters.get("open_now")),
            user_lat=_opt_float(user.get("lat")), user_lon=_opt_float(user.get("lon")),
        )
    else:
        q = _query(req)
        args = dict(
            lat=_float(req, "lat", -90, 90), lon=_float(req, "lon", -180, 180),
            limit=_int(req, "limit", 20, 1, 100), offset=_int(req, "offset", 0, 0, 1000),
            category=req.query_params.get("category") or None,
            kosher=req.query_params.get("kosher") in ("1", "true"),
            open_now=req.query_params.get("open_now") in ("1", "true"),
            user_lat=_float(req, "user_lat", -90, 90), user_lon=_float(req, "user_lon", -180, 180),
        )
    if args["category"] and args["category"] not in categories.SUB_BY_ID and args["category"] not in categories.CATEGORY_BY_ID:
        raise ApiError(400, "invalid_category", "קטגוריה לא מוכרת")
    if not q and not args["category"]:
        raise ApiError(400, "missing_parameter", "יש להזין טקסט לחיפוש")
    t0 = time.perf_counter()
    res = await run_in_threadpool(st.search.search, q, **args)
    res["meta"]["query_time_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return cached(JSONResponse(res), 60)


def _opt_float(v: Any) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


async def poi_get(req: Request) -> Response:
    st = _need_data()
    pid = _parse_id(req.path_params["id"], "poi")
    res = await run_in_threadpool(lambda: poi_mod.poi_detail(st.ds.conn(), pid))
    if res is None:
        raise ApiError(404, "not_found", "המקום לא נמצא")
    return cached(JSONResponse(res), 300)


async def poi_list(req: Request) -> Response:
    st = _need_data()
    lat = _float(req, "lat", -90, 90, required=True)
    lon = _float(req, "lon", -180, 180, required=True)
    radius = _float(req, "radius", 1, 50000) or 1000.0
    limit = _int(req, "limit", 50, 1, 200)
    offset = _int(req, "offset", 0, 0, 5000)
    cat = req.query_params.get("category") or None
    if cat and cat not in categories.SUB_BY_ID and cat not in categories.CATEGORY_BY_ID:
        raise ApiError(400, "invalid_category", "קטגוריה לא מוכרת")
    tag_list = [t.strip() for t in (req.query_params.get("tags") or "").split(",") if t.strip()]
    t0 = time.perf_counter()
    all_items = await run_in_threadpool(lambda: nearby(st.ds.conn(), lat, lon, cat, radius, 5000, 0,
                                                       kosher=req.query_params.get("kosher") in ("1", "true"),
                                                       open_now=req.query_params.get("open_now") in ("1", "true"),
                                                       tags=tag_list or None))
    page = all_items[offset:offset + limit]
    return cached(JSONResponse({"results": page, "meta": {"total": len(all_items), "returned": len(page), "offset": offset,
                                                          "query_time_ms": round((time.perf_counter() - t0) * 1000, 1)}}), 60)


async def place_get(req: Request) -> Response:
    st = _need_data()
    pid = _parse_id(req.path_params["id"], "place")
    res = await run_in_threadpool(lambda: poi_mod.place_detail(st.ds.conn(), pid))
    if res is None:
        raise ApiError(404, "not_found", "המקום לא נמצא")
    return cached(JSONResponse(res), 3600)


async def street_get(req: Request) -> Response:
    st = _need_data()
    sid = _parse_id(req.path_params["id"], "street")
    res = await run_in_threadpool(lambda: poi_mod.street_detail(st.ds.conn(), sid))
    if res is None:
        raise ApiError(404, "not_found", "הרחוב לא נמצא")
    return cached(JSONResponse(res), 3600)


async def geocode(req: Request) -> Response:
    st = _need_data()
    address = (req.query_params.get("address") or "").strip()[:200]
    city = (req.query_params.get("city") or "").strip()[:80]
    if not address:
        raise ApiError(400, "missing_parameter", "חסר פרמטר: address")
    from ..core.query import parse

    def run() -> list[dict]:
        p = parse(f"{address}, {city}" if city and "," not in address else (f"{address} {city}" if city else address))
        if not p.is_address:
            # a street or place without number
            res = st.search.search(address + (f" {city}" if city else ""), limit=5)["results"]
            return [r for r in res if r["type"] in ("street", "place", "address")][:5]
        return st.search.geocode_parsed(p, limit=5)

    results = await run_in_threadpool(run)
    if not results:
        raise ApiError(404, "not_found", "הכתובת לא נמצאה")
    best = results[0]
    body = {
        "lat": best["location"]["lat"], "lon": best["location"]["lon"],
        "address": ", ".join(x for x in (best["name"], best.get("city") or "") if x),
        "confidence": best.get("confidence", 0.7), "accuracy": best.get("accuracy", best["type"]),
        "results": results,
    }
    return cached(JSONResponse(body), 3600)


async def reverse_ep(req: Request) -> Response:
    st = _need_data()
    lat = _float(req, "lat", -90, 90, required=True)
    lon = _float(req, "lon", -180, 180, required=True)
    res = await run_in_threadpool(lambda: reverse(st.ds.conn(), lat, lon))
    return cached(JSONResponse(res), 3600)


async def route_ep(req: Request) -> Response:
    mode = req.query_params.get("mode", "car")
    if mode not in routing.MODES:
        raise ApiError(400, "invalid_parameter", "אמצעי תחבורה לא נתמך (car / foot / bike)")
    if len(req.query_params.getlist("via")) > 5:
        raise ApiError(400, "invalid_parameter", "ניתן להוסיף עד 5 נקודות ביניים")
    pts = [_latlon(req.query_params.get("from"), "from")]
    for v in req.query_params.getlist("via")[:5]:
        pts.append(_latlon(v, "via"))
    pts.append(_latlon(req.query_params.get("to"), "to"))
    alts = req.query_params.get("alternatives", "1") not in ("0", "false")
    try:
        res = await run_in_threadpool(routing.route_any, settings, mode, pts, alts)
    except routing.RoutingError as e:
        return error_response(e.status, "routing_error", e.message)
    return cached(JSONResponse(res), 300)


# ----------------------------------------------------------------------------- tiles & fonts

_TILE_RE_MAX_Z = 20


def _zxy(req: Request) -> tuple[int, int, int]:
    try:
        z, x, y = int(req.path_params["z"]), int(req.path_params["x"]), int(req.path_params["y"])
    except (KeyError, ValueError):
        raise ApiError(400, "invalid_tile", "אריח לא תקין")
    if not (0 <= z <= _TILE_RE_MAX_Z and 0 <= x < 2 ** z and 0 <= y < 2 ** z):
        raise ApiError(400, "invalid_tile", "אריח לא תקין")
    return z, x, y


def _pbf(data: bytes | None, max_age: int, gzipped: bool = False) -> Response:
    if not data:
        return Response(status_code=204, headers={"Cache-Control": f"public, max-age={max_age}"})
    headers = {"Cache-Control": f"public, max-age={max_age}", "Access-Control-Allow-Origin": "*"}
    if gzipped or data[:2] == b"\x1f\x8b":
        headers["Content-Encoding"] = "gzip"
    return Response(data, media_type="application/x-protobuf", headers=headers)


async def tile_base(req: Request) -> Response:
    z, x, y = _zxy(req)
    st = state()
    data = await run_in_threadpool(st.basemap.tile, z, x, y)
    return _pbf(data, 86400 * 7)


async def tile_base_json(req: Request) -> Response:
    st = state()
    if not st.basemap.available:
        raise ApiError(404, "basemap_not_ready", "מפת הבסיס המלאה עדיין בבנייה")
    md = st.basemap.metadata
    base = str(req.base_url).rstrip("/")
    try:
        vector_layers = json.loads(md.get("json", "{}")).get("vector_layers", [])
    except json.JSONDecodeError:
        vector_layers = []
    bounds = [float(v) for v in md.get("bounds", "34.15,29.40,35.95,33.45").split(",")]
    v = str(abs(hash(md.get("mtime", "") + str(st.basemap.path.stat().st_mtime_ns))) % 10**8)
    return cached(JSONResponse({
        "tilejson": "3.0.0", "name": md.get("name", "basemap"), "scheme": "xyz",
        "tiles": [f"{base}/tiles/base/{{z}}/{{x}}/{{y}}.pbf?v={v}"],
        "minzoom": int(md.get("minzoom", 0)), "maxzoom": int(md.get("maxzoom", 14)),
        "bounds": bounds, "vector_layers": vector_layers, "attribution": ATTRIBUTION,
    }), 300)


async def tile_poi(req: Request) -> Response:
    st = _need_data()
    z, x, y = _zxy(req)
    if z < 6:
        return _pbf(None, 3600)
    cats = [c for c in (req.query_params.get("cats") or "").split(",") if c in categories.SUB_BY_ID or c in categories.CATEGORY_BY_ID]
    kosher = req.query_params.get("kosher") in ("1", "true")
    zooms = None if cats else state().poi_zooms()
    data = await run_in_threadpool(lambda: tiles.poi_tile(st.ds.conn(), z, x, y, cats or None, kosher, zooms=zooms))
    return _pbf(data, 3600 if cats or zooms is not None else 60)


async def tile_contours(req: Request) -> Response:
    z, x, y = _zxy(req)
    data = await run_in_threadpool(state().contours.tile, z, x, y)
    return _pbf(data, 86400 * 7)


_SEA_PNG: bytes | None = None


async def tile_terrain(req: Request) -> Response:
    global _SEA_PNG
    z, x, y = _zxy(req)
    st = state()
    if not st.terrain.available:
        raise ApiError(404, "terrain_not_ready", "שכבת הטופוגרפיה לא נבנתה")
    data = await run_in_threadpool(st.terrain.tile, z, x, y)
    if not data:
        # skipped (open sea) or outside the area: a flat tile at sea level keeps the hillshade seamless
        if _SEA_PNG is None:
            import numpy as np

            from ..core import dem
            _SEA_PNG = dem.png_encode(dem.terrarium_encode(np.zeros((dem.TILE_SIZE, dem.TILE_SIZE))))
        data = _SEA_PNG
    return Response(data, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=604800", "Access-Control-Allow-Origin": "*"})


def _points_param(text: str) -> list[tuple[float, float]]:
    pts = [_latlon(p, "points") for p in re.split(r"[|;]", text) if p.strip()]
    if not pts:
        raise ApiError(400, "missing_parameter", "חסר פרמטר: points")
    return pts


async def elevation_ep(req: Request) -> Response:
    st = state()
    pts: list[tuple[float, float]] | None = None  # (lat, lon)
    line: list[tuple[float, float]] | None = None  # (lon, lat)
    samples = 200
    if req.method == "POST":
        try:
            body = await req.json()
        except Exception:  # noqa: BLE001
            raise ApiError(400, "invalid_json", "גוף הבקשה אינו JSON תקין")
        if not isinstance(body, dict):
            raise ApiError(400, "invalid_json", "גוף הבקשה אינו JSON תקין")
        try:
            if body.get("coordinates") is not None:
                line = [(float(c[0]), float(c[1])) for c in body["coordinates"]]
            elif body.get("points") is not None:
                pts = [(float(c[0]), float(c[1])) for c in body["points"]]
            samples = int(body.get("samples") or samples)
        except (TypeError, ValueError, IndexError):
            raise ApiError(400, "invalid_parameter", "קואורדינטות לא תקינות")
    else:
        if req.query_params.get("path"):
            from ..core import geo
            try:
                line = geo.decode_polyline(req.query_params["path"])
            except (IndexError, ValueError):
                raise ApiError(400, "invalid_parameter", "קו (polyline) לא תקין")
        elif req.query_params.get("points"):
            pts = _points_param(req.query_params["points"])
        samples = _int(req, "samples", samples, 2, elev_mod.MAX_SAMPLES)
    if pts is None and line is None:
        raise ApiError(400, "missing_parameter", "חסר פרמטר: points או path")
    n = len(pts if pts is not None else line)
    if n == 0 or n > (elev_mod.MAX_POINTS if pts is not None else 20000):
        raise ApiError(400, "invalid_parameter", "מספר נקודות לא תקין")
    for lat, lon in (pts if pts is not None else [(b, a) for a, b in line]):
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ApiError(400, "invalid_parameter", "קואורדינטות מחוץ לטווח")
    if not st.terrain.available:
        raise ApiError(503, "terrain_not_ready", "נתוני הגובה עדיין לא הותקנו")
    samples = max(2, min(elev_mod.MAX_SAMPLES, samples))
    if pts is not None:
        res = await run_in_threadpool(st.elevation.points, pts)
        body = {"elevations": res, "points": [[a, b] for a, b in pts]}
    else:
        body = await run_in_threadpool(st.elevation.profile, line, samples)
    body.update(unit="m", attribution=elev_mod.ATTRIBUTION)
    return cached(JSONResponse(body), 3600)


async def tile_fallback(req: Request) -> Response:
    st = _need_data()
    z, x, y = _zxy(req)
    data = await run_in_threadpool(lambda: tiles.fallback_tile(st.ds.conn(), z, x, y))
    return _pbf(data, 3600)


_FONTSTACK_RE = re.compile(r"^[\w \-,.]+$")
_RANGE_RE = re.compile(r"^\d{1,5}-\d{1,5}$")


async def fonts(req: Request) -> Response:
    stack = req.path_params["fontstack"]
    rng = req.path_params["range"]
    if not _FONTSTACK_RE.match(stack) or not _RANGE_RE.match(rng):
        raise ApiError(400, "invalid_font", "בקשת גופן לא תקינה")
    candidates = [s.strip() for s in stack.split(",") if s.strip()] + ["Noto Sans Regular", "Hebrew Fallback Regular"]
    # for the Hebrew ranges, a font without Hebrew glyphs (tiny file) must not shadow the fallback font
    min_size = 2000 if rng in ("1280-1535", "64256-64511") else 0
    for name in candidates:
        for d in settings.fonts_dirs:
            p = d / name / f"{rng}.pbf"
            if p.is_file() and p.stat().st_size >= min_size:
                return FileResponse(p, media_type="application/x-protobuf",
                                    headers={"Cache-Control": "public, max-age=2592000", "Access-Control-Allow-Origin": "*"})
    # MapLibre tolerates an empty glyph range
    return Response(b"", media_type="application/x-protobuf", headers={"Cache-Control": "public, max-age=3600"})


async def reports(req: Request) -> Response:
    name = req.path_params["name"]
    if name not in ("quality-report.html", "quality-report.json"):
        raise ApiError(404, "not_found", "הדוח לא נמצא")
    p = settings.data_dir / "reports" / name
    if not p.is_file():
        raise ApiError(404, "not_found", "הדוח עדיין לא נוצר")
    return FileResponse(p, headers={"Cache-Control": "no-cache"})


# ----------------------------------------------------------------------------- metrics

class Metrics:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.count: dict[tuple[str, int], int] = defaultdict(int)
        self.latency_sum: dict[str, float] = defaultdict(float)
        self.buckets = (0.01, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0)
        self.hist: dict[str, list[int]] = defaultdict(lambda: [0] * (len(self.buckets) + 1))

    def observe(self, route: str, status: int, seconds: float) -> None:
        with self.lock:
            self.count[(route, status)] += 1
            self.latency_sum[route] += seconds
            h = self.hist[route]
            for i, b in enumerate(self.buckets):
                if seconds <= b:
                    h[i] += 1
            h[-1] += 1

    def render(self) -> str:
        lines = ["# TYPE hmaps_requests_total counter"]
        with self.lock:
            for (route, status), n in sorted(self.count.items()):
                lines.append(f'hmaps_requests_total{{route="{route}",status="{status}"}} {n}')
            lines.append("# TYPE hmaps_request_seconds histogram")
            for route, h in sorted(self.hist.items()):
                for b, n in zip(self.buckets, h):
                    lines.append(f'hmaps_request_seconds_bucket{{route="{route}",le="{b}"}} {n}')
                lines.append(f'hmaps_request_seconds_bucket{{route="{route}",le="+Inf"}} {h[-1]}')
                lines.append(f'hmaps_request_seconds_sum{{route="{route}"}} {self.latency_sum[route]:.4f}')
                lines.append(f'hmaps_request_seconds_count{{route="{route}"}} {h[-1]}')
        return "\n".join(lines) + "\n"


METRICS = Metrics()


async def metrics_ep(req: Request) -> Response:
    return PlainTextResponse(METRICS.render(), media_type="text/plain; version=0.0.4")


# ----------------------------------------------------------------------------- middleware

REMOTE_IMG_HOSTS = "https://commons.wikimedia.org https://upload.wikimedia.org https://*.fbcdn.net"


def _csp() -> str:
    """Photos may come from Wikimedia / Mapillary unless the map runs fully offline (HMAPS_REMOTE_IMAGES=0)."""
    img = "'self' data: blob:" + (" " + REMOTE_IMG_HOSTS if settings.remote_images else "")
    return (f"default-src 'self'; img-src {img}; "
            "style-src 'self' 'unsafe-inline'; script-src 'self' 'wasm-unsafe-eval'; worker-src 'self' blob:; "
            "child-src 'self' blob:; connect-src 'self'; font-src 'self' data:; frame-ancestors 'self'; base-uri 'self'; "
            "form-action 'self'")


CSP = _csp()
# The desktop shell (web/desktop.html: title bar + one <iframe src="/map.html"> per tab) also talks to
# the Tauri window over its IPC channel (ipc: on Linux/macOS, http://ipc.localhost on Windows/WebView2);
# it only ever calls the window commands granted in src-tauri (minimize/maximize/close/drag).
CSP_DESKTOP = CSP.replace("connect-src 'self';", "connect-src 'self' ipc: http://ipc.localhost;")


class ServiceMiddleware:
    """Request id, structured logging, metrics, rate limiting, security headers, error mapping."""

    def __init__(self, app, rate: float, burst: int):
        self.app = app
        self.rate, self.burst = rate, burst
        self.buckets: dict[str, tuple[float, float]] = {}
        self.lock = threading.Lock()

    def _allow(self, key: str) -> bool:
        now = time.monotonic()
        with self.lock:
            tokens, last = self.buckets.get(key, (float(self.burst), now))
            tokens = min(self.burst, tokens + (now - last) * self.rate)
            if tokens < 1:
                self.buckets[key] = (tokens, now)
                return False
            self.buckets[key] = (tokens - 1, now)
            if len(self.buckets) > 50_000:
                self.buckets.clear()
            return True

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path: str = scope["path"]
        rid = uuid.uuid4().hex[:12]
        t0 = time.perf_counter()
        client = (scope.get("client") or ("?", 0))[0]
        status_holder = {"status": 500}

        if path.startswith("/api/") and not path.startswith("/api/health"):
            if not self._allow(client):
                resp = error_response(429, "rate_limited", "יותר מדי בקשות – נסו שוב בעוד רגע")
                resp.headers["Retry-After"] = "1"
                await resp(scope, receive, send)
                self._done(path, 429, t0, rid)
                return

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                headers = list(message.get("headers", []))
                names = {h[0].lower() for h in headers}
                extra = [
                    (b"x-request-id", rid.encode()),
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"strict-origin-when-cross-origin"),
                    (b"permissions-policy", b"geolocation=(self), camera=(), microphone=()"),
                ]
                if not path.startswith(("/tiles/", "/fonts/", "/api/")):
                    extra.append((b"content-security-policy", (CSP_DESKTOP if path == "/desktop.html" else CSP).encode()))
                    extra.append((b"x-frame-options", b"SAMEORIGIN"))
                if (path in ("/", "/index.html", "/map.html", "/desktop.html", "/sw.js", "/manifest.webmanifest")
                        or path.startswith(("/js/", "/css/"))) and b"cache-control" not in names:
                    extra.append((b"cache-control", b"no-cache"))
                elif path.startswith(("/vendor/", "/fonts/", "/icons/", "/logos/")) and b"cache-control" not in names:
                    extra.append((b"cache-control", b"public, max-age=604800"))
                if settings.cors_origins and path.startswith("/api/"):
                    extra.append((b"access-control-allow-origin", ",".join(settings.cors_origins).encode()))
                message["headers"] = headers + extra
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except ApiError as e:
            status_holder["status"] = e.status
            await error_response(e.status, e.code, e.message)(scope, receive, send_wrapper)
        except Exception:  # noqa: BLE001
            log.exception("unhandled error rid=%s path=%s", rid, path)
            status_holder["status"] = 500
            await error_response(500, "internal_error", "אירעה שגיאה בשרת")(scope, receive, send_wrapper)
        self._done(path, status_holder["status"], t0, rid)

    def _done(self, path: str, status: int, t0: float, rid: str) -> None:
        dt = time.perf_counter() - t0
        route = _route_name(path)
        METRICS.observe(route, status, dt)
        if route not in ("tiles", "fonts", "static") or status >= 400:
            # query strings are deliberately not logged (search terms are user data)
            log.info(json.dumps({"rid": rid, "route": route, "path": path, "status": status, "ms": round(dt * 1000, 1)},
                                ensure_ascii=False))


def _route_name(path: str) -> str:
    if path.startswith("/tiles/"):
        return "tiles"
    if path.startswith("/fonts/"):
        return "fonts"
    if path.startswith("/api/"):
        parts = path.split("/")
        return "api_" + (parts[2] if len(parts) > 2 else "")
    return "static"


# ----------------------------------------------------------------------------- app

@asynccontextmanager
async def lifespan(app):
    st = state()
    log.info("Hebrew Maps API %s – dataset %s%s, basemap %s", __version__,
             "ready" if st.ds.available else "missing", " (demo)" if st.ds.demo else "",
             "ready" if st.basemap.available else "fallback")
    if st.ds.available:
        st.poi_zooms()  # start the POI declutter in the background
    yield


async def openapi(req: Request) -> Response:
    p = Path(__file__).with_name("openapi.json")
    return FileResponse(p, media_type="application/json")


async def api_not_found(req: Request) -> Response:
    return error_response(404, "not_found", "נתיב לא קיים")


async def map_page(req: Request) -> Response:
    """``/`` (and ``/index.html``) is the map itself – the product site lives on GitHub Pages (site/)."""
    return FileResponse(settings.web_dir / "map.html", media_type="text/html; charset=utf-8")


def build_app() -> Starlette:
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s", stream=sys.stdout)
    routes = [
        Route("/api/health", health),
        Route("/api/meta", meta),
        Route("/api/openapi.json", openapi),
        Route("/api/categories", categories_ep),
        Route("/api/search/autocomplete", autocomplete),
        Route("/api/search", search, methods=["GET", "POST"]),
        Route("/api/poi/{id}", poi_get),
        Route("/api/poi", poi_list),
        Route("/api/place/{id}", place_get),
        Route("/api/street/{id}", street_get),
        Route("/api/geocode", geocode),
        Route("/api/reverse", reverse_ep),
        Route("/api/route", route_ep),
        *guide_api.ROUTES,  # /api/hikes…, /api/trails/{id}/guide, POST /api/guide, /tiles/hikes
        *outdoor.ROUTES,  # /api/trails/{id}, /tiles/trails, /tiles/parks
        *transit_api.routes(),  # public transport (hmaps/api/transit.py)
        *minyanim_api.ROUTES,  # /api/minyanim/near (prayer times, hmaps/api/minyanim.py)
        *updates_api.ROUTES,  # /api/updates… (online data updates, hmaps/api/updates.py)
        Route("/api/elevation", elevation_ep, methods=["GET", "POST"]),
        *satellite_api.ROUTES,  # /tiles/satellite (offline-cached govmap orthophotos), /api/satellite/…
        Route("/api/{rest:path}", api_not_found),
        Route("/tiles/base.json", tile_base_json),
        Route("/tiles/base/{z}/{x}/{y}.pbf", tile_base),
        Route("/tiles/poi/{z}/{x}/{y}.pbf", tile_poi),
        Route("/tiles/fallback/{z}/{x}/{y}.pbf", tile_fallback),
        Route("/tiles/contours/{z}/{x}/{y}.pbf", tile_contours),
        Route("/tiles/terrain/{z}/{x}/{y}.png", tile_terrain),
        Route("/fonts/{fontstack}/{range}.pbf", fonts),
        Route("/reports/{name}", reports),
        Route("/reports", lambda req: RedirectResponse("/reports/quality-report.html")),
        Route("/metrics", metrics_ep),
        *rich_api.ROUTES,  # /media/<key> (downloaded photos), /media/mly/<id> (Mapillary)
    ]
    if settings.web_dir.is_dir():
        routes += [Route("/", map_page), Route("/index.html", map_page)]
        routes.append(Mount("/", app=StaticFiles(directory=str(settings.web_dir), html=True), name="web"))
    return Starlette(
        routes=routes,
        middleware=[
            Middleware(ServiceMiddleware, rate=settings.rate_per_sec, burst=settings.rate_burst),
            Middleware(GZipMiddleware, minimum_size=800),
        ],
        exception_handlers={ApiError: _api_error_handler},
        lifespan=lifespan,
    )


async def _api_error_handler(req: Request, exc: Exception) -> Response:
    assert isinstance(exc, ApiError)
    return error_response(exc.status, exc.code, exc.message)


app = build_app()
