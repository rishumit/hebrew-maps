"""OSM extraction with pyosmium (pip package ``osmium``) – no osmium-tool / Docker needed.

Two passes over the PBF:

1. **relations only** (fast): marked-trail route relations are registered in the
   builder's :class:`~hmaps.etl.trails.TrailCollector`, so the second pass knows
   which way ids carry trail geometry;
2. **everything**, with node locations, feeding:

   * the database :class:`~hmaps.etl.build.Builder` (POIs, places, addresses,
     boundaries, streets, named streams/ridges, trail member ways);
   * the built-in router's :class:`~hmaps.route.graph.GraphBuilder` (every routable
     way, with a small preference for ways that belong to a marked trail).

Features are handed over in exactly the same shape that ``osmium export``
produces, so both extraction engines share all downstream logic
(see :mod:`hmaps.etl.osm` for the osmium-tool counterpart).
"""

from __future__ import annotations

import json
import logging
import time

from ..route.graph import GraphBuilder
from .build import LINE_TAGS, is_line_feature
from .trails import ROUTE_KINDS

log = logging.getLogger(__name__)

#: Keys that make a node or area interesting for the database.
FEATURE_KEYS = (
    "amenity", "shop", "tourism", "leisure", "historic", "natural", "waterway", "place", "boundary", "railway",
    "public_transport", "aeroway", "healthcare", "emergency", "office", "addr:housenumber", "mikveh", "building",
    "highway", "man_made", "craft",
)
#: man_made values worth reading (kept in sync with osm.POI_FILTERS)
MAN_MADE = frozenset({"water_tower", "water_well", "well"})


def available() -> bool:
    try:
        import osmium  # noqa: F401
        return True
    except ImportError:
        return False


def _relevant(tags) -> bool:
    for k in FEATURE_KEYS:
        if k in tags:
            if k == "building" and tags.get("building") != "synagogue":
                continue
            if k == "highway" and tags.get("highway") != "bus_stop":
                continue
            if k == "man_made" and tags.get("man_made") not in MAN_MADE:
                continue
            return True
    return False


def _maybe_line(tags) -> bool:
    return any(k in tags for k in LINE_TAGS)


def read_relations(pbf, builder) -> int:
    """Pass 1: register trail route relations with *builder*.routes."""
    import osmium

    routes = builder.routes

    class RelHandler(osmium.SimpleHandler):
        def relation(self, r):
            t = r.tags
            if t.get("route") not in ROUTE_KINDS:
                return
            routes.add_relation(r.id, {tag.k: tag.v for tag in t}, [(m.type, m.ref) for m in r.members])

    RelHandler().apply_file(str(pbf))
    return len(routes.relations)


def extract(pbf, builder, graph: GraphBuilder | None) -> dict:
    """Stream *pbf* into *builder* (and *graph*). Returns counters."""
    import osmium

    factory = osmium.geom.GeoJSONFactory()
    counts = {"nodes": 0, "ways": 0, "areas": 0, "bad_geometry": 0, "route_relations": 0, "trail_ways": 0}
    t0 = time.time()
    counts["route_relations"] = read_relations(pbf, builder)
    log.info("pyosmium: %d trail route relations (%.0fs)", counts["route_relations"], time.time() - t0)
    routes = builder.routes
    wanted = routes.wanted()

    class Handler(osmium.SimpleHandler):
        def node(self, n):
            t = n.tags
            if len(t) == 0 or not _relevant(t):
                return
            loc = n.location
            if not loc.valid():
                return
            props = {tag.k: tag.v for tag in t}
            props["@type"], props["@id"] = "node", n.id
            builder.ingest_feature({"type": "Feature", "properties": props,
                                    "geometry": {"type": "Point", "coordinates": [loc.lon, loc.lat]}})
            counts["nodes"] += 1

        def way(self, w):
            t = w.tags
            member = w.id in wanted
            highway = "highway" in t
            if not (highway or member or _maybe_line(t)):
                return
            try:
                nodes = [(nd.ref, nd.lon, nd.lat) for nd in w.nodes]
            except osmium.InvalidLocationError:
                counts["bad_geometry"] += 1
                return
            if member:
                routes.add_way(w.id, [(x, y) for _, x, y in nodes])
                counts["trail_ways"] += 1
            if not highway:
                tags = {tag.k: tag.v for tag in t}
                if len(nodes) >= 2 and nodes[0][0] != nodes[-1][0] and is_line_feature(tags):
                    tags["@type"], tags["@id"] = "way", w.id
                    builder.ingest_feature({"type": "Feature", "properties": tags,
                                            "geometry": {"type": "LineString", "coordinates": [[x, y] for _, x, y in nodes]}})
                return
            tags = {tag.k: tag.v for tag in t}
            if graph is not None:
                graph.add_way(tags, nodes, marked=routes.marked_profiles(w.id) if member else frozenset())
            if tags.get("name") or tags.get("name:he"):
                props = dict(tags)
                props["@type"], props["@id"] = "way", w.id
                builder.ingest_street({"type": "Feature", "properties": props,
                                       "geometry": {"type": "LineString", "coordinates": [[x, y] for _, x, y in nodes]}})
            counts["ways"] += 1
            if counts["ways"] % 200_000 == 0:
                log.info("  %d ways (%.0fs)", counts["ways"], time.time() - t0)

        def area(self, a):
            t = a.tags
            if len(t) == 0 or not _relevant(t):
                return
            try:
                geom = json.loads(factory.create_multipolygon(a))
            except Exception:  # noqa: BLE001 - invalid polygons are common in raw OSM
                counts["bad_geometry"] += 1
                return
            props = {tag.k: tag.v for tag in t}
            props["@type"] = "way" if a.from_way() else "relation"
            props["@id"] = a.orig_id()
            builder.ingest_feature({"type": "Feature", "properties": props, "geometry": geom})
            counts["areas"] += 1

    log.info("reading %s with pyosmium (this takes a few minutes)…", pbf)
    Handler().apply_file(str(pbf), locations=True, idx="flex_mem")
    log.info("pyosmium: %s in %.0fs", counts, time.time() - t0)
    return counts
