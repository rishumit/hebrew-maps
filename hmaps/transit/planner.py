"""Journey planning on top of :mod:`hmaps.transit.raptor`: itineraries with Hebrew text, departures, status.

All times are scheduled (GTFS) times in Israel local time – the API labels them
"זמנים משוערים לפי לוח הזמנים".
"""

from __future__ import annotations

import math
import re
import time as _time
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import numpy as np

from ..core.hebrew import format_distance, format_duration
from . import raptor
from .data import MODE_COLORS, TransitData, mode_of

TZ = ZoneInfo("Asia/Jerusalem")
WALK_SPEED = 4.8 / 3.6       # m/s
DETOUR = 1.25                # straight line → walking distance
ACCESS_RADIUS = 800.0
ACCESS_RADIUS_WIDE = 1500.0  # used when nothing is within ACCESS_RADIUS
MAX_ACCESS_STOPS = 60
MAX_RIDES = 5                # = up to 4 transfers
CHANGE_SECONDS = 60          # minimum time to change vehicles
DIRECT_WALK_MAX = 2000.0     # offer a walk-only option up to this distance (m)
HORIZONS = (4 * 3600, 10 * 3600)
TIME_BUDGET = 1.2            # seconds; extra alternative passes stop after this
ESTIMATED_TEXT = "זמנים משוערים לפי לוח הזמנים"
WEEKDAYS = ["שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון"]


class TransitError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


# ----------------------------------------------------------------------------- time helpers

def parse_time(text: str | None) -> datetime:
    """ISO 8601 → aware Israel-time datetime. Naive values are Israel local time; empty = now."""
    if not text:
        return datetime.now(TZ).replace(microsecond=0)
    s = text.strip().replace(" ", "T")
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        raise TransitError(400, "invalid_parameter", "פורמט זמן לא תקין – צפוי ISO 8601, למשל 2026-10-14T08:00")
    if dt.tzinfo is None:
        return dt.replace(tzinfo=TZ)
    return dt.astimezone(TZ)


def at(day: date, secs: int) -> datetime:
    """Seconds since *day*'s midnight (may be negative or > 24h) → aware local datetime."""
    return (datetime.combine(day, time()) + timedelta(seconds=int(secs))).replace(tzinfo=TZ)


def hhmm(day: date, secs: int) -> str:
    return at(day, secs).strftime("%H:%M")


def _iso(day: date, secs: int) -> str:
    return at(day, secs).isoformat()


def _minutes(secs: float) -> str:
    m = max(1, int(round(secs / 60)))
    return "דקה" if m == 1 else f"{m} דק׳"


def _hav(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 12_742_017.6 * math.asin(min(1.0, math.sqrt(a)))


def _walk_secs(dist_m) -> np.ndarray:
    """Walking time for straight-line distances; < 16 m (≈ 20 m on foot) counts as "already there"."""
    d = np.asarray(dist_m, dtype=np.float64)
    return np.where(d * DETOUR < 20, 0, np.ceil(d * DETOUR / WALK_SPEED)).astype(np.int64)


def _color(data: TransitData, route: int, mode: str) -> tuple[str, str]:
    c = int(data.route_color[route])
    bg = f"#{c:06X}" if c >= 0 else MODE_COLORS.get(mode, "#15803D")
    tc = int(data.route_text_color[route])
    if tc >= 0:
        return bg, f"#{tc:06X}"
    r, g, b = (int(bg[i:i + 2], 16) / 255 for i in (1, 3, 5))
    lin = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in (r, g, b)]
    lum = 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]
    return bg, ("#111827" if lum > 0.22 else "#FFFFFF")


# ----------------------------------------------------------------------------- status

def status(data: TransitData | None, today: date | None = None) -> dict:
    if data is None:
        return {"available": False,
                "message": "נתוני התחבורה הציבורית עדיין לא הותקנו. להתקנה: update-windows.cmd "
                           "(או python -m hmaps.etl gtfs)."}
    today = today or datetime.now(TZ).date()
    m = data.meta
    days_left = (data.end - today).days
    built = m.get("built", "")
    try:
        age = (datetime.now(TZ) - datetime.fromisoformat(built)).days
    except ValueError:
        age = None
    warning = None
    level = "ok"
    if today > data.end:
        level, warning = "expired", (f"לוח הזמנים של התחבורה הציבורית פג תוקף ב-{data.end:%d/%m/%Y}. "
                                     "יש לעדכן את הנתונים (update-windows.cmd).")
    elif today < data.start:
        level, warning = "future", f"לוח הזמנים מתחיל רק ב-{data.start:%d/%m/%Y}."
    elif days_left <= 5:
        level, warning = "expiring", f"לוח הזמנים יפוג בעוד {days_left} ימים – מומלץ לעדכן את הנתונים."
    elif age is not None and age > 14:
        level, warning = "stale", f"נתוני התחבורה הציבורית עודכנו לפני {age} ימים – מומלץ לעדכן פעם בשבוע."
    return {"available": True, "feed_start": data.start.isoformat(), "feed_end": data.end.isoformat(),
            "built": built, "age_days": age, "days_left": days_left, "freshness": level, "warning": warning,
            "estimated_text": ESTIMATED_TEXT,
            "counts": {k: m.get(k) for k in ("stops", "routes", "trips", "patterns", "agencies")}}


