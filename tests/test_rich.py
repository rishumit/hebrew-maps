"""Rich content enrichment (hmaps.etl.rich) with canned Wikimedia / Wikidata answers – no network."""

from __future__ import annotations

import io
import json
import shutil
import sqlite3
import tempfile
import unittest
import urllib.parse
from pathlib import Path

from hmaps.etl import quality, rich
from tests.helpers import sample_db

EIN_GEDI = (31.4625, 35.3875)
LA, LO = EIN_GEDI
#: geotagged Commons files: (name, lat, lon[, imageinfo overrides])
GEO = [
    ("עין גדי נחל דוד 2019.jpg", LA + 0.004, LO),          # named, ~450 m → kept
    ("Desert view.jpg", LA + 0.0003, LO + 0.0002),          # right at the site → kept
    ("Random road sign.jpg", LA + 0.009, LO + 0.009),       # far, unnamed → dropped
    ("Ein Gedi church ruins.jpg", LA, LO),                  # house of worship → dropped
    ("Ein Gedi map.png", LA, LO),                           # not a photo → dropped
    ("Tiny.jpg", LA + 0.0001, LO, {"w": 120, "h": 90}),     # too small → dropped
    # along שביל ישראל (35.36,31.40 → 35.38,31.44 → 35.385,31.452)
    ("Trail 1.jpg", 31.4005, 35.3602), ("Trail 2.jpg", 31.4105, 35.3651), ("Trail 3.jpg", 31.4205, 35.3702),
    ("Trail 4.jpg", 31.4305, 35.3751), ("Trail 5.jpg", 31.4405, 35.3802),
]


def _img(title, lat, lon, mime="image/jpeg", w=1200, h=800, desc="", author="<a href='x'>Avi Hiker</a>"):
    return {"title": f"File:{title}", "coordinates": [{"lat": lat, "lon": lon}] if lat is not None else [],
            "imageinfo": [{"mime": mime, "width": w, "height": h,
                           "descriptionurl": f"https://commons.wikimedia.org/wiki/File:{title.replace(' ', '_')}",
                           "extmetadata": {"Artist": {"value": author}, "LicenseShortName": {"value": "CC BY-SA 4.0"},
                                           "LicenseUrl": {"value": "https://creativecommons.org/licenses/by-sa/4.0"},
                                           "ImageDescription": {"value": desc}}}]}


