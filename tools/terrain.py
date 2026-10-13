"""Topographic layers from the Copernicus GLO-30 DEM: contour lines + terrain-RGB (hillshade).

    python -m tools.terrain build              # download (resumable) + mosaic + both tile sets
    python -m tools.terrain build --if-missing --clean   # what the Windows setup runs
    python -m tools.terrain download           # only fetch the DEM tiles (~530 MB, MD5-checked)
    python -m tools.terrain contours|terrain   # only one of the tile sets

Outputs (published atomically, the running server picks them up by itself):

* data/tiles/contours-<time>.mbtiles – vector tiles z10-14, layers ``contour`` (ele, idx) and
  ``spot`` (ele): 100 m at z10, 50 m at z11-12, 20 m at z13, 10 m at z14 (index lines every 100 m);
* data/tiles/terrain-<time>.mbtiles  – Terrarium-encoded PNG raster-dem tiles z5-12 (hillshade,
  3D terrain, and the elevation API).

Measured for Israel (2 CPUs): ~10 min, peak RAM ~0.3 GB, ~100 MB + ~100 MB.

Needs numpy and contourpy only (both have wheels for Windows/macOS/Linux). The DEM
GeoTIFFs are decoded by a small built-in reader, so GDAL/rasterio are not required.

Copernicus DEM © DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018,
provided under COPERNICUS by the European Union and ESA; all rights reserved.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import sqlite3
import struct
import sys
import time
import urllib.error
import urllib.request
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hmaps.api import mvt  # noqa: E402
from hmaps.core import dem  # noqa: E402

DATA = Path(os.environ.get("HMAPS_DATA", ROOT / "data"))
DEM_DIR = Path(os.environ.get("HMAPS_DEM_DIR", DATA / "dem"))
URL = "https://copernicus-dem-30m.s3.amazonaws.com/{name}/{name}.tif"
UA = {"User-Agent": "HebrewMaps-setup/1.0"}
ATTRIBUTION = "Copernicus DEM © DLR/Airbus, ESA"

#: DEM tiles to download (1°×1°, named by their south-west corner)
DOWNLOAD_LATS = range(29, 34)
DOWNLOAD_LONS = range(34, 37)
#: extent of the elevation grid (= the downloaded DEM tiles)
GRID_BBOX = (min(DOWNLOAD_LONS), min(DOWNLOAD_LATS), max(DOWNLOAD_LONS) + 1, max(DOWNLOAD_LATS) + 1)
#: area for which tiles are generated (Israel incl. Golan, Judea & Samaria, Eilat + margin)
BUILD_BBOX = (34.1, 29.35, 36.0, 33.5)
#: the mosaic grid: 1 arc-second (Copernicus resolution at these latitudes), decimetres in int16
GRID_RES = 1.0 / 3600.0

#: contour interval and index-line interval per zoom
CONTOUR_ZOOMS = {10: (100, 500), 11: (50, 100), 12: (50, 100), 13: (20, 100), 14: (10, 100)}
TERRAIN_ZOOMS = range(5, 13)


def tile_name(lat: int, lon: int) -> str:
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    return f"Copernicus_DSM_COG_10_{ns}{abs(lat):02d}_00_{ew}{abs(lon):03d}_00_DEM"


# ============================================================================ download

def _download_one(lat: int, lon: int, dest_dir: Path, retries: int = 4) -> tuple[str, str]:
    name = tile_name(lat, lon)
    dest = dest_dir / f"{name}.tif"
    missing = dest_dir / f"{name}.missing"
    if missing.exists():
        return name, "sea"
    url = URL.format(name=name)
    for attempt in range(retries):
        try:
            head = urllib.request.urlopen(urllib.request.Request(url, headers=UA, method="HEAD"), timeout=60)
            size = int(head.headers.get("Content-Length", "0"))
            etag = (head.headers.get("ETag") or "").strip('"')
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):  # no tile = open sea
                missing.write_text("no such tile (sea)\n")
                return name, "sea"
            err = e
        except OSError as e:
            if dest.exists():
                return name, "cached (offline – not re-verified)"
            err = e
        else:
            if dest.exists() and dest.stat().st_size == size and _verify(dest, etag):
                return name, "cached"
            part = dest.with_suffix(".part")
            have = part.stat().st_size if part.exists() else 0
            if have > size:
                part.unlink()
                have = 0
            try:
                hdr = dict(UA)
                if have:
                    hdr["Range"] = f"bytes={have}-"
                with urllib.request.urlopen(urllib.request.Request(url, headers=hdr), timeout=120) as r:
                    if have and r.status != 206:
                        have = 0  # server ignored the range: restart
                    with open(part, "ab" if have else "wb") as f:
                        while chunk := r.read(1 << 20):
                            f.write(chunk)
            except OSError as e:
                err = e
            else:
                if part.stat().st_size == size and _verify(part, etag):
                    os.replace(part, dest)
                    return name, "downloaded"
                part.unlink(missing_ok=True)
                err = RuntimeError("size/checksum mismatch")
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"could not download {name}: {err}")


def _verify(path: Path, etag: str) -> bool:
    """S3 ETag of a single-part upload is the MD5 of the file."""
    if not etag or "-" in etag or len(etag) != 32:
        return True
    h = hashlib.md5()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 22):
            h.update(chunk)
    return h.hexdigest() == etag


def download(dest_dir: Path = DEM_DIR, workers: int = 3) -> list[Path]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    jobs = [(lat, lon) for lat in DOWNLOAD_LATS for lon in DOWNLOAD_LONS]
    with ThreadPoolExecutor(workers) as ex:
        for name, status in ex.map(lambda j: _download_one(*j, dest_dir), jobs):
            print(f"  {name}: {status}", flush=True)
    return sorted(dest_dir.glob("Copernicus_DSM_COG_10_*_DEM.tif"))


# ============================================================================ GeoTIFF reader

_TYPES = {1: "B", 2: "s", 3: "H", 4: "I", 5: "II", 6: "b", 7: "B", 8: "h", 9: "i", 11: "f", 12: "d", 16: "Q"}
_SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 11: 4, 12: 8, 16: 8}


def read_geotiff(path: Path) -> tuple[np.ndarray, tuple[float, float, float, float]]:
    """Read the first image of a (Cloud Optimized) GeoTIFF with float32/int16 samples.

    Supports deflate/uncompressed, horizontal and floating-point predictors, tiles or
    strips – what Copernicus DEM uses. Returns (array, (west, north, xres, yres)).
    """
    with open(path, "rb") as f:
        buf = f.read()
    bo = {b"II": "<", b"MM": ">"}[buf[:2]]
    magic = struct.unpack(bo + "H", buf[2:4])[0]
    if magic == 42:
        ifd = struct.unpack(bo + "I", buf[4:8])[0]
        num_fmt, cnt_fmt, ent_size, off_fmt, inline = "H", "I", 12, "I", 4
    elif magic == 43:
        ifd = struct.unpack(bo + "Q", buf[8:16])[0]
        num_fmt, cnt_fmt, ent_size, off_fmt, inline = "Q", "Q", 20, "Q", 8
    else:
        raise ValueError("not a TIFF")
    n = struct.unpack(bo + num_fmt, buf[ifd:ifd + struct.calcsize(num_fmt)])[0]
    p = ifd + struct.calcsize(num_fmt)
    tags: dict[int, tuple] = {}
    for _ in range(n):
        e = buf[p:p + ent_size]
        tag, typ = struct.unpack(bo + "HH", e[:4])
        count = struct.unpack(bo + cnt_fmt, e[4:4 + struct.calcsize(cnt_fmt)])[0]
        size = _SIZES.get(typ, 1) * count
        vpos = p + 4 + struct.calcsize(cnt_fmt)
        if size > inline:
            vpos = struct.unpack(bo + off_fmt, buf[vpos:vpos + inline])[0]
        raw = buf[vpos:vpos + size]
        if typ == 2:
            tags[tag] = (raw.rstrip(b"\0").decode("latin-1"),)
        elif typ == 5:
            tags[tag] = struct.unpack(bo + "I" * (2 * count), raw)
        else:
            tags[tag] = struct.unpack(bo + _TYPES.get(typ, "B") * count, raw)
        p += ent_size
    w, h = tags[256][0], tags[257][0]
    bits = tags.get(258, (16,))[0]
    comp = tags.get(259, (1,))[0]
    pred = tags.get(317, (1,))[0]
    fmt = tags.get(339, (1,))[0]
    dtype = np.dtype({(32, 3): "f4", (64, 3): "f8", (16, 2): "i2", (16, 1): "u2", (32, 2): "i4"}[(bits, fmt)]).newbyteorder(bo)
    if comp not in (1, 8, 32946):
        raise ValueError(f"unsupported TIFF compression {comp}")
    if 322 in tags:
        tw, th = tags[322][0], tags[323][0]
        offs, cnts = tags[324], tags[325]
    else:
        tw, th = w, tags.get(278, (h,))[0]
        offs, cnts = tags[273], tags[279]
    out = np.zeros((h, w), dtype=dtype.newbyteorder("="))
    across = math.ceil(w / tw)
    bps = dtype.itemsize
    for i, (o, c) in enumerate(zip(offs, cnts)):
        if not c:
            continue
        data = buf[o:o + c]
        if comp != 1:
            data = zlib.decompress(data)
        rows = len(data) // (tw * bps)
        if pred == 3:
            b = np.frombuffer(data, dtype=np.uint8)[: rows * tw * bps].reshape(rows, tw * bps)
            b = np.cumsum(b, axis=1, dtype=np.uint8)  # wraps modulo 256
            # byte planes, most significant first → big-endian samples
            block = b.reshape(rows, bps, tw).transpose(0, 2, 1).copy().view(dtype.newbyteorder(">"))[..., 0]
        else:
            block = np.frombuffer(data, dtype=dtype)[: rows * tw].reshape(rows, tw)
            if pred == 2:
                block = np.cumsum(block, axis=1, dtype=block.dtype)
        ty, tx = divmod(i, across)
        y0, x0 = ty * th, tx * tw
        hh, ww = min(rows, h - y0), min(tw, w - x0)
        if hh > 0 and ww > 0:
            out[y0:y0 + hh, x0:x0 + ww] = block[:hh, :ww]
    scale = tags.get(33550, (1.0, 1.0, 0.0))
    tie = tags.get(33922, (0, 0, 0, 0.0, 0.0, 0.0))
    return out, (tie[3], tie[4], scale[0], scale[1])


# ============================================================================ mosaic

class Grid:
    """A regular lat/lon elevation grid (int16 decimetres, row-major file + .json header).

    ``level(k)`` gives the same grid averaged over 2^k × 2^k cells (built once and cached
    next to it), so low zooms are sampled without aliasing and without loading much.
    """

    def __init__(self, path: Path):
        meta = json.loads(path.with_suffix(".json").read_text())
        self.path = path
        self.west, self.north, self.res = meta["west"], meta["north"], meta["res"]
        self.h, self.w = meta["height"], meta["width"]
        self._levels: dict[int, Grid] = {0: self}

    def rows(self, r0: int, r1: int) -> np.ndarray:
        """Rows r0..r1 as int16 (read from the file: keeps the process' memory small)."""
        r0, r1 = max(0, r0), min(self.h, r1)
        with open(self.path, "rb") as f:
            f.seek(r0 * self.w * 2)
            return np.fromfile(f, dtype="<i2", count=(r1 - r0) * self.w).reshape(r1 - r0, self.w)

    def level(self, k: int) -> "Grid":
        if k <= 0:
            return self
        if k in self._levels:
            return self._levels[k]
        src = self.level(k - 1)
        path = self.path.with_name(f"{self.path.stem}.L{k}{self.path.suffix}")
        meta_path = path.with_suffix(".json")
        if not (path.exists() and meta_path.exists() and path.stat().st_mtime >= self.path.stat().st_mtime):
            h, w = src.h // 2, src.w // 2
            tmp = path.with_suffix(".tmp")
            with open(tmp, "wb") as f:
                for r in range(0, h, 512):
                    rr = min(h, r + 512)
                    block = src.rows(2 * r, 2 * rr)[:, :2 * w].astype(np.float32)
                    f.write(np.round(block.reshape(rr - r, 2, w, 2).mean(axis=(1, 3))).astype("<i2").tobytes())
            os.replace(tmp, path)
            meta_path.write_text(json.dumps({"west": src.west, "north": src.north, "res": src.res * 2,
                                             "width": w, "height": h, "unit": "dm"}))
        g = Grid(path)
        self._levels[k] = g
        return g

    def sample(self, lon: np.ndarray, lat: np.ndarray, px_deg: float | None = None) -> np.ndarray:
        """Bilinear elevation (m) on the outer product of lat (rows) × lon (cols); 0 outside the grid.

        ``px_deg`` – output pixel size in degrees of longitude: picks a pre-averaged level so
        coarse outputs are not aliased.
        """
        if px_deg:
            k = int(math.floor(math.log2(max(1.0, px_deg / self.res))))
            if k > 0:
                return self.level(k).sample(lon, lat)
        lon, lat = np.asarray(lon, dtype=np.float64), np.asarray(lat, dtype=np.float64)
        fx = (lon - self.west) / self.res - 0.5
        fy = (self.north - lat) / self.res - 0.5
        inside = ((fy >= -0.5) & (fy <= self.h - 0.5))[:, None] & ((fx >= -0.5) & (fx <= self.w - 0.5))[None, :]
        out = np.zeros((len(lat), len(lon)), dtype=np.float32)
        if not inside.any():
            return out
        x0 = np.clip(np.floor(fx).astype(np.int64), 0, self.w - 2)
        y0 = np.clip(np.floor(fy).astype(np.int64), 0, self.h - 2)
        tx = np.clip(fx - x0, 0, 1)[None, :].astype(np.float32)
        ty = np.clip(fy - y0, 0, 1)[:, None].astype(np.float32)
        r0, r1 = y0.min(), y0.max() + 2
        c0, c1 = x0.min(), x0.max() + 2
        win = self.rows(int(r0), int(r1))[:, c0:c1].astype(np.float32) / 10.0
        yy, xx = (y0 - r0)[:, None], (x0 - c0)[None, :]
        v = ((win[yy, xx] * (1 - tx) + win[yy, xx + 1] * tx) * (1 - ty)
             + (win[yy + 1, xx] * (1 - tx) + win[yy + 1, xx + 1] * tx) * ty)
        return np.where(inside, v, out)


def build_mosaic(tifs: list[Path], out: Path, bbox=None, margin: float = 0.0) -> Grid:
    """Paste the 1° DEM tiles into one int16 (decimetre) grid file. Missing tiles = sea (0)."""
    bbox = bbox or GRID_BBOX
    # align cell centres with the DEM's whole arc-seconds so no resampling is needed
    west = round((bbox[0] - margin) / GRID_RES) * GRID_RES - GRID_RES / 2
    north = round((bbox[3] + margin) / GRID_RES) * GRID_RES + GRID_RES / 2
    east, south = bbox[2] + margin, bbox[1] - margin
    w = int(round((east - west) / GRID_RES))
    h = int(round((north - south) / GRID_RES))
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    with open(tmp, "wb") as f:
        f.truncate(h * w * 2)  # all zeros (= sea) until a DEM tile is pasted
    for tif in tifs:
        arr, (tw, tn, xres, yres) = read_geotiff(tif)
        arr = np.where(np.isfinite(arr) & (arr > -1000), arr, 0)
        th, twd = arr.shape
        if abs(xres - GRID_RES) > 1e-9:
            # higher latitudes are stored with coarser longitude spacing: resample columns
            src_x = (np.arange(int(round(twd * xres / GRID_RES))) + 0.5) * GRID_RES / xres - 0.5
            arr = np.stack([np.interp(src_x, np.arange(twd), row) for row in arr])
            twd = arr.shape[1]
        # Copernicus pixel-is-point: the tie point is the centre of the corner pixel
        col0 = int(round((tw - GRID_RES / 2 - west) / GRID_RES))
        row0 = int(round((north - (tn + yres / 2)) / GRID_RES))
        r0, c0 = max(0, row0), max(0, col0)
        r1, c1 = min(h, row0 + th), min(w, col0 + twd)
        if r1 <= r0 or c1 <= c0:
            continue
        patch = np.round(arr[r0 - row0:r1 - row0, c0 - col0:c1 - col0] * 10).astype("<i2")
        with open(tmp, "r+b") as f:
            for i, row in enumerate(patch):
                f.seek(((r0 + i) * w + c0) * 2)
                f.write(row.tobytes())
        print(f"  mosaic: {tif.name}", flush=True)
        del arr, patch
    os.replace(tmp, out)
    out.with_suffix(".json").write_text(json.dumps({"west": west, "north": north, "res": GRID_RES,
                                                    "width": w, "height": h, "unit": "dm"}))
    return Grid(out)


# ============================================================================ mbtiles

class MBTilesWriter:
    """Writes into ``<dir>/build/<name>`` and publishes as ``<stem>-<timestamp>.mbtiles``.

    The server serves the newest ``<stem>*.mbtiles``, so a new build replaces the old one
    while it runs – also on Windows, where an open file cannot be overwritten.
    """

    def __init__(self, path: Path, metadata: dict[str, str]):
        self.final = path
        self.tmp = path.parent / "build" / path.name
        self.tmp.parent.mkdir(parents=True, exist_ok=True)
        self.tmp.unlink(missing_ok=True)
        self.con = sqlite3.connect(self.tmp)
        self.con.executescript(
            "PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;"
            "CREATE TABLE metadata (name TEXT, value TEXT);"
            "CREATE TABLE tiles (zoom_level INTEGER, tile_column INTEGER, tile_row INTEGER, tile_data BLOB);")
        self.con.executemany("INSERT INTO metadata VALUES (?, ?)", metadata.items())
        self.count = 0

    def put(self, z: int, x: int, y: int, data: bytes) -> None:
        self.con.execute("INSERT INTO tiles VALUES (?, ?, ?, ?)", (z, x, (1 << z) - 1 - y, data))
        self.count += 1
        if self.count % 500 == 0:
            self.con.commit()

    def close(self) -> Path:
        self.con.execute("CREATE UNIQUE INDEX tile_index ON tiles (zoom_level, tile_column, tile_row)")
        self.con.commit()
        self.con.execute("VACUUM")
        self.con.close()
        stem = self.final.stem
        final = self.final.with_name(f"{stem}-{time.strftime('%Y%m%d-%H%M%S')}{self.final.suffix}")
        os.replace(self.tmp, final)
        for old in self.final.parent.glob(f"{stem}*{self.final.suffix}"):
            if old != final:
                try:
                    old.unlink()
                except OSError:
                    pass  # still open by the running server (Windows): removed next time
        return final


def published(path: Path) -> Path | None:
    """The newest published version of ``<dir>/<stem>.mbtiles``."""
    cands = [p for p in path.parent.glob(f"{path.stem}*{path.suffix}") if p.is_file()]
    return max(cands, key=lambda p: p.stat().st_mtime_ns) if cands else None


def _bbox_meta(bbox) -> str:
    return ",".join(f"{v:.4f}" for v in bbox)


# ============================================================================ contours

def simplify(pts: np.ndarray, tol: float, passes: int = 16) -> np.ndarray:
    """Fast vectorised line simplification.

    Repeatedly drops every other interior vertex whose distance to the chord between
    its neighbours is below ``tol`` (never two neighbours in one pass, so the error
    stays bounded by roughly ``tol`` per pass). Much faster than Douglas–Peucker in
    numpy for the thousands of short contour pieces per tile, with similar results.
    """
    idle = 0
    for p in range(passes):
        n = len(pts)
        if n < 3:
            return pts
        a, m, b = pts[:-2], pts[1:-1], pts[2:]
        d = b - a
        norm = np.hypot(d[:, 0], d[:, 1])
        cross = np.abs(d[:, 0] * (m[:, 1] - a[:, 1]) - d[:, 1] * (m[:, 0] - a[:, 0]))
        dist = np.where(norm > 0, cross / np.where(norm > 0, norm, 1), np.hypot(m[:, 0] - a[:, 0], m[:, 1] - a[:, 1]))
        drop = dist < tol
        drop[(p % 2)::2] = False  # alternate odd/even vertices between passes
        if not drop.any():
            idle += 1
            if idle >= 2:
                break
            continue
        idle = 0
        keep = np.ones(n, dtype=bool)
        keep[1:-1] = ~drop
        pts = pts[keep]
    return pts


def _smooth(a: np.ndarray, passes: int = 1) -> np.ndarray:
    for _ in range(passes):
        p = np.pad(a, 1, mode="edge")
        a = (p[:-2, :-2] + 2 * p[:-2, 1:-1] + p[:-2, 2:] + 2 * p[1:-1, :-2] + 4 * p[1:-1, 1:-1] + 2 * p[1:-1, 2:]
             + p[2:, :-2] + 2 * p[2:, 1:-1] + p[2:, 2:]) / 16.0
    return a


def simplify_many(lines: list[np.ndarray], tol: float, passes: int = 16) -> list[np.ndarray]:
    """``simplify`` for many lines at once (one set of numpy operations for the whole tile)."""
    if not lines:
        return []
    lens = np.array([len(ln) for ln in lines])
    pts = np.concatenate(lines)
    lid = np.repeat(np.arange(len(lines)), lens)
    idle = 0
    for p in range(passes):
        n = len(pts)
        if n < 3:
            break
        a, m, b = pts[:-2], pts[1:-1], pts[2:]
        interior = (lid[:-2] == lid[1:-1]) & (lid[2:] == lid[1:-1])
        d = b - a
        norm = np.hypot(d[:, 0], d[:, 1])
        cross = np.abs(d[:, 0] * (m[:, 1] - a[:, 1]) - d[:, 1] * (m[:, 0] - a[:, 0]))
        dist = np.where(norm > 0, cross / np.where(norm > 0, norm, 1), np.hypot(m[:, 0] - a[:, 0], m[:, 1] - a[:, 1]))
        drop = interior & (dist < tol)
        drop[(p % 2)::2] = False
        if not drop.any():
            idle += 1
            if idle >= 2:
                break
            continue
        idle = 0
        keep = np.ones(n, dtype=bool)
        keep[1:-1] = ~drop
        pts, lid = pts[keep], lid[keep]
    cuts = np.flatnonzero(np.diff(lid)) + 1
    return np.split(pts, cuts)


def varints(values: np.ndarray) -> bytes:
    """Protobuf varint encoding of many non-negative integers (vectorised)."""
    v = np.asarray(values, dtype=np.uint64)
    if not len(v):
        return b""
    nb = 1 + sum((v >= np.uint64(1 << (7 * k))).astype(np.int64) for k in range(1, 5))
    k = np.arange(5, dtype=np.uint64)
    groups = (v[:, None] >> (np.uint64(7) * k[None, :])) & np.uint64(0x7F)
    cont = np.arange(5)[None, :] < (nb[:, None] - 1)
    groups = groups | (cont.astype(np.uint64) << np.uint64(7))
    return groups[np.arange(5)[None, :] < nb[:, None]].astype(np.uint8).tobytes()


def line_geometry(lines: list[np.ndarray]) -> bytes:
    """MVT command stream (packed varints) for a multi-linestring of int (N, 2) arrays."""
    parts = []
    cx = cy = 0
    for ln in lines:
        d = np.diff(ln, axis=0, prepend=np.array([[cx, cy]], dtype=ln.dtype))
        zz = ((d << 1) ^ (d >> 63)).astype(np.uint64)  # zigzag
        n = len(ln)
        cmd = np.empty(2 * n + 2, dtype=np.uint64)
        cmd[0] = 9  # MoveTo, count 1
        cmd[1:3] = zz[0]
        cmd[3] = 2 | ((n - 1) << 3)  # LineTo, count n-1
        cmd[4:] = zz[1:].reshape(-1)
        parts.append(cmd)
        cx, cy = int(ln[-1, 0]), int(ln[-1, 1])
    return varints(np.concatenate(parts))


class FastLayer(mvt.Layer):
    """mvt.Layer with a numpy line encoder (contour tiles have thousands of vertices)."""

    def add_lines_np(self, lines: list[np.ndarray], props: dict) -> None:
        geom = line_geometry(lines)
        body = mvt._packed(2, self._tags(props)) + mvt._key(3, 0) + mvt._varint(2)
        body += mvt._len_delim(4, geom)
        self.features.append(body)


def contour_tile(elev: np.ndarray, buffer: int, size: int, interval: float, index: float,
                 extent: int = mvt.EXTENT, tol: float = 6.0) -> bytes:
    """Contour lines of an elevation patch covering a tile (+ ``buffer`` pixels each side).

    ``elev`` rows run north→south. Returns an (uncompressed) MVT with layer ``contour``
    (``ele`` in metres, ``idx`` = 1 on index contours).
    """
    import contourpy

    lo, hi = float(np.nanmin(elev)), float(np.nanmax(elev))
    first = math.ceil(lo / interval) * interval
    levels = [float(v) for v in np.arange(first, hi + 1e-6, interval) if v != 0]
    if not levels:
        return b""
    gen = contourpy.contour_generator(z=elev, line_type="Separate", quad_as_tri=False)
    scale = extent / size
    raw, owner = [], []
    for li, lev in enumerate(levels):
        for ln in gen.lines(lev):
            if len(ln) >= 2:
                raw.append((ln - buffer + 0.5) * scale)  # grid index → tile units (pixel centres)
                owner.append(li)
    by_level: dict[int, list[np.ndarray]] = {}
    for li, px in zip(owner, simplify_many(raw, tol)):
        seg = np.round(px).astype(np.int64)
        seg = seg[np.r_[True, (np.diff(seg, axis=0) != 0).any(axis=1)]]
        if len(seg) >= 2:
            by_level.setdefault(li, []).append(seg)
    layer = FastLayer("contour")
    for li, segs in sorted(by_level.items()):
        ele = int(round(levels[li]))
        layer.add_lines_np(segs, {"ele": ele, "idx": 1 if ele % index == 0 else 0})
    return mvt.encode_tile([layer])


def spot_heights(elev: np.ndarray, buffer: int, size: int, cell: int, min_prominence: float,
                 extent: int = mvt.EXTENT) -> list[tuple[int, int, int]]:
    """Local summits: the highest point of each ``cell``-pixel block that is also a maximum
    in its 3×3 block neighbourhood and rises ``min_prominence`` m above the block's lowest point."""
    inner = elev[buffer:buffer + size, buffer:buffer + size]
    nb = size // cell
    if nb == 0:
        return []
    blocks = inner[:nb * cell, :nb * cell].reshape(nb, cell, nb, cell).transpose(0, 2, 1, 3).reshape(nb, nb, -1)
    bmax = blocks.max(axis=2)
    bmin = blocks.min(axis=2)
    arg = blocks.argmax(axis=2)
    padded = np.pad(bmax, 1, mode="constant", constant_values=-1e9)
    neigh = np.max(np.stack([padded[1 + dy:1 + dy + nb, 1 + dx:1 + dx + nb]
                             for dy in (-1, 0, 1) for dx in (-1, 0, 1) if dy or dx]), axis=0)
    out = []
    scale = extent / size
    for by, bx in zip(*np.nonzero((bmax > neigh) & (bmax - bmin >= min_prominence) & (bmax > 5))):
        iy, ix = divmod(int(arg[by, bx]), cell)
        y, x = by * cell + iy, bx * cell + ix
        # reject maxima on the patch border (likely a slope continuing outside)
        full = elev[buffer + y - 1:buffer + y + 2, buffer + x - 1:buffer + x + 2]
        if full.shape == (3, 3) and inner[y, x] >= full.max():
            out.append((int((x + 0.5) * scale), int((y + 0.5) * scale), int(round(float(inner[y, x])))))
    return out


def build_contours(grid: Grid, out: Path, bbox=BUILD_BBOX, zooms=CONTOUR_ZOOMS, buffer: int = 8) -> dict:
    t0 = time.time()
    w = MBTilesWriter(out, {
        "name": "contours", "format": "pbf", "type": "overlay", "version": "1",
        "minzoom": str(min(zooms)), "maxzoom": str(max(zooms)), "bounds": _bbox_meta(bbox),
        "attribution": ATTRIBUTION,
        "json": json.dumps({"vector_layers": [
            {"id": "contour", "fields": {"ele": "Number", "idx": "Number"}},
            {"id": "spot", "fields": {"ele": "Number"}}]}),
    })
    stats = {}
    for z, (interval, index) in sorted(zooms.items()):
        tiles = dem.tiles_for_bbox(bbox, z)
        n = nbytes = 0
        size = 128 if z >= 14 else 256  # ≈16 m per sample: about twice the DEM's resolution
        px_deg = 360.0 / (2 ** z * size)
        for (x, y) in tiles:
            lon, lat = dem.tile_pixel_lonlat(z, x, y, size, buffer)
            elev = grid.sample(lon, lat, px_deg)
            if float(elev.max()) - float(elev.min()) < 1 and float(elev.max()) <= 0.5:
                continue  # open sea
            elev = _smooth(elev.astype(np.float64), 2 if z >= 13 else 1)
            data = contour_tile(elev, buffer, size, interval, index, tol=4.0 if z >= 14 else 6.0)
            spots = spot_heights(elev, buffer, size, cell=size // 4 if z >= 13 else 48, min_prominence=interval * 1.5) if z >= 12 else []
            if spots:
                sl = mvt.Layer("spot")
                for sx, sy, ele in spots:
                    sl.add_point(sx, sy, {"ele": ele})
                data += mvt.encode_tile([sl])
            if not data:
                continue
            blob = gzip.compress(data, 9)
            w.put(z, x, y, blob)
            n += 1
            nbytes += len(blob)
        stats[z] = {"tiles": n, "bytes": nbytes}
        print(f"  contours z{z}: {n} tiles, {nbytes / 1e6:.1f} MB ({time.time() - t0:.0f}s)", flush=True)
    w.close()
    return stats


# ============================================================================ terrain-RGB

def build_terrain(grid: Grid, out: Path, bbox=BUILD_BBOX, zooms=TERRAIN_ZOOMS, size: int = 256) -> dict:
    """Terrarium PNG tiles; each zoom is sampled from the grid level matching its pixel size."""
    t0 = time.time()
    w = MBTilesWriter(out, {
        "name": "terrain", "format": "png", "type": "baselayer", "version": "1", "encoding": "terrarium",
        "minzoom": str(min(zooms)), "maxzoom": str(max(zooms)), "bounds": _bbox_meta(bbox), "tileSize": str(size),
        "attribution": ATTRIBUTION,
    })
    stats = {}
    for z in sorted(zooms):
        n = nbytes = 0
        px_deg = 360.0 / (2 ** z * size)
        for (x, y) in dem.tiles_for_bbox(bbox, z):
            lon, lat = dem.tile_pixel_lonlat(z, x, y, size)
            elev = grid.sample(lon, lat, px_deg)
            if float(elev.max()) <= 0.5 and float(elev.min()) >= -0.5:
                continue  # flat sea: nothing to shade
            blob = dem.png_encode(dem.terrarium_encode(elev))
            w.put(z, x, y, blob)
            n += 1
            nbytes += len(blob)
        stats[z] = {"tiles": n, "bytes": nbytes}
        print(f"  terrain z{z}: {n} tiles, {nbytes / 1e6:.1f} MB ({time.time() - t0:.0f}s)", flush=True)
    w.close()
    return stats


# ============================================================================ CLI

def _peak_rss_mb() -> float | None:
    try:
        import resource

        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    except ImportError:
        return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tools.terrain", description=__doc__.split("\n\n")[0])
    ap.add_argument("command", nargs="?", default="build", choices=["build", "download", "mosaic", "contours", "terrain"])
    ap.add_argument("--dem-dir", type=Path, default=DEM_DIR, help="where the DEM tiles are cached (default data/dem)")
    ap.add_argument("--out", type=Path, default=DATA / "tiles", help="output directory for the .mbtiles files")
    ap.add_argument("--bbox", default=",".join(map(str, BUILD_BBOX)), help="min_lon,min_lat,max_lon,max_lat")
    ap.add_argument("--if-missing", action="store_true", help="do nothing when both layers already exist (the DEM never changes)")
    ap.add_argument("--clean", action="store_true", help="delete the DEM cache (~0.9 GB) after a successful build")
    args = ap.parse_args(argv)
    bbox = tuple(float(v) for v in args.bbox.split(","))
    outputs = (args.out / "contours.mbtiles", args.out / "terrain.mbtiles")
    if args.if_missing and args.command == "build" and all(published(p) for p in outputs):
        print("Topographic layers already built – skipping (delete data/tiles/contours*.mbtiles to rebuild).")
        return 0
    t0 = time.time()
    try:
        if args.command in ("build", "contours"):
            import contourpy  # noqa: F401 – fail before downloading anything
        if args.command in ("build", "download"):
            print("Downloading Copernicus GLO-30 DEM tiles (~530 MB once, resumable)…", flush=True)
            download(args.dem_dir)
            if args.command == "download":
                return 0
        grid_path = args.dem_dir / "israel-grid.i2"
        if args.command in ("build", "mosaic") or not grid_path.exists():
            tifs = sorted(args.dem_dir.glob("Copernicus_DSM_COG_10_*_DEM.tif"))
            if not tifs:
                print("No DEM tiles found – run: python -m tools.terrain download")
                return 2
            print("Building the elevation grid…", flush=True)
            build_mosaic(tifs, grid_path)
        grid = Grid(grid_path)
        args.out.mkdir(parents=True, exist_ok=True)
        if args.command in ("build", "contours"):
            print("Building contour lines…", flush=True)
            build_contours(grid, outputs[0], bbox)
        if args.command in ("build", "terrain"):
            print("Building terrain-RGB (hillshade) tiles…", flush=True)
            build_terrain(grid, outputs[1], bbox)
    except ImportError as e:
        print(f"Terrain skipped – missing Python package: {e.name}. Install it with: pip install -r requirements-terrain.txt")
        return 2
    except Exception as e:  # noqa: BLE001
        print(f"Terrain build failed: {e}\nThe map keeps working without the topographic layers.")
        return 1
    if args.clean and args.command == "build":
        for f in args.dem_dir.glob("*"):
            if f.suffix in (".tif", ".i2", ".json", ".part", ".tmp", ".missing"):
                f.unlink(missing_ok=True)
    sizes = {p.name: p.stat().st_size for p in map(published, outputs) if p}
    rss = _peak_rss_mb()
    print(f"Terrain ready in {(time.time() - t0) / 60:.1f} min"
          + (f", peak RAM {rss:.0f} MB" if rss else "") + ": "
          + ", ".join(f"{k} {v / 1e6:.0f} MB" for k, v in sizes.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