# ----------------------------------------------------------------------------- planner

class _Ctx:
    """Per-query state: window + tables, access/egress, coordinates."""

    def __init__(self, data: TransitData, day: date, t_lo: int, t_hi: int):
        self.data = data
        self.day = day
        self.win = raptor.Window(data, data.day_index(day), t_lo, t_hi)
        self.fwd, self._rev_builder = raptor.tables(self.win)
        self._rev = None

    @property
    def rev(self) -> raptor.Table:
        if self._rev is None:
            self._rev = self._rev_builder()
        return self._rev


def _access(data: TransitData, lat: float, lon: float) -> tuple[np.ndarray, np.ndarray]:
    s, d = data.near(lat, lon, ACCESS_RADIUS)
    if not len(s):
        s, d = data.near(lat, lon, ACCESS_RADIUS_WIDE)
    return s[:MAX_ACCESS_STOPS], d[:MAX_ACCESS_STOPS]


def _transfer_secs(data: TransitData, a: int, b: int) -> tuple[int, int]:
    lo, hi = int(data.tr_ptr[a]), int(data.tr_ptr[a + 1])
    k = np.flatnonzero(data.tr_to[lo:hi] == b)
    if len(k):
        return int(data.tr_sec[lo + k[0]]), int(data.tr_dist[lo + k[0]])
    d = _hav(float(data.stop_lat[a]), float(data.stop_lon[a]), float(data.stop_lat[b]), float(data.stop_lon[b])) * DETOUR
    return int(math.ceil(d / WALK_SPEED)), int(round(d))


def _forward_legs(ctx: _Ctx, table: raptor.Table, legs: list[tuple]) -> list[tuple]:
    """Search-order legs → forward legs ('access', s) ('ride', w, inst, i_board, i_alight) ('walk', a, b) ('egress', s)."""
    out = []
    R = table.R
    for leg in legs:
        if leg[0] == "ride":
            _, i, j, t = leg
            if table.reversed:
                rb, ra = R - 1 - j, R - 1 - i
                tf = int(table.pos_m[j]) - 1 - t
            else:
                rb, ra, tf = i, j, t
            fwd = ctx.fwd
            w = int(fwd.pos_wf[rb])
            out.append(("ride", w, int(ctx.win.inst_first[w]) + tf, int(fwd.pos_i[rb]), int(fwd.pos_i[ra])))
        elif leg[0] == "walk":
            out.append(("walk", leg[2], leg[1]) if table.reversed else leg)
        elif leg[0] == "access":
            out.append(("egress" if table.reversed else "access", leg[1]))
        else:
            out.append(("access" if table.reversed else "egress", leg[1]))
    if table.reversed:
        out.reverse()
    return out


def _ride_times(ctx: _Ctx, inst: int, i: int) -> tuple[int, int]:
    d = ctx.data
    trip = int(ctx.win.trip[inst])
    off = int(ctx.win.off[inst])
    p = int(d.prof_ptr[d.trip_prof[trip]]) + i
    base = int(d.trip_start[trip]) + off
    return base + int(d.prof_arr[p]), base + int(d.prof_dep[p])


def _geometry_ride(d: TransitData, pat: int, i: int, j: int) -> list[list[float]]:
    a = int(d.pat_ptr[pat])
    sh = int(d.pat_shape[pat])
    if sh >= 0:
        pb, pa = int(d.pat_shape_pos[a + i]), int(d.pat_shape_pos[a + j])
        if 0 <= pb <= pa:  # absolute indexes into shape_lon/lat
            lon = d.shape_lon[pb:pa + 1]
            lat = d.shape_lat[pb:pa + 1]
            if len(lon) >= 2:
                return [[round(float(x), 5), round(float(y), 5)] for x, y in zip(lon, lat)]
    st = d.pat_stops[a + i:a + j + 1]
    return [[round(float(d.stop_lon[s]), 5), round(float(d.stop_lat[s]), 5)] for s in st]


def _place(d: TransitData, s: int) -> dict:
    return d.stop_info(s)


def _destination(d: TransitData, route: int) -> str:
    """Destination from a MoT route_long_name: "A-city<->B - north-city2-10" → "B - north, city2"."""
    long = d.route_long[route]
    if "<->" not in long:
        return ""
    dest = re.sub(r"-[0-9#]+$", "", long.split("<->")[-1]).strip()
    if "-" in dest:
        name, city = (x.strip() for x in dest.rsplit("-", 1))
        if not name or name == city or city in name or name in city:
            return name or city
        return f"{name}, {city}"
    return dest


def headsign(d: TransitData, route: int, trip: int) -> tuple[str, str | None]:
    """(direction text, train number) – rail headsigns in the MoT feed are train numbers."""
    h = d.headsigns[int(d.trip_headsign[trip])]
    if not h or h.isdigit():
        return _destination(d, route), (h or None)
    return h, None


def _line_label(d: TransitData, route: int, mode: str, mode_he: str) -> str:
    short = d.route_short[route]
    if mode == "rail":
        return "רכבת"
    if mode == "light_rail":
        return f"רכבת קלה {short}".strip() if short and not short.startswith("רכבת") else (short or "רכבת קלה")
    return f"קו {short}" if short else mode_he


