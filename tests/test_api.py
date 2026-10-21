import gzip
import shutil
import tempfile
import unittest
from pathlib import Path

from hmaps.api import mvt, routing
from hmaps.core import geo
from tests import fake_osrm
from tests.helpers import ApiServer, free_port, sample_db


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = Path(tempfile.mkdtemp(prefix="hmaps-api-"))
        db = sample_db()
        shutil.copy(db, cls.data / "maps.db")
        shutil.copytree(db.parent / "reports", cls.data / "reports")
        cls.osrm_port = free_port()
        cls.osrm = fake_osrm.serve(cls.osrm_port)
        cls.api = ApiServer(cls.data, cls.osrm_port)

    @classmethod
    def tearDownClass(cls):
        cls.api.stop()
        cls.osrm.shutdown()

    def test_health_and_meta(self):
        s, body, h = self.api.get("/api/health")
        self.assertEqual(s, 200)
        self.assertTrue(body["dataset"])
        self.assertIn("x-request-id", {k.lower() for k in h})
        s, body, _ = self.api.get("/api/meta")
        self.assertEqual(body["basemap"]["type"], "fallback")
        self.assertTrue(body["routing"]["car"])

    def test_categories(self):
        s, body, _ = self.api.get("/api/categories")
        ids = [c["id"] for c in body["categories"]]
        self.assertIn("religion", ids)
        self.assertIn("food", ids)

    def test_autocomplete(self):
        s, body, _ = self.api.get("/api/search/autocomplete", q="ירו", lat=32.08, lon=34.78)
        self.assertEqual(body["suggestions"][0]["name"], "ירושלים")
        s, body, _ = self.api.get("/api/search/autocomplete", q="מסע")
        self.assertEqual(body["suggestions"][0]["type"], "category")

    def test_search_category_in_city(self):
        s, body, _ = self.api.get("/api/search", q="בתי כנסת בירושלים")
        self.assertEqual(body["interpretation"]["sub"], "synagogue")
        self.assertEqual(body["interpretation"]["near"], "ירושלים")
        self.assertTrue(all(r["sub_category"] == "synagogue" for r in body["results"]))
        self.assertEqual(body["results"][0]["name"], "בית הכנסת הגדול ירושלים")

    def test_search_post_spec(self):
        s, body = self.api.post("/api/search", {"query": "מסעדות", "location": {"lat": 32.08, "lon": 34.78},
                                                "filters": {"kosher": True}, "limit": 5})
        self.assertEqual(s, 200)
        self.assertTrue(body["results"])
        self.assertTrue(all(r["kosher"] for r in body["results"]))
        self.assertIn("query_time_ms", body["meta"])

    def test_typo_and_layout(self):
        s, body, _ = self.api.get("/api/search", q="מגדל דויד")
        self.assertEqual(body["results"][0]["name"], "מגדל דוד")
        self.assertIn("corrected", body["interpretation"])

    def test_poi_detail(self):
        s, body, _ = self.api.get("/api/search", q="מגדל דוד")
        pid = body["results"][0]["id"]
        s, poi, _ = self.api.get(f"/api/poi/{pid}")
        self.assertEqual(poi["name_en"], "Tower of David")
        self.assertEqual(poi["contact"]["phone"], "02-6265333")
        self.assertEqual(len(poi["opening_hours"]["rows"]), 7)
        self.assertEqual(poi["opening_hours"]["rows"][6]["day"], "שבת")
        s, _, _ = self.api.get("/api/poi/poi-424242")
        self.assertEqual(s, 404)

    def test_nearby_radius(self):
        s, body, _ = self.api.get("/api/poi", lat=32.0853, lon=34.7818, radius=1000)
        self.assertTrue(body["results"])
        for r in body["results"]:
            d = geo.haversine(32.0853, 34.7818, r["location"]["lat"], r["location"]["lon"])
            self.assertLessEqual(d, 1000)
        s, body, _ = self.api.get("/api/poi", lat=32.0853, lon=34.7818, category="nope")
        self.assertEqual(s, 400)

    def test_geocode_reverse(self):
        s, body, _ = self.api.get("/api/geocode", address="דיזנגוף 50", city="תל אביב")
        self.assertEqual(body["accuracy"], "exact")
        self.assertAlmostEqual(body["lat"], 32.0775, places=3)
        s, body, _ = self.api.get("/api/geocode", address="שד' רוטשילד 15, תל אביב")
        self.assertEqual(body["accuracy"], "interpolated")
        s, body, _ = self.api.get("/api/reverse", lat=32.0776, lon=34.7746)
        self.assertEqual(body["street"], "דיזנגוף")
        self.assertEqual(body["city"], "תל אביב-יפו")

    def test_route(self):
        s, body, _ = self.api.get("/api/route", **{"from": "32.0776,34.7746", "to": "32.0700,34.7800", "mode": "foot"})
        self.assertEqual(s, 200)
        steps = body["routes"][0]["steps"]
        self.assertTrue(steps[0]["instruction"].startswith("התחילו ללכת"))
        self.assertEqual(steps[1]["instruction"], "פנו שמאלה לשדרות רוטשילד")
        self.assertEqual(steps[2]["instruction"], "בכיכר צאו ביציאה השנייה לרחוב אלנבי")
        self.assertEqual(steps[-1]["instruction"], "הגעתם ליעד, היעד מימין")
        s, body, _ = self.api.get("/api/route", **{"from": "32.0776,34.7746", "to": "29,35"})
        self.assertEqual(s, 404)
        s, body, _ = self.api.get("/api/route", **{"from": "bad", "to": "29,35"})
        self.assertEqual(s, 400)

    def test_app_code_is_revalidated(self):
        """JS/CSS must not be cached heuristically, or code updates never reach the browser."""
        for path in ("/js/map.js", "/css/app.css", "/index.html", "/map.html", "/desktop.html", "/js/desktop.js", "/sw.js"):
            s, _, h = self.api.get(path)
            self.assertEqual(s, 200, path)
            self.assertEqual(h.get("cache-control"), "no-cache", path)

    def test_desktop_shell_headers(self):
        """The tab shell frames /map.html (same origin) and may reach the Tauri IPC channel; nothing else changes."""
        _, _, shell = self.api.get("/desktop.html")
        _, _, page = self.api.get("/map.html")
        self.assertIn("connect-src 'self' ipc: http://ipc.localhost;", shell["content-security-policy"])
        self.assertNotIn("ipc", page["content-security-policy"])
        for h in (shell, page):
            self.assertIn("frame-ancestors 'self'", h["content-security-policy"])
            self.assertEqual(h["x-frame-options"], "SAMEORIGIN")

    def test_tiles(self):
        x, y = geo.lonlat_to_tile(34.7752, 32.0753, 15)
        s, data, h = self.api.get(f"/tiles/poi/15/{x}/{y}.pbf")
        self.assertEqual(s, 200)
        if h.get("content-encoding") == "gzip":
            data = gzip.decompress(data)
        names = [f["props"].get("name") for f in mvt.decode(data)["pois"]]
        self.assertIn("דיזנגוף סנטר", names)
        s, data, _ = self.api.get(f"/tiles/fallback/15/{x}/{y}.pbf")
        self.assertEqual(s, 200)
        s, _, _ = self.api.get("/tiles/poi/3/99/0.pbf")
        self.assertEqual(s, 400)

    # ------------------------------------------------------------- outdoor
    def _tile(self, path):
        s, data, h = self.api.get(path)
        self.assertIn(s, (200, 204), path)
        if s == 204:
            return {}
        if h.get("content-encoding") == "gzip":
            data = gzip.decompress(data)
        return mvt.decode(data)

    def test_trail_search_and_card(self):
        s, body, _ = self.api.get("/api/search", q="שביל ישראל")
        top = body["results"][0]
        self.assertEqual((top["type"], top["name"]), ("trail", "שביל ישראל"))
        self.assertIn("ק\"מ", top["secondary"])
        s, body, _ = self.api.get("/api/search/autocomplete", q="שביל יש")
        self.assertIn("trail", [x["type"] for x in body["suggestions"]])
        s, trail, h = self.api.get(f"/api/trails/{top['id']}")
        self.assertEqual(s, 200)
        self.assertEqual((trail["mark"], trail["network"], trail["route"]), ("orange", "nwn", "hiking"))
        self.assertAlmostEqual(trail["length_km"], 6.3, delta=0.1)
        self.assertTrue(trail["continuous"])
        self.assertEqual(trail["geometry"]["type"], "MultiLineString")
        self.assertEqual(trail["metadata"]["osm_url"], "https://www.openstreetmap.org/relation/282071")
        self.assertIn("max-age", h.get("cache-control", ""))
        s, body, _ = self.api.get("/api/trails/trail-99999")
        self.assertEqual(s, 404)
        s, body, _ = self.api.get("/api/trails/abc")
        self.assertEqual(s, 404)

    def test_trail_and_park_tiles(self):
        x, y = geo.lonlat_to_tile(35.375, 31.43, 13)
        feats = self._tile(f"/tiles/trails/13/{x}/{y}.pbf").get("trails", [])
        marks = {f["props"]["mark"] for f in feats}
        self.assertIn("orange", marks)
        self.assertTrue(any(f["props"]["name"] == "שביל ישראל" for f in feats))
        # low zoom: only long-distance trails
        x, y = geo.lonlat_to_tile(35.375, 31.43, 8)
        low = self._tile(f"/tiles/trails/8/{x}/{y}.pbf").get("trails", [])
        self.assertTrue(low and all(f["props"]["network"] == "nwn" for f in low))
        s, _, _ = self.api.get(f"/tiles/trails/5/{x // 8}/{y // 8}.pbf")
        self.assertEqual(s, 204)
        x, y = geo.lonlat_to_tile(35.352, 31.312, 12)
        parks = self._tile(f"/tiles/parks/12/{x}/{y}.pbf")["parks"]
        masada = next(f for f in parks if f["props"]["name"] == "גן לאומי מצדה")
        self.assertEqual(masada["type"], 3)
        self.assertTrue(masada["props"]["inpa"])

    def test_outdoor_poi_cards(self):
        s, body, _ = self.api.get("/api/search", q="נחל דוד", lat=31.46, lon=35.38)
        stream = body["results"][0]
        self.assertEqual(stream["sub_category"], "stream")
        s, poi, _ = self.api.get(f"/api/poi/{stream['id']}")
        self.assertEqual(poi["geometry"]["type"], "MultiLineString")
        self.assertGreater(poi["length_m"], 2000)
        s, body, _ = self.api.get("/api/search", q="גן לאומי מצדה")
        s, site, _ = self.api.get(f"/api/poi/{body['results'][0]['id']}")
        self.assertEqual(site["sub_category"], "inpa_site")
        self.assertTrue(site["nature_site"]["inpa"])
        self.assertEqual(site["operator_logo"], "/logos/inpa.svg")
        self.assertEqual(site["operator_brand"]["key"], "inpa")
        # the logo is served by our own server, cached like the other static assets
        s, svg, h = self.api.get(site["operator_logo"])
        self.assertEqual(s, 200)
        self.assertIn(b"<svg", svg[:4096])
        self.assertIn("max-age", h.get("cache-control", ""))
        self.assertEqual(site["nature_site"]["fee_he"], "כניסה בתשלום")
        self.assertEqual(site["geometry"]["type"], "MultiPolygon")
        self.assertIsNotNone(site["opening_hours"])
        # "שמורות טבע" (category) also lists the INPA sites; "אתרי רט"ג" only them
        s, body, _ = self.api.get("/api/search", q="שמורות טבע", lat=31.4, lon=35.35)
        subs = {r["sub_category"] for r in body["results"]}
        self.assertEqual(subs, {"nature_reserve", "inpa_site"})
        s, body, _ = self.api.get("/api/search", q='אתרי רט"ג', lat=31.4, lon=35.35)
        self.assertEqual({r["sub_category"] for r in body["results"]}, {"inpa_site"})
        s, body, _ = self.api.get("/api/search", q="ברזיות מים", lat=31.46, lon=35.38)
        self.assertEqual(body["results"][0]["sub_category"], "drinking_water")

    def test_fonts_and_security(self):
        s, _, _ = self.api.get("/fonts/..%2F..%2Fetc/0-255.pbf")
        self.assertIn(s, (400, 404))
        for path in ("/", "/index.html"):  # the local server goes straight to the map (no landing page)
            s, body, h = self.api.get(path)
            self.assertEqual(s, 200)
            self.assertIn("default-src 'self'", h.get("content-security-policy", ""))
            self.assertIn(b'id="map"', body)
            self.assertNotIn(b"landing.css", body)
            self.assertEqual(h.get("cache-control"), "no-cache")

        s, body, h = self.api.get("/map.html")
        self.assertEqual(s, 200)
        self.assertIn(b'id="map"', body)
        self.assertEqual(h.get("cache-control"), "no-cache")

    def test_reports(self):
        s, body, _ = self.api.get("/reports/quality-report.html")
        self.assertEqual(s, 200)
        s, body, _ = self.api.get("/reports/..%2Fmaps.db")
        self.assertEqual(s, 404)

    def test_metrics(self):
        self.api.get("/api/health")
        s, body, _ = self.api.get("/metrics")
        self.assertIn(b"hmaps_requests_total", body)


