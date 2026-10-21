"""Satellite / aerial imagery layer with a persistent offline cache.

Sources (``HMAPS_SATELLITE_SOURCE``, a comma-separated list in order of preference; default
``govmap,eox``):

``govmap``       Survey of Israel orthophoto (מרכז מיפוי ישראל) from govmap's public tile CDN,
                 cache ``B0BZ1ORTO23`` (flown 2023, published 2024-01, ~0.13 m/px).
``govmap-2023``  the same CDN, cache ``M2023ORT3TS`` (2023-03).
``govmap-2022``  the same CDN, cache ``020522B0B20R`` (2022-05).
``eox``          Sentinel-2 cloudless 2024 by EOX (10 m/px, z ≤ 14; CC BY-NC-SA 4.0) – global
                 fallback, e.g. when govmap is blocked by a filter or retired.
``eox-2016``     Sentinel-2 cloudless 2016 by EOX (CC BY 4.0).
``https://…``    any XYZ template with ``{z}``/``{x}``/``{y}`` (legacy ``HMAPS_SAT_URL`` too).

govmap tiles are **not** WebMercator: the CDN serves an ArcGIS tile cache on the Israeli TM
Grid (ITM, EPSG:2039), ``<cache>/L<level>/R<row hex>/C<col hex>.jpg`` with its own origin and
12 resolutions.  ``GovmapItm`` therefore renders every WebMercator tile the map asks for
from the ITM tiles under it (``hmaps.core.itm`` + numpy bilinear resampling, Pillow for
JPEG).  The old code asked ``M2023ORT3TS/{z}/{y}/{x}.jpg`` – a key that never existed, so
the CDN (S3 behind CloudFront) answered 403 ``AccessDenied`` for every tile.

The browser always asks *our* server for ``/tiles/satellite/{z}/{x}/{y}.jpg``.  Every tile
fetched once is kept in an MBTiles file per source (``data/tiles/satellite.mbtiles`` for
govmap, ``satellite-<source>.mbtiles`` for the others), so an area that was viewed – or
downloaded with ``python -m hmaps.etl satellite`` / ``POST /api/satellite/download`` – keeps
working with no internet at all.

Lookup for a tile: for each source in order – its cache; else, unless that source is in an
"offline" back-off, fetch + store.  A source that fails (no network, blocked by a content
filter – NetFree answers with an HTML page, sometimes with status 200 or 418 – or a 5xx) is
not tried again for ``OFFLINE_BACKOFF`` seconds, and the next source is used.
"""

from __future__ import annotations

import argparse
import io
import logging
import math
import os
import sqlite3
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import OrderedDict
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Callable, Iterable, Iterator

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from ..core import geo

log = logging.getLogger("hmaps.satellite")

MIN_ZOOM, MAX_ZOOM = 7, 19
#: coverage handled by the layer (Israel); requests outside are answered with 204
BOUNDS = (34.15, 29.4, 35.95, 33.45)
FETCH_TIMEOUT = float(os.environ.get("HMAPS_SAT_TIMEOUT", "8"))
OFFLINE_BACKOFF = float(os.environ.get("HMAPS_SAT_BACKOFF", "30"))
#: used for size estimates until the cache has real tiles to average
AVG_TILE_BYTES = 25_000
#: tiles per second of a download job, for the time estimate (govmap measured at 4–6/s with 4 workers)
EST_TILES_PER_SEC = float(os.environ.get("HMAPS_SAT_RATE", "5"))
#: per job from the map for a free area (the CLI may raise it with --max-tiles)
MAX_JOB_TILES = int(os.environ.get("HMAPS_SAT_MAX_TILES", "30000"))
JOB_WORKERS = 4
DEFAULT_SOURCES = "govmap,eox"
USER_AGENT = "Mozilla/5.0 (hebrew-maps offline map; +https://github.com/rishumit/hebrew-maps)"

#: whole-country download levels: id -> (Hebrew name, max zoom)
LEVELS = {
    "basic": ("בסיסי", 13),
    "detailed": ("מפורט", 15),
    "full": ("מלא", 16),
}

#: coarse land outline of Israel incl. the Golan Heights, Judea & Samaria and Gaza (lon, lat),
#: drawn ~1–2 km outside the coast and borders; used to skip sea / neighbouring-country tiles
ISRAEL_POLYGON: tuple[tuple[float, float], ...] = (
    (35.10, 33.11), (35.07, 33.02), (35.05, 32.92), (34.99, 32.85), (34.94, 32.83), (34.93, 32.73),
    (34.90, 32.60), (34.86, 32.46), (34.83, 32.33), (34.78, 32.17), (34.73, 32.06), (34.68, 31.95),
    (34.60, 31.80), (34.50, 31.65), (34.40, 31.53), (34.27, 31.38), (34.19, 31.31), (34.23, 31.20),
    (34.33, 31.00), (34.38, 30.85), (34.48, 30.60), (34.56, 30.40), (34.68, 30.12), (34.80, 29.85),
    (34.86, 29.52), (34.88, 29.46), (34.95, 29.47), (34.99, 29.53), (35.06, 29.80), (35.13, 30.05),
    (35.17, 30.35), (35.23, 30.62), (35.31, 30.80), (35.42, 30.93), (35.46, 31.10), (35.50, 31.30), (35.55, 31.55),
    (35.58, 31.80), (35.58, 32.10), (35.60, 32.40), (35.61, 32.62), (35.70, 32.68), (35.80, 32.69),
    (35.87, 32.76), (35.92, 32.95), (35.89, 33.13), (35.87, 33.30), (35.86, 33.43), (35.78, 33.43), (35.70, 33.36),
    (35.62, 33.30), (35.55, 33.29), (35.53, 33.14), (35.42, 33.13), (35.25, 33.12), (35.12, 33.13),
)


class Offline(Exception):
    """The source could not be reached (no internet, DNS, timeout, 5xx)."""


class Blocked(Offline):
    """The answer was not a tile: a content filter (e.g. NetFree) or geo-block page."""


# ============================================================================= HTTP

def _ssl_context() -> ssl.SSLContext:
    # Filtering proxies such as NetFree re-sign HTTPS traffic with their own root certificate,
    # which Python's bundle does not know.  This server-side fetch only reads public imagery
    # and sends nothing, so it does not verify certificates.
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


_CTX = _ssl_context()


def _is_image(data: bytes) -> bool:
    return data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n"


def _looks_html(ctype: str, data: bytes) -> bool:
    head = data[:512].lstrip().lower()
    return "html" in ctype or head.startswith((b"<!doctype html", b"<html", b"<head", b"<body", b"<script"))


