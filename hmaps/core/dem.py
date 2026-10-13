"""Elevation codecs shared by the terrain build (tools/terrain.py) and the API.

* Terrarium encoding (``elevation = R*256 + G + B/256 - 32768``) – the raster-dem
  format MapLibre understands (``encoding: "terrarium"``);
* a tiny PNG encoder/decoder (zlib + numpy only, no Pillow needed at runtime);
* Web-Mercator tile helpers.

Only numpy is required.
"""

from __future__ import annotations

import math
import struct
import zlib

import numpy as np

TILE_SIZE = 256


# ----------------------------------------------------------------------------- terrarium

def terrarium_encode(elev: np.ndarray, step: float = 0.125) -> np.ndarray:
    """float elevations (m) → uint8 RGB array (H, W, 3).

    ``step`` quantises the fractional part (1/8 m by default): it keeps hillshading
    smooth while making the PNGs compress much better than full 1/256 m precision.
    """
    e = np.nan_to_num(np.asarray(elev, dtype=np.float64), nan=0.0)
    v = np.round((np.clip(e, -32768, 32767) + 32768.0) / step) * step
    r = np.floor(v / 256.0)
    g = np.floor(v - r * 256.0)
    b = np.round((v - r * 256.0 - g) * 256.0)
    out = np.empty(e.shape + (3,), dtype=np.uint8)
    out[..., 0] = np.clip(r, 0, 255)
    out[..., 1] = np.clip(g, 0, 255)
    out[..., 2] = np.clip(b, 0, 255)
    return out


def terrarium_decode(rgb: np.ndarray) -> np.ndarray:
    """uint8 RGB(A) array → float32 elevations (m)."""
    a = np.asarray(rgb, dtype=np.float32)
    return a[..., 0] * 256.0 + a[..., 1] + a[..., 2] / 256.0 - 32768.0


# ----------------------------------------------------------------------------- PNG

_PNG_SIG = b"\x89PNG\r\n\x1a\n"


def _chunk(tag: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


def png_encode(rgb: np.ndarray, level: int = 9) -> bytes:
    """Encode an 8-bit RGB image. Tries the "Up" and "Sub" filters and keeps the smaller.

    Only filters 0-2 are used, so ``png_decode`` can undo them with vectorised numpy.
    """
    a = np.ascontiguousarray(rgb, dtype=np.uint8)
    h, w, c = a.shape
    color_type = {1: 0, 3: 2, 4: 6}[c]
    flat = a.reshape(h, w * c).astype(np.int16)
    up = flat.copy()
    up[1:] -= flat[:-1]
    sub = flat.copy()
    sub[:, c:] -= flat[:, :-c]
    best = None
    cands = []
    for ftype, data in ((2, up), (1, sub)):
        raw = np.empty((h, w * c + 1), dtype=np.uint8)
        raw[:, 0] = ftype
        raw[:, 1:] = (data & 0xFF).astype(np.uint8)
        if ftype == 2:
            raw[0, 0] = 1  # the first row has nothing above it: use Sub instead
            r0 = flat[0].copy()
            r0[c:] -= flat[0, :-c]
            raw[0, 1:] = (r0 & 0xFF).astype(np.uint8)
        cands.append(raw.tobytes())
    # pick the filter with a fast trial compression, then compress that one properly
    best_raw = min(cands, key=lambda r: len(zlib.compress(r, 1))) if len(cands) > 1 else cands[0]
    best = zlib.compress(best_raw, level)
    ihdr = struct.pack(">IIBBBBB", w, h, 8, color_type, 0, 0, 0)
    return _PNG_SIG + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", best) + _chunk(b"IEND", b"")


def png_decode(data: bytes) -> np.ndarray:
    """Decode an 8-bit non-interlaced gray/RGB/RGBA PNG to (H, W, C) uint8."""
    if data[:8] != _PNG_SIG:
        raise ValueError("not a PNG")
    i = 8
    idat = []
    w = h = ctype = 0
    while i < len(data):
        ln, tag = struct.unpack(">I4s", data[i:i + 8])
        body = data[i + 8:i + 8 + ln]
        i += 12 + ln
        if tag == b"IHDR":
            w, h, depth, ctype, _, _, interlace = struct.unpack(">IIBBBBB", body)
            if depth != 8 or interlace:
                raise ValueError("unsupported PNG (need 8-bit, non-interlaced)")
        elif tag == b"IDAT":
            idat.append(body)
        elif tag == b"IEND":
            break
    c = {0: 1, 2: 3, 4: 2, 6: 4}[ctype]
    raw = np.frombuffer(zlib.decompress(b"".join(idat)), dtype=np.uint8).reshape(h, w * c + 1)
    filters = raw[:, 0]
    rows = raw[:, 1:].astype(np.uint16)
    out = np.empty((h, w * c), dtype=np.uint8)
    prev = np.zeros(w * c, dtype=np.uint16)
    for y in range(h):
        f = filters[y]
        line = rows[y]
        if f == 0:
            cur = line
        elif f == 1:
            cur = np.cumsum(line.reshape(w, c), axis=0, dtype=np.uint32).reshape(-1) & 0xFF
        elif f == 2:
            cur = (line + prev) & 0xFF
        elif f == 3:
            cur = line.copy()
            for x in range(w * c):
                left = cur[x - c] if x >= c else 0
                cur[x] = (cur[x] + ((int(left) + int(prev[x])) >> 1)) & 0xFF
        elif f == 4:
            cur = line.copy()
            for x in range(w * c):
                a_ = int(cur[x - c]) if x >= c else 0
                b_ = int(prev[x])
                c_ = int(prev[x - c]) if x >= c else 0
                p = a_ + b_ - c_
                pa, pb, pc = abs(p - a_), abs(p - b_), abs(p - c_)
                pred = a_ if pa <= pb and pa <= pc else (b_ if pb <= pc else c_)
                cur[x] = (int(cur[x]) + pred) & 0xFF
        else:
            raise ValueError("bad PNG filter")
        cur = np.asarray(cur, dtype=np.uint16)
        out[y] = cur
        prev = cur
    return out.reshape(h, w, c)


# ----------------------------------------------------------------------------- tiles

def lonlat_to_tile_f(lon: float, lat: float, z: int) -> tuple[float, float]:
    """Fractional tile coordinates (x, y) of a point at zoom z."""
    n = 2 ** z
    lat = max(-85.0511, min(85.0511, lat))
    x = (lon + 180.0) / 360.0 * n
    r = math.radians(lat)
    y = (1.0 - math.log(math.tan(r) + 1.0 / math.cos(r)) / math.pi) / 2.0 * n
    return x, y


def tile_pixel_lonlat(z: int, x: int, y: int, size: int, buffer: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """lon (W,) and lat (H,) of pixel centres of a tile (plus ``buffer`` pixels each side)."""
    n = 2 ** z
    idx = (np.arange(-buffer, size + buffer, dtype=np.float64) + 0.5) / size
    lon = (x + idx) / n * 360.0 - 180.0
    lat = np.degrees(np.arctan(np.sinh(np.pi * (1 - 2 * (y + idx) / n))))
    return lon, lat


def tiles_for_bbox(bbox: tuple[float, float, float, float], z: int) -> list[tuple[int, int]]:
    min_lon, min_lat, max_lon, max_lat = bbox
    x0, y0 = lonlat_to_tile_f(min_lon, max_lat, z)
    x1, y1 = lonlat_to_tile_f(max_lon, min_lat, z)
    return [(x, y) for x in range(int(x0), int(x1) + 1) for y in range(int(y0), int(y1) + 1)]
