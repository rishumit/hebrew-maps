"""Satellite layer: sources (govmap ITM cache, XYZ fallback), offline MBTiles caches, area downloads.

The real servers are never contacted: unit tests inject fake fetch functions / sources, and
the HTTP tests point the sources at a tiny local server.
"""

import contextlib
import http.server
import io
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from hmaps.api import satellite as S
from hmaps.core import geo, itm
from tests.helpers import ApiServer, free_port

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 60 + b"fake-jpeg"
TLV = (34.7818, 32.0853)  # Tel Aviv (lon, lat)
S3_DENIED = b'<?xml version="1.0" encoding="UTF-8"?>\n<Error><Code>AccessDenied</Code><Message>Access Denied</Message></Error>'
NETFREE = b"<!DOCTYPE html><html><head><title>NetFree</title></head><body>\xd7\x94\xd7\x90\xd7\xaa\xd7\xa8 \xd7\x97\xd7\xa1\xd7\x95\xd7\x9d</body></html>"

try:
    import numpy  # noqa: F401
    from PIL import Image
    HAVE_PIL = True
except ImportError:  # pragma: no cover
    HAVE_PIL = False


def tlv_tile(z: int) -> tuple[int, int, int]:
    return (z, *geo.lonlat_to_tile(*TLV, z))


def real_jpeg(color=(90, 120, 60)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (256, 256), color).save(buf, "JPEG", quality=90)
    return buf.getvalue()


class FakeFetch:
    """Counts calls; returns a JPEG, nothing (``None``) or raises Offline / Blocked."""

    def __init__(self, mode: str = "ok"):
        self.mode = mode
        self.calls: list[tuple[int, int, int]] = []
        self.lock = threading.Lock()

    def __call__(self, z, x, y):
        with self.lock:
            self.calls.append((z, x, y))
        if self.mode == "offline":
            raise S.Offline("no network")
        if self.mode == "blocked":
            raise S.Blocked("blocked: HTTP 418 text/html")
        if self.mode == "missing":
            return None
        return JPEG + f"{z}/{x}/{y}".encode()


def fake_source(id_, mode="ok", max_zoom=19, primary=False):
    src = S.FuncSource(FakeFetch(mode), id=id_)
    src.max_zoom = max_zoom
    src.primary_cache = primary
    return src


# ============================================================================= cache + lookup

class CacheTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="hmaps-sat-"))
        self.path = self.tmp / "tiles" / "satellite.mbtiles"

    def sat(self, mode="ok", backoff=30.0):
        s = S.Satellite(self.path, fetch=FakeFetch(mode), backoff=backoff)
        self.addCleanup(s.close)
        return s

    def test_put_get_uses_tms_rows(self):
        c = S.TileCache(self.path)
        self.addCleanup(c.close)
        self.assertIsNone(c.get(12, 2443, 1663))
        self.assertFalse(self.path.exists())  # reading never creates the file
        c.put(12, 2443, 1663, JPEG)
        self.assertEqual(c.get(12, 2443, 1663), JPEG)
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
        fetch = s.layers[0].source.fn
        self.assertEqual(fetch.calls, [(z, x, y)])
        self.assertEqual(s.tile(z, x, y), (data, "cache"))
        self.assertEqual(len(fetch.calls), 1)  # second request never left the machine
        offline = self.sat("offline")  # a fresh service on the same file (restart, no internet)
        self.assertEqual(offline.tile(z, x, y), (data, "cache"))
        self.assertEqual(offline.layers[0].source.fn.calls, [])

    def test_offline_returns_quickly_and_backs_off(self):
        s = self.sat("offline", backoff=30)
        z, x, y = tlv_tile(14)
        self.assertEqual(s.tile(z, x, y), (None, "offline"))
        self.assertTrue(s.offline)
        t0 = time.monotonic()
        for dx in range(20):
            self.assertEqual(s.tile(z, x + dx, y), (None, "offline"))
        self.assertLess(time.monotonic() - t0, 0.5)
        fetch = s.layers[0].source.fn
        self.assertEqual(len(fetch.calls), 1)  # the back-off skips the source for the other tiles
        s.layers[0]._offline_until = 0
        fetch.mode = "ok"
        self.assertEqual(s.tile(z, x, y)[1], "cdn")
        self.assertFalse(s.offline)

    def test_missing_and_outside(self):
        s = self.sat("missing")
        self.assertEqual(s.tile(*tlv_tile(16)), (None, "missing"))
        self.assertFalse(s.offline)  # "no such tile" is not a network failure
        self.assertEqual(s.tile(*tlv_tile(5)), (None, "outside"))  # below the layer's zoom range
        self.assertEqual(s.tile(12, *geo.lonlat_to_tile(2.35, 48.85, 12)), (None, "outside"))  # Paris
        self.assertEqual(len(s.layers[0].source.fn.calls), 1)


