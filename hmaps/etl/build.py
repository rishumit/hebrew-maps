"""Turning exported OSM features into the application database.

Stages (all in one SQLite file):

1. **ingest** – stream features, classify them into places, admin areas,
   POIs, addresses and street segments;
2. **assign cities** – every POI / address / street gets its settlement from
   ``addr:city`` or a point-in-polygon test against municipal boundaries,
   falling back to the nearest settlement;
3. **deduplicate** POIs (the same shop mapped as a node and a building);
4. **merge** street segments into streets per settlement;
5. **write** final tables + spatial (R*Tree) indexes.

Search indexing, enrichment and the quality report are separate modules.
"""

from __future__ import annotations

import json
import logging
import math
import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from ..core import categories, geo, hours
from ..core.hebrew import normalize
from ..core.text import format_phone, normalize_url
from .osm import osm_ref
from .spatial import AreaIndex, NearestPlace
from .trails import TrailCollector

log = logging.getLogger(__name__)

PLACE_KINDS = {
    "city": 1.0, "town": 0.85, "village": 0.6, "hamlet": 0.45, "isolated_dwelling": 0.25,
    "suburb": 0.55, "quarter": 0.45, "neighbourhood": 0.45, "locality": 0.3, "borough": 0.55,
}
#: radius (m) within which a point is considered to belong to a settlement node
PLACE_RADIUS = {"city": 5000, "town": 3000, "village": 1500, "hamlet": 800, "isolated_dwelling": 300, "borough": 3000}
SETTLEMENT_KINDS = {"city", "town", "village", "hamlet", "isolated_dwelling"}
NEIGHBOURHOOD_KINDS = {"suburb", "quarter", "neighbourhood", "borough"}

STREET_HIGHWAYS = {
    "motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential",
    "living_street", "pedestrian", "service", "road", "footway", "path", "cycleway", "steps",
    "motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link", "track",
}
STREET_IMPORTANCE = {"motorway": 0.75, "trunk": 0.7, "primary": 0.65, "secondary": 0.6, "tertiary": 0.55}

EXTRA_KEYS = (
    "cuisine", "operator", "brand", "internet_access", "fee", "takeaway", "delivery",
    "outdoor_seating", "capacity", "ele", "denomination", "nusach", "network", "level",
    "description", "description:he", "toilets:wheelchair", "smoking", "air_conditioning",
    "charge", "stars", "rooms", "beds", "isced:level", "school", "healthcare:speciality",
    "dispensing", "drive_through", "ref", "station", "railway:ref", "route_ref", "shelter", "bench",
    "protect_class", "protection_title", "drinking_water", "access", "shelter_type", "information", "intermittent",
)

#: open ways that become (merged) line POIs – kept in sync with osm.LINE_FILTERS / osm_py
LINE_TAGS: dict[str, tuple[str, ...]] = {
    "waterway": ("stream", "wadi", "river"),
    "natural": ("ridge", "arete", "valley", "gorge", "cliff"),
}
#: segments of the same name closer than this (m) belong to the same stream / ridge
LINE_JOIN_M = 250.0


def is_line_feature(tags) -> bool:
    """Named stream / ridge / valley / cliff way (any engine)."""
    if not (tags.get("name") or tags.get("name:he")):
        return False
    return any(tags.get(k) in vals for k, vals in LINE_TAGS.items())


def _round_geom(geom: dict, tolerance_m: float) -> dict:
    """Simplified, 5-decimal copy of a (Multi)Polygon / (Multi)LineString."""
    def ring(r):
        pts = geo.simplify([tuple(p) for p in r], tolerance_m)
        return [[round(x, 5), round(y, 5)] for x, y in pts]
    t = geom["type"]
    c = geom["coordinates"]
    if t == "Polygon":
        return {"type": "MultiPolygon", "coordinates": [[ring(r) for r in c]]}
    if t == "MultiPolygon":
        return {"type": "MultiPolygon", "coordinates": [[ring(r) for r in poly] for poly in c]}
    if t == "LineString":
        return {"type": "MultiLineString", "coordinates": [ring(c)]}
    return {"type": "MultiLineString", "coordinates": [ring(line) for line in c]}


_LEADING_INT = re.compile(r"^\s*(\d+)")


def _he_name(tags: dict) -> str:
    return (tags.get("name:he") or tags.get("name") or "").strip()


