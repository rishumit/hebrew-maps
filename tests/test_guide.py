"""Hiking-route guide: time formula, stage splitting, DEM / hikes-table fallbacks, endpoints."""

import datetime as dt
import math
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from hmaps.api import guide
from hmaps.core import db, geo
from tests.helpers import ApiServer, sample_db


class Ramp:
    """Fake DEM: 100 m at the start latitude, climbing northwards (~11 m per 100 m)."""

    def __init__(self, lat0=31.40, rate=11_000.0):
        self.lat0, self.rate = lat0, rate

    def at(self, lat, lon):
        return 100.0 + (lat - self.lat0) * self.rate


class Broken:
    def at(self, lat, lon):
        raise OSError("tile missing")


def _con():
    return db.open_readonly(sample_db())


def _empty_con():
    """A database without any table: an old / partial dataset."""
    return sqlite3.connect(":memory:")


class FormulaTest(unittest.TestCase):
    def test_tobler_scaled_to_4kmh(self):
        self.assertAlmostEqual(guide.tobler_kmh(0.0), 4.0, places=2)
        self.assertGreater(guide.tobler_kmh(-0.05), guide.tobler_kmh(0.0))  # gentle descent is fastest
        self.assertLess(guide.tobler_kmh(0.15), 3.0)
        self.assertLess(guide.tobler_kmh(0.30), guide.tobler_kmh(0.15))
        self.assertLess(guide.tobler_kmh(-0.30), guide.tobler_kmh(-0.05))  # steep descents are slow too
        self.assertEqual(guide.tobler_kmh(5.0), guide.tobler_kmh(0.6))  # DEM spikes are clamped

    def test_naismith_without_dem(self):
        self.assertAlmostEqual(guide.walking_minutes(4000), 60.0)
        self.assertAlmostEqual(guide.walking_minutes(4000, 600), 120.0)

    def test_difficulty_words(self):
        self.assertEqual(guide.difficulty_level("קל"), "easy")
        self.assertEqual(guide.difficulty_level("קשה מאוד"), "very_hard")
        self.assertEqual(guide.difficulty_level("mountain_hiking"), "moderate")
        self.assertEqual(guide.difficulty_level("Moderate"), "moderate")
        self.assertIsNone(guide.difficulty_level(None))
        self.assertIsNone(guide.difficulty_level("???"))

    def test_sun_times_jerusalem(self):
        rise, sset = guide.sun_times(31.778, 35.235, dt.date(2026, 6, 21))
        tz = guide.TZ
        self.assertEqual(rise.astimezone(tz).strftime("%H:%M")[:4], "05:3")  # ≈ 05:33 IDT
        self.assertEqual(sset.astimezone(tz).strftime("%H:%M")[:4], "19:4")  # ≈ 19:47 IDT
        _, winter = guide.sun_times(31.778, 35.235, dt.date(2026, 12, 21))
        self.assertEqual(winter.astimezone(tz).strftime("%H"), "16")
        d = guide.daylight(31.778, 35.235, dt.date(2026, 12, 21), 120)
        self.assertIn("latest_start", d)
        self.assertLess(d["latest_start"], d["sunset"])
        self.assertTrue(guide.daylight(31.778, 35.235, dt.date(2026, 12, 21), 15 * 60).get("too_long_for_daylight"))

    def test_chain_lines(self):
        a = [(35.0, 31.0), (35.01, 31.0)]
        b = [(35.02, 31.0), (35.01, 31.0)]  # digitised backwards
        c = [(34.99, 31.0), (35.0, 31.0)]
        line = guide.chain_lines([a, b, c])
        self.assertEqual({line[0], line[-1]}, {(34.99, 31.0), (35.02, 31.0)})
        xs = [p[0] for p in line]
        self.assertIn(xs, (sorted(xs), sorted(xs, reverse=True)))
        self.assertEqual(len(line), 4)


