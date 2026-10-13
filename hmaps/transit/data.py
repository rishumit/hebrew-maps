"""Loading the transit store and the static lookups the engine needs."""

from __future__ import annotations

import json
import logging
import math
import threading
from datetime import date, timedelta
from pathlib import Path

import numpy as np

from .gtfs import unpack

log = logging.getLogger("hmaps.transit")

# GTFS route_type → (mode id, Hebrew name)
_BASIC = {
    0: ("light_rail", "רכבת קלה"), 1: ("subway", "מטרו"), 2: ("rail", "רכבת"), 3: ("bus", "אוטובוס"),
    4: ("ferry", "מעבורת"), 5: ("funicular", "כרמלית"), 6: ("cable_car", "רכבל"), 7: ("funicular", "פוניקולר"),
    8: ("share_taxi", "מונית שירות"), 11: ("bus", "טרוליבוס"), 12: ("light_rail", "מונורייל"),
}
MODE_COLORS = {  # default badge colours (white text, ≥ 4.5:1 contrast)
    "bus": "#15803D", "rail": "#1D4ED8", "light_rail": "#B91C1C", "subway": "#7E22CE", "funicular": "#9D174D",
    "cable_car": "#9D174D", "ferry": "#0E7490", "share_taxi": "#A16207",
}


def mode_of(route_type: int) -> tuple[str, str]:
    if route_type in _BASIC:
        return _BASIC[route_type]
    if 100 <= route_type < 200:
        return "rail", "רכבת"
    if 400 <= route_type < 500:
        return "subway", "מטרו"
    if 900 <= route_type < 1000:
        return "light_rail", "רכבת קלה"
    if 1300 <= route_type < 1400:
        return "cable_car", "רכבל"
    return "bus", "אוטובוס"


