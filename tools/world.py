"""Build the low-zoom world map (web/world/world.json) from Natural Earth.

    python -m tools.world            (or scripts/windows/build-world.cmd)

When the map is zoomed out it shows the whole globe – countries with their borders and
Hebrew names, capitals and big cities, oceans and seas – under the detailed map of Israel.
Natural Earth is public domain (https://www.naturalearthdata.com).

The result is one small GeoJSON file (~1–2 MB) that ships with the app (web/world/),
so nothing has to be downloaded by the user. It only needs to be rebuilt when Natural
Earth publishes a new version; commit the file after building it.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "web" / "world" / "world.json"
UA = {"User-Agent": "HebrewMaps-world/1.0"}
MIRRORS = (
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/{name}.geojson",
    "https://cdn.jsdelivr.net/gh/nvkelso/natural-earth-vector@master/geojson/{name}.geojson",
)
SOURCES = {
    "countries": "ne_50m_admin_0_countries",
    "places": "ne_50m_populated_places",
    "marine": "ne_110m_geography_marine_polys",
    "lakes": "ne_50m_lakes",
}
#: drawn by the detailed map of Israel itself
SKIP_LABELS = {"IL", "PS"}
TOLERANCE = 0.03  # degrees (~3 km) – plenty for zoom ≤ 7
DIGITS = 3


def download(name: str, cache: Path | None) -> dict:
    if cache is not None:
        f = cache / f"{name}.geojson"
        if f.exists():
            return json.loads(f.read_text(encoding="utf-8"))
    last = None
    for tpl in MIRRORS:
        url = tpl.format(name=name)
        try:
            print(f"  מוריד {url}")
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120) as r:
                raw = r.read()
            data = json.loads(raw)
            if cache is not None:
                cache.mkdir(parents=True, exist_ok=True)
                (cache / f"{name}.geojson").write_bytes(raw)
            return data
        except Exception as e:  # noqa: BLE001 - try the next mirror
            last = e
            print(f"    נכשל: {e}")
    raise SystemExit(f"לא ניתן להוריד את {name}: {last}\n"
                     "ברשת מסוננת יש לאשר את raw.githubusercontent.com או cdn.jsdelivr.net")


def prop(p: dict, *keys, default=None):
    """Case-insensitive property lookup (Natural Earth mixes NAME_HE / name_he between files)."""
    low = {k.lower(): v for k, v in p.items()}
    for k in keys:
        v = low.get(k.lower())
        if v not in (None, "", -99, "-99"):
            return v
    return default


# ---------------------------------------------------------------------------- geometry

def _dp(points: list[list[float]], tol: float) -> list[list[float]]:
    """Douglas–Peucker (iterative)."""
    if len(points) < 3:
        return points
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    t2 = tol * tol
    while stack:
        a, b = stack.pop()
        ax, ay = points[a]
        bx, by = points[b]
        dx, dy = bx - ax, by - ay
        L = dx * dx + dy * dy
        best, idx = -1.0, -1
        for i in range(a + 1, b):
            px, py = points[i]
            if L == 0:
                d = (px - ax) ** 2 + (py - ay) ** 2
            else:
                t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L))
                d = (px - ax - t * dx) ** 2 + (py - ay - t * dy) ** 2
            if d > best:
                best, idx = d, i
        if best > t2 and idx > 0:
            keep[idx] = True
            stack += [(a, idx), (idx, b)]
    return [p for p, k in zip(points, keep) if k]


def _ring(ring: list, tol: float) -> list | None:
    pts = _dp([[float(x), float(y)] for x, y, *_ in ring], tol)
    out: list[list[float]] = []
    for x, y in pts:
        q = [round(x, DIGITS), round(y, DIGITS)]
        if not out or q != out[-1]:
            out.append(q)
    if len(out) < 4:
        return None
    if out[0] != out[-1]:
        out.append(out[0])
    return out


def simplify_polygons(geom: dict, tol: float = TOLERANCE) -> dict | None:
    polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
    out = []
    for poly in polys:
        rings = [r for r in (_ring(r, tol) for r in poly) if r]
        if rings and len(rings[0]) >= 4:
            out.append(rings)
    if not out:
        return None
    return {"type": "MultiPolygon", "coordinates": out} if len(out) > 1 else {"type": "Polygon", "coordinates": out[0]}


def label_point(geom: dict) -> list[float]:
    """A point inside the largest polygon (vertex average of its outer ring, good enough for labels)."""
    polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]

    def area(r):
        return abs(sum(x1 * y2 - x2 * y1 for (x1, y1, *_), (x2, y2, *_) in zip(r, r[1:]))) / 2

    ring = max((p[0] for p in polys), key=area)
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    return [round(sum(xs) / len(xs), 3), round(sum(ys) / len(ys), 3)]


def point(lon, lat) -> dict:
    return {"type": "Point", "coordinates": [round(float(lon), 3), round(float(lat), 3)]}


# ---------------------------------------------------------------------------- build

def build(cache: Path | None = None) -> dict:
    feats = []
    countries = download(SOURCES["countries"], cache)
    for f in countries["features"]:
        p = f["properties"]
        g = simplify_polygons(f["geometry"]) if f.get("geometry") else None
        if not g:
            continue
        iso = prop(p, "ISO_A2_EH", "ISO_A2", default="")
        name = prop(p, "NAME_HE", "NAME", default="")
        feats.append({"type": "Feature", "properties": {"l": "country", "c": int(prop(p, "MAPCOLOR7", default=1) or 1)},
                      "geometry": g})
        if name and iso not in SKIP_LABELS:
            lx, ly = prop(p, "LABEL_X"), prop(p, "LABEL_Y")
            geom = point(lx, ly) if lx is not None and ly is not None else {"type": "Point", "coordinates": label_point(g)}
            feats.append({"type": "Feature", "properties": {"l": "clabel", "n": name,
                                                            "r": float(prop(p, "MIN_LABEL", "LABELRANK", default=5))},
                          "geometry": geom})
    lakes = download(SOURCES["lakes"], cache)
    for f in lakes["features"]:
        p = f["properties"]
        if float(prop(p, "scalerank", default=9)) > 3:
            continue
        g = simplify_polygons(f["geometry"], TOLERANCE / 2) if f.get("geometry") else None
        if g:
            feats.append({"type": "Feature", "properties": {"l": "lake"}, "geometry": g})
    marine = download(SOURCES["marine"], cache)
    for f in marine["features"]:
        p = f["properties"]
        name = prop(p, "name_he", "NAME_HE", "name", "NAME")
        if not name or not f.get("geometry"):
            continue
        cla = str(prop(p, "featurecla", default="")).lower()
        feats.append({"type": "Feature", "properties": {"l": "marine", "n": name, "o": 1 if cla == "ocean" else 0,
                                                        "r": float(prop(p, "scalerank", default=3))},
                      "geometry": {"type": "Point", "coordinates": label_point(f["geometry"])}})
    places = download(SOURCES["places"], cache)
    for f in places["features"]:
        p = f["properties"]
        name = prop(p, "NAME_HE", "name_he", "NAME", "name")
        iso = prop(p, "ISO_A2", "SOV_A3", default="")
        if not name or iso in SKIP_LABELS or prop(p, "ADM0_A3", default="") in ("ISR", "PSX", "PSE"):
            continue
        rank = float(prop(p, "SCALERANK", "scalerank", default=8))
        cap = int(prop(p, "ADM0CAP", "adm0cap", default=0) or 0)
        if rank > 6 and not cap:
            continue
        g = f["geometry"]["coordinates"]
        feats.append({"type": "Feature", "properties": {"l": "place", "n": name, "r": rank, "cap": cap},
                      "geometry": point(g[0], g[1])})
    return {"type": "FeatureCollection", "attribution": "Natural Earth (public domain)", "features": feats}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--cache", default=None, help="folder for the downloaded Natural Earth files (reused)")
    args = ap.parse_args(argv)
    data = build(Path(args.cache) if args.cache else None)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(out)
    n = {k: sum(1 for f in data["features"] if f["properties"]["l"] == k) for k in ("country", "clabel", "place", "marine", "lake")}
    print(f"נכתב {out} ({out.stat().st_size / 1e6:.1f} MB): {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