def _alt_names(tags: dict, primary: str) -> list[str]:
    out = []
    for k in ("name", "name:he", "alt_name", "alt_name:he", "old_name", "old_name:he", "short_name",
              "short_name:he", "official_name", "official_name:he", "loc_name", "brand", "name:en", "name:ar"):
        v = tags.get(k)
        if v:
            for part in v.split(";"):
                part = part.strip()
                if part and part != primary and part not in out:
                    out.append(part)
    return out


def _polygons(geom: dict) -> list[list[list[tuple[float, float]]]] | None:
    t = geom.get("type")
    if t == "Polygon":
        return [[[tuple(p) for p in ring] for ring in geom["coordinates"]]]
    if t == "MultiPolygon":
        return [[[tuple(p) for p in ring] for ring in poly] for poly in geom["coordinates"]]
    return None


def representative(geom: dict) -> tuple[float, float] | None:
    """(lon, lat) representative point for any geometry."""
    t = geom.get("type")
    c = geom.get("coordinates")
    if not c:
        return None
    if t == "Point":
        return c[0], c[1]
    if t == "LineString":
        return geo.line_midpoint([tuple(p) for p in c])
    if t == "MultiLineString":
        longest = max(c, key=len)
        return geo.line_midpoint([tuple(p) for p in longest])
    polys = _polygons(geom)
    if polys:
        return geo.representative_point(polys)
    return None


def _bbox_of_geom(geom: dict) -> tuple[float, float, float, float] | None:
    polys = _polygons(geom)
    if polys:
        return geo.bbox_of(p for poly in polys for p in poly[0])
    if geom.get("type") == "LineString":
        return geo.bbox_of(tuple(p) for p in geom["coordinates"])
    return None


@dataclass
class Stats:
    features: int = 0
    pois: Counter = field(default_factory=Counter)
    skipped_outside: int = 0
    duplicates: int = 0
    addresses: int = 0
    segments: int = 0
    places: int = 0
    areas: int = 0
    city_from_tag: int = 0
    city_from_polygon: int = 0
    city_from_nearest: int = 0
    city_missing: int = 0
    trails: int = 0

    def as_dict(self) -> dict:
        d = dict(self.__dict__)
        d["pois"] = dict(self.pois)
        return d


