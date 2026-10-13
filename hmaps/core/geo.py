"""Small, dependency-free geometry helpers (WGS84)."""

from __future__ import annotations

import math
from typing import Iterable, Sequence

EARTH_RADIUS_M = 6_371_008.8

#: Bounding box covering Israel incl. Golan, Judea & Samaria and Eilat.
ISRAEL_BBOX = (34.15, 29.40, 35.95, 33.45)  # min_lon, min_lat, max_lon, max_lat

Coord = tuple[float, float]  # (lon, lat)


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in meters."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def in_israel(lat: float, lon: float) -> bool:
    min_lon, min_lat, max_lon, max_lat = ISRAEL_BBOX
    return min_lat <= lat <= max_lat and min_lon <= lon <= max_lon


def bbox_around(lat: float, lon: float, radius_m: float) -> tuple[float, float, float, float]:
    """(min_lon, min_lat, max_lon, max_lat) of a circle's bounding square."""
    dlat = radius_m / 111_320.0
    dlon = radius_m / (111_320.0 * max(0.01, math.cos(math.radians(lat))))
    return lon - dlon, lat - dlat, lon + dlon, lat + dlat


def bbox_of(coords: Iterable[Coord]) -> tuple[float, float, float, float]:
    xs, ys = [], []
    for x, y in coords:
        xs.append(x)
        ys.append(y)
    return min(xs), min(ys), max(xs), max(ys)


def ring_area_centroid(ring: Sequence[Coord]) -> tuple[float, float, float]:
    """Signed area (deg²) and centroid of a closed ring."""
    a = cx = cy = 0.0
    n = len(ring)
    for i in range(n - 1):
        x0, y0 = ring[i]
        x1, y1 = ring[i + 1]
        cross = x0 * y1 - x1 * y0
        a += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    a *= 0.5
    if abs(a) < 1e-18:
        xs = [p[0] for p in ring]
        ys = [p[1] for p in ring]
        return 0.0, sum(xs) / len(xs), sum(ys) / len(ys)
    return a, cx / (6 * a), cy / (6 * a)


def polygon_centroid(polygons: Sequence[Sequence[Sequence[Coord]]]) -> Coord:
    """Centroid of a (multi)polygon given as [[outer, hole...], ...]."""
    tot = sx = sy = 0.0
    for rings in polygons:
        for idx, ring in enumerate(rings):
            if len(ring) < 4:
                continue
            a, cx, cy = ring_area_centroid(ring)
            a = abs(a) if idx == 0 else -abs(a)
            tot += a
            sx += cx * a
            sy += cy * a
    if abs(tot) < 1e-18:
        pts = [p for rings in polygons for ring in rings for p in ring]
        return sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
    return sx / tot, sy / tot


def point_in_ring(x: float, y: float, ring: Sequence[Coord]) -> bool:
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-30) + xi:
            inside = not inside
        j = i
    return inside


def point_in_polygons(x: float, y: float, polygons: Sequence[Sequence[Sequence[Coord]]]) -> bool:
    for rings in polygons:
        if rings and point_in_ring(x, y, rings[0]) and not any(point_in_ring(x, y, h) for h in rings[1:]):
            return True
    return False


def representative_point(polygons: Sequence[Sequence[Sequence[Coord]]]) -> Coord:
    """A point guaranteed to be inside the polygon when possible.

    The centroid is used when it lies inside; otherwise (U shaped areas) the
    middle of the widest horizontal chord through the centroid latitude.
    """
    cx, cy = polygon_centroid(polygons)
    if point_in_polygons(cx, cy, polygons):
        return cx, cy
    best: tuple[float, Coord] | None = None
    for rings in polygons:
        xs: list[float] = []
        for ring in rings:
            for i in range(len(ring) - 1):
                (x0, y0), (x1, y1) = ring[i], ring[i + 1]
                if (y0 > cy) != (y1 > cy):
                    xs.append(x0 + (cy - y0) * (x1 - x0) / (y1 - y0))
        xs.sort()
        for i in range(0, len(xs) - 1, 2):
            w = xs[i + 1] - xs[i]
            if best is None or w > best[0]:
                best = (w, ((xs[i] + xs[i + 1]) / 2, cy))
    if best:
        return best[1]
    return polygons[0][0][0]


def line_length_m(coords: Sequence[Coord]) -> float:
    return sum(haversine(coords[i][1], coords[i][0], coords[i + 1][1], coords[i + 1][0]) for i in range(len(coords) - 1))


def line_midpoint(coords: Sequence[Coord]) -> Coord:
    """Point halfway along a linestring."""
    if len(coords) == 1:
        return coords[0]
    total = line_length_m(coords)
    half = total / 2
    acc = 0.0
    for i in range(len(coords) - 1):
        (x0, y0), (x1, y1) = coords[i], coords[i + 1]
        seg = haversine(y0, x0, y1, x1)
        if acc + seg >= half and seg > 0:
            f = (half - acc) / seg
            return x0 + (x1 - x0) * f, y0 + (y1 - y0) * f
        acc += seg
    return coords[-1]