def _build_itinerary(ctx: _Ctx, flegs: list[tuple], origin: tuple, dest: tuple, acc: dict, egr: dict) -> dict:
    d, day = ctx.data, ctx.day
    rides = [lg for lg in flegs if lg[0] == "ride"]
    first_dep = _ride_times(ctx, rides[0][2], rides[0][3])[1]
    out_legs = []
    walk_m = walk_s = 0
    t = None
    start = first_dep
    for k, lg in enumerate(flegs):
        if lg[0] == "access":
            s = lg[1]
            secs, dist = acc[s]
            if dist < 20:  # already at the stop
                secs = dist = 0
            t = start = first_dep - secs
            if dist >= 20:
                out_legs.append(_walk_leg(d, day, t, secs, dist, {"name": "נקודת המוצא", "lat": origin[0], "lon": origin[1]},
                                          _place(d, s), "access"))
                walk_m += dist
                walk_s += secs
            t = first_dep
        elif lg[0] == "walk":
            a, b = lg[1], lg[2]
            secs, dist = _transfer_secs(d, a, b)
            out_legs.append(_walk_leg(d, day, t, secs, dist, _place(d, a), _place(d, b), "transfer"))
            walk_m += dist
            walk_s += secs
            t += secs
        elif lg[0] == "egress":
            s = lg[1]
            secs, dist = egr[s]
            if dist < 20:
                secs = dist = 0
            if dist >= 20:
                out_legs.append(_walk_leg(d, day, t, secs, dist, _place(d, s),
                                          {"name": "היעד", "lat": dest[0], "lon": dest[1]}, "egress"))
                walk_m += dist
                walk_s += secs
            t += secs
        else:
            _, w, inst, i, j = lg
            pat = int(ctx.win.upat[w])
            dep = _ride_times(ctx, inst, i)[1]
            arr = _ride_times(ctx, inst, j)[0]
            wait = max(0, dep - t) if t is not None else 0
            out_legs.append(_ride_leg(ctx, pat, inst, i, j, dep, arr, wait))
            t = arr
    end = t
    n_rides = len(rides)
    labels = [lg["line"] for lg in out_legs if lg["type"] != "walk"]
    return {
        "departure": _iso(day, start), "arrival": _iso(day, end),
        "departure_text": hhmm(day, start), "arrival_text": hhmm(day, end),
        "duration": int(end - start), "duration_text": format_duration(end - start),
        "transfers": max(0, n_rides - 1), "rides": n_rides,
        "walk_distance": int(walk_m), "walk_distance_text": format_distance(walk_m),
        "walk_duration": int(walk_s), "walk_duration_text": format_duration(walk_s),
        "summary": " ← ".join(labels), "legs": out_legs, "tags": [],
        "_key": tuple((lg[2], lg[3], lg[4]) for lg in rides), "_start": start, "_end": end,
    }


def _walk_leg(d, day, t, secs, dist, a: dict, b: dict, kind: str) -> dict:
    if kind == "egress":
        text = f"הליכה {_minutes(secs)} ({format_distance(dist)}) ליעד"
    elif kind == "transfer":
        text = f"הליכה {_minutes(secs)} ({format_distance(dist)}) לתחנה {b['name']}"
    else:
        text = f"הליכה {_minutes(secs)} ({format_distance(dist)}) לתחנה {b['name']}"
    return {"type": "walk", "mode_he": "הליכה", "kind": kind, "from": a, "to": b,
            "departure": _iso(day, t), "arrival": _iso(day, t + secs),
            "departure_text": hhmm(day, t), "arrival_text": hhmm(day, t + secs),
            "duration": int(secs), "duration_text": format_duration(secs),
            "distance": int(dist), "distance_text": format_distance(dist),
            "geometry": {"type": "LineString", "coordinates": [[round(a["lon"], 6), round(a["lat"], 6)],
                                                               [round(b["lon"], 6), round(b["lat"], 6)]]},
            "instruction": text}


def _ride_leg(ctx: _Ctx, pat: int, inst: int, i: int, j: int, dep: int, arr: int, wait: int) -> dict:
    d, day = ctx.data, ctx.day
    route = int(d.pat_route[pat])
    mode, mode_he = mode_of(int(d.route_type[route]))
    trip = int(ctx.win.trip[inst])
    head, train_no = headsign(d, route, trip)
    a = int(d.pat_ptr[pat])
    stops = d.pat_stops[a + i:a + j + 1]
    n = j - i
    agency = d.agency_names[int(d.route_agency[route])]
    line = _line_label(d, route, mode, mode_he)
    bg, fg = _color(d, route, mode)
    frm, to = _place(d, int(stops[0])), _place(d, int(stops[-1]))
    toward = f" לכיוון {head}" if head else ""
    off = " בתחנה הבאה" if n == 1 else f" אחרי {n} תחנות"
    if mode == "rail":
        plat = f" (רציף {frm['platform']})" if frm.get("platform") else ""
        text = f"רכבת{toward} מתחנת {frm['name']}{plat}, ירידה בתחנת {to['name']}{off}"
    else:
        text = f"{line}{toward}, ירידה בתחנה {to['name']}{off}"
    if wait >= 60:
        text = f"{text} (המתנה {_minutes(wait)})"
    inter = []
    for k, s in enumerate(stops):
        ta, td = _ride_times(ctx, inst, i + k)
        inter.append({"name": d.stop_names[int(s)], "time_text": hhmm(day, td if k == 0 else ta),
                      "lat": round(float(d.stop_lat[s]), 6), "lon": round(float(d.stop_lon[s]), 6)})
    return {"type": mode, "mode_he": mode_he, "line": line, "route_id": d.route_ids[route],
            "route_short_name": d.route_short[route] or None,
            "route_long_name": d.route_long[route] or None, "agency": agency, "headsign": head or None,
            "train_number": train_no,
            "color": bg, "text_color": fg, "from": frm, "to": to,
            "departure": _iso(day, dep), "arrival": _iso(day, arr),
            "departure_text": hhmm(day, dep), "arrival_text": hhmm(day, arr),
            "duration": int(arr - dep), "duration_text": format_duration(arr - dep),
            "wait": int(wait), "num_stops": int(n), "stops": inter,
            "geometry": {"type": "LineString", "coordinates": _geometry_ride(d, pat, i, j)},
            "instruction": text}


