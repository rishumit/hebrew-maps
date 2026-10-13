"""Road graph for the built-in router (used when OSRM / Docker is not available).

The ETL feeds every routable OSM way (node ids + coordinates + tags) into
:class:`GraphBuilder`. Ways are split at junctions into edges; every edge
gets a travel time per profile (car / foot / bike) for each direction, or
``inf`` when that direction is not allowed. The result is a compact set of
numpy arrays saved as ``routing/graph.npz``.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
from array import array
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

PROFILES = ("car", "foot", "bike")

HIGHWAY_CLASSES = (
    "motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential", "living_street",
    "service", "road", "motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link",
    "track", "pedestrian", "footway", "path", "cycleway", "steps", "bridleway",
)
CLASS_ID = {c: i for i, c in enumerate(HIGHWAY_CLASSES)}

CAR_SPEED = {  # km/h – conservative urban Israeli averages
    "motorway": 100, "trunk": 80, "primary": 60, "secondary": 50, "tertiary": 40, "unclassified": 35,
    "residential": 28, "living_street": 10, "service": 15, "road": 30, "motorway_link": 50, "trunk_link": 45,
    "primary_link": 40, "secondary_link": 35, "tertiary_link": 30,
}
BIKE_SPEED = {
    "cycleway": 18, "residential": 16, "living_street": 12, "service": 14, "unclassified": 16, "tertiary": 15,
    "tertiary_link": 15, "secondary": 14, "secondary_link": 14, "primary": 13, "primary_link": 13, "trunk": 12,
    "trunk_link": 12, "road": 14, "track": 10, "path": 10, "pedestrian": 8, "footway": 8, "bridleway": 8, "steps": 2,
}
FOOT_SPEED = 5.0
#: ways on a marked trail (OSM route relation) are made this much cheaper for foot / bike.
#: The cost is the travel time, so durations along trails are under-estimated by up to ~10%.
TRAIL_COST_FACTOR = 0.9
MAX_SPEED_MS = {"car": 115 / 3.6, "foot": FOOT_SPEED / 3.6 / TRAIL_COST_FACTOR, "bike": 18 / 3.6 / TRAIL_COST_FACTOR}

_NO = {"no", "private", "agricultural", "forestry", "delivery"}


def _hav(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 12_742_017.6 * math.asin(min(1.0, math.sqrt(a)))


def _maxspeed(tags: dict) -> float | None:
    m = re.match(r"^\s*(\d+)", tags.get("maxspeed", ""))
    return float(m.group(1)) if m else None


def _oneway(tags: dict, hw: str) -> int:
    """1 = forward only, -1 = backward only, 0 = both."""
    v = tags.get("oneway", "")
    if v in ("yes", "1", "true"):
        return 1
    if v == "-1" or v == "reverse":
        return -1
    if v == "no":
        return 0
    if tags.get("junction") in ("roundabout", "circular") or hw in ("motorway",):
        return 1
    return 0


def speeds(tags: dict) -> dict[str, tuple[float, float]]:
    """Speed (m/s) forward and backward per profile; 0 = not allowed."""
    hw = tags.get("highway", "")
    access = tags.get("access", "")
    out: dict[str, tuple[float, float]] = {}

    # car
    car = CAR_SPEED.get(hw, 0.0)
    if car and (access in _NO or tags.get("motor_vehicle") in _NO or tags.get("motorcar") in _NO or tags.get("area") == "yes"):
        car = 0.0
    ms = _maxspeed(tags)
    if car and ms:
        car = min(car * 1.3, ms * 0.85)
    ow = _oneway(tags, hw)
    c = car / 3.6
    out["car"] = (c if ow >= 0 else 0.0, c if ow <= 0 else 0.0)

    # foot
    foot_ok = hw not in ("motorway", "motorway_link", "trunk", "trunk_link") or tags.get("foot") in ("yes", "designated")
    if tags.get("foot") in _NO or (access in _NO and tags.get("foot") not in ("yes", "designated", "permissive")):
        foot_ok = False
    f = (3.0 if hw == "steps" else FOOT_SPEED) / 3.6 if foot_ok else 0.0
    out["foot"] = (f, f)

    # bike
    bicycle = tags.get("bicycle", "")
    b = BIKE_SPEED.get(hw, 0.0)
    if hw in ("footway", "pedestrian", "steps") and bicycle not in ("yes", "designated", "permissive"):
        b = 0.0 if hw == "steps" else b * 0.6  # walk the bike
    if hw in ("motorway", "motorway_link") or bicycle in _NO or (access in _NO and bicycle not in ("yes", "designated")):
        b = 0.0
    bow = 0 if tags.get("oneway:bicycle") == "no" else ow
    bb = b / 3.6
    out["bike"] = (bb if bow >= 0 else 0.0, bb if bow <= 0 else 0.0)
    return out


class GraphBuilder:
    """Collects ways, then splits them into a routable graph."""

    def __init__(self) -> None:
        self.refs = array("q")
        self.lons = array("d")
        self.lats = array("d")
        self.way_start = array("q", [0])
        self.way_attr: list[tuple] = []  # (name_idx, ref_idx, class_id, roundabout, speeds...)
        self.names: dict[str, int] = {"": 0}

    def _name(self, s: str | None) -> int:
        s = (s or "").strip()
        if s not in self.names:
            self.names[s] = len(self.names)
        return self.names[s]

    def add_way(self, tags: dict, nodes: list[tuple[int, float, float]], marked: frozenset[str] = frozenset()) -> bool:
        """*marked*: profiles ("foot", "bike") for which the way is part of a marked trail."""
        hw = tags.get("highway")
        if hw not in CLASS_ID or len(nodes) < 2 or tags.get("area") == "yes" and hw != "pedestrian":
            return False
        sp = speeds(tags)
        for prof in marked:
            if prof in sp:
                f, b = sp[prof]
                sp[prof] = (f / TRAIL_COST_FACTOR, b / TRAIL_COST_FACTOR)
        if not any(v > 0 for pair in sp.values() for v in pair):
            return False
        for ref, lon, lat in nodes:
            self.refs.append(ref)
            self.lons.append(lon)
            self.lats.append(lat)
        self.way_start.append(len(self.refs))
        name = tags.get("name:he") or tags.get("name")
        self.way_attr.append((
            self._name(name), self._name(tags.get("ref")), CLASS_ID[hw],
            tags.get("junction") in ("roundabout", "circular"),
            sp["car"][0], sp["car"][1], sp["foot"][0], sp["foot"][1], sp["bike"][0], sp["bike"][1],
        ))
        return True

    @property
    def way_count(self) -> int:
        return len(self.way_attr)

    def build(self) -> dict[str, np.ndarray]:
        refs = np.frombuffer(self.refs, dtype=np.int64)
        lons = np.frombuffer(self.lons, dtype=np.float64)
        lats = np.frombuffer(self.lats, dtype=np.float64)
        starts = np.frombuffer(self.way_start, dtype=np.int64)
        if len(refs) == 0:
            raise ValueError("no routable ways")
        uniq, inverse, counts = np.unique(refs, return_inverse=True, return_counts=True)
        is_vertex = counts > 1
        # way endpoints are always vertices
        is_vertex[inverse[starts[:-1]]] = True
        is_vertex[inverse[starts[1:] - 1]] = True
        vertex_id = np.full(len(uniq), -1, dtype=np.int64)
        vertex_id[is_vertex] = np.arange(int(is_vertex.sum()))
        vlon = np.zeros(int(is_vertex.sum()))
        vlat = np.zeros(int(is_vertex.sum()))
        node_v = vertex_id[inverse]  # per ref occurrence: vertex index or -1
        vlon[node_v[node_v >= 0]] = lons[node_v >= 0]
        vlat[node_v[node_v >= 0]] = lats[node_v >= 0]

        eu, ev, elen, gstart, attr_idx = array("i"), array("i"), array("f"), array("q"), array("i")
        geom = array("f")
        node_v_l = node_v.tolist()
        lons_l, lats_l = lons.tolist(), lats.tolist()
        starts_l = starts.tolist()
        for w in range(len(self.way_attr)):
            a, b = starts_l[w], starts_l[w + 1]
            seg_start = a
            length = 0.0
            for i in range(a + 1, b):
                length += _hav(lons_l[i - 1], lats_l[i - 1], lons_l[i], lats_l[i])
                if node_v_l[i] >= 0:
                    u, v = node_v_l[seg_start], node_v_l[i]
                    if u != v or length > 0:
                        eu.append(u)
                        ev.append(v)
                        elen.append(max(length, 0.1))
                        gstart.append(len(geom) // 2)
                        for k in range(seg_start, i + 1):
                            geom.append(lons_l[k])
                            geom.append(lats_l[k])
                        attr_idx.append(w)
                    seg_start = i
                    length = 0.0
        gstart.append(len(geom) // 2)
        attrs = np.array(self.way_attr, dtype=np.float64)[np.frombuffer(attr_idx, dtype=np.int32)]
        length = np.frombuffer(elen, dtype=np.float32)
        out = {
            "vlon": vlon, "vlat": vlat,
            "u": np.frombuffer(eu, dtype=np.int32).copy(), "v": np.frombuffer(ev, dtype=np.int32).copy(),
            "length": length.copy(),
            "geom_start": np.frombuffer(gstart, dtype=np.int64).copy(),
            "geom": np.frombuffer(geom, dtype=np.float32).reshape(-1, 2).copy(),
            "name": attrs[:, 0].astype(np.int32), "ref": attrs[:, 1].astype(np.int32),
            "cls": attrs[:, 2].astype(np.int8), "roundabout": attrs[:, 3].astype(bool),
        }
        with np.errstate(divide="ignore"):
            for i, p in enumerate(PROFILES):
                for j, d in enumerate(("f", "b")):
                    sp = attrs[:, 4 + i * 2 + j]
                    out[f"cost_{p}_{d}"] = np.where(sp > 0, length / np.maximum(sp, 1e-6), np.inf).astype(np.float32)
        names = [""] * len(self.names)
        for s, i in self.names.items():
            names[i] = s
        out["names"] = np.frombuffer("\n".join(n.replace("\n", " ") for n in names).encode("utf-8"), dtype=np.uint8)
        log.info("routing graph: %d vertices, %d edges", len(vlon), len(out["u"]))
        return out

    def save(self, path: Path) -> Path:
        arrays = self.build()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.stem + ".tmp.npz")
        np.savez(tmp, **arrays)
        os.replace(tmp, path)
        (path.with_suffix(".json")).write_text(json.dumps({"vertices": int(len(arrays["vlon"])),
                                                           "edges": int(len(arrays["u"]))}), encoding="utf-8")
        return path
