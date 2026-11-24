import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from hmaps.route.kohen import KohenAreas

SQUARE = [[35.0, 32.0], [35.1, 32.0], [35.1, 32.1], [35.0, 32.1], [35.0, 32.0]]
HOLE = [[35.04, 32.04], [35.06, 32.04], [35.06, 32.06], [35.04, 32.06], [35.04, 32.04]]
# פוליגון "L" שאינו קמור
L_SHAPE = [[0, 0], [2, 0], [2, 1], [1, 1], [1, 2], [0, 2], [0, 0]]


def fc(*feats):
    return {"type": "FeatureCollection", "features": list(feats)}


def feat(ring, level="א", holes=()):
    return {"type": "Feature", "properties": {"level": level},
            "geometry": {"type": "Polygon", "coordinates": [ring, *holes]}}


class KohenAreasTest(unittest.TestCase):
    def test_contains_basic(self):
        a = KohenAreas.from_geojson(fc(feat(SQUARE)))
        lon = np.array([35.05, 35.2, 34.9])
        lat = np.array([32.05, 32.05, 32.05])
        self.assertEqual(a.contains(lon, lat).tolist(), [True, False, False])

    def test_hole(self):
        a = KohenAreas.from_geojson(fc(feat(SQUARE, holes=[HOLE])))
        self.assertEqual(a.contains(np.array([35.05, 35.01]), np.array([32.05, 32.01])).tolist(), [False, True])

    def test_concave(self):
        a = KohenAreas([[L_SHAPE]])
        self.assertEqual(a.contains(np.array([0.5, 1.5, 1.5]), np.array([1.5, 0.5, 1.5])).tolist(),
                         [True, True, False])

    def test_segment_crossing_without_endpoints_inside(self):
        a = KohenAreas.from_geojson(fc(feat(SQUARE)))
        # קטע ארוך שחוצה את הריבוע; האמצע בתוכו
        self.assertTrue(a.hits_segments([34.9], [32.05], [35.2], [32.05])[0])
        # קטע שחוצה פינה בלבד: קצוות ואמצע מחוץ לפוליגון
        # קטע רחוק
        self.assertFalse(a.hits_segments([36.0], [33.0], [36.1], [33.1])[0])

    def test_segment_corner(self):
        a = KohenAreas.from_geojson(fc(feat(SQUARE)))
        # עובר ממש ליד הפינה הדרום-מערבית ופוספס אותה: x+y=66.99
        self.assertFalse(a.hits_segments([34.98], [32.01], [35.01], [31.98])[0])
        # חותך את הפינה
        self.assertTrue(a.hits_segments([34.99], [32.02], [35.02], [31.99])[0])

    def test_level_filter(self):
        f = fc(feat(SQUARE, "א"), feat(L_SHAPE, "ג"))
        self.assertEqual(len(KohenAreas.from_geojson(f)), 2)
        self.assertEqual(len(KohenAreas.from_geojson(f, frozenset({"א"}))), 1)
        self.assertEqual(len(KohenAreas.from_geojson(fc(feat(SQUARE, "A")), frozenset({"א"}))), 1)

    def test_load(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "avoid_areas.json"
            self.assertIsNone(KohenAreas.load(p))
            p.write_text(json.dumps({"version": 1, "pa": []}), encoding="utf-8")
            self.assertIsNone(KohenAreas.load(p))
            p.write_text(json.dumps({"version": 2, "kohen": fc(feat(SQUARE))}), encoding="utf-8")
            self.assertEqual(len(KohenAreas.load(p)), 1)

    def test_scale(self):
        rng = np.random.default_rng(1)
        polys = []
        for _ in range(511):
            cx, cy = rng.uniform(34.3, 35.6), rng.uniform(29.5, 33.2)
            polys.append([[[cx, cy], [cx + .002, cy], [cx + .002, cy + .0005], [cx, cy + .0005], [cx, cy]]])
        a = KohenAreas(polys)
        n = 1_000_000
        lon = rng.uniform(34.3, 35.6, n)
        lat = rng.uniform(29.5, 33.2, n)
        import time
        t = time.time()
        a.contains(lon, lat)
        self.assertLess(time.time() - t, 10)


if __name__ == "__main__":
    unittest.main()


class KohenEtlParseTest(unittest.TestCase):
    def test_parse(self):
        from hmaps.etl import kohen_areas as ka
        feats = [feat(SQUARE) for _ in range(ka.MIN_FEATURES)]
        feats[0]["properties"].update(name="x", city="y")
        out = ka.parse({"success": True, "data": fc(*feats)})
        self.assertEqual(len(out["features"]), ka.MIN_FEATURES)
        self.assertEqual(out["features"][0]["properties"], {"level": "א"})
        self.assertIsNone(ka.parse({"success": True, "data": fc(feat(SQUARE))}))
        self.assertIsNone(ka.parse({}))


class KohenRoutingTest(unittest.TestCase):
    """End-to-end: a tiny graph with a short road over a grave area and a longer detour."""

    @classmethod
    def setUpClass(cls):
        from hmaps.route.graph import GraphBuilder

        gb = GraphBuilder()
        tags = {"highway": "residential"}
        # A(35.000) ── M(35.010) ── B(35.020) on lat 32.000 (direct) and a detour via lat 32.010
        gb.add_way(tags, [(1, 35.000, 32.000), (2, 35.010, 32.000)])
        gb.add_way(tags, [(2, 35.010, 32.000), (3, 35.020, 32.000)])
        gb.add_way(tags, [(1, 35.000, 32.000), (4, 35.000, 32.010), (5, 35.020, 32.010), (3, 35.020, 32.000)])
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = Path(cls.tmp.name) / "graph.npz"
        gb.save(cls.path)
        # a grave area on the direct road around M
        poly = [[35.008, 31.999], [35.012, 31.999], [35.012, 32.001], [35.008, 32.001], [35.008, 31.999]]
        (Path(cls.tmp.name) / "avoid_areas.json").write_text(
            json.dumps({"version": 2, "pa": [], "kohen": fc(feat(poly))}), encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _router(self):
        from hmaps.route.engine import Router
        return Router(self.path)

    def test_parse_and_profiles(self):
        from hmaps.route import avoid
        self.assertEqual(avoid.parse("kohen,pa", "foot"), frozenset({"kohen", "pa"}))
        self.assertEqual(avoid.parse("kohen", "bike"), frozenset({"kohen"}))
        self.assertIn("kohen", avoid.LABELS_HE)

    def test_available(self):
        r = self._router()
        self.assertTrue(r.avoid_available()["kohen"])
        self.assertIsNotNone(r.kohen_areas())

    def test_route_avoids_area(self):
        r = self._router()
        pts = [(32.000, 35.000), (32.000, 35.020)]  # (lat, lon)
        plain = r.route("car", pts)["routes"][0]
        self.assertLess(plain["distance"], 2500)  # direct road ~1.9 km
        kohen = r.route("car", pts, frozenset({"kohen"}))["routes"][0]
        self.assertGreater(kohen["distance"], plain["distance"])  # takes the detour
        self.assertEqual(kohen["avoid_unmet"], [])

    def test_unavoidable_is_reported(self):
        r = self._router()
        # both ends inside the area: there is no way around it
        res = r.route("foot", [(32.000, 35.009), (32.000, 35.0115)], frozenset({"kohen"}))["routes"][0]
        self.assertEqual(res["avoid_unmet"], ["kohen"])
        # a route far from the area keeps the option
        res = r.route("foot", [(32.000, 35.000), (32.010, 35.000)], frozenset({"kohen"}))["routes"][0]
        self.assertEqual(res["avoid_unmet"], [])

    def test_parse_levels(self):
        from hmaps.route.kohen import parse_levels
        self.assertEqual(parse_levels("א"), frozenset({"א"}))
        self.assertEqual(parse_levels("א,ב"), frozenset({"א", "ב"}))
        self.assertEqual(parse_levels("A"), frozenset({"א"}))
        for everything in ("", None, "אבג", "xyz"):  # all levels (the default)
            self.assertIsNone(parse_levels(everything), everything)

    def test_levels_choose_the_areas(self):
        r = self._router()
        pts = [(32.000, 35.000), (32.000, 35.020)]
        plain = r.route("car", pts)["routes"][0]["distance"]
        # the area is level א: avoiding only "ספק" (ב) keeps the direct road
        self.assertIsNone(r.kohen_areas(frozenset({"ב"})))
        only_b = r.route("car", pts, frozenset({"kohen"}), frozenset({"ב"}))["routes"][0]
        self.assertAlmostEqual(only_b["distance"], plain, delta=1)
        # א + ב: the detour, and the all-levels profile is still cached separately
        ab = r.route("car", pts, frozenset({"kohen"}), frozenset({"א", "ב"}))["routes"][0]
        self.assertGreater(ab["distance"], plain)
        self.assertGreater(r.route("car", pts, frozenset({"kohen"}))["routes"][0]["distance"], plain)

    def test_api_reports_levels_without_areas(self):
        from hmaps.api import routing
        pts = [(32.000, 35.000), (32.000, 35.020)]
        res = routing._local(self.path, "car", pts, frozenset({"kohen"}), frozenset({"ג"}))
        self.assertEqual([a["id"] for a in res.get("avoid_ignored", [])], ["kohen"])
        res = routing._local(self.path, "car", pts, frozenset({"kohen"}), frozenset({"א"}))
        self.assertEqual(res.get("avoid_ignored", []), [])

    def test_no_file_means_unavailable(self):
        with tempfile.TemporaryDirectory() as d:
            from hmaps.route.graph import GraphBuilder
            from hmaps.route.engine import Router
            gb = GraphBuilder()
            gb.add_way({"highway": "residential"}, [(1, 35.0, 32.0), (2, 35.01, 32.0)])
            p = Path(d) / "graph.npz"
            gb.save(p)
            r = Router(p)
            self.assertFalse(r.avoid_available()["kohen"])
            self.assertTrue(r.avoid_available()["toll"])


class KohenSegmentsPropertyTest(unittest.TestCase):
    def test_matches_brute_force(self):
        from hmaps.route.kohen import _points_in_ring, _segments_cross_ring
        rng = np.random.default_rng(3)
        polys = []
        for _ in range(40):
            cx, cy = rng.uniform(34.9, 35.1), rng.uniform(31.9, 32.1)
            k = int(rng.integers(4, 12))
            ang = np.sort(rng.uniform(0, 2 * np.pi, k))
            r = rng.uniform(.0005, .004, k)
            ring = [[cx + ri * np.cos(a), cy + ri * np.sin(a)] for a, ri in zip(ang, r)]
            polys.append([ring + [ring[0]]])
        areas = KohenAreas(polys)
        n = 5000
        x1, y1 = rng.uniform(34.88, 35.12, n), rng.uniform(31.88, 32.12, n)
        length, th = rng.choice([.0005, .003, .02, .1], n), rng.uniform(0, 2 * np.pi, n)
        x2, y2 = x1 + length * np.cos(th), y1 + length * np.sin(th)
        brute = np.zeros(n, dtype=bool)
        for rings in areas._rings:
            rx, ry = rings[0]
            brute |= _points_in_ring(x1, y1, rx, ry) | _points_in_ring(x2, y2, rx, ry) \
                | _segments_cross_ring(x1, y1, x2, y2, rx, ry)
        self.assertEqual(int((areas.hits_segments(x1, y1, x2, y2) != brute).sum()), 0)
        self.assertGreater(int(brute.sum()), 0)
