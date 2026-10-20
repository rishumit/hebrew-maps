"""Satellite (govmap orthophoto) layer: offline MBTiles cache, CDN fallback, area download.

The real CDN is never contacted: unit tests inject a fake fetch function, and the
API tests point ``HMAPS_SAT_URL`` at a tiny local HTTP server.
"""

import http.server
import tempfile
import threading
import time
import unittest
from pathlib import Path

from hmaps.api import satellite as S
from hmaps.core import geo
from tests.helpers import ApiServer, free_port

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 60 + b"fake-jpeg"
TLV = (34.7818, 32.0853)  # Tel Aviv (lon, lat)


def tlv_tile(z: int) -> tuple[int, int, int]:
    return (z, *geo.lonlat_to_tile(*TLV, z))


class FakeFetch:
    """Counts calls; returns a JPEG, nothing (``None``) or raises Offline."""

    def __init__(self, mode: str = "ok"):
        self.mode = mode
        self.calls: list[tuple[int, int, int]] = []
        self.lock = threading.Lock()

    def __call__(self, z, x, y):
        with self.lock:
            self.calls.append((z, x, y))
        if self.mode == "offline":
            raise S.Offline("no network")
        if self.mode == "missing":
            return None
        return JPEG + f"{z}/{x}/{y}".encode()


class CacheTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="hmaps-sat-"))
        self.path = self.tmp / "tiles" / "satellite.mbtiles"

    def sat(self, mode="ok", backoff=30.0):
        s = S.Satellite(self.path, fetch=FakeFetch(mode), backoff=backoff)
        self.addCleanup(s.cache.close)
        return s

    def test_put_get_uses_tms_rows(self):
        c = S.TileCache(self.path)
        self.addCleanup(c.close)
        self.assertIsNone(c.get(12, 2443, 1663))
        self.assertFalse(self.path.exists())  # reading never creates the file
        c.put(12, 2443, 1663, JPEG)
        self.assertEqual(c.get(12, 2443, 1663), JPEG)
        import sqlite3
        con = sqlite3.connect(self.path)
        row = con.execute("SELECT zoom_level, tile_column, tile_row FROM tiles").fetchone()
        meta = dict(con.execute("SELECT name, value FROM metadata"))
        con.close()
        self.assertEqual(row, (12, 2443, (1 << 12) - 1 - 1663))
        self.assertEqual(meta["format"], "jpg")
        self.assertEqual(c.existing(12, 2440, 2445, 1660, 1665), {(2443, 1663)})
        self.assertEqual(c.stats()["tiles"], 1)

    def test_miss_fetches_and_stores_then_hits_cache(self):
        s = self.sat()
        z, x, y = tlv_tile(15)
        data, src = s.tile(z, x, y)
        self.assertEqual(src, "cdn")
        self.assertTrue(data.startswith(b"\xff\xd8"))
        self.assertEqual(s.fetch.calls, [(z, x, y)])
        data2, src2 = s.tile(z, x, y)
        self.assertEqual((data2, src2), (data, "cache"))
        self.assertEqual(len(s.fetch.calls), 1)  # second request never left the machine
        # a fresh service on the same file (server restart, no internet) still has it
        offline = self.sat("offline")
        self.assertEqual(offline.tile(z, x, y), (data, "cache"))
        self.assertEqual(offline.fetch.calls, [])

    def test_offline_returns_quickly_and_backs_off(self):
        s = self.sat("offline", backoff=30)
        z, x, y = tlv_tile(14)
        self.assertEqual(s.tile(z, x, y), (None, "offline"))
        self.assertTrue(s.offline)
        t0 = time.monotonic()
        for dx in range(20):
            self.assertEqual(s.tile(z, x + dx, y), (None, "offline"))
        self.assertLess(time.monotonic() - t0, 0.5)
        self.assertEqual(len(s.fetch.calls), 1)  # the back-off skips the CDN for the other tiles
        # after the back-off the CDN is tried again
        s._offline_until = 0
        s.fetch.mode = "ok"
        self.assertEqual(s.tile(z, x, y)[1], "cdn")
        self.assertFalse(s.offline)

    def test_missing_and_outside(self):
        s = self.sat("missing")
        self.assertEqual(s.tile(*tlv_tile(16)), (None, "missing"))
        self.assertFalse(s.offline)  # "no such tile" is not a network failure
        self.assertEqual(s.tile(*tlv_tile(5)), (None, "outside"))  # below the photo's zoom range
        self.assertEqual(s.tile(12, *geo.lonlat_to_tile(2.35, 48.85, 12)), (None, "outside"))  # Paris
        self.assertEqual(len(s.fetch.calls), 1)


