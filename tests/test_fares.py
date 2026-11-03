"""Estimated public-transport fares (hmaps/transit/fares.py) and walking legs along streets (issue #9)."""

import unittest
from unittest import mock

from hmaps.api import transit as transit_api
from hmaps.transit import fares


def ride(kind, a, b, dep, line="קו 1"):
    return {"type": kind, "line": line, "from": {"lat": a[0], "lon": a[1]}, "to": {"lat": b[0], "lon": b[1]},
            "departure": f"2026-11-03T{dep}:00+02:00"}


TLV, RG = (32.08, 34.78), (32.07, 34.82)       # ~4 km
JLM = (31.78, 35.21)                           # ~55 km from Tel Aviv


class FaresTest(unittest.TestCase):
    def test_bands(self):
        self.assertEqual(fares.band(3), 8.0)
        self.assertEqual(fares.band(30), 14.5)
        self.assertEqual(fares.band(30, train=True), 21.0)
        self.assertEqual(fares.band(500, train=True), None)

    def test_urban_transfer_is_free(self):
        it = {"legs": [ride("bus", TLV, RG, "08:00"), {"type": "walk"}, ride("light_rail", RG, TLV, "08:40", "רכבת קלה")]}
        f = fares.estimate(it)
        self.assertEqual(f["total"], 8.0)
        self.assertEqual(len(f["parts"]), 1)
        self.assertEqual(f["parts"][0]["note"], "כולל מעבר חינם")

    def test_after_90_minutes_a_new_ticket(self):
        it = {"legs": [ride("bus", TLV, RG, "08:00"), ride("bus", RG, TLV, "09:45", "קו 2")]}
        self.assertEqual(fares.estimate(it)["total"], 16.0)

    def test_train_plus_bus(self):
        it = {"legs": [ride("rail", TLV, JLM, "08:00", "רכבת"), ride("bus", JLM, (31.77, 35.22), "08:50")]}
        f = fares.estimate(it)
        self.assertEqual(f["total"], 27.0 + 8.0)
        self.assertEqual(f["text"], "₪35")

    def test_walk_only(self):
        self.assertIsNone(fares.estimate({"legs": [{"type": "walk"}]}))


class EnrichTest(unittest.TestCase):
    def test_walk_legs_follow_streets(self):
        res = {"itineraries": [{"legs": [
            {"type": "walk", "distance": 300, "from": {"lat": 32.08, "lon": 34.78}, "to": {"lat": 32.082, "lon": 34.782},
             "geometry": {"type": "LineString", "coordinates": [[34.78, 32.08], [34.782, 32.082]]}},
            ride("bus", TLV, RG, "08:00")]}]}
        street = {"routes": [{"geometry": {"type": "LineString", "coordinates": [[34.7801, 32.0801], [34.7801, 32.0819], [34.7819, 32.0819]]}}]}
        with mock.patch("hmaps.api.routing.route_any", return_value=street):
            transit_api.enrich(res)
        it = res["itineraries"][0]
        coords = it["legs"][0]["geometry"]["coordinates"]
        self.assertEqual(len(coords), 5)  # stop + 3 street points + stop
        self.assertTrue(it["legs"][0]["routed"])
        self.assertEqual(it["fare"]["total"], 8.0)

    def test_routing_failure_keeps_straight_line(self):
        leg = {"type": "walk", "distance": 300, "from": {"lat": 32.08, "lon": 34.78}, "to": {"lat": 32.082, "lon": 34.782},
               "geometry": {"type": "LineString", "coordinates": [[34.78, 32.08], [34.782, 32.082]]}}
        with mock.patch("hmaps.api.routing.route_any", side_effect=RuntimeError("no graph")):
            transit_api.enrich({"itineraries": [{"legs": [leg]}]})
        self.assertEqual(len(leg["geometry"]["coordinates"]), 2)
        self.assertNotIn("routed", leg)


if __name__ == "__main__":
    unittest.main()