class FallbackTest(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="hmaps-satfb-")) / "satellite.mbtiles"

    def make(self, *sources):
        s = S.Satellite(self.base, sources=list(sources), backoff=30)
        self.addCleanup(s.close)
        return s

    def test_blocked_primary_falls_back_and_caches_separately(self):
        s = self.make(fake_source("govmap", "blocked", primary=True), fake_source("eox", max_zoom=14))
        z, x, y = tlv_tile(13)
        data, how, provider = s.tile_info(z, x, y)
        self.assertEqual((how, provider), ("cdn", "eox"))
        self.assertTrue(s.layers[0].offline)
        self.assertIn("blocked", s.layers[0].last_error)
        self.assertEqual(s.layers[1].cache.path.name, "satellite-eox.mbtiles")
        self.assertIsNone(s.layers[0].cache.get(z, x, y))  # the fallback never fills govmap's cache
        self.assertEqual(s.layers[1].cache.get(z, x, y), data)
        st = s.status()
        self.assertEqual([d["id"] for d in st["sources"]], ["govmap", "eox"])
        self.assertTrue(st["sources"][0]["offline"])
        self.assertFalse(st["offline"])

    def test_online_primary_wins_over_cached_fallback(self):
        prim, fb = fake_source("govmap", primary=True), fake_source("eox", max_zoom=14)
        s = self.make(prim, fb)
        z, x, y = tlv_tile(12)
        s.layers[1].cache.put(z, x, y, b"\xff\xd8\xff-old-eox")
        data, how, provider = s.tile_info(z, x, y)
        self.assertEqual((how, provider), ("cdn", "govmap"))
        prim.fn.mode = "offline"
        s.layers[0].cache.close()
        s2 = self.make(fake_source("govmap", "offline", primary=True), fake_source("eox", "offline", max_zoom=14))
        self.assertEqual(s2.tile_info(z, x, y + 1)[1], "offline")
        self.assertEqual(s2.tile_info(z, x, y)[2], "govmap")  # its own cache first

    def test_missing_primary_tile_uses_fallback(self):
        s = self.make(fake_source("govmap", "missing", primary=True), fake_source("eox", max_zoom=14))
        self.assertEqual(s.tile_info(*tlv_tile(10))[2], "eox")

    @unittest.skipUnless(HAVE_PIL, "Pillow")
    def test_overzoom_above_the_fallbacks_deepest_zoom(self):
        fb = S.FuncSource(lambda z, x, y: real_jpeg((200, 10, 10)), id="eox")
        fb.max_zoom = 14
        s = self.make(fake_source("govmap", "offline", primary=True), fb)
        data, how, provider = s.tile_info(*tlv_tile(16))
        self.assertEqual(provider, "eox")
        with Image.open(io.BytesIO(data)) as im:
            self.assertEqual(im.size, (256, 256))
        z, x, y = tlv_tile(16)
        self.assertIsNone(s.layers[1].cache.get(z, x, y))  # only the z14 ancestor is stored
        self.assertIsNotNone(s.layers[1].cache.get(14, x >> 2, y >> 2))


