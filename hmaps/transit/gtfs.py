"""GTFS → compact offline transit store (``data/transit/transit-<ts>.npz``).

The Israeli Ministry of Transport feed is ~1 GB unpacked (``stop_times.txt``
alone is ~13.5 M rows), so everything is streamed straight from the zip into
typed ``array`` buffers – nothing is loaded into pandas – and then reshaped with
numpy. Peak memory on the full feed is well under 2 GB.

Store layout (all numpy arrays in one ``.npz``; strings are UTF-8 blobs joined by ``\\n``):

* stops     – ``stop_lat/lon`` (float32), ``stop_code``, ``stop_parent``, names / cities / platforms / GTFS ids
* routes    – ``route_agency``, ``route_type``, ``route_color`` (0xRRGGBB or -1), short / long names, GTFS ``route_ids``
* patterns  – trips of one route that serve exactly the same stop sequence with the same pickup/drop-off
  rules. ``pat_ptr`` (CSR) → ``pat_stops`` / ``pat_flags`` (bit 1 = no pickup, bit 2 = no drop-off) /
  ``pat_shape_pos`` (index of the stop in the shape polyline); ``pat_dir`` = GTFS ``direction_id`` (-1 if none)
* trips     – sorted by (pattern, first departure); ``pat_trips`` (CSR) → ``trip_start`` (seconds of the
  service day, may exceed 24h), ``trip_end``, ``trip_prof``, ``trip_service``, ``trip_headsign``
* profiles  – de-duplicated relative timetables (``prof_ptr`` → ``prof_arr`` / ``prof_dep``, seconds
  since the trip's first departure). Most trips of a pattern share one profile, which keeps the
  13.5 M stop times down to a few hundred thousand integers.
* service   – ``svc_days`` bool[n_services, n_days] for the feed's date range (``meta.start``)
* transfers – foot paths between stops ≤ 400 m apart (straight line × 1.25, 4.8 km/h) as CSR
* shapes    – ``shape_ptr`` → ``shape_lon/lat`` (float32, simplified to 3 m)
"""

from __future__ import annotations

import csv
import io
import json
import logging
import math
import os
import re
import time
import urllib.request
import zipfile
from array import array
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np

log = logging.getLogger("hmaps.transit.gtfs")

GTFS_URL = os.environ.get("HMAPS_GTFS_URL", "https://gtfs.mot.gov.il/gtfsfiles/israel-public-transportation.zip")
REQUIRED = ("agency.txt", "routes.txt", "stops.txt", "trips.txt", "stop_times.txt")
TRANSFER_RADIUS_M = 400.0
WALK_SPEED_MS = 4.8 / 3.6
DETOUR = 1.25           # straight line → street distance
SHAPE_TOLERANCE_M = 3.0
SKIP_ROUTE_TYPES = {715}  # demand-responsive ("קו בהזמנה") – needs booking, not a scheduled service


# ----------------------------------------------------------------------------- download

def download(url: str = GTFS_URL, dest: Path | None = None, force: bool = False, max_age_h: float = 12.0) -> Path:
    from ..etl.enrich import USER_AGENT

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and not force and time.time() - dest.stat().st_mtime < max_age_h * 3600:
        log.info("using recent %s", dest)
        return dest
    tmp = dest.with_suffix(".part")
    log.info("downloading %s", url)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=180) as resp, open(tmp, "wb") as out:
        total = int(resp.headers.get("Content-Length") or 0)
        got, last = 0, 0.0
        while chunk := resp.read(1 << 20):
            out.write(chunk)
            got += len(chunk)
            if time.time() - last > 5:
                last = time.time()
                log.info("  %.0f / %.0f MB", got / 1e6, total / 1e6)
    validate(tmp)
    os.replace(tmp, dest)
    return dest


def validate(path: Path) -> None:
    """Cheap sanity check (the zip is ~1 GB unpacked, so no full CRC pass)."""
    try:
        with zipfile.ZipFile(path) as z:
            names = {Path(n).name for n in z.namelist()}
    except zipfile.BadZipFile as e:
        raise ValueError(f"not a valid GTFS zip: {path}") from e
    missing = [n for n in REQUIRED if n not in names]
    if "calendar.txt" not in names and "calendar_dates.txt" not in names:
        missing.append("calendar.txt|calendar_dates.txt")
    if missing:
        raise ValueError(f"GTFS zip is missing {', '.join(missing)}")


# ----------------------------------------------------------------------------- helpers

