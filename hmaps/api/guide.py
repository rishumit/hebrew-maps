"""Hiking-route guide (מדריך מסלול): summary, stages, sights, practical & safety info, credits.

    GET  /api/hikes?bbox=minlon,minlat,maxlon,maxlat | ?lat=&lon=&radius=   described hikes (Amud Anan …)
    GET  /api/hikes/{id}                    hike card (description, waypoints, tips, geometry, photos)
    GET  /api/hikes/{id}/guide              guide of a described hike
    GET  /api/trails/{id}/guide             guide of an OSM marked trail
    POST /api/guide                         guide of any line, e.g. a walking route planned in the app
    GET  /tiles/hikes/{z}/{x}/{y}.pbf       vector tiles, layer "hikes"

The engine (:func:`build_guide`) works on a single line (start → end) and only
reads what the dataset has: marked trails (colour changes), POIs along the way
(springs, viewpoints, ruins, water, shelters, parking …), short texts of the
sites, the Copernicus DEM (when installed) and the guide source's own
waypoints. Every part degrades gracefully – without a DEM the walking time is
distance-based, without the ``hikes`` table (datasets built before it existed)
the listing is empty and hike ids answer 404.

Walking time: Tobler's hiking function, ``v = 6·exp(-3.5·|slope + 0.05|)`` km/h,
applied to every ~25–40 m step of the DEM profile and scaled by 0.8 so that
flat ground is walked at 4 km/h (a realistic marked-trail pace with short
stops; the raw function gives 5 km/h). Without elevation data: 4 km/h plus
Naismith's 1 h per 600 m of climb when the source states the ascent.

Stages: the route is cut at (1) the source's named waypoints, (2) junctions
where the marked trail being followed changes colour, (3) named key sights
(springs, viewpoints, ruins, peaks, caves, waterfalls) when they are far enough
from other cuts; legs longer than 4 km are then split at the sight / water /
shelter closest to their middle, or evenly every ~3 km. Legs shorter than a
minimum length (400 m – 1.5 km depending on route length) are not created.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import re
import sqlite3
import threading
from collections import OrderedDict, defaultdict
from typing import Any, Iterable, Sequence

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from ..core import categories, geo
from ..core.hebrew import format_distance, format_duration
from ..etl.trails import BIKE_ROUTES, MARK_HE, NETWORK_HE, display_name
from . import mvt
from . import rich

# ----------------------------------------------------------------------------- constants

MAX_ROUTE_M = 300_000           # longer lines (the whole Israel Trail …) are refused
MAX_STAGE_M = 4_000
EVEN_SPLIT_M = 3_000
POI_RADIUS = 150.0              # sights "along the way"
PRACTICAL_RADIUS = 250.0        # water / toilets / parking may be a little off the line
TRAIL_RADIUS = 30.0             # the route "follows" a marked trail closer than this
STREAM_RADIUS = 80.0
FLAT_KMH = 4.0
TOBLER_SCALE = FLAT_KMH / (6.0 * math.exp(-3.5 * 0.05))  # ≈ 0.794

DIFFICULTY_HE = {"easy": "קל", "moderate": "בינוני", "hard": "קשה", "very_hard": "קשה מאוד"}
LEVELS = ["easy", "moderate", "hard", "very_hard"]
SAC_LEVEL = {"hiking": "easy", "mountain_hiking": "moderate", "demanding_mountain_hiking": "hard",
             "alpine_hiking": "very_hard", "demanding_alpine_hiking": "very_hard", "difficult_alpine_hiking": "very_hard"}
#: free-text difficulty of guide sources → level
_DIFF_WORDS = [("קשה מאוד", "very_hard"), ("קשה", "hard"), ("בינוני", "moderate"), ("קל", "easy"),
               ("very", "very_hard"), ("hard", "hard"), ("moder", "moderate"), ("medium", "moderate"), ("easy", "easy")]

SOURCES = {
    "sample": {"name": "נתוני דוגמה", "url": None, "license": None},
    "amudanan": {"name": "עמוד ענן", "url": "https://amudanan.co.il",
                 "license": "תיאור המסלול באדיבות עמוד ענן – לשימוש לפי תנאי האתר"},
}

#: sights that may name a stage break
KEY_SIGHTS = {"spring", "waterfall", "viewpoint", "archaeological", "historic", "peak", "cave", "memorial", "well", "saddle"}
#: listed along a stage (sights + practical points)
SIGHT_SUBS = KEY_SIGHTS | {"attraction", "museum", "inpa_site", "picnic", "camp_site", "cliff", "ridge", "valley",
                           "beach", "tourist_info", "trail_info"}
PRACTICAL_SUBS = {"drinking_water", "toilets", "shelter", "parking", "picnic", "camp_site"}
EMERGENCY_SUBS = {"hospital", "emergency", "police", "fire_station"}
WATER_SUBS = {"drinking_water", "spring", "well"}

EMERGENCY_PHONES = [
    {"label": "מוקד חירום (מכל טלפון)", "number": "112"},
    {"label": "מד״א", "number": "101"},
    {"label": "משטרה – כולל יחידות חילוץ", "number": "100"},
    {"label": "כבאות והצלה", "number": "102"},
]

# ----------------------------------------------------------------------------- small helpers


def _q(con: sqlite3.Connection, sql: str, args: tuple = ()) -> list:
    try:
        return con.execute(sql, args).fetchall()
    except sqlite3.OperationalError:  # table missing in an older dataset
        return []


def _json(text: str | None) -> dict:
    if not text:
        return {}
    try:
        v = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return v if isinstance(v, dict) else {}


def difficulty_level(value: str | None) -> str | None:
    if not value:
        return None
    v = str(value).strip().lower()
    if v in DIFFICULTY_HE:
        return v
    if v in SAC_LEVEL:
        return SAC_LEVEL[v]
    for word, level in _DIFF_WORDS:
        if word in v:
            return level
    return None


def _from(name: str) -> str:
    """Hebrew "from" prefix: "מחניון …" / "מ-Ein Gedi" / "מ-1"."""
    return ("מ" if re.match(r"[א-ת]", name or "") else "מ-") + name


def round5(minutes: float) -> int:
    return max(5, int(round(minutes / 5.0)) * 5)


def tobler_kmh(slope: float) -> float:
    """Tobler's hiking function scaled to 4 km/h on the flat (see module doc)."""
    s = max(-0.6, min(0.6, slope))
    return 6.0 * math.exp(-3.5 * abs(s + 0.05)) * TOBLER_SCALE


def walking_minutes(distance_m: float, ascent_m: float = 0.0) -> float:
    """No DEM: 4 km/h plus Naismith's rule (1 h per 600 m of climb)."""
    return distance_m / 1000.0 / FLAT_KMH * 60.0 + ascent_m / 600.0 * 60.0


# ----------------------------------------------------------------------------- geometry

class Proj:
    """Local equirectangular projection in metres (good to <0.5 % over a hike)."""

    def __init__(self, lat0: float):
        self.kx = math.cos(math.radians(lat0)) * 111_320.0
        self.ky = 110_574.0

    def xy(self, lon: float, lat: float) -> tuple[float, float]:
        return lon * self.kx, lat * self.ky


def _seg_dist(px, py, ax, ay, bx, by) -> tuple[float, float]:
    dx, dy = bx - ax, by - ay
    L = dx * dx + dy * dy
    t = 0.0 if L == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L))
    qx, qy = ax + t * dx, ay + t * dy
    return math.hypot(px - qx, py - qy), t


class EdgeIndex:
    """Grid hash of line edges (projected metres) for fast "what is near this point"."""

    def __init__(self, cell: float = 120.0):
        self.cell = cell
        self.grid: dict[tuple[int, int], list[int]] = defaultdict(list)
        self.edges: list[tuple[float, float, float, float, Any, int]] = []  # ax, ay, bx, by, ref, edge_no

    def add(self, pts: Sequence[tuple[float, float]], ref: Any) -> None:
        c = self.cell
        for i in range(len(pts) - 1):
            (ax, ay), (bx, by) = pts[i], pts[i + 1]
            k = len(self.edges)
            self.edges.append((ax, ay, bx, by, ref, i))
            for gx in range(int(math.floor(min(ax, bx) / c)), int(math.floor(max(ax, bx) / c)) + 1):
                for gy in range(int(math.floor(min(ay, by) / c)), int(math.floor(max(ay, by) / c)) + 1):
                    self.grid[(gx, gy)].append(k)

    def near(self, x: float, y: float, r: float) -> list[tuple[float, Any, int, float]]:
        """(distance, ref, edge_no, t) of every edge within r."""
        c = self.cell
        seen: set[int] = set()
        out = []
        for gx in range(int(math.floor((x - r) / c)), int(math.floor((x + r) / c)) + 1):
            for gy in range(int(math.floor((y - r) / c)), int(math.floor((y + r) / c)) + 1):
                for k in self.grid.get((gx, gy), ()):
                    if k in seen:
                        continue
                    seen.add(k)
                    ax, ay, bx, by, ref, i = self.edges[k]
                    d, t = _seg_dist(x, y, ax, ay, bx, by)
                    if d <= r:
                        out.append((d, ref, i, t))
        return out