class FakeWeb:
    """Answers by API + parameters; records every call."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, req, timeout):
        url = req.full_url
        base, _, q = url.partition("?")
        params = dict(urllib.parse.parse_qsl(req.data.decode() if req.data else q))
        self.calls.append((base, params))
        return json.dumps(self.answer(base, params), ensure_ascii=False).encode()

    def answer(self, base, p):
        if "query.wikidata.org" in base:
            return {"results": {"bindings": [
                {"item": {"value": "http://www.wikidata.org/entity/Q1"}, "p": {"value": "http://www.wikidata.org/prop/direct/P571"},
                 "v": {"value": "1971-01-01T00:00:00Z"}, "vLabel": {"value": "1971-01-01T00:00:00Z"},
                 "voy": {"value": "https://he.wikivoyage.org/wiki/%D7%A2%D7%99%D7%9F_%D7%92%D7%93%D7%99"}},
                {"item": {"value": "http://www.wikidata.org/entity/Q1"}, "p": {"value": "http://www.wikidata.org/prop/direct/P1435"},
                 "v": {"value": "http://www.wikidata.org/entity/Q9"}, "vLabel": {"value": "שמורת טבע"}},
                {"item": {"value": "http://www.wikidata.org/entity/Q1"}, "p": {"value": "http://www.wikidata.org/prop/direct/P373"},
                 "v": {"value": "Ein Gedi"}, "vLabel": {"value": "Ein Gedi"}},
            ]}}
        if "commons.wikimedia.org" in base:
            if p.get("generator") == "categorymembers":
                return {"query": {"pages": [_img("Ein Gedi waterfall.jpg", None, None)]}}
            if p.get("list") == "geosearch":
                top, left, bottom, right = map(float, p["gsbbox"].split("|"))
                return {"query": {"geosearch": [{"title": f"File:{t}", "lat": la, "lon": lo} for t, la, lo, *_ in GEO
                                                if bottom <= la <= top and left <= lo <= right]}}
            if p.get("titles"):
                pages = []
                for t in p["titles"].split("|"):
                    name = t.split(":", 1)[1]
                    meta = {g[0]: g for g in GEO}.get(name)
                    if meta and len(meta) > 3:
                        pages.append(_img(name, None, None, **meta[3]))
                    else:
                        pages.append(_img(name, None, None))
                return {"query": {"pages": pages}}
        if "he.wikipedia.org" in base:
            if p.get("generator") == "geosearch":
                return {"query": {"pages": [
                    {"title": "עין גדי (שמורת טבע)", "coordinates": [{"lat": LA, "lon": LO}],
                     "pageprops": {"wikibase_item": "Q1"}},
                    {"title": "ים המלח", "coordinates": [{"lat": LA + 0.001, "lon": LO}],
                     "pageprops": {}},
                ]}}
            if p.get("list") == "search":
                return {"query": {"search": [{"title": "שביל ישראל"}] if "שביל ישראל" in p["srsearch"] else []}}
            if p.get("prop", "").startswith("extracts"):
                pages = []
                for t in p["titles"].split("|"):
                    pages.append({"title": t, "extract": f"{t} הוא מקום יפה.\nפסקה שנייה על {t}.\n== היסטוריה ==\nלא בהקדמה",
                                  "pageimage": "Ein_Gedi_lead.jpg" if "עין גדי" in t else None,
                                  "pageprops": {"wikibase_item": "Q1"} if "עין גדי" in t else {}})
                return {"query": {"pages": pages}}
        if "he.wikivoyage.org" in base:
            if p.get("list") == "geosearch":
                return {"query": {"geosearch": []}}
            if p.get("prop", "").startswith("extracts"):
                return {"query": {"pages": [{"title": t, "extract": "מדריך טיולים: כדאי להגיע מוקדם."} for t in p["titles"].split("|")]}}
        return {}


class RichEtlTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = Path(tempfile.mkdtemp(prefix="hmaps-rich-etl-"))
        shutil.copy(sample_db(), cls.dir / "maps.db")
        cls.con = sqlite3.connect(cls.dir / "maps.db")
        cls.web = FakeWeb()
        http = rich.Http(cls.dir / "cache" / "http.sqlite", opener=cls.web, interval=0)
        cls.stats = rich.run(cls.con, cls.dir, workers=2, http=http)
        cls.calls_first = len(cls.web.calls)
        # second run: everything from the cache
        http2 = rich.Http(cls.dir / "cache" / "http.sqlite", opener=cls.web, interval=0)
        rich.run(cls.con, cls.dir, workers=2, http=http2)
        cls.http2 = http2
        cls.site = cls.con.execute("SELECT id FROM pois WHERE name = 'שמורת עין גדי'").fetchone()[0]
        cls.trail = cls.con.execute("SELECT id FROM trails WHERE name = 'שביל ישראל'").fetchone()[0]

    def test_helpers(self):
        self.assertEqual(rich.name_match("שמורת עין גדי", "עין גדי (שמורת טבע)"), 1.0)
        self.assertLess(rich.name_match("שמורת עין גדי", "ים המלח"), 0.5)
        self.assertTrue(rich.title_mentions("Ein_Gedi_waterfall.jpg", ["שמורת עין גדי", "Ein Gedi Nature Reserve"]))
        self.assertFalse(rich.title_mentions("Desert view.jpg", ["שמורת עין גדי", "Ein Gedi"]))
        pts = rich.sample_points([[(35.0, 31.0), (35.0, 31.1)]], 2000, 10)
        self.assertEqual(len(pts), 6)  # ~11 km → every 2 km, capped evenly

    def test_wikipedia_match_and_texts(self):
        wp, wd, desc = self.con.execute("SELECT wikipedia, wikidata, description FROM pois WHERE id = ?", (self.site,)).fetchone()
        self.assertEqual(wp, "he:עין גדי (שמורת טבע)")
        self.assertEqual(wd, "Q1")
        self.assertTrue(desc)
        title, text = self.con.execute("SELECT title, text FROM texts WHERE entity='poi' AND ref_id=? AND source='wikipedia'",
                                       (self.site,)).fetchone()
        self.assertEqual(title, "עין גדי (שמורת טבע)")
        self.assertIn("פסקה שנייה", text)
        self.assertNotIn("היסטוריה", text)  # only the introduction
        # a far article (ים המלח) is not matched to anything named differently
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM pois WHERE wikipedia = 'he:ים המלח'").fetchone()[0], 0)

    def test_wikipedia_geosearch_batches_nearby_sites_and_filters_by_distance(self):
        con = sqlite3.connect(":memory:")
        con.execute("CREATE TABLE pois (id INTEGER PRIMARY KEY, wikipedia TEXT, wikidata TEXT)")
        sites = [
            {"id": 1, "name": "שמורת עין גדי", "lat": LA, "lon": LO, "radius": 200,
             "wikipedia": None, "wikidata": None},
            {"id": 2, "name": "שמורת עין גדי", "lat": LA + 0.001, "lon": LO, "radius": 200,
             "wikipedia": None, "wikidata": None},
            {"id": 3, "name": "שמורת עין גדי", "lat": LA + 0.2, "lon": LO, "radius": 200,
             "wikipedia": None, "wikidata": None},
        ]
        for site in sites:
            con.execute("INSERT INTO pois (id) VALUES (?)", (site["id"],))
        web = FakeWeb()
        http = rich.Http(None, opener=web, interval=0)

        self.assertEqual(rich.step_wikipedia_match(con, http, sites, workers=2), 2)
        wiki_calls = [(base, params) for base, params in web.calls if "he.wikipedia.org" in base]
        self.assertEqual(len(wiki_calls), 2)
        self.assertTrue(all(params["ggslimit"] == "500" for _, params in wiki_calls))
        self.assertEqual(
            con.execute("SELECT wikipedia FROM pois WHERE id IN (1, 2) ORDER BY id").fetchall(),
            [("he:עין גדי (שמורת טבע)",), ("he:עין גדי (שמורת טבע)",)],
        )
        self.assertIsNone(con.execute("SELECT wikipedia FROM pois WHERE id = 3").fetchone()[0])
        con.close()

    def test_facts_and_wikivoyage(self):
        facts = dict(self.con.execute("SELECT key, value FROM facts WHERE entity='poi' AND ref_id=?", (self.site,)))
        self.assertEqual(facts["inception"], "1971")
        self.assertEqual(facts["heritage"], "שמורת טבע")
        self.assertEqual(facts["commons_category"], "Ein Gedi")
        self.assertEqual(facts["wikivoyage"], "עין גדי")
        voy = self.con.execute("SELECT text FROM texts WHERE entity='poi' AND ref_id=? AND source='wikivoyage'", (self.site,)).fetchone()
        self.assertIn("מדריך", voy[0])

    def test_gallery_selection(self):
        rows = self.con.execute("SELECT file, author, license FROM media WHERE entity='poi' AND ref_id=? ORDER BY rank",
                                (self.site,)).fetchall()
        files = [r[0] for r in rows]
        self.assertIn("Ein_Gedi_lead.jpg", files)           # Wikipedia lead image
        self.assertIn("Ein_Gedi_waterfall.jpg", files)      # Commons category
        self.assertIn("עין_גדי_נחל_דוד_2019.jpg", files)    # named, 450 m away
        self.assertIn("Desert_view.jpg", files)             # right at the site
        for bad in ("Random_road_sign.jpg", "Ein_Gedi_church_ruins.jpg", "Ein_Gedi_map.png", "Tiny.jpg"):
            self.assertNotIn(bad, files)
        self.assertLess(files.index("Ein_Gedi_lead.jpg"), files.index("Desert_view.jpg"))
        self.assertEqual(rows[0][1], "Avi Hiker")           # HTML stripped from the credit
        self.assertEqual(rows[0][2], "CC BY-SA 4.0")
        img = self.con.execute("SELECT image FROM pois WHERE id = ?", (self.site,)).fetchone()[0]
        self.assertTrue(img)

    def test_trail(self):
        n = self.con.execute("SELECT COUNT(*) FROM media WHERE entity='trail' AND ref_id=?", (self.trail,)).fetchone()[0]
        self.assertGreaterEqual(n, 3)
        extra = json.loads(self.con.execute("SELECT extra FROM trails WHERE id=?", (self.trail,)).fetchone()[0])
        self.assertEqual(extra["wikipedia"], "he:שביל ישראל")
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM texts WHERE entity='trail' AND ref_id=?", (self.trail,)).fetchone()[0], 1)

    def test_cache_and_report(self):
        # second run: answered from the cache (a batch can differ once new article links were found)
        self.assertLessEqual(self.http2.requests, 6)
        self.assertLess(self.http2.requests * 4, self.calls_first)
        self.assertGreater(self.http2.hits, 0)
        names = {k["name"]: k for k in quality.compute(self.con)["kpis"]}
        self.assertIn("אתרי טיול עם גלריית תמונות", names)
        self.assertGreater(names["אתרי טיול עם גלריית תמונות"]["value"], 0)
        meta = dict(self.con.execute("SELECT key, value FROM meta"))
        self.assertIn("site_galleries", json.loads(meta["rich_content"]))

    def test_mapillary_skipped_without_token(self):
        self.assertNotIn("mapillary", self.stats)
        self.assertFalse(any("mapillary" in b for b, _ in self.web.calls))


class FilterAndRateLimitTest(unittest.TestCase):
    """NetFree blocks he.wikipedia.org (HTTP 418 page); Commons answers 429 once."""

    def test_blocked_host_skipped_and_429_retried(self):
        import email.message
        import urllib.error

        web = FakeWeb()
        state = {"429": 0, "wiki": 0}

        def opener(req, timeout):
            host = urllib.parse.urlsplit(req.full_url).hostname
            if host == "he.wikipedia.org":
                state["wiki"] += 1
                raise urllib.error.HTTPError(req.full_url, 418, "I'm a teapot", email.message.Message(),
                                             io.BytesIO(b'<html><link href="//netfree.link/img/block-favicon.png"></html>'))
            if host == "commons.wikimedia.org" and state["429"] == 0:
                state["429"] += 1
                h = email.message.Message()
                h["Retry-After"] = "1"
                raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", h, io.BytesIO(b"slow down"))
            return web(req, timeout)

        d = Path(tempfile.mkdtemp(prefix="hmaps-rich-block-"))
        shutil.copy(sample_db(), d / "maps.db")
        con = sqlite3.connect(d / "maps.db")
        http = rich.Http(None, opener=opener, interval=0)
        with self.assertLogs("hmaps.etl.rich", level="WARNING") as logs:
            stats = rich.run(con, d, steps=("wikipedia", "commons"), workers=2, http=http)
        self.assertEqual(state["wiki"], 1)  # one refusal, then the host is skipped
        self.assertIn("he.wikipedia.org", http.blocked)
        self.assertTrue(any("content filter" in m for m in logs.output))
        self.assertEqual(state["429"], 1)
        self.assertGreater(stats["site_galleries"], 0)  # Commons still worked after slowing down


class EnrichCommandTest(unittest.TestCase):
    def test_enrich_publishes_a_new_version(self):
        import os
        import subprocess
        import sys

        from tests.helpers import ROOT
        d = Path(tempfile.mkdtemp(prefix="hmaps-rich-cmd-"))
        shutil.copy(sample_db(), d / "maps-20260101-000000.db")
        (d / "maps.current").write_text("maps-20260101-000000.db", encoding="utf-8")
        env = dict(os.environ, HMAPS_DATA=str(d), PYTHONPATH=str(ROOT), HMAPS_RICH_OFFLINE="1")
        r = subprocess.run([sys.executable, "-m", "hmaps.etl", "enrich", "--db", str(d / "maps.db"), "--limit", "3",
                            "--steps", "wikipedia,commons"], env=env, cwd=ROOT, capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        live = (d / "maps.current").read_text(encoding="utf-8").strip()
        self.assertNotEqual(live, "maps-20260101-000000.db")
        con = sqlite3.connect(d / live)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM media").fetchone()[0], 0)  # offline: nothing found, tables exist
        self.assertTrue((d / "reports" / "quality-report.json").exists())


if __name__ == "__main__":
    unittest.main()
