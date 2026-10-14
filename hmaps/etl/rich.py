"""Rich content for trip sites and marked trails: photo galleries, texts and facts.

Open sources only (no Google): everything stored here may be kept offline and
redistributed with attribution.

* **Wikimedia Commons** – photos taken near each site / along each trail
  (geosearch), the Commons category of the Wikidata item (P373) and the lead
  image of the Wikipedia article. Author and licence are stored for the credit
  line in the card.
* **Hebrew Wikipedia** – articles matched by location + name for sites without a
  ``wikipedia`` tag; the full introduction of every matched article.
* **Wikidata** – extra facts (heritage status, inception, elevation, area,
  length, official website, Commons category, Hebrew Wikivoyage article).
* **Hebrew Wikivoyage** – travel-guide introductions (sitelink or geosearch).
* **Mapillary** (optional, needs a free token in ``HMAPS_MAPILLARY_TOKEN`` or
  ``data/mapillary-token.txt``) – street-level photos where Commons has few.

Run it on the live database without rebuilding the map::

    python -m hmaps.etl enrich              # all steps
    python -m hmaps.etl enrich --steps commons,wikipedia --limit 200

Every HTTP answer is cached in ``data/cache/rich-http.sqlite`` (60 days), so an
interrupted run resumes quickly and a full rebuild repeats only what is new.
The images themselves are NOT downloaded – the card loads them from Commons
(``tools/media.py`` can fetch offline copies later).
"""

from __future__ import annotations

import html
import json
import logging
import math
import os
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Iterable

from ..core import db, geo
from ..core.hebrew import normalize

log = logging.getLogger(__name__)

USER_AGENT = ("HebrewMaps/1.1 (https://github.com/rishumit/hebrew-maps; self-hosted open-data map of Israel) "
              "python-urllib")
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
HEWIKI_API = "https://he.wikipedia.org/w/api.php"
HEVOY_API = "https://he.wikivoyage.org/w/api.php"
WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"
MAPILLARY_API = "https://graph.mapillary.com"
COMMONS_FILEPATH = "https://commons.wikimedia.org/wiki/Special:FilePath/"

#: trip sites: nature, heritage, viewpoints, parks … (named POIs of these sub-categories)
TRIP_SUBS = frozenset({
    "museum", "archaeological", "zoo", "theme_park", "attraction", "memorial", "historic", "tourist_info",
    "camp_site", "park", "inpa_site", "nature_reserve", "viewpoint", "spring", "waterfall", "peak", "cave",
    "picnic", "stream", "ridge", "saddle", "cliff", "valley", "well", "beach",
})
#: search radius (m) around point sites, by sub-category
POINT_RADIUS = {"peak": 400, "viewpoint": 300, "archaeological": 300, "historic": 200, "spring": 200,
                "waterfall": 250, "cave": 150, "museum": 150, "memorial": 150, "well": 120, "picnic": 200,
                "camp_site": 250, "beach": 400, "zoo": 400, "theme_park": 300}
DEFAULT_RADIUS = 200
MAX_RADIUS = 3000
MAX_GALLERY = 10


# Photos we do not want in a trip card: maps / diagrams / logos, and houses of worship
# of other religions (the map does not list those places either – see build.py).
_EXCLUDE = re.compile(
    r"\b(map|maps|karte|plan|diagram|logo|svg|chart|scheme|coat[ _]of[ _]arms|flag|signature|"
    r"church|chapel|cathedral|basilica|monastery|convent|mosque|minaret|abbey|"
    r"portrait|selfie|wedding|concert)\b|מפה|מפת|לוגו|כנסי|מסגד|מנזר|קתדרל|בזיליק",
    re.I)
_PHOTO_MIME = {"image/jpeg", "image/webp"}
_TAG_RE = re.compile(r"<[^>]+>")


def ensure_tables(con: sqlite3.Connection) -> None:
    """Add the media / texts / facts tables to a database built before schema 5."""
    con.executescript(db.RICH_DDL)


# =========================================================================== HTTP with cache

class Blocked(RuntimeError):
    """The host refuses us for good (content filter page, 403): skip it for the rest of the run."""