def _direct_walk(day: date, t: int, origin, dest, dist: float, arrive: bool) -> dict:
    secs = int(math.ceil(dist / WALK_SPEED))
    start = t - secs if arrive else t
    leg = _walk_leg(None, day, start, secs, dist, {"name": "נקודת המוצא", "lat": origin[0], "lon": origin[1]},
                    {"name": "היעד", "lat": dest[0], "lon": dest[1]}, "egress")
    return {"departure": leg["departure"], "arrival": leg["arrival"], "departure_text": leg["departure_text"],
            "arrival_text": leg["arrival_text"], "duration": secs, "duration_text": format_duration(secs),
            "transfers": 0, "rides": 0, "walk_distance": int(dist), "walk_distance_text": format_distance(dist),
            "walk_duration": secs, "walk_duration_text": format_duration(secs), "summary": "הליכה",
            "legs": [leg], "tags": ["walk_only"], "_key": ("walk",), "_start": start, "_end": start + secs}


def _search(ctx: _Ctx, arrive: bool, t: int, acc_s, acc_d, egr_s, egr_d, walk_factor: float = 1.0):
    """One RAPTOR run; returns forward-leg journeys (Pareto over number of rides)."""
    table = ctx.rev if arrive else ctx.fwd
    a_t = np.ceil(_walk_secs(acc_d) * walk_factor).astype(np.int64)
    e_t = np.ceil(_walk_secs(egr_d) * walk_factor).astype(np.int64)
    if arrive:  # search from the destination backwards in (negated) time
        src_s, src_t, dst_s, dst_t, t0 = egr_s, e_t, acc_s, a_t, -t
    else:
        src_s, src_t, dst_s, dst_t, t0 = acc_s, a_t, egr_s, e_t, t
    res = raptor.run(table, ctx.data.n_stops, src_s, src_t, dst_s, dst_t, t0,
                     (ctx.data.tr_ptr, ctx.data.tr_to, ctx.data.tr_sec), max_rides=MAX_RIDES,
                     change=CHANGE_SECONDS, walk_factor=walk_factor)
    out = []
    prev = None
    for k in range(1, len(res.dest)):
        dk = res.dest[k]
        if dk is None or dk == prev:
            continue
        prev = dk
        legs = raptor.journey(table, res, k, dk[1])
        out.append(_forward_legs(ctx, table, legs))
    return out


