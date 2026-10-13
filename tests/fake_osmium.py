"""Minimal stand-in for the pyosmium API used by hmaps.etl.osm_py (tests only).

The "PBF" is a JSON file: {"nodes": [...], "ways": [...], "areas": [...]} built from
the sample GeoJSON by :func:`write_fake_pbf`. Node ids are derived from coordinates,
so ways that share a coordinate share a node (as in real OSM).
"""

from __future__ import annotations

import json
from pathlib import Path


class InvalidLocationError(Exception):
    pass


class _Tag:
    def __init__(self, k, v):
        self.k, self.v = k, v


class TagList:
    def __init__(self, d: dict):
        self._d = {k: str(v) for k, v in d.items()}

    def __len__(self):
        return len(self._d)

    def __contains__(self, k):
        return k in self._d

    def get(self, k, default=None):
        return self._d.get(k, default)

    def __iter__(self):
        return (_Tag(k, v) for k, v in self._d.items())


class _Loc:
    def __init__(self, lon, lat):
        self.lon, self.lat = lon, lat

    def valid(self):
        return True


class _Node:
    def __init__(self, d):
        self.id = d["id"]
        self.tags = TagList(d["tags"])
        self.location = _Loc(d["lon"], d["lat"])


class _NodeRef:
    def __init__(self, ref, lon, lat):
        self.ref, self.lon, self.lat = ref, lon, lat


class _Way:
    def __init__(self, d):
        self.id = d["id"]
        self.tags = TagList(d["tags"])
        self.nodes = [_NodeRef(*n) for n in d["nodes"]]


class _Member:
    def __init__(self, t, ref, role=""):
        self.type, self.ref, self.role = t, ref, role


class _Relation:
    def __init__(self, d):
        self.id = d["id"]
        self.tags = TagList(d["tags"])
        self.members = [_Member(*m) for m in d["members"]]


class _Area:
    def __init__(self, d):
        self._d = d
        self.tags = TagList(d["tags"])

    def from_way(self):
        return self._d["from_way"]

    def orig_id(self):
        return self._d["orig_id"]


class SimpleHandler:
    def apply_file(self, filename, locations=False, idx="flex_mem"):
        data = json.loads(Path(filename).read_text(encoding="utf-8"))
        for n in data["nodes"]:
            if hasattr(self, "node"):
                self.node(_Node(n))
        for w in data["ways"]:
            if hasattr(self, "way"):
                self.way(_Way(w))
        for r in data.get("relations", []):
            if hasattr(self, "relation"):
                self.relation(_Relation(r))
        for a in data["areas"]:
            if hasattr(self, "area"):
                self.area(_Area(a))


class _GeoJSONFactory:
    def create_multipolygon(self, a):
        return json.dumps(a._d["geometry"])


class geom:  # noqa: N801 - mimics the module attribute
    GeoJSONFactory = _GeoJSONFactory


def _nid(lon, lat):
    return int(round(lon * 1e6)) * 100_000_000 + int(round(lat * 1e6))


def write_fake_pbf(poi_seq: Path, streets_seq: Path, out: Path) -> Path:
    """Also picks up lines.geojsonseq / relations.jsonl next to *poi_seq* (tools/sample_data.py)."""
    nodes, ways, areas, relations = [], [], [], []
    seen_ways: set = set()
    lines_seq, rel_path = poi_seq.parent / "lines.geojsonseq", poi_seq.parent / "relations.jsonl"
    if rel_path.exists():
        relations = [json.loads(x) for x in rel_path.read_text(encoding="utf-8").splitlines() if x.strip()]
    for path in (poi_seq, streets_seq, *([lines_seq] if lines_seq.exists() else [])):
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip().lstrip("\x1e")
            if not line:
                continue
            f = json.loads(line)
            props = dict(f["properties"])
            t, i = props.pop("@type"), props.pop("@id")
            g = f["geometry"]
            if g["type"] == "Point":
                lon, lat = g["coordinates"]
                nodes.append({"id": i, "lon": lon, "lat": lat, "tags": props})
            elif g["type"] == "LineString":
                if i in seen_ways:
                    continue  # named trail member ways are listed in streets and lines
                seen_ways.add(i)
                ways.append({"id": i, "tags": props, "nodes": [[_nid(x, y), x, y] for x, y in g["coordinates"]]})
            else:
                if t == "way":  # closed ways are seen both as ways and as areas
                    ring = g["coordinates"][0]
                    ways.append({"id": i, "tags": props, "nodes": [[_nid(x, y), x, y] for x, y in ring]})
                if g["type"] == "Polygon":
                    g = {"type": "MultiPolygon", "coordinates": [g["coordinates"]]}
                areas.append({"from_way": t == "way", "orig_id": i, "tags": props, "geometry": g})
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"nodes": nodes, "ways": ways, "areas": areas, "relations": relations}, ensure_ascii=False), encoding="utf-8")
    return out