class Http:
    """JSON GET/POST with an on-disk cache, retries, maxlag handling and politeness.

    Wikimedia rate-limits clients that send many parallel requests (HTTP 429), so requests to
    each host are spaced by *interval* seconds (slower after every 429). A host that answers
    with a content-filter block page (e.g. NetFree, HTTP 418) or 403 is skipped from then on.
    """

    def __init__(self, cache_path: Path | None, ttl_days: float = 60, offline: bool = False,
                 opener: Callable[[urllib.request.Request, int], bytes] | None = None, interval: float | None = None):
        self.ttl = ttl_days * 86400
        self.offline = offline
        self._open = opener or self._urlopen
        self._lock = threading.Lock()
        self.requests = self.hits = self.errors = 0
        self.interval = interval if interval is not None else float(os.environ.get("HMAPS_RICH_INTERVAL", "1.0"))
        self._host_lock: dict[str, threading.Lock] = {}
        self._next: dict[str, float] = {}
        self._gap: dict[str, float] = {}
        self.blocked: dict[str, str] = {}
        self._db = None
        if cache_path is not None:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(cache_path, check_same_thread=False)
            self._db.execute("CREATE TABLE IF NOT EXISTS http (k TEXT PRIMARY KEY, ts REAL, body BLOB)")
            self._db.commit()

    @staticmethod
    def _urlopen(req: urllib.request.Request, timeout: int) -> bytes:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()

    def _cached(self, k: str) -> Any:
        if self._db is None:
            return None
        with self._lock:
            row = self._db.execute("SELECT ts, body FROM http WHERE k = ?", (k,)).fetchone()
        if row and (self.offline or time.time() - row[0] < self.ttl):
            self.hits += 1
            return json.loads(zlib.decompress(row[1]))
        return None

    def _store(self, k: str, value: Any) -> None:
        if self._db is None:
            return
        blob = zlib.compress(json.dumps(value, ensure_ascii=False).encode("utf-8"), 6)
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO http VALUES (?, ?, ?)", (k, time.time(), blob))
            self._db.commit()

    def get(self, url: str, params: dict | None = None, *, headers: dict | None = None, cache: bool = True,
            post: bool = False, retries: int = 4, timeout: int = 60) -> Any:
        params = dict(params or {})
        query = urllib.parse.urlencode(sorted(params.items()))
        k = f"{'POST ' if post else ''}{url}?{query}"
        if cache:
            hit = self._cached(k)
            if hit is not None:
                return hit
        if self.offline:
            raise RuntimeError("offline")
        host = urllib.parse.urlsplit(url).hostname or ""
        if host in self.blocked:
            raise Blocked(f"{host}: {self.blocked[host]}")
        last: Exception | None = None
        for attempt in range(retries):
            self._pace(host)
            body = query.encode() if post else None
            full = url if post else (url + ("&" if "?" in url else "?") + query if query else url)
            req = urllib.request.Request(full, data=body, headers={"User-Agent": USER_AGENT, "Accept": "application/json",
                                                                   **(headers or {})})
            try:
                self.requests += 1
                raw = self._open(req, timeout)
                data = json.loads(raw.decode("utf-8-sig"))
                err = data.get("error") if isinstance(data, dict) else None
                if isinstance(err, dict) and err.get("code") == "maxlag":
                    time.sleep(5 * (attempt + 1))
                    continue
                if cache:
                    self._store(k, data)
                return data
            except urllib.error.HTTPError as e:
                last = e
                if e.code in (429, 503):
                    try:
                        wait = float(e.headers.get("Retry-After") or 0)
                    except ValueError:
                        wait = 0
                    self._slow_down(host, wait or 20 * (attempt + 1))
                    continue
                if e.code in (403, 418, 451):
                    try:
                        head = e.read(2000).decode("utf-8", "replace").lower()
                    except Exception:  # noqa: BLE001
                        head = ""
                    why = "blocked by the content filter" if ("netfree" in head or e.code == 418) else f"HTTP {e.code}"
                    with self._lock:
                        if host not in self.blocked:
                            self.blocked[host] = why
                            log.warning("%s is %s – skipping it for the rest of this run", host, why)
                    raise Blocked(f"{host}: {why}")
                if 400 <= e.code < 500:
                    break
                time.sleep(2 ** attempt)
            except Exception as e:  # noqa: BLE001 – network is best effort
                last = e
                time.sleep(2 ** attempt)
        self.errors += 1
        raise RuntimeError(f"request failed: {url} ({last})")

    def _pace(self, host: str) -> None:
        with self._lock:
            lk = self._host_lock.setdefault(host, threading.Lock())
        with lk:
            now = time.time()
            wait = self._next.get(host, 0.0) - now
            if wait > 0:
                time.sleep(wait)
            self._next[host] = max(now, self._next.get(host, 0.0)) + self._gap.get(host, self.interval)

    def _slow_down(self, host: str, pause: float) -> None:
        """After a 429: pause this host and space its requests further apart."""
        with self._lock:
            gap = min(6.0, max(self.interval, self._gap.get(host, self.interval)) * 1.5)
            self._gap[host] = gap
            self._next[host] = max(self._next.get(host, 0.0), time.time() + min(pause, 120))
        log.info("  %s asked us to slow down – pausing %.0fs, then one request every %.1fs", host, min(pause, 120), gap)

    def mw(self, api: str, **params) -> dict:
        """MediaWiki action API (formatversion 2, maxlag)."""
        return self.get(api, {"action": "query", "format": "json", "formatversion": 2, "maxlag": 5, **params})

    def close(self) -> None:
        if self._db is not None:
            self._db.close()


# =========================================================================== helpers

def _clean_html(s: str | None, limit: int = 160) -> str | None:
    if not s:
        return None
    t = html.unescape(_TAG_RE.sub("", s))
    t = re.sub(r"\s+", " ", t).strip()
    if not t:
        return None
    return t if len(t) <= limit else t[: limit - 1].rsplit(" ", 1)[0] + "…"


def _core_name(text: str) -> str:
    """Normalised name without a parenthesised disambiguation: "עין גדי (שמורת טבע)" → "עינ גדי"."""
    return normalize(re.sub(r"\s*\([^)]*\)\s*", " ", text or ""))


_GENERIC = {normalize(w) for w in ("נחל", "הר", "עין", "מעיין", "מערת", "מערה", "גן", "פארק", "שמורת", "טבע", "לאומי",
                                   "חורבת", "תל", "באר", "מצפה", "תצפית", "אתר", "חניון", "יער", "גבעת", "רמת", "מפל",
                                   "the", "of", "park", "nature", "reserve", "national", "mount", "spring", "cave")}


def name_match(name: str | None, other: str | None) -> float:
    """0..1 similarity of two place names (distinctive tokens only)."""
    a, b = _core_name(name or ""), _core_name(other or "")
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ta = [t for t in a.split() if t not in _GENERIC and len(t) > 1]
    tb = set(t for t in b.split() if t not in _GENERIC and len(t) > 1)
    if not ta:
        return 0.0
    hit = sum(1 for t in ta if t in tb or any(len(t) > 3 and (t in u or u in t) for u in tb))
    return hit / len(ta)


def title_mentions(title: str, names: Iterable[str | None]) -> bool:
    """True when a Commons file title contains the (distinctive part of the) site name."""
    t = normalize(re.sub(r"\.[a-z0-9]+$", "", title, flags=re.I).replace("_", " "))
    if not t:
        return False
    for n in names:
        core = _core_name(n or "")
        toks = [x for x in core.split() if x not in _GENERIC and len(x) > 2]
        if toks and all(x in t for x in toks):
            return True
    return False