def plan(data: TransitData, frm: tuple[float, float], to: tuple[float, float], when: datetime | None = None,
         arrive: bool = False, max_results: int = 5, _retry: bool = True, _raw: bool = False) -> dict:
    t_start = _time.perf_counter()
    when = (when or datetime.now(TZ)).astimezone(TZ)
    day = when.date()
    T = when.hour * 3600 + when.minute * 60 + when.second
    base = {"mode": "arrive" if arrive else "depart", "time": when.isoformat(), "estimated": True,
            "estimated_text": ESTIMATED_TEXT, "itineraries": [], "notice": None, "reason": None,
            "next_departure": None, "feed": status(data)}
    if not (data.covers(day) or data.covers(day - timedelta(days=1))):
        base["reason"] = "out_of_range"
        base["notice"] = (f"לוח הזמנים הזמין מכסה את התאריכים {data.start:%d/%m/%Y}–{data.end:%d/%m/%Y}. "
                          "בחרו תאריך בטווח זה או עדכנו את נתוני התחבורה הציבורית.")
        return base
    acc_s, acc_d = _access(data, *frm)
    egr_s, egr_d = _access(data, *to)
    direct = _hav(frm[0], frm[1], to[0], to[1]) * DETOUR
    itins: list[dict] = []
    if direct <= DIRECT_WALK_MAX:
        itins.append(_direct_walk(day, T, frm, to, direct, arrive))
    if not len(acc_s) or not len(egr_s):
        base["reason"] = "no_stops_nearby"
        side = "נקודת המוצא" if not len(acc_s) else "היעד"
        base["notice"] = f"אין תחנות תחבורה ציבורית במרחק הליכה ({format_distance(ACCESS_RADIUS_WIDE)}) מ{side}."
        base["itineraries"] = itins if _raw else [_public(i) for i in itins]
        return base
    acc = {int(s): (int(t), int(round(d * DETOUR))) for s, d, t in zip(acc_s, acc_d, _walk_secs(acc_d))}
    egr = {int(s): (int(t), int(round(d * DETOUR))) for s, d, t in zip(egr_s, egr_d, _walk_secs(egr_d))}

    journeys: dict[tuple, dict] = {}
    ctx = None
    for horizon in HORIZONS:
        lo, hi = (T - horizon, T) if arrive else (T, T + horizon)
        ctx = _Ctx(data, day, lo, hi)
        found = _search(ctx, arrive, T, acc_s, acc_d, egr_s, egr_d)
        if found:
            break
    for fl in found:
        it = _build_itinerary(ctx, fl, frm, to, acc, egr)
        journeys.setdefault(it["_key"], it)
    if journeys:
        # alternatives: less walking, then later (earlier for arrive-by) departures
        if _time.perf_counter() - t_start < TIME_BUDGET:
            for fl in _search(ctx, arrive, T, acc_s, acc_d, egr_s, egr_d, walk_factor=3.0):
                it = _build_itinerary(ctx, fl, frm, to, acc, egr)
                journeys.setdefault(it["_key"], it)
        ref = [j for j in journeys.values() if j["rides"]]
        t2 = (min(j["_end"] for j in ref) - 60) if arrive else (min(j["_start"] for j in ref) + 60)
        for _ in range(4):
            if _time.perf_counter() - t_start > TIME_BUDGET or len(journeys) >= max_results + 1:
                break
            if (arrive and t2 < ctx.win.t_lo) or (not arrive and t2 > ctx.win.t_hi):
                break
            more = [_build_itinerary(ctx, fl, frm, to, acc, egr)
                    for fl in _search(ctx, arrive, t2, acc_s, acc_d, egr_s, egr_d)]
            if not more:
                break
            for it in more:
                journeys.setdefault(it["_key"], it)
            t2 = (min(j["_end"] for j in more) - 60) if arrive else (min(j["_start"] for j in more) + 60)
    transit = _pareto_filter(list(journeys.values()), arrive)
    itins += transit
    if transit:  # found, but only hours away (night, Shabbat, holiday): say why
        gap = (T - max(j["_end"] for j in transit)) if arrive else (min(j["_start"] for j in transit) - T)
        if gap > 90 * 60:
            ref = max(j["_end"] for j in transit) if arrive else min(j["_start"] for j in transit)
            base["reason"], base["notice"] = _no_service_reason(data, day, T, (day, ref), arrive)
            base["next_departure"] = _iso(day, ref)
    else:
        nxt = next_service(data, day, T, egr_s if arrive else acc_s, arrive)
        base["reason"], base["notice"] = _no_service_reason(data, day, T, nxt, arrive)
        if nxt is not None:
            nday, nsec = nxt
            base["next_departure"] = _iso(nday, nsec)
            if _retry:
                if arrive:
                    when2 = at(nday, nsec + int(_walk_secs(egr_d.min())))
                else:
                    when2 = at(nday, nsec - int(_walk_secs(acc_d.min())))
                again = plan(data, frm, to, when2, arrive, max_results, _retry=False, _raw=True)
                itins += [i for i in again["itineraries"] if i.get("rides")]
    _tag(itins, arrive)
    wd = day.weekday()
    if transit and base["reason"] is None and (wd == 5 or (wd == 4 and T >= 15 * 3600)):
        base["reason"] = "shabbat_limited"
        base["notice"] = "בשבת השירות מצומצם מאוד – מוצגים רק קווים שפועלים בשבת."
    if arrive:
        itins.sort(key=lambda i: (-i["_start"], i["_end"] - i["_start"], i["transfers"]))
    else:
        itins.sort(key=lambda i: (i["_end"], i["transfers"], -i["_start"]))
    itins = itins[:max_results]
    base["itineraries"] = itins if _raw else [_public(i) for i in itins]
    base["query_time_ms"] = round((_time.perf_counter() - t_start) * 1000, 1)
    return base


def _pareto_filter(its: list[dict], arrive: bool) -> list[dict]:
    """Drop itineraries that are worse in every respect than another one."""
    out = []
    for a in its:
        dom = False
        for b in its:
            if a is b:
                continue
            better_time = (b["_start"] >= a["_start"] and b["_end"] <= a["_end"])
            if (better_time and b["transfers"] <= a["transfers"] and b["walk_distance"] <= a["walk_distance"]
                    and (b["_start"], b["_end"], b["transfers"], b["walk_distance"])
                    != (a["_start"], a["_end"], a["transfers"], a["walk_distance"])):
                dom = True
                break
        if not dom:
            out.append(a)
    return out


def _tag(its: list[dict], arrive: bool = False) -> None:
    tr = [i for i in its if i.get("rides")]
    if not tr:
        return
    if arrive:
        max(tr, key=lambda i: (i["_start"], -i["duration"]))["tags"].append("latest_departure")
    else:
        min(tr, key=lambda i: (i["_end"], i["duration"]))["tags"].append("earliest_arrival")
    min(tr, key=lambda i: (i["duration"], i["_end"]))["tags"].append("fastest")
    min(tr, key=lambda i: (i["transfers"], i["duration"]))["tags"].append("fewest_transfers")
    min(tr, key=lambda i: (i["walk_distance"], i["duration"]))["tags"].append("least_walking")
    for i in tr:
        i["tags"] = list(dict.fromkeys(i["tags"]))


