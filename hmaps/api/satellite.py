"""Satellite / aerial orthophoto layer (govmap.gov.il) with a persistent offline cache.

The Israeli Survey Department publishes its orthophotos as JPEG tiles on
``cdn.govmap.gov.il``.  The CDN refuses cross-origin browser requests (CORS +
Referer check), so the browser asks *our* server for
``/tiles/satellite/{z}/{x}/{y}.jpg`` and we fetch the tile from Python.

Every tile that was fetched once is kept in an MBTiles file
(``data/tiles/satellite.mbtiles``), so an area that was viewed – or downloaded
on purpose with ``python -m hmaps.etl satellite`` / ``POST /api/satellite/download``
– keeps working with no internet at all.

Lookup order for a tile request:

1. the MBTiles cache;
2. if missing and we are not in an "offline" back-off: fetch from the CDN
   (short timeout), store, return;
3. otherwise ``204 No Content`` straight away.  After a network failure the
   CDN is not tried again for ``OFFLINE_BACKOFF`` seconds, so a disconnected
   PC does not wait for a timeout on every tile.

Note the CDN path order: ``{z}/{y}/{x}`` (row before column) while our own URL
and the MBTiles file use the usual XYZ ``{z}/{x}/{y}``.
"""

from __future__ import annotations

import argparse
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
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Callable, Iterator

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from ..core import geo

log = logging.getLogger("hmaps.satellite")

#: tile URL on the CDN – note {y} before {x}
CDN_URL = os.environ.get("HMAPS_SAT_URL", "https://cdn.govmap.gov.il/M2023ORT3TS/{z}/{y}/{x}.jpg")
REFERER = "https://www.govmap.gov.il/"
MIN_ZOOM, MAX_ZOOM = 7, 20
#: coverage of the orthophotos (Israel); requests outside are answered with 204
BOUNDS = (34.2, 29.4, 35.9, 33.4)
ATTRIBUTION = "תצלומי אוויר: © מרכז מיפוי ישראל (govmap)"
FETCH_TIMEOUT = float(os.environ.get("HMAPS_SAT_TIMEOUT", "5"))
OFFLINE_BACKOFF = float(os.environ.get("HMAPS_SAT_BACKOFF", "30"))
#: used for size estimates until the cache has real tiles to average
AVG_TILE_BYTES = 22_000
#: per job from the map (the CLI may raise it with --max-tiles)
MAX_JOB_TILES = int(os.environ.get("HMAPS_SAT_MAX_TILES", "30000"))
JOB_WORKERS = 3


class Offline(Exception):
    """The CDN could not be reached (no internet, DNS, timeout, blocked)."""


# ----------------------------------------------------------------------------- CDN fetch

def _ssl_context() -> ssl.SSLContext:
    # govmap uses a certificate chain that Python's ssl may not trust on Windows, and the
    # NetFree filter re-signs HTTPS traffic; we disable verification for this server-side
    # fetch only (public, read-only imagery – nothing is sent, nothing browser-facing).
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


_CTX = _ssl_context()


