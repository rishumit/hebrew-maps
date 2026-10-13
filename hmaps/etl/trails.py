"""Marked trails from OpenStreetMap route relations (hiking / foot / bicycle / mtb).

Route relations do not carry geometry; it comes from their member ways. Both
extraction engines therefore work in two steps:

1. read the relations and register them in a :class:`TrailCollector`, which
   then knows which way ids it needs (:meth:`TrailCollector.wanted`);
2. while streaming the ways (with node locations), hand every wanted way's
   coordinates to :meth:`TrailCollector.add_way`.

:meth:`TrailCollector.build` chains member ways into continuous lines,
computes the length (each way counted once), the number of disconnected
pieces (continuity) and the Israeli marking colour.

The Israeli marking scheme ("ועדת השבילים") uses red / blue / green / black
stripes for local trails and dedicated colours for long-distance trails
(the Israel National Trail is orange-white-blue). OSM records it in
``osmc:symbol`` (``waycolor:background:foreground…``) or ``colour``.
"""

from __future__ import annotations

import colorsys
import logging
import math
import re
from typing import Iterable

from ..core import geo

log = logging.getLogger(__name__)

ROUTE_KINDS = frozenset({"hiking", "foot", "bicycle", "mtb"})
FOOT_ROUTES = frozenset({"hiking", "foot"})
BIKE_ROUTES = frozenset({"bicycle", "mtb"})
_INACTIVE = {"proposed", "disused", "abandoned", "construction"}

MARKS = ("red", "blue", "green", "black", "orange", "purple", "yellow")
MARK_HE = {"red": "אדום", "blue": "כחול", "green": "ירוק", "black": "שחור", "orange": "כתום", "purple": "סגול",
           "yellow": "צהוב", "other": ""}
ROUTE_HE = {"hiking": "שביל הליכה", "foot": "שביל הליכה", "bicycle": "שביל אופניים", "mtb": "מסלול אופני שטח"}
NETWORK_HE = {"nwn": "שביל ארצי", "rwn": "שביל אזורי / למרחקים ארוכים", "lwn": "שביל מסומן מקומי",
              "ncn": "מסלול אופניים ארצי", "rcn": "מסלול אופניים אזורי", "lcn": "מסלול אופניים מקומי"}
NETWORK_IMPORTANCE = {"nwn": 0.9, "ncn": 0.75, "rwn": 0.75, "rcn": 0.65, "lcn": 0.5, "lwn": 0.5}

_COLOUR_WORDS = {
    "red": "red", "darkred": "red", "maroon": "red", "crimson": "red", "blue": "blue", "navy": "blue", "darkblue": "blue",
    "aqua": "blue", "cyan": "blue", "lightblue": "blue", "green": "green", "darkgreen": "green", "olive": "green",
    "lime": "green", "black": "black", "gray": "black", "grey": "black", "orange": "orange", "brown": "orange",
    "purple": "purple", "violet": "purple", "magenta": "purple", "fuchsia": "purple", "yellow": "yellow", "gold": "yellow",
}
_HEX = re.compile(r"^#?([0-9a-fA-F]{6}|[0-9a-fA-F]{3})$")


def is_trail_relation(tags) -> bool:
    return (tags.get("type") in ("route", "superroute") and tags.get("route") in ROUTE_KINDS
            and tags.get("state") not in _INACTIVE)


def _colour_word(v: str | None) -> str | None:
    if not v:
        return None
    v = v.strip().lower()
    w = re.split(r"[_\s]", v)[0]
    if w in _COLOUR_WORDS:
        return _COLOUR_WORDS[w]
    m = _HEX.match(v)
    if not m:
        return None
    h = m.group(1)
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    hue, light, sat = colorsys.rgb_to_hls(r, g, b)
    if light < 0.18 or sat < 0.2:
        return "black"
    deg = hue * 360
    for limit, name in ((15, "red"), (45, "orange"), (70, "yellow"), (170, "green"), (255, "blue"), (335, "purple"), (361, "red")):
        if deg < limit:
            return name
    return "red"


def marking(tags) -> str:
    """Israeli marking colour of a trail (one of :data:`MARKS` or ``"other"``)."""
    osmc = tags.get("osmc:symbol") or ""
    parts = osmc.split(":")
    for p in parts[:1] + parts[2:3]:  # waycolor, then the foreground ("blue_stripe")
        c = _colour_word(p)
        if c and c != "white":
            return c
    c = _colour_word(tags.get("colour"))
    if c:
        return c
    net = tags.get("network")
    if net == "nwn":
        return "orange"
    if net == "rwn":
        return "purple"
    return "other"