def _public(it: dict) -> dict:
    return {k: v for k, v in it.items() if not k.startswith("_")}


# ----------------------------------------------------------------------------- no service

def next_service(data: TransitData, day: date, T: int, stops, arrive: bool, horizon_days: int = 3):
    """Next departure from *stops* after T (or last arrival before T for arrive-by): (day, secs) or None."""
    best = None
    k0 = data.day_index(day)
    for s in stops:
        for p, i in zip(data.sp_pat[data.sp_ptr[s]:data.sp_ptr[s + 1]], data.sp_i[data.sp_ptr[s]:data.sp_ptr[s + 1]]):
            a = int(data.pat_ptr[p]) + int(i)
            fl = int(data.pat_flags[a])
            if (fl & (2 if arrive else 1)):
                continue
            t0, t1 = int(data.pat_trips[p]), int(data.pat_trips[p + 1])
            prof = data.trip_prof[t0:t1]
            rel = (data.prof_arr if arrive else data.prof_dep)[data.prof_ptr[prof] + i]
            tt = data.trip_start[t0:t1].astype(np.int64) + rel
            for dd in range(-1, horizon_days + 1):
                kd = k0 + (-dd if arrive else dd)
                act = data.active(kd)
                if act is None:
                    continue
                off = (kd - k0) * 86400
                times = tt[act[t0:t1]] + off
                times = times[times <= T] if arrive else times[times >= T]
                if not len(times):
                    continue
                v = int(times.max() if arrive else times.min())
                if best is None or (v > best if arrive else v < best):
                    best = v
    if best is None:
        return None
    return day, best


def _no_service_reason(data: TransitData, day: date, T: int, nxt, arrive: bool) -> tuple[str, str]:
    wd = day.weekday()  # Mon=0 … Sun=6
    k = data.day_index(day)
    low = 0 <= k < data.n_days and data.day_trips[k] < 0.35 * data.normal_day_trips
    when = ""
    if nxt is not None:
        nd = at(nxt[0], nxt[1])
        rel = (nd.date() - day).days
        dayname = "היום" if rel == 0 else ("מחר" if rel == 1 else ("אתמול" if rel == -1 else f"ביום {WEEKDAYS[nd.weekday()]}"))
        word = "הנסיעה האחרונה שמגיעה בזמן" if arrive else "היציאה הבאה"
        when = f" {word}: {dayname} ב-{nd:%H:%M}."
    if wd == 5 or (wd == 4 and T >= 14 * 3600) or (wd == 6 and T < 4 * 3600 and arrive):
        return "shabbat", "אין תחבורה ציבורית בשבת (מכניסת שבת ועד צאת השבת)." + when
    if low:
        return "holiday", "נראה שזה חג או יום מיוחד – אין שירות או שהשירות מצומצם." + when
    if T % 86400 < 5 * 3600 or T % 86400 >= 23 * 3600:
        return "night", "אין שירות תחבורה ציבורית בשעות הלילה במסלול זה." + when
    return "no_route", "לא נמצא מסלול בתחבורה ציבורית בשעה זו." + when


# ----------------------------------------------------------------------------- departures board