class StageTest(unittest.TestCase):
    def test_sample_hike_stages_without_dem(self):
        g = guide.hike_guide(_con(), 1)
        s = g["summary"]
        self.assertEqual(s["time_formula"], "flat")
        self.assertIsNone(s["ascent"])
        self.assertIsNone(g["elevation"])
        self.assertEqual(s["duration_min"], guide.round5(s["length_m"] / 1000 / 4 * 60))
        self.assertEqual(s["duration_source_min"], 180)
        self.assertEqual(s["difficulty"], "moderate")
        self.assertFalse(s["loop"])
        self.assertEqual(g["start"]["name"], "חניון הדרום (לדוגמה)")
        self.assertEqual(g["end"]["name"], "עין דוד")
        stages = g["stages"]
        self.assertGreaterEqual(len(stages), 3)
        self.assertEqual(stages[0]["to"]["name"], "מצפה הדקלים (לדוגמה)")  # source waypoint
        self.assertTrue(any(st["to"].get("kind") == "junction" for st in stages))  # leaves the Israel Trail
        self.assertEqual(stages[0]["marks"][0]["name"], "שביל ישראל")
        self.assertTrue(stages[0]["instruction"].startswith("יוצאים מחניון הדרום"))
        self.assertIn("בשביל ישראל", stages[0]["instruction"])
        self.assertIn("סוף המסלול", stages[-1]["instruction"])
        # legs are contiguous and add up
        for a, b in zip(stages, stages[1:]):
            self.assertEqual(a["to"]["at_m"], b["from"]["at_m"])
        self.assertAlmostEqual(sum(st["distance_m"] for st in stages), s["length_m"], delta=len(stages))
        self.assertIn("עין דוד", [x["name"] for x in g["sights"]])
        names = [c["name"] for c in g["credits"]]
        self.assertIn("OpenStreetMap", names)
        self.assertTrue(any(t["kind"] == "shuttle" for t in g["practical"]["tips"]))  # one-way route
        self.assertTrue(any(t["kind"] == "source" for t in g["practical"]["tips"]))

    def test_with_dem_tobler(self):
        g = guide.hike_guide(_con(), 1, Ramp())
        s = g["summary"]
        self.assertEqual(s["time_formula"], "tobler")
        expected_climb = (31.4645 - 31.41) * 11_000
        self.assertAlmostEqual(s["ascent"], expected_climb, delta=expected_climb * 0.05)
        self.assertLess(s["descent"], 20)
        self.assertGreater(s["duration_min"], guide.round5(s["length_m"] / 1000 / 4 * 60))  # climbing is slower
        self.assertEqual(len(g["elevation"]["profile"][0]), 4)
        self.assertGreater(s["max_ele"], s["min_ele"])
        stage_up = sum(st["ascent"] for st in g["stages"])
        self.assertAlmostEqual(stage_up, s["ascent"], delta=10)
        self.assertIn("נתוני גובה", [c["role"] for c in g["credits"]])

    def test_broken_dem_degrades(self):
        g = guide.hike_guide(_con(), 1, Broken())
        self.assertEqual(g["summary"]["time_formula"], "flat")

    def test_loop_and_stream(self):
        g = guide.hike_guide(_con(), 2)
        s = g["summary"]
        self.assertTrue(s["loop"])
        self.assertTrue(s["stream"])
        self.assertEqual(s["difficulty"], "easy")  # "קל" in the source
        self.assertEqual(g["end"]["name"], g["start"]["name"])
        self.assertIn("חוזרים", g["stages"][-1]["instruction"])
        self.assertTrue(any(t["kind"] == "flood" for t in g["practical"]["tips"]))
        self.assertTrue(g["practical"]["water"]["points"])
        self.assertTrue(g["practical"]["toilets"])

    def test_long_line_split_evenly(self):
        # 11 km straight line in an empty part of the map: legs ≤ 4 km, named by distance
        coords = [(34.60 + i * 0.0105, 30.50) for i in range(11)]
        g = guide.build_guide(_con(), coords, {"name": "בדיקה"})
        legs = g["stages"]
        self.assertGreaterEqual(len(legs), 3)
        self.assertTrue(all(st["distance_m"] <= guide.MAX_STAGE_M + 1 for st in legs))
        self.assertTrue(legs[0]["to"]["name"].startswith("נקודת ק״מ"))
        self.assertEqual(g["summary"]["difficulty_source"], "estimated")

    def test_waypoints_order_and_spacing(self):
        coords = [(35.0, 32.0), (35.03, 32.0)]  # ~2.8 km
        wps = [{"name": "ב", "lat": 32.0, "lon": 35.02}, {"name": "א", "lat": 32.0, "lon": 35.01},
               {"name": "קרוב מדי", "lat": 32.0, "lon": 35.0101}, {"name": "רחוק", "lat": 32.1, "lon": 35.01}]
        g = guide.build_guide(_empty_con(), coords, {"waypoints": wps})
        self.assertEqual([st["to"]["name"] for st in g["stages"][:-1]], ["ב"])  # 'א' comes after 'ב' only backwards
        g = guide.build_guide(_empty_con(), coords, {"waypoints": wps[1:]})
        self.assertEqual([st["to"]["name"] for st in g["stages"][:-1]], ["א"])

    def test_too_long_or_short(self):
        with self.assertRaises(ValueError):
            guide.build_guide(_empty_con(), [(34.5, 30.0), (35.5, 33.0)])
        with self.assertRaises(ValueError):
            guide.build_guide(_empty_con(), [(35.0, 32.0), (35.0, 32.00001)])


