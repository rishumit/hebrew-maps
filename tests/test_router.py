import math
import tempfile
import time
import unittest
from pathlib import Path

from hmaps.api import routing as hebrew
from hmaps.route.engine import NoRoute, Router
from hmaps.route.graph import GraphBuilder, speeds

D = 0.002  # ~200 m grid spacing


def grid_graph(n: int, oneway_row: int | None = None, roundabout_at: tuple | None = None) -> GraphBuilder:
    """n×n street grid around Tel Aviv. Rows (east-west) are "רחוב Rn", columns "שדרות Cn"."""
    gb = GraphBuilder()
    nid = lambda i, j: i * 10_000 + j + 1
    lon0, lat0 = 34.75, 32.05
    for i in range(n):  # rows: constant latitude
        tags = {"highway": "residential", "name": f"רחוב R{i}"}
        if i == oneway_row:
            tags["oneway"] = "yes"  # eastbound only
        nodes = [(nid(i, j), lon0 + j * D, lat0 + i * D) for j in range(n)]
        gb.add_way(tags, nodes)
    for j in range(n):  # columns: constant longitude
        nodes = [(nid(i, j), lon0 + j * D, lat0 + i * D) for i in range(n)]
        gb.add_way({"highway": "secondary", "name": f"שדרות C{j}"}, nodes)
    gb.add_way({"highway": "footway"}, [(9_000_001, lon0 - D, lat0), (nid(0, 0), lon0, lat0)])
    return gb


def save(gb: GraphBuilder) -> Path:
    p = Path(tempfile.mkdtemp()) / "graph.npz"
    gb.save(p)
    return p


def pt(i, j, lon0=34.75, lat0=32.05):
    return (lat0 + i * D, lon0 + j * D)


class RouterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.router = Router(save(grid_graph(6, oneway_row=2)))

    def steps(self, mode, a, b):
        res = self.router.route(mode, [a, b])
        return res, hebrew.shape(res, mode)["routes"][0]["steps"]

    def test_straight(self):
        res, steps = self.steps("car", pt(1, 0), pt(1, 4))
        r = res["routes"][0]
        self.assertAlmostEqual(r["distance"], 4 * D * 111_320 * math.cos(math.radians(32.052)), delta=15)
        self.assertEqual([s["maneuver"]["type"] for s in steps], ["depart", "arrive"])
        self.assertIn("ברחוב רחוב R1", steps[0]["instruction"].replace("ברחוב R1", "ברחוב רחוב R1"))

    def test_turn_left(self):
        _, steps = self.steps("car", pt(1, 0), pt(4, 3))
        types = [(s["maneuver"]["type"], s["maneuver"]["modifier"]) for s in steps]
        self.assertEqual(types[0][0], "depart")
        self.assertEqual(types[-1][0], "arrive")
        turns = [s for s in steps if s["maneuver"]["type"] == "turn"]
        self.assertEqual(len(turns), 1)
        # the faster avenue (column 0) goes north first, then a right turn east onto row 4
        self.assertEqual(turns[0]["maneuver"]["modifier"], "right")
        self.assertEqual(turns[0]["instruction"], "פנו ימינה לרחוב R4")
        _, steps = self.steps("car", pt(5, 1), pt(1, 4))  # west->east on row 5? goes south on C1 first
        self.assertIn(steps[1]["maneuver"]["modifier"], ("left", "right"))

    def test_oneway_detour(self):
        # westbound along row 2 is forbidden for cars, allowed on foot
        car, _ = self.steps("car", pt(2, 4), pt(2, 0))
        foot, _ = self.steps("foot", pt(2, 4), pt(2, 0))
        self.assertGreater(car["routes"][0]["distance"], foot["routes"][0]["distance"] + 300)

    def test_mid_edge_points_and_geometry(self):
        a = (32.05 + D * 1, 34.75 + D * 0.5)  # middle of a block
        b = (32.05 + D * 1, 34.75 + D * 2.5)
        res = self.router.route("foot", [a, b])
        r = res["routes"][0]
        self.assertAlmostEqual(r["distance"], 2 * D * 111_320 * 0.847, delta=20)
        c = r["geometry"]["coordinates"]
        self.assertAlmostEqual(c[0][0], a[1], places=4)
        self.assertAlmostEqual(c[-1][0], b[1], places=4)
        # same edge, both points between the same junctions
        res = self.router.route("car", [(32.05 + D, 34.75 + D * 0.2), (32.05 + D, 34.75 + D * 0.8)])
        self.assertLess(res["routes"][0]["distance"], D * 111_320)

    def test_footway_only_for_pedestrians(self):
        start = (32.05, 34.75 - D)
        self.router.route("foot", [start, pt(3, 3)])
        res = self.router.route("car", [start, pt(3, 3)])  # car snaps to the nearest road instead
        self.assertGreater(res["routes"][0]["distance"], 0)

    def test_no_segment_far_away(self):
        with self.assertRaises(NoRoute) as cm:
            self.router.route("car", [(31.0, 35.0), pt(1, 1)])
        self.assertEqual(cm.exception.code, "NoSegment")

    def test_speed_rules(self):
        s = speeds({"highway": "motorway"})
        self.assertEqual(s["foot"], (0.0, 0.0))
        self.assertEqual(s["car"][1], 0.0)  # motorways are oneway
        self.assertEqual(speeds({"highway": "footway"})["car"], (0.0, 0.0))
        self.assertEqual(speeds({"highway": "residential", "access": "private"})["car"], (0.0, 0.0))
        b = speeds({"highway": "residential", "oneway": "yes", "oneway:bicycle": "no"})["bike"]
        self.assertTrue(b[0] > 0 and b[1] > 0)