class TransitData:
    """Read-only arrays from ``transit.npz`` plus derived indexes."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.mtime = self.path.stat().st_mtime_ns
        with np.load(self.path) as z:
            d = {k: z[k] for k in z.files}
        self.meta = json.loads(unpack(d.pop("meta"))[0])
        self.stop_names = unpack(d.pop("stop_names"))
        self.stop_ids = unpack(d.pop("stop_ids"))
        self.city_names = unpack(d.pop("city_names"))
        self.platform_names = unpack(d.pop("platform_names"))
        self.route_short = unpack(d.pop("route_short"))
        self.route_long = unpack(d.pop("route_long"))
        self.agency_names = unpack(d.pop("agency_names"))
        self.headsigns = unpack(d.pop("headsigns"))
        for k, v in d.items():
            setattr(self, k, v)
        self.start = date.fromisoformat(self.meta["start"])
        self.end = date.fromisoformat(self.meta["end"])
        self.n_days = self.svc_days.shape[1]
        self.n_stops = len(self.stop_lat)
        self.pat_len = np.diff(self.pat_ptr).astype(np.int64)
        n_trips = len(self.trip_start)
        self.trip_pat = np.repeat(np.arange(len(self.pat_route), dtype=np.int32), np.diff(self.pat_trips))
        assert len(self.trip_pat) == n_trips
        # stop → (pattern, index) positions, for departures boards
        pos_pat = np.repeat(np.arange(len(self.pat_route), dtype=np.int32), self.pat_len)
        order = np.argsort(self.pat_stops, kind="stable")
        self.sp_ptr = np.zeros(self.n_stops + 1, dtype=np.int64)
        np.add.at(self.sp_ptr, self.pat_stops + 1, 1)
        self.sp_ptr = np.cumsum(self.sp_ptr)
        self.sp_pat = pos_pat[order]
        self.sp_i = (order - self.pat_ptr[pos_pat[order]]).astype(np.int32)
        self.stop_used = np.diff(self.sp_ptr) > 0
        self.used_ids = np.flatnonzero(self.stop_used)
        lat0 = math.radians(31.5)
        self._kx = 111_320.0 * math.cos(lat0)
        self._ky = 110_574.0
        self._ux = self.stop_lon[self.used_ids].astype(np.float64) * self._kx
        self._uy = self.stop_lat[self.used_ids].astype(np.float64) * self._ky
        self.id_index = {s: i for i, s in enumerate(self.stop_ids)}
        self._active: dict[int, np.ndarray] = {}
        self._lock = threading.Lock()
        typ = self.route_type[self.pat_route]
        self.pat_mode = [mode_of(int(t))[0] for t in typ]
        # weekday "normal" level of service, for holiday detection
        dt = self.day_trips.astype(np.float64)
        wk = np.array([(self.start + timedelta(days=i)).weekday() for i in range(self.n_days)])
        weekdays = dt[(wk <= 3) | (wk == 6)]  # Sun–Thu
        self.normal_day_trips = float(np.median(weekdays)) if len(weekdays) else float(dt.max(initial=0))
        log.info("transit data loaded: %d stops, %d patterns, %d trips, %s – %s", int(self.stop_used.sum()),
                 len(self.pat_route), n_trips, self.start, self.end)

    # ------------------------------------------------------------------ calendar
    def day_index(self, d: date) -> int:
        return (d - self.start).days

    def covers(self, d: date) -> bool:
        return 0 <= self.day_index(d) < self.n_days

    def active(self, k: int) -> np.ndarray | None:
        """bool[n_trips]: trips running on feed day *k* (cached)."""
        if not 0 <= k < self.n_days:
            return None
        a = self._active.get(k)
        if a is None:
            a = self.svc_days[self.trip_service, k]
            with self._lock:
                if len(self._active) > 8:
                    self._active.clear()
                self._active[k] = a
        return a

    # ------------------------------------------------------------------ space
    def near(self, lat: float, lon: float, radius: float) -> tuple[np.ndarray, np.ndarray]:
        """Stops (that are served) within *radius* metres: (stop ids, distances)."""
        dx = (self._ux - lon * self._kx) * (math.cos(math.radians(lat)) / math.cos(math.radians(31.5)))
        dy = self._uy - lat * self._ky
        d = np.hypot(dx, dy)
        m = np.flatnonzero(d <= radius)
        o = np.argsort(d[m])
        return self.used_ids[m[o]], d[m[o]]

    # ------------------------------------------------------------------ names
    def stop_label(self, s: int) -> str:
        return self.stop_names[s]

    def stop_info(self, s: int) -> dict:
        code = int(self.stop_code[s])
        plat = self.platform_names[self.stop_platform[s]] if len(self.platform_names) > self.stop_platform[s] else ""
        return {"id": self.stop_ids[s], "name": self.stop_names[s], "code": code if code >= 0 else None,
                "city": self.city_names[self.stop_city[s]] or None, "platform": plat or None,
                "lat": round(float(self.stop_lat[s]), 6), "lon": round(float(self.stop_lon[s]), 6)}

    def trip_times(self, trip: int) -> tuple[np.ndarray, np.ndarray]:
        """(arrival, departure) seconds of the service day for every stop of *trip*."""
        p = int(self.trip_prof[trip])
        a, b = int(self.prof_ptr[p]), int(self.prof_ptr[p + 1])
        t0 = int(self.trip_start[trip])
        return self.prof_arr[a:b] + t0, self.prof_dep[a:b] + t0


_CACHE: dict[str, TransitData] = {}
_LOCK = threading.Lock()


def resolve(dir_: Path) -> Path | None:
    """The live store in *dir_* (pointer file written by :func:`hmaps.core.db.publish`)."""
    from ..core import db

    p = db.resolve(Path(dir_) / "transit.npz")
    return p if p.exists() else None


def get(dir_: Path) -> TransitData | None:
    """Cached store; reloaded when a new version is published."""
    p = resolve(dir_)
    if p is None:
        return None
    key = str(Path(dir_))
    d = _CACHE.get(key)
    if d is None or d.path != p or d.mtime != p.stat().st_mtime_ns:
        with _LOCK:
            d = _CACHE.get(key)
            if d is None or d.path != p or d.mtime != p.stat().st_mtime_ns:
                d = TransitData(p)
                _CACHE[key] = d
    return d