def _radius(sub: str, extra: dict) -> tuple[float, tuple | None]:
    bbox = extra.get("bbox")
    if bbox and len(bbox) == 4:
        minx, miny, maxx, maxy = bbox
        half = geo.haversine(miny, minx, maxy, maxx) / 2
        return max(DEFAULT_RADIUS, min(MAX_RADIUS, half)), tuple(bbox)
    return POINT_RADIUS.get(sub, DEFAULT_RADIUS), None


def _in_bbox(lat: float, lon: float, bbox: tuple | None, pad: float = 0.0005) -> bool:
    return bool(bbox) and bbox[0] - pad <= lon <= bbox[2] + pad and bbox[1] - pad <= lat <= bbox[3] + pad


def _commons_candidates(pages: list[dict]) -> list[dict]:
    """Commons pages (with imageinfo) → photo candidates with credit fields."""
    out = []
    for p in pages:
        ii = (p.get("imageinfo") or [None])[0]
        if not ii:
            continue
        title = p.get("title", "")
        file = title.split(":", 1)[-1].replace(" ", "_")
        if ii.get("mime") not in _PHOTO_MIME:
            continue
        if (ii.get("width") or 0) < 400 or (ii.get("height") or 0) < 250:
            continue
        md = ii.get("extmetadata") or {}
        desc = _clean_html((md.get("ImageDescription") or {}).get("value"), 300) or ""
        if _EXCLUDE.search(title.replace("_", " ")) or _EXCLUDE.search(desc):
            continue
        coords = (p.get("coordinates") or [None])[0] or {}
        out.append({
            "file": file,
            "width": ii.get("width"), "height": ii.get("height"),
            "title": _clean_html((md.get("ObjectName") or {}).get("value"), 140)
                     or re.sub(r"\.[A-Za-z0-9]+$", "", file).replace("_", " "),
            "author": _clean_html((md.get("Artist") or {}).get("value"), 100),
            "license": _clean_html((md.get("LicenseShortName") or {}).get("value"), 40),
            "license_url": (md.get("LicenseUrl") or {}).get("value"),
            "page_url": ii.get("descriptionurl") or f"https://commons.wikimedia.org/wiki/File:{urllib.parse.quote(file)}",
            "lat": coords.get("lat"), "lon": coords.get("lon"),
            "desc": desc,
        })
    return out


_IMAGEINFO = {"prop": "imageinfo|coordinates", "iiprop": "url|size|mime|extmetadata",
              "iiextmetadatafilter": "Artist|LicenseShortName|LicenseUrl|ImageDescription|ObjectName",
              "colimit": "max"}


def commons_near(http: Http, lat: float, lon: float, radius: float, limit: int = 30) -> list[dict]:
    data = http.mw(COMMONS_API, generator="geosearch", ggscoord=f"{lat:.6f}|{lon:.6f}",
                   ggsradius=int(max(10, min(10000, radius))), ggslimit=limit, ggsnamespace=6, **_IMAGEINFO)
    return _commons_candidates(data.get("query", {}).get("pages", []))


def commons_category(http: Http, category: str, limit: int = 20) -> list[dict]:
    data = http.mw(COMMONS_API, generator="categorymembers", gcmtitle=f"Category:{category}", gcmtype="file",
                   gcmlimit=limit, **_IMAGEINFO)
    return _commons_candidates(data.get("query", {}).get("pages", []))


def commons_files(http: Http, files: list[str]) -> list[dict]:
    out: list[dict] = []
    for i in range(0, len(files), 40):
        batch = ["File:" + f.replace("_", " ") for f in files[i:i + 40]]
        data = http.mw(COMMONS_API, titles="|".join(batch), **_IMAGEINFO)
        out += _commons_candidates(data.get("query", {}).get("pages", []))
    return out


def _file_of(url_or_name: str | None) -> str | None:
    from ..core import media
    if not url_or_name:
        return None
    if "://" in url_or_name:
        return media.commons_file(url_or_name)
    return url_or_name.split(":", 1)[-1].replace(" ", "_")


class CommonsIndex:
    """Geotagged Commons files by map tile: one ``list=geosearch`` per ~4 km tile instead of one
    search per site (11k sites → a few hundred requests). Full tiles (500 results) are split."""

    TILE = 0.04  # degrees (~4.4 × 3.8 km)

    def __init__(self, http: Http):
        self.http = http
        self.tiles: dict[tuple[int, int], list[dict]] = {}

    def keys_for(self, lat: float, lon: float, radius_m: float) -> set[tuple[int, int]]:
        dlat = radius_m / 111_000
        dlon = radius_m / (111_000 * max(0.3, math.cos(math.radians(lat))))
        t = self.TILE
        return {(i, j) for i in range(math.floor((lat - dlat) / t), math.floor((lat + dlat) / t) + 1)
                for j in range(math.floor((lon - dlon) / t), math.floor((lon + dlon) / t) + 1)}

    def _box(self, top: float, left: float, bottom: float, right: float, depth: int = 0) -> list[dict]:
        data = self.http.mw(COMMONS_API, list="geosearch", gsbbox=f"{top:.5f}|{left:.5f}|{bottom:.5f}|{right:.5f}",
                            gsnamespace=6, gslimit=500)
        res = data.get("query", {}).get("geosearch", [])
        if len(res) >= 500 and depth < 3:  # crowded (cities): split in four
            mlat, mlon = (top + bottom) / 2, (left + right) / 2
            out: list[dict] = []
            for box in ((top, left, mlat, mlon), (top, mlon, mlat, right), (mlat, left, bottom, mlon), (mlat, mlon, bottom, right)):
                out += self._box(*box, depth=depth + 1)
            return out
        return res

    def load(self, keys: set[tuple[int, int]], workers: int) -> None:
        todo = sorted(k for k in keys if k not in self.tiles)
        log.info("commons: %d map tiles to scan", len(todo))
        t = self.TILE

        def one(k):
            i, j = k
            return self._box((i + 1) * t, j * t, i * t, (j + 1) * t)

        for k, res in _parallel(one, todo, workers, "commons tiles"):
            pts = []
            for r in res or []:
                title = r.get("title", "")
                name = title.split(":", 1)[-1]
                if not re.search(r"\.(jpe?g|webp)$", name, re.I) or _EXCLUDE.search(name.replace("_", " ")):
                    continue
                pts.append({"file": name.replace(" ", "_"), "title": re.sub(r"\.[A-Za-z0-9]+$", "", name),
                            "lat": r.get("lat"), "lon": r.get("lon"), "desc": ""})
            self.tiles[k] = pts

    def near(self, lat: float, lon: float, radius_m: float) -> list[dict]:
        out, seen = [], set()
        for k in self.keys_for(lat, lon, radius_m):
            for p in self.tiles.get(k, ()):
                if p["file"] not in seen:
                    seen.add(p["file"])
                    out.append(p)
        return out