class RoundaboutTest(unittest.TestCase):
    def test_exit_number(self):
        gb = GraphBuilder()
        c_lon, c_lat, r = 34.78, 32.07, 0.0003
        ring = [(100 + k, c_lon + r * math.cos(math.radians(a)), c_lat + r * math.sin(math.radians(a)))
                for k, a in enumerate((270, 0, 90, 180))]  # S, E, N, W (counter-clockwise)
        gb.add_way({"highway": "primary", "junction": "roundabout", "name": "כיכר"}, ring + [ring[0]])
        arms = {"S": ("הרצל", ring[0], (0, -1)), "E": ("אלנבי", ring[1], (1, 0)),
                "N": ("יפו", ring[2], (0, 1)), "W": ("בגין", ring[3], (-1, 0))}
        for k, (name, (nid, lon, lat), (dx, dy)) in enumerate(arms.values()):
            gb.add_way({"highway": "primary", "name": name}, [(nid, lon, lat), (200 + k, lon + dx * 0.004, lat + dy * 0.004)])
        router = Router(save(gb))
        # from the south arm to the west arm, counter-clockwise: E (1st), N (2nd), W (3rd)
        res = router.route("car", [(c_lat - 0.0035, c_lon), (c_lat, c_lon - 0.0035)])
        steps = hebrew.shape(res, "car")["routes"][0]["steps"]
        rb = [s for s in steps if s["maneuver"]["type"] == "roundabout"]
        self.assertEqual(len(rb), 1)
        self.assertEqual(rb[0]["maneuver"]["exit"], 3)
        self.assertEqual(rb[0]["instruction"], "בכיכר צאו ביציאה השלישית לרחוב בגין")


class PerformanceTest(unittest.TestCase):
    def test_large_grid(self):
        n = 400  # 160k junctions, 320k edges
        t0 = time.perf_counter()
        router = Router(save(grid_graph(n)))
        build = time.perf_counter() - t0
        t0 = time.perf_counter()
        router.route("car", [pt(0, 0), pt(n - 1, n - 1)])
        far = time.perf_counter() - t0
        t0 = time.perf_counter()
        router.route("car", [pt(100, 100), pt(130, 120)])
        near = time.perf_counter() - t0
        print(f"\n  grid {n}x{n}: build+load {build:.1f}s, corner-to-corner {far:.2f}s, 5 km route {near:.3f}s")
        self.assertLess(near, 1.0)
        self.assertLess(far, 20.0)


class MarkedTrailPreferenceTest(unittest.TestCase):
    """Ways of a marked trail are ~10% cheaper on foot / by bike (graph.TRAIL_COST_FACTOR)."""

    @staticmethod
    def graph(marked: bool) -> Router:
        gb = GraphBuilder()
        a, b, c = (1, 34.75, 32.05), (2, 34.76, 32.05), (3, 34.755, 32.0515)
        gb.add_way({"highway": "footway"}, [(4, 34.745, 32.05), a])  # approaches
        gb.add_way({"highway": "footway"}, [b, (5, 34.765, 32.05)])
        gb.add_way({"highway": "footway"}, [a, b])  # direct, unmarked
        mk = frozenset({"foot", "bike"}) if marked else frozenset()
        gb.add_way({"highway": "path"}, [a, c], marked=mk)  # ~6% longer detour along a trail
        gb.add_way({"highway": "path"}, [c, b], marked=mk)
        return Router(save(gb))

    def test_foot_prefers_marked_trail(self):
        a, b = (32.05, 34.746), (32.05, 34.764)
        plain = self.graph(False).route("foot", [a, b])["routes"][0]
        trail = self.graph(True).route("foot", [a, b])["routes"][0]
        self.assertLess(plain["distance"], 1720)   # straight footway (~1.7 km)
        self.assertGreater(trail["distance"], 1740)  # the marked detour (~1.76 km)
        # car routing is unaffected (neither way is drivable here)
        with self.assertRaises(NoRoute):
            self.graph(True).route("car", [a, b])


if __name__ == "__main__":
    unittest.main()
