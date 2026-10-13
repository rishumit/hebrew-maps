import json
import sqlite3
import unittest

from tests.helpers import sample_db


class EtlTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = sample_db()
        cls.con = sqlite3.connect(cls.path)
        cls.con.row_factory = sqlite3.Row

    def q(self, sql, *args):
        return self.con.execute(sql, args).fetchall()

    def test_excluded_categories_not_imported(self):
        names = {r["name"] for r in self.q("SELECT name FROM pois")}
        self.assertNotIn("מועדון", names)  # nightclub
        self.assertNotIn("כנסייה", names)  # non-jewish place of worship

    def test_duplicates_merged(self):
        rows = self.q("SELECT * FROM pois WHERE name = 'דיזנגוף סנטר'")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["phone"], "03-6212400")  # from the richer node
        self.assertIn("bbox", json.loads(rows[0]["extra"]))  # geometry from the building

    def test_city_assignment(self):
        r = self.q("SELECT city FROM pois WHERE name = 'הכותל המערבי'")[0]
        self.assertEqual(r["city"], "ירושלים")
        r = self.q("SELECT city FROM pois WHERE name = 'שמורת עין גדי'")[0]
        self.assertEqual(r["city"], "עין גדי")  # nearest settlement fallback
        streets = {(r["name"], r["city"]) for r in self.q("SELECT name, city FROM streets")}
        self.assertIn(("ז'בוטינסקי", "רמת גן"), streets)
        self.assertIn(("ז'בוטינסקי", "בני ברק"), streets)  # same name, two cities -> two streets

    def test_street_segments_merged(self):
        rows = self.q("SELECT s.id, COUNT(g.id) n FROM streets s JOIN street_segments g ON g.street_id = s.id "
                      "WHERE s.name = 'דיזנגוף' GROUP BY s.id")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["n"], 2)

    def test_place_node_and_area_merged(self):
        self.assertEqual(len(self.q("SELECT * FROM places WHERE name = 'עין גדי'")), 1)

    def test_neighbourhood_parent(self):
        self.assertEqual(self.q("SELECT parent FROM places WHERE name = 'פלורנטין'")[0]["parent"], "תל אביב-יפו")

    def test_place_bbox_from_boundary(self):
        r = self.q("SELECT * FROM places WHERE name = 'תל אביב-יפו'")[0]
        self.assertAlmostEqual(r["min_lon"], 34.739, places=3)

    def test_formats(self):
        r = self.q("SELECT * FROM pois WHERE name = 'מוזיאון תל אביב לאמנות'")[0]
        self.assertEqual(r["website"], "https://www.tamuseum.org.il")
        self.assertIsNotNone(r["hours_json"])
        self.assertEqual(r["wheelchair"], "yes")
        self.assertGreater(r["importance"], 0.9)

    def test_addresses_include_poi_addresses(self):
        rows = self.q("SELECT * FROM addresses WHERE street = 'דרך מנחם בגין' AND housenumber = '132'")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["city"], "תל אביב-יפו")

    def test_search_index(self):
        n = self.q("SELECT COUNT(*) c FROM search_items")[0]["c"]
        self.assertGreater(n, 50)
        hits = self.q("SELECT rowid FROM search_fts WHERE search_fts MATCH '\"כותל\"'")
        self.assertEqual(len(hits), 1)  # article-stripped variant of "הכותל"

    # ------------------------------------------------------------- outdoor
    def test_trails(self):
        rows = {r["osm"]: r for r in self.q("SELECT * FROM trails")}
        self.assertEqual(set(rows), {"r282071", "r900001", "r900002"})  # bus route and proposed trail skipped
        it = rows["r282071"]
        self.assertEqual((it["name"], it["network"], it["mark"], it["route"]), ("שביל ישראל", "nwn", "orange", "hiking"))
        self.assertAlmostEqual(it["length_m"], 6252, delta=20)
        self.assertEqual(it["parts"], 1)  # reversed member way still joins up
        blue = rows["r900001"]
        self.assertIsNone(blue["name"])
        self.assertEqual((blue["mark"], blue["parts"]), ("blue", 2))
        self.assertEqual(rows["r900002"]["mark"], "red")  # colour=#e11e26
        n_segs = self.q("SELECT COUNT(*) c FROM trail_segments WHERE trail_id = ?", it["id"])[0]["c"]
        self.assertGreaterEqual(n_segs, 1)
        self.assertEqual(self.q("SELECT COUNT(*) c FROM trail_segments_rtree")[0]["c"],
                         self.q("SELECT COUNT(*) c FROM trail_segments")[0]["c"])
        # only named trails are searchable
        names = [r["name"] for r in self.q("SELECT name FROM search_items WHERE kind = 'trail'")]
        self.assertEqual(sorted(names), sorted(["שביל ישראל", "סינגל הכרמל לדוגמה"]))

    def test_streams_merged_per_name(self):
        rows = self.q("SELECT * FROM pois WHERE sub = 'stream' ORDER BY lon DESC")
        self.assertEqual([r["name"] for r in rows], ["נחל דוד", "נחל דוד"])  # touching segments merged, far namesake kept
        ein_gedi = rows[0]
        extra = json.loads(ein_gedi["extra"])
        self.assertGreater(extra["length_m"], 2000)
        shape = json.loads(self.q("SELECT geom FROM poi_shapes WHERE poi_id = ?", ein_gedi["id"])[0]["geom"])
        self.assertEqual(shape["type"], "MultiLineString")
        self.assertEqual(len(shape["coordinates"]), 2)
        self.assertEqual(len(self.q("SELECT * FROM pois WHERE sub = 'ridge'")), 1)

    def test_inpa_and_reserves(self):
        rows = {r["name"]: r for r in self.q("SELECT * FROM pois WHERE sub IN ('inpa_site', 'nature_reserve')")}
        self.assertEqual(rows["גן לאומי מצדה"]["sub"], "inpa_site")
        self.assertEqual(json.loads(rows["גן לאומי מצדה"]["extra"])["inpa"], "operator")
        self.assertEqual(rows["גן לאומי מצדה"]["wheelchair"], "limited")
        self.assertEqual(rows["גן לאומי מצדה"]["phone"], "08-6584207")
        self.assertEqual(json.loads(rows["גן לאומי עין אפק"]["extra"])["inpa"], "name")
        self.assertEqual(rows["שמורת טבע לדוגמה"]["sub"], "nature_reserve")  # operated by KKL
        self.assertEqual(rows["שמורת עין גדי"]["sub"], "nature_reserve")
        split = self.q("SELECT * FROM pois WHERE name = 'שמורת טבע מצוק לדוגמה'")
        self.assertEqual(len(split), 1)  # two polygons of one reserve -> one site
        g = json.loads(self.q("SELECT geom FROM poi_shapes WHERE poi_id = ?", split[0]["id"])[0]["geom"])
        self.assertEqual(len(g["coordinates"]), 2)
        self.assertEqual(json.loads(split[0]["extra"])["bbox"], [34.7, 30.9, 34.72, 30.91])
        for r in rows.values():  # every reserve keeps its polygon for the parks layer
            g = json.loads(self.q("SELECT geom FROM poi_shapes WHERE poi_id = ?", r["id"])[0]["geom"])
            self.assertEqual(g["type"], "MultiPolygon")

    def test_new_poi_types(self):
        subs = {r["sub"] for r in self.q("SELECT sub FROM pois")}
        for s in ("drinking_water", "toilets", "shelter", "trail_info", "water_tower", "well", "memorial", "office",
                  "craft", "saddle"):
            self.assertIn(s, subs)
        names = {r["name"] for r in self.q("SELECT name FROM pois WHERE name IS NOT NULL")}
        self.assertNotIn("יקב לדוגמה", names)
        self.assertEqual(len(self.q("SELECT * FROM pois WHERE sub = 'shelter'")), 1)  # bus shelter skipped
        self.assertEqual(len(self.q("SELECT * FROM pois WHERE sub = 'trail_info'")), 1)  # unnamed guidepost skipped
        self.assertEqual(self.q("SELECT kind_he FROM pois WHERE sub = 'office'")[0]["kind_he"], "עורך דין")

    def test_quality_report(self):
        rep = json.loads((self.path.parent / "reports" / "quality-report.json").read_text(encoding="utf-8"))
        self.assertEqual(rep["validation"]["POI מחוץ לגבולות ישראל"], 0)
        self.assertGreater(rep["totals"]["pois"], 30)
        self.assertEqual(rep["totals"]["trails"], 3)
        kpi = {k["name"]: k for k in rep["kpis"]}
        self.assertEqual(kpi["אורך שביל ישראל"]["value"], 6.3)  # the sample has a 6 km stretch only
        self.assertEqual(kpi["אתרי רשות הטבע והגנים"]["value"], 3)
        self.assertEqual(rep["validation"]["שבילים מסומנים שאינם רציפים"], 1)
        self.assertTrue((self.path.parent / "reports" / "quality-report.html").exists())

    def test_quality_media_kpis_use_notable_places(self):
        from hmaps.core import categories
        from hmaps.etl import quality

        con = sqlite3.connect(self.path)
        try:
            rep = quality.compute(con)
            subs = sorted(categories.NOTABLE_SUBS)
            marks = ",".join("?" * len(subs))
            n, ni = con.execute(f"SELECT COUNT(*), COUNT(image) FROM pois WHERE name IS NOT NULL AND sub IN ({marks})",
                                subs).fetchone()
        finally:
            con.close()
        kpi = {k["name"]: k for k in rep["kpis"]}
        img = kpi["תמונה – אתרים ונקודות ציון"]
        self.assertEqual(img["target"], quality.NOTABLE_IMAGE_TARGET)
        self.assertEqual(img["value"], round(100.0 * ni / n, 1) if n else 0.0)
        self.assertIn(f"מתוך {n:,}", img["note"])
        self.assertIsNone(kpi["POI עם תמונה (כלל הנקודות)"]["target"])  # informational only
        self.assertTrue(kpi["POI עם תמונה (כלל הנקודות)"]["ok"])
        self.assertNotIn("תחנות אוטובוס", rep["media_coverage"])
        self.assertEqual(sum(v["pois"] for v in rep["media_coverage"].values()), n)
        html = (self.path.parent / "reports" / "quality-report.html").read_text(encoding="utf-8")
        self.assertIn("כיסוי תמונות ותיאורים", html)


if __name__ == "__main__":
    unittest.main()