def chain_lines(lines: Sequence[Sequence[tuple[float, float]]], max_gap: float = 250.0) -> list[tuple[float, float]]:
    """Join a trail's pieces into one walking line (greedy, from the longest piece)."""
    pieces = [list(line) for line in lines if len(line) >= 2]
    if not pieces:
        return []
    pieces.sort(key=geo.line_length_m, reverse=True)
    out = pieces.pop(0)
    while pieces:
        best = None
        for i, p in enumerate(pieces):
            for at_end in (True, False):
                anchor = out[-1] if at_end else out[0]
                for rev in (False, True):
                    q = p[::-1] if rev else p
                    tip = q[0] if at_end else q[-1]
                    d = geo.haversine(anchor[1], anchor[0], tip[1], tip[0])
                    if best is None or d < best[0]:
                        best = (d, i, at_end, q)
        if best is None or best[0] > max_gap:
            break
        _, i, at_end, q = best
        pieces.pop(i)
        out = out + q[1:] if at_end else q[:-1] + out
    return out


class Line:
    """A walking line with cumulative distances, point location and slicing."""

    def __init__(self, coords: Sequence[tuple[float, float]]):
        pts = [coords[0]]
        for c in coords[1:]:
            if c != pts[-1]:
                pts.append(c)
        self.coords = pts
        lat0 = sum(c[1] for c in pts) / len(pts)
        self.proj = Proj(lat0)
        self.xy = [self.proj.xy(*c) for c in pts]
        self.cum = [0.0]
        for (lo0, la0), (lo1, la1) in zip(pts, pts[1:]):
            self.cum.append(self.cum[-1] + geo.haversine(la0, lo0, la1, lo1))
        self.total = self.cum[-1]
        self.index = EdgeIndex(cell=max(80.0, min(400.0, self.total / 200.0 or 80.0)))
        self.index.add(self.xy, 0)

    def locate(self, lon: float, lat: float, r: float, after: float = -1.0) -> tuple[float, float] | None:
        """(distance from the line, position along it) of the nearest point, or None beyond *r*.

        With *after* ≥ 0 the first passage after that position is preferred (out-and-back / loops)."""
        x, y = self.proj.xy(lon, lat)
        hits = self.index.near(x, y, r)
        if not hits:
            return None
        cands = []
        for d, _, i, t in hits:
            seg = self.cum[i + 1] - self.cum[i]
            cands.append((d, self.cum[i] + t * seg))
        best = min(cands)
        if after >= 0:
            later = [c for c in cands if c[1] >= after - 1 and c[0] <= best[0] + 60]
            if later:
                return min(later, key=lambda c: c[1])
        return best

    def point_at(self, at: float) -> tuple[float, float]:
        at = max(0.0, min(self.total, at))
        lo, hi = 0, len(self.cum) - 1
        while lo < hi - 1:
            mid = (lo + hi) // 2
            if self.cum[mid] <= at:
                lo = mid
            else:
                hi = mid
        seg = self.cum[hi] - self.cum[lo]
        t = 0.0 if seg == 0 else (at - self.cum[lo]) / seg
        (x0, y0), (x1, y1) = self.coords[lo], self.coords[hi]
        return x0 + (x1 - x0) * t, y0 + (y1 - y0) * t

    def slice(self, a: float, b: float) -> list[tuple[float, float]]:
        out = [self.point_at(a)]
        out += [c for c, d in zip(self.coords, self.cum) if a < d < b]
        out.append(self.point_at(b))
        return out


# ----------------------------------------------------------------------------- astronomy

def sun_times(lat: float, lon: float, day: dt.date) -> tuple[dt.datetime | None, dt.datetime | None]:
    """Sunrise / sunset (UTC) – the standard sunrise equation, ±1–2 minutes."""
    jdate = (dt.datetime(day.year, day.month, day.day, 12, tzinfo=dt.timezone.utc).timestamp() / 86400.0) + 2440587.5
    n = round(jdate - 2451545.0 + 0.0008)
    jstar = n - lon / 360.0
    m = (357.5291 + 0.98560028 * jstar) % 360.0
    mr = math.radians(m)
    c = 1.9148 * math.sin(mr) + 0.0200 * math.sin(2 * mr) + 0.0003 * math.sin(3 * mr)
    lam = math.radians((m + c + 180.0 + 102.9372) % 360.0)
    jtransit = 2451545.0 + jstar + 0.0053 * math.sin(mr) - 0.0069 * math.sin(2 * lam)
    sin_d = math.sin(lam) * math.sin(math.radians(23.4397))
    cos_d = math.cos(math.asin(sin_d))
    phi = math.radians(lat)
    cos_w = (math.sin(math.radians(-0.833)) - math.sin(phi) * sin_d) / (math.cos(phi) * cos_d)
    if not -1.0 <= cos_w <= 1.0:
        return None, None
    w = math.degrees(math.acos(cos_w))

    def to_dt(j: float) -> dt.datetime:
        return dt.datetime.fromtimestamp((j - 2440587.5) * 86400.0, tz=dt.timezone.utc)

    return to_dt(jtransit - w / 360.0), to_dt(jtransit + w / 360.0)


def _tz():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("Asia/Jerusalem")
    except Exception:  # noqa: BLE001 - no tz database: Israel standard time
        return dt.timezone(dt.timedelta(hours=2))


TZ = _tz()


def daylight(lat: float, lon: float, day: dt.date, duration_min: float | None) -> dict:
    rise, sset = sun_times(lat, lon, day)
    if rise is None or sset is None:
        return {"date": day.isoformat()}
    rise, sset = rise.astimezone(TZ), sset.astimezone(TZ)
    out: dict[str, Any] = {"date": day.isoformat(), "sunrise": rise.strftime("%H:%M"), "sunset": sset.strftime("%H:%M"),
                           "timezone": "Asia/Jerusalem", "day_length_min": round((sset - rise).total_seconds() / 60)}
    note = f"השקיעה היום ({day.strftime('%d/%m')}) בשעה {out['sunset']}. יש לתכנן לסיים את המסלול לפני החשכה."
    if duration_min:
        latest = sset - dt.timedelta(minutes=duration_min + 30)
        out["latest_start"] = latest.strftime("%H:%M")
        if latest <= rise:
            out["too_long_for_daylight"] = True
            note += " המסלול ארוך משעות האור של יום אחד – מומלץ לחלק אותו לכמה ימים או לקצר."
        else:
            note += f" כדי לסיים באור יום (עם מרווח של חצי שעה) מומלץ לצאת לא יאוחר מ-{out['latest_start']}."
    out["note"] = note
    return out


# ----------------------------------------------------------------------------- data lookups

def _chunks(line: Line, size: float = 5000.0) -> list[tuple[float, float, float, float]]:
    """Bounding boxes of ~5 km pieces of the line (keeps R*Tree queries small on long routes)."""
    boxes = []
    a = 0.0
    while True:
        b = min(line.total, a + size)
        boxes.append(geo.bbox_of(line.slice(a, b)))
        if b >= line.total:
            break
        a = b
    return boxes


def _pad(box, metres: float):
    dlat = metres / 111_320.0
    dlon = metres / (111_320.0 * max(0.2, math.cos(math.radians(box[1]))))
    return box[0] - dlon, box[1] - dlat, box[2] + dlon, box[3] + dlat


def _pois_near(con: sqlite3.Connection, line: Line, subs: Iterable[str], pad: float) -> list[tuple]:
    subs = sorted(set(subs))
    seen: dict[int, tuple] = {}
    for box in _chunks(line):
        minx, miny, maxx, maxy = _pad(box, pad)
        for r in _q(con, "SELECT p.id, p.name, p.category, p.sub, p.kind_he, p.lat, p.lon, p.image, p.importance, p.description "
                         "FROM pois_rtree t JOIN pois p ON p.id = t.id "
                         "WHERE t.min_lon <= ? AND t.max_lon >= ? AND t.min_lat <= ? AND t.max_lat >= ? "
                         f"AND p.sub IN ({','.join('?' * len(subs))}) LIMIT 2000", (maxx, minx, maxy, miny, *subs)):
            seen[r[0]] = tuple(r)
    return list(seen.values())