def pick_photos(cands: list[dict], *, lat: float, lon: float, radius: float, bbox: tuple | None,
                names: list[str | None], boost: float = 0.0, near: float | None = None) -> list[tuple[float, dict]]:
    """Score candidates; keep the ones that are either named after the site or clearly at it."""
    near = near if near is not None else min(radius, 150.0)
    out = []
    for c in cands:
        named = title_mentions(c["file"], names) or title_mentions(c["title"] or "", names)
        if c.get("lat") is not None:
            d = geo.haversine(lat, lon, c["lat"], c["lon"])
            inside = _in_bbox(c["lat"], c["lon"], bbox)
        else:
            d, inside = (0.0, True) if boost else (radius, False)
        if not (boost or named or d <= near or inside):
            continue
        score = boost + (2.0 if named else 0.0) + max(0.0, 1.0 - d / max(radius, 1.0)) + (0.2 if c.get("desc") else 0)
        out.append((score, c))
    return out


def _store_media(con: sqlite3.Connection, entity: str, ref_id: int, scored: list[tuple[float, dict]],
                 source: str = "commons", keep: int = MAX_GALLERY) -> int:
    seen: set[str] = set()
    rows = []
    for _, c in sorted(scored, key=lambda x: -x[0]):
        if c["file"] in seen:
            continue
        seen.add(c["file"])
        rows.append(c)
        if len(rows) >= keep:
            break
    con.execute("DELETE FROM media WHERE entity = ? AND ref_id = ? AND source = ?", (entity, ref_id, source))
    con.executemany(
        "INSERT OR IGNORE INTO media (entity, ref_id, rank, source, file, width, height, title, author, license,"
        " license_url, page_url, lat, lon) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(entity, ref_id, i, source, c["file"], c.get("width"), c.get("height"), c.get("title"), c.get("author"),
          c.get("license"), c.get("license_url"), c.get("page_url"), c.get("lat"), c.get("lon")) for i, c in enumerate(rows)])
    return len(rows)


def _parallel(fn: Callable[[Any], Any], items: list, workers: int, label: str) -> Iterable[tuple[Any, Any]]:
    """Run *fn* over *items* in a small thread pool; yields (item, result) in input order, logs progress."""
    t0 = time.time()
    last = 0.0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futures = [(it, ex.submit(fn, it)) for it in items]
        for n, (it, fut) in enumerate(futures, 1):
            try:
                res = fut.result()
            except Exception as e:  # noqa: BLE001 – one bad item must not stop the run
                log.debug("%s: %s", label, e)
                res = None
            if time.time() - last > 15 or n == len(futures):
                last = time.time()
                log.info("  %s: %d / %d (%.0fs)", label, n, len(futures), time.time() - t0)
            yield it, res


# =========================================================================== targets

def trip_sites(con: sqlite3.Connection, limit: int | None = None) -> list[dict]:
    subs = sorted(TRIP_SUBS)
    sql = (f"SELECT id, name, name_en, sub, lat, lon, wikidata, wikipedia, image, extra FROM pois "
           f"WHERE name IS NOT NULL AND sub IN ({','.join('?' * len(subs))}) ORDER BY importance DESC")
    rows = con.execute(sql + (f" LIMIT {int(limit)}" if limit else ""), subs).fetchall()
    out = []
    for r in rows:
        extra = json.loads(r[9]) if r[9] else {}
        radius, bbox = _radius(r[3], extra)
        out.append({"id": r[0], "name": r[1], "name_en": r[2], "sub": r[3], "lat": r[4], "lon": r[5],
                    "wikidata": r[6], "wikipedia": r[7], "image": r[8], "radius": radius, "bbox": bbox})
    return out


def named_trails(con: sqlite3.Connection, limit: int | None = None) -> list[dict]:
    try:
        rows = con.execute("SELECT id, name, name_en, route, length_m, extra FROM trails "
                           "WHERE name IS NOT NULL ORDER BY importance DESC"
                           + (f" LIMIT {int(limit)}" if limit else "")).fetchall()
    except sqlite3.OperationalError:
        return []
    out = []
    for tid, name, name_en, route, length, extra in rows:
        lines = [geo.decode_polyline(g[0]) for g in
                 con.execute("SELECT geom FROM trail_segments WHERE trail_id = ? ORDER BY id", (tid,))]
        out.append({"id": tid, "name": name, "name_en": name_en, "route": route, "length_m": length,
                    "extra": json.loads(extra) if extra else {}, "lines": lines})
    return out