def nearest_point_on_segment(px: float, py: float, a: Coord, b: Coord) -> tuple[Coord, float]:
    """Closest point to P on segment AB and the distance in meters."""
    kx = math.cos(math.radians(py))
    ax, ay, bx, by = a[0] * kx, a[1], b[0] * kx, b[1]
    x, y = px * kx, py
    dx, dy = bx - ax, by - ay
    L = dx * dx + dy * dy
    t = 0.0 if L == 0 else max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / L))
    qx, qy = (ax + t * dx) / kx, ay + t * dy
    return (qx, qy), haversine(py, px, qy, qx)


def simplify(coords: Sequence[Coord], tolerance_m: float) -> list[Coord]:
    """Douglas–Peucker simplification (tolerance in meters); keeps both endpoints."""
    n = len(coords)
    if n <= 2 or tolerance_m <= 0:
        return list(coords)
    kx = math.cos(math.radians(coords[0][1])) * 111_320.0
    ky = 110_574.0
    pts = [(x * kx, y * ky) for x, y in coords]
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    tol2 = tolerance_m * tolerance_m
    while stack:
        a, b = stack.pop()
        ax, ay = pts[a]
        bx, by = pts[b]
        dx, dy = bx - ax, by - ay
        L = dx * dx + dy * dy
        best, idx = -1.0, -1
        for i in range(a + 1, b):
            px, py = pts[i]
            if L == 0:
                d = (px - ax) ** 2 + (py - ay) ** 2
            else:
                t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L))
                d = (px - ax - t * dx) ** 2 + (py - ay - t * dy) ** 2
            if d > best:
                best, idx = d, i
        if idx >= 0 and best > tol2:
            keep[idx] = True
            stack.append((a, idx))
            stack.append((idx, b))
    return [c for c, k in zip(coords, keep) if k]


def distance_to_line(px: float, py: float, coords: Sequence[Coord]) -> tuple[float, Coord]:
    best = (float("inf"), coords[0])
    for i in range(len(coords) - 1):
        q, d = nearest_point_on_segment(px, py, coords[i], coords[i + 1])
        if d < best[0]:
            best = (d, q)
    return best


# --- polyline encoding (Google, precision 5) for compact storage -------------------

def encode_polyline(coords: Sequence[Coord], precision: int = 5) -> str:
    factor = 10 ** precision
    out = []
    plat = plon = 0
    for lon, lat in coords:
        ilat, ilon = int(round(lat * factor)), int(round(lon * factor))
        for v in (ilat - plat, ilon - plon):
            v = ~(v << 1) if v < 0 else v << 1
            while v >= 0x20:
                out.append(chr((0x20 | (v & 0x1F)) + 63))
                v >>= 5
            out.append(chr(v + 63))
        plat, plon = ilat, ilon
    return "".join(out)


def decode_polyline(s: str, precision: int = 5) -> list[Coord]:
    factor = 10 ** precision
    coords: list[Coord] = []
    i = lat = lon = 0
    while i < len(s):
        vals = []
        for _ in range(2):
            shift = result = 0
            while True:
                b = ord(s[i]) - 63
                i += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            vals.append(~(result >> 1) if result & 1 else result >> 1)
        lat += vals[0]
        lon += vals[1]
        coords.append((lon / factor, lat / factor))
    return coords


# --- web mercator tiles ------------------------------------------------------------

def lonlat_to_tile(lon: float, lat: float, z: int) -> tuple[int, int]:
    n = 2 ** z
    x = int((lon + 180.0) / 360.0 * n)
    lat_r = math.radians(max(-85.0511, min(85.0511, lat)))
    y = int((1.0 - math.asinh(math.tan(lat_r)) / math.pi) / 2.0 * n)
    return max(0, min(n - 1, x)), max(0, min(n - 1, y))


def tile_bounds(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    """(min_lon, min_lat, max_lon, max_lat) of an XYZ tile."""
    n = 2 ** z

    def lat(yy: float) -> float:
        return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * yy / n))))

    return x / n * 360.0 - 180.0, lat(y + 1), (x + 1) / n * 360.0 - 180.0, lat(y)


def lonlat_to_tile_pixel(lon: float, lat: float, z: int, x: int, y: int, extent: int = 4096) -> tuple[int, int]:
    """Integer coordinates of a point inside tile (z, x, y) on a 0..extent grid."""
    n = 2 ** z
    fx = (lon + 180.0) / 360.0 * n
    lat_r = math.radians(max(-85.0511, min(85.0511, lat)))
    fy = (1.0 - math.asinh(math.tan(lat_r)) / math.pi) / 2.0 * n
    return int(round((fx - x) * extent)), int(round((fy - y) * extent))
