"""Public transport: GTFS ETL, RAPTOR engine (transfers, after midnight, no-service days, arrive-by) and API."""

import json
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path

from tests import gtfs_fixture
from tests.helpers import ApiServer

A = (32.0000, 34.8000)
C = (32.0000, 34.8200)
E = (32.0100, 34.8300)
F = (32.0500, 34.8000)
H = (32.1500, 34.8000)


def _build(root: Path) -> Path:
    from hmaps.transit import gtfs

    z = gtfs_fixture.write(root / "src" / "gtfs.zip")
    gtfs.build(z, root / "transit", source="test")
    return root / "transit"


class TransitEngineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from hmaps.transit import data, planner

        cls.root = Path(tempfile.mkdtemp(prefix="hmaps-transit-"))
        cls.dir = _build(cls.root)
        cls.data = data.get(cls.dir)
        cls.planner = planner

    def plan(self, frm, to, when, arrive=False):
        res = self.planner.plan(self.data, frm, to, self.planner.parse_time(when), arrive=arrive)
        for it in res["itineraries"]:
            self._check_consistent(it)
        return res

    def _check_consistent(self, it):
        legs = it["legs"]
        self.assertTrue(legs)
        self.assertEqual(it["departure"], legs[0]["departure"])
        self.assertEqual(it["arrival"], legs[-1]["arrival"])
        for a, b in zip(legs, legs[1:]):
            self.assertLessEqual(datetime.fromisoformat(a["arrival"]), datetime.fromisoformat(b["departure"]),
                                 f"{a['instruction']} → {b['instruction']}")
        for lg in legs:
            self.assertGreaterEqual(len(lg["geometry"]["coordinates"]), 2)
            self.assertTrue(lg["instruction"])

    @staticmethod
    def rides(it):
        return [lg for lg in it["legs"] if lg["type"] != "walk"]

    # ------------------------------------------------------------------ ETL
    def test_store_and_meta(self):
        d = self.data
        self.assertEqual(d.meta["start"], "2026-10-01")
        self.assertEqual(d.meta["end"], "2026-11-30")
        self.assertEqual(d.meta["routes"], 4)  # the demand-responsive route (715) is skipped
        self.assertEqual(d.meta["trips"], 9)
        self.assertTrue((self.dir / "transit.current").exists())
        meta = json.loads((self.dir / "transit.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["file"], (self.dir / "transit.current").read_text(encoding="utf-8").strip())
        # foot transfer C ↔ C2 (~100 m), none between A and B (~950 m)
        c, c2, a, b = (d.id_index[x] for x in ("C", "C2", "A", "B"))
        self.assertIn(c2, d.tr_to[d.tr_ptr[c]:d.tr_ptr[c + 1]].tolist())
        self.assertNotIn(b, d.tr_to[d.tr_ptr[a]:d.tr_ptr[a + 1]].tolist())
        self.assertEqual(d.stop_info(a)["city"], "תל אביב יפו")
        # holiday removed by calendar_dates
        self.assertEqual(int(d.day_trips[d.day_index(datetime(2026, 10, 19).date())]), 0)
        self.assertGreater(int(d.day_trips[d.day_index(datetime(2026, 10, 14).date())]), 0)

    def test_republish_is_atomic_and_reloads(self):
        from hmaps.transit import data, gtfs

        root = Path(tempfile.mkdtemp(prefix="hmaps-transit2-"))
        z = gtfs_fixture.write(root / "g.zip")
        gtfs.build(z, root / "transit")
        first = data.get(root / "transit")
        time.sleep(1.1)  # versioned names have one-second resolution
        gtfs.build(z, root / "transit")
        second = data.get(root / "transit")
        self.assertIsNot(first, second)
        self.assertNotEqual(first.path, second.path)
        self.assertLessEqual(len(list((root / "transit").glob("transit-*.npz"))), 2)

    # ------------------------------------------------------------------ engine
    def test_direct_ride(self):
        res = self.plan(A, C, f"{gtfs_fixture.WEDNESDAY}T07:55")
        it = res["itineraries"][0]
        r = self.rides(it)
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]["line"], "קו 1")
        self.assertEqual(r[0]["agency"], "אגד")
        self.assertEqual(r[0]["departure_text"], "08:00")
        self.assertEqual(r[0]["arrival_text"], "08:10")
        self.assertEqual(r[0]["num_stops"], 2)
        self.assertEqual(r[0]["from"]["name"], "אלף/הרצל")
        self.assertIn("ירידה בתחנה גימל/מרכז אחרי 2 תחנות", r[0]["instruction"])
        self.assertGreaterEqual(len(r[0]["geometry"]["coordinates"]), 4)  # follows shapes.txt
        self.assertTrue(res["estimated"])
        self.assertEqual(res["estimated_text"], "זמנים משוערים לפי לוח הזמנים")

    def test_transfer_with_walk(self):
        res = self.plan(A, E, f"{gtfs_fixture.WEDNESDAY}T07:55")
        it = res["itineraries"][0]
        self.assertEqual(it["transfers"], 1)
        r = self.rides(it)
        self.assertEqual([x["line"] for x in r], ["קו 1", "קו 2"])
        self.assertEqual(r[0]["departure_text"], "08:00")
        self.assertEqual(r[1]["departure_text"], "08:20")
        self.assertEqual(it["arrival_text"], "08:30")
        walks = [lg for lg in it["legs"] if lg["type"] == "walk" and lg["kind"] == "transfer"]
        self.assertEqual(len(walks), 1)
        self.assertTrue(walks[0]["instruction"].startswith("הליכה"))
        self.assertIn("גימל 2/מסוף", walks[0]["instruction"])
        # later alternatives are offered too
        deps = [x["departure_text"] for x in res["itineraries"]]
        self.assertGreater(len(deps), 1)
        self.assertIn("fewest_transfers", res["itineraries"][0]["tags"] + sum((x["tags"] for x in res["itineraries"]), []))

    def test_arrive_by(self):
        res = self.plan(A, E, f"{gtfs_fixture.WEDNESDAY}T09:00", arrive=True)
        self.assertEqual(res["mode"], "arrive")
        it = res["itineraries"][0]
        r = self.rides(it)
        self.assertEqual([x["departure_text"] for x in r], ["08:30", "08:50"])
        self.assertEqual(it["arrival_text"], "09:00")
        self.assertIn("latest_departure", it["tags"])
        for x in res["itineraries"]:
            self.assertLessEqual(datetime.fromisoformat(x["arrival"]),
                                 datetime.fromisoformat(f"{gtfs_fixture.WEDNESDAY}T09:00+03:00"))

    def test_after_midnight_uses_yesterdays_service(self):
        res = self.plan(A, E, f"{gtfs_fixture.WEDNESDAY}T00:05")
        it = res["itineraries"][0]
        r = self.rides(it)
        self.assertEqual(r[0]["line"], "קו 3")
        self.assertEqual(r[0]["departure"][:16], f"{gtfs_fixture.WEDNESDAY}T00:10")
        self.assertEqual(r[0]["arrival"][:16], f"{gtfs_fixture.WEDNESDAY}T00:40")
        # Tuesday-night bus does not run on Wednesday night
        res = self.plan(A, E, "2026-10-15T00:05")
        self.assertEqual(res["reason"], "night")
        self.assertTrue(res["next_departure"].startswith("2026-10-15T08:00"))
        self.assertEqual(self.rides(res["itineraries"][0])[0]["line"], "קו 1")

    def test_late_evening_query_reaches_after_midnight_trip(self):
        res = self.plan(A, E, f"{gtfs_fixture.TUESDAY}T23:50")
        r = self.rides(res["itineraries"][0])
        self.assertEqual(r[0]["departure"][:16], f"{gtfs_fixture.WEDNESDAY}T00:10")

    def test_shabbat_no_service(self):
        res = self.plan(A, C, "2026-10-17T10:00")
        self.assertEqual(res["reason"], "shabbat")
        self.assertIn("שבת", res["notice"])
        self.assertTrue(res["next_departure"].startswith("2026-10-18T08:00"))
        self.assertTrue(res["itineraries"])  # the next available journey
        self.assertTrue(res["itineraries"][0]["departure"].startswith("2026-10-18"))
        # Friday afternoon counts as Shabbat
        res = self.plan(A, C, "2026-10-16T16:00")
        self.assertEqual(res["reason"], "shabbat")
        # Friday morning has the Friday trip
        res = self.plan(A, C, "2026-10-16T07:50")
        self.assertIsNone(res["reason"])
        self.assertEqual(self.rides(res["itineraries"][0])[0]["departure_text"], "08:00")

    def test_holiday_and_out_of_range(self):
        res = self.plan(A, C, f"{gtfs_fixture.HOLIDAY}T10:00")
        self.assertEqual(res["reason"], "holiday")
        self.assertTrue(res["next_departure"].startswith("2026-10-20T08:00"))
        res = self.plan(A, C, "2027-01-05T10:00")
        self.assertEqual(res["reason"], "out_of_range")
        self.assertEqual(res["itineraries"], [])

    def test_rail_leg(self):
        res = self.plan(F, H, f"{gtfs_fixture.WEDNESDAY}T08:00")
        r = self.rides(res["itineraries"][0])
        self.assertEqual(r[0]["type"], "rail")
        self.assertEqual(r[0]["line"], "רכבת")
        self.assertEqual(r[0]["headsign"], "תחנת חית, חדרה")
        self.assertEqual(r[0]["train_number"], "123")
        self.assertEqual(r[0]["from"]["platform"], "1")
        self.assertIn("רציף 1", r[0]["instruction"])
        self.assertEqual(r[0]["num_stops"], 2)

    def test_walk_only_and_no_stops(self):
        res = self.plan(A, (32.0, 34.806), f"{gtfs_fixture.WEDNESDAY}T08:00")
        self.assertTrue(any(i["summary"] == "הליכה" for i in res["itineraries"]))
        res = self.plan((31.0, 35.0), C, f"{gtfs_fixture.WEDNESDAY}T08:00")
        self.assertEqual(res["reason"], "no_stops_nearby")

    def test_departures_board(self):
        d = self.data
        res = self.planner.departures(d, d.id_index["A"], self.planner.parse_time(f"{gtfs_fixture.WEDNESDAY}T07:00"))
        times = [x["time_text"] for x in res["departures"]]
        self.assertEqual(times[:3], ["08:00", "08:30", "09:00"])
        self.assertEqual(res["departures"][0]["line"], "קו 1")
        self.assertEqual(res["departures"][0]["headsign"], "גימל")
        self.assertEqual(res["stop"]["code"], 1000)
        self.assertIn("קו 3", [x["line"] for x in res["lines"]])

    def test_status_freshness(self):
        from datetime import date
        st = self.planner.status(self.data, today=date(2026, 10, 14))
        self.assertTrue(st["available"])
        self.assertEqual(st["feed_end"], "2026-11-30")
        st = self.planner.status(self.data, today=date(2026, 12, 5))
        self.assertEqual(st["freshness"], "expired")
        self.assertIn("פג תוקף", st["warning"])
        st = self.planner.status(self.data, today=date(2026, 11, 27))
        self.assertEqual(st["freshness"], "expiring")
        self.assertFalse(self.planner.status(None)["available"])

    def test_parse_time(self):
        p = self.planner.parse_time
        self.assertEqual(p("2026-10-14T08:00").hour, 8)
        self.assertEqual(p("2026-10-14T05:00:00Z").hour, 8)  # UTC → Israel summer time
        with self.assertRaises(self.planner.TransitError):
            p("yesterday")


class TransitApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp(prefix="hmaps-transit-api-"))
        _build(cls.root)
        cls.api = ApiServer(cls.root)
        cls.empty = Path(tempfile.mkdtemp(prefix="hmaps-transit-empty-"))
        cls.api_empty = ApiServer(cls.empty)

    @classmethod
    def tearDownClass(cls):
        cls.api.stop()
        cls.api_empty.stop()

    def test_plan(self):
        s, body, h = self.api.get("/api/transit", **{"from": "32.0,34.8", "to": "32.01,34.83",
                                                     "time": f"{gtfs_fixture.WEDNESDAY}T07:55", "mode": "depart"})
        self.assertEqual(s, 200, body)
        it = body["itineraries"][0]
        self.assertEqual(it["transfers"], 1)
        types = [lg["type"] for lg in it["legs"]]
        self.assertIn("bus", types)
        self.assertIn("walk", types)
        self.assertIn("max-age", h.get("cache-control", ""))
        self.assertTrue(body["feed"]["available"])
        s, body, _ = self.api.get("/api/transit", **{"from": "32.0,34.8", "to": "32.01,34.83",
                                                     "time": f"{gtfs_fixture.WEDNESDAY}T09:00", "mode": "arrive"})
        self.assertEqual(s, 200)
        self.assertEqual(body["itineraries"][0]["arrival_text"], "09:00")

    def test_bad_parameters(self):
        s, body, _ = self.api.get("/api/transit", **{"from": "32.0,34.8"})
        self.assertEqual(s, 400)
        self.assertEqual(body["error"]["code"], "missing_parameter")
        s, body, _ = self.api.get("/api/transit", **{"from": "32.0,34.8", "to": "x", "mode": "depart"})
        self.assertEqual(s, 400)
        s, body, _ = self.api.get("/api/transit", **{"from": "32.0,34.8", "to": "32.01,34.83", "mode": "fly"})
        self.assertEqual(s, 400)
        s, body, _ = self.api.get("/api/transit", **{"from": "32.0,34.8", "to": "32.01,34.83", "time": "bad"})
        self.assertEqual(s, 400)
        self.assertIn("ISO", body["error"]["message"])

    def test_stop_and_stops(self):
        s, body, _ = self.api.get("/api/transit/stop/A", time=f"{gtfs_fixture.WEDNESDAY}T07:00")
        self.assertEqual(s, 200)
        self.assertEqual(body["departures"][0]["time_text"], "08:00")
        s, body2, _ = self.api.get("/api/transit/stop/code-1000", time=f"{gtfs_fixture.WEDNESDAY}T07:00")
        self.assertEqual(body2["stop"]["id"], "A")
        s, body, _ = self.api.get("/api/transit/stop/nope")
        self.assertEqual(s, 404)
        s, body, _ = self.api.get("/api/transit/stops", lat="32.0", lon="34.82", radius="300")
        self.assertEqual(s, 200)
        self.assertEqual({x["id"] for x in body["stops"]}, {"C", "C2"})

    def test_status(self):
        s, body, _ = self.api.get("/api/transit/status")
        self.assertEqual(s, 200)
        self.assertTrue(body["available"])
        self.assertEqual(body["feed_start"], "2026-10-01")

    def test_disabled_without_data(self):
        s, body, _ = self.api_empty.get("/api/transit", **{"from": "32.0,34.8", "to": "32.01,34.83"})
        self.assertEqual(s, 503)
        self.assertEqual(body["error"]["code"], "transit_not_ready")
        self.assertIn("update-windows.cmd", body["error"]["message"])
        s, body, _ = self.api_empty.get("/api/transit/status")
        self.assertEqual(s, 200)
        self.assertFalse(body["available"])
        s, body, _ = self.api_empty.get("/api/transit/stop/A")
        self.assertEqual(s, 503)
        # the rest of the API is unaffected
        s, body, _ = self.api_empty.get("/api/categories")
        self.assertEqual(s, 200)


if __name__ == "__main__":
    unittest.main()