def sample_points(lines: list[list[tuple[float, float]]], every_m: float, max_points: int) -> list[tuple[float, float]]:
    """(lon, lat) points spread along the trail (the longest pieces first)."""
    pts: list[tuple[float, float]] = []
    for line in sorted(lines, key=lambda ln: -geo.line_length_m(ln)):
        acc, nxt = 0.0, 0.0
        for (x0, y0), (x1, y1) in zip(line, line[1:]):
            seg = geo.haversine(y0, x0, y1, x1)
            while seg > 0 and acc + seg >= nxt:
                f = (nxt - acc) / seg
                pts.append((x0 + (x1 - x0) * f, y0 + (y1 - y0) * f))
                nxt += every_m
            acc += seg
    if len(pts) > max_points:
        step = len(pts) / max_points
        pts = [pts[int(i * step)] for i in range(max_points)]
    return pts


# =========================================================================== steps

def step_wikipedia_match(con: sqlite3.Connection, http: Http, sites: list[dict], workers: int) -> int:
    """Find Hebrew Wikipedia articles (by location + name) for sites that have none."""
    todo = [s for s in sites if not s["wikipedia"]]
    log.info("wikipedia: matching %d sites without an article", len(todo))

    def one(s: dict) -> tuple[str, str | None] | None:
        data = http.mw(HEWIKI_API, generator="geosearch", ggscoord=f"{s['lat']:.6f}|{s['lon']:.6f}",
                       ggsradius=int(min(10000, max(500, s["radius"] * 2))), ggslimit=15,
                       prop="pageprops|coordinates", ppprop="wikibase_item|disambiguation")
        best, best_score = None, 0.0
        for p in data.get("query", {}).get("pages", []):
            if "disambiguation" in (p.get("pageprops") or {}):
                continue
            sc = max(name_match(s["name"], p["title"]), name_match(p["title"], s["name"]) * 0.9)
            if sc > best_score:
                best, best_score = p, sc
        if best is None or best_score < 0.75:
            return None
        return best["title"], (best.get("pageprops") or {}).get("wikibase_item")

    n = 0
    for s, res in _parallel(one, todo, workers, "wikipedia match"):
        if not res:
            continue
        title, qid = res
        s["wikipedia"] = f"he:{title}"
        if qid and not s["wikidata"]:
            s["wikidata"] = qid
        con.execute("UPDATE pois SET wikipedia = COALESCE(wikipedia, ?), wikidata = COALESCE(wikidata, ?) WHERE id = ?",
                    (s["wikipedia"], qid, s["id"]))
        n += 1
    con.commit()
    log.info("wikipedia: %d new article matches", n)
    return n


def _intro(text: str, limit: int = 2400) -> str:
    paras = [re.sub(r"[ \t]+", " ", p).strip() for p in text.split("\n")]
    paras = [p for p in paras if p and not p.startswith("==")]
    out, size = [], 0
    for p in paras:
        if size + len(p) > limit and out:
            break
        out.append(p)
        size += len(p)
    t = "\n\n".join(out)
    return t if len(t) <= limit else t[: limit - 1].rsplit(" ", 1)[0] + "…"


def _short(text: str, limit: int = 320) -> str:
    t = re.sub(r"\s+", " ", text).strip()
    return t if len(t) <= limit else t[: limit - 3].rsplit(" ", 1)[0] + "…"


def fetch_extracts(http: Http, api: str, titles: list[str]) -> dict[str, dict]:
    """title → {"title", "text", "image", "qid"} (intro sections, plain text)."""
    out: dict[str, dict] = {}
    for i in range(0, len(titles), 20):
        batch = titles[i:i + 20]
        data = http.mw(api, prop="extracts|pageimages|pageprops", exintro=1, explaintext=1, exlimit=20,
                       piprop="name", ppprop="wikibase_item", redirects=1, titles="|".join(batch))
        q = data.get("query", {})
        back = {r["to"]: r["from"] for r in q.get("redirects", [])}
        norm = {r["to"]: r["from"] for r in q.get("normalized", [])}
        for p in q.get("pages", []):
            if p.get("missing") or not (p.get("extract") or "").strip():
                continue
            orig = back.get(p["title"], p["title"])
            orig = norm.get(orig, orig)
            rec = {"title": p["title"], "text": p["extract"], "image": p.get("pageimage"),
                   "qid": (p.get("pageprops") or {}).get("wikibase_item")}
            out[orig] = rec
            out[p["title"]] = rec
    return out


def step_wikipedia_texts(con: sqlite3.Connection, http: Http, sites: list[dict], trails: list[dict]) -> int:
    """Full article introductions (texts table) + lead image file name for the gallery."""
    want: dict[str, list[tuple[str, dict]]] = {}
    for s in sites:
        if s["wikipedia"] and s["wikipedia"].startswith("he:"):
            want.setdefault(s["wikipedia"][3:].strip(), []).append(("poi", s))
    for t in trails:
        wp = t["extra"].get("wikipedia") or ""
        if wp.startswith("he:"):
            want.setdefault(wp[3:].strip(), []).append(("trail", t))
    titles = list(want)
    log.info("wikipedia: fetching %d article introductions", len(titles))
    got: dict[str, dict] = {}
    for i in range(0, len(titles), 200):
        try:
            got.update(fetch_extracts(http, HEWIKI_API, titles[i:i + 200]))
        except RuntimeError as e:
            log.warning("wikipedia: %s", e)
        log.info("  wikipedia texts: %d / %d", min(i + 200, len(titles)), len(titles))
    n = 0
    for title, users in want.items():
        rec = got.get(title)
        if not rec:
            continue
        text = _intro(rec["text"])
        url = f"https://he.wikipedia.org/wiki/{urllib.parse.quote(rec['title'].replace(' ', '_'))}"
        for entity, obj in users:
            con.execute("INSERT OR REPLACE INTO texts VALUES (?, ?, 'wikipedia', ?, ?, ?, 'CC BY-SA 4.0')",
                        (entity, obj["id"], rec["title"], text, url))
            if entity == "poi":
                con.execute("UPDATE pois SET description = COALESCE(description, ?) WHERE id = ?",
                            (_short(rec["text"]), obj["id"]))
                if rec["image"]:
                    obj.setdefault("lead_files", []).append(rec["image"])
                if rec["qid"] and not obj["wikidata"]:
                    obj["wikidata"] = rec["qid"]
                    con.execute("UPDATE pois SET wikidata = COALESCE(wikidata, ?) WHERE id = ?", (rec["qid"], obj["id"]))
            elif rec["image"]:
                obj.setdefault("lead_files", []).append(rec["image"])
            n += 1
    con.commit()
    log.info("wikipedia: %d texts", n)
    return n