def display_name(name: str | None, route: str, mark: str) -> str:
    """Name for the UI – unnamed local trails are called by their colour ("שביל מסומן כחול")."""
    if name:
        return name
    colour = MARK_HE.get(mark, "")
    if route in BIKE_ROUTES:
        return f"מסלול אופניים מסומן{' ' + colour if colour else ''}"
    return f"שביל מסומן{' ' + colour if colour else ''}"


def _k(p: tuple[float, float]) -> tuple[int, int]:
    return (round(p[0] * 1e7), round(p[1] * 1e7))


def chain(ways: list[list[tuple[float, float]]]) -> list[list[tuple[float, float]]]:
    """Join ways (in relation order, any direction) into as few lines as possible."""
    parts: list[list[tuple[float, float]]] = []
    cur: list[tuple[float, float]] | None = None
    for coords in ways:
        if len(coords) < 2:
            continue
        if cur is None:
            cur = list(coords)
            continue
        if _k(coords[0]) == _k(cur[-1]):
            cur.extend(coords[1:])
        elif _k(coords[-1]) == _k(cur[-1]):
            cur.extend(reversed(coords[:-1]))
        elif _k(coords[-1]) == _k(cur[0]):
            cur = list(coords[:-1]) + cur
        elif _k(coords[0]) == _k(cur[0]):
            cur = list(reversed(coords[1:])) + cur
        else:
            parts.append(cur)
            cur = list(coords)
    if cur is not None:
        parts.append(cur)
    return _merge_parts(parts)


def _merge_parts(parts: list[list[tuple[float, float]]]) -> list[list[tuple[float, float]]]:
    """Join pieces whose endpoints coincide (members listed out of order)."""
    changed = True
    while changed and len(parts) > 1:
        changed = False
        ends: dict[tuple[int, int], list[int]] = {}
        for i, p in enumerate(parts):
            ends.setdefault(_k(p[0]), []).append(i)
            ends.setdefault(_k(p[-1]), []).append(i)
        used: set[int] = set()
        out = []
        for i, p in enumerate(parts):
            if i in used:
                continue
            used.add(i)
            cur = list(p)
            grown = True
            while grown:
                grown = False
                for j in ends.get(_k(cur[-1]), ()):
                    if j not in used:
                        q = parts[j]
                        cur.extend(q[1:] if _k(q[0]) == _k(cur[-1]) else q[-2::-1])
                        used.add(j)
                        grown = changed = True
                        break
                if grown:
                    continue
                for j in ends.get(_k(cur[0]), ()):
                    if j not in used:
                        q = parts[j]
                        cur = (q[:-1] if _k(q[-1]) == _k(cur[0]) else q[:0:-1]) + cur
                        used.add(j)
                        grown = changed = True
                        break
            out.append(cur)
        parts = out
    return parts


def components(ways: list[list[tuple[float, float]]]) -> int:
    """Connected pieces of a set of ways (ways touching at any shared node are connected)."""
    parent: dict[int, int] = {}

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    owner: dict[tuple[int, int], int] = {}
    for i, coords in enumerate(ways):
        parent[i] = i
        for p in coords:
            k = _k(p)
            j = owner.setdefault(k, i)
            if j != i:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[ri] = rj
    return len({find(i) for i in parent})


def _chunks(line: list[tuple[float, float]], size: int = 80) -> Iterable[list[tuple[float, float]]]:
    """Short pieces keep R*Tree boxes small (tile queries stay cheap)."""
    if len(line) <= size:
        yield line
        return
    for i in range(0, len(line) - 1, size - 1):
        piece = line[i:i + size]
        if len(piece) >= 2:
            yield piece