class _Zip:
    def __init__(self, path: Path):
        self.z = zipfile.ZipFile(path)
        self.names = {Path(n).name: n for n in self.z.namelist()}

    def has(self, name: str) -> bool:
        return name in self.names

    def text(self, name: str):
        return io.TextIOWrapper(self.z.open(self.names[name]), encoding="utf-8-sig", newline="")

    def rows(self, name: str):
        """Yields (header_index, row) – rows as lists."""
        with self.text(name) as f:
            r = csv.reader(f)
            header = [h.strip() for h in next(r)]
            idx = {h: i for i, h in enumerate(header)}
            for row in r:
                if row:
                    yield idx, row


def _get(idx: dict, row: list, col: str, default: str = "") -> str:
    i = idx.get(col)
    return row[i].strip() if i is not None and i < len(row) else default


def _secs(s: str) -> int:
    if len(s) == 8 and s[2] == ":":
        return int(s[0:2]) * 3600 + int(s[3:5]) * 60 + int(s[6:8])
    s = s.strip()
    if not s:
        return -1
    h, m, sec = s.split(":")
    return int(h) * 3600 + int(m) * 60 + int(sec)


def _date(s: str) -> date:
    return date(int(s[0:4]), int(s[4:6]), int(s[6:8]))


def pack(strings) -> np.ndarray:
    return np.frombuffer("\n".join(str(s).replace("\n", " ") for s in strings).encode("utf-8"), dtype=np.uint8)


def unpack(blob: np.ndarray) -> list[str]:
    return bytes(blob).decode("utf-8").split("\n") if len(blob) else [""]


def clean_name(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("''", '"')).strip()


class _Intern:
    def __init__(self) -> None:
        self.ids: dict[str, int] = {"": 0}

    def __call__(self, s: str) -> int:
        i = self.ids.get(s)
        if i is None:
            i = self.ids[s] = len(self.ids)
        return i

    def list(self) -> list[str]:
        out = [""] * len(self.ids)
        for s, i in self.ids.items():
            out[i] = s
        return out