FACT_PROPS = {"P373": "commons_category", "P856": "website", "P1435": "heritage", "P571": "inception",
              "P2044": "elevation", "P2046": "area", "P2043": "length", "P2048": "height", "P1619": "opened"}


def step_wikidata_facts(con: sqlite3.Connection, http: Http, sites: list[dict], trails: list[dict]) -> int:
    by_q: dict[str, list[tuple[str, dict]]] = {}
    for entity, objs in (("poi", sites), ("trail", trails)):
        for o in objs:
            wd = o.get("wikidata") or (o.get("extra") or {}).get("wikidata")
            m = re.search(r"Q\d+", wd or "")
            if m:
                by_q.setdefault(m.group(0), []).append((entity, o))
    qids = list(by_q)
    log.info("wikidata: facts for %d items", len(qids))
    n = 0
    for i in range(0, len(qids), 120):
        batch = qids[i:i + 120]
        query = f"""
SELECT ?item ?p ?v ?vLabel ?voy WHERE {{
  VALUES ?item {{ {' '.join('wd:' + q for q in batch)} }}
  OPTIONAL {{
    VALUES ?p {{ {' '.join('wdt:' + p for p in FACT_PROPS)} }}
    ?item ?p ?v.
  }}
  OPTIONAL {{ ?voy schema:about ?item; schema:isPartOf <https://he.wikivoyage.org/>. }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "he,en". }}
}}"""
        try:
            data = http.get(WIKIDATA_SPARQL, {"query": query, "format": "json"}, post=True,
                            headers={"Accept": "application/sparql-results+json"})
        except RuntimeError as e:
            log.warning("wikidata: %s", e)
            continue
        facts: dict[str, dict[str, str]] = {}
        for b in data.get("results", {}).get("bindings", []):
            q = b["item"]["value"].rsplit("/", 1)[-1]
            f = facts.setdefault(q, {})
            if b.get("voy"):
                f["wikivoyage"] = urllib.parse.unquote(b["voy"]["value"].rsplit("/", 1)[-1]).replace("_", " ")
            if not b.get("p"):
                continue
            key = FACT_PROPS.get(b["p"]["value"].rsplit("/", 1)[-1])
            v = b.get("vLabel", b.get("v", {})).get("value") or b.get("v", {}).get("value")
            if not key or not v or key in f:
                continue
            if key in ("inception", "opened") and re.match(r"^-?\d{4}", v):
                y = int(v[:5] if v.startswith("-") else v[:4])
                v = f"{-y} לפנה״ס" if y < 0 else str(y)
            elif key in ("elevation", "area", "length", "height"):
                try:
                    v = f"{float(v):g}"
                except ValueError:
                    continue
            elif re.match(r"^Q\d+$", v):
                continue  # unlabeled item
            f[key] = v
        for q, f in facts.items():
            for entity, o in by_q.get(q, []):
                for k, v in f.items():
                    con.execute("INSERT OR REPLACE INTO facts VALUES (?, ?, ?, ?)", (entity, o["id"], k, v))
                o["facts"] = f
                n += 1
        con.commit()
        log.info("  wikidata: %d / %d", min(i + 120, len(qids)), len(qids))
    return n


def _finalize(http: Http, picks: dict[tuple[str, int], list[tuple[float, dict]]], workers: int) -> dict[str, dict]:
    """imageinfo (size, MIME, author, licence) for every picked file, 40 per request."""
    files = sorted({c["file"] for lst in picks.values() for _, c in lst if "page_url" not in c})
    log.info("commons: details of %d photos", len(files))
    info: dict[str, dict] = {}
    batches = [files[i:i + 40] for i in range(0, len(files), 40)]
    for _, res in _parallel(lambda b: commons_files(http, b), batches, workers, "photo details"):
        for c in res or []:
            info[c["file"]] = c
    return info


