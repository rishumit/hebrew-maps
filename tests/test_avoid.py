"""Avoid options of the built-in router (hmaps/route/avoid.py)."""

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from hmaps.api import routing as hebrew
from hmaps.route import avoid
from hmaps.route.engine import Router
from hmaps.route.graph import FLAG_TOLL, FLAG_UNPAVED, GraphBuilder, way_flags

D = 0.004  # ~400 m
LON0, LAT0 = 35.0, 31.9


def nid(i, j):
    return i * 1000 + j + 1


def pt(i, j):
    return (LAT0 + i * D, LON0 + j * D)


def ladder() -> GraphBuilder:
    """Two parallel east-west roads, 10 blocks long, joined at both ends and in the middle.

    Row 0 is a fast toll motorway, row 2 a slow residential road; a short unpaved
    shortcut (column 5, row 0→1→2) and a long track along row 1."""
    gb = GraphBuilder()
    n = 11
    gb.add_way({"highway": "motorway", "ref": "6", "toll": "yes", "oneway": "no", "name": "כביש 6"},
               [(nid(0, j), LON0 + j * D, LAT0) for j in range(n)])
    gb.add_way({"highway": "residential", "name": "רחוב הכפר"}, [(nid(2, j), LON0 + j * D, LAT0 + 2 * D) for j in range(n)])
    gb.add_way({"highway": "track", "name": "דרך עפר"}, [(nid(1, j), LON0 + j * D, LAT0 + D) for j in range(n)])
    for j in (0, 10):
        gb.add_way({"highway": "tertiary", "name": f"מחבר {j}"},
                   [(nid(0, j), LON0 + j * D, LAT0), (nid(1, j), LON0 + j * D, LAT0 + D), (nid(2, j), LON0 + j * D, LAT0 + 2 * D)])
    return gb


def save(gb: GraphBuilder, areas: dict | None = None) -> Path:
    p = Path(tempfile.mkdtemp()) / "graph.npz"
    gb.save(p)
    if areas is not None:
        avoid.areas_path(p).write_text(json.dumps(areas), encoding="utf-8")
    return p


def uses(res, name):
    return any(s.get("name") == name for leg in res["routes"][0]["legs"] for s in leg["steps"])


class FlagsTest(unittest.TestCase):
    def test_way_flags(self):
        self.assertEqual(way_flags({"highway": "motorway", "toll": "yes"}), FLAG_TOLL)
        self.assertEqual(way_flags({"highway": "track"}), FLAG_UNPAVED)
        self.assertEqual(way_flags({"highway": "track", "surface": "asphalt"}), 0)
        self.assertEqual(way_flags({"highway": "residential", "surface": "gravel"}), FLAG_UNPAVED)
        self.assertEqual(way_flags({"highway": "residential"}), 0)

    def test_parse(self):
        self.assertEqual(avoid.parse("toll,motorway,bogus"), {"toll", "motorway"})
        self.assertEqual(avoid.parse("toll,unpaved", "foot"), {"unpaved"})
        self.assertEqual(avoid.parse(None), frozenset())


class AvoidRouterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        areas = {"pa": [[LON0 + 5 * D, LAT0 + 2 * D, 300]]}  # one "locality" on the village road
        cls.path = save(ladder(), areas)
        cls.router = Router(cls.path)

    def route(self, avoid_set=frozenset(), mode="car"):
        return self.router.route(mode, [pt(0, 0), pt(0, 10)], frozenset(avoid_set))

    def test_default_uses_motorway(self):
        res = self.route()
        self.assertTrue(uses(res, "כביש 6"))
        self.assertNotIn("avoid_unmet", res["routes"][0])

    def test_avoid_toll_and_motorway(self):
        for a in ({"toll"}, {"motorway"}):
            res = self.route(a)
            r = res["routes"][0]
            self.assertFalse(uses(res, "כביש 6"), a)
            self.assertEqual(r["avoid_unmet"], [])
            # the reported time is the real travel time, not the penalised search cost
            base = self.route()["routes"][0]
            self.assertLess(r["duration"], base["duration"] * 6)

    def test_avoid_toll_and_unpaved_goes_through_village(self):
        res = self.route({"toll", "unpaved"})
        self.assertTrue(uses(res, "רחוב הכפר"))
        self.assertFalse(uses(res, "דרך עפר"))

    def test_avoid_pa_area(self):
        res = self.route({"toll", "unpaved", "pa"})
        r = res["routes"][0]
        # every way round touches something avoided: the route still exists and says what it could not keep
        self.assertTrue(r["avoid_unmet"])
        self.assertTrue(set(r["avoid_unmet"]) <= {"toll", "unpaved", "pa"})

    def test_unmet_reported_when_no_alternative(self):
        res = self.router.route("car", [pt(0, 2), pt(0, 3)], frozenset({"toll"}))
        self.assertEqual(res["routes"][0]["avoid_unmet"], ["toll"])
        shaped = hebrew.shape(res, "car", frozenset({"toll"}))
        self.assertEqual(shaped["routes"][0]["avoid_unmet"][0]["name_he"], "כבישי אגרה")

    def test_options_irrelevant_for_profile_are_ignored(self):
        res = self.router.route("foot", [pt(2, 0), pt(2, 10)], frozenset({"toll", "motorway"}))
        self.assertNotIn("avoid_unmet", res["routes"][0])

    def test_junctions(self):
        jv = avoid.junction_vertices(self.router.g)
        self.assertEqual(jv.dtype, bool)
        res = self.route({"junctions"})
        self.assertTrue(res["routes"][0]["legs"])

    def test_old_graph_without_flags_falls_back_to_ref(self):
        g = dict(self.router.g)
        g.pop("flags")
        m = avoid.toll_mask(g, self.router.names)
        self.assertTrue(m.any())

    def test_available(self):
        self.assertTrue(self.router.avoid_available()["pa"])
        r2 = Router(save(ladder()))
        self.assertFalse(r2.avoid_available()["pa"])


class AreasTest(unittest.TestCase):
    def test_contains(self):
        a = avoid.Areas([(35.25, 32.22, 1000), (35.2, 31.9, 500)])
        lon = np.array([35.25, 35.26, 35.30, 35.2, 34.8])
        lat = np.array([32.22, 32.22, 32.22, 31.9035, 32.0])
        self.assertEqual(a.contains(lon, lat).tolist(), [True, True, False, True, False])

    def test_build_pa_areas(self):
        places = [(35.26, 32.22, "city", "نابلس"), (35.17, 32.10, "town", "אריאל"),
                  (35.15, 32.52, "city", "أم الفحم"), (34.45, 31.5, "city", "غزة"), (35.3, 32.0, "village", "قرية")]
        inside = lambda lon, lat: lat < 32.4  # noqa: E731 - test stand-in for the boundary polygon
        out = avoid.build_pa_areas(places, inside)
        self.assertEqual([c[:2] for c in out["pa"]], [[35.26, 32.22], [35.3, 32.0]])
        self.assertEqual(out["pa"][0][2], avoid.PA_RADIUS["city"])


if __name__ == "__main__":
    unittest.main()
