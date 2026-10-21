"""Operator logos: name -> brand mapping, bundled files / manifest, and the fields in POI and transit answers."""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from hmaps.core import logos
from tests import gtfs_fixture
from tests.helpers import ROOT, sample_db

LOGO_DIR = ROOT / "web" / "logos"


class MappingTest(unittest.TestCase):
    def test_normalize(self):
        n = logos.normalize
        self.assertEqual(n('קק"ל'), "קקל")
        self.assertEqual(n("קק״ל"), "קקל")
        self.assertEqual(n("  רט׳׳ג "), "רטג")
        self.assertEqual(n('נתיב אקספרס - תחבורה ציבורית בע"מ'), "נתיב אקספרס תחבורה ציבורית")
        self.assertEqual(n("Israel  Nature-and Parks Authority"), "israel nature and parks authority")
        self.assertEqual(n(None), "")

    def test_match_variants(self):
        m = logos.match
        cases = {
            "אגד": "egged", "Egged": "egged", "אגד תעבורה": "egged-taavura",
            "דן": "dan", "דן בדרום": "dan-south", "דן באר שבע": "dan-beersheva",
            "מטרופולין": "metropoline", "קווים": "kavim", "אפיקים": "afikim", "אלקטרה אפיקים": "electra-afikim",
            "סופרבוס": "superbus", 'נתיב אקספרס - תחבורה ציבורית בע"מ': "nateev-express", "תנופה": "tnufa",
            "גלים": "galim", "אקסטרה": "extra", "בית שמש אקספרס": "beit-shemesh-express",
            "רכבת ישראל": "israel-railways", "כרמלית": "carmelit", "סיטיפס": "jerusalem-light-rail",
            'נת"ע': "dankal", "תבל": "tevel", "גי.בי.טורס": "gb-tours",
            "רשות הטבע והגנים": "inpa", "Israel Nature and Parks Authority": "inpa", 'רט"ג': "inpa",
            'קק"ל': "kkl", "קרן קיימת לישראל": "kkl", "KKL-JNF": "kkl",
        }
        for name, key in cases.items():
            self.assertEqual(m(name), key, name)

    def test_no_false_positives(self):
        for name in ("דנדי", "בנק לאומי", "ועדת השבילים", "", None, "עיריית תל אביב", "אגדה", "גלי צה\"ל"):
            self.assertIsNone(logos.match(name), name)
        # an alias at the start of a longer name still matches, longest alias first
        self.assertEqual(logos.match("אגד – מחוז ירושלים"), "egged")
        self.assertEqual(logos.match("דן בדרום בע\"מ"), "dan-south")

    def test_brand_shape(self):
        b = logos.brand("אגד")
        self.assertEqual(b, {"key": "egged", "name": "אגד", "short": "אגד", "color": b["color"], "logo": "/logos/egged.svg"})
        dan = logos.brand("דן")
        self.assertIsNone(dan["logo"])  # no genuine file -> text badge
        self.assertTrue(dan["short"] and dan["color"].startswith("#"))
        self.assertIsNone(logos.brand("חברה לא מוכרת"))

    def test_poi_brand(self):
        self.assertEqual(logos.poi_brand({"operator": "רשות הטבע והגנים"})["logo"], "/logos/inpa.svg")
        self.assertEqual(logos.poi_brand({"inpa": "name"})["key"], "inpa")
        self.assertEqual(logos.poi_brand({"operator": 'קק"ל'})["key"], "kkl")
        self.assertIsNone(logos.poi_brand({"operator": "בנק לאומי"}))
        self.assertIsNone(logos.poi_brand({}))


