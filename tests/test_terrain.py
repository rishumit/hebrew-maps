"""Topographic layers: DEM reading, contours, terrain-RGB codecs, elevation API (synthetic data only)."""

import gzip
import json
import shutil
import sqlite3
import struct
import tempfile
import unittest
import urllib.parse
import zlib
from pathlib import Path

import numpy as np

from hmaps.api import elevation as elev_mod
from hmaps.api import mvt
from hmaps.core import dem, geo
from tests.helpers import ApiServer, sample_db

try:
    import contourpy  # noqa: F401
    HAVE_CONTOURPY = True
except ImportError:  # pragma: no cover
    HAVE_CONTOURPY = False

from tools import terrain as T

RES = T.GRID_RES


def hill(h: int, w: int, peak: float = 800.0, base: float = 100.0) -> np.ndarray:
    """A round hill in the middle of an (h, w) patch."""
    yy, xx = np.mgrid[0:h, 0:w]
    r2 = ((yy - h / 2) / (h / 4)) ** 2 + ((xx - w / 2) / (w / 4)) ** 2
    return base + (peak - base) * np.exp(-r2)


def write_tiff(path: Path, arr: np.ndarray, west: float, north: float, res: float, tile: int = 64) -> None:
    """Minimal tiled, deflate, floating-point-predictor (3) GeoTIFF – the Copernicus COG layout."""
    arr = np.asarray(arr, dtype="<f4")
    h, w = arr.shape
    tiles = []
    for ty in range(0, h, tile):
        for tx in range(0, w, tile):
            block = np.zeros((tile, tile), dtype=">f4")
            part = arr[ty:ty + tile, tx:tx + tile]
            block[:part.shape[0], :part.shape[1]] = part
            planes = block.view(np.uint8).reshape(tile, tile, 4).transpose(0, 2, 1).reshape(tile, 4 * tile)
            diff = planes.astype(np.int16)
            diff[:, 1:] -= planes[:, :-1].astype(np.int16)
            tiles.append(zlib.compress((diff & 0xFF).astype(np.uint8).tobytes()))
    entries = []  # (tag, type, count, value bytes)

    def ent(tag, typ, values):
        fmt = {3: "H", 4: "I", 12: "d"}[typ]
        entries.append((tag, typ, len(values), struct.pack("<" + fmt * len(values), *values)))

    data_off = 8
    offsets, counts = [], []
    blob = b""
    for t in tiles:
        offsets.append(data_off + len(blob))
        counts.append(len(t))
        blob += t
    ent(256, 3, [w]); ent(257, 3, [h]); ent(258, 3, [32]); ent(259, 3, [8]); ent(277, 3, [1])
    ent(317, 3, [3]); ent(322, 3, [tile]); ent(323, 3, [tile]); ent(324, 4, offsets); ent(325, 4, counts)
    ent(339, 3, [3]); ent(33550, 12, [res, res, 0.0]); ent(33922, 12, [0, 0, 0, west, north, 0])
    ifd_off = data_off + len(blob)
    extra_off = ifd_off + 2 + 12 * len(entries) + 4
    ifd, extra = b"", b""
    for tag, typ, count, val in sorted(entries):
        if len(val) <= 4:
            ifd += struct.pack("<HHI", tag, typ, count) + val.ljust(4, b"\0")
        else:
            ifd += struct.pack("<HHII", tag, typ, count, extra_off + len(extra))
            extra += val
    out = b"II" + struct.pack("<HI", 42, ifd_off) + blob + struct.pack("<H", len(entries)) + ifd + b"\0\0\0\0" + extra
    path.write_bytes(out)


def make_grid(tmp: Path, west=35.0, north=31.9, size=720) -> T.Grid:
    """A 0.2°×0.2° grid with a hill, written through the GeoTIFF reader and the mosaic."""
    tif = tmp / "Copernicus_DSM_COG_10_N31_00_E035_00_DEM.tif"
    write_tiff(tif, hill(size, size), west, north, RES)  # tie point = centre of the corner pixel
    return T.build_mosaic([tif], tmp / "grid.i2", bbox=(west, north - size * RES, west + size * RES, north))