class SourceConfigTest(unittest.TestCase):
    def test_make_and_configure(self):
        with mock.patch.dict("os.environ", {"HMAPS_SATELLITE_SOURCE": "", "HMAPS_SAT_URL": ""}):
            self.assertEqual([s.id for s in S.configured_sources()], ["govmap", "eox"])
        with mock.patch.dict("os.environ", {"HMAPS_SATELLITE_SOURCE": "eox-2016, govmap-2022,eox-2016"}):
            self.assertEqual([s.id for s in S.configured_sources()], ["eox-2016", "govmap-2022"])
        with mock.patch.dict("os.environ", {"HMAPS_SATELLITE_SOURCE": "", "HMAPS_SAT_URL": "http://h/{z}/{x}/{y}.png"}):
            self.assertEqual([s.id for s in S.configured_sources()], ["custom", "govmap", "eox"])
        with self.assertRaises(ValueError):
            S.make_source("bing")
        g = S.make_source("govmap")
        self.assertEqual(g.cache, "B0BZ1ORTO23")
        self.assertTrue(g.primary_cache)
        self.assertFalse(S.make_source("govmap-2023").primary_cache)
        eox = S.make_source("eox")
        self.assertEqual(eox.tile_url(14, 9774, 6648),
                         "https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2024_3857/default/g/14/6648/9774.jpg")
        self.assertIn("NC", eox.license)
        self.assertEqual(S.make_source("eox-2016").license, "CC BY 4.0")
        tms = S.XyzSource("t", "http://h/{z}/{x}/{-y}.png")
        self.assertEqual(tms.tile_url(3, 1, 2), "http://h/3/1/5.png")

    def test_cache_paths(self):
        base = Path("/d/tiles/satellite.mbtiles")
        self.assertEqual(S.cache_path(base, S.make_source("govmap")), base)
        self.assertEqual(S.cache_path(base, S.make_source("eox")).name, "satellite-eox.mbtiles")


# ============================================================================= HTTP classification

class ClassifyTest(unittest.TestCase):
    def test_classify(self):
        self.assertEqual(S.classify(200, "image/jpeg", JPEG), "image")
        self.assertEqual(S.classify(403, "application/xml", S3_DENIED), "missing")  # S3: no such key
        self.assertEqual(S.classify(404, "text/plain", b"nope"), "missing")
        self.assertEqual(S.classify(200, "text/html; charset=utf-8", NETFREE), "blocked")
        self.assertEqual(S.classify(418, "text/html", NETFREE), "blocked")
        self.assertEqual(S.classify(200, "image/jpeg", NETFREE), "blocked")  # lying content type
        self.assertEqual(S.classify(403, "text/html", b"<HTML><HEAD><TITLE>ERROR: The request could not be satisfied"),
                         "blocked")  # CloudFront geo-block
        self.assertEqual(S.classify(500, "text/plain", b"oops"), "error")
        self.assertEqual(S.classify(200, "image/jpeg", b""), "missing")


class FakeServer(http.server.BaseHTTPRequestHandler):
    """``/ok/…`` a JPEG, ``/s3/…`` S3 AccessDenied, ``/netfree/…`` an HTML page with 418,
    ``/html200/…`` HTML with 200, ``/govmap/<cache>/L../R../C...jpg`` an ITM tile (missing for odd rows)."""

    hits: list[str] = []

    def do_GET(self):  # noqa: N802
        FakeServer.hits.append(self.path)
        p = self.path
        if p.startswith("/ok/"):
            return self._send(200, "image/jpeg", JPEG + p.encode())
        if p.startswith("/s3/"):
            return self._send(403, "application/xml", S3_DENIED)
        if p.startswith("/netfree/"):
            return self._send(418, "text/html; charset=utf-8", NETFREE)
        if p.startswith("/html200/"):
            return self._send(200, "text/html", NETFREE)
        if p.startswith("/govmap/"):
            parts = p.split("/")  # ['', 'govmap', cache, 'L08', 'R…', 'C….jpg']
            row = int(parts[4][1:], 16)
            if FakeServer.missing_rows_mod and row % FakeServer.missing_rows_mod == 1:
                return self._send(403, "application/xml", S3_DENIED)
            return self._send(200, "image/jpeg", real_jpeg((row % 200, 100, 50)))
        return self._send(404, "text/plain", b"not found")

    missing_rows_mod = 0

    def _send(self, status, ctype, body):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def serve() -> tuple[http.server.ThreadingHTTPServer, int]:
    port = free_port()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), FakeServer)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, port


class FetchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv, cls.port = serve()
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_xyz_variants(self):
        ok = S.XyzSource("t", self.base + "/ok/{z}/{x}/{y}.jpg")
        self.assertTrue(ok.fetch(15, 19549, 13304, timeout=3).startswith(b"\xff\xd8"))
        self.assertEqual(FakeServer.hits[-1], "/ok/15/19549/13304.jpg")
        self.assertIsNone(S.XyzSource("t", self.base + "/s3/{z}/{x}/{y}.jpg").fetch(15, 1, 1, timeout=3))
        for kind in ("netfree", "html200"):
            with self.assertRaises(S.Blocked):  # never cached, never "missing": the next source is used
                S.XyzSource("t", f"{self.base}/{kind}/{{z}}/{{x}}/{{y}}.jpg").fetch(15, 1, 1, timeout=3)
        with self.assertRaises(S.Offline):
            S.XyzSource("t", f"http://127.0.0.1:{free_port()}/{{z}}/{{x}}/{{y}}.jpg").fetch(15, 1, 1, timeout=2)

    def test_diagnose_reports_status_type_size(self):
        srcs = [S.XyzSource("good", self.base + "/ok/{z}/{x}/{y}.jpg"),
                S.XyzSource("filtered", self.base + "/netfree/{z}/{x}/{y}.jpg"),
                S.XyzSource("dead", f"http://127.0.0.1:{free_port()}/{{z}}/{{x}}/{{y}}.jpg")]
        res = {r["source"]: r for r in S.diagnose(srcs, timeout=3)}
        self.assertTrue(res["good"]["ok"])
        c = res["good"]["checks"][0]
        self.assertEqual((c["status"], c["content_type"], c["kind"]), (200, "image/jpeg", "image"))
        self.assertGreater(c["bytes"], 0)
        c = res["filtered"]["checks"][0]
        self.assertEqual((c["status"], c["kind"], c["ok"]), (418, "blocked", False))
        self.assertIn("NetFree", c["snippet"])
        self.assertFalse(res["filtered"]["ok"])
        self.assertEqual(res["dead"]["checks"][0]["kind"], "network")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = S.cmd_test(None, srcs)
        self.assertEqual(rc, 0)
        self.assertIn("HTTP 418", out.getvalue())
        self.assertIn("good", out.getvalue())


# ============================================================================= govmap ITM

class ItmTest(unittest.TestCase):
    def test_matches_proj(self):
        # reference values: PROJ 9 (pyproj) EPSG:4326 -> EPSG:2039
        cases = [((34.77417, 32.07780), (178832.381, 665026.555)),
                 ((35.2045169444444, 31.7343936111111), (219463.548, 626866.427)),
                 ((34.95, 29.55), (194796.112, 384721.165)),
                 ((35.57, 33.27), (253515.028, 797220.194))]
        for (lon, lat), (e, n) in cases:
            x, y = itm.from_wgs84(lon, lat)
            self.assertAlmostEqual(float(x), e, delta=0.05)
            self.assertAlmostEqual(float(y), n, delta=0.05)