def step_commons(con: sqlite3.Connection, http: Http, sites: list[dict], trails: list[dict], workers: int,
                 do_sites: bool = True, do_trails: bool = True) -> dict[str, int]:
    """Photo galleries for trip sites and named trails (one Commons tile index for both)."""
    index = CommonsIndex(http)
    keys: set[tuple[int, int]] = set()
    trail_pts: dict[int, list[tuple[float, float]]] = {}
    if do_sites:
        for s in sites:
            keys |= index.keys_for(s["lat"], s["lon"], s["radius"])
    if do_trails:
        for t in trails:
            pts = sample_points(t["lines"], max(800.0, min(6000.0, t["length_m"] / 8)), 10)
            trail_pts[t["id"]] = pts
            for lon, lat in pts:
                keys |= index.keys_for(lat, lon, 250)
    log.info("commons: %d sites, %d trails", len(sites) if do_sites else 0, len(trail_pts))
    index.load(keys, workers)

    picks: dict[tuple[str, int], list[tuple[float, dict]]] = {}
    if do_sites:
        cats = [(s, (s.get("facts") or {}).get("commons_category")) for s in sites]
        cats = [(s, c) for s, c in cats if c]
        log.info("commons: %d Commons categories", len(cats))
        cat_res = {sc[0]["id"]: res for sc, res in
                   _parallel(lambda sc: commons_category(http, sc[1], limit=12), cats, workers, "commons categories")}
        for s in sites:
            names = [s["name"], s["name_en"]]
            scored = pick_photos(index.near(s["lat"], s["lon"], s["radius"]), lat=s["lat"], lon=s["lon"],
                                 radius=s["radius"], bbox=s["bbox"], names=names)
            scored.sort(key=lambda x: -x[0])
            scored = scored[:14]  # a few spares: some drop out after the size / licence check
            if cat_res.get(s["id"]):
                scored += pick_photos(cat_res[s["id"]], lat=s["lat"], lon=s["lon"], radius=s["radius"], bbox=s["bbox"],
                                      names=names, boost=3.0)
            main = [f for f in [_file_of(s.get("image")), *s.get("lead_files", [])] if f]
            scored += [(5.0 - i * 0.1, {"file": f, "title": None, "lat": None, "lon": None, "desc": ""})
                       for i, f in enumerate(dict.fromkeys(main))]
            if scored:
                picks[("poi", s["id"])] = scored
    if do_trails:
        for t in trails:
            scored = []
            for k, (lon, lat) in enumerate(trail_pts.get(t["id"], [])):
                near = pick_photos(index.near(lat, lon, 250), lat=lat, lon=lon, radius=250, bbox=None,
                                   names=[t["name"], t["name_en"]], near=250)
                near.sort(key=lambda x: -x[0])
                scored += [(sc - k * 0.001, c) for sc, c in near[:3]]  # spread along the trail
            scored += [(5.0, {"file": f, "title": None, "lat": None, "lon": None, "desc": ""}) for f in t.get("lead_files") or []]
            if scored:
                picks[("trail", t["id"])] = scored

    info = _finalize(http, picks, workers)
    out = {"site_galleries": 0, "trail_galleries": 0}
    by_id = {s["id"]: s for s in sites}
    for (entity, ref), scored in picks.items():
        full = []
        for sc, c in scored:
            d = c if "page_url" in c else info.get(c["file"])
            if d:
                full.append((sc, {**d, "lat": d.get("lat") if d.get("lat") is not None else c.get("lat"),
                                  "lon": d.get("lon") if d.get("lon") is not None else c.get("lon")}))
        if full and _store_media(con, entity, ref, full):
            out["site_galleries" if entity == "poi" else "trail_galleries"] += 1
            if entity == "poi" and ref in by_id:
                best = max(full, key=lambda x: x[0])[1]
                con.execute("UPDATE pois SET image = COALESCE(image, ?) WHERE id = ?",
                            (COMMONS_FILEPATH + urllib.parse.quote(best["file"]), ref))
    con.commit()
    log.info("commons: %d sites and %d trails with photos", out["site_galleries"], out["trail_galleries"])
    return out


def step_trail_wikipedia(con: sqlite3.Connection, http: Http, trails: list[dict], workers: int) -> int:
    """Wikipedia articles for named trails without a wikipedia tag (exact title match)."""
    todo = [t for t in trails if not t["extra"].get("wikipedia") and t["length_m"] > 3000]

    def one(t: dict) -> str | None:
        data = http.mw(HEWIKI_API, list="search", srsearch=f'intitle:"{t["name"]}"', srlimit=5, srnamespace=0)
        for r in data.get("query", {}).get("search", []):
            if _core_name(r["title"]) == _core_name(t["name"]):
                return r["title"]
        return None

    n = 0
    for t, title in _parallel(one, todo, workers, "trail articles"):
        if title:
            t["extra"]["wikipedia"] = f"he:{title}"
            n += 1
    log.info("wikipedia: %d trail articles", n)
    return n


def step_wikivoyage(con: sqlite3.Connection, http: Http, sites: list[dict], workers: int) -> int:
    """Travel-guide introductions from Hebrew Wikivoyage (sitelink, else geosearch + name)."""
    linked = {s["id"]: s["facts"]["wikivoyage"] for s in sites if (s.get("facts") or {}).get("wikivoyage")}
    # geosearch only for notable sites (with a Wikipedia article): Wikivoyage has few articles on single sites
    todo = [s for s in sites if s["id"] not in linked and s["wikipedia"]]

    def one(s: dict) -> str | None:
        data = http.mw(HEVOY_API, list="geosearch", gscoord=f"{s['lat']:.6f}|{s['lon']:.6f}",
                       gsradius=int(min(10000, max(1000, s["radius"] * 3))), gslimit=10)
        for p in data.get("query", {}).get("geosearch", []):
            if name_match(s["name"], p["title"]) >= 0.8:
                return p["title"]
        return None

    for s, title in _parallel(one, todo, workers, "wikivoyage match"):
        if title:
            linked[s["id"]] = title
    titles = sorted(set(linked.values()))
    log.info("wikivoyage: %d articles for %d sites", len(titles), len(linked))
    got: dict[str, dict] = {}
    for i in range(0, len(titles), 100):
        try:
            got.update(fetch_extracts(http, HEVOY_API, titles[i:i + 100]))
        except RuntimeError as e:
            log.warning("wikivoyage: %s", e)
    n = 0
    for pid, title in linked.items():
        rec = got.get(title)
        if not rec:
            continue
        url = f"https://he.wikivoyage.org/wiki/{urllib.parse.quote(rec['title'].replace(' ', '_'))}"
        con.execute("INSERT OR REPLACE INTO texts VALUES ('poi', ?, 'wikivoyage', ?, ?, ?, 'CC BY-SA 4.0')",
                    (pid, rec["title"], _intro(rec["text"], 1800), url))
        con.execute("UPDATE pois SET description = COALESCE(description, ?) WHERE id = ?", (_short(rec["text"]), pid))
        n += 1
    con.commit()
    log.info("wikivoyage: %d texts", n)
    return n


