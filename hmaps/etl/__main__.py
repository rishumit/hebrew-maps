"""Command line for the data pipeline.

    python -m hmaps.etl download            # fetch the Israel OSM extract
    python -m hmaps.etl build               # PBF -> data/maps.db (+ enrichment)
    python -m hmaps.etl build --no-enrich   # OSM only, no other network access
    python -m hmaps.etl from-geojson --poi a.geojsonseq --streets b.geojsonseq --out x.db
    python -m hmaps.etl report              # re-create the quality report
    python -m hmaps.etl enrich              # photos / texts / facts for trip sites and trails (hmaps.etl.rich)
    python -m hmaps.etl gtfs                # public transport timetables -> data/transit/ (hmaps.transit)
    python -m hmaps.etl zmanim              # synagogue prayer times (Nedarim Plus) -> live maps.db (hmaps.etl.zmanim)
    python -m hmaps.etl satellite [--level basic|detailed|full]   # aerial photos of all of Israel for offline use
    python -m hmaps.etl satellite --bbox 34.75,32.03,34.83,32.12 --maxzoom 17   # one area, more detail
    python -m hmaps.etl satellite --test    # diagnose: one tile per source (status, type, size)
                                            # -> data/tiles/satellite*.mbtiles (hmaps.api.satellite)

The database is built under a temporary name and moved into place
atomically, so a running API never sees a half-written file.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from ..core import db
from . import build as builder
from . import enrich, osm, quality, rich, search_index

log = logging.getLogger("hmaps.etl")

DATA_DIR = Path(os.environ.get("HMAPS_DATA", "data"))
PBF_URL = os.environ.get("HMAPS_PBF_URL", "https://download.geofabrik.de/asia/israel-and-palestine-latest.osm.pbf")


def _pbf_path() -> Path:
    return DATA_DIR / "sources" / "israel-and-palestine.osm.pbf"


def download(url: str = PBF_URL, dest: Path | None = None, force: bool = False) -> Path:
    dest = dest or _pbf_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and not force and time.time() - dest.stat().st_mtime < 6 * 3600:
        log.info("using recent %s", dest)
        return dest
    tmp = dest.with_suffix(".part")
    req = urllib.request.Request(url, headers={"User-Agent": enrich.USER_AGENT})
    log.info("downloading %s", url)
    with urllib.request.urlopen(req, timeout=120) as resp, open(tmp, "wb") as out:
        total = int(resp.headers.get("Content-Length") or 0)
        got, last = 0, 0.0
        md5 = hashlib.md5()
        while chunk := resp.read(1 << 20):
            out.write(chunk)
            md5.update(chunk)
            got += len(chunk)
            if time.time() - last > 5:
                last = time.time()
                log.info("  %.0f / %.0f MB", got / 1e6, total / 1e6)
    try:
        with urllib.request.urlopen(urllib.request.Request(url + ".md5", headers={"User-Agent": enrich.USER_AGENT}), timeout=30) as r:
            expected = r.read().decode().split()[0]
        if expected != md5.hexdigest():
            tmp.unlink(missing_ok=True)
            raise SystemExit("checksum mismatch – download corrupted, please retry")
        log.info("checksum OK")
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001
        log.warning("could not verify checksum (continuing)")
    os.replace(tmp, dest)
    return dest


def _build(fill, out: Path, do_enrich: bool, meta: dict, versioned: bool = False) -> dict:
    """Run *fill(con) -> Stats*, enrichment, indexing and QA; publish the database."""
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".building")
    con = db.create(tmp)
    t0 = time.time()
    stats = fill(con)
    log.info("core build done in %.0fs", time.time() - t0)
    gov = {}
    if do_enrich:
        gov = enrich.enrich_gov(
            con,
            os.environ.get("HMAPS_GOV_SETTLEMENTS", enrich.SETTLEMENTS_RESOURCE),
            os.environ.get("HMAPS_GOV_STREETS", enrich.STREETS_RESOURCE),
        )
        enrich.enrich_wikidata(con)
        enrich.enrich_wikipedia(con)
        if os.environ.get("HMAPS_RICH", "1") != "0":
            try:
                rich.run(con, DATA_DIR)
            except Exception:  # noqa: BLE001 – the map must still be published
                log.exception("rich content failed – continuing without it")
    _zmanim_from_snapshot(con)
    search_index.build(con)
    meta = {**meta, "built": datetime.now(timezone.utc).isoformat(timespec="seconds"), "enriched": str(do_enrich)}
    con.executemany("INSERT OR REPLACE INTO meta VALUES (?, ?)", meta.items())
    con.commit()
    report = quality.compute(con, {**stats.as_dict(), "gov": gov})
    db.finalize(con)
    con.close()
    if versioned:
        live = db.publish(tmp, out)
    else:
        os.replace(tmp, out)
        live = out
    quality.write(report, out.parent / "reports")
    log.info("database ready: %s (%.1f MB) in %.0fs", live, live.stat().st_size / 1e6, time.time() - t0)
    return report


def _engine(requested: str) -> str:
    if requested != "auto":
        return requested
    if osm.osmium_available():
        return "osmium"
    from . import osm_py
    if osm_py.available():
        return "pyosmium"
    raise SystemExit("Neither osmium-tool nor the pyosmium package is installed (pip install osmium)")


def _zmanim_from_snapshot(con) -> None:
    """Re-attach the last prayer-times snapshot (offline) so a rebuild keeps synagogue times."""
    from . import zmanim
    snap = zmanim.snapshot_path(DATA_DIR)
    if not snap.exists() or os.environ.get("HMAPS_ZMANIM", "1") == "0":
        return
    try:
        zmanim.apply(con, zmanim.load_snapshot(DATA_DIR))
    except Exception:  # noqa: BLE001 – the map must still be published
        log.exception("prayer times failed – continuing without them")


def cmd_build(args: argparse.Namespace) -> None:
    pbf = Path(args.pbf) if args.pbf else _pbf_path()
    if not pbf.exists():
        pbf = download()
    engine = _engine(args.engine)
    osm_date = datetime.fromtimestamp(pbf.stat().st_mtime, timezone.utc).strftime("%Y-%m-%d")
    meta = {"osm_date": osm_date, "source": PBF_URL, "engine": engine}
    out = Path(args.out)
    if engine == "pyosmium":
        from ..route.graph import GraphBuilder
        from . import osm_py

        graph = GraphBuilder() if not args.no_routing else None

        def fill(con):
            b = builder.Builder(con)
            osm_py.extract(pbf, b, graph)
            return b.finish()

        report = _build(fill, out, not args.no_enrich, meta, versioned=True)
        if graph is not None and graph.way_count:
            path = graph.save(DATA_DIR / "routing" / "graph.npz")
            log.info("routing graph ready: %s", path)
    else:
        work = DATA_DIR / "work"
        poi_seq, streets_seq = osm.extract(pbf, work)
        lines_seq, routes_opl = work / "lines.geojsonseq", work / "routes.opl"
        report = _build(lambda con: builder.run(con, osm.iter_features(poi_seq), osm.iter_features(streets_seq),
                                                line_features=osm.iter_features(lines_seq) if lines_seq.exists() else (),
                                                relations=osm.iter_relations(routes_opl)),
                        out, not args.no_enrich, meta, versioned=True)
        if not args.keep_work:
            for tmp in (poi_seq, streets_seq, lines_seq, routes_opl):
                tmp.unlink(missing_ok=True)
    _summary(report)


def cmd_from_geojson(args: argparse.Namespace) -> None:
    poi, streets = Path(args.poi), Path(args.streets)
    lines = Path(args.lines) if args.lines else None
    rels = Path(args.relations) if args.relations else None
    report = _build(lambda con: builder.run(con, osm.iter_features(poi), osm.iter_features(streets),
                                            line_features=osm.iter_features(lines) if lines else (),
                                            relations=osm.iter_relations(rels) if rels else ()), Path(args.out),
                    args.enrich, {"osm_date": args.date or "", "source": "geojson"})
    _summary(report)


def cmd_enrich(args: argparse.Namespace) -> None:
    """Add rich content to the live database without rebuilding it from OpenStreetMap."""
    import shutil
    import sqlite3

    logical = Path(args.db)
    live = db.resolve(logical)
    if not live.exists():
        raise SystemExit(f"no database at {logical} – run the build first")
    tmp = logical.with_suffix(".building")
    log.info("copying %s", live)
    shutil.copyfile(live, tmp)
    con = sqlite3.connect(tmp)
    try:
        steps = [s.strip() for s in args.steps.split(",")] if args.steps else rich.ALL_STEPS
        rich.run(con, logical.parent, steps=steps, limit=args.limit, workers=args.workers)
        report = quality.compute(con)
        con.execute("VACUUM")
    finally:
        con.close()
    final = db.publish(tmp, logical)
    quality.write(report, logical.parent / "reports")
    log.info("published %s – the running map picks it up within a few seconds", final.name)
    _summary(report)


def cmd_report(args: argparse.Namespace) -> None:
    import sqlite3
    con = sqlite3.connect(db.resolve(args.db))
    report = quality.compute(con)
    paths = quality.write(report, Path(args.db).parent / "reports")
    print("\n".join(str(p) for p in paths))


def _summary(report: dict) -> None:
    t = report["totals"]
    log.info("POIs %s | settlements %s | streets %s | addresses %s", f"{t['pois']:,}", f"{t['settlements']:,}",
             f"{t['streets']:,}", f"{t['addresses']:,}")
    for k in report["kpis"]:
        log.info("  %s %s: %s%s", "✓" if k["ok"] else "✗", k["name"], k["value"], k["unit"])


def main(argv: list[str] | None = None) -> None:
    for stream in (sys.stdout, sys.stderr):  # Hebrew log lines on Windows consoles
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    if os.environ.get("HMAPS_LOG_FILE"):  # also keep a UTF-8 log (enrich-content.cmd)
        fh = logging.FileHandler(os.environ["HMAPS_LOG_FILE"], encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logging.getLogger().addHandler(fh)
    p = argparse.ArgumentParser(prog="hmaps.etl", description="Hebrew Maps data pipeline")
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("download", help="download the OSM extract")
    d.add_argument("--force", action="store_true")
    b = sub.add_parser("build", help="build data/maps.db")
    b.add_argument("--pbf")
    b.add_argument("--out", default=str(DATA_DIR / "maps.db"))
    b.add_argument("--no-enrich", action="store_true", help="skip Wikidata/Wikipedia/data.gov.il")
    b.add_argument("--keep-work", action="store_true")
    b.add_argument("--engine", choices=["auto", "osmium", "pyosmium"], default="auto",
                   help="osmium-tool (Docker) or the pyosmium Python package (Windows without Docker)")
    b.add_argument("--no-routing", action="store_true", help="pyosmium engine: skip the built-in routing graph")
    g = sub.add_parser("from-geojson", help="build from pre-exported GeoJSON sequences (tests, demos)")
    g.add_argument("--poi", required=True)
    g.add_argument("--streets", required=True)
    g.add_argument("--lines", help="open ways: streams, ridges, trail members (geojsonseq)")
    g.add_argument("--relations", help="route relations (osmium OPL or JSON lines)")
    g.add_argument("--out", required=True)
    g.add_argument("--enrich", action="store_true")
    g.add_argument("--date")
    e = sub.add_parser("enrich", help="photos, texts and facts for trip sites and trails (on the live database)")
    e.add_argument("--db", default=str(DATA_DIR / "maps.db"))
    e.add_argument("--steps", help=f"comma separated subset of: {','.join(rich.ALL_STEPS)}")
    e.add_argument("--limit", type=int, help="only the N most important sites / trails (testing)")
    e.add_argument("--workers", type=int, default=4, help="parallel requests (default 4)")
    r = sub.add_parser("report", help="re-create the quality report")
    r.add_argument("--db", default=str(DATA_DIR / "maps.db"))
    t = sub.add_parser("gtfs", help="download + convert the GTFS public transport feed (data/transit/)")
    t.add_argument("--url", help="GTFS zip URL (default: Israel Ministry of Transport)")
    t.add_argument("--zip", help="use a local GTFS zip instead of downloading")
    t.add_argument("--out-dir", help="output directory (default: data/transit)")
    t.add_argument("--force", action="store_true", help="download even if a recent copy exists")
    z = sub.add_parser("zmanim", help="synagogue prayer times from Nedarim Plus (fetch snapshot + apply to the live database)")
    z.add_argument("--db", default=str(DATA_DIR / "maps.db"))
    z.add_argument("--fetch", action="store_true", help="only download -> data/sources/zmanim/zmanim.json")
    z.add_argument("--apply", action="store_true", help="only load the existing snapshot into the database")
    z.add_argument("--limit", type=int, help="only the first N synagogues (testing)")
    z.add_argument("--workers", type=int, default=4, help="parallel requests (max 4)")
    from ..api import satellite
    satellite.add_parser(sub)
    args = p.parse_args(argv)
    if args.cmd == "download":
        download(force=args.force)
    elif args.cmd == "build":
        cmd_build(args)
    elif args.cmd == "from-geojson":
        cmd_from_geojson(args)
    elif args.cmd == "enrich":
        cmd_enrich(args)
    elif args.cmd == "report":
        cmd_report(args)
    elif args.cmd == "gtfs":
        from ..transit import gtfs
        gtfs.cmd(args)
    elif args.cmd == "zmanim":
        from . import zmanim
        zmanim.cmd(args)
    elif args.cmd == "satellite":
        satellite.cmd(args)


if __name__ == "__main__":
    main()