class EnumerationTest(unittest.TestCase):
    BBOX = (34.76, 32.06, 34.80, 32.10)

    def test_count_matches_iteration(self):
        tiles = list(S.iter_tiles(self.BBOX, 12, 15))
        self.assertEqual(len(tiles), S.count_tiles(self.BBOX, 12, 15))
        self.assertEqual(len(set(tiles)), len(tiles))
        self.assertEqual([t[0] for t in tiles], sorted(t[0] for t in tiles))  # coarse zooms first
        for z, x, y in tiles:  # every tile touches the bbox
            w, s, e, n = geo.tile_bounds(z, x, y)
            self.assertTrue(w <= self.BBOX[2] and e >= self.BBOX[0] and s <= self.BBOX[3] and n >= self.BBOX[1])
        # one tile per zoom at a coarse zoom, 4x as many per extra zoom (roughly)
        self.assertEqual(S.count_tiles(self.BBOX, 7, 7), 1)
        self.assertGreater(S.count_tiles(self.BBOX, 16, 16), 3 * S.count_tiles(self.BBOX, 15, 15))

    def test_clipping_and_parsing(self):
        self.assertEqual(S.count_tiles((2.0, 48.0, 3.0, 49.0), 7, 12), 0)  # outside Israel
        self.assertEqual(S.count_tiles(self.BBOX, 3, 6), 0)  # below zoom 7
        self.assertEqual(S.count_tiles(self.BBOX, 15, 12), S.count_tiles(self.BBOX, 12, 15))
        self.assertEqual(S.parse_bbox("34.8,32.1,34.7,32.0"), (34.7, 32.0, 34.8, 32.1))
        self.assertEqual(S.parse_bbox([34.7, 32.0, 34.8, 32.1]), (34.7, 32.0, 34.8, 32.1))
        for bad in ("1,2,3", "a,b,c,d", "nan,1,2,3", None):
            with self.assertRaises((ValueError, TypeError, AttributeError)):
                S.parse_bbox(bad)
        # all of Israel at z7..13 is a few thousand tiles
        self.assertTrue(3000 < S.count_tiles(S.BOUNDS, 7, 13) < 10000)