class Builder:
    def __init__(self, con: sqlite3.Connection, bbox: tuple[float, float, float, float] = geo.ISRAEL_BBOX):
        self.con = con
        self.bbox = bbox
        self.stats = Stats()
        self.pois: list[dict[str, Any]] = []
        self.places: list[dict[str, Any]] = []
        self.areas: list[dict[str, Any]] = []  # admin boundaries + settlement polygons
        self.addr_rows: list[tuple] = []
        self.con.execute(
            "CREATE TEMP TABLE stg_seg (name TEXT, name_en TEXT, name_norm TEXT, highway TEXT, city TEXT,"
            " lon REAL, lat REAL, min_lon REAL, min_lat REAL, max_lon REAL, max_lat REAL, length REAL, geom TEXT)"
        )
        self._seg_buf: list[tuple] = []
        self.now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        #: marked trails (route relations); engines register relations before streaming ways
        self.routes = TrailCollector()
        #: named stream / ridge / … segments, merged per name in finish()
        self.line_segs: dict[tuple[str, str], list[dict]] = defaultdict(list)

    # ------------------------------------------------------------------ ingest
    def _inside(self, lon: float, lat: float) -> bool:
        minx, miny, maxx, maxy = self.bbox
        return minx <= lon <= maxx and miny <= lat <= maxy

    def ingest_feature(self, f: dict) -> None:
        self.stats.features += 1
        props = dict(f.get("properties") or {})
        ref = osm_ref(props, f)
        geom = f.get("geometry") or {}
        tags = {k: str(v) for k, v in props.items()}
        if not tags:
            return

        if tags.get("boundary") == "administrative" and _polygons(geom):
            self._ingest_admin(ref, tags, geom)
            # boundaries may also carry place=* – fall through only for that
            if "place" not in tags:
                return

        if tags.get("place") in PLACE_KINDS:
            self._ingest_place(ref, tags, geom)
            return

        cls = categories.classify(tags)
        if cls and cls[1] in categories.LINE_SUBS and geom.get("type") in ("LineString", "MultiLineString"):
            self._add_line_segment(ref, tags, geom, cls)
        elif cls and cls[1] == "stream" and _polygons(geom):
            return  # water areas of a stream: the line is what people look for
        elif cls:
            self._ingest_poi(ref, tags, geom, cls)  # its address is added after de-duplication
        elif tags.get("addr:housenumber") and (tags.get("addr:street") or tags.get("addr:place")):
            self._ingest_address(tags, geom)

    def ingest_line(self, f: dict) -> None:
        """Open ways that are not streets: trail members and named streams / ridges / valleys."""
        props = f.get("properties") or {}
        typ, oid = props.get("@type"), props.get("@id")
        geom = f.get("geometry") or {}
        if geom.get("type") != "LineString":
            return
        coords = geom.get("coordinates") or []
        if typ == "way" and oid is not None and int(oid) in self.routes.wanted():
            self.routes.add_way(int(oid), coords)
        if len(coords) < 2 or coords[0] == coords[-1] or not is_line_feature(props):
            return  # closed ways arrive as areas through ingest_feature
        self.ingest_feature(f)

    def _add_line_segment(self, ref: str, tags: dict, geom: dict, cls: tuple[str, str]) -> None:
        lines = [geom["coordinates"]] if geom["type"] == "LineString" else geom["coordinates"]
        name = _he_name(tags)
        for line in lines:
            coords = [tuple(p) for p in line]
            if len(coords) >= 2:
                self.line_segs[(cls[1], normalize(name))].append({"ref": ref, "tags": tags, "cls": cls, "coords": coords})

    def ingest_street(self, f: dict) -> None:
        props = dict(f.get("properties") or {})
        osm_ref(props, f)
        tags = {k: str(v) for k, v in props.items()}
        hw = tags.get("highway")
        name = _he_name(tags)
        if hw not in STREET_HIGHWAYS or not name:
            return
        geom = f.get("geometry") or {}
        if geom.get("type") == "LineString":
            coords = [tuple(p) for p in geom["coordinates"]]
        elif geom.get("type") in ("Polygon", "MultiPolygon"):
            polys = _polygons(geom)
            coords = polys[0][0] if polys else []
        else:
            return
        if len(coords) < 2:
            return
        lon, lat = geo.line_midpoint(coords)
        if not self._inside(lon, lat):
            return
        minx, miny, maxx, maxy = geo.bbox_of(coords)
        self._seg_buf.append((
            name, tags.get("name:en"), normalize(name), hw, tags.get("addr:city"),
            lon, lat, minx, miny, maxx, maxy, geo.line_length_m(coords), geo.encode_polyline(coords),
        ))
        self.stats.segments += 1
        if len(self._seg_buf) >= 5000:
            self._flush_segments()

    def _flush_segments(self) -> None:
        if self._seg_buf:
            self.con.executemany("INSERT INTO stg_seg VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", self._seg_buf)
            self._seg_buf.clear()

    def _ingest_admin(self, ref: str, tags: dict, geom: dict) -> None:
        name = _he_name(tags)
        try:
            level = int(tags.get("admin_level", "0"))
        except ValueError:
            return
        if not name or level < 4 or level > 10:
            return
        polys = _polygons(geom)
        bbox = _bbox_of_geom(geom)
        if not polys or not bbox:
            return
        self.areas.append({"osm": ref, "name": name, "name_en": tags.get("name:en"), "level": level,
                           "kind": "admin", "polygons": polys, "bbox": bbox})
        self.stats.areas += 1

    def _ingest_place(self, ref: str, tags: dict, geom: dict) -> None:
        name = _he_name(tags)
        kind = tags["place"]
        pt = representative(geom)
        if not name or not pt or not self._inside(*pt):
            return
        try:
            pop = int(re.sub(r"[^\d]", "", tags.get("population", "")) or 0) or None
        except ValueError:
            pop = None
        bbox = _bbox_of_geom(geom)
        self.places.append({
            "osm": ref, "name": name, "name_en": tags.get("name:en"), "alt": _alt_names(tags, name),
            "kind": kind, "population": pop, "lon": pt[0], "lat": pt[1], "bbox": bbox,
            "wikidata": tags.get("wikidata"),
        })
        polys = _polygons(geom)
        if polys and bbox:
            level = 8 if kind in SETTLEMENT_KINDS else 10
            self.areas.append({"osm": ref, "name": name, "name_en": tags.get("name:en"), "level": level,
                               "kind": kind, "polygons": polys, "bbox": bbox})

    def _ingest_poi(self, ref: str, tags: dict, geom: dict, cls: tuple[str, str]) -> None:
        pt = representative(geom)
        if not pt:
            return
        if not self._inside(*pt):
            self.stats.skipped_outside += 1
            return
        name = _he_name(tags) or None
        oh = tags.get("opening_hours")
        week = hours.parse(oh) if oh else None
        extra = {k: tags[k] for k in EXTRA_KEYS if tags.get(k)}
        alt = _alt_names(tags, name or "")
        if alt:
            extra["alt_names"] = alt
        bbox = _bbox_of_geom(geom)
        if bbox and geom.get("type") in ("Polygon", "MultiPolygon"):
            extra["bbox"] = [round(v, 6) for v in bbox]
        shape = None
        if cls[1] in categories.SHAPE_SUBS:
            src = categories.inpa_source(tags)
            if src:
                extra["inpa"] = src
            if _polygons(geom):
                shape = _round_geom(geom, 8.0)
        wikipedia = tags.get("wikipedia") or (f"he:{tags['wikipedia:he']}" if tags.get("wikipedia:he") else None)
        image = tags.get("image") if (tags.get("image") or "").startswith("http") else None
        if not image and tags.get("wikimedia_commons", "").startswith("File:"):
            image = "https://commons.wikimedia.org/wiki/Special:FilePath/" + tags["wikimedia_commons"][5:].replace(" ", "_")
        self.pois.append({
            "osm": ref, "name": name, "name_en": tags.get("name:en"),
            "category": cls[0], "sub": cls[1],
            "kind_he": categories.kind_he(tags, cls[1]),
            "lon": pt[0], "lat": pt[1],
            "street": tags.get("addr:street"), "housenumber": tags.get("addr:housenumber"),
            "city": tags.get("addr:city"), "postcode": tags.get("addr:postcode"),
            "phone": format_phone(tags.get("phone") or tags.get("contact:phone") or tags.get("contact:mobile")),
            "website": normalize_url(tags.get("website") or tags.get("contact:website") or tags.get("url")),
            "email": tags.get("email") or tags.get("contact:email"),
            "opening_hours": oh, "hours_json": json.dumps(hours.to_json(week)) if week else None,
            "kosher": {True: 1, False: 0, None: None}[categories.is_kosher(tags)],
            "wheelchair": tags.get("wheelchair"),
            "description": tags.get("description:he") or None,
            "wikidata": tags.get("wikidata"), "wikipedia": wikipedia, "image": image,
            "extra": extra, "richness": len(tags) + (5 if name else 0),
            "is_area": geom.get("type") in ("Polygon", "MultiPolygon"), "shape": shape,
        })
        self.stats.pois[cls[1]] += 1

    def _ingest_address(self, tags: dict, geom: dict) -> None:
        pt = representative(geom)
        if not pt or not self._inside(*pt):
            return
        street = (tags.get("addr:street") or tags.get("addr:place") or "").strip()
        hn = tags["addr:housenumber"].strip()
        for number in [h.strip() for h in re.split(r"[;,]", hn) if h.strip()][:4]:
            m = _LEADING_INT.match(number)
            self.addr_rows.append((
                street, normalize(street), number, int(m.group(1)) if m else None,
                tags.get("addr:city"), tags.get("addr:postcode"), pt[1], pt[0],
            ))
        self.stats.addresses += 1

    # ------------------------------------------------------------------ finish
    def finish(self) -> Stats:
        self._flush_segments()
        self._dedupe_places()
        area_index = AreaIndex()
        settlements = [a for a in self.areas if (a["kind"] == "admin" and a["level"] >= 7) or a["kind"] in SETTLEMENT_KINDS]
        for a in settlements:
            area_index.add(a["name"], a["level"], a["polygons"])
        regional = AreaIndex()
        for a in self.areas:
            if a["kind"] == "admin" and a["level"] in (5, 6):
                regional.add(a["name"], a["level"], a["polygons"])
        nearest = NearestPlace([
            (p["name"], p["lat"], p["lon"], PLACE_RADIUS.get(p["kind"], 1000))
            for p in self.places if p["kind"] in SETTLEMENT_KINDS
        ])
        self._assign = (area_index, regional, nearest)

        self._write_places(area_index)
        self._write_areas()
        self._merge_lines()
        self._write_pois()
        self._write_trails()
        self._write_addresses()
        self._write_streets()
        self.con.commit()
        return self.stats

    def _resolve_cities(self, lons: list[float], lats: list[float], tagged: list[str | None]) -> list[str | None]:
        area_index, regional, nearest = self._assign
        need = [i for i, c in enumerate(tagged) if not c]
        out: list[str | None] = list(tagged)
        self.stats.city_from_tag += len(tagged) - len(need)
        if not need:
            return out
        poly = area_index.assign([lons[i] for i in need], [lats[i] for i in need])
        still: list[int] = []
        for i, c in zip(need, poly):
            if c:
                out[i] = c
                self.stats.city_from_polygon += 1
            else:
                still.append(i)
        reg = regional.assign([lons[i] for i in still], [lats[i] for i in still]) if still else []
        for i, r in zip(still, reg):
            c = nearest.find(lats[i], lons[i])
            if c:
                self.stats.city_from_nearest += 1
                out[i] = c
            elif r:
                out[i] = r
                self.stats.city_from_polygon += 1
            else:
                self.stats.city_missing += 1
        return out

    def _dedupe_places(self) -> None:
        """Merge place nodes and place areas describing the same settlement."""
        by_key: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for p in self.places:
            group = "settlement" if p["kind"] in SETTLEMENT_KINDS else p["kind"]
            by_key[(normalize(p["name"]), group)].append(p)
        merged: list[dict] = []
        for items in by_key.values():
            items.sort(key=lambda p: (p["osm"][:1] != "n", -(p["population"] or 0)))
            kept: list[dict] = []
            for p in items:
                twin = next((k for k in kept if geo.haversine(k["lat"], k["lon"], p["lat"], p["lon"]) < 8000), None)
                if twin:
                    twin["bbox"] = twin["bbox"] or p["bbox"]
                    twin["population"] = twin["population"] or p["population"]
                    twin["name_en"] = twin["name_en"] or p["name_en"]
                    twin["wikidata"] = twin["wikidata"] or p["wikidata"]
                else:
                    kept.append(p)
            merged.extend(kept)
        self.places = merged

    def _write_places(self, area_index: AreaIndex) -> None:
        neigh = [p for p in self.places if p["kind"] in NEIGHBOURHOOD_KINDS]
        parents = area_index.assign([p["lon"] for p in neigh], [p["lat"] for p in neigh]) if neigh else []
        nearest = self._assign[2]
        for p, parent in zip(neigh, parents):
            p["parent"] = parent or nearest.find(p["lat"], p["lon"])
        area_bbox = {}
        for a in self.areas:
            if (a["kind"] == "admin" and a["level"] in (7, 8)) or a["kind"] in SETTLEMENT_KINDS:
                area_bbox.setdefault(normalize(a["name"]), a["bbox"])
        rows = []
        for i, p in enumerate(self.places, start=1):
            p["id"] = i
            if p["kind"] in SETTLEMENT_KINDS:
                b = area_bbox.get(normalize(p["name"]))
                if b and b[0] - 0.05 <= p["lon"] <= b[2] + 0.05 and b[1] - 0.05 <= p["lat"] <= b[3] + 0.05:
                    p["bbox"] = b
            base = PLACE_KINDS[p["kind"]]
            pop = p["population"] or 0
            imp = min(1.0, base * 0.7 + (math.log10(pop + 1) / 6.0) * 0.3 + (0.05 if p.get("wikidata") else 0))
            bbox = p["bbox"] or geo.bbox_around(p["lat"], p["lon"], PLACE_RADIUS.get(p["kind"], 700))
            rows.append((
                i, p["osm"], p["name"], p["name_en"], json.dumps(p["alt"], ensure_ascii=False) if p["alt"] else None,
                p["kind"], p["population"], p["lat"], p["lon"], *bbox, p.get("parent"), None, None, round(imp, 4),
            ))
        self.con.executemany("INSERT INTO places VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        self.con.executemany(
            "INSERT INTO places_rtree VALUES (?,?,?,?,?)",
            ((r[0], r[9], r[11], r[10], r[12]) for r in rows),
        )
        self.stats.places = len(rows)

    def _write_areas(self) -> None:
        rows, rt = [], []
        for i, a in enumerate(self.areas, start=1):
            polys = [[[[round(x, 5), round(y, 5)] for x, y in ring] for ring in poly] for poly in a["polygons"]]
            rows.append((i, a["osm"], a["name"], a["name_en"], a["level"], a["kind"], json.dumps(polys, separators=(",", ":"))))
            minx, miny, maxx, maxy = a["bbox"]
            rt.append((i, minx, maxx, miny, maxy))
        self.con.executemany("INSERT INTO areas VALUES (?,?,?,?,?,?,?)", rows)
        self.con.executemany("INSERT INTO areas_rtree VALUES (?,?,?,?,?)", rt)

    def _dedupe_pois(self) -> list[dict]:
        groups: dict[tuple, list[dict]] = defaultdict(list)
        for p in self.pois:
            groups[(p["sub"], normalize(p["name"]) if p["name"] else None)].append(p)
        out: list[dict] = []
        for (sub, name), items in groups.items():
            radius = 80.0 if name else 15.0
            if name and sub in categories.SHAPE_SUBS:
                radius = 5000.0  # a reserve mapped as several polygons of the same name is one site
            items.sort(key=lambda p: (-p["richness"], p["is_area"]))
            cell = radius / 111_000 * 2
            grid: dict[tuple[int, int], list[dict]] = defaultdict(list)
            for p in items:
                cx, cy = int(p["lon"] / cell), int(p["lat"] / cell)
                dup = None
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        for k in grid.get((cx + dx, cy + dy), ()):
                            if geo.haversine(k["lat"], k["lon"], p["lat"], p["lon"]) < radius:
                                dup = k
                                break
                        if dup:
                            break
                    if dup:
                        break
                if dup:
                    for fld in ("phone", "website", "email", "opening_hours", "hours_json", "street", "housenumber",
                                "city", "wikidata", "wikipedia", "image", "name_en", "description", "kosher", "wheelchair"):
                        if dup.get(fld) is None and p.get(fld) is not None:
                            dup[fld] = p[fld]
                    if "bbox" in p["extra"] and "bbox" not in dup["extra"]:
                        dup["extra"]["bbox"] = p["extra"]["bbox"]
                    if p.get("shape") and not dup.get("shape"):
                        dup["shape"] = p["shape"]
                    elif p.get("shape") and dup["shape"]["type"] == p["shape"]["type"] == "MultiPolygon" and p["is_area"]:
                        dup["shape"]["coordinates"] += p["shape"]["coordinates"]
                        if "bbox" in p["extra"] and "bbox" in dup["extra"]:
                            a, b = dup["extra"]["bbox"], p["extra"]["bbox"]
                            dup["extra"]["bbox"] = [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]
                    self.stats.duplicates += 1
                    continue
                grid[(cx, cy)].append(p)
                out.append(p)
        return out

    def _write_pois(self) -> None:
        pois = self._dedupe_pois()
        cities = self._resolve_cities([p["lon"] for p in pois], [p["lat"] for p in pois], [p["city"] for p in pois])
        rows, rt, shapes = [], [], []
        for i, (p, city) in enumerate(zip(pois, cities), start=1):
            if p.get("shape"):
                shapes.append((i, json.dumps(p["shape"], separators=(",", ":"))))
            sub = categories.SUB_BY_ID[p["sub"]]
            imp = sub.importance
            imp += 0.12 if p["wikidata"] else 0
            imp += 0.08 if p["wikipedia"] else 0
            imp += 0.03 if p["website"] else 0
            imp += 0.02 if p["opening_hours"] else 0
            imp += 0.02 if p["name"] else -0.05
            bbox = p["extra"].get("bbox")
            if bbox:  # big parks/campuses/malls matter more
                area_km2 = abs(bbox[2] - bbox[0]) * 95 * abs(bbox[3] - bbox[1]) * 111
                imp += min(0.15, max(0.0, math.log10(area_km2 * 100 + 1) * 0.05))
            rows.append((
                i, p["osm"], p["name"], p["name_en"], p["category"], p["sub"], p["kind_he"], p["lat"], p["lon"],
                p["street"], p["housenumber"], city, p["postcode"], p["phone"], p["website"], p["email"],
                p["opening_hours"], p["hours_json"], p["kosher"], p["wheelchair"], p["description"],
                p["wikidata"], p["wikipedia"], p["image"],
                json.dumps(p["extra"], ensure_ascii=False) if p["extra"] else None,
                round(min(1.0, max(0.01, imp)), 4), self.now,
            ))
            if bbox:
                rt.append((i, bbox[0], bbox[2], bbox[1], bbox[3]))
            else:
                rt.append((i, p["lon"], p["lon"], p["lat"], p["lat"]))
            if p["street"] and p["housenumber"]:
                m = _LEADING_INT.match(p["housenumber"])
                self.addr_rows.append((p["street"], normalize(p["street"]), p["housenumber"],
                                       int(m.group(1)) if m else None, city, p["postcode"], p["lat"], p["lon"]))
        self.con.executemany(f"INSERT INTO pois VALUES ({','.join('?' * 27)})", rows)
        self.con.executemany("INSERT INTO pois_rtree VALUES (?,?,?,?,?)", rt)
        self.con.executemany("INSERT INTO poi_shapes VALUES (?,?)", shapes)
        self.pois = []

    def _merge_lines(self) -> None:
        """One POI per stream / ridge / valley: join same-name segments that touch or nearly touch."""
        total = sum(len(v) for v in self.line_segs.values())
        made = 0
        for segs in self.line_segs.values():
            parent = list(range(len(segs)))

            def find(a: int) -> int:
                while parent[a] != a:
                    parent[a] = parent[parent[a]]
                    a = parent[a]
                return a

            cell = LINE_JOIN_M / 111_000
            grid: dict[tuple[int, int], list[tuple[int, tuple[float, float]]]] = defaultdict(list)
            for i, s in enumerate(segs):
                for pt in (s["coords"][0], s["coords"][-1]):
                    cx, cy = int(pt[0] / cell), int(pt[1] / cell)
                    for dx in (-1, 0, 1):
                        for dy in (-1, 0, 1):
                            for j, q in grid.get((cx + dx, cy + dy), ()):
                                if find(i) != find(j) and geo.haversine(pt[1], pt[0], q[1], q[0]) <= LINE_JOIN_M:
                                    parent[find(i)] = find(j)
                    grid[(cx, cy)].append((i, pt))
            comps: dict[int, list[dict]] = defaultdict(list)
            for i, s in enumerate(segs):
                comps[find(i)].append(s)
            for items in comps.values():
                best = max(items, key=lambda s: (len(s["tags"]), len(s["coords"])))
                longest = max(items, key=lambda s: geo.line_length_m(s["coords"]))
                before = len(self.pois)
                self._ingest_poi(best["ref"], best["tags"], {"type": "LineString", "coordinates": longest["coords"]}, best["cls"])
                if len(self.pois) == before:
                    continue  # outside the area
                p = self.pois[-1]
                all_pts = [pt for s in items for pt in s["coords"]]
                p["extra"]["bbox"] = [round(v, 6) for v in geo.bbox_of(all_pts)]
                p["extra"]["length_m"] = round(sum(geo.line_length_m(s["coords"]) for s in items))
                p["shape"] = _round_geom({"type": "MultiLineString", "coordinates": [s["coords"] for s in items]}, 5.0)
                made += 1
        if total:
            log.info("line features: %d named segments merged into %d POIs", total, made)
        self.line_segs.clear()

    def _write_trails(self) -> None:
        trails = self.routes.build(self.bbox)
        rows, seg_rows, seg_rt = [], [], []
        seg_id = 0
        for tid, t in enumerate(trails, start=1):
            minx, miny, maxx, maxy = t["bbox"]
            rows.append((tid, t["osm"], t["name"], t["name_en"], t["route"], t["network"], t["mark"], t["colour"], t["osmc"],
                         t["ref"], t["operator"], normalize_url(t["website"]) if t["website"] else None, t["description"],
                         t["length_m"], t["parts"], t["lat"], t["lon"], minx, miny, maxx, maxy, t["importance"],
                         json.dumps(t["extra"], ensure_ascii=False) if t["extra"] else None))
            for piece in TrailCollector.segments(t):
                seg_id += 1
                bx = geo.bbox_of(piece)
                seg_rows.append((seg_id, tid, geo.encode_polyline(piece)))
                seg_rt.append((seg_id, bx[0], bx[2], bx[1], bx[3]))
        self.con.executemany(f"INSERT INTO trails VALUES ({','.join('?' * 23)})", rows)
        self.con.executemany("INSERT INTO trail_segments VALUES (?,?,?)", seg_rows)
        self.con.executemany("INSERT INTO trail_segments_rtree VALUES (?,?,?,?,?)", seg_rt)
        self.stats.trails = len(rows)
        self.routes = TrailCollector()  # free the way geometries

    def _write_addresses(self) -> None:
        rows = self.addr_rows
        cities = self._resolve_cities([r[7] for r in rows], [r[6] for r in rows], [r[4] for r in rows])
        seen: set[tuple] = set()
        out, rt = [], []
        for r, city in zip(rows, cities):
            key = (r[1], r[2], normalize(city) if city else None)
            if key in seen:
                continue
            seen.add(key)
            i = len(out) + 1
            out.append((i, r[0], r[1], r[2], r[3], city, normalize(city) if city else None, r[5], r[6], r[7]))
            rt.append((i, r[7], r[7], r[6], r[6]))
        self.con.executemany("INSERT INTO addresses VALUES (?,?,?,?,?,?,?,?,?,?)", out)
        self.con.executemany("INSERT INTO addresses_rtree VALUES (?,?,?,?,?)", rt)
        self.addr_rows = []

    def _write_streets(self) -> None:
        cur = self.con.execute("SELECT rowid, lon, lat, city FROM stg_seg")
        seg = cur.fetchall()
        cities = self._resolve_cities([s[1] for s in seg], [s[2] for s in seg], [s[3] for s in seg])
        self.con.executemany("UPDATE stg_seg SET city = ? WHERE rowid = ?", ((c, s[0]) for s, c in zip(seg, cities)))
        groups: dict[tuple, list[tuple]] = defaultdict(list)
        for row in self.con.execute(
            "SELECT name, name_en, name_norm, highway, city, lon, lat, min_lon, min_lat, max_lon, max_lat, length, geom FROM stg_seg"
        ):
            city = row[4]
            key = (row[2], normalize(city)) if city else (row[2], None, int(row[5] / 0.05), int(row[6] / 0.05))
            groups[key].append(row)
        street_rows, seg_rows, seg_rt = [], [], []
        seg_id = 0
        for sid, (key, items) in enumerate(groups.items(), start=1):
            total = sum(r[11] for r in items)
            longest = max(items, key=lambda r: r[11])
            minx = min(r[7] for r in items)
            miny = min(r[8] for r in items)
            maxx = max(r[9] for r in items)
            maxy = max(r[10] for r in items)
            hw_rank = max(items, key=lambda r: STREET_IMPORTANCE.get(r[3], 0.4))[3]
            imp = STREET_IMPORTANCE.get(hw_rank, 0.4) + min(0.15, math.log10(total + 1) * 0.04)
            name_en = next((r[1] for r in items if r[1]), None)
            city = items[0][4]
            street_rows.append((sid, longest[0], name_en, key[0], city, normalize(city) if city else None, hw_rank,
                                longest[6], longest[5], minx, miny, maxx, maxy, round(total, 1), round(min(1.0, imp), 4)))
            for r in items:
                seg_id += 1
                seg_rows.append((seg_id, sid, r[12]))
                seg_rt.append((seg_id, r[7], r[9], r[8], r[10]))
        self.con.executemany("INSERT INTO streets VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", street_rows)
        self.con.executemany("INSERT INTO street_segments VALUES (?,?,?)", seg_rows)
        self.con.executemany("INSERT INTO street_segments_rtree VALUES (?,?,?,?,?)", seg_rt)
        self.con.execute("DROP TABLE stg_seg")


def run(con: sqlite3.Connection, poi_features: Iterable[dict], street_features: Iterable[dict],
        bbox: tuple[float, float, float, float] = geo.ISRAEL_BBOX,
        line_features: Iterable[dict] = (), relations: Iterable[dict] = ()) -> Stats:
    """*relations*: ``{"id", "tags", "members": [(type, ref), …]}`` (route relations);
    *line_features*: open ways – trail members and named streams / ridges (see :meth:`Builder.ingest_line`)."""
    b = Builder(con, bbox)
    for r in relations:
        b.routes.add_relation(r["id"], r["tags"], r["members"])
    for f in poi_features:
        b.ingest_feature(f)
    for f in line_features:
        b.ingest_line(f)
    log.info("ingested %d features (%d POIs, %d addresses, %d places)",
             b.stats.features, len(b.pois), len(b.addr_rows), len(b.places))
    for f in street_features:
        b.ingest_street(f)
    log.info("ingested %d street segments", b.stats.segments)
    return b.finish()
