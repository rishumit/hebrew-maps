""""Avoid" options of the built-in router (הימנעות מכבישי אגרה, כבישים מהירים, דרכי עפר …).

An avoided road is not forbidden – it is made much more expensive, so a route still
exists when there is no other way (the client is told which avoidances could not be
met).  Costs only ever grow, so the A* heuristic of :mod:`hmaps.route.engine` stays
admissible.

Options (``?avoid=toll,motorway,unpaved,junctions,pa``):

``toll``       Highway 6, the Carmel Tunnels, the Fast Lane … (OSM ``toll=yes``; older graphs
               without the ``flags`` array fall back to the well-known roads by number / name)
``motorway``   motorways and trunk roads (כבישים מהירים / דרכים מהירות) with their ramps
``unpaved``    long dirt roads (unpaved / track segments longer than ~250 m)
``junctions``  busy junctions: crossings of several roads without a roundabout (a time penalty per junction)
``pa``         Areas A / B of the Palestinian Authority (approximate areas around Palestinian
               localities, ``routing/avoid_areas.json`` built by the ETL – see :func:`build_pa_areas`)
"""

from __future__ import annotations

import json
import logging
import math
import re
from pathlib import Path

import numpy as np

from .graph import CLASS_ID, FLAG_TOLL, FLAG_UNPAVED

log = logging.getLogger(__name__)

AVOID = ("toll", "motorway", "unpaved", "junctions", "pa")
LABELS_HE = {
    "toll": "כבישי אגרה",
    "motorway": "כבישים מהירים",
    "unpaved": "דרכי עפר ארוכות",
    "junctions": "צמתים מאתגרים",
    "pa": "שטחי A/B של הרשות הפלסטינית",
}
#: which options make sense for which profile
FOR_PROFILE = {
    "car": AVOID,
    "bike": ("unpaved", "pa"),
    "foot": ("unpaved", "pa"),
}

TOLL_FACTOR = 40.0
MOTORWAY_FACTOR = 6.0
UNPAVED_FACTOR = 8.0
UNPAVED_SHORT_FACTOR = 1.5
UNPAVED_LONG_M = 250.0
JUNCTION_PENALTY_S = 25.0
PA_FACTOR = 60.0

_MOTORWAY_CLASSES = [CLASS_ID[c] for c in ("motorway", "motorway_link", "trunk", "trunk_link")]
_TRACK = CLASS_ID["track"]
_JUNCTION_CLASSES = [CLASS_ID[c] for c in ("trunk", "primary", "secondary", "tertiary", "trunk_link", "primary_link",
                                           "secondary_link", "tertiary_link", "unclassified")]
# toll roads by number / name, for graphs built before the flags were stored
_TOLL_REFS = {"6", "6 א", "6א"}
_TOLL_NAME = re.compile(r"מנהרות הכרמל|הנתיב המהיר|נתיב מהיר|Carmel Tunnels|Fast Lane|כביש חוצה ישראל")


def parse(text: str | None, profile: str | None = None) -> frozenset[str]:
    """``"toll,pa"`` → ``{"toll", "pa"}`` (unknown and, with *profile*, irrelevant options are dropped)."""
    if not text:
        return frozenset()
    allowed = FOR_PROFILE.get(profile or "car", AVOID) if profile else AVOID
    return frozenset(p.strip() for p in str(text).replace(";", ",").split(",") if p.strip() in allowed)


def key(avoid: frozenset[str]) -> str:
    return ",".join(sorted(avoid))


# ---------------------------------------------------------------------------- per-edge masks

def toll_mask(g: dict, names: list[str]) -> np.ndarray:
    if "flags" in g:
        mask = (g["flags"] & FLAG_TOLL) > 0
        if mask.any():
            return mask
    ref_ok = np.array([bool(r and (r.split(";")[0].strip() in _TOLL_REFS)) for r in names], dtype=bool)
    name_ok = np.array([bool(s and _TOLL_NAME.search(s)) for s in names], dtype=bool)
    # Highway 6 is a motorway; a "6" on a local street is a different road
    by_ref = ref_ok[g["ref"]] & np.isin(g["cls"], _MOTORWAY_CLASSES)
    return by_ref | name_ok[g["name"]]