def _short_text(con: sqlite3.Connection, entity: str, ref: int, fallback: str | None = None, limit: int = 220) -> dict | None:
    rows = _q(con, "SELECT source, text, url, license FROM texts WHERE entity = ? AND ref_id = ? "
                   "ORDER BY CASE source WHEN 'wikipedia' THEN 0 ELSE 1 END", (entity, ref))
    if rows:
        src, text, url, lic = rows[0]
        return {"text": _clip(text.split("\n\n")[0], limit), "source": src,
                "source_he": rich.TEXT_SOURCE_HE.get(src, src), "url": url, "license": lic}
    if fallback:
        return {"text": _clip(fallback, limit), "source": "osm", "source_he": "OpenStreetMap", "url": None, "license": "ODbL"}
    return None


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    dot = max(cut.rfind(". "), cut.rfind("׃ "))
    return cut[:dot + 1] if dot > limit * 0.5 else cut.rsplit(" ", 1)[0] + "…"


def _poi_item(con, r, dist: float, at: float, with_text: bool = True) -> dict:
    pid, name, cat, sub, kind_he, lat, lon, image, importance, desc = r
    s = categories.SUB_BY_ID.get(sub)
    c = categories.CATEGORY_BY_ID.get(cat)
    sub_he = (kind_he or s.name_he) if s else ""
    item = {"id": f"poi-{pid}", "type": "poi", "name": name or sub_he, "named": bool(name), "sub": sub,
            "sub_category_he": sub_he, "secondary": sub_he, "icon": s.icon if s else "pin",
            "color": c.color if c else "#4B5563", "location": {"lat": lat, "lon": lon},
            "distance": round(dist), "at_m": round(at), "importance": importance,
            "thumbnail": rich.image_src(image, 160) if image else None}
    if with_text and name:
        t = _short_text(con, "poi", pid, desc)
        if t:
            item["text"] = t
    return item


def _trails_near(con: sqlite3.Connection, line: Line) -> tuple[EdgeIndex, dict[int, dict]]:
    idx = EdgeIndex(cell=120.0)
    info: dict[int, dict] = {}
    seen: set[int] = set()
    from .outdoor import MARK_HEX
    for box in _chunks(line):
        minx, miny, maxx, maxy = _pad(box, 60)
        for sid, tid, name, route, mark, network, imp, geom in _q(
                con, "SELECT g.id, t.id, t.name, t.route, t.mark, t.network, t.importance, g.geom FROM trail_segments_rtree r "
                     "JOIN trail_segments g ON g.id = r.id JOIN trails t ON t.id = g.trail_id "
                     "WHERE r.min_lon <= ? AND r.max_lon >= ? AND r.min_lat <= ? AND r.max_lat >= ? LIMIT 3000",
                (maxx, minx, maxy, miny)):
            if sid in seen:
                continue
            seen.add(sid)
            if route in BIKE_ROUTES:
                continue  # a hiking guide follows hiking marks
            idx.add([line.proj.xy(*c) for c in geo.decode_polyline(geom)], tid)
            info[tid] = {"id": f"trail-{tid}", "trail_id": tid, "name": display_name(name, route, mark), "named": bool(name),
                         "mark": mark, "mark_he": MARK_HE.get(mark) or None, "network": network,
                         "network_he": NETWORK_HE.get(network or ""), "color": MARK_HEX.get(mark, MARK_HEX["other"]),
                         "importance": imp}
    return idx, info


def _streams_near(con: sqlite3.Connection, line: Line) -> list[tuple[str, EdgeIndex]]:
    out = []
    seen: set[int] = set()
    for box in _chunks(line):
        minx, miny, maxx, maxy = _pad(box, STREAM_RADIUS)
        for pid, name, gj in _q(con, "SELECT p.id, p.name, s.geom FROM pois_rtree t JOIN pois p ON p.id = t.id "
                                     "JOIN poi_shapes s ON s.poi_id = p.id WHERE t.min_lon <= ? AND t.max_lon >= ? "
                                     "AND t.min_lat <= ? AND t.max_lat >= ? AND p.sub = 'stream' AND p.name IS NOT NULL",
                                (maxx, minx, maxy, miny)):
            if pid in seen:
                continue
            seen.add(pid)
            g = _json(gj)
            parts = g.get("coordinates") or []
            if g.get("type") == "LineString":
                parts = [parts]
            if g.get("type") not in ("LineString", "MultiLineString"):
                continue
            idx = EdgeIndex(cell=150.0)
            for part in parts:
                idx.add([line.proj.xy(c[0], c[1]) for c in part], pid)
            out.append((name, idx))
    return out


def _nearest_place(con: sqlite3.Connection, lat: float, lon: float, radius: float = 8000) -> dict | None:
    minx, miny, maxx, maxy = geo.bbox_around(lat, lon, radius)
    best = None
    for name, kind, plat, plon in _q(con, "SELECT p.name, p.kind, p.lat, p.lon FROM places_rtree t JOIN places p ON p.id = t.id "
                                          "WHERE t.min_lon <= ? AND t.max_lon >= ? AND t.min_lat <= ? AND t.max_lat >= ? "
                                          "AND p.kind IN ('city','town','village','hamlet','isolated_dwelling','locality')",
                                     (maxx, minx, maxy, miny)):
        d = geo.haversine(lat, lon, plat, plon)
        if d <= radius and (best is None or d < best["distance"]):
            best = {"name": name, "kind": kind, "distance": round(d)}
    return best


def _emergency_near(con: sqlite3.Connection, lat: float, lon: float, radius: float = 25000, limit: int = 4) -> list[dict]:
    minx, miny, maxx, maxy = geo.bbox_around(lat, lon, radius)
    subs = sorted(EMERGENCY_SUBS)
    found = []
    for r in _q(con, "SELECT p.id, p.name, p.category, p.sub, p.kind_he, p.lat, p.lon, p.phone FROM pois_rtree t "
                     "JOIN pois p ON p.id = t.id WHERE t.min_lon <= ? AND t.max_lon >= ? AND t.min_lat <= ? AND t.max_lat >= ? "
                     f"AND p.sub IN ({','.join('?' * len(subs))}) AND p.name IS NOT NULL LIMIT 400",
                (maxx, minx, maxy, miny, *subs)):
        d = geo.haversine(lat, lon, r[5], r[6])
        if d <= radius:
            s = categories.SUB_BY_ID.get(r[3])
            found.append({"id": f"poi-{r[0]}", "type": "poi", "name": r[1], "sub": r[3],
                          "sub_category_he": (r[4] or s.name_he) if s else "", "phone": r[7],
                          "location": {"lat": r[5], "lon": r[6]}, "distance": round(d)})
    # the nearest of each kind first (a hospital is more useful than a 3rd police station)
    found.sort(key=lambda x: x["distance"])
    out, kinds = [], set()
    for f in found:
        if f["sub"] not in kinds:
            out.append(f)
            kinds.add(f["sub"])
    for f in found:
        if len(out) >= limit:
            break
        if f not in out:
            out.append(f)
    return out[:limit]


# ----------------------------------------------------------------------------- profile

def _profile(line: Line, elevation) -> dict:
    """Samples along the line: positions, coordinates, elevations (smoothed) and cumulative climb / time."""
    from .elevation import smooth

    step = max(25.0, min(250.0, line.total / 800.0))
    n = max(2, int(line.total / step) + 1)
    pos = [line.total * i / (n - 1) for i in range(n)]
    pts = [line.point_at(a) for a in pos]
    ele = None
    if elevation is not None:
        try:
            ele = smooth([elevation.at(lat, lon) for lon, lat in pts])
        except Exception:  # noqa: BLE001 - a broken tile must not break the guide
            ele = None
    up = [0.0] * n
    down = [0.0] * n
    tmin = [0.0] * n
    if ele:
        ref = ele[0]
        u = d = 0.0
        for i in range(1, n):
            v = ele[i]
            if v - ref >= 2.0:
                u += v - ref
                ref = v
            elif ref - v >= 2.0:
                d += ref - v
                ref = v
            up[i], down[i] = u, d
            seg = pos[i] - pos[i - 1]
            slope = (ele[i] - ele[i - 1]) / seg if seg > 0 else 0.0
            tmin[i] = tmin[i - 1] + seg / 1000.0 / tobler_kmh(slope) * 60.0
    else:
        for i in range(1, n):
            tmin[i] = tmin[i - 1] + (pos[i] - pos[i - 1]) / 1000.0 / FLAT_KMH * 60.0
    return {"pos": pos, "pts": pts, "ele": ele, "up": up, "down": down, "t": tmin, "step": step}


