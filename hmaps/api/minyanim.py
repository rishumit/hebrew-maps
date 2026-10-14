"""Prayer times of synagogues (Nedarim Plus "זמני תפילות" project, see hmaps/etl/zmanim.py).

    GET /api/minyanim/near?lat=&lon=&radius=&prayer=&after=HH:MM&day=   next minyanim nearby
    (+ the "minyanim" block of /api/poi/{id} for linked synagogues)

Databases without the ``synagogues_ext`` / ``minyanim`` tables answer with empty lists.
"""

from __future__ import annotations

import math
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from ..core import hours

SOURCE_HE = "נדרים פלוס – פרוייקט זמני תפילות הארצי"
SOURCE_URL = "https://www.matara.pro/nedarimplus/zmanim/"
DAY_LETTERS = "אבגדהוש"   # Sunday .. Shabbat
PRAYERS = ("שחרית", "מנחה", "ערבית", "שיעור")
_TIME = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def day_letter(d: date) -> str:
    return DAY_LETTERS[(d.weekday() + 1) % 7]


def _has_tables(con: sqlite3.Connection) -> bool:
    return bool(con.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'minyanim'").fetchone())


# --------------------------------------------------------------------------- sun (approximate, sea level)

def sun_times(lat: float, lon: float, d: date) -> dict | None:
    """Sunrise / sunset (NOAA algorithm, zenith 90.833°) in Israel local time, "HH:MM"."""
    n = d.timetuple().tm_yday
    out = {}
    for key, rising in (("sunrise", True), ("sunset", False)):
        lng_h = lon / 15
        t = n + ((6 if rising else 18) - lng_h) / 24
        m = 0.9856 * t - 3.289
        lam = (m + 1.916 * math.sin(math.radians(m)) + 0.020 * math.sin(math.radians(2 * m)) + 282.634) % 360
        ra = math.degrees(math.atan(0.91764 * math.tan(math.radians(lam)))) % 360
        ra = (ra + (math.floor(lam / 90) - math.floor(ra / 90)) * 90) / 15
        sin_dec = 0.39782 * math.sin(math.radians(lam))
        cos_dec = math.cos(math.asin(sin_dec))
        cos_h = (math.cos(math.radians(90.833)) - sin_dec * math.sin(math.radians(lat))) / (cos_dec * math.cos(math.radians(lat)))
        if not -1 <= cos_h <= 1:
            return None
        h = (360 - math.degrees(math.acos(cos_h)) if rising else math.degrees(math.acos(cos_h))) / 15
        ut = (h + ra - 0.06571 * t - 6.622 - lng_h) % 24
        utc = datetime(d.year, d.month, d.day, tzinfo=timezone.utc) + timedelta(hours=ut)
        local = utc.astimezone(hours.TZ)
        if local.date() != d:  # UT wrapped around midnight
            local += timedelta(days=(d - local.date()).days)
        out[key] = local.strftime("%H:%M")
    return out


# --------------------------------------------------------------------------- POI card block

def poi_block(con: sqlite3.Connection, poi_id: int, lat: float | None = None, lon: float | None = None,
              today: date | None = None) -> dict | None:
    """Prayer times of the synagogue *poi_id*, or None when it has none."""
    if not _has_tables(con):
        return None
    ext = con.execute("SELECT mosad_id, name, address, phone_line, updated, lat, lon FROM synagogues_ext "
                      "WHERE poi_id = ? ORDER BY mosad_id LIMIT 1", (poi_id,)).fetchone()
    if ext is None:
        return None
    rows = con.execute("SELECT prayer, time, days, hall, note FROM minyanim WHERE mosad_id = ? ORDER BY time, prayer",
                       (ext[0],)).fetchall()
    today = today or datetime.now(hours.TZ).date()
    return {
        "source": SOURCE_HE, "source_url": SOURCE_URL,
        "mosad_id": ext[0], "name": ext[1], "address": ext[2],
        "phone": ext[3], "updated": ext[4],
        "items": [{"prayer": r[0], "time": r[1], "days": r[2], "hall": r[3], "note": r[4]} for r in rows],
        "sun": sun_times(lat if lat is not None else ext[5], lon if lon is not None else ext[6], today),
        "sun_note": "זריחה ושקיעה מחושבות בקירוב, בגובה פני הים – לזמנים הלכתיים מדויקים יש לעיין בלוח מוסמך",
    }


# --------------------------------------------------------------------------- nearby

def _dist(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dy = (lat2 - lat1) * 111_320
    dx = (lon2 - lon1) * 111_320 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(dx, dy)


def near(con: sqlite3.Connection, lat: float, lon: float, radius: float = 1000.0, prayer: str | None = None,
         after: str | None = None, day: str | None = None, limit: int = 30, now: datetime | None = None) -> dict:
    """The next minyanim within *radius* m: the rest of *day* after *after*, then the next day until *after*
    (24 hours, like the source's "nearest minyan"), sorted by time then distance."""
    now = now or datetime.now(hours.TZ)
    after = after or now.strftime("%H:%M")
    day = day or day_letter(now.date())
    nxt = DAY_LETTERS[(DAY_LETTERS.index(day) + 1) % 7]
    meta = {"day": day, "after": after, "radius": radius, "source": SOURCE_HE, "source_url": SOURCE_URL}
    if not _has_tables(con):
        return {"results": [], "meta": {**meta, "total": 0}}
    dlat = radius / 111_320
    dlon = radius / (111_320 * math.cos(math.radians(lat)))
    syn = {}
    for r in con.execute(
            "SELECT s.mosad_id, s.poi_id, COALESCE(s.name, p.name), s.address, s.lat, s.lon, p.street, p.housenumber, p.city "
            "FROM synagogues_ext s LEFT JOIN pois p ON p.id = s.poi_id "
            "WHERE s.lat BETWEEN ? AND ? AND s.lon BETWEEN ? AND ?", (lat - dlat, lat + dlat, lon - dlon, lon + dlon)):
        d = _dist(lat, lon, r[4], r[5])
        if d <= radius:
            addr = r[3] or " ".join(x for x in (r[6], r[7]) if x) + (f", {r[8]}" if r[8] else "")
            syn[r[0]] = {"mosad_id": r[0], "poi_id": f"poi-{r[1]}" if r[1] is not None else None,
                         "name": r[2] or "בית כנסת", "address": addr.strip(", ") or None,
                         "lat": r[4], "lon": r[5], "distance_m": round(d)}
    if not syn:
        return {"results": [], "meta": {**meta, "total": 0}}
    out = []
    ids = list(syn)
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        q = f"SELECT mosad_id, prayer, time, days, hall, note FROM minyanim WHERE mosad_id IN ({','.join('?' * len(part))})"
        for mid, pr, t, days, hall, note in con.execute(q, part):
            if prayer and pr != prayer:
                continue
            if not prayer and pr == "שיעור":
                continue  # lessons only on request
            if day in days and t >= after:
                offset = 0
            elif nxt in days and t < after:
                offset = 1
            else:
                continue
            out.append({**syn[mid], "prayer": pr, "time": t, "days": days, "hall": hall, "note": note,
                        "tomorrow": bool(offset), "_k": (offset, t, syn[mid]["distance_m"])})
    out.sort(key=lambda x: x["_k"])
    for x in out:
        del x["_k"]
    return {"results": out[:limit], "meta": {**meta, "total": len(out)}}


# --------------------------------------------------------------------------- endpoint

async def near_ep(req: Request) -> Response:
    from .app import ApiError, _float, _int, _need_data, cached

    st = _need_data()
    lat = _float(req, "lat", -90, 90, required=True)
    lon = _float(req, "lon", -180, 180, required=True)
    radius = _float(req, "radius", 50, 10000) or 1000.0
    limit = _int(req, "limit", 30, 1, 200)
    prayer = (req.query_params.get("prayer") or "").strip() or None
    if prayer and prayer not in PRAYERS:
        raise ApiError(400, "invalid_parameter", "סוג תפילה לא מוכר (שחרית / מנחה / ערבית / שיעור)")
    after = (req.query_params.get("after") or "").strip() or None
    if after:
        m = _TIME.match(after)
        if not m:
            raise ApiError(400, "invalid_parameter", "שעה לא תקינה (after=HH:MM)")
        after = f"{int(m.group(1)):02d}:{m.group(2)}"
    day = (req.query_params.get("day") or "").strip() or None
    if day:
        if day.isdigit() and int(day) < 7:
            day = DAY_LETTERS[int(day)]
        day = day.replace("'", "").replace("׳", "")
        if day == "שבת":
            day = "ש"
        if day not in DAY_LETTERS or len(day) != 1:
            raise ApiError(400, "invalid_parameter", "יום לא תקין (day=א..ש או 0..6)")
    res = await run_in_threadpool(lambda: near(st.ds.conn(), lat, lon, radius, prayer, after, day, limit))
    return cached(JSONResponse(res), 30)


ROUTES = [Route("/api/minyanim/near", near_ep)]