def departures(data: TransitData, stop: int, when: datetime | None = None, horizon: int = 3 * 3600,
               limit: int = 20) -> dict:
    when = (when or datetime.now(TZ)).astimezone(TZ)
    day = when.date()
    T = when.hour * 3600 + when.minute * 60 + when.second
    k0 = data.day_index(day)
    rows = []
    lines = {}
    for p, i in zip(data.sp_pat[data.sp_ptr[stop]:data.sp_ptr[stop + 1]], data.sp_i[data.sp_ptr[stop]:data.sp_ptr[stop + 1]]):
        p, i = int(p), int(i)
        route = int(data.pat_route[p])
        mode, mode_he = mode_of(int(data.route_type[route]))
        bg, fg = _color(data, route, mode)
        label = _line_label(data, route, mode, mode_he)
        lines.setdefault(label, {"line": label, "type": mode, "color": bg, "text_color": fg, "route_id": data.route_ids[route]})
        a = int(data.pat_ptr[p]) + i
        if int(data.pat_flags[a]) & 1:
            continue
        t0, t1 = int(data.pat_trips[p]), int(data.pat_trips[p + 1])
        tt = data.trip_start[t0:t1].astype(np.int64) + data.prof_dep[data.prof_ptr[data.trip_prof[t0:t1]] + i]
        for dd in (-1, 0, 1):
            act = data.active(k0 + dd)
            if act is None:
                continue
            times = tt + dd * 86400
            sel = np.flatnonzero(act[t0:t1] & (times >= T) & (times <= T + horizon))
            for q in sel:
                trip = t0 + int(q)
                rows.append((int(times[q]), label, mode, mode_he, bg, fg, route, trip))
    rows.sort()
    out = []
    for secs, label, mode, mode_he, bg, fg, route, trip in rows[:limit]:
        out.append({"time": _iso(day, secs), "time_text": hhmm(day, secs), "minutes": int((secs - T) // 60),
                    "line": label, "route_id": data.route_ids[route], "route_short_name": data.route_short[route] or None, "type": mode,
                    "mode_he": mode_he, "agency": data.agency_names[int(data.route_agency[route])],
                    "headsign": headsign(data, route, trip)[0] or None, "color": bg, "text_color": fg})
    return {"stop": data.stop_info(stop), "time": when.isoformat(), "departures": out,
            "lines": sorted(lines.values(), key=lambda x: (x["type"] != "rail", len(x["line"]), x["line"])),
            "estimated_text": ESTIMATED_TEXT, "feed": status(data)}


def nearby_stops(data: TransitData, lat: float, lon: float, radius: float = 500, limit: int = 20) -> list[dict]:
    s, d = data.near(lat, lon, radius)
    out = []
    for stop, dist in zip(s[:limit], d[:limit]):
        info = data.stop_info(int(stop))
        info["distance"] = int(round(dist))
        out.append(info)
    return out


# ----------------------------------------------------------------------------- lines at a stop / line view

MODE_ORDER = ["rail", "light_rail", "subway", "bus", "share_taxi", "funicular", "cable_car", "ferry"]
MODE_GROUP_HE = {"rail": "רכבות", "light_rail": "רכבת קלה", "subway": "מטרו", "bus": "אוטובוסים",
                 "share_taxi": "מוניות שירות", "funicular": "כרמלית ופוניקולר", "cable_car": "רכבל", "ferry": "מעבורות"}
NO_PICKUP, NO_DROPOFF = 1, 2


def natural_key(text: str) -> tuple:
    """"1" < "1א" < "2" < "10" < "L1" < "" – numbers first, by value; unnamed last."""
    m = re.match(r"^(\d+)(.*)$", text or "")
    if m:
        return (0, int(m.group(1)), m.group(2))
    return (1 if text else 2, 0, text or "")


def _route_info(d: TransitData, route: int) -> dict:
    mode, mode_he = mode_of(int(d.route_type[route]))
    bg, fg = _color(d, route, mode)
    return {"id": d.route_ids[route], "line": _line_label(d, route, mode, mode_he),
            "route_short_name": d.route_short[route] or None, "route_long_name": d.route_long[route] or None,
            "type": mode, "mode_he": mode_he, "agency": d.agency_names[int(d.route_agency[route])],
            "color": bg, "text_color": fg}


def _pat_headsign(d: TransitData, pat: int) -> str:
    """Direction text of a pattern: its first trip's headsign, else the last stop's name."""
    route = int(d.pat_route[pat])
    h = headsign(d, route, int(d.pat_trips[pat]))[0]
    if h:
        return h
    return d.stop_names[int(d.pat_stops[int(d.pat_ptr[pat + 1]) - 1])]


def find_route(d: TransitData, rid: str) -> int | None:
    r = d.route_index.get(rid)
    if r is None and rid.isdigit() and d.route_ids == [str(i) for i in range(len(d.route_ids))]:
        r = int(rid)
    return r


def stop_routes(d: TransitData, stop: int) -> dict:
    """Every line serving *stop* (not only the next departures), grouped by mode and naturally sorted.

    Routes of one line (both directions, variants) are merged into one entry; each direction it is
    boarded in here is listed with its headsign. Lines that only end here are flagged ``terminates``.
    """
    line_of = d.lines()[0]
    lines: dict[int, dict] = {}
    dir_of: dict[int, int] = {}  # pattern → index into _directions(route)
    a, b = int(d.sp_ptr[stop]), int(d.sp_ptr[stop + 1])
    for p, i in zip(d.sp_pat[a:b].tolist(), d.sp_i[a:b].tolist()):
        route = int(d.pat_route[p])
        lid = int(line_of[route])
        n = int(d.pat_trips[p + 1] - d.pat_trips[p])
        e = lines.get(lid)
        if e is None:
            e = lines[lid] = {"routes": {}, "heads": {}, "board": 0}
        e["routes"][route] = e["routes"].get(route, 0) + n
        if int(d.pat_flags[int(d.pat_ptr[p]) + i]) & NO_PICKUP:
            continue
        e["board"] += n
        h = _pat_headsign(d, p)
        cur = e["heads"].setdefault(h, {"trips": 0, "best": -1, "pat": p})
        cur["trips"] += n
        if n > cur["best"]:
            cur["best"], cur["pat"] = n, p
    for e in lines.values():
        for h in e["heads"].values():
            p = h["pat"]
            if p not in dir_of:
                for k, dr in enumerate(_directions(d, int(d.pat_route[p]))):
                    for q in dr["pats"]:
                        dir_of[q] = k
    groups: dict[str, list] = {}
    for e in lines.values():
        board = e["board"] > 0
        # the route boarded most often here represents the line
        heads = sorted(e["heads"].items(), key=lambda kv: -kv[1]["trips"])
        main = int(d.pat_route[heads[0][1]["pat"]]) if heads else max(e["routes"], key=e["routes"].get)
        info = _route_info(d, main)
        info["directions"] = []
        for h, x in heads:
            p = x["pat"]
            dr = int(d.pat_dir[p])
            info["directions"].append({"route_id": d.route_ids[int(d.pat_route[p])], "direction": dir_of.get(p, 0),
                                       "direction_id": dr if dr >= 0 else None, "headsign": h, "trips": x["trips"]})
        info["route_ids"] = sorted({d.route_ids[r] for r in e["routes"]})
        info["terminates"] = not board
        groups.setdefault(info["type"], []).append(info)
    out = []
    for mode in sorted(groups, key=lambda m: MODE_ORDER.index(m) if m in MODE_ORDER else 99):
        ls = sorted(groups[mode], key=lambda x: (x["terminates"], natural_key(x["route_short_name"] or ""),
                                                 x["agency"], x["directions"][0]["headsign"] if x["directions"] else ""))
        out.append({"type": mode, "mode_he": MODE_GROUP_HE.get(mode, ls[0]["mode_he"]), "lines": ls})
    return {"stop": d.stop_info(stop), "groups": out, "count": sum(len(g["lines"]) for g in out)}


def _directions(d: TransitData, route: int) -> list[dict]:
    """Directions of one route: patterns grouped by ``direction_id`` (or by the last stop when missing)."""
    dirs: dict[tuple, list[int]] = {}
    for p in d.route_patterns(route).tolist():
        dr = int(d.pat_dir[p])
        key = (0, dr) if dr >= 0 else (1, int(d.pat_stops[int(d.pat_ptr[p + 1]) - 1]))
        dirs.setdefault(key, []).append(p)
    out = []
    for key in sorted(dirs):
        pats = sorted(dirs[key], key=lambda p: -int(d.pat_trips[p + 1] - d.pat_trips[p]))
        out.append({"pats": pats, "trips": sum(int(d.pat_trips[p + 1] - d.pat_trips[p]) for p in pats),
                    "dir": key[1] if key[0] == 0 else -1})
    return out


def _serves(d: TransitData, pat: int, stop: int, board: bool = True) -> int:
    a, b = int(d.pat_ptr[pat]), int(d.pat_ptr[pat + 1])
    hits = np.flatnonzero(d.pat_stops[a:b] == stop)
    for h in hits.tolist():
        if not board or not int(d.pat_flags[a + h]) & NO_PICKUP:
            return h
    return -1


def line_view(d: TransitData, route: int, direction: int | None = None, from_stop: int | None = None) -> dict:
    """The full, ordered stop list of one direction of a line (its most common stop pattern)."""
    dirs = _directions(d, route)
    if not dirs:
        raise TransitError(404, "not_found", "לקו אין נסיעות בלוח הזמנים")
    if direction is not None and not 0 <= direction < len(dirs):
        raise TransitError(400, "invalid_parameter", f"direction חייב להיות בין 0 ל-{len(dirs) - 1}")
    if direction is None:
        direction = 0
        best = -1
        for k, dr in enumerate(dirs):
            score = dr["trips"] + (10 ** 9 if from_stop is not None and any(_serves(d, p, from_stop) >= 0 for p in dr["pats"]) else 0)
            if score > best:
                best, direction = score, k
    pats = dirs[direction]["pats"]
    pat = pats[0]
    if from_stop is not None and _serves(d, pat, from_stop) < 0:
        pat = next((p for p in pats if _serves(d, p, from_stop) >= 0),
                   next((p for p in pats if _serves(d, p, from_stop, board=False) >= 0), pat))
    a, b = int(d.pat_ptr[pat]), int(d.pat_ptr[pat + 1])
    # a representative trip: the most common timetable profile of the pattern
    t0, t1 = int(d.pat_trips[pat]), int(d.pat_trips[pat + 1])
    profs = d.trip_prof[t0:t1]
    vals, cnt = np.unique(profs, return_counts=True)
    prof = int(vals[int(np.argmax(cnt))])
    pa = int(d.prof_ptr[prof])
    stops = []
    for k in range(b - a):
        s = int(d.pat_stops[a + k])
        f = int(d.pat_flags[a + k])
        info = d.stop_info(s)
        info.update({"offset": int(d.prof_dep[pa + k] if k == 0 else d.prof_arr[pa + k]),
                     "pickup": not f & NO_PICKUP, "drop_off": not f & NO_DROPOFF})
        stops.append(info)
    from_index = _serves(d, pat, from_stop) if from_stop is not None else -1
    if from_stop is not None and from_index < 0:
        from_index = _serves(d, pat, from_stop, board=False)
    # every direction of the line (the MoT feed has one route per direction): for the selector
    options = []
    for r in d.line_routes(route):
        for k, dr in enumerate(_directions(d, r) if r != route else dirs):
            p = dr["pats"][0]
            pa_, pb_ = int(d.pat_ptr[p]), int(d.pat_ptr[p + 1])
            options.append({"route_id": d.route_ids[r], "direction": k, "headsign": _pat_headsign(d, p),
                            "from": d.stop_names[int(d.pat_stops[pa_])], "to": d.stop_names[int(d.pat_stops[pb_ - 1])],
                            "trips": dr["trips"], "num_stops": pb_ - pa_,
                            "serves_stop": from_stop is not None and any(_serves(d, q, from_stop) >= 0 for q in dr["pats"]),
                            "current": r == route and k == direction})
    options.sort(key=lambda o: (not o["current"], not o["serves_stop"], -o["trips"]))
    options = options[:12]
    options.sort(key=lambda o: (not o["serves_stop"], -o["trips"]))
    return {"route": _route_info(d, route), "direction": direction, "headsign": _pat_headsign(d, pat),
            "directions": options, "from_index": from_index if from_index >= 0 else None,
            "stops": stops, "trips": int(t1 - t0),
            "geometry": {"type": "LineString", "coordinates": _geometry_ride(d, pat, 0, b - a - 1)},
            "estimated_text": ESTIMATED_TEXT}