def _hav_np(lon1, lat1, lon2, lat2):
    lon1, lat1, lon2, lat2 = (np.radians(np.asarray(a, dtype=np.float64)) for a in (lon1, lat1, lon2, lat2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 12_742_017.6 * np.arcsin(np.minimum(1.0, np.sqrt(a)))


# ----------------------------------------------------------------------------- build

def build(zip_path: Path, out_dir: Path, source: str = "") -> dict:
    """Convert *zip_path* and publish ``out_dir/transit-<ts>.npz``. Returns the meta dict."""
    t0 = time.time()
    validate(zip_path)
    gz = _Zip(zip_path)
    out: dict[str, np.ndarray] = {}

    # --- agencies
    agencies, agency_idx = [], {}
    for idx, row in gz.rows("agency.txt"):
        aid = _get(idx, row, "agency_id") or str(len(agencies))
        agency_idx[aid] = len(agencies)
        agencies.append(clean_name(_get(idx, row, "agency_name")))

    # --- routes
    route_idx: dict[str, int] = {}
    r_agency, r_type, r_color, r_tcolor = array("i"), array("i"), array("i"), array("i")
    r_short, r_long, r_ids = [], [], []
    for idx, row in gz.rows("routes.txt"):
        try:
            rtype = int(_get(idx, row, "route_type") or 3)
        except ValueError:
            rtype = 3
        if rtype in SKIP_ROUTE_TYPES:
            continue
        route_idx[_get(idx, row, "route_id")] = len(r_short)
        r_ids.append(_get(idx, row, "route_id"))
        r_agency.append(agency_idx.get(_get(idx, row, "agency_id"), 0) if agencies else 0)
        r_type.append(rtype)
        col = _get(idx, row, "route_color")
        tcol = _get(idx, row, "route_text_color")
        r_color.append(int(col, 16) if re.fullmatch(r"[0-9A-Fa-f]{6}", col) else -1)
        r_tcolor.append(int(tcol, 16) if re.fullmatch(r"[0-9A-Fa-f]{6}", tcol) else -1)
        r_short.append(clean_name(_get(idx, row, "route_short_name")))
        r_long.append(clean_name(_get(idx, row, "route_long_name")))
    if not agencies:
        agencies = [""]
    log.info("routes: %d (%d agencies)", len(r_short), len(agencies))

    # --- stops
    stop_idx: dict[str, int] = {}
    s_lat, s_lon, s_code, s_type = array("d"), array("d"), array("i"), array("b")
    s_ids, s_names, s_parent_raw = [], [], []
    cities, platforms = _Intern(), _Intern()
    s_city, s_plat = array("i"), array("i")
    for idx, row in gz.rows("stops.txt"):
        sid = _get(idx, row, "stop_id")
        try:
            lat, lon = float(_get(idx, row, "stop_lat")), float(_get(idx, row, "stop_lon"))
        except ValueError:
            continue
        stop_idx[sid] = len(s_ids)
        s_ids.append(sid)
        s_names.append(clean_name(_get(idx, row, "stop_name")))
        s_lat.append(lat)
        s_lon.append(lon)
        code = _get(idx, row, "stop_code")
        s_code.append(int(code) if code.isdigit() and len(code) < 10 else -1)
        lt = _get(idx, row, "location_type")
        s_type.append(int(lt) if lt.isdigit() else 0)
        s_parent_raw.append(_get(idx, row, "parent_station"))
        desc = _get(idx, row, "stop_desc")
        m = re.search(r"עיר:\s*(.*?)\s*(?:רציף:|$)", desc)
        s_city.append(cities(clean_name(m.group(1)) if m else ""))
        m = re.search(r"רציף:\s*(.*?)\s*(?:קומה:|$)", desc)
        s_plat.append(platforms(clean_name(m.group(1)) if m else ""))
    n_stops = len(s_ids)
    s_parent = np.array([stop_idx.get(p, -1) for p in s_parent_raw], dtype=np.int32)
    del s_parent_raw
    log.info("stops: %d", n_stops)

    # --- calendar → service-day bitmap
    svc_idx: dict[str, int] = {}
    cal_rows = []
    if gz.has("calendar.txt"):
        for idx, row in gz.rows("calendar.txt"):
            sid = _get(idx, row, "service_id")
            svc_idx.setdefault(sid, len(svc_idx))
            week = [_get(idx, row, d) == "1" for d in ("monday", "tuesday", "wednesday", "thursday", "friday",
                                                         "saturday", "sunday")]
            cal_rows.append((svc_idx[sid], _date(_get(idx, row, "start_date")), _date(_get(idx, row, "end_date")), week))
    exc_rows = []
    if gz.has("calendar_dates.txt"):
        for idx, row in gz.rows("calendar_dates.txt"):
            sid = _get(idx, row, "service_id")
            svc_idx.setdefault(sid, len(svc_idx))
            exc_rows.append((svc_idx[sid], _date(_get(idx, row, "date")), _get(idx, row, "exception_type") == "1"))
    all_dates = [c[1] for c in cal_rows] + [c[2] for c in cal_rows] + [e[1] for e in exc_rows]
    if not all_dates:
        raise ValueError("GTFS feed has no service calendar")
    start, end = min(all_dates), max(all_dates)
    if (end - start).days > 400:  # very old/far calendar rows – keep a sane window around today
        start = max(start, date.today() - timedelta(days=30))
        end = min(end, start + timedelta(days=400))
    n_days = (end - start).days + 1
    days = np.array([(start + timedelta(days=i)).weekday() for i in range(n_days)])
    svc_days = np.zeros((max(1, len(svc_idx)), n_days), dtype=bool)
    for si, a, b, week in cal_rows:
        lo, hi = max(0, (a - start).days), min(n_days - 1, (b - start).days)
        if lo > hi:
            continue
        wk = np.array(week)
        svc_days[si, lo:hi + 1] |= wk[days[lo:hi + 1]]
    for si, d, add in exc_rows:
        k = (d - start).days
        if 0 <= k < n_days:
            svc_days[si, k] = add
    log.info("services: %d, feed dates %s – %s", len(svc_idx), start, end)

    # --- trips
    trip_idx: dict[str, int] = {}
    t_route, t_service, t_head, t_shape = array("i"), array("i"), array("i"), array("i")
    t_dir = array("b")
    heads, shapes = _Intern(), _Intern()
    for idx, row in gz.rows("trips.txt"):
        r = route_idx.get(_get(idx, row, "route_id"))
        s = svc_idx.get(_get(idx, row, "service_id"))
        if r is None or s is None:
            continue
        trip_idx[_get(idx, row, "trip_id")] = len(t_route)
        t_route.append(r)
        t_service.append(s)
        t_head.append(heads(clean_name(_get(idx, row, "trip_headsign").replace("_", " - "))))
        t_shape.append(shapes(_get(idx, row, "shape_id")))
        dval = _get(idx, row, "direction_id")
        t_dir.append(int(dval) if dval in ("0", "1") else -1)
    n_trips_in = len(t_route)
    log.info("trips: %d", n_trips_in)

    # --- stop_times (streamed; the big one)
    tix, six, seq, arr, dep = array("i"), array("i"), array("i"), array("i"), array("i")
    flag = array("b")
    dist = array("f")
    rows = skipped = 0
    with gz.text("stop_times.txt") as f:
        header = [h.strip() for h in next(csv.reader([f.readline()]))]
        ci = {h: i for i, h in enumerate(header)}
        c_trip, c_stop, c_seq = ci["trip_id"], ci["stop_id"], ci["stop_sequence"]
        c_arr, c_dep = ci.get("arrival_time"), ci.get("departure_time")
        c_pu, c_do, c_dist = ci.get("pickup_type"), ci.get("drop_off_type"), ci.get("shape_dist_traveled")
        width = len(header)
        last_tid, last_t = None, -1
        get_trip, get_stop = trip_idx.get, stop_idx.get
        nan = float("nan")
        for line in f:
            p = line.rstrip("\r\n").split(",")
            if len(p) != width or '"' in line:
                p = next(csv.reader([line]))
                if not p:
                    continue
                p += [""] * (width - len(p))
            tid = p[c_trip]
            if tid != last_tid:
                last_tid, last_t = tid, get_trip(tid, -1)
            rows += 1
            if last_t < 0:
                skipped += 1
                continue
            s = get_stop(p[c_stop])
            if s is None:
                skipped += 1
                continue
            a = p[c_arr] if c_arr is not None else ""
            d = p[c_dep] if c_dep is not None else ""
            ta = _secs(a) if a else -1
            td = ta if d == a else (_secs(d) if d else -1)
            if ta < 0:
                ta = td
            if td < 0:
                td = ta
            tix.append(last_t)
            six.append(s)
            seq.append(int(p[c_seq]))
            arr.append(ta)
            dep.append(td)
            fl = 0
            if c_pu is not None and p[c_pu] == "1":
                fl |= 1
            if c_do is not None and p[c_do] == "1":
                fl |= 2
            flag.append(fl)
            if c_dist is not None:
                v = p[c_dist]
                dist.append(float(v) if v else nan)
            else:
                dist.append(nan)
            if rows % 2_000_000 == 0:
                log.info("  stop_times: %.1f M rows", rows / 1e6)
    log.info("stop_times: %d rows (%d skipped) in %.0fs", rows, skipped, time.time() - t0)

    tix_a = np.frombuffer(tix, dtype=np.int32)
    seq_a = np.frombuffer(seq, dtype=np.int32)
    order = np.lexsort((seq_a, tix_a))
    del seq, seq_a
    tix_a = tix_a[order]
    del tix
    six_a = np.frombuffer(six, dtype=np.int32)[order]
    del six
    arr_a = np.frombuffer(arr, dtype=np.int32)[order]
    del arr
    dep_a = np.frombuffer(dep, dtype=np.int32)[order]
    del dep
    flag_a = np.frombuffer(flag, dtype=np.int8)[order]
    del flag
    dist_a = np.frombuffer(dist, dtype=np.float32)[order]
    del dist, order

    bounds = np.flatnonzero(np.r_[True, tix_a[1:] != tix_a[:-1], True])
    starts, ends = bounds[:-1], bounds[1:]
    _interpolate_missing(arr_a, dep_a, starts, ends)

    # relative times (seconds since the trip's first departure)
    first_dep = dep_a[starts]
    rep = np.repeat(first_dep, ends - starts)
    rel_arr = (arr_a - rep).astype(np.int32)
    rel_dep = (dep_a - rep).astype(np.int32)
    del rep, arr_a, dep_a
    # first stop: no drop-off; last stop: no pickup (implicit in every journey planner)
    flag_a[starts] |= 2
    flag_a[ends - 1] |= 1

    # --- patterns + profiles
    t_route_a = np.frombuffer(t_route, dtype=np.int32)
    trips_of = tix_a[starts]
    pat_key: dict[bytes, int] = {}
    prof_key: dict[bytes, int] = {}
    pat_route, pat_rep = array("i"), array("i")
    prof_list_start = array("q", [0])
    prof_parts_arr, prof_parts_dep = [], []
    n_prof_elems = 0
    k_pat = np.empty(len(starts), dtype=np.int32)
    k_prof = np.empty(len(starts), dtype=np.int32)
    k_ok = np.ones(len(starts), dtype=bool)
    six_b, flag_b = six_a, flag_a
    for k in range(len(starts)):
        a, b = int(starts[k]), int(ends[k])
        if b - a < 2:
            k_ok[k] = False
            continue
        t = int(trips_of[k])
        key = int(t_route_a[t]).to_bytes(4, "little") + six_b[a:b].tobytes() + flag_b[a:b].tobytes()
        pid = pat_key.get(key)
        if pid is None:
            pid = pat_key[key] = len(pat_route)
            pat_route.append(int(t_route_a[t]))
            pat_rep.append(k)
        k_pat[k] = pid
        ra, rd = rel_arr[a:b], rel_dep[a:b]
        pkey = ra.tobytes() + rd.tobytes()
        qid = prof_key.get(pkey)
        if qid is None:
            qid = prof_key[pkey] = len(prof_key)
            prof_parts_arr.append(ra.copy())
            prof_parts_dep.append(rd.copy())
            n_prof_elems += b - a
            prof_list_start.append(n_prof_elems)
        k_prof[k] = qid
    del pat_key, prof_key
    n_pat = len(pat_route)
    log.info("patterns: %d, timetable profiles: %d (%d values)", n_pat, len(prof_parts_arr), n_prof_elems)

    # pattern stop lists from the representative trip
    pat_rep_a = np.frombuffer(pat_rep, dtype=np.int32)
    pat_len = (ends[pat_rep_a] - starts[pat_rep_a]).astype(np.int64)
    pat_ptr = np.zeros(n_pat + 1, dtype=np.int64)
    pat_ptr[1:] = np.cumsum(pat_len)
    src = np.repeat(starts[pat_rep_a] - pat_ptr[:-1], pat_len) + np.arange(int(pat_ptr[-1]))
    pat_stops = six_a[src].astype(np.int32)
    pat_flags = flag_a[src].astype(np.int8)
    pat_dist = dist_a[src]
    pat_shape = np.frombuffer(t_shape, dtype=np.int32)[trips_of[pat_rep_a]]
    pat_dir = np.frombuffer(t_dir, dtype=np.int8)[trips_of[pat_rep_a]].copy()
    del src, six_a, flag_a, dist_a

    # trips sorted by (pattern, first departure)
    keep = np.flatnonzero(k_ok)
    tp = k_pat[keep]
    ts = first_dep[keep]
    o = np.lexsort((ts, tp))
    keep = keep[o]
    trip_pat = k_pat[keep]
    trip_start = first_dep[keep].astype(np.int32)
    trip_prof = k_prof[keep]
    gtrip = trips_of[keep]
    prof_ptr = np.frombuffer(prof_list_start, dtype=np.int64).copy()
    prof_arr = np.concatenate(prof_parts_arr).astype(np.int32)
    prof_dep = np.concatenate(prof_parts_dep).astype(np.int32)
    del prof_parts_arr, prof_parts_dep
    trip_end = (trip_start + prof_arr[prof_ptr[trip_prof + 1] - 1]).astype(np.int32)
    pat_trips = np.zeros(n_pat + 1, dtype=np.int64)
    np.add.at(pat_trips, trip_pat + 1, 1)
    pat_trips = np.cumsum(pat_trips)
    n_trips = len(trip_pat)

    out.update({
        "stop_lat": np.frombuffer(s_lat, dtype=np.float64).astype(np.float32),
        "stop_lon": np.frombuffer(s_lon, dtype=np.float64).astype(np.float32),
        "stop_code": np.frombuffer(s_code, dtype=np.int32).copy(),
        "stop_type": np.frombuffer(s_type, dtype=np.int8).copy(),
        "stop_parent": s_parent,
        "stop_city": np.frombuffer(s_city, dtype=np.int32).copy(),
        "stop_platform": np.frombuffer(s_plat, dtype=np.int32).copy(),
        "stop_names": pack(s_names), "stop_ids": pack(s_ids), "city_names": pack(cities.list()),
        "platform_names": pack(platforms.list()),
        "route_agency": np.frombuffer(r_agency, dtype=np.int32).copy(),
        "route_type": np.frombuffer(r_type, dtype=np.int32).copy(),
        "route_color": np.frombuffer(r_color, dtype=np.int32).copy(),
        "route_text_color": np.frombuffer(r_tcolor, dtype=np.int32).copy(),
        "route_short": pack(r_short), "route_long": pack(r_long), "agency_names": pack(agencies),
        "route_ids": pack(r_ids), "pat_dir": pat_dir,
        "pat_route": np.frombuffer(pat_route, dtype=np.int32).copy(), "pat_ptr": pat_ptr, "pat_stops": pat_stops,
        "pat_flags": pat_flags, "pat_trips": pat_trips,
        "trip_start": trip_start, "trip_end": trip_end, "trip_prof": trip_prof.astype(np.int32),
        "trip_service": np.frombuffer(t_service, dtype=np.int32)[gtrip].copy(),
        "trip_headsign": np.frombuffer(t_head, dtype=np.int32)[gtrip].copy(),
        "headsigns": pack(heads.list()),
        "prof_ptr": prof_ptr, "prof_arr": prof_arr, "prof_dep": prof_dep,
        "svc_days": svc_days,
    })
    del t_route, t_service, t_head, t_dir, trip_idx, stop_idx

    # --- shapes
    shape_names = shapes.list()
    used_shapes = np.unique(pat_shape[pat_shape > 0])
    shape_out = _shapes(gz, shape_names, used_shapes, pat_shape, pat_ptr, pat_stops, pat_dist, out)
    out.update(shape_out)

    # --- foot transfers
    used_stops = np.zeros(n_stops, dtype=bool)
    used_stops[pat_stops] = True
    out.update(_transfers(out["stop_lat"], out["stop_lon"], used_stops))

    # --- per-day counts (for "holiday?" detection)
    trips_per_svc = np.bincount(out["trip_service"], minlength=svc_days.shape[0])
    out["day_trips"] = (trips_per_svc[:, None] * svc_days).sum(axis=0).astype(np.int64)

    built = datetime.now(timezone.utc).isoformat(timespec="seconds")
    meta = {
        "format": 1, "built": built, "source": source or str(zip_path),
        "start": start.isoformat(), "end": end.isoformat(),
        "feed_file_date": datetime.fromtimestamp(zip_path.stat().st_mtime, timezone.utc).strftime("%Y-%m-%d"),
        "stops": int(used_stops.sum()), "routes": len(r_short), "agencies": len(agencies), "trips": int(n_trips),
        "patterns": int(n_pat), "stop_times": int(rows - skipped), "transfers": int(len(out["tr_to"])),
        "shape_points": int(len(out["shape_lon"])),
    }
    out["meta"] = pack([json.dumps(meta, ensure_ascii=False)])
    meta["build_seconds"] = round(time.time() - t0, 1)
    _publish(out, Path(out_dir), meta)
    return meta


def _interpolate_missing(arr: np.ndarray, dep: np.ndarray, starts, ends) -> None:
    bad = np.flatnonzero(dep < 0)
    if not len(bad):
        return
    trip_of_row = np.searchsorted(starts, bad, side="right") - 1
    for k in np.unique(trip_of_row):
        a, b = int(starts[k]), int(ends[k])
        d = dep[a:b].astype(np.float64)
        ok = d >= 0
        if ok.sum() < 2:
            continue
        x = np.arange(b - a)
        d[~ok] = np.interp(x[~ok], x[ok], d[ok])
        dep[a:b] = np.round(d).astype(np.int32)
        aa = arr[a:b]
        aa[aa < 0] = dep[a:b][aa < 0]


def _shapes(gz: _Zip, shape_names, used, pat_shape, pat_ptr, pat_stops, pat_dist, out) -> dict:
    n_pat = len(pat_shape)
    empty = {"shape_ptr": np.zeros(1, dtype=np.int64), "shape_lon": np.zeros(0, np.float32),
             "shape_lat": np.zeros(0, np.float32), "pat_shape": np.full(n_pat, -1, np.int32),
             "pat_shape_pos": np.full(len(pat_stops), -1, np.int32)}
    if not gz.has("shapes.txt") or not len(used):
        return empty
    want = {shape_names[i]: j for j, i in enumerate(used)}
    sid, sseq, slat, slon = array("i"), array("i"), array("d"), array("d")
    with gz.text("shapes.txt") as f:
        header = [h.strip() for h in next(csv.reader([f.readline()]))]
        ci = {h: i for i, h in enumerate(header)}
        c_id, c_lat, c_lon, c_seq = ci["shape_id"], ci["shape_pt_lat"], ci["shape_pt_lon"], ci["shape_pt_sequence"]
        last, lj = None, None
        for line in f:
            p = line.rstrip("\r\n").split(",")
            if '"' in line:
                p = next(csv.reader([line]))
            if p[c_id] != last:
                last, lj = p[c_id], want.get(p[c_id])
            if lj is None:
                continue
            sid.append(lj)
            sseq.append(int(p[c_seq]))
            slat.append(float(p[c_lat]))
            slon.append(float(p[c_lon]))
    if not len(sid):
        return empty
    sid_a = np.frombuffer(sid, dtype=np.int32)
    o = np.lexsort((np.frombuffer(sseq, dtype=np.int32), sid_a))
    sid_a = sid_a[o]
    lat = np.frombuffer(slat, dtype=np.float64)[o]
    lon = np.frombuffer(slon, dtype=np.float64)[o]
    del sid, sseq, slat, slon, o
    n_sh = len(used)
    cnt = np.bincount(sid_a, minlength=n_sh)
    ptr = np.zeros(n_sh + 1, dtype=np.int64)
    ptr[1:] = np.cumsum(cnt)
    seg = np.zeros(len(lat))
    seg[1:] = _hav_np(lon[:-1], lat[:-1], lon[1:], lat[1:])
    seg[ptr[:-1][cnt > 0]] = 0.0
    cum = np.cumsum(seg)
    cum -= np.repeat(cum[ptr[:-1]], cnt)  # per-shape cumulative distance

    stop_lat, stop_lon = out["stop_lat"], out["stop_lon"]
    local = np.searchsorted(used, pat_shape)  # pattern → index into used shapes
    has = (pat_shape > 0) & (local < n_sh)
    has[has] &= used[local[has]] == pat_shape[has]
    pos = np.full(len(pat_stops), -1, dtype=np.int64)
    keep = np.zeros(len(lat), dtype=bool)
    for p in np.flatnonzero(has):
        j = int(local[p])
        s0, s1 = int(ptr[j]), int(ptr[j + 1])
        if s1 - s0 < 2:
            continue
        a, b = int(pat_ptr[p]), int(pat_ptr[p + 1])
        c = cum[s0:s1]
        d = pat_dist[a:b].astype(np.float64)
        if np.all(np.isfinite(d)) and d[-1] > 0 and np.all(np.diff(d) >= 0):
            idx = np.searchsorted(c, d * (c[-1] / d[-1]))
            idx = np.minimum(idx, s1 - s0 - 1)
        else:  # nearest shape point, kept monotonic
            st = pat_stops[a:b]
            dx = (lon[s0:s1][None, :] - stop_lon[st][:, None]) * math.cos(math.radians(31.5))
            dy = lat[s0:s1][None, :] - stop_lat[st][:, None]
            idx = np.maximum.accumulate(np.argmin(dx * dx + dy * dy, axis=1))
        pos[a:b] = s0 + idx
        keep[s0 + idx] = True
    # thin the polylines (Douglas–Peucker, SHAPE_TOLERANCE_M), always keeping every stop point
    x = lon * (111_320.0 * math.cos(math.radians(31.5)))
    y = lat * 110_574.0
    for j in range(n_sh):
        s0, s1 = int(ptr[j]), int(ptr[j + 1])
        if s1 - s0 > 2:
            keep[s0:s1] |= _douglas_peucker(x[s0:s1], y[s0:s1], SHAPE_TOLERANCE_M)
    keep[ptr[:-1][cnt > 0]] = True
    keep[ptr[1:][cnt > 0] - 1] = True
    new_index = np.cumsum(keep) - 1
    kept_cnt = np.bincount(sid_a[keep], minlength=n_sh)
    new_ptr = np.zeros(n_sh + 1, dtype=np.int64)
    new_ptr[1:] = np.cumsum(kept_cnt)
    pos_new = np.where(pos >= 0, new_index[np.maximum(pos, 0)], -1).astype(np.int32)
    pat_sh = np.where(has, local, -1).astype(np.int32)
    log.info("shapes: %d (%d → %d points)", n_sh, len(lat), int(keep.sum()))
    return {"shape_ptr": new_ptr, "shape_lon": lon[keep].astype(np.float32), "shape_lat": lat[keep].astype(np.float32),
            "pat_shape": pat_sh, "pat_shape_pos": pos_new}


def _douglas_peucker(x: np.ndarray, y: np.ndarray, tol: float) -> np.ndarray:
    n = len(x)
    keep = np.zeros(n, dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        a, b = stack.pop()
        if b - a < 2:
            continue
        dx, dy = x[b] - x[a], y[b] - y[a]
        xs, ys = x[a + 1:b] - x[a], y[a + 1:b] - y[a]
        length = math.hypot(dx, dy)
        d = np.hypot(xs, ys) if length == 0 else np.abs(xs * dy - ys * dx) / length
        i = int(np.argmax(d))
        if d[i] > tol:
            m = a + 1 + i
            keep[m] = True
            stack.append((a, m))
            stack.append((m, b))
    return keep


def _transfers(lat32, lon32, used) -> dict:
    """Foot paths between nearby stops (grid search, straight line × DETOUR)."""
    n = len(lat32)
    ids = np.flatnonzero(used)
    lat = lat32[ids].astype(np.float64)
    lon = lon32[ids].astype(np.float64)
    ky = 110_574.0
    kx = 111_320.0 * math.cos(math.radians(31.5))
    x, y = lon * kx, lat * ky
    cell = TRANSFER_RADIUS_M * 1.05
    cx, cy = np.floor(x / cell).astype(np.int64), np.floor(y / cell).astype(np.int64)
    key = cx * 1_000_003 + cy
    o = np.argsort(key, kind="stable")
    ks = key[o]
    src_all, dst_all, d_all = [], [], []
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            nk = (cx + dx) * 1_000_003 + (cy + dy)
            lo = np.searchsorted(ks, nk, side="left")
            hi = np.searchsorted(ks, nk, side="right")
            c = hi - lo
            tot = int(c.sum())
            if not tot:
                continue
            src = np.repeat(np.arange(len(ids)), c)
            off = np.arange(tot) - np.repeat(np.cumsum(c) - c, c)
            dst = o[np.repeat(lo, c) + off]
            d = np.hypot((x[src] - x[dst]) * (np.cos(np.radians(lat[src])) / math.cos(math.radians(31.5))), y[src] - y[dst])
            m = (d <= TRANSFER_RADIUS_M) & (src != dst)
            src_all.append(src[m])
            dst_all.append(dst[m])
            d_all.append(d[m])
    src = np.concatenate(src_all) if src_all else np.zeros(0, np.int64)
    dst = np.concatenate(dst_all) if dst_all else np.zeros(0, np.int64)
    d = np.concatenate(d_all) if d_all else np.zeros(0)
    gs, gd = ids[src], ids[dst]
    o = np.lexsort((gd, gs))
    gs, gd, d = gs[o], gd[o], d[o]
    ptr = np.zeros(n + 1, dtype=np.int64)
    np.add.at(ptr, gs + 1, 1)
    ptr = np.cumsum(ptr)
    secs = np.ceil(d * DETOUR / WALK_SPEED_MS).astype(np.int32)
    log.info("foot transfers: %d", len(gd))
    return {"tr_ptr": ptr, "tr_to": gd.astype(np.int32), "tr_sec": secs, "tr_dist": np.round(d * DETOUR).astype(np.int32)}


def _publish(arrays: dict, out_dir: Path, meta: dict) -> Path:
    from ..core import db

    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / "transit.building.npz"
    np.savez(tmp, **arrays)
    live = db.publish(tmp, out_dir / "transit.npz")
    mtmp = out_dir / "transit.json.tmp"
    mtmp.write_text(json.dumps({**meta, "file": live.name}, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(mtmp, out_dir / "transit.json")
    log.info("transit data ready: %s (%.1f MB)", live, live.stat().st_size / 1e6)
    return live


def cmd(args) -> None:
    """``python -m hmaps.etl gtfs`` entry point."""
    data_dir = Path(os.environ.get("HMAPS_DATA", "data"))
    if args.zip:
        zp = Path(args.zip)
    else:
        zp = download(args.url or GTFS_URL, data_dir / "sources" / "gtfs" / "israel-public-transportation.zip",
                      force=args.force)
    out_dir = Path(args.out_dir) if args.out_dir else data_dir / "transit"
    meta = build(zp, out_dir, source=args.url or GTFS_URL if not args.zip else str(zp))
    log.info("stops %s | routes %s | trips %s | patterns %s | feed %s – %s | %.0fs",
             f"{meta['stops']:,}", f"{meta['routes']:,}", f"{meta['trips']:,}", f"{meta['patterns']:,}",
             meta["start"], meta["end"], meta["build_seconds"])