def mapillary_token(data_dir: Path) -> str | None:
    tok = os.environ.get("HMAPS_MAPILLARY_TOKEN", "").strip()
    if not tok:
        p = data_dir / "mapillary-token.txt"
        if p.is_file():
            tok = p.read_text(encoding="utf-8").strip()
    return tok or None


def step_mapillary(con: sqlite3.Connection, http: Http, token: str, sites: list[dict], trails: list[dict],
                   workers: int) -> int:
    """Street-level photos for sites with fewer than 3 Commons photos and for trails."""
    have = dict(con.execute("SELECT ref_id, COUNT(*) FROM media WHERE entity = 'poi' AND source = 'commons' GROUP BY ref_id"))
    todo: list[tuple[str, dict, list[tuple[float, float]]]] = []
    for s in sites:
        if have.get(s["id"], 0) < 3:
            todo.append(("poi", s, [(s["lon"], s["lat"])]))
    for t in trails:
        todo.append(("trail", t, sample_points(t["lines"], max(1000.0, t["length_m"] / 6), 6)))
    log.info("mapillary: %d sites / trails", len(todo))

    def one(job) -> list[tuple[float, dict]]:
        entity, obj, pts = job
        out = []
        d = 0.0006 if entity == "poi" else 0.0004
        for k, (lon, lat) in enumerate(pts):
            data = http.get(f"{MAPILLARY_API}/images", {
                "fields": "id,captured_at,creator,computed_geometry,is_pano", "limit": 5,
                "bbox": f"{lon - d:.6f},{lat - d:.6f},{lon + d:.6f},{lat + d:.6f}"},
                headers={"Authorization": f"OAuth {token}"})
            for img in data.get("data", []):
                if img.get("is_pano"):
                    continue
                c = (img.get("computed_geometry") or {}).get("coordinates") or [lon, lat]
                out.append((1.0 - k * 0.01 - geo.haversine(lat, lon, c[1], c[0]) / 1000, {
                    "file": str(img["id"]), "title": None,
                    "author": (img.get("creator") or {}).get("username"),
                    "license": "CC BY-SA 4.0", "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
                    "page_url": f"https://www.mapillary.com/app/?pKey={img['id']}", "lat": c[1], "lon": c[0]}))
                break  # one photo per sample point
        return out

    n = 0
    for (entity, obj, _), scored in _parallel(one, todo, workers, "mapillary"):
        if scored and _store_media(con, entity, obj["id"], scored, source="mapillary", keep=6):
            n += 1
    con.commit()
    log.info("mapillary: %d with photos", n)
    return n


# =========================================================================== driver

ALL_STEPS = ("wikipedia", "wikidata", "commons", "trails", "wikivoyage", "mapillary")


def run(con: sqlite3.Connection, data_dir: Path, steps: Iterable[str] = ALL_STEPS, limit: int | None = None,
        workers: int = 4, http: Http | None = None) -> dict:
    """Enrich *con* in place. Returns counters for the log / quality report."""
    steps = set(steps)
    ensure_tables(con)
    own = http is None
    http = http or Http(data_dir / "cache" / "rich-http.sqlite", offline=os.environ.get("HMAPS_RICH_OFFLINE") == "1")
    stats: dict[str, int] = {}
    t0 = time.time()
    try:
        sites = trip_sites(con, limit)
        trails = named_trails(con, limit)
        for t in trails:
            t["wikidata"] = t["extra"].get("wikidata")
        log.info("rich content: %d trip sites, %d named trails", len(sites), len(trails))
        if "wikipedia" in steps:
            stats["wikipedia_matched"] = step_wikipedia_match(con, http, sites, workers)
            if "trails" in steps:
                stats["trail_articles"] = step_trail_wikipedia(con, http, trails, workers)
            stats["wikipedia_texts"] = step_wikipedia_texts(con, http, sites, trails)
        if "wikidata" in steps or "wikivoyage" in steps or "commons" in steps:
            stats["wikidata_facts"] = step_wikidata_facts(con, http, sites, trails)
        if "commons" in steps or "trails" in steps:
            stats.update(step_commons(con, http, sites, trails, workers,
                                      do_sites="commons" in steps, do_trails="trails" in steps))
        if "trails" in steps:
            for t in trails:  # keep the article link found by search
                if t["extra"].get("wikipedia"):
                    con.execute("UPDATE trails SET extra = ? WHERE id = ?", (json.dumps(t["extra"], ensure_ascii=False), t["id"]))
            con.commit()
        if "wikivoyage" in steps:
            stats["wikivoyage_texts"] = step_wikivoyage(con, http, sites, workers)
        if "mapillary" in steps:
            token = mapillary_token(data_dir)
            if token:
                stats["mapillary"] = step_mapillary(con, http, token, sites, trails, workers)
            else:
                log.info("mapillary: skipped (no token – see docs/03-data-collection.md)")
        con.execute("INSERT OR REPLACE INTO meta VALUES ('rich_content', ?)",
                    (json.dumps({**stats, "at": time.strftime("%Y-%m-%d %H:%M")}, ensure_ascii=False),))
        con.commit()
    finally:
        log.info("rich content done in %.0fs – %d requests, %d from cache, %d failed",
                 time.time() - t0, http.requests, http.hits, http.errors)
        for host, why in http.blocked.items():
            log.warning("NOTE: %s was not reachable (%s) – its content is missing from this run", host, why)
        if own:
            http.close()
    return stats