class CodecTest(unittest.TestCase):
    def test_terrarium_roundtrip(self):
        e = np.array([[-430.3, 0.0, 12.34], [815.9, 2814.0, -0.06]])
        dec = dem.terrarium_decode(dem.terrarium_encode(e))
        self.assertTrue(np.all(np.abs(dec - e) <= 0.0625 + 1e-6))
        self.assertEqual(dem.terrarium_encode(np.zeros((1, 1)))[0, 0].tolist(), [128, 0, 0])
        exact = dem.terrarium_decode(dem.terrarium_encode(e, step=1 / 256))
        self.assertTrue(np.all(np.abs(exact - e) <= 1 / 512 + 1e-6))

    def test_png_roundtrip(self):
        rng = np.random.default_rng(1)
        img = dem.terrarium_encode(hill(256, 256) + rng.normal(0, 3, (256, 256)))
        png = dem.png_encode(img)
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        self.assertTrue(np.array_equal(dem.png_decode(png), img))

    def test_png_decode_all_filters(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow not installed")
        import io

        img = dem.terrarium_encode(hill(40, 50))
        for opt in (False, True):  # Pillow picks adaptive filters, incl. Average and Paeth
            buf = io.BytesIO()
            Image.fromarray(img).save(buf, "PNG", optimize=opt)
            self.assertTrue(np.array_equal(dem.png_decode(buf.getvalue()), img))
        # and Pillow reads ours
        self.assertTrue(np.array_equal(np.asarray(Image.open(io.BytesIO(dem.png_encode(img)))), img))

    def test_tiles_for_bbox(self):
        tiles = dem.tiles_for_bbox((34.75, 32.05, 34.80, 32.10), 12)
        x, y = geo.lonlat_to_tile(34.7818, 32.0853, 12)
        self.assertIn((x, y), tiles)
        lon, lat = dem.tile_pixel_lonlat(12, x, y, 256)
        self.assertTrue(lon[0] < 34.7818 < lon[-1])
        self.assertGreater(lat[0], lat[-1])  # rows run north → south

    def test_varints_and_geometry_match_mvt(self):
        vals = np.array([0, 1, 127, 128, 300, 16383, 16384, 2 ** 21, 2 ** 28 + 5])
        self.assertEqual(T.varints(vals), b"".join(mvt._varint(int(v)) for v in vals))
        lines = [np.array([[10, 10], [100, 10], [100, 4000]]), np.array([[-20, 50], [4100, 60]])]
        a = T.FastLayer("contour")
        a.add_lines_np(lines, {"ele": 100, "idx": 1})
        b = mvt.Layer("contour")
        b.add_line([[tuple(p) for p in ln.tolist()] for ln in lines], {"ele": 100, "idx": 1})
        self.assertEqual(mvt.decode(mvt.encode_tile([a])), mvt.decode(mvt.encode_tile([b])))


class GeoTiffTest(unittest.TestCase):
    def test_read_predictor3_tiled(self):
        tmp = Path(tempfile.mkdtemp())
        arr = hill(100, 130).astype(np.float32)
        arr[3, 7] = -12.5
        write_tiff(tmp / "a.tif", arr, 35.0, 32.0, RES, tile=64)
        out, (west, north, xres, yres) = T.read_geotiff(tmp / "a.tif")
        self.assertEqual(out.shape, (100, 130))
        self.assertTrue(np.array_equal(out, arr))
        self.assertEqual((west, north), (35.0, 32.0))
        self.assertAlmostEqual(xres, RES)
        shutil.rmtree(tmp)


class ContourTest(unittest.TestCase):
    @unittest.skipUnless(HAVE_CONTOURPY, "contourpy not installed")
    def test_contours_of_a_hill(self):
        elev = hill(272, 272, peak=850, base=100)
        data = T.contour_tile(elev, buffer=8, size=256, interval=100, index=500)
        layer = mvt.decode(data)["contour"]
        eles = sorted(f["props"]["ele"] for f in layer)
        self.assertEqual(eles, [100 * k for k in range(2, 9)])  # 200 … 800 (100 is the flat base)
        self.assertEqual({f["props"]["ele"]: f["props"]["idx"] for f in layer}[500], 1)
        self.assertEqual({f["props"]["ele"]: f["props"]["idx"] for f in layer}[400], 0)
        for f in layer:
            self.assertEqual(f["type"], 2)
        # the 800 m ring is a small loop around the centre of the tile (2048, 2048)
        ring = next(f for f in layer if f["props"]["ele"] == 800)["geometry"]
        x, y = (ring[1] >> 1) ^ -(ring[1] & 1), (ring[2] >> 1) ^ -(ring[2] & 1)
        self.assertLess(np.hypot(x - 2048, y - 2048), 600)

    @unittest.skipUnless(HAVE_CONTOURPY, "contourpy not installed")
    def test_flat_and_sea_produce_nothing(self):
        self.assertEqual(T.contour_tile(np.zeros((40, 40)), 4, 32, 10, 100), b"")
        self.assertEqual(T.contour_tile(np.full((40, 40), 55.0), 4, 32, 10, 100), b"")

    def test_simplify(self):
        line = np.stack([np.linspace(0, 1000, 200), np.zeros(200)], axis=1)
        self.assertEqual(len(T.simplify(line, 1.0)), 2)
        corner = np.concatenate([line, np.stack([np.full(50, 1000.0), np.linspace(0, 500, 50)], axis=1)])
        out = T.simplify(corner, 1.0)
        self.assertLess(len(out), 10)
        self.assertTrue(any(np.allclose(p, [1000, 0], atol=25) for p in out))
        many = T.simplify_many([line, corner], 1.0)
        self.assertEqual(len(many), 2)
        self.assertEqual(len(many[0]), 2)

    def test_spot_heights(self):
        elev = hill(80, 80, peak=500, base=0)
        spots = T.spot_heights(elev, buffer=8, size=64, cell=32, min_prominence=50)
        self.assertEqual(len(spots), 1)
        self.assertGreater(spots[0][2], 450)


@unittest.skipUnless(HAVE_CONTOURPY, "contourpy not installed")
class BuildAndApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="hmaps-terrain-"))
        cls.grid = make_grid(cls.tmp)
        cls.data = cls.tmp / "data"
        (cls.data / "tiles").mkdir(parents=True)
        shutil.copy(sample_db(), cls.data / "maps.db")
        bbox = (35.02, 31.72, 35.18, 31.88)
        cls.cstats = T.build_contours(cls.grid, cls.data / "tiles" / "contours.mbtiles", bbox, {12: (50, 100), 13: (20, 100)})
        cls.tstats = T.build_terrain(cls.grid, cls.data / "tiles" / "terrain.mbtiles", bbox, range(9, 13))
        cls.api = ApiServer(cls.data)

    @classmethod
    def tearDownClass(cls):
        cls.api.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_grid_matches_source(self):
        lon, lat = 35.1, 31.8  # centre of the hill
        self.assertAlmostEqual(float(self.grid.sample([lon], [lat])[0, 0]), 800.0, delta=1.0)
        self.assertEqual(float(self.grid.sample([36.5], [31.8])[0, 0]), 0.0)  # outside → sea level
        coarse = self.grid.sample([lon], [lat], px_deg=RES * 8)
        self.assertGreater(float(coarse[0, 0]), 700)
        self.assertTrue((self.tmp / "grid.L3.i2").exists())

    def test_outputs(self):
        self.assertGreater(self.cstats[13]["tiles"], 0)
        self.assertGreater(self.tstats[12]["tiles"], 0)
        files = sorted(p.name for p in (self.data / "tiles").glob("*.mbtiles"))
        self.assertEqual(len(files), 2)
        self.assertTrue(files[0].startswith("contours-") and files[1].startswith("terrain-"))
        con = sqlite3.connect(T.published(self.data / "tiles" / "terrain.mbtiles"))
        md = dict(con.execute("SELECT name, value FROM metadata"))
        self.assertEqual((md["format"], md["encoding"], md["maxzoom"]), ("png", "terrarium", "12"))
        con.close()

    def test_meta_and_tiles(self):
        s, meta, _ = self.api.get("/api/meta")
        t = meta["terrain"]
        self.assertTrue(t["elevation"])
        self.assertIn("/tiles/contours/{z}/{x}/{y}.pbf", t["contours"]["tiles"])
        self.assertIn("/tiles/terrain/{z}/{x}/{y}.png", t["dem"]["tiles"])
        self.assertIn("Copernicus", meta["attribution"])
        x, y = geo.lonlat_to_tile(35.1, 31.8, 13)
        s, data, h = self.api.get(f"/tiles/contours/13/{x}/{y}.pbf")
        self.assertEqual(s, 200)
        self.assertEqual(h.get("content-type"), "application/x-protobuf")
        self.assertEqual(h.get("content-encoding"), "gzip")
        layers = mvt.decode(gzip.decompress(data))
        self.assertIn("contour", layers)
        self.assertTrue(all(f["props"]["ele"] % 20 == 0 for f in layers["contour"]))
        x, y = geo.lonlat_to_tile(35.1, 31.8, 12)
        s, png, h = self.api.get(f"/tiles/terrain/12/{x}/{y}.png")
        self.assertEqual((s, h.get("content-type")), (200, "image/png"))
        self.assertGreater(float(dem.terrarium_decode(dem.png_decode(png)).max()), 700)
        s, png, _ = self.api.get("/tiles/terrain/12/100/100.png")  # nowhere near: flat sea tile
        self.assertEqual(s, 200)
        self.assertEqual(float(np.abs(dem.terrarium_decode(dem.png_decode(png))).max()), 0.0)
        s, data, _ = self.api.get("/tiles/contours/13/1/1.pbf")
        self.assertEqual(s, 204)

    def test_elevation_points(self):
        s, body, _ = self.api.get("/api/elevation", points="31.8,35.1|31.75,35.03;31.0,34.0")
        self.assertEqual(s, 200)
        top, side, sea = body["elevations"]
        self.assertAlmostEqual(top, 800, delta=3)
        self.assertGreater(top, side)
        self.assertEqual(sea, 0.0)
        self.assertEqual(body["unit"], "m")
        self.assertIn("Copernicus", body["attribution"])
        s, body = self.api.post("/api/elevation", {"points": [[31.8, 35.1]]})
        self.assertAlmostEqual(body["elevations"][0], 800, delta=3)

    def test_elevation_profile(self):
        line = [(35.03, 31.8), (35.1, 31.8), (35.17, 31.8)]  # west → over the hill → east
        q = urllib.parse.urlencode({"path": geo.encode_polyline(line), "samples": 50})
        s, body, _ = self.api.get(f"/api/elevation?{q}")
        self.assertEqual(s, 200)
        self.assertEqual(len(body["profile"]), 50)
        self.assertAlmostEqual(body["distance"], geo.haversine(31.8, 35.03, 31.8, 35.17), delta=5)
        self.assertGreater(body["ascent"], 450)
        self.assertAlmostEqual(body["ascent"], body["descent"], delta=60)
        self.assertGreater(body["max"], 750)
        d = [p[0] for p in body["profile"]]
        self.assertEqual(d, sorted(d))
        s, body2 = self.api.post("/api/elevation", {"coordinates": [list(p) for p in line], "samples": 50})
        self.assertEqual(body2["ascent"], body["ascent"])

    def test_elevation_errors(self):
        self.assertEqual(self.api.get("/api/elevation")[0], 400)
        self.assertEqual(self.api.get("/api/elevation", points="91,35")[0], 400)
        self.assertEqual(self.api.get("/api/elevation", points="abc")[0], 400)
        self.assertEqual(self.api.get("/api/elevation", points="|".join(["31.8,35.1"] * 1001))[0], 400)
        self.assertEqual(self.api.post("/api/elevation", {"coordinates": "x"})[0], 400)


