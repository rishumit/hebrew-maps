"""Prayer times (Nedarim Plus) – fetch with a fake server, apply to the sample database, API."""

import json
import shutil
import sqlite3
import tempfile
import unittest
import urllib.parse
from datetime import date, datetime
from pathlib import Path

from hmaps.api import minyanim as mn_api
from hmaps.api import pois as poi_api
from hmaps.core import hours
from hmaps.etl import zmanim
from tests.helpers import ApiServer, sample_db

# --- a fake Manage.aspx ---------------------------------------------------------------------------
GREAT = (32.06885, 34.77172)      # ~ 2 m from "בית הכנסת הגדול" (Allenby 110) in the sample data
UNNAMED = (32.0600, 34.7700)      # an unnamed OSM synagogue added by the test
NEW = (32.0500, 34.7600)          # nothing in OSM here
TIMETABLES = {
    "100": {"Day": "3", "ShortId": "123", "DirectId": "", "Message": "", "data": [
        {"T": "שחרית", "L": "בית המדרש", "H": "6:30", "D": "אבגדהו", "C": "ק.הודו"},
        {"T": "שחרית", "L": "", "H": "08:00", "D": "ש", "C": ""},
        {"T": "מנחה", "L": "", "H": "13:30", "D": "אבגדה", "C": ""},
        {"T": "ערבית", "L": "", "H": "20:00", "D": "אבגדה", "C": ""},
        {"T": "שיעור", "L": "", "H": "21:00", "D": "ב", "C": "דף יומי"},
        {"T": "ערבית", "L": "", "H": "bad", "D": "א", "C": ""}]},
    "200": {"Day": "3", "DirectId": "02-5555555", "data": [{"T": "שחרית", "L": "", "H": "07:00", "D": "אבגדה", "C": ""}]},
    "300": {"Day": "3", "data": [{"T": "מנחה", "L": "", "H": "13:00", "D": "אבגדה", "C": ""}]},
}
NEAREST = {  # by rounded location: what the "nearest minyan" search reveals there
    GREAT: [{"T": "מנחה", "L": "בית הכנסת הגדול - נ.פ", "H": "13:30", "C": "", "D": "2 מטר", "M": "100",
             "A": "אלנבי 110 תל אביב-יפו", "W": f"{GREAT[0]}*{GREAT[1]}"}],
    UNNAMED: [{"T": "שחרית", "L": "אוהל משה", "H": "07:00", "C": "", "D": "0 מטר", "M": "200",
               "A": "הרצל 5/2 תל אביב-יפו", "W": f"{UNNAMED[0]}*{UNNAMED[1]}"}],
    NEW: [{"T": "מנחה", "L": "קהילת  זכרון  יעקב", "H": "13:00", "C": "", "D": "0 מטר", "M": "300",
           "A": "רחוב לדוגמה 12 תל אביב-יפו", "W": f"{NEW[0]}*{NEW[1]}"}],
}


def fake_opener(calls):
    def opener(url: str) -> str:
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        calls.append(q)
        if q["Action"] == "GetLocation":
            det = [{"Loc": f"{GREAT[0]}*{GREAT[1]}", "RavKav": "False", "MosadId": "100"},
                   {"Loc": f"{UNNAMED[0]}*{UNNAMED[1]}", "RavKav": "False", "MosadId": "200"},
                   {"Loc": f"{NEW[0]}*{NEW[1]}", "RavKav": "False", "MosadId": "300"},
                   {"Loc": f"{NEW[0] + 0.00001}*{NEW[1]}", "RavKav": "False", "MosadId": "300"},
                   {"Loc": "32.1*34.8", "RavKav": "False", "MosadId": "0"},          # test entry
                   {"Loc": "32.2*34.9", "RavKav": "False", "MosadId": "400"}]        # no data at all
            return json.dumps({"Details": det}, ensure_ascii=False)
        if q["Action"] == "GetMosadMinyan":
            d = TIMETABLES.get(q["MosadId"])
            return json.dumps(d, ensure_ascii=False) if d else ""
        if q["Action"] == "GetNearestMinyan":
            here = (round(float(q["lat"]), 5), round(float(q["lng"]), 5))
            rows = [r for loc, rs in NEAREST.items() for r in rs
                    if abs(loc[0] - here[0]) < 0.0002 and abs(loc[1] - here[1]) < 0.0002]
            return json.dumps({"data": rows}, ensure_ascii=False)
        raise AssertionError(url)
    return opener


