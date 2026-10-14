"""Shared fixtures: build the sample database once, run the API in a subprocess."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_built: dict[str, Path] = {}


def sample_db(bulk: int = 0) -> Path:
    key = f"bulk{bulk}"
    if key in _built:
        return _built[key]
    from hmaps.core import db
    from hmaps.etl import build, osm, quality, search_index
    from tools.sample_data import add_sample_hikes, extras, generate

    tmp = Path(tempfile.mkdtemp(prefix="hmaps-test-"))
    poi, streets = generate(tmp / "src", bulk=bulk)
    lines, rels = extras(tmp / "src")
    out = tmp / "maps.db"
    con = db.create(out)
    stats = build.run(con, osm.iter_features(poi), osm.iter_features(streets),
                      line_features=osm.iter_features(lines), relations=osm.iter_relations(rels))
    search_index.build(con)
    add_sample_hikes(con)
    con.execute("INSERT OR REPLACE INTO meta VALUES ('built', 'test')")
    con.commit()
    quality.write(quality.compute(con, stats.as_dict()), tmp / "reports")
    db.finalize(con)
    con.close()
    _built[key] = out
    return out


def make_mbtiles(path: Path) -> Path:
    """A tiny valid MBTiles file (one gzipped tile) standing in for the Planetiler output."""
    import gzip
    import sqlite3

    from hmaps.api import mvt

    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE metadata (name TEXT, value TEXT)")
    con.execute("CREATE TABLE tiles (zoom_level INTEGER, tile_column INTEGER, tile_row INTEGER, tile_data BLOB)")
    meta = {"name": "test", "format": "pbf", "minzoom": "0", "maxzoom": "14", "bounds": "34.2,29.4,35.9,33.4",
            "json": json.dumps({"vector_layers": [{"id": "water", "fields": {}}, {"id": "place", "fields": {"name": "String"}}]})}
    con.executemany("INSERT INTO metadata VALUES (?, ?)", meta.items())
    layer = mvt.Layer("place")
    layer.add_point(2048, 2048, {"name": "תל אביב", "class": "city"})
    data = gzip.compress(mvt.encode_tile([layer]))
    # z=10 tile containing Tel Aviv: x=610, y=418 (TMS row = 1023 - 418)
    con.execute("INSERT INTO tiles VALUES (10, 610, ?, ?)", (1023 - 418, data))
    con.commit()
    con.close()
    return path


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class ApiServer:
    def __init__(self, data_dir: Path, osrm_port: int | None = None, extra_env: dict | None = None):
        self.port = free_port()
        env = dict(os.environ)
        env.update({
            "HMAPS_DATA": str(data_dir), "HMAPS_SAMPLE_DB": str(data_dir / "none.db"),
            "HMAPS_RATE_PER_SEC": "1000", "HMAPS_RATE_BURST": "1000", "PYTHONPATH": str(ROOT),
        })
        if osrm_port:
            for i, m in enumerate(("CAR", "FOOT", "BIKE")):
                env[f"HMAPS_OSRM_{m}"] = f"http://127.0.0.1:{osrm_port}"
        else:
            for m in ("CAR", "FOOT", "BIKE"):
                env[f"HMAPS_OSRM_{m}"] = "http://127.0.0.1:9"  # nothing listens there
        env.update(extra_env or {})
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "hmaps.api.app:app", "--port", str(self.port), "--log-level", "warning"],
            cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        for _ in range(100):
            try:
                urllib.request.urlopen(self.url("/api/health"), timeout=1)
                break
            except urllib.error.HTTPError:
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.1)

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def get(self, path: str, **params) -> tuple[int, dict | bytes, dict]:
        q = ("?" + urllib.parse.urlencode(params, doseq=True)) if params else ""
        try:
            with urllib.request.urlopen(self.url(path + q), timeout=10) as r:
                body = r.read()
                headers = dict(r.headers)
                status = r.status
        except urllib.error.HTTPError as e:
            body, headers, status = e.read(), dict(e.headers), e.code
        headers = {k.lower(): v for k, v in headers.items()}
        if headers.get("content-type", "").startswith("application/json"):
            return status, json.loads(body), headers
        return status, body, headers

    def post(self, path: str, payload: dict) -> tuple[int, dict]:
        req = urllib.request.Request(self.url(path), data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def stop(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