class BasemapTest(unittest.TestCase):
    def test_mbtiles_basemap(self):
        from tests.helpers import make_mbtiles
        data = Path(tempfile.mkdtemp(prefix="hmaps-base-"))
        shutil.copy(sample_db(), data / "maps.db")
        make_mbtiles(data / "tiles" / "israel.mbtiles")
        api = ApiServer(data)
        try:
            s, meta, _ = api.get("/api/meta")
            self.assertEqual(meta["basemap"]["type"], "openmaptiles")
            s, tj, _ = api.get("/tiles/base.json")
            self.assertEqual(tj["tilejson"], "3.0.0")
            self.assertIn("/tiles/base/{z}/{x}/{y}.pbf", tj["tiles"][0])
            self.assertEqual([l["id"] for l in tj["vector_layers"]], ["water", "place"])
            s, body, h = api.get("/tiles/base/10/610/418.pbf")
            self.assertEqual(s, 200)
            self.assertEqual(h.get("content-encoding"), "gzip")
            s, body, h = api.get("/tiles/base/10/0/0.pbf")
            self.assertEqual(s, 204)
        finally:
            api.stop()


class RoutingInstructionTest(unittest.TestCase):
    def test_instructions(self):
        I = routing.instruction
        self.assertEqual(I({"maneuver": {"type": "turn", "modifier": "right"}, "name": "הרצל"}), "פנו ימינה לרחוב הרצל")
        self.assertEqual(I({"maneuver": {"type": "turn", "modifier": "slight left"}, "name": "דרך בגין"}), "פנו קלות שמאלה לדרך בגין")
        self.assertEqual(I({"maneuver": {"type": "on ramp", "modifier": "right"}, "name": "", "ref": "1"}), "עלו לכביש 1")
        self.assertEqual(I({"maneuver": {"type": "off ramp", "modifier": "right"}, "destinations": "חיפה, נתניה"}),
                         "צאו מהכביש ימינה לכיוון חיפה")
        self.assertEqual(I({"maneuver": {"type": "fork", "modifier": "slight left"}, "name": "", "ref": "6"}), "בפיצול הישארו שמאלה לכביש 6")
        self.assertEqual(I({"maneuver": {"type": "turn", "modifier": "uturn"}}), "בצעו פניית פרסה")
        self.assertEqual(I({"maneuver": {"type": "arrive"}}), "הגעתם ליעד")


class DemoModeTest(unittest.TestCase):
    def test_sample_fallback_and_missing(self):
        empty = Path(tempfile.mkdtemp(prefix="hmaps-empty-"))
        api = ApiServer(empty)
        try:
            s, body, _ = api.get("/api/health")
            self.assertEqual(s, 503)
            s, body, _ = api.get("/api/search", q="x")
            self.assertEqual(s, 503)
            self.assertEqual(body["error"]["code"], "data_not_ready")
        finally:
            api.stop()
        api = ApiServer(empty, extra_env={"HMAPS_SAMPLE_DB": str(sample_db())})
        try:
            s, body, _ = api.get("/api/meta")
            self.assertTrue(body["dataset"]["demo"])
        finally:
            api.stop()


if __name__ == "__main__":
    unittest.main()