class TrailCollector:
    """Route relations + the geometry of their member ways."""

    def __init__(self) -> None:
        self.relations: dict[int, tuple[dict, list[int], list[int]]] = {}
        self.ways: dict[int, list[tuple[float, float]]] = {}
        self._wanted: set[int] | None = None
        self._profiles: dict[int, int] | None = None

    # ---------------------------------------------------------------- step 1
    def add_relation(self, rid: int, tags: dict, members: Iterable[tuple[str, int]]) -> bool:
        tags = {k: str(v) for k, v in tags.items()}
        if not is_trail_relation(tags):
            return False
        way_ids, rel_ids = [], []
        for t, ref in members:
            if t == "w":
                way_ids.append(int(ref))
            elif t == "r":
                rel_ids.append(int(ref))
        self.relations[int(rid)] = (tags, way_ids, rel_ids)
        self._wanted = self._profiles = None
        return True

    def _member_ways(self, rid: int, depth: int = 0) -> list[int]:
        tags, ways, rels = self.relations[rid]
        out = list(ways)
        if depth < 3:
            for r in rels:
                if r in self.relations and r != rid:
                    out += self._member_ways(r, depth + 1)
        return out

    def wanted(self) -> set[int]:
        if self._wanted is None:
            self._wanted = {w for _, ways, _ in self.relations.values() for w in ways}
        return self._wanted

    def marked_profiles(self, way_id: int) -> frozenset[str]:
        """Routing profiles for which *way_id* is part of a marked trail."""
        if self._profiles is None:
            prof: dict[int, int] = {}
            for tags, ways, _ in self.relations.values():
                bit = 1 if tags.get("route") in FOOT_ROUTES else 2
                for w in ways:
                    prof[w] = prof.get(w, 0) | bit
            self._profiles = prof
        bits = self._profiles.get(way_id, 0)
        if not bits:
            return frozenset()
        return frozenset(p for p, b in (("foot", 1), ("bike", 2)) if bits & b)

    # ---------------------------------------------------------------- step 2
    def add_way(self, way_id: int, coords: list[tuple[float, float]]) -> None:
        if way_id in self.wanted() and len(coords) >= 2:
            self.ways[way_id] = [(float(x), float(y)) for x, y in coords]

    # ---------------------------------------------------------------- result
    def build(self, bbox: tuple[float, float, float, float] = geo.ISRAEL_BBOX) -> list[dict]:
        out = []
        missing = 0
        for rid, (tags, _, _) in self.relations.items():
            ids = list(dict.fromkeys(self._member_ways(rid)))  # a way listed twice counts once
            geoms = [self.ways[w] for w in ids if w in self.ways]
            missing += len(ids) - len(geoms)
            if not geoms:
                continue
            length = sum(geo.line_length_m(g) for g in geoms)
            if length < 50:
                continue
            lines = chain(geoms)
            longest = max(lines, key=len)
            lon, lat = geo.line_midpoint(longest)
            minx, miny, maxx, maxy = geo.bbox_of(p for g in geoms for p in g)
            if not (bbox[0] <= lon <= bbox[2] and bbox[1] <= lat <= bbox[3]):
                continue
            route = tags["route"]
            mark = marking(tags)
            name = (tags.get("name:he") or tags.get("name") or "").strip() or None
            net = tags.get("network")
            imp = NETWORK_IMPORTANCE.get(net or "", 0.45) + min(0.08, math.log10(length / 1000 + 1) * 0.04)
            imp += 0.04 if name else -0.1
            extra = {k: tags[k] for k in ("distance", "from", "to", "roundtrip", "wikipedia", "wikidata", "symbol",
                                          "ascent", "descent", "duration", "sac_scale", "name:ar") if tags.get(k)}
            out.append({
                "osm": f"r{rid}", "name": name, "name_en": tags.get("name:en"), "route": route, "network": net,
                "mark": mark, "colour": tags.get("colour"), "osmc": tags.get("osmc:symbol"), "ref": tags.get("ref"),
                "operator": tags.get("operator"),
                "website": tags.get("website") or tags.get("url"),
                "description": tags.get("description:he") or tags.get("description"),
                "length_m": round(length, 1), "parts": components(geoms), "lat": lat, "lon": lon,
                "bbox": (minx, miny, maxx, maxy), "importance": round(max(0.05, min(1.0, imp)), 4),
                "lines": [geo.simplify(line, 2.0) for line in lines], "extra": extra,
            })
        log.info("trails: %d of %d route relations (%d member ways missing from the extract)",
                 len(out), len(self.relations), missing)
        return out

    @staticmethod
    def segments(trail: dict) -> Iterable[list[tuple[float, float]]]:
        for line in trail["lines"]:
            yield from _chunks(line)


# ------------------------------------------------------------------ osmium-tool OPL

_OPL_ESC = re.compile(r"%([0-9a-fA-F]+)%")


def _opl_unescape(s: str) -> str:
    return _OPL_ESC.sub(lambda m: chr(int(m.group(1), 16)), s)


def parse_opl_relation(line: str) -> tuple[int, dict, list[tuple[str, int]]] | None:
    """Parse one relation line of ``osmium cat -f opl`` output."""
    line = line.strip()
    if not line.startswith("r"):
        return None
    rid, tags, members = None, {}, []
    for field in line.split(" "):
        if not field:
            continue
        key, val = field[0], field[1:]
        if key == "r":
            rid = int(val)
        elif key == "T" and val:
            for kv in val.split(","):
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    tags[_opl_unescape(k)] = _opl_unescape(v)
        elif key == "M" and val:
            for m in val.split(","):
                ref = m.split("@", 1)[0]
                if len(ref) > 1 and ref[1:].lstrip("-").isdigit():
                    members.append((ref[0], int(ref[1:])))
    if rid is None:
        return None
    return rid, tags, members