class FilesTest(unittest.TestCase):
    def test_manifest_matches_files(self):
        data = json.loads((LOGO_DIR / "logos.json").read_text(encoding="utf-8"))
        self.assertEqual(data, logos.manifest())
        attribution = (LOGO_DIR / "ATTRIBUTION.md").read_text(encoding="utf-8")
        for key, rec in data.items():
            path = LOGO_DIR / rec["file"]
            self.assertTrue(path.is_file(), rec["file"])
            self.assertTrue(rec["source"].startswith("https://commons.wikimedia.org/wiki/File:"), key)
            self.assertTrue(rec["license"], key)
            self.assertIn(rec["file"], attribution)
            head = path.read_bytes()[:4096]
            if rec["file"].endswith(".svg"):
                self.assertIn(b"<svg", head)
                body = path.read_text(encoding="utf-8")
                # nothing in a logo may reach out to the internet at runtime
                self.assertNotRegex(body, r"(?:href|src)\s*=\s*[\"']https?://(?!www\.w3\.org)")
                self.assertNotIn("<script", body.lower())
            else:
                self.assertTrue(head.startswith(b"\x89PNG"), rec["file"])

    def test_service_worker_precaches_logos(self):
        sw = (ROOT / "web" / "sw.js").read_text(encoding="utf-8")
        for rec in logos.manifest().values():
            self.assertIn(f"'{rec['file']}'", sw)

    def test_every_logo_brand_has_a_file(self):
        for b in logos.BRANDS:
            if b.file:
                self.assertTrue((LOGO_DIR / b.file).is_file(), b.file)
                self.assertTrue(b.source and b.license, b.key)


class PoiFieldTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.con = sqlite3.connect(sample_db())
        cls.con.row_factory = sqlite3.Row

    @classmethod
    def tearDownClass(cls):
        cls.con.close()

    def _detail(self, name):
        from hmaps.api import pois

        r = self.con.execute("SELECT id FROM pois WHERE name = ?", (name,)).fetchone()
        return pois.poi_detail(self.con, r["id"])

    def test_inpa_site_by_operator_and_by_name(self):
        masada = self._detail("גן לאומי מצדה")
        self.assertEqual(masada["operator_logo"], "/logos/inpa.svg")
        self.assertEqual(masada["operator_brand"]["key"], "inpa")
        self.assertEqual(self._detail("גן לאומי עין אפק")["operator_logo"], "/logos/inpa.svg")

    def test_kkl_and_rail_operator(self):
        self.assertEqual(self._detail("שמורת טבע לדוגמה")["operator_brand"]["key"], "kkl")
        self.assertEqual(self._detail("תל אביב סבידור מרכז")["operator_logo"], "/logos/israel-railways.svg")

    def test_no_operator(self):
        d = self._detail("גן מאיר")
        self.assertIsNone(d["operator_logo"])
        self.assertIsNone(d["operator_brand"])


class TransitFieldTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from hmaps.transit import data, gtfs, planner

        root = Path(tempfile.mkdtemp(prefix="hmaps-logos-"))
        gtfs.build(gtfs_fixture.write(root / "g.zip"), root / "transit", source="test")
        cls.data = data.get(root / "transit")
        cls.planner = planner

    def test_departures_and_lines(self):
        d = self.data
        res = self.planner.departures(d, d.id_index["A"], self.planner.parse_time(f"{gtfs_fixture.WEDNESDAY}T07:00"))
        dep = res["departures"][0]
        self.assertEqual(dep["agency"], "אגד")
        self.assertEqual(dep["agency_logo"], "/logos/egged.svg")
        self.assertEqual(dep["agency_brand"]["key"], "egged")
        self.assertTrue(all(x["agency_logo"] == "/logos/egged.svg" for x in res["lines"] if x["type"] == "bus"))

    def test_itinerary_legs(self):
        res = self.planner.plan(self.data, (32.0, 34.8), (32.0, 34.82), self.planner.parse_time(f"{gtfs_fixture.WEDNESDAY}T07:55"))
        rides = [leg for it in res["itineraries"] for leg in it["legs"] if leg["type"] != "walk"]
        self.assertTrue(rides)
        for leg in rides:
            self.assertIn("agency_logo", leg)
            self.assertEqual(leg["agency_logo"], logos.brand(leg["agency"])["logo"])


if __name__ == "__main__":
    unittest.main()


class MapLogoBrandTest(unittest.TestCase):
    """POI tiles carry `brand` for INPA / KKL sites so the map draws the official logo."""

    def test_map_brand(self):
        from hmaps.api.tiles import _map_brand
        self.assertEqual(_map_brand('{"inpa": "name"}'), "inpa")
        self.assertEqual(_map_brand('{"operator": "רשות הטבע והגנים"}'), "inpa")
        self.assertEqual(_map_brand('{"operator": "קרן קיימת לישראל"}'), "kkl")
        self.assertIsNone(_map_brand('{"operator": "אגד"}'))  # bus companies keep the category icon
        self.assertIsNone(_map_brand(None))
        self.assertIsNone(_map_brand("not json, but mentions operator"))