def http_get(url: str, timeout: float = FETCH_TIMEOUT, headers: dict | None = None) -> tuple[int, str, bytes]:
    """(status, content type, body) – also for HTTP errors; ``Offline`` when nothing answered."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r:
            return r.status, r.headers.get("Content-Type", "") or "", r.read()
    except urllib.error.HTTPError as e:
        try:
            body = e.read() or b""
        except Exception:  # noqa: BLE001
            body = b""
        return e.code, (e.headers.get("Content-Type", "") if e.headers else "") or "", body
    except (urllib.error.URLError, OSError, TimeoutError, ValueError) as e:
        raise Offline(str(getattr(e, "reason", e))) from e


def classify(status: int, ctype: str, body: bytes) -> str:
    """``image`` | ``missing`` (no such tile) | ``blocked`` (filter / block page) | ``error``."""
    if status == 200 and _is_image(body):
        return "image"
    if _looks_html(ctype.lower(), body):
        return "blocked"  # NetFree / geo-block pages are HTML, whatever the status (200, 403, 418)
    if status in (204, 404) or (status == 200 and not body):
        return "missing"
    if status == 403 and (b"AccessDenied" in body or b"NoSuchKey" in body):
        return "missing"  # S3 behind CloudFront: a key that does not exist answers 403 AccessDenied
    return "error"


def get_image(url: str, timeout: float = FETCH_TIMEOUT, headers: dict | None = None) -> bytes | None:
    """A tile image, ``None`` when the server has no such tile; raises ``Offline``/``Blocked``."""
    status, ctype, body = http_get(url, timeout, headers)
    kind = classify(status, ctype, body)
    if kind == "image":
        return body
    if kind == "missing":
        return None
    if kind == "blocked":
        raise Blocked(f"blocked: HTTP {status} {ctype or ''} page instead of an image (content filter?)".strip())
    raise Offline(f"HTTP {status}")


# ============================================================================= sources

class Source:
    """One imagery provider.  ``fetch(z, x, y)`` returns a WebMercator XYZ tile (JPEG/PNG bytes)."""

    id = "source"
    name_he = ""
    attribution = ""
    license = ""
    terms_url = ""
    min_zoom, max_zoom = MIN_ZOOM, MAX_ZOOM
    #: kept in data/tiles/satellite.mbtiles itself (else satellite-<id>.mbtiles)
    primary_cache = False
    #: a tile inside the coverage for ``--test`` (Tel Aviv, zoom 15)
    test_tile = (15, 19549, 13297)

    def fetch(self, z: int, x: int, y: int, timeout: float = FETCH_TIMEOUT) -> bytes | None:
        raise NotImplementedError

    def describe(self) -> dict:
        return {"id": self.id, "name_he": self.name_he, "attribution": self.attribution, "license": self.license,
                "terms_url": self.terms_url, "min_zoom": self.min_zoom, "max_zoom": self.max_zoom}

    def probe_urls(self) -> list[str]:
        """Raw URLs that ``--test`` fetches in addition to rendering ``test_tile``."""
        return []

    def probe_tile(self) -> tuple[int, int, int]:
        """``test_tile``, or the Tel Aviv tile at the deepest zoom the source has."""
        if self.test_tile[0] <= self.max_zoom:
            return self.test_tile
        z = self.max_zoom
        return (z, *geo.lonlat_to_tile(34.7818, 32.0853, z))


class FuncSource(Source):
    """Wraps a plain ``fetch(z, x, y)`` function (tests, embedding)."""

    primary_cache = True

    def __init__(self, fn: Callable[[int, int, int], bytes | None], id: str = "default"):
        self.fn = fn
        self.id = id

    def fetch(self, z, x, y, timeout=FETCH_TIMEOUT):
        return self.fn(z, x, y)


class XyzSource(Source):
    """A WebMercator XYZ tile server (``{z}``, ``{x}``, ``{y}``; ``{-y}`` for TMS rows)."""

    def __init__(self, id: str, url: str, *, name_he: str = "", attribution: str = "", license: str = "",
                 terms_url: str = "", min_zoom: int = 0, max_zoom: int = MAX_ZOOM, headers: dict | None = None):
        self.id, self.url = id, url
        self.name_he, self.attribution, self.license, self.terms_url = name_he or id, attribution, license, terms_url
        self.min_zoom, self.max_zoom = min_zoom, max_zoom
        self.headers = headers or {}

    def tile_url(self, z, x, y) -> str:
        return self.url.replace("{-y}", str((1 << z) - 1 - y)).format(z=z, x=x, y=y)

    def fetch(self, z, x, y, timeout=FETCH_TIMEOUT):
        return get_image(self.tile_url(z, x, y), timeout, self.headers)

    def probe_urls(self):
        return [self.tile_url(*self.probe_tile())]


class _Lru:
    """Small thread-safe LRU of raw ITM tiles (neighbouring WebMercator tiles share them)."""

    def __init__(self, size: int = 800):
        self.size = size
        self._d: OrderedDict = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            if key in self._d:
                self._d.move_to_end(key)
                return True, self._d[key]
        return False, None

    def put(self, key, value) -> None:
        with self._lock:
            self._d[key] = value
            self._d.move_to_end(key)
            while len(self._d) > self.size:
                self._d.popitem(last=False)


class GovmapItm(Source):
    """govmap.gov.il orthophoto: ArcGIS tile cache on the Israeli TM Grid, rendered to WebMercator.

    Tile grid (govmap's own client configuration, verified against the CDN): origin
    (-5403700, 7116700) top-left in EPSG:2039 metres, 256 px tiles, rows counted south,
    levels L00–L11 at 1:3,000,000 … 1:500 (96 dpi).  Path ``L<2 digits>/R<8 hex>/C<8 hex>``,
    hex in lower case.  A tile that does not exist answers 403 ``AccessDenied`` (S3).
    """

    HOST = os.environ.get("HMAPS_GOVMAP_CDN", "https://cdn.govmap.gov.il")
    ORIGIN = (-5403700.0, 7116700.0)
    RESOLUTIONS = (793.751587503175, 264.583862501058, 132.291931250529, 66.1459656252646, 26.4583862501058,
                   13.2291931250529, 6.61459656252646, 2.64583862501058, 1.32291931250529, 0.661459656252646,
                   0.330729828126323, 0.13229193125052918)
    primary_cache = True
    license = "© מרכז מיפוי ישראל (המרכז למיפוי ישראל) – כל הזכויות שמורות; שימוש אישי בלבד לפי תנאי govmap"
    terms_url = "https://www.govmap.gov.il/"
    max_zoom = 19

    def __init__(self, id: str, cache: str, label: str):
        self.id, self.cache = id, cache
        self.name_he = f"תצלומי אוויר govmap ({label})"
        self.attribution = f"תצלומי אוויר © מרכז מיפוי ישראל (govmap, {label})"
        self._raw = _Lru()
        if id != "govmap":
            self.primary_cache = False

    def raw_url(self, level: int, row: int, col: int) -> str:
        return f"{self.HOST}/{self.cache}/L{level:02d}/R{row:08x}/C{col:08x}.jpg"

    def probe_urls(self):
        e, n = 178_830.0, 665_030.0  # Dizengoff Square, Tel Aviv (ITM)
        level = 8
        t = 256 * self.RESOLUTIONS[level]
        return [self.raw_url(level, int((self.ORIGIN[1] - n) // t), int((e - self.ORIGIN[0]) // t))]

    def _raw_tile(self, level, row, col, timeout):
        key = (level, row, col)
        hit, data = self._raw.get(key)
        if hit:
            return data
        data = get_image(self.raw_url(level, row, col), timeout)  # Offline / Blocked propagate
        self._raw.put(key, data)
        return data

    def level_for(self, mpp: float) -> int:
        """The coarsest ITM level that is at least as detailed as *mpp* (20 % upsampling allowed)."""
        best = len(self.RESOLUTIONS) - 1
        for i, r in enumerate(self.RESOLUTIONS):
            if r <= mpp * 1.2:
                return i
        return best

    def fetch(self, z, x, y, timeout=FETCH_TIMEOUT):
        try:
            import numpy as np
            import PIL.Image  # noqa: F401 – decoding / encoding below

            from ..core import itm
        except ImportError as e:  # pragma: no cover – an install without Pillow: use the next source
            raise Offline(f"govmap needs numpy + Pillow ({e})") from e

        # WebMercator pixel centres of the requested tile -> lon/lat -> ITM metres
        n = 1 << z
        px = (np.arange(256) + 0.5) / 256.0
        lon = (x + px) / n * 360.0 - 180.0
        lat = np.degrees(np.arctan(np.sinh(math.pi * (1 - 2 * (y + px) / n))))
        lon_g, lat_g = np.meshgrid(lon, lat)
        east, north = itm.from_wgs84(lon_g, lat_g)
        mpp = float(np.hypot(east[128, -1] - east[128, 0], north[128, -1] - north[128, 0])) / 255.0
        level = self.level_for(mpp)
        res = self.RESOLUTIONS[level]
        u = (east - self.ORIGIN[0]) / res - 0.5   # source pixel column (pixel centres on integers)
        v = (self.ORIGIN[1] - north) / res - 0.5  # source pixel row
        c0, c1 = int(math.floor(u.min() / 256)), int(math.floor((u.max() + 1) / 256))
        r0, r1 = int(math.floor(v.min() / 256)), int(math.floor((v.max() + 1) / 256))
        if (c1 - c0 + 1) * (r1 - r0 + 1) > 36:  # never happens with the level choice above
            raise ValueError("too many source tiles")
        mosaic = np.zeros(((r1 - r0 + 1) * 256, (c1 - c0 + 1) * 256, 3), dtype=np.uint8)
        valid = np.zeros(mosaic.shape[:2], dtype=bool)
        found = False
        keys = [(row, col) for row in range(r0, r1 + 1) for col in range(c0, c1 + 1)]
        # the source tiles of one map tile are fetched in parallel (shared pool, Offline propagates)
        raws = list(_raw_pool().map(lambda rc: self._raw_tile(level, rc[0], rc[1], timeout), keys))
        for (row, col), data in zip(keys, raws):
            img = _decode(data) if data else None
            if img is None:
                continue
            oy, ox = (row - r0) * 256, (col - c0) * 256
            h, w = img.shape[:2]
            mosaic[oy:oy + h, ox:ox + w] = img[:256, :256]
            valid[oy:oy + h, ox:ox + w] = True
            found = True
        if not found:
            return None
        u -= c0 * 256
        v -= r0 * 256
        # much finer source than needed (levels step ×2–×3): box-filter first, against aliasing
        f = int(mpp / res + 1e-9)
        if f >= 2:
            hh, ww = (mosaic.shape[0] // f) * f, (mosaic.shape[1] // f) * f
            mosaic = mosaic[:hh, :ww].reshape(hh // f, f, ww // f, f, 3).mean(axis=(1, 3)).astype(np.uint8)
            valid = valid[:hh, :ww].reshape(hh // f, f, ww // f, f).all(axis=(1, 3))
            u = (u + 0.5) / f - 0.5
            v = (v + 0.5) / f - 0.5
        out, ok = _bilinear(mosaic, valid, u, v)
        if not ok.any():
            return None
        return _encode(out, None if ok.all() else ok)


_POOL: ThreadPoolExecutor | None = None
_POOL_LOCK = threading.Lock()


def _raw_pool() -> ThreadPoolExecutor:
    global _POOL
    with _POOL_LOCK:
        if _POOL is None:
            _POOL = ThreadPoolExecutor(8, thread_name_prefix="govmap-raw")
        return _POOL


def _decode(data: bytes):
    import numpy as np
    from PIL import Image

    try:
        with Image.open(io.BytesIO(data)) as im:
            return np.asarray(im.convert("RGB"))
    except Exception:  # noqa: BLE001 – a corrupt tile is treated as missing
        return None


def _bilinear(img, valid, u, v):
    import numpy as np

    h, w = img.shape[:2]
    u = np.clip(u, 0, w - 1)
    v = np.clip(v, 0, h - 1)
    x0 = np.minimum(np.floor(u).astype(np.int64), w - 2 if w > 1 else 0)
    y0 = np.minimum(np.floor(v).astype(np.int64), h - 2 if h > 1 else 0)
    x1, y1 = np.minimum(x0 + 1, w - 1), np.minimum(y0 + 1, h - 1)
    fx = (u - x0)[..., None]
    fy = (v - y0)[..., None]
    im = img.astype(np.float32)
    top = im[y0, x0] * (1 - fx) + im[y0, x1] * fx
    bot = im[y1, x0] * (1 - fx) + im[y1, x1] * fx
    out = np.clip(top * (1 - fy) + bot * fy + 0.5, 0, 255).astype(np.uint8)
    ok = valid[np.rint(v).astype(np.int64), np.rint(u).astype(np.int64)]
    return out, ok


def _encode(rgb, alpha_mask=None) -> bytes:
    """JPEG (q 85) for a full tile; PNG with transparency where the source has no photo."""
    import numpy as np
    from PIL import Image

    buf = io.BytesIO()
    if alpha_mask is None:
        Image.fromarray(rgb, "RGB").save(buf, "JPEG", quality=85, optimize=True)
    else:
        rgba = np.dstack([rgb, np.where(alpha_mask, 255, 0).astype(np.uint8)])
        Image.fromarray(rgba, "RGBA").save(buf, "PNG", optimize=True)
    return buf.getvalue()


EOX_TERMS = "https://cloudless.eox.at/documentation/license"


def make_source(spec: str) -> Source:
    """A source from its id (see the module docstring) or an XYZ URL template."""
    s = spec.strip()
    if s.startswith(("http://", "https://")):
        return XyzSource("custom", s, name_he="מקור מותאם", attribution="", license="לפי תנאי המקור")
    if s == "govmap":
        return GovmapItm("govmap", "B0BZ1ORTO23", "2023")
    if s == "govmap-2023":
        return GovmapItm("govmap-2023", "M2023ORT3TS", "2023-03")
    if s == "govmap-2022":
        return GovmapItm("govmap-2022", "020522B0B20R", "2022")
    if s == "eox":
        return XyzSource(
            "eox", "https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2024_3857/default/g/{z}/{y}/{x}.jpg",
            name_he="תצלומי לוויין Sentinel-2 ‏(EOX, ‏2024, ‏10 מ')",
            attribution="Sentinel-2 cloudless 2024 © EOX IT Services GmbH (Contains modified Copernicus Sentinel data 2024)",
            license="CC BY-NC-SA 4.0 (שימוש לא מסחרי)", terms_url=EOX_TERMS, min_zoom=0, max_zoom=14)
    if s == "eox-2016":
        return XyzSource(
            "eox-2016", "https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless_3857/default/g/{z}/{y}/{x}.jpg",
            name_he="תצלומי לוויין Sentinel-2 ‏(EOX, ‏2016, ‏10 מ')",
            attribution="Sentinel-2 cloudless 2016 © EOX IT Services GmbH (Contains modified Copernicus Sentinel data 2016)",
            license="CC BY 4.0", terms_url=EOX_TERMS, min_zoom=0, max_zoom=14)
    raise ValueError(f"unknown satellite source {spec!r} (govmap, govmap-2023, govmap-2022, eox, eox-2016 or an https:// XYZ URL)")


SOURCE_IDS = ("govmap", "govmap-2023", "govmap-2022", "eox", "eox-2016")


def configured_sources(spec: str | None = None) -> list[Source]:
    """``HMAPS_SATELLITE_SOURCE`` (default ``govmap,eox``); a legacy ``HMAPS_SAT_URL`` comes first."""
    out: list[Source] = []
    if spec is None:
        spec = os.environ.get("HMAPS_SATELLITE_SOURCE", "").strip()
        legacy = os.environ.get("HMAPS_SAT_URL", "").strip()
        if legacy and not spec:
            out.append(make_source(legacy))
        spec = spec or DEFAULT_SOURCES
    for part in spec.split(","):
        if part.strip():
            src = make_source(part)
            if all(o.id != src.id for o in out):
                out.append(src)
    return out


# ============================================================================= MBTiles cache

class TileCache:
    """Thread-safe MBTiles store (XYZ in the API, TMS rows on disk as the spec says)."""

    def __init__(self, path: Path, name: str = "aerial imagery", attribution: str = "", description: str = ""):
        self.path = Path(path)
        self.meta = {"name": name, "attribution": attribution, "description": description}
        self._local = threading.local()
        self._wlock = threading.Lock()
        self._wcon: sqlite3.Connection | None = None

    def _ensure(self) -> sqlite3.Connection:
        """The single writer connection (created with the schema on first write)."""
        if self._wcon is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            con = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA synchronous=NORMAL")
            con.execute("CREATE TABLE IF NOT EXISTS metadata (name TEXT PRIMARY KEY, value TEXT)")
            con.execute("CREATE TABLE IF NOT EXISTS tiles (zoom_level INTEGER, tile_column INTEGER, "
                        "tile_row INTEGER, tile_data BLOB)")
            con.execute("CREATE UNIQUE INDEX IF NOT EXISTS tile_index ON tiles (zoom_level, tile_column, tile_row)")
            meta = {"format": "jpg", "type": "baselayer", "version": "1", "minzoom": str(MIN_ZOOM),
                    "maxzoom": str(MAX_ZOOM), "bounds": ",".join(map(str, BOUNDS)), **self.meta}
            con.executemany("INSERT OR IGNORE INTO metadata VALUES (?, ?)", meta.items())
            con.commit()
            self._wcon = con
        return self._wcon

    def _reader(self) -> sqlite3.Connection | None:
        con = getattr(self._local, "con", None)
        if con is None:
            if not self.path.exists():
                return None
            con = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
            self._local.con = con
        return con

    def get(self, z: int, x: int, y: int) -> bytes | None:
        con = self._reader()
        if con is None:
            return None
        try:
            row = con.execute("SELECT tile_data FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?",
                              (z, x, (1 << z) - 1 - y)).fetchone()
        except sqlite3.OperationalError:  # file exists but the schema is still being created
            return None
        return row[0] if row else None

    def put(self, z: int, x: int, y: int, data: bytes) -> None:
        with self._wlock:
            con = self._ensure()
            con.execute("INSERT OR REPLACE INTO tiles VALUES (?, ?, ?, ?)", (z, x, (1 << z) - 1 - y, data))
            con.commit()

    def existing(self, z: int, x0: int, x1: int, y0: int, y1: int) -> set[tuple[int, int]]:
        """(x, y) of the cached tiles of zoom ``z`` inside an XYZ range (resume support)."""
        con = self._reader()
        if con is None:
            return set()
        n = 1 << z
        try:
            rows = con.execute("SELECT tile_column, tile_row FROM tiles WHERE zoom_level=? AND tile_column BETWEEN ? AND ? "
                               "AND tile_row BETWEEN ? AND ?", (z, x0, x1, n - 1 - y1, n - 1 - y0)).fetchall()
        except sqlite3.OperationalError:
            return set()
        return {(c, n - 1 - r) for c, r in rows}

    def stats(self) -> dict:
        con = self._reader()
        count, size, by_zoom = 0, 0, {}
        if con is not None:
            try:
                for z, n, s in con.execute("SELECT zoom_level, COUNT(*), SUM(LENGTH(tile_data)) FROM tiles GROUP BY zoom_level"):
                    by_zoom[str(z)] = n
                    count += n
                    size += s or 0
            except sqlite3.OperationalError:
                pass
        disk = 0
        for suffix in ("", "-wal"):
            p = Path(str(self.path) + suffix)
            if p.exists():
                disk += p.stat().st_size
        return {"tiles": count, "bytes": size, "disk_bytes": disk, "by_zoom": by_zoom}

    def close(self) -> None:
        with self._wlock:
            if self._wcon is not None:
                self._wcon.close()
                self._wcon = None
        con = getattr(self._local, "con", None)
        if con is not None:
            con.close()
            self._local.con = None


# ============================================================================= areas / tile enumeration

def parse_bbox(value) -> tuple[float, float, float, float]:
    """``"minlon,minlat,maxlon,maxlat"`` or a 4-item list -> a validated bbox."""
    parts = value.split(",") if isinstance(value, str) else list(value)
    if len(parts) != 4:
        raise ValueError("bbox needs 4 numbers: minlon,minlat,maxlon,maxlat")
    w, s, e, n = (float(p) for p in parts)
    if not all(math.isfinite(v) for v in (w, s, e, n)):
        raise ValueError("bbox is not finite")
    w, e = min(w, e), max(w, e)
    s, n = min(s, n), max(s, n)
    return w, s, e, n


def clip_bbox(bbox: tuple[float, float, float, float]) -> tuple[float, float, float, float] | None:
    """The part of ``bbox`` inside the coverage (``None`` if outside)."""
    w, s, e, n = max(bbox[0], BOUNDS[0]), max(bbox[1], BOUNDS[1]), min(bbox[2], BOUNDS[2]), min(bbox[3], BOUNDS[3])
    return (w, s, e, n) if w < e and s < n else None


def tile_range(bbox: tuple[float, float, float, float], z: int) -> tuple[int, int, int, int]:
    """Inclusive XYZ ranges (x0, x1, y0, y1) covering ``bbox`` at zoom ``z``."""
    x0, y0 = geo.lonlat_to_tile(bbox[0], bbox[3], z)  # north-west corner
    x1, y1 = geo.lonlat_to_tile(bbox[2], bbox[1], z)  # south-east corner
    return x0, x1, y0, y1


def _zooms(minzoom: int, maxzoom: int) -> range:
    lo, hi = max(MIN_ZOOM, min(minzoom, maxzoom)), min(MAX_ZOOM, max(minzoom, maxzoom))
    return range(lo, hi + 1)


def _merge(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[list[int]] = []
    for a, b in sorted(ranges):
        if out and a <= out[-1][1] + 1:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


class Area:
    """A bbox, optionally intersected with a polygon: per zoom, the tile columns of every row."""

    def __init__(self, bbox, polygon: Iterable[tuple[float, float]] | None = None, name: str = ""):
        self.polygon = tuple(polygon) if polygon else None
        if self.polygon:
            pb = geo.bbox_of(self.polygon)
            bbox = (max(bbox[0], pb[0]), max(bbox[1], pb[1]), min(bbox[2], pb[2]), min(bbox[3], pb[3]))
        self.bbox = tuple(bbox)
        self.clipped = clip_bbox(self.bbox) if bbox[0] < bbox[2] and bbox[1] < bbox[3] else None
        self.name = name

    def _poly_spans(self, lat_s: float, lat_n: float) -> list[tuple[float, float]]:
        """Longitude intervals of the polygon inside the latitude band [lat_s, lat_n] (conservative)."""
        pts = self.polygon
        spans: list[tuple[float, float]] = []
        for lat in (lat_s, (lat_s + lat_n) / 2, lat_n):
            xs = []
            for i in range(len(pts)):
                (x1, y1), (x2, y2) = pts[i], pts[(i + 1) % len(pts)]
                if (y1 <= lat < y2) or (y2 <= lat < y1):
                    xs.append(x1 + (lat - y1) * (x2 - x1) / (y2 - y1))
            xs.sort()
            spans += [(xs[i], xs[i + 1]) for i in range(0, len(xs) - 1, 2)]
        spans += [(x, x) for x, y in pts if lat_s <= y <= lat_n]
        return spans

    def rows(self, z: int) -> Iterator[tuple[int, list[tuple[int, int]]]]:
        """(y, [(x0, x1), …]) for every row of zoom ``z`` that has tiles, north to south."""
        if self.clipped is None:
            return
        x0, x1, y0, y1 = tile_range(self.clipped, z)
        n = 1 << z
        for y in range(y0, y1 + 1):
            if not self.polygon:
                yield y, [(x0, x1)]
                continue
            _, s, _, nn = geo.tile_bounds(z, 0, y)
            rng = []
            for a, b in self._poly_spans(s, nn):
                ta = int(math.floor((a + 180.0) / 360.0 * n)) - 1  # one tile of slack each side
                tb = int(math.floor((b + 180.0) / 360.0 * n)) + 1
                ta, tb = max(ta, x0), min(tb, x1)
                if ta <= tb:
                    rng.append((ta, tb))
            if rng:
                yield y, _merge(rng)

    def count(self, z: int) -> int:
        return sum(b - a + 1 for _, rs in self.rows(z) for a, b in rs)

    def tiles(self, z: int) -> Iterator[tuple[int, int]]:
        for y, rs in self.rows(z):
            for a, b in rs:
                for x in range(a, b + 1):
                    yield x, y


def country_area() -> Area:
    return Area(BOUNDS, ISRAEL_POLYGON, name="israel")


def _area(a) -> Area:
    return a if isinstance(a, Area) else Area(tuple(a))


def count_tiles(area, minzoom: int, maxzoom: int) -> int:
    area = _area(area)
    return sum(area.count(z) for z in _zooms(minzoom, maxzoom))


def iter_tiles(area, minzoom: int, maxzoom: int) -> Iterator[tuple[int, int, int]]:
    """All (z, x, y) of the area, coarse zooms first (the overview is usable soonest)."""
    area = _area(area)
    for z in _zooms(minzoom, maxzoom):
        for x, y in area.tiles(z):
            yield z, x, y


# ============================================================================= service

class Layer:
    """A source with its own cache file and offline back-off."""

    def __init__(self, source: Source, cache: TileCache, backoff: float):
        self.source, self.cache, self.backoff = source, cache, backoff
        self._offline_until = 0.0
        self.last_error: str | None = None

    @property
    def offline(self) -> bool:
        return time.monotonic() < self._offline_until

    def mark_offline(self, why: str) -> None:
        if not self.offline:
            log.info("satellite source %s unreachable (%s) – not tried for %ds", self.source.id, why, self.backoff)
        self.last_error = why
        self._offline_until = time.monotonic() + self.backoff

    def mark_online(self) -> None:
        self._offline_until = 0.0
        self.last_error = None


def cache_path(base: Path, source: Source) -> Path:
    base = Path(base)
    return base if source.primary_cache else base.with_name(f"{base.stem}-{source.id}{base.suffix}")


class Satellite:
    """Caches + sources + offline back-off + one background download job at a time."""

    def __init__(self, path: Path, fetch: Callable[[int, int, int], bytes | None] | None = None,
                 backoff: float = OFFLINE_BACKOFF, sources: list[Source] | None = None):
        if fetch is not None:
            sources = [FuncSource(fetch)]
        sources = sources or configured_sources()
        self.base_path = Path(path)
        self.layers = [Layer(s, TileCache(cache_path(path, s), s.name_he or s.id, s.attribution,
                                          getattr(s, "url", getattr(s, "cache", ""))), backoff) for s in sources]
        self._lock = threading.Lock()
        self.job: Job | None = None

    # -- compatibility helpers (single-source users and tests)
    @property
    def cache(self) -> TileCache:
        return self.layers[0].cache

    @property
    def offline(self) -> bool:
        return all(layer.offline for layer in self.layers)

    def layer(self, source_id: str | None) -> Layer:
        if not source_id:
            return self.layers[0]
        for layer in self.layers:
            if layer.source.id == source_id:
                return layer
        raise KeyError(source_id)

    def close(self) -> None:
        for layer in self.layers:
            layer.cache.close()

    # -- tiles
    def _fetch_store(self, layer: Layer, z: int, x: int, y: int) -> bytes | None:
        data = layer.source.fetch(z, x, y)
        layer.mark_online()
        if data:
            try:
                layer.cache.put(z, x, y, data)
            except sqlite3.Error:  # disk full / read-only: still show the tile
                log.exception("could not cache satellite tile %s/%s/%s", z, x, y)
        return data

    def _overzoom(self, layer: Layer, z: int, x: int, y: int) -> tuple[bytes | None, str]:
        """Above a source's deepest zoom: cut the tile out of its ancestor (not stored)."""
        mz = layer.source.max_zoom
        d = z - mz
        px, py = x >> d, y >> d
        data = layer.cache.get(mz, px, py)
        how = "cache"
        if not data and not layer.offline:
            try:
                data = self._fetch_store(layer, mz, px, py)
                how = "cdn"
            except Offline as e:
                layer.mark_offline(str(e))
        if not data:
            return None, "missing"
        try:
            from PIL import Image
            k = 1 << d
            size = 256 // k
            with Image.open(io.BytesIO(data)) as im:
                ox, oy = (x - (px << d)) * size, (y - (py << d)) * size
                part = im.convert("RGB").crop((ox, oy, ox + max(1, size), oy + max(1, size)))
                buf = io.BytesIO()
                part.resize((256, 256), Image.BILINEAR).save(buf, "JPEG", quality=85)
                return buf.getvalue(), how
        except Exception:  # noqa: BLE001
            return None, "missing"

    def tile(self, z: int, x: int, y: int) -> tuple[bytes | None, str]:
        """(data, how): how is ``cache`` | ``cdn`` | ``missing`` | ``offline`` | ``outside``."""
        data, how, _ = self.tile_info(z, x, y)
        return data, how

    def tile_info(self, z: int, x: int, y: int) -> tuple[bytes | None, str, str | None]:
        """Like ``tile`` plus the id of the source that answered."""
        if not MIN_ZOOM <= z <= MAX_ZOOM:
            return None, "outside", None
        w, s, e, n = geo.tile_bounds(z, x, y)
        if e < BOUNDS[0] or w > BOUNDS[2] or n < BOUNDS[1] or s > BOUNDS[3]:
            return None, "outside", None
        saw_offline = False
        for layer in self.layers:
            src = layer.source
            if z < src.min_zoom:
                continue
            if z > src.max_zoom:
                data, how = self._overzoom(layer, z, x, y)
                if data:
                    return data, how, src.id
                continue
            data = layer.cache.get(z, x, y)
            if data:
                return data, "cache", src.id
            if layer.offline:
                saw_offline = True
                continue
            try:
                data = self._fetch_store(layer, z, x, y)
            except Offline as e:
                layer.mark_offline(str(e))
                saw_offline = True
                continue
            if data:
                return data, "cdn", src.id
        # "offline": the browser asks again in 30 s; "missing": nothing to come (cached a day)
        return None, ("offline" if saw_offline else "missing"), None

    # -- estimates & status
    def avg_tile_bytes(self, st: dict | None = None, layer: Layer | None = None) -> int:
        st = st or (layer or self.layers[0]).cache.stats()
        return int(st["bytes"] / st["tiles"]) if st["tiles"] >= 20 else AVG_TILE_BYTES

    def estimate(self, area, minzoom: int, maxzoom: int, source: str | None = None) -> dict:
        area = _area(area)
        layer = self.layer(source)
        total = count_tiles(area, minzoom, maxzoom)
        cached = 0
        if area.clipped is not None and total <= 4 * MAX_JOB_TILES:
            for z in _zooms(minzoom, maxzoom):
                for y, rs in area.rows(z):
                    for a, b in rs:
                        cached += len(layer.cache.existing(z, a, b, y, y))
        elif area.clipped is not None:  # big areas: the cache only ever holds tiles of Israel
            by_zoom = layer.cache.stats()["by_zoom"]
            cached = sum(min(by_zoom.get(str(z), 0), area.count(z)) for z in _zooms(minzoom, maxzoom))
        missing = max(0, total - cached)
        return {"tiles": total, "cached": cached, "missing": missing, "source": layer.source.id,
                "bytes_estimate": missing * self.avg_tile_bytes(layer=layer), "max_tiles": MAX_JOB_TILES,
                "seconds_estimate": int(missing / EST_TILES_PER_SEC), "too_many": total > MAX_JOB_TILES}

    def country_levels(self, source: str | None = None) -> list[dict]:
        area = country_area()
        out = []
        for lid, (name, mz) in LEVELS.items():
            e = self.estimate(area, MIN_ZOOM, mz, source)
            e.update(id=lid, name_he=name, minzoom=MIN_ZOOM, maxzoom=mz, too_many=False)
            out.append(e)
        return out

    def status(self) -> dict:
        st = self.cache.stats()
        layers = []
        for layer in self.layers:
            d = layer.source.describe()
            d.update(offline=layer.offline, error=layer.last_error,
                     cache=st if layer is self.layers[0] else layer.cache.stats(), file=layer.cache.path.name)
            layers.append(d)
        return {"cache": st, "offline": self.offline, "source": self.layers[0].source.id, "sources": layers,
                "attribution": " · ".join(s["attribution"] for s in layers if s["attribution"]),
                "min_zoom": MIN_ZOOM, "max_zoom": MAX_ZOOM, "bounds": list(BOUNDS), "max_job_tiles": MAX_JOB_TILES,
                "levels": {k: {"name_he": v[0], "maxzoom": v[1]} for k, v in LEVELS.items()},
                "job": self.job.progress() if self.job else None}

    # -- download jobs
    def start_job(self, area, minzoom: int, maxzoom: int, max_tiles: int = MAX_JOB_TILES,
                  workers: int = JOB_WORKERS, source: str | None = None) -> "Job":
        with self._lock:
            if self.job is not None and self.job.state in ("pending", "running"):
                raise RuntimeError("busy")
            job = Job(self, area, minzoom, maxzoom, max_tiles=max_tiles, workers=workers, source=source)
            self.job = job
        threading.Thread(target=job.run, name="satellite-download", daemon=True).start()
        return job


class Job:
    """Pre-download all tiles of an area and zoom range into one source's cache (skips cached ones).

    Without an explicit ``source`` the first source that answers a test tile is used, so a
    download still works when govmap is blocked (it then fills the fallback's cache).
    """

    #: this many network failures in a row ends the job (the PC is offline)
    MAX_CONSECUTIVE_FAILURES = 8

    def __init__(self, sat: Satellite, area, minzoom: int, maxzoom: int, max_tiles: int = MAX_JOB_TILES,
                 workers: int = JOB_WORKERS, source: str | None = None):
        self.sat = sat
        self.area = _area(area)
        self.bbox = self.area.bbox
        self.minzoom, self.maxzoom = minzoom, maxzoom
        self.workers = max(1, min(8, workers))
        self.source = source
        self.layer: Layer | None = sat.layer(source) if source else None
        self.total = count_tiles(self.area, minzoom, maxzoom)
        if self.total > max_tiles:
            raise ValueError(f"too many tiles: {self.total} > {max_tiles}")
        self.done = self.fetched = self.skipped = self.missing = self.failed = 0
        self.bytes = 0
        self.state = "pending"
        self.error: str | None = None
        self.started = time.time()
        self.finished: float | None = None
        self._cancel = threading.Event()
        self._lock = threading.Lock()
        self._fails_in_row = 0
        self._last_failure = ""

    def cancel(self) -> None:
        self._cancel.set()

    def progress(self) -> dict:
        with self._lock:
            pct = round(100.0 * self.done / self.total, 1) if self.total else 100.0
            elapsed = (self.finished or time.time()) - self.started
            rate = self.fetched / elapsed if elapsed > 5 and self.fetched else 0.0
            left = self.total - self.done
            return {"state": self.state, "total": self.total, "done": self.done, "percent": pct,
                    "fetched": self.fetched, "skipped": self.skipped, "missing": self.missing, "failed": self.failed,
                    "bytes": self.bytes, "error": self.error, "bbox": list(self.bbox), "area": self.area.name or "bbox",
                    "minzoom": self.minzoom, "maxzoom": self.maxzoom,
                    "source": self.layer.source.id if self.layer else self.source,
                    "rate": round(rate, 1), "eta_seconds": int(left / rate) if rate else None,
                    "started": self.started, "finished": self.finished}

    def _pick_layer(self) -> Layer:
        """The explicit source, else the first one whose test tile downloads."""
        if self.layer is not None:
            return self.layer
        if len(self.sat.layers) == 1:
            return self.sat.layers[0]
        errors = []
        for layer in self.sat.layers:
            try:
                if layer.source.fetch(*layer.source.probe_tile()):
                    layer.mark_online()
                    return layer
                errors.append(f"{layer.source.id}: no test tile")
            except Offline as e:
                layer.mark_offline(str(e))
                errors.append(f"{layer.source.id}: {e}")
        raise Offline("; ".join(errors) or "no source")

    def _one(self, z: int, x: int, y: int) -> None:
        if self._cancel.is_set():
            return
        layer = self.layer
        try:
            data = layer.source.fetch(z, x, y) if z <= layer.source.max_zoom else None
        except Offline as e:
            with self._lock:
                self.failed += 1
                self.done += 1
                self._last_failure = str(e)
                self._fails_in_row += 1
                if self._fails_in_row >= self.MAX_CONSECUTIVE_FAILURES:
                    self.error = f"offline: {e}"
                    self._cancel.set()
            return
        except Exception as e:  # noqa: BLE001 – one bad tile must not end a country-wide download
            log.warning("satellite tile %s/%s/%s failed: %s", z, x, y, e)
            data = None
        if data:
            layer.cache.put(z, x, y, data)
        with self._lock:
            self._fails_in_row = 0
            self.done += 1
            if data:
                self.fetched += 1
                self.bytes += len(data)
            else:
                self.missing += 1

    def _todo(self) -> Iterator[tuple[int, int, int]]:
        cache = self.layer.cache
        band = 32
        for z in _zooms(self.minzoom, self.maxzoom):
            rows = list(self.area.rows(z))
            for i in range(0, len(rows), band):
                chunk = rows[i:i + band]
                xs = [a for _, rs in chunk for a, _ in rs] + [b for _, rs in chunk for _, b in rs]
                have = cache.existing(z, min(xs), max(xs), chunk[0][0], chunk[-1][0])
                for y, rs in chunk:
                    for a, b in rs:
                        for x in range(a, b + 1):
                            if self._cancel.is_set():
                                return
                            if (x, y) in have:
                                with self._lock:
                                    self.skipped += 1
                                    self.done += 1
                            else:
                                yield z, x, y

    def run(self) -> None:
        self.state = "running"
        try:
            self.layer = self._pick_layer()
            with ThreadPoolExecutor(self.workers, thread_name_prefix="sat-dl") as pool:
                pending = set()
                for t in self._todo():
                    if self._cancel.is_set():
                        break
                    pending.add(pool.submit(self._one, *t))
                    if len(pending) >= self.workers * 4:  # bounded queue: polite and memory-flat
                        done, pending = wait(pending, return_when=FIRST_COMPLETED)
                        for f in done:
                            f.result()
                for f in pending:
                    f.result()
            if not self.error and self.failed and not self.fetched and not self.missing:
                self.error = f"offline: {self._last_failure}"  # every tile failed
            if self.error:
                self.state = "error"
            elif self._cancel.is_set():
                self.state = "cancelled"
            else:
                self.state = "done"
        except Offline as e:
            self.error, self.state = f"offline: {e}", "error"
        except Exception as e:  # noqa: BLE001
            log.exception("satellite download failed")
            self.error, self.state = str(e), "error"
        finally:
            self.finished = time.time()


# ============================================================================= diagnose

def probe_url(url: str, timeout: float = 15) -> dict:
    """status / content type / size / verdict of one raw request (for ``--test``)."""
    t0 = time.monotonic()
    try:
        status, ctype, body = http_get(url, timeout)
    except Offline as e:
        return {"url": url, "status": None, "content_type": "", "bytes": 0, "ms": int((time.monotonic() - t0) * 1000),
                "kind": "network", "ok": False, "message": f"אין חיבור / network error: {e}"}
    kind = classify(status, ctype, body)
    msg = {"image": "תקין – התקבלה תמונה / OK image",
           "missing": "השרת ענה שאין אריח כזה / no such tile",
           "blocked": "התקבל דף HTML במקום תמונה – כנראה חסימה של הסינון (נטפרי) או חסימה גאוגרפית / blocked (filter page)",
           "error": f"שגיאת שרת HTTP {status} / server error"}[kind]
    snippet = "" if kind == "image" else body[:160].decode("utf-8", "replace").replace("\n", " ")
    return {"url": url, "status": status, "content_type": ctype, "bytes": len(body),
            "ms": int((time.monotonic() - t0) * 1000), "kind": kind, "ok": kind == "image", "message": msg,
            "snippet": snippet}


def diagnose(sources: list[Source], timeout: float = 15) -> list[dict]:
    out = []
    for src in sources:
        checks = [probe_url(u, timeout) for u in src.probe_urls()]
        z, x, y = src.probe_tile()
        t0 = time.monotonic()
        try:
            data = src.fetch(z, x, y, timeout)
            render = {"tile": f"{z}/{x}/{y}", "ok": bool(data), "bytes": len(data or b""),
                      "format": ("png" if data and data[:4] == b"\x89PNG" else "jpeg") if data else None,
                      "message": "תקין / OK" if data else "אין אריח / no tile", "ms": int((time.monotonic() - t0) * 1000)}
        except Offline as e:
            render = {"tile": f"{z}/{x}/{y}", "ok": False, "bytes": 0, "format": None, "message": str(e),
                      "ms": int((time.monotonic() - t0) * 1000)}
        except Exception as e:  # noqa: BLE001
            render = {"tile": f"{z}/{x}/{y}", "ok": False, "bytes": 0, "format": None, "message": f"error: {e}",
                      "ms": int((time.monotonic() - t0) * 1000)}
        out.append({"source": src.id, "name_he": src.name_he, "license": src.license, "checks": checks,
                    "render": render, "ok": render["ok"]})
    return out


# ============================================================================= HTTP API

_SAT: Satellite | None = None


def service() -> Satellite:
    global _SAT
    if _SAT is None:
        from .settings import settings
        _SAT = Satellite(settings.satellite_path)
    return _SAT


def _err(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


async def tile_ep(req: Request) -> Response:
    try:
        z, x, y = (int(req.path_params[k]) for k in ("z", "x", "y"))
    except (KeyError, ValueError):
        return _err(400, "invalid_tile", "אריח לא תקין")
    if not (0 <= z <= 22 and 0 <= x < 2 ** z and 0 <= y < 2 ** z):
        return _err(400, "invalid_tile", "אריח לא תקין")
    data, source, provider = await run_in_threadpool(service().tile_info, z, x, y)
    if not data:
        # offline: let the browser ask again soon; outside / missing: there is nothing to come
        age = 30 if source == "offline" else 86400
        return Response(status_code=204, headers={"Cache-Control": f"public, max-age={age}", "X-Tile-Source": source})
    return Response(data, media_type="image/png" if data[:4] == b"\x89PNG" else "image/jpeg",
                    headers={"Cache-Control": "public, max-age=2592000", "Access-Control-Allow-Origin": "*",
                             "X-Tile-Source": source, "X-Tile-Provider": provider or ""})


def _area_params(src: dict) -> tuple[Area, int, int]:
    if src.get("area") == "country":
        level = src.get("level") or "basic"
        if level not in LEVELS:
            raise ValueError("unknown level")
        return country_area(), MIN_ZOOM, LEVELS[level][1]
    bbox = parse_bbox(src.get("bbox"))
    minzoom = int(src.get("minzoom", MIN_ZOOM))
    maxzoom = int(src.get("maxzoom", minzoom))
    if not (0 <= minzoom <= 22 and 0 <= maxzoom <= 22):
        raise ValueError("zoom out of range")
    return Area(bbox), minzoom, maxzoom


async def estimate_ep(req: Request) -> Response:
    try:
        area, minzoom, maxzoom = _area_params(dict(req.query_params))
    except (TypeError, ValueError, AttributeError):
        return _err(400, "invalid_parameter", "יש לציין bbox=minlon,minlat,maxlon,maxlat ו-minzoom/maxzoom")
    est = await run_in_threadpool(service().estimate, area, minzoom, maxzoom)
    if area.name == "israel":
        est["too_many"] = False
    return JSONResponse(est)


async def country_ep(req: Request) -> Response:
    """Whole-country download levels with their estimates (tiles, bytes, seconds)."""
    sat = service()
    levels = await run_in_threadpool(sat.country_levels)
    return JSONResponse({"levels": levels, "source": sat.layers[0].source.id}, headers={"Cache-Control": "no-store"})


async def download_ep(req: Request) -> Response:
    try:
        body = await req.json()
    except Exception:  # noqa: BLE001
        return _err(400, "invalid_json", "גוף הבקשה אינו JSON תקין")
    if not isinstance(body, dict):
        return _err(400, "invalid_json", "גוף הבקשה אינו JSON תקין")
    try:
        area, minzoom, maxzoom = _area_params(body)
    except (TypeError, ValueError, AttributeError):
        return _err(400, "invalid_parameter", "יש לציין bbox=[minlon,minlat,maxlon,maxlat] ו-minzoom/maxzoom, "
                                              "או area=country ו-level=basic|detailed|full")
    sat = service()
    country = area.name == "israel"
    total = count_tiles(area, minzoom, maxzoom)
    if total == 0:
        return _err(400, "outside_coverage", "האזור מחוץ לכיסוי תצלומי האוויר (ישראל)")
    if total > MAX_JOB_TILES and not country:
        return _err(413, "too_many_tiles", f"האזור גדול מדי: {total:,} אריחים (עד {MAX_JOB_TILES:,}). הגדילו את המפה או הקטינו את טווח הזום")
    try:
        job = sat.start_job(area, minzoom, maxzoom, max_tiles=max(total, MAX_JOB_TILES) if country else MAX_JOB_TILES)
    except RuntimeError:
        return _err(409, "busy", "הורדה אחרת כבר פועלת")
    return JSONResponse({"job": job.progress()}, status_code=202)


async def cancel_ep(req: Request) -> Response:
    job = service().job
    if job is not None and job.state in ("pending", "running"):
        job.cancel()
    return JSONResponse({"job": job.progress() if job else None})


async def status_ep(req: Request) -> Response:
    st = await run_in_threadpool(service().status)
    return JSONResponse(st, headers={"Cache-Control": "no-store"})


ROUTES = [
    Route("/tiles/satellite/{z}/{x}/{y}.jpg", tile_ep),
    Route("/api/satellite/status", status_ep),
    Route("/api/satellite/estimate", estimate_ep),
    Route("/api/satellite/country", country_ep),
    Route("/api/satellite/download", download_ep, methods=["POST"]),
    Route("/api/satellite/download/cancel", cancel_ep, methods=["POST"]),
]


# ============================================================================= CLI

def _mb(n: float) -> str:
    if n >= 1e9:
        return f"{n / 1e9:,.1f}GB"
    return f"{n / 1e6:,.0f}MB" if n >= 1e6 else f"{n / 1e3:,.0f}KB"


def _dur(sec: float) -> str:
    if sec < 90:
        return "פחות משתי דקות"
    if sec < 3600:
        return f"כ-{sec / 60:.0f} דקות"
    if sec < 48 * 3600:
        return f"כ-{sec / 3600:.1f} שעות"
    return f"כ-{sec / 86400:.1f} ימים"


def _print(s: str = "") -> None:
    print(s, flush=True)


def cmd_test(args, sources: list[Source]) -> int:
    """``--test``: one known tile per source – status, content type, size, verdict."""
    _print("בדיקת מקורות תצלומי האוויר / satellite source check")
    _print("=" * 60)
    results = diagnose(sources)
    for r in results:
        _print(f"\n[{r['source']}] {r['name_he']}")
        _print(f"  רישיון / license: {r['license']}")
        for c in r["checks"]:
            _print(f"  GET {c['url']}")
            _print(f"      HTTP {c['status']}  {c['content_type'] or '-'}  {c['bytes']:,} bytes  {c['ms']} ms  -> {c['message']}")
            if c.get("snippet"):
                _print(f"      {c['snippet'][:120]}")
        rd = r["render"]
        _print(f"  אריח מפה {rd['tile']}: {'OK' if rd['ok'] else 'FAIL'}  {rd['bytes']:,} bytes {rd['format'] or ''}  "
               f"{rd['ms']} ms  {rd['message']}")
    good = [r["source"] for r in results if r["ok"]]
    _print("\n" + "=" * 60)
    if good:
        _print(f"מקורות שעובדים מהמחשב הזה: {', '.join(good)}")
        _print(f"working sources: {', '.join(good)} – the map uses them in this order.")
    else:
        _print("אף מקור לא עובד מהמחשב הזה. אם יש אינטרנט – ייתכן שהסינון חוסם את cdn.govmap.gov.il / "
               "tiles.maps.eox.at; אפשר לבקש מהסינון לפתוח אותם.")
    return 0 if good else 1


def _choose_level(sat: Satellite, source: str | None) -> str | None:
    levels = sat.country_levels(source)
    _print("הורדת תצלומי אוויר לכל הארץ (אפשר להפסיק בכל רגע עם Ctrl+C ולהמשיך אחר כך):")
    for i, lv in enumerate(levels, 1):
        warn = "  ⚠ גדול מאוד" if lv["maxzoom"] >= 16 else ""
        _print(f"  {i}. {lv['name_he']} (זום עד {lv['maxzoom']}): {lv['missing']:,} אריחים להורדה, "
               f"כ-{_mb(lv['bytes_estimate'])}, {_dur(lv['seconds_estimate'])}{warn}")
    ans = input("בחרו 1/2/3 [1]: ").strip() or "1"
    try:
        return levels[int(ans) - 1]["id"]
    except (ValueError, IndexError):
        return None


def cmd(args: argparse.Namespace) -> None:
    """``python -m hmaps.etl satellite`` – pre-download imagery for offline use (default: all of Israel)."""
    from .settings import settings

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    sources = [make_source(s) for s in args.source.split(",")] if args.source else configured_sources()
    if args.test:
        raise SystemExit(cmd_test(args, sources))
    base = Path(args.out) if args.out else settings.satellite_path
    sat = Satellite(base, sources=sources)
    source_id = sources[0].id if args.source else None
    interactive = sys.stdin.isatty() and not args.yes
    if args.bbox:
        area, minzoom, maxzoom = Area(parse_bbox(args.bbox)), args.minzoom, args.maxzoom or 13
        max_tiles = args.max_tiles or 250_000
    else:
        level = args.level
        if not level and args.maxzoom is None:
            level = _choose_level(sat, source_id) if interactive else "basic"
            if not level:
                return
        maxzoom = LEVELS[level][1] if level else args.maxzoom
        area, minzoom = country_area(), args.minzoom
        max_tiles = args.max_tiles or 3_000_000
    est = sat.estimate(area, minzoom, maxzoom, source_id)
    total, missing = est["tiles"], est["missing"]
    where = "כל הארץ" if area.name == "israel" else ",".join(f"{v:.4f}" for v in area.bbox)
    _print(f"{where}, זום {minzoom}–{maxzoom}: {total:,} אריחים, {est['cached']:,} כבר שמורים, "
           f"להורדה {missing:,} (כ-{_mb(est['bytes_estimate'])}, {_dur(est['seconds_estimate'])})")
    if maxzoom >= 16 and area.name == "israel":
        _print("שימו לב: רמה זו תופסת הרבה מקום בדיסק ולוקחת שעות רבות. אפשר לעצור ולהמשיך בכל זמן.")
    if total == 0:
        raise SystemExit("the area is outside the orthophoto coverage (Israel)")
    if total > max_tiles:
        raise SystemExit(f"too many tiles ({total:,} > --max-tiles {max_tiles:,}); "
                         "use a smaller area or zoom range, or raise --max-tiles")
    if args.dry_run or missing == 0:
        if missing == 0:
            _print("הכול כבר שמור במחשב.")
        return
    if interactive and input("להמשיך בהורדה? / continue? [Y/n] ").strip().lower() in ("n", "no", "ל", "לא"):
        return
    job = Job(sat, area, minzoom, maxzoom, max_tiles=max_tiles, workers=args.workers, source=source_id)
    t = threading.Thread(target=job.run, daemon=True)
    t.start()
    try:
        while t.is_alive():
            t.join(10)
            p = job.progress()
            eta = f", נותרו {_dur(p['eta_seconds'])}" if p["eta_seconds"] else ""
            _print(f"  {p['percent']:5.1f}%  {p['done']:,}/{p['total']:,}  (חדשים {p['fetched']:,}, שמורים {p['skipped']:,}, "
                   f"אין {p['missing']:,}, נכשלו {p['failed']}, {_mb(p['bytes'])}, מקור {p['source']}{eta})")
    except KeyboardInterrupt:
        job.cancel()
        t.join()
        _print("נעצר – הרצה חוזרת של אותה פקודה ממשיכה מאותה נקודה.")
    p = job.progress()
    layer = job.layer or sat.layers[0]
    st = layer.cache.stats()
    sat.close()
    _print(f"{p['state']}: {p['fetched']:,} new tiles ({_mb(p['bytes'])}). cache {layer.cache.path.name}: "
           f"{st['tiles']:,} tiles, {_mb(st['disk_bytes'])} on disk")
    if p["state"] == "error":
        raise SystemExit(f"download stopped: {p['error']} (run again to resume; check with --test)")


def add_parser(sub) -> None:
    s = sub.add_parser("satellite", help="download aerial photos for offline use (default: all of Israel; "
                                         "data/tiles/satellite*.mbtiles)")
    s.add_argument("--level", choices=list(LEVELS), help="whole country: basic (z≤13), detailed (z≤15), full (z≤17)")
    s.add_argument("--bbox", help="minlon,minlat,maxlon,maxlat – a single area instead of the whole country")
    s.add_argument("--minzoom", type=int, default=MIN_ZOOM, help=f"default {MIN_ZOOM}")
    s.add_argument("--maxzoom", type=int, help="deepest zoom (default: by --level; 13 with --bbox)")
    s.add_argument("--source", help=f"source id(s): {', '.join(SOURCE_IDS)} or an XYZ URL "
                                    "(default: HMAPS_SATELLITE_SOURCE or govmap,eox)")
    s.add_argument("--test", action="store_true", help="diagnose: fetch one known tile per source and print the result")
    s.add_argument("--workers", type=int, default=JOB_WORKERS, help=f"parallel requests (default {JOB_WORKERS}, max 8)")
    s.add_argument("--max-tiles", type=int, help="safety cap (default 250,000 for --bbox, 3,000,000 for the country)")
    s.add_argument("--out", help="MBTiles path of the first source (default: data/tiles/satellite.mbtiles)")
    s.add_argument("--dry-run", action="store_true", help="only print the estimate")
    s.add_argument("-y", "--yes", action="store_true", help="do not ask before downloading")