class NoTerrainTest(unittest.TestCase):
    def test_graceful_without_terrain(self):
        data = Path(tempfile.mkdtemp(prefix="hmaps-noterrain-"))
        shutil.copy(sample_db(), data / "maps.db")
        api = ApiServer(data)
        try:
            s, meta, _ = api.get("/api/meta")
            self.assertEqual(meta["terrain"], {"contours": None, "dem": None, "elevation": False})
            self.assertNotIn("Copernicus", meta["attribution"])
            s, body, _ = api.get("/api/elevation", points="31.8,35.1")
            self.assertEqual((s, body["error"]["code"]), (503, "terrain_not_ready"))
            self.assertEqual(api.get("/tiles/contours/12/2447/1660.pbf")[0], 204)
            self.assertEqual(api.get("/tiles/terrain/12/2447/1660.png")[0], 404)
        finally:
            api.stop()
            shutil.rmtree(data, ignore_errors=True)


class ProfileMathTest(unittest.TestCase):
    def test_climb_hysteresis(self):
        self.assertEqual(elev_mod.climb([0, 1, 0, 1, 0, 1]), (0.0, 0.0))  # noise
        self.assertEqual(elev_mod.climb([0, 5, 10, 4, 4]), (10.0, 6.0))

    def test_resample(self):
        pts = elev_mod.resample([(35.0, 31.0), (35.01, 31.0)], 11)
        self.assertEqual(len(pts), 11)
        self.assertAlmostEqual(pts[-1][0], geo.haversine(31.0, 35.0, 31.0, 35.01), delta=0.01)
        self.assertAlmostEqual(pts[5][1], 35.005)

    def test_smooth_removes_spike(self):
        out = elev_mod.smooth([10, 10, 10, 60, 10, 10, 10])
        self.assertLess(max(out), 15)


if __name__ == "__main__":
    unittest.main()
