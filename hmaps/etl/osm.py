"""Extracting what we need from an OpenStreetMap PBF with osmium-tool.

osmium (C++) does the heavy lifting – filtering ~1 GB of raw OSM objects,
resolving node locations and assembling multipolygons – and emits
GeoJSON text sequences that the Python builder streams through.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Iterator

log = logging.getLogger(__name__)

#: Everything except streets (OR-ed by osmium tags-filter).
POI_FILTERS = [
    "nwr/amenity", "nwr/shop", "nwr/tourism", "nwr/leisure", "nwr/historic",
    "nwr/natural=beach,spring,peak,volcano,cave_entrance,waterfall,ridge,arete,saddle,mountain_pass,cliff,valley,gorge",
    "nwr/waterway=waterfall", "nwr/place",
    "r/boundary=administrative,national_park,protected_area",
    "nwr/railway=station,halt,tram_stop", "nwr/public_transport=platform,station",
    "n/highway=bus_stop", "nwr/aeroway=aerodrome", "nwr/healthcare",
    "nwr/emergency=ambulance_station", "nwr/office", "nwr/craft",
    "nwr/man_made=water_tower,water_well,well",
    "nwr/addr:housenumber", "nwr/building=synagogue", "nwr/mikveh",
]

#: open ways for line POIs (named streams / ridges …, see build.LINE_TAGS) and trail route
#: relations; tags-filter also pulls in every member way (and its nodes) of those relations.
#: Kept in sync with osm_py (LINE_TAGS, trails.ROUTE_KINDS).
LINE_FILTERS = [
    "w/waterway=stream,wadi,river", "w/natural=ridge,arete,valley,gorge,cliff",
    "r/route=hiking,foot,bicycle,mtb",
]

_EXCLUDE = ["created_by", "source", "note", "fixme", "FIXME", "comment", "mapillary"]
#: POIs: closed ways become polygons only (not also linestrings) to avoid duplicates
POI_EXPORT_CONFIG = {"attributes": {"type": "@type", "id": "@id"}, "linear_tags": False, "area_tags": True,
                     "exclude_tags": _EXCLUDE}
#: streets: every way (closed or not) is a linestring
STREET_EXPORT_CONFIG = {"attributes": {"type": "@type", "id": "@id"}, "linear_tags": True, "area_tags": False,
                        "exclude_tags": _EXCLUDE}


def osmium_available() -> bool:
    return shutil.which("osmium") is not None


def _run(args: list[str]) -> None:
    log.info("$ %s", " ".join(args))
    subprocess.run(args, check=True)


def extract(pbf: Path, workdir: Path) -> tuple[Path, Path]:
    """Create ``poi.geojsonseq`` and ``streets.geojsonseq`` from *pbf*."""
    workdir.mkdir(parents=True, exist_ok=True)
    poi_cfg = workdir / "export-poi.json"
    poi_cfg.write_text(json.dumps(POI_EXPORT_CONFIG), encoding="utf-8")
    st_cfg = workdir / "export-streets.json"
    st_cfg.write_text(json.dumps(STREET_EXPORT_CONFIG), encoding="utf-8")

    poi_pbf = workdir / "poi.osm.pbf"
    hw_pbf = workdir / "highways.osm.pbf"
    hw_named_pbf = workdir / "highways-named.osm.pbf"
    poi_seq = workdir / "poi.geojsonseq"
    streets_seq = workdir / "streets.geojsonseq"

    _run(["osmium", "tags-filter", "--overwrite", "-o", str(poi_pbf), str(pbf), *POI_FILTERS])
    # streets = highway AND name: two filters in sequence
    _run(["osmium", "tags-filter", "--overwrite", "-o", str(hw_pbf), str(pbf), "w/highway"])
    _run(["osmium", "tags-filter", "--overwrite", "-o", str(hw_named_pbf), str(hw_pbf), "w/name", "w/name:he"])

    common = ["-f", "geojsonseq", "--format-option", "print_record_separator=false", "--overwrite"]
    _run(["osmium", "export", "-c", str(poi_cfg), *common, "-o", str(poi_seq), str(poi_pbf)])
    _run(["osmium", "export", "-c", str(st_cfg), *common, "--geometry-types=linestring", "-o", str(streets_seq), str(hw_named_pbf)])
    for tmp in (poi_pbf, hw_pbf, hw_named_pbf):
        tmp.unlink(missing_ok=True)
    extract_lines(pbf, workdir)
    return poi_seq, streets_seq


def extract_lines(pbf: Path, workdir: Path) -> tuple[Path, Path]:
    """``lines.geojsonseq`` (open ways: streams, ridges, trail members) and ``routes.opl`` (route relations).

    The same input the pyosmium engine gets from its two passes (relations first, then ways)."""
    cfg = workdir / "export-lines.json"
    cfg.write_text(json.dumps(STREET_EXPORT_CONFIG), encoding="utf-8")  # every way is a linestring
    lines_pbf = workdir / "lines.osm.pbf"
    lines_seq = workdir / "lines.geojsonseq"
    routes_opl = workdir / "routes.opl"
    _run(["osmium", "tags-filter", "--overwrite", "-o", str(lines_pbf), str(pbf), *LINE_FILTERS])
    _run(["osmium", "cat", "--overwrite", "-t", "relation", "-f", "opl", "-o", str(routes_opl), str(lines_pbf)])
    # --keep-untagged: trail member ways are sometimes untagged
    _run(["osmium", "export", "-c", str(cfg), "-f", "geojsonseq", "--format-option", "print_record_separator=false",
          "--overwrite", "--keep-untagged", "--geometry-types=linestring", "-o", str(lines_seq), str(lines_pbf)])
    lines_pbf.unlink(missing_ok=True)
    return lines_seq, routes_opl


def iter_relations(path: Path) -> Iterator[dict]:
    """Route relations from ``osmium cat -f opl`` output, or JSON lines ``{"id", "tags", "members"}``."""
    from .trails import parse_opl_relation

    if not path.exists():
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if line.startswith("{"):
                d = json.loads(line)
                yield {"id": d["id"], "tags": d["tags"], "members": [(m[0], int(m[1])) for m in d["members"]]}
                continue
            r = parse_opl_relation(line)
            if r:
                yield {"id": r[0], "tags": r[1], "members": r[2]}


def iter_features(path: Path) -> Iterator[dict]:
    """Stream features from a GeoJSON text sequence (RFC 8142 tolerant)."""
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip().lstrip("\x1e")
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                log.warning("skipping malformed feature line")


def osm_ref(props: dict, feature: dict) -> str:
    t = props.pop("@type", None)
    i = props.pop("@id", None)
    if t and i is not None:
        return f"{t[0]}{i}"
    fid = feature.get("id")
    return str(fid) if fid is not None else ""