class JobTest(unittest.TestCase):
    BBOX = (34.76, 32.06, 34.80, 32.10)

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="hmaps-satjob-"))
        self.sat = S.Satellite(self.tmp / "sat.mbtiles", fetch=FakeFetch())
        self.addCleanup(self.sat.cache.close)

    def run_job(self, **kw):
        job = S.Job(self.sat, self.BBOX, 12, 15, **kw)
        job.run()
        return job

    def test_download_then_resume(self):
        total = S.count_tiles(self.BBOX, 12, 15)
        est = self.sat.estimate(self.BBOX, 12, 15)
        self.assertEqual((est["tiles"], est["cached"], est["missing"]), (total, 0, total))
        self.assertGreater(est["bytes_estimate"], 0)
        job = self.run_job(workers=3)
        p = job.progress()
        self.assertEqual(p["state"], "done")
        self.assertEqual((p["total"], p["done"], p["fetched"], p["skipped"]), (total, total, total, 0))
        self.assertEqual(p["percent"], 100.0)
        self.assertEqual(sorted(self.sat.fetch.calls), sorted(S.iter_tiles(self.BBOX, 12, 15)))
        self.assertEqual(self.sat.cache.stats()["tiles"], total)
        # resume: nothing is fetched twice
        self.sat.fetch.calls.clear()
        p = self.run_job().progress()
        self.assertEqual((p["fetched"], p["skipped"], p["state"]), (0, total, "done"))
        self.assertEqual(self.sat.fetch.calls, [])
        self.assertEqual(self.sat.estimate(self.BBOX, 12, 15)["missing"], 0)

    def test_cap_and_offline(self):
        with self.assertRaises(ValueError):
            S.Job(self.sat, self.BBOX, 12, 18, max_tiles=100)
        self.sat.fetch.mode = "offline"
        p = self.run_job(workers=2).progress()
        self.assertEqual(p["state"], "error")
        self.assertIn("offline", p["error"])
        # stops after a handful of failures instead of trying every tile
        self.assertLess(len(self.sat.fetch.calls), 30)

    def test_service_runs_one_job_at_a_time(self):
        gate = threading.Event()
        real = self.sat.fetch

        def slow(z, x, y):
            gate.wait(5)
            return real(z, x, y)

        self.sat.fetch = slow
        job = self.sat.start_job(self.BBOX, 12, 13)
        with self.assertRaises(RuntimeError):
            self.sat.start_job(self.BBOX, 12, 13)
        gate.set()
        for _ in range(200):
            if job.state not in ("pending", "running"):
                break
            time.sleep(0.02)
        self.assertEqual(job.state, "done")
        self.assertEqual(self.sat.status()["job"]["state"], "done")


# ----------------------------------------------------------------------------- HTTP

class FakeCdn(http.server.BaseHTTPRequestHandler):
    """``/{z}/{y}/{x}.jpg`` like the govmap CDN; ``/404/…`` paths answer 404, ``/html/…`` a filter page."""

    hits: list[str] = []

    def do_GET(self):  # noqa: N802
        FakeCdn.hits.append(self.path)
        if self.headers.get("Referer") != S.REFERER:
            self.send_response(403)
            self.end_headers()
            return
        if self.path.startswith("/404/"):
            self.send_response(404)
            self.end_headers()
            return
        body = b"<html>blocked</html>" if self.path.startswith("/html/") else JPEG + self.path.encode()
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def serve_cdn() -> tuple[http.server.ThreadingHTTPServer, int]:
    port = free_port()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), FakeCdn)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, port


class FetchTileTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv, cls.port = serve_cdn()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def fetch(self, prefix=""):
        old = S.CDN_URL
        S.CDN_URL = f"http://127.0.0.1:{self.port}{prefix}/{{z}}/{{y}}/{{x}}.jpg"
        try:
            return S.fetch_tile(15, 19549, 13304, timeout=3)
        finally:
            S.CDN_URL = old

    def test_fetch_variants(self):
        FakeCdn.hits.clear()
        self.assertTrue(self.fetch().startswith(b"\xff\xd8"))
        self.assertEqual(FakeCdn.hits[-1], "/15/13304/19549.jpg")  # the CDN wants {z}/{y}/{x}
        self.assertIsNone(self.fetch("/404"))
        self.assertIsNone(self.fetch("/html"))  # never cache a filter / error page
        old = S.CDN_URL
        S.CDN_URL = f"http://127.0.0.1:{free_port()}/{{z}}/{{y}}/{{x}}.jpg"  # nothing listens
        try:
            with self.assertRaises(S.Offline):
                S.fetch_tile(15, 1, 1, timeout=2)
        finally:
            S.CDN_URL = old