@unittest.skipUnless(HAVE_PIL, "numpy + Pillow")
class GovmapItmTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv, cls.port = serve()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def src(self):
        g = S.make_source("govmap")
        g.HOST = f"http://127.0.0.1:{self.port}/govmap"
        return g

    def test_levels(self):
        g = S.make_source("govmap")
        self.assertEqual(g.level_for(2000), 0)
        self.assertEqual(g.level_for(2.0), 8)      # 1.32 m/px for a ~2 m/px map tile (z16)
        self.assertEqual(g.level_for(1.5), 8)      # 2.65 would be too coarse
        self.assertEqual(g.level_for(2.3), 7)      # 2.65 m/px is within the 20 % allowance
        self.assertEqual(g.level_for(0.01), 11)    # deeper than the cache: the finest level
        self.assertEqual(g.raw_url(11, 0x2e844, 0x283e8),
                         "https://cdn.govmap.gov.il/B0BZ1ORTO23/L11/R0002e844/C000283e8.jpg")

    def test_renders_webmercator_tile_from_itm_tiles(self):
        FakeServer.missing_rows_mod = 0
        FakeServer.hits.clear()
        g = self.src()
        data = g.fetch(*tlv_tile(16), timeout=3)
        self.assertTrue(data.startswith(b"\xff\xd8"))
        with Image.open(io.BytesIO(data)) as im:
            self.assertEqual((im.size, im.mode), ((256, 256), "RGB"))
        hits = [h for h in FakeServer.hits if h.startswith("/govmap/")]
        self.assertTrue(1 <= len(hits) <= 16)
        for h in hits:
            self.assertRegex(h, r"^/govmap/B0BZ1ORTO23/L08/R[0-9a-f]{8}/C[0-9a-f]{8}\.jpg$")
        # the ITM tiles under the map tile are the ones around Tel Aviv
        e, n = itm.from_wgs84(*TLV)
        t = 256 * g.RESOLUTIONS[8]
        row, col = int((g.ORIGIN[1] - float(n)) // t), int((float(e) - g.ORIGIN[0]) // t)
        self.assertIn(f"/govmap/B0BZ1ORTO23/L08/R{row:08x}/C{col:08x}.jpg", hits)
        # neighbouring tiles reuse the source tiles (in-memory LRU)
        before = len(FakeServer.hits)
        z, x, y = tlv_tile(16)
        g.fetch(z, x + 1, y, timeout=3)
        self.assertLess(len(FakeServer.hits) - before, len(hits) + 1)

    def test_partial_coverage_is_transparent_png_and_none_when_empty(self):
        FakeServer.missing_rows_mod = 2  # every odd source row is missing (403 AccessDenied)
        try:
            data = self.src().fetch(*tlv_tile(14), timeout=3)
        finally:
            FakeServer.missing_rows_mod = 0
        self.assertTrue(data.startswith(b"\x89PNG"))
        with Image.open(io.BytesIO(data)) as im:
            self.assertEqual(im.mode, "RGBA")
            alpha = im.getchannel("A").getextrema()
        self.assertEqual(alpha, (0, 255))
        g = self.src()
        g.cache = "nothing-here"
        g.HOST = f"http://127.0.0.1:{self.port}/s3"
        self.assertIsNone(g.fetch(*tlv_tile(14), timeout=3))

    def test_blocked_source_tile_raises(self):
        g = self.src()
        g.HOST = f"http://127.0.0.1:{self.port}/netfree"
        with self.assertRaises(S.Blocked):
            g.fetch(*tlv_tile(15), timeout=3)


# ============================================================================= areas

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

    def test_country_polygon_skips_sea_and_neighbours(self):
        country = S.country_area()
        box = S.Area(S.BOUNDS)
        for z in (13, 15):  # (at coarse zooms the one-tile margin dominates)
            self.assertLess(country.count(z), 0.6 * box.count(z))
        tiles = set(country.tiles(13))
        for lon, lat in [TLV, (34.95, 29.55), (35.57, 33.28), (35.69, 32.99), (35.39, 31.46), (35.82, 33.41),
                         (34.765, 32.08), (35.37, 30.94), (35.44, 31.86)]:  # coast, Eilat, Metula, Golan, Hermon…
            self.assertIn(geo.lonlat_to_tile(lon, lat, 13), tiles, (lon, lat))
        for lon, lat in [(34.5, 32.5), (34.0, 30.5), (35.93, 31.95), (35.5, 33.9)]:  # sea, Sinai, Amman, Lebanon
            self.assertNotIn(geo.lonlat_to_tile(lon, lat, 13), tiles, (lon, lat))
        self.assertEqual(sum(1 for _ in country.tiles(13)), country.count(13))
        self.assertTrue(1500 < country.count(13) < 3000)
        self.assertTrue(25_000 < S.count_tiles(country, 7, 15) < 60_000)


# ============================================================================= jobs

class JobTest(unittest.TestCase):
    BBOX = (34.76, 32.06, 34.80, 32.10)

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="hmaps-satjob-"))
        self.sat = S.Satellite(self.tmp / "sat.mbtiles", fetch=FakeFetch())
        self.addCleanup(self.sat.close)
        self.fetch = self.sat.layers[0].source.fn

    def run_job(self, area=None, minzoom=12, maxzoom=15, **kw):
        job = S.Job(self.sat, area or self.BBOX, minzoom, maxzoom, **kw)
        job.run()
        return job

    def test_download_then_resume(self):
        total = S.count_tiles(self.BBOX, 12, 15)
        est = self.sat.estimate(self.BBOX, 12, 15)
        self.assertEqual((est["tiles"], est["cached"], est["missing"]), (total, 0, total))
        self.assertGreater(est["bytes_estimate"], 0)
        self.assertGreater(est["seconds_estimate"], 0)
        job = self.run_job(workers=3)
        p = job.progress()
        self.assertEqual(p["state"], "done")
        self.assertEqual((p["total"], p["done"], p["fetched"], p["skipped"]), (total, total, total, 0))
        self.assertEqual(p["percent"], 100.0)
        self.assertEqual(sorted(self.fetch.calls), sorted(S.iter_tiles(self.BBOX, 12, 15)))
        self.assertEqual(self.sat.cache.stats()["tiles"], total)
        self.fetch.calls.clear()
        p = self.run_job().progress()
        self.assertEqual((p["fetched"], p["skipped"], p["state"]), (0, total, "done"))
        self.assertEqual(self.fetch.calls, [])
        self.assertEqual(self.sat.estimate(self.BBOX, 12, 15)["missing"], 0)

    def test_country_job_follows_the_polygon(self):
        area = S.country_area()
        p = self.run_job(area, 7, 10).progress()
        self.assertEqual(p["state"], "done")
        self.assertEqual(p["total"], S.count_tiles(area, 7, 10))
        self.assertEqual(p["area"], "israel")
        self.assertEqual(self.sat.cache.stats()["tiles"], p["total"])
        levels = {lv["id"]: lv for lv in self.sat.country_levels()}
        self.assertEqual([levels[k]["maxzoom"] for k in ("basic", "detailed", "full")], [13, 15, 16])
        self.assertEqual(levels["basic"]["cached"], p["total"])
        self.assertLess(levels["basic"]["missing"], levels["detailed"]["missing"])

    def test_cap_and_offline(self):
        with self.assertRaises(ValueError):
            S.Job(self.sat, self.BBOX, 12, 18, max_tiles=100)
        self.fetch.mode = "offline"
        p = self.run_job(workers=2).progress()
        self.assertEqual(p["state"], "error")
        self.assertIn("offline", p["error"])
        self.assertLess(len(self.fetch.calls), 30)  # stops at once instead of trying every tile

    def test_job_uses_the_first_working_source(self):
        sat = S.Satellite(self.tmp / "multi.mbtiles", sources=[fake_source("govmap", "blocked", primary=True),
                                                               fake_source("eox", max_zoom=14)])
        self.addCleanup(sat.close)
        job = S.Job(sat, self.BBOX, 12, 13)
        job.run()
        p = job.progress()
        self.assertEqual((p["state"], p["source"]), ("done", "eox"))
        self.assertEqual(sat.layers[1].cache.stats()["tiles"], p["total"])
        self.assertEqual(sat.layers[0].cache.stats()["tiles"], 0)
        job = S.Job(sat, self.BBOX, 12, 13, source="govmap")  # explicit source: no fallback
        job.run()
        self.assertEqual(job.progress()["state"], "error")

    def test_service_runs_one_job_at_a_time(self):
        gate = threading.Event()
        real = self.fetch.__call__

        def slow(z, x, y):
            gate.wait(5)
            return real(z, x, y)

        self.sat.layers[0].source.fn = slow
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


