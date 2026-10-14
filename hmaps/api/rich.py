"""Rich card content: photo gallery with credits, long texts, facts, trails and sites nearby.

Reads the ``media`` / ``texts`` / ``facts`` tables filled by ``hmaps.etl.rich``;
databases without them simply produce empty sections.

Images: a downloaded copy (``data/media/<key>``, see tools/media.py) is served
from ``/media/<key>``; otherwise the card loads the photo straight from
Wikimedia Commons (HMAPS_REMOTE_IMAGES, on by default). Mapillary photos go
through ``/media/mly/<id>``, which looks up a fresh thumbnail URL with the
server-side token and redirects to it.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import urllib.parse
import urllib.request

from starlette.requests import Request
from starlette.responses import FileResponse, RedirectResponse, Response
from starlette.routing import Route

from ..core import categories, geo, media
from ..etl.trails import BIKE_ROUTES, display_name
from .images import local_image
from .settings import settings

COMMONS_FILEPATH = "https://commons.wikimedia.org/wiki/Special:FilePath/"
SOURCE_HE = {"commons": "Wikimedia Commons", "mapillary": "Mapillary"}
TEXT_SOURCE_HE = {"wikipedia": "ויקיפדיה", "wikivoyage": "ויקימסע"}
TEXT_ORDER = {"wikipedia": 0, "wikivoyage": 1}
HERITAGE_SKIP = {"none"}

#: trip-site sub-categories (same list as hmaps.etl.rich – kept here to avoid importing the ETL at runtime)
TRIP_SUBS = frozenset({
    "museum", "archaeological", "zoo", "theme_park", "attraction", "memorial", "historic", "tourist_info",
    "camp_site", "park", "inpa_site", "nature_reserve", "viewpoint", "spring", "waterfall", "peak", "cave",
    "picnic", "stream", "ridge", "saddle", "cliff", "valley", "well", "beach",
})
#: things worth listing along a trail
ALONG_SUBS = TRIP_SUBS | {"drinking_water", "shelter", "trail_info", "parking"}


def image_src(url: str | None, width: int | None = None) -> str | None:
    """Local copy if downloaded, else the remote URL (when allowed)."""
    return local_image(url, width)


def commons_url(file: str) -> str:
    return COMMONS_FILEPATH + urllib.parse.quote(file)


def _q(con: sqlite3.Connection, sql: str, args: tuple) -> list:
    try:
        return con.execute(sql, args).fetchall()
    except sqlite3.OperationalError:  # database built before schema 5
        return []


def gallery(con: sqlite3.Connection, entity: str, ref_id: int, main_image: str | None = None) -> list[dict]:
    rows = _q(con, "SELECT source, file, width, height, title, author, license, license_url, page_url FROM media "
                   "WHERE entity = ? AND ref_id = ? ORDER BY CASE source WHEN 'commons' THEN 0 ELSE 1 END, rank",
              (entity, ref_id))
    out: list[dict] = []
    seen: set[str] = set()
    main_file = media.commons_file(main_image) if main_image else None
    if main_image and not any(r[1] == main_file for r in rows):
        src = image_src(main_image, 640) if main_file else image_src(main_image)
        if src:
            out.append({"src": src, "full": image_src(main_image, 1600) if main_file else src, "title": None,
                        "author": None, "license": None, "license_url": None,
                        "page_url": f"https://commons.wikimedia.org/wiki/File:{urllib.parse.quote(main_file)}" if main_file else None,
                        "source": "commons" if main_file else "web",
                        "source_he": "Wikimedia Commons" if main_file else None})
            seen.add(main_file or main_image)
    if main_file:
        rows.sort(key=lambda r: r[1] != main_file)  # the card's main photo first
    for source, file, w, h, title, author, lic, lic_url, page in rows:
        if file in seen:
            continue
        seen.add(file)
        if source == "commons":
            url = commons_url(file)
            src, full = image_src(url, 640), image_src(url, 1600)
        elif source == "mapillary":
            if not settings.remote_images:
                continue
            src = full = f"/media/mly/{urllib.parse.quote(file)}"
        else:
            continue
        if not src:
            continue
        out.append({"src": src, "full": full or src, "width": w, "height": h, "title": title, "author": author,
                    "license": lic, "license_url": lic_url, "page_url": page, "source": source,
                    "source_he": SOURCE_HE.get(source, source)})
    return out


def texts(con: sqlite3.Connection, entity: str, ref_id: int) -> list[dict]:
    rows = _q(con, "SELECT source, title, text, url, license FROM texts WHERE entity = ? AND ref_id = ?", (entity, ref_id))
    rows.sort(key=lambda r: TEXT_ORDER.get(r[0], 9))
    return [{"source": s, "source_he": TEXT_SOURCE_HE.get(s, s), "title": t, "text": x,
             "paragraphs": [p for p in x.split("\n\n") if p.strip()], "url": u, "license": lic}
            for s, t, x, u, lic in rows]


def _num(v: str, unit: str) -> str:
    try:
        f = float(v)
    except ValueError:
        return v
    return f"{f:,.0f} {unit}" if f >= 10 else f"{f:g} {unit}"


def facts(con: sqlite3.Connection, entity: str, ref_id: int, known: dict | None = None) -> tuple[list[dict], dict]:
    """Display rows + the raw dict (website / commons category …)."""
    raw = {k: v for k, v in _q(con, "SELECT key, value FROM facts WHERE entity = ? AND ref_id = ?", (entity, ref_id))}
    known = known or {}
    rows: list[dict] = []
    if raw.get("heritage") and raw["heritage"].lower() not in HERITAGE_SKIP:
        rows.append({"icon": "landmark", "label": "מעמד", "value": raw["heritage"]})
    if raw.get("inception"):
        rows.append({"icon": "history", "label": "הוקם / נוסד", "value": raw["inception"]})
    if raw.get("opened") and not raw.get("inception"):
        rows.append({"icon": "history", "label": "נפתח", "value": raw["opened"]})
    if raw.get("elevation") and not known.get("elevation"):
        rows.append({"icon": "mountain", "label": "גובה", "value": _num(raw["elevation"], "מ׳")})
    if raw.get("height"):
        rows.append({"icon": "mountain", "label": "גובה המבנה / המפל", "value": _num(raw["height"], "מ׳")})
    if raw.get("length") and not known.get("length_m"):
        rows.append({"icon": "road", "label": "אורך", "value": _num(raw["length"], "ק״מ" if float(raw["length"]) < 2000 else "מ׳")})
    if raw.get("area"):
        rows.append({"icon": "leaf", "label": "שטח", "value": _num(raw["area"], "קמ״ר" if float(raw["area"]) < 5000 else "דונם")})
    return rows, raw


def _to_rect(x: float, y: float, rect: tuple) -> float:
    cx, cy = min(max(x, rect[0]), rect[2]), min(max(y, rect[1]), rect[3])
    return 0.0 if (cx, cy) == (x, y) else geo.haversine(y, x, cy, cx)


def nearby_trails(con: sqlite3.Connection, lat: float, lon: float, radius: float = 300, limit: int = 6,
                  bbox: tuple | None = None) -> list[dict]:
    """Marked trails within *radius* m of the site (of its bounding box for areas)."""
    if bbox:
        a = geo.bbox_around(bbox[1], bbox[0], radius)
        b = geo.bbox_around(bbox[3], bbox[2], radius)
        minx, miny, maxx, maxy = a[0], a[1], b[2], b[3]
    else:
        minx, miny, maxx, maxy = geo.bbox_around(lat, lon, radius)
    rows = _q(con, "SELECT DISTINCT t.id, t.name, t.route, t.mark, t.length_m, t.importance, g.geom "
                   "FROM trail_segments_rtree r JOIN trail_segments g ON g.id = r.id JOIN trails t ON t.id = g.trail_id "
                   "WHERE r.min_lon <= ? AND r.max_lon >= ? AND r.min_lat <= ? AND r.max_lat >= ? "
                   "ORDER BY t.importance DESC LIMIT 200", (maxx, minx, maxy, miny))
    best: dict[int, dict] = {}
    from .outdoor import MARK_HEX
    for tid, name, route, mark, length, imp, geom in rows:
        line = geo.decode_polyline(geom)
        d, _ = geo.distance_to_line(lon, lat, line)
        if bbox:
            d = min(d, min(_to_rect(x, y, bbox) for x, y in line))
        if d > radius or (tid in best and best[tid]["distance"] <= d):
            continue
        best[tid] = {"id": f"trail-{tid}", "type": "trail", "name": display_name(name, route, mark),
                     "named": bool(name), "color": MARK_HEX.get(mark, MARK_HEX["other"]), "mark": mark,
                     "icon": "bike" if route in BIKE_ROUTES else "hiker", "length_m": length,
                     "distance": round(d), "importance": imp}
    out = sorted(best.values(), key=lambda t: (-t["named"], -t["importance"], t["distance"]))[:limit]
    for t in out:
        t.pop("importance")
    return out


def _site_item(r, distance: float | None = None, extra: dict | None = None) -> dict:
    sub = categories.SUB_BY_ID.get(r["sub"])
    cat = categories.CATEGORY_BY_ID.get(r["category"])
    return {"id": f"poi-{r['id']}", "type": "poi", "name": r["name"], "sub_category_he": (r["kind_he"] or sub.name_he) if sub else "",
            "secondary": (r["kind_he"] or sub.name_he) if sub else "",
            "icon": sub.icon if sub else "pin", "color": cat.color if cat else "#4B5563",
            "location": {"lat": r["lat"], "lon": r["lon"]},
            "thumbnail": image_src(r["image"], 160) if r["image"] else None,
            **({"distance": round(distance)} if distance is not None else {}), **(extra or {})}


def nearby_sites(con: sqlite3.Connection, poi_id: int, lat: float, lon: float, radius: float = 2500, limit: int = 8) -> list[dict]:
    minx, miny, maxx, maxy = geo.bbox_around(lat, lon, radius)
    subs = sorted(TRIP_SUBS - {"picnic", "camp_site", "well"})
    rows = _q(con, "SELECT p.id, p.name, p.category, p.sub, p.kind_he, p.lat, p.lon, p.image, p.importance FROM pois_rtree t "
                   "JOIN pois p ON p.id = t.id WHERE t.min_lon <= ? AND t.max_lon >= ? AND t.min_lat <= ? AND t.max_lat >= ? "
                   f"AND p.name IS NOT NULL AND p.id != ? AND p.sub IN ({','.join('?' * len(subs))}) "
                   "ORDER BY p.importance DESC LIMIT 60", (maxx, minx, maxy, miny, poi_id, *subs))
    out = []
    names: set[str] = set()
    for r in rows:
        d = geo.haversine(lat, lon, r["lat"], r["lon"])
        if d > radius or r["name"] in names:
            continue
        names.add(r["name"])
        out.append((r["importance"] - d / radius * 0.3, _site_item(r, d)))
    out.sort(key=lambda x: -x[0])
    return [x for _, x in out[:limit]]


# ------------------------------------------------------------------ trails

DIFFICULTY_HE = {"hiking": "קל", "mountain_hiking": "בינוני", "demanding_mountain_hiking": "קשה",
                 "alpine_hiking": "קשה מאוד", "demanding_alpine_hiking": "קשה מאוד", "difficult_alpine_hiking": "קשה מאוד"}
SPEED_KMH = {"hiking": 4.0, "foot": 4.0, "bicycle": 14.0, "mtb": 10.0}


def trail_rich(con: sqlite3.Connection, r, lines: list[list[tuple[float, float]]], extra: dict) -> dict:
    """Start / end, loop detection, time estimate, difficulty, sites along the way, gallery and texts."""
    longest = max(lines, key=geo.line_length_m) if lines else []
    start = {"lon": longest[0][0], "lat": longest[0][1]} if longest else None
    end = {"lon": longest[-1][0], "lat": longest[-1][1]} if longest else None
    loop = bool(start and end and geo.haversine(start["lat"], start["lon"], end["lat"], end["lon"]) < 300)
    speed = SPEED_KMH.get(r["route"], 4.0)
    minutes = round(r["length_m"] / 1000 / speed * 60)
    sac = extra.get("sac_scale")
    out = {
        "start": start, "end": end, "loop": loop or extra.get("roundtrip") == "yes",
        "duration_min": minutes if r["length_m"] < 60000 else None,
        "speed_kmh": speed,
        "duration_tag": extra.get("duration"),
        "difficulty": sac, "difficulty_he": DIFFICULTY_HE.get(sac or ""),
        "ascent_tag": extra.get("ascent"), "descent_tag": extra.get("descent"),
        "sites_along": sites_along(con, lines, r["length_m"]),
        "media": {"gallery": gallery(con, "trail", r["id"])},
        "texts": texts(con, "trail", r["id"]),
    }
    return out


def sites_along(con: sqlite3.Connection, lines: list[list[tuple[float, float]]], length_m: float,
                max_dist: float = 150, limit: int = 30) -> list[dict]:
    """Springs, viewpoints, ruins, water taps … within *max_dist* of the trail, in walking order."""
    if not lines or length_m > 120000:
        return []
    lines = [geo.simplify(line, 12.0) for line in lines]  # plenty for a 150 m corridor, much faster
    pts = [p for line in lines for p in line]
    minx, miny, maxx, maxy = geo.bbox_of(pts)
    pad = 0.002
    subs = sorted(ALONG_SUBS)
    rows = _q(con, "SELECT p.id, p.name, p.category, p.sub, p.kind_he, p.lat, p.lon, p.image, p.importance FROM pois_rtree t "
                   "JOIN pois p ON p.id = t.id WHERE t.min_lon <= ? AND t.max_lon >= ? AND t.min_lat <= ? AND t.max_lat >= ? "
                   f"AND p.sub IN ({','.join('?' * len(subs))}) ORDER BY p.importance DESC LIMIT 600",
              (maxx + pad, minx - pad, maxy + pad, miny - pad, *subs))
    if not rows:
        return []
    # cumulative distance along the longest piece, for ordering
    main = max(lines, key=geo.line_length_m)
    cum = [0.0]
    for (x0, y0), (x1, y1) in zip(main, main[1:]):
        cum.append(cum[-1] + geo.haversine(y0, x0, y1, x1))
    found = []
    for r in rows:
        best, pos = float("inf"), 0.0
        for line in lines:
            d, q = geo.distance_to_line(r["lon"], r["lat"], line)
            if d < best:
                best = d
                if line is main:
                    i = min(range(len(main)), key=lambda k: (main[k][0] - q[0]) ** 2 + (main[k][1] - q[1]) ** 2)
                    pos = cum[i]
                else:
                    pos = float("inf")
        if best > max_dist:
            continue
        if r["name"] is None and r["sub"] not in ("drinking_water", "spring", "viewpoint", "shelter"):
            continue
        item = _site_item(r, best, {"at_m": round(pos) if pos != float("inf") else None})
        if not item["name"]:
            item["name"] = item["sub_category_he"]
        found.append((pos, -r["importance"], item))
    found.sort(key=lambda x: (x[0], x[1]))
    # unnamed taps / shelters are useful but must not crowd out the sites
    named = [f for f in found if f[2]["name"] != f[2]["sub_category_he"]]
    if len(found) > limit:
        keep = {id(f) for f in named[:limit]}
        rest = [f for f in found if id(f) not in keep][: max(0, limit - len(keep))]
        keep |= {id(f) for f in rest}
        found = [f for f in found if id(f) in keep]
    return [f[2] for f in found]


# ------------------------------------------------------------------ image endpoints

async def media_file(req: Request) -> Response:
    k = req.path_params["key"]
    if not media.KEY_RE.match(k):
        return Response(status_code=404)
    p = settings.media_dir / k
    if not p.is_file():
        return Response(status_code=404)
    with open(p, "rb") as f:
        head = f.read(16)
    return FileResponse(p, media_type=media.sniff(head) or "application/octet-stream",
                        headers={"Cache-Control": "public, max-age=2592000, immutable"})


_MLY_CACHE: dict[str, tuple[float, str | None]] = {}
_MLY_LOCK = threading.Lock()


def _mapillary_thumb(image_id: str) -> str | None:
    from ..etl.rich import mapillary_token

    now = time.time()
    with _MLY_LOCK:
        hit = _MLY_CACHE.get(image_id)
        if hit and now - hit[0] < 1800:
            return hit[1]
    token = mapillary_token(settings.data_dir)
    url = None
    if token:
        req = urllib.request.Request(
            f"https://graph.mapillary.com/{urllib.parse.quote(image_id)}?fields=thumb_1024_url",
            headers={"Authorization": f"OAuth {token}", "User-Agent": "HebrewMaps/1.1"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                url = json.loads(resp.read()).get("thumb_1024_url")
        except Exception:  # noqa: BLE001
            url = None
    with _MLY_LOCK:
        _MLY_CACHE[image_id] = (now, url)
        if len(_MLY_CACHE) > 5000:
            _MLY_CACHE.clear()
    return url


async def media_mapillary(req: Request) -> Response:
    from starlette.concurrency import run_in_threadpool

    image_id = req.path_params["id"]
    if not image_id.isdigit() or not settings.remote_images:
        return Response(status_code=404)
    url = await run_in_threadpool(_mapillary_thumb, image_id)
    if not url:
        return Response(status_code=404)
    return RedirectResponse(url, status_code=302, headers={"Cache-Control": "private, max-age=1500"})


ROUTES = [
    Route("/media/mly/{id}", media_mapillary),
    Route("/media/{key}", media_file),
]
