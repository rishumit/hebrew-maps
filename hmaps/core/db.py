"""SQLite storage: schema and connection helpers.

The dataset is read-mostly: the ETL builds a brand-new database file and
atomically swaps it in, the API opens it read-only. SQLite with R*Tree
spatial indexes and FTS5 gives single-digit-millisecond queries for the
Israeli dataset without operating a database server (see docs/adr/0002).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 4

DDL = """
PRAGMA journal_mode = OFF;
PRAGMA synchronous = OFF;

CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE places (
    id INTEGER PRIMARY KEY,
    osm TEXT,
    name TEXT NOT NULL,
    name_en TEXT,
    alt_names TEXT,
    kind TEXT NOT NULL,
    population INTEGER,
    lat REAL NOT NULL, lon REAL NOT NULL,
    min_lon REAL, min_lat REAL, max_lon REAL, max_lat REAL,
    parent TEXT,
    settlement_code INTEGER,
    district TEXT,
    importance REAL NOT NULL DEFAULT 0
);
CREATE VIRTUAL TABLE places_rtree USING rtree(id, min_lon, max_lon, min_lat, max_lat);

CREATE TABLE areas (
    id INTEGER PRIMARY KEY,
    osm TEXT,
    name TEXT NOT NULL,
    name_en TEXT,
    level INTEGER NOT NULL,
    kind TEXT,
    geom TEXT NOT NULL
);
CREATE VIRTUAL TABLE areas_rtree USING rtree(id, min_lon, max_lon, min_lat, max_lat);

CREATE TABLE streets (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    name_en TEXT,
    name_norm TEXT NOT NULL,
    city TEXT,
    city_norm TEXT,
    highway TEXT,
    lat REAL NOT NULL, lon REAL NOT NULL,
    min_lon REAL, min_lat REAL, max_lon REAL, max_lat REAL,
    length_m REAL,
    importance REAL NOT NULL DEFAULT 0
);
CREATE INDEX streets_lookup ON streets(city_norm, name_norm);
CREATE INDEX streets_name ON streets(name_norm);

CREATE TABLE street_segments (
    id INTEGER PRIMARY KEY,
    street_id INTEGER NOT NULL,
    geom TEXT NOT NULL
);
CREATE INDEX street_segments_street ON street_segments(street_id);
CREATE VIRTUAL TABLE street_segments_rtree USING rtree(id, min_lon, max_lon, min_lat, max_lat);

CREATE TABLE addresses (
    id INTEGER PRIMARY KEY,
    street TEXT NOT NULL,
    street_norm TEXT NOT NULL,
    housenumber TEXT NOT NULL,
    number_int INTEGER,
    city TEXT,
    city_norm TEXT,
    postcode TEXT,
    lat REAL NOT NULL, lon REAL NOT NULL
);
CREATE INDEX addresses_lookup ON addresses(street_norm, city_norm, number_int);
CREATE VIRTUAL TABLE addresses_rtree USING rtree(id, min_lon, max_lon, min_lat, max_lat);

CREATE TABLE pois (
    id INTEGER PRIMARY KEY,
    osm TEXT,
    name TEXT,
    name_en TEXT,
    category TEXT NOT NULL,
    sub TEXT NOT NULL,
    kind_he TEXT,
    lat REAL NOT NULL, lon REAL NOT NULL,
    street TEXT, housenumber TEXT, city TEXT, postcode TEXT,
    phone TEXT, website TEXT, email TEXT,
    opening_hours TEXT, hours_json TEXT,
    kosher INTEGER,
    wheelchair TEXT,
    description TEXT,
    wikidata TEXT, wikipedia TEXT, image TEXT,
    extra TEXT,
    importance REAL NOT NULL DEFAULT 0,
    updated TEXT
);
CREATE INDEX pois_category ON pois(category, sub);
CREATE VIRTUAL TABLE pois_rtree USING rtree(id, min_lon, max_lon, min_lat, max_lat);

-- geometry of line / area POIs (streams, ridges, nature reserves …) for highlighting and the parks layer
CREATE TABLE poi_shapes (
    poi_id INTEGER PRIMARY KEY,
    geom TEXT NOT NULL                    -- GeoJSON geometry (MultiLineString / MultiPolygon), simplified
);