# ============================================================================= HTTP API

class SatelliteApiTest(unittest.TestCase):
    BBOX = [34.776, 32.08, 34.786, 32.09]

    @classmethod
    def setUpClass(cls):
        cls.srv, port = serve()
        cls.data = Path(tempfile.mkdtemp(prefix="hmaps-satapi-"))
        cls.api = ApiServer(cls.data, extra_env={"HMAPS_SATELLITE_SOURCE": f"http://127.0.0.1:{port}/ok/{{z}}/{{x}}/{{y}}.jpg"})
        off = Path(tempfile.mkdtemp(prefix="hmaps-satoff-"))
        cls.offline = ApiServer(off, extra_env={
            "HMAPS_SATELLITE_SOURCE": f"http://127.0.0.1:{port}/netfree/{{z}}/{{x}}/{{y}}.jpg,"
                                      f"http://127.0.0.1:{free_port()}/{{z}}/{{x}}/{{y}}.jpg"})

    @classmethod
    def tearDownClass(cls):
        cls.api.stop()
        cls.offline.stop()
        cls.srv.shutdown()

    def test_tile_cache_miss_then_hit(self):
        z = 18
        x, y = geo.lonlat_to_tile(35.2345, 31.7767, z)  # Jerusalem, away from the download test's area
        s, body, h = self.api.get(f"/tiles/satellite/{z}/{x}/{y}.jpg")
        self.assertEqual(s, 200)
        self.assertEqual(h["content-type"], "image/jpeg")
        self.assertEqual((h["x-tile-source"], h["x-tile-provider"]), ("cdn", "custom"))
        self.assertTrue(body.startswith(b"\xff\xd8"))
        s, body2, h = self.api.get(f"/tiles/satellite/{z}/{x}/{y}.jpg")
        self.assertEqual((s, body2, h["x-tile-source"]), (200, body, "cache"))
        self.assertTrue((self.data / "tiles" / "satellite-custom.mbtiles").exists())
        s, _, h = self.api.get(f"/tiles/satellite/{z}/{x}/99999999.jpg")
        self.assertEqual(s, 400)
        s, _, h = self.api.get("/tiles/satellite/4/9/6.jpg")
        self.assertEqual((s, h["x-tile-source"]), (204, "outside"))

    def test_blocked_and_offline_is_204_fast(self):
        t0 = time.monotonic()
        for i in range(10):
            z, x, y = tlv_tile(17)
            s, _, h = self.offline.get(f"/tiles/satellite/{z}/{x + i}/{y}.jpg")
            self.assertEqual((s, h["x-tile-source"]), (204, "offline"))
        self.assertLess(time.monotonic() - t0, 5)
        s, st, _ = self.offline.get("/api/satellite/status")
        self.assertTrue(st["offline"])
        self.assertIn("blocked", st["sources"][0]["error"])

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

    def test_country_levels(self):
        s, body, _ = self.api.get("/api/satellite/country")
        self.assertEqual(s, 200)
        self.assertEqual([lv["id"] for lv in body["levels"]], ["basic", "detailed", "full"])
        for lv in body["levels"]:
            self.assertGreater(lv["tiles"], 0)
            self.assertGreater(lv["bytes_estimate"], 0)
            self.assertGreater(lv["seconds_estimate"], 0)
            self.assertFalse(lv["too_many"])
        s, est, _ = self.api.get("/api/satellite/estimate", area="country", level="detailed")
        self.assertEqual((s, est["tiles"], est["too_many"]), (200, body["levels"][1]["tiles"], False))

    def test_download_validation(self):
        s, body = self.api.post("/api/satellite/download", {"bbox": [34.2, 29.4, 35.9, 33.4], "minzoom": 7, "maxzoom": 18})
        self.assertEqual((s, body["error"]["code"]), (413, "too_many_tiles"))
        s, body = self.api.post("/api/satellite/download", {"bbox": [2, 48, 3, 49], "minzoom": 10, "maxzoom": 12})
        self.assertEqual((s, body["error"]["code"]), (400, "outside_coverage"))
        s, body = self.api.post("/api/satellite/download", {"area": "country", "level": "huge"})
        self.assertEqual(s, 400)
        s, body = self.api.post("/api/satellite/download", {"bbox": "oops"})
        self.assertEqual(s, 400)
        s, body, _ = self.api.get("/api/satellite/estimate")
        self.assertEqual(s, 400)


if __name__ == "__main__":
    unittest.main()