def _interp(prof: dict, key: str, at: float) -> float:
    pos, arr = prof["pos"], prof[key]
    if at <= 0:
        return arr[0]
    if at >= pos[-1]:
        return arr[-1]
    step = pos[1] - pos[0]
    i = min(len(pos) - 2, int(at / step))
    f = (at - pos[i]) / step if step else 0.0
    return arr[i] + (arr[i + 1] - arr[i]) * f


def _max_grade(prof: dict, window: float = 200.0) -> float:
    ele = prof["ele"]
    if not ele:
        return 0.0
    k = max(1, int(round(window / prof["step"])))
    best = 0.0
    for i in range(0, len(ele) - k):
        d = prof["pos"][i + k] - prof["pos"][i]
        if d > 0:
            best = max(best, abs(ele[i + k] - ele[i]) / d)
    return best


# ----------------------------------------------------------------------------- the engine

def build_guide(con: sqlite3.Connection, coords: Sequence[tuple[float, float]], info: dict | None = None,
                elevation=None, day: dt.date | None = None) -> dict:
    """Structured guide for a walking line (lon, lat), start → end.

    *info* (all optional): kind, id, name, description, difficulty, duration_min, circular, season,
    waypoints [{name, lat, lon, note}], tips [str], water, parking, shade, url, license, source,
    ascent (m, from tags), gallery, texts, entity / ref_id (for photos & texts).
    *elevation*: an object with ``at(lat, lon)`` (hmaps.api.elevation.Elevation) or None.
    """
    info = dict(info or {})
    if len(coords) < 2:
        raise ValueError("route needs at least two points")
    line = Line(coords)
    if line.total < 20:
        raise ValueError("route too short")
    if line.total > MAX_ROUTE_M:
        raise ValueError("route too long")
    total = line.total
    prof = _profile(line, elevation)
    has_dem = prof["ele"] is not None
    start_pt, end_pt = line.coords[0], line.coords[-1]
    loop_gap = geo.haversine(start_pt[1], start_pt[0], end_pt[1], end_pt[0])
    circular = info.get("circular")
    loop = bool(circular) if circular is not None else loop_gap < 300

    # --- marked trails followed (per sample)
    tindex, tinfo = _trails_near(con, line)
    per_sample: list[int | None] = []
    coverage: dict[int, float] = defaultdict(float)
    sample_sets = []
    for lon, lat in prof["pts"]:
        x, y = line.proj.xy(lon, lat)
        ids = {ref for _, ref, _, _ in tindex.near(x, y, TRAIL_RADIUS)}
        sample_sets.append(ids)
        for t in ids:
            coverage[t] += prof["step"]
    for ids in sample_sets:
        # a stable choice: the trail that the route follows longest (then the most important)
        per_sample.append(max(ids, key=lambda t: (coverage[t], tinfo[t]["importance"])) if ids else None)
    runs = _runs(per_sample, prof["step"], min_run=200.0)

    # --- streams
    streams = _streams_near(con, line)
    stream_at: list[str | None] = []
    for lon, lat in prof["pts"]:
        x, y = line.proj.xy(lon, lat)
        name = None
        for sname, sidx in streams:
            if sidx.near(x, y, STREAM_RADIUS):
                name = sname
                break
        stream_at.append(name)
    stream_share = sum(1 for s in stream_at if s) / len(stream_at)

    # --- POIs along the route
    rows = _pois_near(con, line, SIGHT_SUBS | PRACTICAL_SUBS, PRACTICAL_RADIUS)
    along: list[dict] = []
    for r in rows:
        sub = r[3]
        radius = PRACTICAL_RADIUS if sub in PRACTICAL_SUBS else POI_RADIUS
        hit = line.locate(r[6], r[5], radius)
        if hit is None:
            continue
        if not r[1] and sub not in PRACTICAL_SUBS | WATER_SUBS | {"viewpoint"}:
            continue
        along.append(_poi_item(con, r, hit[0], hit[1]))
    along.sort(key=lambda x: (x["at_m"], -x["importance"]))

    # --- start / end names
    waypoints = []
    last = 0.0
    for w in info.get("waypoints") or []:
        try:
            lat, lon = float(w["lat"]), float(w["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        name = str(w.get("name") or "").strip()
        if not name:
            continue
        hit = line.locate(lon, lat, 300.0, after=last)
        if hit is None or hit[1] < last - 50:  # off the route, or listed out of walking order
            continue
        last = hit[1]
        waypoints.append({"name": name, "note": (w.get("note") or None), "at_m": hit[1], "lat": lat, "lon": lon})

    def end_name(at: float, which: str) -> dict:
        lon, lat = line.point_at(at)
        for w in waypoints:
            if abs(w["at_m"] - at) <= 200:
                return {"name": w["name"], "note": w["note"], "lat": lat, "lon": lon, "at_m": round(at), "source": "waypoint"}
        park = [p for p in along if p["sub"] == "parking" and abs(p["at_m"] - at) <= 400]
        if park:
            p = min(park, key=lambda p: abs(p["at_m"] - at))
            nm = p["name"] if p["named"] else "חניון"
            return {"name": nm, "lat": lat, "lon": lon, "at_m": round(at), "source": "parking", "poi": p["id"]}
        named = [p for p in along if p["named"] and abs(p["at_m"] - at) <= 250 and p["sub"] != "trail_info"]
        if named:
            p = min(named, key=lambda p: (abs(p["at_m"] - at), -p["importance"]))
            return {"name": p["name"], "lat": lat, "lon": lon, "at_m": round(at), "source": "poi", "poi": p["id"]}
        place = _nearest_place(con, lat, lon, 3000)
        if place:
            nm = place["name"] if place["distance"] < 600 else f"{'נקודת ההתחלה' if which == 'start' else 'נקודת הסיום'} ליד {place['name']}"
            return {"name": nm, "lat": lat, "lon": lon, "at_m": round(at), "source": "place"}
        return {"name": "נקודת ההתחלה" if which == "start" else "נקודת הסיום", "lat": lat, "lon": lon, "at_m": round(at),
                "source": "generic"}

    start = end_name(0.0, "start")
    end = dict(start, at_m=round(total)) if loop and loop_gap < 300 else end_name(total, "end")
    if loop and loop_gap < 300:
        end["name"] = start["name"]
        end["loop"] = True

    # --- stage breaks
    breaks = _breaks(line, prof, waypoints, runs, tinfo, along)
    stages = _stages(con, line, prof, breaks, start, end, per_sample, tinfo, stream_at, along, has_dem)

    # --- summary
    ascent = round(prof["up"][-1]) if has_dem else None
    descent = round(prof["down"][-1]) if has_dem else None
    if has_dem:
        minutes = prof["t"][-1]
        formula = "tobler"
    else:
        minutes = walking_minutes(total, float(info.get("ascent") or 0))
        formula = "naismith" if info.get("ascent") else "flat"
    level_src = "source"
    level = difficulty_level(info.get("difficulty"))
    max_grade = _max_grade(prof)
    if not level:
        level_src = "estimated"
        effort = total / 1000.0 + (ascent or float(info.get("ascent") or 0)) / 100.0
        level = "easy" if effort < 8 else "moderate" if effort < 15 else "hard" if effort < 24 else "very_hard"
        if max_grade > 0.25 and level != "very_hard":
            level = LEVELS[LEVELS.index(level) + 1]
    marks = []
    for tid, m in sorted(coverage.items(), key=lambda kv: -kv[1]):
        share = min(1.0, m / total)
        if share >= 0.05:
            marks.append({**{k: v for k, v in tinfo[tid].items() if k != "importance"}, "share": round(share, 2),
                          "length_m": round(min(m, total))})
    marked_share = sum(1 for t in per_sample if t is not None) / len(per_sample)
    duration = round5(minutes)
    summary = {
        "length_m": round(total), "length_km": round(total / 1000, 1), "length_text": format_distance(total),
        "duration_min": duration, "duration_text": format_duration(duration * 60), "time_formula": formula,
        "time_formula_he": {"tobler": "נוסחת טובלר (לפי שיפוע), מכוילת ל-4 קמ״ש בשטח מישורי",
                            "naismith": "4 קמ״ש ועוד שעה לכל 600 מ׳ עלייה (כלל נייסמית)",
                            "flat": "4 קמ״ש, ללא נתוני גובה"}[formula],
        "duration_source_min": info.get("duration_min"),
        "ascent": ascent, "descent": descent,
        "min_ele": round(min(prof["ele"])) if has_dem else None, "max_ele": round(max(prof["ele"])) if has_dem else None,
        "start_ele": round(prof["ele"][0]) if has_dem else None, "end_ele": round(prof["ele"][-1]) if has_dem else None,
        "max_grade_pct": round(max_grade * 100) if has_dem else None,
        "difficulty": level, "difficulty_he": DIFFICULTY_HE[level], "difficulty_source": level_src,
        "loop": loop, "type_he": "מסלול מעגלי" if loop else "מסלול קווי (בכיוון אחד)",
        "marks": marks, "marked_share": round(marked_share, 2),
        "stream": stream_share >= 0.25 or "נחל" in (info.get("name") or "") and stream_share > 0.05,
        "season": info.get("season"), "stages": len(stages),
    }

    lat0, lon0 = start["lat"], start["lon"]
    day = day or dt.datetime.now(TZ).date()
    practical = _practical(con, info, summary, along, start, end, lat0, lon0, day, total)
    elev_profile = None
    if has_dem:
        k = max(1, len(prof["pos"]) // 200)
        idx = list(range(0, len(prof["pos"]), k))
        if idx[-1] != len(prof["pos"]) - 1:
            idx.append(len(prof["pos"]) - 1)
        elev_profile = {
            "distance": round(total, 1), "ascent": ascent, "descent": descent,
            "min": summary["min_ele"], "max": summary["max_ele"], "start": summary["start_ele"], "end": summary["end_ele"],
            "profile": [[round(prof["pos"][i], 1), round(prof["ele"][i], 1), round(prof["pts"][i][0], 6), round(prof["pts"][i][1], 6)]
                        for i in idx],
            "stage_breaks": [s["to"]["at_m"] for s in stages[:-1]],
        }

    sights = [a for a in along if a["sub"] not in PRACTICAL_SUBS and a["sub"] != "trail_info"]
    for a in along:
        a.pop("importance", None)
    used_texts = [s["text"] for s in sights if s.get("text") and s["text"]["source"] != "osm"]
    bbox = list(geo.bbox_of(line.coords))
    return {
        "kind": info.get("kind", "route"), "ref": info.get("id"), "name": info.get("name") or "מסלול הליכה",
        "description": info.get("description"),
        "paragraphs": [p.strip() for p in (info.get("description") or "").split("\n\n") if p.strip()],
        "summary": summary, "start": start, "end": end,
        "stages": stages, "sights": sights,
        "practical": practical,
        "elevation": elev_profile,
        "waypoints": [{**w, "at_m": round(w["at_m"])} for w in waypoints],
        "bbox": bbox,
        "geometry": {"type": "LineString", "coordinates": [[round(x, 6), round(y, 6)] for x, y in geo.simplify(line.coords, 3.0)]},
        "media": {"gallery": info.get("gallery") or []},
        "texts": info.get("texts") or [],
        "credits": _credits(info, has_dem, used_texts, bool(tinfo) or bool(along)),
        "disclaimer": "המדריך נוצר אוטומטית מנתוני המפה ומקורות פתוחים. לפני היציאה יש לבדוק את מצב השביל, "
                      "הסימון בשטח, מזג האוויר והודעות רשות הטבע והגנים. אין להסתמך על המדריך כמפת סימון שבילים רשמית.",
    }


def _runs(per_sample: list, step: float, min_run: float) -> list[list]:
    """Run-length encoding [value, first, last] with runs shorter than *min_run* merged into the previous one."""
    runs: list[list] = []
    for i, v in enumerate(per_sample):
        if runs and runs[-1][0] == v:
            runs[-1][2] = i
        else:
            runs.append([v, i, i])
    changed = True
    while changed and len(runs) > 1:
        changed = False
        for k, r in enumerate(runs):
            if (r[2] - r[1] + 1) * step < min_run:
                if k > 0:
                    runs[k - 1][2] = r[2]
                else:
                    runs[1][1] = r[1]
                runs.pop(k)
                changed = True
                break
        merged: list[list] = []
        for r in runs:
            if merged and merged[-1][0] == r[0]:
                merged[-1][2] = r[2]
            else:
                merged.append(r)
        runs = merged
    return runs


def _breaks(line: Line, prof: dict, waypoints: list[dict], runs: list[list], tinfo: dict, along: list[dict]) -> list[dict]:
    total = line.total
    min_gap = max(400.0, min(1500.0, total / 12.0))
    sight_gap = max(800.0, min(3000.0, total / 6.0))
    breaks: list[dict] = []

    def free(at: float, gap: float) -> bool:
        return gap <= at <= total - gap and all(abs(at - b["at_m"]) >= gap for b in breaks)

    for w in waypoints:  # the source's own waypoints
        if free(w["at_m"], 150.0):
            breaks.append({"at_m": w["at_m"], "name": w["name"], "note": w.get("note"), "kind": "waypoint"})
    pos = prof["pos"]
    for k in range(1, len(runs)):  # mark changes
        prev, cur = runs[k - 1], runs[k]
        at = pos[cur[1]]
        if not free(at, min_gap):
            continue
        a = tinfo.get(prev[0]) if prev[0] is not None else None
        b = tinfo.get(cur[0]) if cur[0] is not None else None
        guide = [p for p in along if p["sub"] == "trail_info" and p["named"] and abs(p["at_m"] - at) <= 150]
        if guide:
            name = guide[0]["name"]
        elif a and b:
            name = f"צומת שבילים ({_mark_word(a)} ← {_mark_word(b)})"
        elif b:
            name = f"ההצטרפות ל{_trail_word(b)}"
        else:
            name = f"היציאה מ{_trail_word(a)}" if a else "צומת"
        breaks.append({"at_m": at, "name": name, "kind": "junction"})
    sights = sorted((p for p in along if p["named"] and p["sub"] in KEY_SIGHTS), key=lambda p: -p["importance"])
    for p in sights:  # notable sights
        if free(p["at_m"], sight_gap):
            breaks.append({"at_m": float(p["at_m"]), "name": p["name"], "kind": "sight", "poi": p["id"]})
    # a walk of 2.5 km or more gets at least two stages when something worth naming is near its middle
    if not breaks and total >= 2500:
        mid = total / 2
        cands = [p for p in along if total / 4 <= p["at_m"] <= total * 3 / 4 and p["named"]]
        if cands:
            p = min(cands, key=lambda p: (abs(p["at_m"] - mid) / total - p["importance"] * 0.3))
            breaks.append({"at_m": float(p["at_m"]), "name": p["name"], "kind": "sight", "poi": p["id"]})
    # long legs: split at the point of interest nearest the middle, else evenly
    for _ in range(200):
        cuts = sorted([0.0] + [b["at_m"] for b in breaks] + [total])
        long_leg = next(((a, b) for a, b in zip(cuts, cuts[1:]) if b - a > MAX_STAGE_M), None)
        if long_leg is None:
            break
        a, b = long_leg
        mid = (a + b) / 2
        cands = [p for p in along if a + 500 <= p["at_m"] <= b - 500 and (p["named"] or p["sub"] in WATER_SUBS | {"shelter"})]
        cands = [p for p in cands if abs(p["at_m"] - mid) <= (b - a) / 4]
        if cands:
            p = min(cands, key=lambda p: (abs(p["at_m"] - mid), -p["importance"]))
            breaks.append({"at_m": float(p["at_m"]), "name": p["name"], "kind": "sight", "poi": p["id"]})
            continue
        parts = math.ceil((b - a) / EVEN_SPLIT_M)
        for i in range(1, parts):
            at = a + (b - a) * i / parts
            near = [p for p in along if p["named"] and abs(p["at_m"] - at) <= 300]
            name = near[0]["name"] if near else f"נקודת ק״מ {at / 1000:.1f}"
            breaks.append({"at_m": at, "name": name, "kind": "distance", **({"poi": near[0]["id"]} if near else {})})
    breaks.sort(key=lambda b: b["at_m"])
    return breaks


def _mark_word(t: dict) -> str:
    if t.get("named") and t.get("network") in ("nwn", "rwn"):
        return t["name"]
    return t.get("mark_he") or t["name"]


def _trail_word(t: dict) -> str:
    if t.get("named") and t.get("network") in ("nwn", "rwn"):
        return t["name"]
    return f"שביל המסומן {t['mark_he']}" if t.get("mark_he") else t["name"]


def _stages(con, line: Line, prof: dict, breaks: list[dict], start: dict, end: dict, per_sample: list, tinfo: dict,
            stream_at: list, along: list[dict], has_dem: bool) -> list[dict]:
    total = line.total
    points = [start] + [dict(b, **dict(zip(("lon", "lat"), line.point_at(b["at_m"])))) for b in breaks] + [end]
    step = prof["step"]
    out = []
    for i in range(len(points) - 1):
        a, b = points[i], points[i + 1]
        fa, fb = float(a["at_m"]), float(b["at_m"]) if i + 1 < len(points) - 1 else total
        dist = fb - fa
        ia, ib = int(fa / step), min(len(per_sample) - 1, max(int(fa / step), int(math.ceil(fb / step))))
        cov: dict[int, int] = defaultdict(int)
        streams: dict[str, int] = defaultdict(int)
        for k in range(ia, ib + 1):
            if per_sample[k] is not None:
                cov[per_sample[k]] += 1
            if stream_at[k]:
                streams[stream_at[k]] += 1
        n = ib - ia + 1
        stage_marks = [{**{kk: vv for kk, vv in tinfo[t].items() if kk != "importance"}, "share": round(c / n, 2)}
                       for t, c in sorted(cov.items(), key=lambda kv: -kv[1]) if c / n >= 0.2]
        stream = max(streams, key=streams.get) if streams and max(streams.values()) / n >= 0.4 else None
        up = _interp(prof, "up", fb) - _interp(prof, "up", fa) if has_dem else None
        down = _interp(prof, "down", fb) - _interp(prof, "down", fa) if has_dem else None
        minutes = _interp(prof, "t", fb) - _interp(prof, "t", fa)
        eles = prof["ele"][ia:ib + 1] if has_dem else None
        sights = [p for p in along if fa <= p["at_m"] < fb or (i == len(points) - 2 and p["at_m"] >= fa)]
        to_poi = b.get("poi")
        geom = geo.simplify(line.slice(fa, fb), 3.0)
        stage = {
            "n": i + 1,
            "from": {k: a.get(k) for k in ("name", "lat", "lon", "at_m", "note") if a.get(k) is not None},
            "to": {k: b.get(k) for k in ("name", "lat", "lon", "at_m", "note", "kind", "poi", "loop") if b.get(k) is not None},
            "distance_m": round(dist), "distance_text": format_distance(dist),
            "duration_min": max(1, round(minutes)), "duration_text": format_duration(minutes * 60),
            "ascent": round(up) if up is not None else None, "descent": round(down) if down is not None else None,
            "min_ele": round(min(eles)) if eles else None, "max_ele": round(max(eles)) if eles else None,
            "marks": stage_marks, "color": stage_marks[0]["color"] if stage_marks else None,
            "stream": stream,
            "sights": [p for p in sights if p["id"] != to_poi or p["sub"] in PRACTICAL_SUBS],
            "bbox": list(geo.bbox_of(geom)),
            "geometry": {"type": "LineString", "coordinates": [[round(x, 6), round(y, 6)] for x, y in geom]},
        }
        stage["to"]["at_m"] = round(fb)
        stage["from"]["at_m"] = round(fa)
        stage["instruction"] = instruction(stage, first=i == 0, last=i == len(points) - 2, trail_data=bool(tinfo))
        out.append(stage)
    return out


def instruction(stage: dict, first: bool = False, last: bool = False, trail_data: bool = False) -> str:
    """One Hebrew sentence (plural, gender neutral), e.g.
    "ממשיכים בשביל המסומן כחול לאורך נחל דוד בעלייה כ-1.2 ק״מ עד עין דוד."."""
    parts = [f"יוצאים {_from(stage['from']['name'])}" if first else "ממשיכים"]
    marks = stage.get("marks") or []
    if marks:
        m = marks[0]
        if m.get("named") and m.get("network") in ("nwn", "rwn"):
            parts.append(f"ב{m['name']}")
        elif m.get("mark_he"):
            parts.append(f"בשביל המסומן {m['mark_he']}")
        else:
            parts.append(f"ב{m['name']}")
        if len(marks) > 1 and marks[1].get("mark_he") and marks[1].get("mark_he") != m.get("mark_he") and marks[1]["share"] >= 0.3:
            parts.append(f"ואחר כך בסימון {marks[1]['mark_he']}")
    elif trail_data:
        parts.append("בקטע שאינו מסומן במפה")
    if stage.get("stream"):
        parts.append(f"לאורך {stage['stream']}")
    up, down, dist = stage.get("ascent"), stage.get("descent"), max(1, stage["distance_m"])
    if up is not None and down is not None:
        net = (up - down) / dist
        if up / dist >= 0.08 and net > 0.04:
            parts.append("בעלייה תלולה" if up / dist >= 0.15 else "בעלייה")
        elif down / dist >= 0.08 and net < -0.04:
            parts.append("בירידה תלולה" if down / dist >= 0.15 else "בירידה")
        elif up + down < dist * 0.02:
            parts.append("בדרך מישורית")
    parts.append(f"כ-{stage['distance_text']}")
    to = stage["to"]["name"]
    if last and stage["to"].get("loop"):
        parts.append(f"וחוזרים אל {to} – סוף המסלול")
    else:
        parts.append(f"עד {to}" + (" – סוף המסלול" if last and not to.startswith("נקודת הסיום") else ""))
    text = " ".join(parts) + "."
    names = [s["name"] for s in stage.get("sights", []) if s.get("named") and s.get("sub") not in PRACTICAL_SUBS
             and s.get("sub") != "trail_info" and s["name"] != to][:3]
    if names:
        text += " בדרך: " + ", ".join(names) + "."
    return text


def _practical(con, info: dict, summary: dict, along: list[dict], start: dict, end: dict, lat: float, lon: float,
               day: dt.date, total: float) -> dict:
    def pick(subs, near_ends=False):
        items = [p for p in along if p["sub"] in subs]
        if near_ends:
            items = [p for p in items if p["at_m"] <= 500 or p["at_m"] >= total - 500]
        return [{k: v for k, v in p.items() if k not in ("text", "importance")} for p in items]

    water = pick({"drinking_water"})
    springs = pick({"spring", "well"})
    hours = summary["duration_min"] / 60.0
    liters = max(1.5, math.ceil(hours * 2) / 2)
    water_note = (f"מומלץ לשאת לפחות {liters:g} ליטר מים לאדם (כליטר לכל שעת הליכה, ויותר בימים חמים)."
                  + ("" if water else " לא ידוע על ברזיית מים לאורך המסלול."))
    if springs:
        water_note += " מי מעיינות אינם בהכרח ראויים לשתייה."
    tips: list[dict] = []

    def tip(kind: str, text: str) -> None:
        tips.append({"kind": kind, "text": text})

    if summary.get("stream"):
        tip("flood", "אין להיכנס לנחלים ולערוצים בזמן גשם או כשיש התראת שיטפונות – שיטפון עלול להגיע גם כשלא יורד גשם במקום.")
    if lat < 31.35 or (lon > 35.3 and lat < 31.85):
        tip("heat", "אזור מדברי וחם: יוצאים מוקדם בבוקר, נמנעים מהליכה בשעות החמות ובימי שרב, וחובשים כובע.")
    if summary["duration_min"] >= 240:
        tip("long", "מסלול ארוך: מומלץ להודיע למישהו על המסלול ועל שעת החזרה המשוערת, ולתכנן נקודות יציאה.")
    if not summary["loop"]:
        tip("shuttle", "המסלול קווי: יש לתאם הקפצת רכב או הסעה בין נקודת ההתחלה לנקודת הסיום.")
    if summary.get("max_grade_pct") and summary["max_grade_pct"] >= 25:
        tip("steep", f"במסלול קטעים תלולים (עד כ-{summary['max_grade_pct']}% שיפוע): נעלי הליכה עם סוליה טובה, וזהירות בירידות.")
    if summary.get("marked_share", 0) < 0.5:
        tip("navigation", "חלקים גדולים מהמסלול אינם על שביל מסומן במפה – מומלץ לשמור את המפה במכשיר ולהיעזר בניווט.")
    tip("general", "לפני היציאה: בודקים תחזית מזג אוויר, הודעות רשות הטבע והגנים וסגירת שטחי אש, והולכים רק בשבילים המסומנים.")
    for t in info.get("tips") or []:
        if isinstance(t, str) and t.strip():
            tip("source", t.strip())

    shade = info.get("shade")
    shelters = pick({"shelter"})
    if not shade and shelters:
        shade = f"לאורך המסלול {len(shelters)} סככות / מחסות."
    return {
        "water": {"points": water, "springs": springs, "liters": liters, "note": water_note,
                  "source_note": info.get("water")},
        "toilets": pick({"toilets"}),
        "parking": pick({"parking"}, near_ends=True),
        "parking_note": info.get("parking"),
        "shelters": shelters,
        "shade": shade,
        "season": info.get("season"),
        "daylight": daylight(lat, lon, day, summary["duration_min"]),
        "emergency": {"phones": EMERGENCY_PHONES, "nearby": _emergency_near(con, lat, lon),
                      "note": "בשטח ללא קליטה: 112 עובד גם דרך רשת של מפעיל אחר. שתפו את המיקום ממסך \"המיקום שלי\"."},
        "tips": tips,
        "start_area": _nearest_place(con, start["lat"], start["lon"]),
        "end_area": _nearest_place(con, end["lat"], end["lon"]),
    }


def _credits(info: dict, has_dem: bool, used_texts: list[dict], osm: bool) -> list[dict]:
    out = []
    src = info.get("source")
    if src:
        meta = SOURCES.get(src, {"name": src, "url": None, "license": None})
        out.append({"role": "תיאור המסלול", "name": meta["name"], "url": info.get("url") or meta.get("url"),
                    "license": info.get("license") or meta.get("license")})
    out.append({"role": "שבילים, נקודות עניין ומפה", "name": "OpenStreetMap", "url": "https://www.openstreetmap.org/copyright",
                "license": "ODbL"})
    if has_dem:
        from .elevation import ATTRIBUTION
        out.append({"role": "נתוני גובה", "name": ATTRIBUTION, "url": "https://spacedata.copernicus.eu", "license": None})
    seen = set()
    for t in used_texts:
        if t.get("url") and t["url"] not in seen:
            seen.add(t["url"])
            out.append({"role": "תיאורי אתרים", "name": t.get("source_he") or "ויקיפדיה", "url": t["url"],
                        "license": t.get("license") or "CC BY-SA"})
    if info.get("gallery"):
        out.append({"role": "תמונות", "name": "Wikimedia Commons / Mapillary", "url": None,
                    "license": "לפי הקרדיט שליד כל תמונה"})
    for t in info.get("texts") or []:
        if t.get("url") and t["url"] not in seen:
            seen.add(t["url"])
            out.append({"role": "מידע נוסף", "name": t.get("source_he") or t.get("source"), "url": t["url"],
                        "license": t.get("license")})
    return out


# ----------------------------------------------------------------------------- hikes table

def _hike_row(con: sqlite3.Connection, hid: int):
    rows = _q(con, "SELECT id, source, src_id, name, description, difficulty, duration_min, length_m, circular, season, geom, "
                   "lat, lon, min_lon, min_lat, max_lon, max_lat, url, license, extra FROM hikes WHERE id = ?", (hid,))
    if not rows:
        return None
    keys = ("id", "source", "src_id", "name", "description", "difficulty", "duration_min", "length_m", "circular", "season",
            "geom", "lat", "lon", "min_lon", "min_lat", "max_lon", "max_lat", "url", "license", "extra")
    return dict(zip(keys, rows[0]))


def _source_he(src: str | None) -> str | None:
    return SOURCES.get(src or "", {}).get("name") or src


def hike_detail(con: sqlite3.Connection, hid: int) -> dict | None:
    r = _hike_row(con, hid)
    if r is None:
        return None
    try:
        coords = geo.decode_polyline(r["geom"])
    except (IndexError, ValueError, TypeError):
        coords = []
    extra = _json(r["extra"])
    level = difficulty_level(r["difficulty"])
    length = r["length_m"] or (geo.line_length_m(coords) if len(coords) > 1 else None)
    bbox = [r["min_lon"], r["min_lat"], r["max_lon"], r["max_lat"]]
    if None in bbox and coords:
        bbox = list(geo.bbox_of(coords))
    tips = extra.get("tips")
    if isinstance(tips, str):
        tips = [tips]
    return {
        "id": f"hike-{r['id']}", "type": "hike", "name": r["name"], "icon": "hiker", "color": "#15803D",
        "source": r["source"], "source_he": _source_he(r["source"]), "src_id": r["src_id"],
        "description": r["description"], "paragraphs": [p.strip() for p in (r["description"] or "").split("\n\n") if p.strip()],
        "difficulty": level, "difficulty_he": DIFFICULTY_HE.get(level or "") or (r["difficulty"] or None),
        "duration_min": r["duration_min"], "length_m": round(length) if length else None,
        "length_km": round(length / 1000, 1) if length else None,
        "circular": None if r["circular"] is None else bool(r["circular"]), "season": r["season"],
        "location": {"lat": r["lat"], "lon": r["lon"]}, "bbox": bbox,
        "geometry": {"type": "LineString", "coordinates": [[round(x, 6), round(y, 6)] for x, y in coords]},
        "waypoints": [w for w in extra.get("waypoints") or [] if isinstance(w, dict)],
        "tips": [t for t in tips or [] if isinstance(t, str)], "water": extra.get("water"), "parking": extra.get("parking"),
        "shade": extra.get("shade"), "extra": extra,
        "url": r["url"], "license": r["license"],
        "media": {"gallery": rich.gallery(con, "hike", r["id"], extra.get("image"))},
        "texts": rich.texts(con, "hike", r["id"]),
    }


def hike_guide(con: sqlite3.Connection, hid: int, elevation=None, day: dt.date | None = None) -> dict | None:
    h = hike_detail(con, hid)
    if h is None:
        return None
    coords = [tuple(c) for c in h["geometry"]["coordinates"]]
    info = {"kind": "hike", "id": h["id"], "name": h["name"], "description": h["description"],
            "difficulty": h["difficulty"], "duration_min": h["duration_min"], "circular": h["circular"],
            "season": h["season"], "waypoints": h["waypoints"], "tips": h["tips"], "water": h["water"],
            "parking": h["parking"], "shade": h["shade"], "url": h["url"], "license": h["license"], "source": h["source"],
            "gallery": h["media"]["gallery"], "texts": h["texts"]}
    g = build_guide(con, coords, info, elevation, day)
    g["source_he"] = h["source_he"]
    g["url"] = h["url"]
    return g


def trail_guide(con: sqlite3.Connection, tid: int, elevation=None, day: dt.date | None = None) -> dict | None:
    from .outdoor import _row

    r = _row(con, tid)
    if r is None:
        return None
    lines = [geo.decode_polyline(g[0]) for g in _q(con, "SELECT geom FROM trail_segments WHERE trail_id = ? ORDER BY id", (tid,))]
    coords = chain_lines(lines)
    extra = _json(r["extra"])
    ascent = None
    if extra.get("ascent"):
        m = re.match(r"\s*(\d+(?:\.\d+)?)", str(extra["ascent"]))
        ascent = float(m.group(1)) if m else None
    info = {"kind": "trail", "id": f"trail-{tid}", "name": display_name(r["name"], r["route"], r["mark"]),
            "description": r["description"], "difficulty": extra.get("sac_scale"),
            "circular": True if extra.get("roundtrip") == "yes" else None, "ascent": ascent,
            "gallery": rich.gallery(con, "trail", tid), "texts": rich.texts(con, "trail", tid)}
    return build_guide(con, coords, info, elevation, day)


def list_hikes(con: sqlite3.Connection, bbox: tuple | None = None, center: tuple[float, float] | None = None,
               radius: float | None = None, limit: int = 20, with_geometry: bool = False) -> list[dict]:
    """Hikes intersecting *bbox* (or within *radius* of *center*), nearest to the centre first."""
    if bbox is None and center is not None:
        bbox = geo.bbox_around(center[0], center[1], radius or 20000)
    if bbox is None:
        return []
    minx, miny, maxx, maxy = bbox
    if center is None:
        center = ((miny + maxy) / 2, (minx + maxx) / 2)
    rows = _q(con, "SELECT id, name, difficulty, duration_min, length_m, circular, lat, lon, source, geom, "
                   "COALESCE(min_lon, lon), COALESCE(min_lat, lat), COALESCE(max_lon, lon), COALESCE(max_lat, lat), extra "
                   "FROM hikes WHERE COALESCE(min_lon, lon) <= ? AND COALESCE(max_lon, lon) >= ? "
                   "AND COALESCE(min_lat, lat) <= ? AND COALESCE(max_lat, lat) >= ? LIMIT 2000", (maxx, minx, maxy, miny))
    out = []
    for hid, name, diff, dur, length, circ, lat, lon, src, geom, x0, y0, x1, y1, extra in rows:
        d = geo.haversine(center[0], center[1], lat, lon)
        if radius and bbox is not None and d > radius * 1.5 and not (x0 <= center[1] <= x1 and y0 <= center[0] <= y1):
            continue
        level = difficulty_level(diff)
        parts = []
        if length:
            parts.append(format_distance(length))
        if level:
            parts.append(DIFFICULTY_HE[level])
        if circ is not None:
            parts.append("מעגלי" if circ else "קווי")
        if dur:
            parts.append(format_duration(dur * 60))
        item = {"id": f"hike-{hid}", "type": "hike", "name": name, "secondary": " · ".join(parts), "icon": "hiker",
                "color": "#15803D", "length_m": round(length) if length else None, "duration_min": dur,
                "difficulty": level, "difficulty_he": DIFFICULTY_HE.get(level or ""),
                "circular": None if circ is None else bool(circ), "source_he": _source_he(src),
                "location": {"lat": lat, "lon": lon}, "distance": round(d), "bbox": [x0, y0, x1, y1]}
        gal = rich.gallery(con, "hike", hid, _json(extra).get("image"))
        item["thumbnail"] = gal[0]["src"] if gal else None
        if with_geometry:
            try:
                line = geo.simplify(geo.decode_polyline(geom), 20.0)
                item["geometry"] = {"type": "LineString", "coordinates": [[round(x, 5), round(y, 5)] for x, y in line]}
            except (IndexError, ValueError, TypeError):
                pass
        out.append(item)
    out.sort(key=lambda h: h["distance"])
    return out[:limit]


def hikes_tile(con: sqlite3.Connection, z: int, x: int, y: int) -> bytes:
    from .tiles import _simplify_px, _tile_query_bbox

    if z < 8:
        return b""
    minx, miny, maxx, maxy = _tile_query_bbox(z, x, y, 0.05)
    rows = _q(con, "SELECT id, name, difficulty, length_m, circular, geom FROM hikes "
                   "WHERE COALESCE(min_lon, lon) <= ? AND COALESCE(max_lon, lon) >= ? "
                   "AND COALESCE(min_lat, lat) <= ? AND COALESCE(max_lat, lat) >= ? LIMIT 1500", (maxx, minx, maxy, miny))
    if not rows:
        return b""
    layer = mvt.Layer("hikes")
    tol = 2 if z >= 14 else (4 if z >= 11 else 8)
    for hid, name, diff, length, circ, geom in rows:
        try:
            pts = [geo.lonlat_to_tile_pixel(lo, la, z, x, y) for lo, la in geo.decode_polyline(geom)]
        except (IndexError, ValueError, TypeError):
            continue
        layer.add_line([_simplify_px(pts, tol)], {"id": int(hid), "name": name or "", "difficulty": difficulty_level(diff) or "",
                                                  "length_m": float(length or 0), "circular": bool(circ)}, fid=int(hid))
    return mvt.encode_tile([layer])


# ----------------------------------------------------------------------------- endpoints

_CACHE: OrderedDict = OrderedDict()
_CACHE_LOCK = threading.Lock()


def _cached(key: tuple, fn):
    with _CACHE_LOCK:
        if key in _CACHE:
            _CACHE.move_to_end(key)
            return _CACHE[key]
    val = fn()
    with _CACHE_LOCK:
        _CACHE[key] = val
        while len(_CACHE) > 64:
            _CACHE.popitem(last=False)
    return val


def _day(req: Request) -> dt.date | None:
    from .app import ApiError

    v = req.query_params.get("date")
    if not v:
        return None
    try:
        return dt.date.fromisoformat(v)
    except ValueError:
        raise ApiError(400, "invalid_parameter", "תאריך לא תקין (YYYY-MM-DD)")


def _elev(st):
    return st.elevation if st.terrain.available else None


def _guide_or_error(fn):
    from .app import ApiError

    try:
        return fn()
    except ValueError as e:
        if "too long" in str(e):
            raise ApiError(422, "route_too_long", "המסלול ארוך מדי למדריך (מעל 300 ק״מ). בחרו קטע קצר יותר.")
        raise ApiError(422, "invalid_route", "לא ניתן לבנות מדריך למסלול הזה")


async def hikes_list(req: Request) -> Response:
    from .app import ApiError, _float, _int, _need_data, cached

    st = _need_data()
    bbox = None
    if req.query_params.get("bbox"):
        try:
            bbox = tuple(float(v) for v in req.query_params["bbox"].split(","))
        except ValueError:
            bbox = ()
        if len(bbox) != 4 or not (bbox[0] <= bbox[2] and bbox[1] <= bbox[3]):
            raise ApiError(400, "invalid_parameter", "bbox לא תקין – צפוי minlon,minlat,maxlon,maxlat")
    lat = _float(req, "lat", -90, 90)
    lon = _float(req, "lon", -180, 180)
    if bbox is None and (lat is None or lon is None):
        raise ApiError(400, "missing_parameter", "חסר פרמטר: bbox או lat ו-lon")
    radius = _float(req, "radius", 100, 200000) or 20000.0
    limit = _int(req, "limit", 20, 1, 100)
    geom = req.query_params.get("geometry") in ("1", "true")
    center = (lat, lon) if lat is not None and lon is not None else None
    res = await run_in_threadpool(lambda: list_hikes(st.ds.conn(), bbox, center, None if bbox else radius, limit, geom))
    return cached(JSONResponse({"results": res, "meta": {"returned": len(res)}}), 300)


async def hike_get(req: Request) -> Response:
    from .app import ApiError, _need_data, _parse_id, cached

    st = _need_data()
    hid = _parse_id(req.path_params["id"], "hike")
    res = await run_in_threadpool(lambda: hike_detail(st.ds.conn(), hid))
    if res is None:
        raise ApiError(404, "not_found", "המסלול לא נמצא")
    return cached(JSONResponse(res), 3600)


async def hike_guide_get(req: Request) -> Response:
    from .app import ApiError, _need_data, _parse_id, cached

    st = _need_data()
    hid = _parse_id(req.path_params["id"], "hike")
    day = _day(req)
    key = ("hike", st.ds.version, hid, day, st.terrain.available)
    res = await run_in_threadpool(lambda: _guide_or_error(lambda: _cached(key, lambda: hike_guide(st.ds.conn(), hid, _elev(st), day))))
    if res is None:
        raise ApiError(404, "not_found", "המסלול לא נמצא")
    return cached(JSONResponse(res), 1800)


async def trail_guide_get(req: Request) -> Response:
    from .app import ApiError, _need_data, _parse_id, cached

    st = _need_data()
    tid = _parse_id(req.path_params["id"], "trail")
    day = _day(req)
    key = ("trail", st.ds.version, tid, day, st.terrain.available)
    res = await run_in_threadpool(lambda: _guide_or_error(lambda: _cached(key, lambda: trail_guide(st.ds.conn(), tid, _elev(st), day))))
    if res is None:
        raise ApiError(404, "not_found", "השביל לא נמצא")
    return cached(JSONResponse(res), 1800)


async def guide_post(req: Request) -> Response:
    from .app import ApiError, _need_data

    st = _need_data()
    try:
        body = await req.json()
    except Exception:  # noqa: BLE001
        raise ApiError(400, "invalid_json", "גוף הבקשה אינו JSON תקין")
    if not isinstance(body, dict):
        raise ApiError(400, "invalid_json", "גוף הבקשה אינו JSON תקין")
    try:
        coords = [(float(c[0]), float(c[1])) for c in body.get("coordinates") or []]
    except (TypeError, ValueError, IndexError):
        raise ApiError(400, "invalid_parameter", "קואורדינטות לא תקינות")
    if len(coords) < 2 or len(coords) > 20000:
        raise ApiError(400, "invalid_parameter", "יש לשלוח בין 2 ל-20000 נקודות [lon, lat]")
    if not all(-180 <= x <= 180 and -90 <= y <= 90 for x, y in coords):
        raise ApiError(400, "invalid_parameter", "קואורדינטות מחוץ לטווח")
    day = None
    if body.get("date"):
        try:
            day = dt.date.fromisoformat(str(body["date"]))
        except ValueError:
            raise ApiError(400, "invalid_parameter", "תאריך לא תקין (YYYY-MM-DD)")
    wps = body.get("waypoints") if isinstance(body.get("waypoints"), list) else []
    info = {"kind": "route", "name": str(body.get("name") or "")[:120] or None, "waypoints": wps[:50],
            "circular": body.get("circular") if isinstance(body.get("circular"), bool) else None}
    res = await run_in_threadpool(lambda: _guide_or_error(lambda: build_guide(st.ds.conn(), coords, info, _elev(st), day)))
    return JSONResponse(res, headers={"Cache-Control": "no-store"})


async def tile_hikes(req: Request) -> Response:
    from .app import _need_data, _pbf, _zxy

    st = _need_data()
    z, x, y = _zxy(req)
    data = await run_in_threadpool(lambda: hikes_tile(st.ds.conn(), z, x, y))
    return _pbf(data, 3600)


#: mounted by app.build_app() before the /api catch-all (and before /api/trails/{id})
ROUTES = [
    Route("/api/hikes", hikes_list),
    Route("/api/hikes/{id}/guide", hike_guide_get),
    Route("/api/hikes/{id}", hike_get),
    Route("/api/trails/{id}/guide", trail_guide_get),
    Route("/api/guide", guide_post, methods=["POST"]),
    Route("/tiles/hikes/{z}/{x}/{y}.pbf", tile_hikes),
]