-- marked trails (OSM route relations: hiking / foot / bicycle / mtb)
CREATE TABLE trails (
    id INTEGER PRIMARY KEY,
    osm TEXT,
    name TEXT,                            -- NULL for unnamed local trails (drawn, not searchable)
    name_en TEXT,
    route TEXT NOT NULL,                  -- hiking | foot | bicycle | mtb
    network TEXT,                         -- nwn | rwn | lwn | ncn | rcn | lcn …
    mark TEXT NOT NULL,                   -- Israeli marking colour: red blue green black orange purple yellow other
    colour TEXT,
    osmc TEXT,
    ref TEXT,
    operator TEXT,
    website TEXT,
    description TEXT,
    length_m REAL NOT NULL,
    parts INTEGER NOT NULL,               -- connected pieces (1 = continuous)
    lat REAL NOT NULL, lon REAL NOT NULL,
    min_lon REAL, min_lat REAL, max_lon REAL, max_lat REAL,
    importance REAL NOT NULL DEFAULT 0,
    extra TEXT
);
CREATE TABLE trail_segments (
    id INTEGER PRIMARY KEY,
    trail_id INTEGER NOT NULL,
    geom TEXT NOT NULL                    -- encoded polyline
);
CREATE INDEX trail_segments_trail ON trail_segments(trail_id);
CREATE VIRTUAL TABLE trail_segments_rtree USING rtree(id, min_lon, max_lon, min_lat, max_lat);

CREATE TABLE search_items (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    ref_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    context TEXT,
    category TEXT,
    sub TEXT,
    lat REAL NOT NULL, lon REAL NOT NULL,
    importance REAL NOT NULL DEFAULT 0
);
CREATE VIRTUAL TABLE search_fts USING fts5(
    name_n, alt_n, context_n,
    tokenize = "unicode61 remove_diacritics 0",
    prefix = '1 2 3 4'
);

CREATE TABLE vocab (word TEXT PRIMARY KEY, freq INTEGER NOT NULL) WITHOUT ROWID;
CREATE VIRTUAL TABLE vocab_tri USING fts5(word, tokenize = 'trigram');

CREATE TABLE official_settlements (
    code INTEGER PRIMARY KEY, name TEXT, name_en TEXT, district TEXT, council TEXT, matched INTEGER DEFAULT 0
);
CREATE TABLE official_streets (
    settlement_code INTEGER, settlement TEXT, code INTEGER, name TEXT, matched INTEGER DEFAULT 0
);
"""


def create(path: str | Path) -> sqlite3.Connection:
    p = Path(path)
    if p.exists():
        p.unlink()
    con = sqlite3.connect(p)
    con.executescript(DDL)
    con.execute("INSERT INTO meta VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
    return con


def open_readonly(path: str | Path) -> sqlite3.Connection:
    """Open the dataset read-only; safe to share across threads."""
    uri = f"file:{Path(path).as_posix()}?mode=ro&immutable=1"
    con = sqlite3.connect(uri, uri=True, check_same_thread=False, cached_statements=256)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only = 1")
    con.execute("PRAGMA mmap_size = 268435456")
    con.execute("PRAGMA cache_size = -65536")
    return con


def finalize(con: sqlite3.Connection) -> None:
    """Optimize indexes and compact the file before it goes live."""
    con.execute("INSERT INTO search_fts(search_fts) VALUES ('optimize')")
    con.execute("INSERT INTO vocab_tri(vocab_tri) VALUES ('optimize')")
    con.commit()
    con.execute("ANALYZE")
    con.commit()
    con.execute("VACUUM")


# --------------------------------------------------------------------------- publishing
# Windows cannot replace a file that another process (the running server) has open,
# so new builds get a versioned name and a small pointer file says which one is live.

def pointer_of(path: Path) -> Path:
    return path.with_name(path.stem + ".current")


def resolve(path: str | Path) -> Path:
    """The live database for a logical path like ``data/maps.db``."""
    path = Path(path)
    ptr = pointer_of(path)
    try:
        target = path.with_name(ptr.read_text(encoding="utf-8").strip())
        if target.name and target.exists():
            return target
    except OSError:
        pass
    return path


def publish(built: Path, logical: Path) -> Path:
    """Make *built* the live version of *logical*; old versions are removed when possible."""
    import os
    import time

    stamp = time.strftime("%Y%m%d-%H%M%S")
    final = logical.with_name(f"{logical.stem}-{stamp}{logical.suffix}")
    os.replace(built, final)
    ptr = pointer_of(logical)
    tmp = ptr.with_suffix(".tmp")
    tmp.write_text(final.name, encoding="utf-8")
    os.replace(tmp, ptr)
    versions = sorted(logical.parent.glob(f"{logical.stem}-*{logical.suffix}"))
    for old in versions[:-2]:  # keep the live one and the previous one
        try:
            old.unlink()
        except OSError:
            pass  # still open by the server – removed next time
    if logical.exists():
        try:
            logical.unlink()  # legacy un-versioned file
        except OSError:
            pass
    return final