def motorway_mask(g: dict) -> np.ndarray:
    return np.isin(g["cls"], _MOTORWAY_CLASSES)


def unpaved_mask(g: dict) -> np.ndarray:
    if "flags" in g:
        return ((g["flags"] & FLAG_UNPAVED) > 0) | (g["cls"] == _TRACK)
    return g["cls"] == _TRACK


def junction_vertices(g: dict) -> np.ndarray:
    """Vertices where three or more roads of the main network meet, outside roundabouts."""
    n = len(g["vlon"])
    main = np.isin(g["cls"], _JUNCTION_CLASSES) & ~g["roundabout"].astype(bool)
    deg = np.zeros(n, dtype=np.int32)
    np.add.at(deg, g["u"][main], 1)
    np.add.at(deg, g["v"][main], 1)
    # roundabout vertices are not "challenging" even when many roads arrive
    rb = np.zeros(n, dtype=bool)
    r = g["roundabout"].astype(bool)
    rb[g["u"][r]] = True
    rb[g["v"][r]] = True
    return (deg >= 4) & ~rb


# ---------------------------------------------------------------------------- areas A / B

class Areas:
    """Circles (lon, lat, radius m) around Palestinian localities, with a grid index."""

    CELL = 0.05  # degrees

    def __init__(self, circles: list[tuple[float, float, float]]):
        self.circles = circles
        self.grid: dict[tuple[int, int], list[int]] = {}
        for i, (lon, lat, r) in enumerate(circles):
            d = r / 111_000.0 / max(0.3, math.cos(math.radians(lat)))
            for gx in range(int(math.floor((lon - d) / self.CELL)), int(math.floor((lon + d) / self.CELL)) + 1):
                for gy in range(int(math.floor((lat - d) / self.CELL)), int(math.floor((lat + d) / self.CELL)) + 1):
                    self.grid.setdefault((gx, gy), []).append(i)

    def __len__(self) -> int:
        return len(self.circles)

    def contains(self, lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
        out = np.zeros(len(lon), dtype=bool)
        if not self.circles:
            return out
        lons = np.array([c[0] for c in self.circles])
        lats = np.array([c[1] for c in self.circles])
        pad = max(c[2] for c in self.circles) / 80_000.0
        near = np.nonzero((lon >= lons.min() - pad) & (lon <= lons.max() + pad)
                          & (lat >= lats.min() - pad) & (lat <= lats.max() + pad))[0]
        if not len(near):
            return out
        gx = np.floor(lon[near] / self.CELL).astype(np.int64)
        gy = np.floor(lat[near] / self.CELL).astype(np.int64)
        keys = gx * 1_000_003 + gy
        order = np.argsort(keys, kind="stable")
        keys_s = keys[order]
        bounds = np.flatnonzero(np.diff(keys_s)) + 1
        for grp in np.split(order, bounds):
            cand = self.grid.get((int(gx[grp[0]]), int(gy[grp[0]])))
            if not cand:
                continue
            idx = near[grp]
            x, y = lon[idx], lat[idx]
            hit = np.zeros(len(idx), dtype=bool)
            for ci in cand:
                clon, clat, r = self.circles[ci]
                dx = (x - clon) * 111_320.0 * math.cos(math.radians(clat))
                dy = (y - clat) * 110_574.0
                hit |= dx * dx + dy * dy <= r * r
            out[idx] = hit
        return out

    @classmethod
    def load(cls, path: Path) -> "Areas | None":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        circles = [(float(c[0]), float(c[1]), float(c[2])) for c in data.get("pa", []) if len(c) >= 3]
        return cls(circles) if circles else None


def areas_path(graph_path: Path) -> Path:
    return graph_path.with_name("avoid_areas.json")


def edge_midpoints(g: dict) -> tuple[np.ndarray, np.ndarray]:
    gs = g["geom_start"]
    geom = g["geom"]
    mid = ((gs[:-1] + gs[1:] - 1) // 2).astype(np.int64)
    return geom[mid, 0].astype(np.float64), geom[mid, 1].astype(np.float64)


def pa_mask(g: dict, areas: "Areas | None") -> np.ndarray:
    if areas is None:
        return np.zeros(len(g["u"]), dtype=bool)
    lon, lat = edge_midpoints(g)
    return areas.contains(lon, lat)


# ---------------------------------------------------------------------------- costs

def masks(g: dict, names: list[str], avoid: frozenset[str], areas: "Areas | None") -> dict[str, np.ndarray]:
    out: dict[str, np.ndarray] = {}
    if "toll" in avoid:
        out["toll"] = toll_mask(g, names)
    if "motorway" in avoid:
        out["motorway"] = motorway_mask(g)
    if "unpaved" in avoid:
        out["unpaved"] = unpaved_mask(g)
    if "pa" in avoid:
        out["pa"] = pa_mask(g, areas)
    return out


def apply(g: dict, cf: np.ndarray, cb: np.ndarray, m: dict[str, np.ndarray], avoid: frozenset[str],
          junctions: np.ndarray | None) -> tuple[np.ndarray, np.ndarray]:
    """Penalised copies of the forward / backward cost arrays."""
    factor = np.ones(len(cf), dtype=np.float64)
    if "toll" in m:
        factor[m["toll"]] *= TOLL_FACTOR
    if "motorway" in m:
        factor[m["motorway"]] *= MOTORWAY_FACTOR
    if "unpaved" in m:
        long_ = g["length"] >= UNPAVED_LONG_M
        factor[m["unpaved"] & long_] *= UNPAVED_FACTOR
        factor[m["unpaved"] & ~long_] *= UNPAVED_SHORT_FACTOR
    if "pa" in m:
        factor[m["pa"]] *= PA_FACTOR
    cf2 = cf.astype(np.float64) * factor
    cb2 = cb.astype(np.float64) * factor
    if "junctions" in avoid and junctions is not None:
        # a penalty for arriving at a challenging junction (forward edges end at v, backward ones at u)
        cf2 = cf2 + np.where(junctions[g["v"]], JUNCTION_PENALTY_S, 0.0)
        cb2 = cb2 + np.where(junctions[g["u"]], JUNCTION_PENALTY_S, 0.0)
    return cf2.astype(np.float32), cb2.astype(np.float32)


# ---------------------------------------------------------------------------- building the areas

_ARABIC = re.compile(r"[؀-ۿ]")
_HEBREW = re.compile(r"[֐-׿]")
#: radius (m) of the area around a Palestinian locality, by OSM place kind
PA_RADIUS = {"city": 2600, "town": 1500, "village": 800, "hamlet": 350, "isolated_dwelling": 150,
             "suburb": 700, "quarter": 500, "neighbourhood": 400}
#: Gaza is not on any route; only the West Bank part of the boundary counts
WEST_BANK_MIN_LON = 34.85


def is_palestinian_name(name: str | None) -> bool:
    """OSM's default ``name`` of a Palestinian locality is Arabic; Israeli localities use Hebrew."""
    if not name:
        return False
    return bool(_ARABIC.search(name)) and not _HEBREW.search(name)


def build_pa_areas(places: list[tuple[float, float, str, str]], inside) -> dict:
    """Approximate Areas A / B: a circle around every Palestinian locality of the West Bank.

    *places* – ``(lon, lat, kind, default_name)``; *inside(lon, lat)* – whether the point is
    within the Palestinian Authority boundary (OSM admin_level 2, ISO3166-1 PS).
    Areas A and B hold the Palestinian towns and villages (Oslo II), so the circles cover them
    closely; the open land between them (mostly Area C) is left out.  An approximation for
    route planning only – not an official map.
    """
    circles = []
    for lon, lat, kind, name in places:
        r = PA_RADIUS.get(kind)
        if not r or lon < WEST_BANK_MIN_LON or not is_palestinian_name(name) or not inside(lon, lat):
            continue
        circles.append([round(lon, 5), round(lat, 5), r])
    return {"version": 1, "source": "OpenStreetMap (approximation)", "pa": circles}
