"""Public transport endpoints (offline GTFS planner, see hmaps.transit and docs/adr/0007-transit.md).

    GET /api/transit?from=lat,lon&to=lat,lon&time=ISO&mode=depart|arrive   itineraries
    GET /api/transit/stop/{id}?time=ISO&limit=20                         next departures at a stop
    GET /api/transit/stops?lat=&lon=&radius=500                          stops nearby
    GET /api/transit/status                                              feed dates and freshness

When ``data/transit`` has not been built the endpoints answer 503 with a
Hebrew explanation, and the rest of the API is unaffected.
"""

from __future__ import annotations

import logging
import re

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from ..transit import data as tdata
from ..transit import planner
from .settings import settings

log = logging.getLogger("hmaps.api.transit")

NOT_READY = ("נתוני התחבורה הציבורית עדיין לא הותקנו. כדי להוסיף אותם הריצו את update-windows.cmd "
             "(או: python -m hmaps.etl gtfs).")


def _transit_dir():
    return settings.data_dir / "transit"


def _err(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


def _load():
    try:
        return tdata.get(_transit_dir())
    except Exception:  # noqa: BLE001 – a damaged file must not break the rest of the API
        log.exception("could not load transit data")
        return None


def _latlon(text: str | None, name: str) -> tuple[float, float]:
    if not text:
        raise planner.TransitError(400, "missing_parameter", f"חסר פרמטר: {name}")
    m = re.match(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$", text)
    if not m:
        raise planner.TransitError(400, "invalid_parameter", f"פורמט נקודה לא תקין ({name}) – צפוי lat,lon")
    lat, lon = float(m.group(1)), float(m.group(2))
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise planner.TransitError(400, "invalid_parameter", f"קואורדינטות מחוץ לטווח ({name})")
    return lat, lon


def _num(req: Request, name: str, default: float, lo: float, hi: float) -> float:
    v = req.query_params.get(name)
    if v in (None, ""):
        return default
    try:
        f = float(v)
    except ValueError:
        raise planner.TransitError(400, "invalid_parameter", f"ערך לא תקין עבור {name}")
    return max(lo, min(hi, f))


def _cache(resp: Response, req: Request) -> Response:
    # answers for an explicit time are stable; "now" answers are not
    resp.headers["Cache-Control"] = "public, max-age=120" if req.query_params.get("time") else "no-cache"
    return resp


async def plan_ep(req: Request) -> Response:
    try:
        frm = _latlon(req.query_params.get("from"), "from")
        to = _latlon(req.query_params.get("to"), "to")
        mode = req.query_params.get("mode", "depart")
        if mode not in ("depart", "arrive"):
            raise planner.TransitError(400, "invalid_parameter", "mode חייב להיות depart או arrive")
        when = planner.parse_time(req.query_params.get("time"))
        limit = int(_num(req, "limit", 5, 1, 8))
        data = _load()
        if data is None:
            return _err(503, "transit_not_ready", NOT_READY)
        res = await run_in_threadpool(planner.plan, data, frm, to, when, mode == "arrive", limit)
    except planner.TransitError as e:
        return _err(e.status, e.code, e.message)
    return _cache(JSONResponse(res), req)


async def stop_ep(req: Request) -> Response:
    try:
        data = _load()
        if data is None:
            return _err(503, "transit_not_ready", NOT_READY)
        sid = req.path_params["id"]
        s = data.id_index.get(sid)
        if s is None and sid.startswith("code-") and sid[5:].isdigit():  # by the code printed on the sign
            hits = (data.stop_code == int(sid[5:])).nonzero()[0]
            s = int(hits[0]) if len(hits) else None
        if s is None:
            return _err(404, "not_found", "התחנה לא נמצאה")
        when = planner.parse_time(req.query_params.get("time"))
        limit = int(_num(req, "limit", 20, 1, 60))
        res = await run_in_threadpool(planner.departures, data, int(s), when, 3 * 3600, limit)
    except planner.TransitError as e:
        return _err(e.status, e.code, e.message)
    return _cache(JSONResponse(res), req)


async def stops_ep(req: Request) -> Response:
    try:
        data = _load()
        if data is None:
            return _err(503, "transit_not_ready", NOT_READY)
        lat = req.query_params.get("lat")
        lon = req.query_params.get("lon")
        pt = _latlon(f"{lat},{lon}" if lat and lon else None, "lat/lon")
        radius = _num(req, "radius", 500, 10, 2000)
        limit = int(_num(req, "limit", 20, 1, 100))
        res = await run_in_threadpool(planner.nearby_stops, data, pt[0], pt[1], radius, limit)
    except planner.TransitError as e:
        return _err(e.status, e.code, e.message)
    return JSONResponse({"stops": res}, headers={"Cache-Control": "public, max-age=3600"})


async def status_ep(req: Request) -> Response:
    data = await run_in_threadpool(_load)
    body = planner.status(data)
    if data is None:
        body["message"] = NOT_READY
    return JSONResponse(body, headers={"Cache-Control": "no-cache"})


def routes() -> list[Route]:
    """Routes to register before the /api catch-all in :mod:`hmaps.api.app`."""
    return [
        Route("/api/transit", plan_ep),
        Route("/api/transit/status", status_ep),
        Route("/api/transit/stops", stops_ep),
        Route("/api/transit/stop/{id}", stop_ep),
    ]