class ParseTest(unittest.TestCase):
    def test_minyanim_and_phone(self):
        items, phone, msg = zmanim.parse_minyanim(TIMETABLES["100"])
        self.assertEqual(len(items), 5)  # the bad time is dropped
        self.assertEqual(items[0], {"prayer": "שחרית", "time": "06:30", "days": "אבגדהו", "hall": "בית המדרש", "note": "ק.הודו"})
        self.assertEqual(phone, "03-7630585 שלוחה 123")
        self.assertEqual(zmanim.parse_minyanim(TIMETABLES["200"])[1], "02-5555555 שלוחה 5")
        self.assertEqual(zmanim.parse_minyanim(None), ([], None, None))

    def test_helpers(self):
        self.assertEqual(zmanim.norm_time("7:05"), "07:05")
        self.assertIsNone(zmanim.norm_time("25:00"))
        self.assertEqual(zmanim.norm_days("ואש"), "אוש")
        self.assertEqual(zmanim.clean_name("קהילות יעקב  - נוה יעקב  - נ.פ"), "קהילות יעקב - נוה יעקב")
        self.assertEqual(zmanim.clean_name("בית המדרש יד סופר - אח לאח"), "בית המדרש יד סופר")
        self.assertEqual(zmanim.clean_name("קהילת בני הישיבות - מ.ש.ג.ב"), "קהילת בני הישיבות")
        self.assertEqual(zmanim.clean_name("שערי תורה - מ.ה"), "שערי תורה")
        self.assertEqual(zmanim.clean_name("משכן אשר - בני ברק"), "משכן אשר - בני ברק")
        places = ["ירושלים", "תל אביב-יפו", "נווה יעקב"]
        self.assertEqual(zmanim.parse_address("אביר יעקב 30/8 ירושלים", places),
                         {"street": "אביר יעקב", "housenumber": "30", "city": "ירושלים"})
        self.assertEqual(zmanim.parse_address("הרב פרדס 71 נוה יעקב ירושלים", places)["city"], "ירושלים")
        self.assertEqual(zmanim.parse_address("נווה יעקב", places), {"street": None, "housenumber": None, "city": "נווה יעקב"})
        self.assertEqual(zmanim.parse_address("", places)["street"], None)
        self.assertGreater(zmanim.syn_name_score("בית הכנסת אוהל משה", "אוהל משה"), 0.9)
        self.assertEqual(zmanim.syn_name_score("בית הכנסת הגדול", "בית כנסת"), 0.0)

    def test_sun_times(self):
        s = mn_api.sun_times(31.84, 35.24, date(2026, 10, 14))  # the source: sunset 18:08 in Neve Yaakov
        self.assertEqual(s["sunset"], "18:08")
        self.assertTrue("06:30" <= s["sunrise"] <= "06:50")


class FetchApplyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="hmaps-zm-"))
        cls.calls = []
        f = zmanim.Fetcher(None, interval=0, opener=fake_opener(cls.calls))
        cls.snap = zmanim.fetch(cls.tmp, workers=2, fetcher=f)
        cls.db = cls.tmp / "maps.db"
        shutil.copy(sample_db(), cls.db)
        con = sqlite3.connect(cls.db)
        cls.unnamed_id = con.execute("SELECT MAX(id) + 1 FROM pois").fetchone()[0]
        con.execute("INSERT INTO pois (id, osm, category, sub, kind_he, lat, lon, importance) VALUES (?,?,?,?,?,?,?,?)",
                    (cls.unnamed_id, "n999", "religion", "synagogue", "בית כנסת", UNNAMED[0] + 0.0002, UNNAMED[1], 0.45))
        con.execute("INSERT INTO pois_rtree VALUES (?,?,?,?,?)",
                    (cls.unnamed_id, UNNAMED[1], UNNAMED[1], UNNAMED[0] + 0.0002, UNNAMED[0] + 0.0002))
        con.commit()
        cls.pois_before = con.execute("SELECT COUNT(*) FROM pois").fetchone()[0]
        cls.great_id = con.execute("SELECT id FROM pois WHERE name = 'בית הכנסת הגדול'").fetchone()[0]
        cls.stats = zmanim.apply(con, cls.snap)
        con.close()

    def con(self):
        c = sqlite3.connect(self.db)
        c.row_factory = sqlite3.Row
        return c

    def test_snapshot(self):
        p = zmanim.snapshot_path(self.tmp)
        self.assertTrue(p.exists())
        snap = json.loads(p.read_text(encoding="utf-8"))
        by = {m["id"]: m for m in snap["mosads"]}
        self.assertEqual(set(by), {"100", "200", "300", "400"})  # "0" skipped
        self.assertEqual(by["100"]["name"], "בית הכנסת הגדול")
        self.assertEqual(by["300"]["name"], "קהילת זכרון יעקב")
        self.assertEqual(len(by["300"]["locations"]), 2)
        self.assertIsNone(by["400"]["name"])
        self.assertEqual(snap["stats"]["with_name"], 3)
        self.assertEqual(snap["stats"]["minyanim"], 7)
        # no "nearest" request for a mosad without timetable
        self.assertFalse([c for c in self.calls if c["Action"] == "GetNearestMinyan"
                          and abs(float(c["lat"]) - 32.2) < 1e-6])

    def test_names_survive_a_refetch(self):
        tmp = Path(tempfile.mkdtemp(prefix="hmaps-zm2-"))
        zmanim.fetch(tmp, fetcher=zmanim.Fetcher(None, interval=0, opener=fake_opener([])))
        saved = dict(NEAREST)
        NEAREST.clear()  # e.g. a Shabbat-only synagogue on a weekday: no names this time
        try:
            snap = zmanim.fetch(tmp, fetcher=zmanim.Fetcher(None, interval=0, opener=fake_opener([])))
        finally:
            NEAREST.update(saved)
        self.assertEqual({m["id"]: m["name"] for m in snap["mosads"]}["100"], "בית הכנסת הגדול")
        self.assertEqual(snap["stats"]["names_from_previous"], 3)

    def test_apply_links_and_adds(self):
        self.assertEqual(self.stats["matched_osm"], 2)
        self.assertEqual(self.stats["new_pois"], 1)
        self.assertEqual(self.stats["no_name_skipped"], 1)  # 400: no name, no times
        con = self.con()
        link = {r["mosad_id"]: r["poi_id"] for r in con.execute("SELECT * FROM synagogues_ext")}
        self.assertEqual(link["100"], self.great_id)
        self.assertEqual(link["200"], self.unnamed_id)
        r = con.execute("SELECT * FROM pois WHERE id = ?", (self.unnamed_id,)).fetchone()
        self.assertEqual((r["name"], r["street"], r["housenumber"], r["city"]), ("אוהל משה", "הרצל", "5", "תל אביב-יפו"))
        new = con.execute("SELECT * FROM pois WHERE id = ?", (link["300"],)).fetchone()
        self.assertEqual((new["osm"], new["sub"], new["name"], new["street"], new["housenumber"]),
                         ("zm:300", "synagogue", "קהילת זכרון יעקב", "רחוב לדוגמה", "12"))
        self.assertTrue(con.execute("SELECT 1 FROM pois_rtree WHERE id = ?", (link["300"],)).fetchone())
        # searchable
        hits = con.execute("SELECT i.ref_id FROM search_fts f JOIN search_items i ON i.id = f.rowid "
                           "WHERE search_fts MATCH ?", ('"זכרונ" "יעקב"',)).fetchall()
        self.assertIn(link["300"], [h[0] for h in hits])
        hits = con.execute("SELECT i.ref_id FROM search_fts f JOIN search_items i ON i.id = f.rowid "
                           "WHERE search_fts MATCH ?", ('"אוהל" "משה"',)).fetchall()
        self.assertIn(self.unnamed_id, [h[0] for h in hits])
        self.assertEqual(con.execute("SELECT COUNT(*) FROM minyanim").fetchone()[0], 7)
        self.assertEqual(json.loads(con.execute("SELECT value FROM meta WHERE key = 'zmanim'").fetchone()[0])["new_pois"], 1)

    def test_reapply_is_idempotent_and_removes(self):
        tmp = Path(tempfile.mkdtemp(prefix="hmaps-zm3-")) / "maps.db"
        shutil.copy(self.db, tmp)
        con = sqlite3.connect(tmp)
        n = con.execute("SELECT COUNT(*) FROM pois").fetchone()[0]
        st = zmanim.apply(con, self.snap)
        self.assertEqual((st["new_pois"], st["updated_new_pois"]), (0, 1))
        self.assertEqual(con.execute("SELECT COUNT(*) FROM pois").fetchone()[0], n)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM search_items WHERE name = 'קהילת זכרון יעקב'").fetchone()[0], 1)
        snap = {**self.snap, "mosads": [m for m in self.snap["mosads"] if m["id"] != "300"]}
        st = zmanim.apply(con, snap)
        self.assertEqual(st["removed_pois"], 1)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM pois WHERE osm LIKE 'zm:%'").fetchone()[0], 0)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM search_items WHERE name = 'קהילת זכרון יעקב'").fetchone()[0], 0)
        con.close()

    def test_poi_card_block(self):
        con = self.con()
        card = poi_api.poi_detail(con, self.great_id)
        m = card["minyanim"]
        self.assertEqual(m["phone"], "03-7630585 שלוחה 123")
        self.assertEqual(m["source"], "נדרים פלוס – פרוייקט זמני תפילות הארצי")
        self.assertEqual([i["time"] for i in m["items"]][:2], ["06:30", "08:00"])
        self.assertEqual(m["updated"], self.snap["fetched"])
        self.assertIn("sunset", m["sun"])
        new_id = con.execute("SELECT poi_id FROM synagogues_ext WHERE mosad_id = '300'").fetchone()[0]
        self.assertEqual(poi_api.poi_detail(con, new_id)["metadata"]["source"], "נדרים פלוס")
        other = con.execute("SELECT id FROM pois WHERE sub = 'restaurant' LIMIT 1").fetchone()[0]
        self.assertNotIn("minyanim", poi_api.poi_detail(con, other))

    def test_near(self):
        con = self.con()
        wed_morning = datetime(2026, 10, 14, 9, 0, tzinfo=hours.TZ)  # Wednesday = ד
        res = mn_api.near(con, GREAT[0], GREAT[1], 3000, now=wed_morning)
        rows = [(r["mosad_id"], r["time"], r["tomorrow"]) for r in res["results"]]
        self.assertEqual(rows[0], ("300", "13:00", False))
        self.assertIn(("100", "13:30", False), rows)
        self.assertIn(("100", "06:30", True), rows)   # Thursday morning, after today's times
        self.assertNotIn("שיעור", [r["prayer"] for r in res["results"]])
        times = [(r["tomorrow"], r["time"]) for r in res["results"]]
        self.assertEqual(times, sorted(times))
        self.assertEqual(res["meta"]["day"], "ד")
        # explicit filters
        res = mn_api.near(con, GREAT[0], GREAT[1], 3000, prayer="ערבית", after="12:00", day="ד")
        self.assertEqual({r["time"] for r in res["results"]}, {"20:00"})
        res = mn_api.near(con, GREAT[0], GREAT[1], 3000, after="07:00", day="ש")
        self.assertEqual([(r["time"], r["mosad_id"]) for r in res["results"]][0], ("08:00", "100"))
        # radius
        res = mn_api.near(con, GREAT[0], GREAT[1], 100, prayer="מנחה", after="00:00", day="א")
        self.assertEqual({r["mosad_id"] for r in res["results"]}, {"100"})
        self.assertEqual(res["results"][0]["poi_id"], f"poi-{self.great_id}")

    def test_old_database_without_tables(self):
        con = sqlite3.connect(":memory:")
        self.assertEqual(mn_api.near(con, 32, 34.8)["results"], [])
        self.assertIsNone(mn_api.poi_block(con, 1))


class ZmanimHttpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        t = FetchApplyTest
        t.setUpClass()
        cls.great_id = t.great_id
        cls.data = Path(tempfile.mkdtemp(prefix="hmaps-zm-api-"))
        shutil.copy(t.db, cls.data / "maps.db")
        cls.api = ApiServer(cls.data)

    @classmethod
    def tearDownClass(cls):
        cls.api.stop()

    def test_endpoints(self):
        s, body, _ = self.api.get(f"/api/poi/poi-{self.great_id}")
        self.assertEqual(s, 200)
        self.assertEqual(body["minyanim"]["items"][0]["prayer"], "שחרית")
        s, body, _ = self.api.get("/api/minyanim/near", lat=GREAT[0], lon=GREAT[1], radius=3000, after="12:00", day="2")
        self.assertEqual(s, 200)
        self.assertEqual(body["meta"]["day"], "ג")
        self.assertEqual(body["results"][0]["time"], "13:00")
        s, body, _ = self.api.get("/api/minyanim/near", lat=GREAT[0], lon=GREAT[1], prayer="מוסף")
        self.assertEqual(s, 400)
        s, body, _ = self.api.get("/api/minyanim/near", lat=GREAT[0], lon=GREAT[1], after="7")
        self.assertEqual(s, 400)
        s, body, _ = self.api.get("/api/minyanim/near", lat=GREAT[0])
        self.assertEqual(s, 400)
        s, body, _ = self.api.get("/api/minyanim/near", lat=GREAT[0], lon=GREAT[1], day="שבת", after="06:00")
        self.assertEqual(body["results"][0]["time"], "08:00")


if __name__ == "__main__":
    unittest.main()