class MissingDataTest(unittest.TestCase):
    def test_no_tables_at_all(self):
        con = _empty_con()
        g = guide.build_guide(con, [(35.0, 32.0), (35.01, 32.01), (35.02, 32.0)])
        self.assertEqual(g["summary"]["marks"], [])
        self.assertEqual(g["sights"], [])
        self.assertEqual(g["start"]["name"], "נקודת ההתחלה")
        self.assertEqual(guide.list_hikes(con, center=(32.0, 35.0), radius=5000), [])
        self.assertIsNone(guide.hike_detail(con, 1))
        self.assertIsNone(guide.hike_guide(con, 1))
        self.assertEqual(guide.hikes_tile(con, 12, 2447, 1655), b"")

    def test_db_without_hikes_table(self):
        tmp = Path(tempfile.mkdtemp())
        shutil.copy(sample_db(), tmp / "maps.db")
        con = sqlite3.connect(tmp / "maps.db")
        con.execute("DROP TABLE hikes")
        con.execute("DROP TABLE hikes_rtree")
        con.commit()
        con.close()
        ro = db.open_readonly(tmp / "maps.db")
        self.assertEqual(guide.list_hikes(ro, center=(31.45, 35.38), radius=20000), [])
        self.assertIsNone(guide.hike_detail(ro, 1))
        self.assertIsNotNone(guide.trail_guide(ro, 1))  # OSM trails still get a guide


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = Path(tempfile.mkdtemp(prefix="hmaps-guide-"))
        shutil.copy(sample_db(), cls.data / "maps.db")
        cls.api = ApiServer(cls.data)

    @classmethod
    def tearDownClass(cls):
        cls.api.stop()

    def test_hikes_list(self):
        st, body, _ = self.api.get("/api/hikes", lat=31.45, lon=35.38, radius=20000)
        self.assertEqual(st, 200)
        names = [h["name"] for h in body["results"]]
        self.assertEqual(len(names), 2)
        self.assertTrue(all(h["id"].startswith("hike-") for h in body["results"]))
        st, body, _ = self.api.get("/api/hikes", bbox="35.36,31.40,35.39,31.47", geometry=1)
        self.assertEqual(st, 200)
        self.assertEqual(body["results"][0]["geometry"]["type"], "LineString")
        st, body, _ = self.api.get("/api/hikes", lat=32.08, lon=34.78, radius=1000)
        self.assertEqual(body["results"], [])
        self.assertEqual(self.api.get("/api/hikes")[0], 400)
        self.assertEqual(self.api.get("/api/hikes", bbox="1,2,3")[0], 400)

    def test_hike_card_and_guide(self):
        st, h, _ = self.api.get("/api/hikes/hike-1")
        self.assertEqual(st, 200)
        self.assertEqual(h["type"], "hike")
        self.assertEqual(len(h["waypoints"]), 3)
        self.assertEqual(h["difficulty_he"], "בינוני")
        st, g, _ = self.api.get("/api/hikes/hike-1/guide", date="2026-06-21")
        self.assertEqual(st, 200)
        self.assertGreaterEqual(len(g["stages"]), 3)
        self.assertEqual(g["practical"]["daylight"]["date"], "2026-06-21")
        self.assertEqual(self.api.get("/api/hikes/hike-999")[0], 404)
        self.assertEqual(self.api.get("/api/hikes/hike-999/guide")[0], 404)
        self.assertEqual(self.api.get("/api/hikes/hike-1/guide", date="yesterday")[0], 400)

    def test_trail_guide(self):
        st, g, _ = self.api.get("/api/trails/trail-1/guide")
        self.assertEqual(st, 200)
        self.assertEqual(g["kind"], "trail")
        self.assertEqual(g["name"], "שביל ישראל")
        self.assertTrue(all(s["distance_m"] <= guide.MAX_STAGE_M + 1 for s in g["stages"]))
        self.assertEqual(self.api.get("/api/trails/trail-999/guide")[0], 404)

    def test_post_guide(self):
        coords = [[35.370, 31.420], [35.375, 31.430], [35.380, 31.440]]
        st, g = self.api.post("/api/guide", {"coordinates": coords, "name": "מסלול שתכננתי"})
        self.assertEqual(st, 200)
        self.assertEqual(g["name"], "מסלול שתכננתי")
        self.assertEqual(g["kind"], "route")
        self.assertEqual(g["summary"]["marks"][0]["name"], "שביל ישראל")
        self.assertEqual(self.api.post("/api/guide", {"coordinates": [[1, 2]]})[0], 400)
        self.assertEqual(self.api.post("/api/guide", {"coordinates": "x"})[0], 400)
        self.assertEqual(self.api.post("/api/guide", {"coordinates": [[34.5, 30.0], [35.5, 33.0]]})[0], 422)

    def test_hikes_tile(self):
        from hmaps.api import mvt

        x, y = geo.lonlat_to_tile(35.375, 31.43, 13)
        st, body, _ = self.api.get(f"/tiles/hikes/13/{x}/{y}.pbf")
        self.assertEqual(st, 200)
        layer = mvt.decode(body)["hikes"]
        self.assertTrue(any(f["props"]["id"] == 1 for f in layer))
        self.assertEqual(self.api.get("/tiles/hikes/5/1/1.pbf")[0], 204)


if __name__ == "__main__":
    unittest.main()