def fetch_tile(z: int, x: int, y: int, timeout: float = FETCH_TIMEOUT) -> bytes | None:
    """One tile from the CDN: bytes, ``None`` when the CDN has no such tile, ``Offline`` on network failure."""
    url = CDN_URL.format(z=z, x=x, y=y)
    req = urllib.request.Request(url, headers={"Referer": REFERER, "User-Agent": "Mozilla/5.0 (hebrew-maps)"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r:
            data = r.read() if r.status == 200 else None
    except urllib.error.HTTPError as e:
        if e.code in (204, 404):
            return None
        raise Offline(f"HTTP {e.code}") from e  # 403 (geo-block / filter), 5xx: treat as unreachable
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise Offline(str(e)) from e
    if not data or not (data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n"):
        return None  # an HTML error / filter page is never cached as a tile
    return data


# ----------------------------------------------------------------------------- MBTiles cache

class TileCache:
    """Thread-safe MBTiles store (XYZ in the API, TMS rows on disk as the spec says)."""

    def __init__(self, path: Path):
        self.path = Path(path)
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
            meta = {"name": "govmap orthophoto", "format": "jpg", "type": "baselayer", "version": "1",
                    "minzoom": str(MIN_ZOOM), "maxzoom": str(MAX_ZOOM), "bounds": ",".join(map(str, BOUNDS)),
                    "attribution": ATTRIBUTION, "description": CDN_URL}
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


# ----------------------------------------------------------------------------- tile enumeration

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
    """The part of ``bbox`` inside the orthophoto coverage (``None`` if outside)."""
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


def count_tiles(bbox, minzoom: int, maxzoom: int) -> int:
    area = clip_bbox(bbox)
    if area is None:
        return 0
    total = 0
    for z in _zooms(minzoom, maxzoom):
        x0, x1, y0, y1 = tile_range(area, z)
        total += (x1 - x0 + 1) * (y1 - y0 + 1)
    return total


def iter_tiles(bbox, minzoom: int, maxzoom: int) -> Iterator[tuple[int, int, int]]:
    """All (z, x, y) of the bbox, coarse zooms first (the overview is usable soonest)."""
    area = clip_bbox(bbox)
    if area is None:
        return
    for z in _zooms(minzoom, maxzoom):
        x0, x1, y0, y1 = tile_range(area, z)
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                yield z, x, y


# ----------------------------------------------------------------------------- service

class Satellite:
    """Cache + CDN fetcher + offline back-off + one background download job at a time."""

    def __init__(self, path: Path, fetch: Callable[[int, int, int], bytes | None] = fetch_tile,
                 backoff: float = OFFLINE_BACKOFF):
        self.cache = TileCache(path)
        self.fetch = fetch
        self.backoff = backoff
        self._offline_until = 0.0
        self._lock = threading.Lock()
        self.job: Job | None = None

    # -- online state
    @property
    def offline(self) -> bool:
        return time.monotonic() < self._offline_until

    def _mark_offline(self, why: str) -> None:
        if not self.offline:
            log.info("satellite CDN unreachable (%s) – serving cached tiles only for %ds", why, self.backoff)
        self._offline_until = time.monotonic() + self.backoff

    # -- tiles
    def tile(self, z: int, x: int, y: int) -> tuple[bytes | None, str]:
        """(data, source): source is ``cache`` | ``cdn`` | ``missing`` | ``offline`` | ``outside``."""
        if not MIN_ZOOM <= z <= MAX_ZOOM:
            return None, "outside"
        w, s, e, n = geo.tile_bounds(z, x, y)
        if e < BOUNDS[0] or w > BOUNDS[2] or n < BOUNDS[1] or s > BOUNDS[3]:
            return None, "outside"
        data = self.cache.get(z, x, y)
        if data:
            return data, "cache"
        if self.offline:
            return None, "offline"
        try:
            data = self.fetch(z, x, y)
        except Offline as e:
            self._mark_offline(str(e))
            return None, "offline"
        if not data:
            return None, "missing"
        self._offline_until = 0.0
        try:
            self.cache.put(z, x, y, data)
        except sqlite3.Error:  # disk full / read-only: still show the tile
            log.exception("could not cache satellite tile %s/%s/%s", z, x, y)
        return data, "cdn"

    # -- estimates & status
    def avg_tile_bytes(self, st: dict | None = None) -> int:
        st = st or self.cache.stats()
        return int(st["bytes"] / st["tiles"]) if st["tiles"] >= 20 else AVG_TILE_BYTES

    def estimate(self, bbox, minzoom: int, maxzoom: int) -> dict:
        total = count_tiles(bbox, minzoom, maxzoom)
        cached = 0
        area = clip_bbox(bbox)
        if area is not None and total <= 4 * MAX_JOB_TILES:
            for z in _zooms(minzoom, maxzoom):
                cached += len(self.cache.existing(z, *tile_range(area, z)))
        missing = max(0, total - cached)
        return {"tiles": total, "cached": cached, "missing": missing,
                "bytes_estimate": missing * self.avg_tile_bytes(), "max_tiles": MAX_JOB_TILES,
                "too_many": total > MAX_JOB_TILES}

    def status(self) -> dict:
        st = self.cache.stats()
        return {"cache": st, "offline": self.offline, "source": CDN_URL.split("/{z}")[0],
                "min_zoom": MIN_ZOOM, "max_zoom": MAX_ZOOM, "bounds": list(BOUNDS), "max_job_tiles": MAX_JOB_TILES,
                "job": self.job.progress() if self.job else None}

    # -- download jobs
    def start_job(self, bbox, minzoom: int, maxzoom: int, max_tiles: int = MAX_JOB_TILES,
                  workers: int = JOB_WORKERS) -> "Job":
        with self._lock:
            if self.job is not None and self.job.state == "running":
                raise RuntimeError("busy")
            job = Job(self, bbox, minzoom, maxzoom, max_tiles=max_tiles, workers=workers)
            self.job = job
        threading.Thread(target=job.run, name="satellite-download", daemon=True).start()
        return job


class Job:
    """Pre-download all tiles of a bbox and zoom range into the cache (skips what is cached)."""

    #: this many network failures in a row ends the job (the PC is offline)
    MAX_CONSECUTIVE_FAILURES = 8

    def __init__(self, sat: Satellite, bbox, minzoom: int, maxzoom: int, max_tiles: int = MAX_JOB_TILES,
                 workers: int = JOB_WORKERS):
        self.sat = sat
        self.bbox = tuple(bbox)
        self.minzoom, self.maxzoom = minzoom, maxzoom
        self.workers = max(1, min(8, workers))
        self.total = count_tiles(self.bbox, minzoom, maxzoom)
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

    def cancel(self) -> None:
        self._cancel.set()

    def progress(self) -> dict:
        with self._lock:
            pct = round(100.0 * self.done / self.total, 1) if self.total else 100.0
            return {"state": self.state, "total": self.total, "done": self.done, "percent": pct,
                    "fetched": self.fetched, "skipped": self.skipped, "missing": self.missing, "failed": self.failed,
                    "bytes": self.bytes, "error": self.error, "bbox": list(self.bbox),
                    "minzoom": self.minzoom, "maxzoom": self.maxzoom,
                    "started": self.started, "finished": self.finished}

    def _one(self, z: int, x: int, y: int) -> None:
        if self._cancel.is_set():
            return
        try:
            data = self.sat.fetch(z, x, y)
        except Offline as e:
            with self._lock:
                self.failed += 1
                self.done += 1
                self._fails_in_row += 1
                if self._fails_in_row >= self.MAX_CONSECUTIVE_FAILURES:
                    self.error = f"offline: {e}"
                    self._cancel.set()
            return
        if data:
            self.sat.cache.put(z, x, y, data)
        with self._lock:
            self._fails_in_row = 0
            self.done += 1
            if data:
                self.fetched += 1
                self.bytes += len(data)
            else:
                self.missing += 1

    def _todo(self) -> Iterator[tuple[int, int, int]]:
        area = clip_bbox(self.bbox)
        if area is None:
            return
        for z in _zooms(self.minzoom, self.maxzoom):
            x0, x1, y0, y1 = tile_range(area, z)
            have = self.sat.cache.existing(z, x0, x1, y0, y1)
            for y in range(y0, y1 + 1):
                for x in range(x0, x1 + 1):
                    if (x, y) in have:
                        with self._lock:
                            self.skipped += 1
                            self.done += 1
                    else:
                        yield z, x, y

    def run(self) -> None:
        self.state = "running"
        try:
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
            if self.error:
                self.state = "error"
            elif self._cancel.is_set():
                self.state = "cancelled"
            else:
                self.state = "done"
        except Exception as e:  # noqa: BLE001
            log.exception("satellite download failed")
            self.error, self.state = str(e), "error"
        finally:
            self.finished = time.time()


# ----------------------------------------------------------------------------- HTTP API

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
    data, source = await run_in_threadpool(service().tile, z, x, y)
    if not data:
        # offline: let the browser ask again soon; outside / missing: there is nothing to come
        age = 30 if source == "offline" else 86400
        return Response(status_code=204, headers={"Cache-Control": f"public, max-age={age}", "X-Tile-Source": source})
    return Response(data, media_type="image/png" if data[:4] == b"\x89PNG" else "image/jpeg",
                    headers={"Cache-Control": "public, max-age=2592000", "Access-Control-Allow-Origin": "*",
                             "X-Tile-Source": source})


def _area_params(src: dict) -> tuple[tuple[float, float, float, float], int, int]:
    bbox = parse_bbox(src.get("bbox"))
    minzoom = int(src.get("minzoom", MIN_ZOOM))
    maxzoom = int(src.get("maxzoom", minzoom))
    if not (0 <= minzoom <= 22 and 0 <= maxzoom <= 22):
        raise ValueError("zoom out of range")
    return bbox, minzoom, maxzoom


async def estimate_ep(req: Request) -> Response:
    try:
        bbox, minzoom, maxzoom = _area_params(dict(req.query_params))
    except (TypeError, ValueError, AttributeError):
        return _err(400, "invalid_parameter", "יש לציין bbox=minlon,minlat,maxlon,maxlat ו-minzoom/maxzoom")
    est = await run_in_threadpool(service().estimate, bbox, minzoom, maxzoom)
    return JSONResponse(est)


async def download_ep(req: Request) -> Response:
    try:
        body = await req.json()
    except Exception:  # noqa: BLE001
        return _err(400, "invalid_json", "גוף הבקשה אינו JSON תקין")
    if not isinstance(body, dict):
        return _err(400, "invalid_json", "גוף הבקשה אינו JSON תקין")
    try:
        bbox, minzoom, maxzoom = _area_params(body)
    except (TypeError, ValueError, AttributeError):
        return _err(400, "invalid_parameter", "יש לציין bbox=[minlon,minlat,maxlon,maxlat] ו-minzoom/maxzoom")
    sat = service()
    total = count_tiles(bbox, minzoom, maxzoom)
    if total == 0:
        return _err(400, "outside_coverage", "האזור מחוץ לכיסוי תצלומי האוויר (ישראל)")
    if total > MAX_JOB_TILES:
        return _err(413, "too_many_tiles", f"האזור גדול מדי: {total:,} אריחים (עד {MAX_JOB_TILES:,}). הגדילו את המפה או הקטינו את טווח הזום")
    try:
        job = sat.start_job(bbox, minzoom, maxzoom)
    except RuntimeError:
        return _err(409, "busy", "הורדה אחרת כבר פועלת")
    return JSONResponse({"job": job.progress()}, status_code=202)


async def cancel_ep(req: Request) -> Response:
    job = service().job
    if job is not None and job.state == "running":
        job.cancel()
    return JSONResponse({"job": job.progress() if job else None})


async def status_ep(req: Request) -> Response:
    st = await run_in_threadpool(service().status)
    return JSONResponse(st, headers={"Cache-Control": "no-store"})


ROUTES = [
    Route("/tiles/satellite/{z}/{x}/{y}.jpg", tile_ep),
    Route("/api/satellite/status", status_ep),
    Route("/api/satellite/estimate", estimate_ep),
    Route("/api/satellite/download", download_ep, methods=["POST"]),
    Route("/api/satellite/download/cancel", cancel_ep, methods=["POST"]),
]


# ----------------------------------------------------------------------------- CLI

def _mb(n: float) -> str:
    return f"{n / 1e6:,.0f}MB" if n >= 1e6 else f"{n / 1e3:,.0f}KB"


def cmd(args: argparse.Namespace) -> None:
    """``python -m hmaps.etl satellite`` – pre-download an area for offline use."""
    from .settings import settings
    path = Path(args.out) if args.out else settings.satellite_path
    bbox = parse_bbox(args.bbox)
    sat = Satellite(path)
    est = sat.estimate(bbox, args.minzoom, args.maxzoom)
    total = est["tiles"]
    missing = total - est["cached"]
    log.info("area %s, zoom %d–%d: %s tiles, %s already cached, ~%s to download",
             ",".join(f"{v:.4f}" for v in bbox), args.minzoom, args.maxzoom, f"{total:,}", f"{est['cached']:,}",
             _mb(missing * sat.avg_tile_bytes()))
    if total == 0:
        raise SystemExit("the area is outside the orthophoto coverage (Israel)")
    if total > args.max_tiles:
        raise SystemExit(f"too many tiles ({total:,} > --max-tiles {args.max_tiles:,}); "
                         "use a smaller area or zoom range, or raise --max-tiles")
    if args.dry_run:
        return
    if not args.yes and sys.stdin.isatty():
        if input("להמשיך בהורדה? / continue? [Y/n] ").strip().lower() in ("n", "no", "ל", "לא"):
            return
    job = Job(sat, bbox, args.minzoom, args.maxzoom, max_tiles=args.max_tiles, workers=args.workers)
    t = threading.Thread(target=job.run, daemon=True)
    t.start()
    try:
        while t.is_alive():
            t.join(5)
            p = job.progress()
            log.info("  %5.1f%%  %s/%s  (new %s, cached %s, none %s, failed %s, %s)", p["percent"], f"{p['done']:,}",
                     f"{p['total']:,}", f"{p['fetched']:,}", f"{p['skipped']:,}", p["missing"], p["failed"], _mb(p["bytes"]))
    except KeyboardInterrupt:
        job.cancel()
        t.join()
        log.info("stopped – run the same command again to resume")
    p = job.progress()
    st = sat.cache.stats()
    sat.cache.close()
    log.info("%s: %s new tiles (%s). cache now %s tiles, %s on disk – %s", p["state"], f"{p['fetched']:,}", _mb(p["bytes"]),
             f"{st['tiles']:,}", _mb(st["disk_bytes"]), path)
    if p["state"] == "error":
        raise SystemExit(f"download stopped: {p['error']} (run again to resume)")


def add_parser(sub) -> None:
    s = sub.add_parser("satellite", help="download govmap aerial photos of an area for offline use (data/tiles/satellite.mbtiles)")
    s.add_argument("--bbox", default=",".join(map(str, BOUNDS)),
                   help="minlon,minlat,maxlon,maxlat (default: all of Israel)")
    s.add_argument("--minzoom", type=int, default=MIN_ZOOM, help=f"default {MIN_ZOOM}")
    s.add_argument("--maxzoom", type=int, default=13, help="default 13 (all of Israel ≈ 6,000 tiles, ~130MB)")
    s.add_argument("--workers", type=int, default=4, help="parallel requests (default 4, max 8)")
    s.add_argument("--max-tiles", type=int, default=250_000, help="safety cap (default 250,000)")
    s.add_argument("--out", help="MBTiles path (default: data/tiles/satellite.mbtiles)")
    s.add_argument("--dry-run", action="store_true", help="only print the estimate")
    s.add_argument("-y", "--yes", action="store_true", help="do not ask before downloading")