class SatelliteApiTest(unittest.TestCase):
    BBOX = [34.776, 32.08, 34.786, 32.09]

    @classmethod
    def setUpClass(cls):
        cls.cdn, port = serve_cdn()
        cls.data = Path(tempfile.mkdtemp(prefix="hmaps-satapi-"))
        cls.api = ApiServer(cls.data, extra_env={"HMAPS_SAT_URL": f"http://127.0.0.1:{port}/{{z}}/{{y}}/{{x}}.jpg"})
        off = Path(tempfile.mkdtemp(prefix="hmaps-satoff-"))
        cls.offline = ApiServer(off, extra_env={"HMAPS_SAT_URL": f"http://127.0.0.1:{free_port()}/{{z}}/{{y}}/{{x}}.jpg"})

    @classmethod
    def tearDownClass(cls):
        cls.api.stop()
        cls.offline.stop()
        cls.cdn.shutdown()

    def test_tile_cache_miss_then_hit(self):
        z = 18
        x, y = geo.lonlat_to_tile(35.2345, 31.7767, z)  # Jerusalem, away from the download test's area
        s, body, h = self.api.get(f"/tiles/satellite/{z}/{x}/{y}.jpg")
        self.assertEqual(s, 200)
        self.assertEqual(h["content-type"], "image/jpeg")
        self.assertEqual(h["x-tile-source"], "cdn")
        self.assertTrue(body.startswith(b"\xff\xd8"))
        s, body2, h = self.api.get(f"/tiles/satellite/{z}/{x}/{y}.jpg")
        self.assertEqual((s, body2, h["x-tile-source"]), (200, body, "cache"))
        self.assertTrue((self.data / "tiles" / "satellite.mbtiles").exists())
        s, _, h = self.api.get(f"/tiles/satellite/{z}/{x}/99999999.jpg")
        self.assertEqual(s, 400)
        s, _, h = self.api.get("/tiles/satellite/4/9/6.jpg")
        self.assertEqual((s, h["x-tile-source"]), (204, "outside"))

    def test_offline_is_204_fast(self):
        t0 = time.monotonic()
        for i in range(10):
            z, x, y = tlv_tile(17)
            s, _, h = self.offline.get(f"/tiles/satellite/{z}/{x + i}/{y}.jpg")
            self.assertEqual((s, h["x-tile-source"]), (204, "offline"))
        self.assertLess(time.monotonic() - t0, 5)
        s, st, _ = self.offline.get("/api/satellite/status")
        self.assertTrue(st["offline"])

    def test_estimate_download_status(self):
        s, est, _ = self.api.get("/api/satellite/estimate", bbox=",".join(map(str, self.BBOX)), minzoom=14, maxzoom=16)
        self.assertEqual(s, 200)
        total = S.count_tiles(self.BBOX, 14, 16)
        self.assertEqual(est["tiles"], total)
        self.assertFalse(est["too_many"])
        s, body = self.api.post("/api/satellite/download", {"bbox": self.BBOX, "minzoom": 14, "maxzoom": 16})
        self.assertEqual(s, 202)
        self.assertEqual(body["job"]["total"], total)
        for _ in range(200):
            s, st, h = self.api.get("/api/satellite/status")
            if st["job"]["state"] not in ("pending", "running"):
                break
            time.sleep(0.05)
        self.assertEqual(st["job"]["state"], "done")
        self.assertEqual(st["job"]["done"], total)
        self.assertGreaterEqual(st["cache"]["tiles"], total)
        self.assertGreater(st["cache"]["disk_bytes"], 0)
        self.assertEqual(h["cache-control"], "no-store")
        s, est, _ = self.api.get("/api/satellite/estimate", bbox=",".join(map(str, self.BBOX)), minzoom=14, maxzoom=16)
        self.assertEqual(est["missing"], 0)

    def test_download_validation(self):
        s, body = self.api.post("/api/satellite/download", {"bbox": [34.2, 29.4, 35.9, 33.4], "minzoom": 7, "maxzoom": 18})
        self.assertEqual((s, body["error"]["code"]), (413, "too_many_tiles"))
        s, body = self.api.post("/api/satellite/download", {"bbox": [2, 48, 3, 49], "minzoom": 10, "maxzoom": 12})
        self.assertEqual((s, body["error"]["code"]), (400, "outside_coverage"))
        s, body = self.api.post("/api/satellite/download", {"bbox": "oops"})
        self.assertEqual(s, 400)
        s, body, _ = self.api.get("/api/satellite/estimate")
        self.assertEqual(s, 400)


if __name__ == "__main__":
    unittest.main()
