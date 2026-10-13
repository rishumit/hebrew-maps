"""The Docker-free path: pyosmium extraction, versioned publishing, built-in routing."""

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

from tests import fake_osmium
from tests.helpers import ApiServer


class NativePipelineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tools.sample_data import generate

        cls.data = Path(tempfile.mkdtemp(prefix="hmaps-native-"))
        poi, streets = generate(cls.data / "src")
        cls.pbf = fake_osmium.write_fake_pbf(poi, streets, cls.data / "sources" / "fake.osm.json")
        sys.modules["osmium"] = fake_osmium
        os.environ["HMAPS_DATA"] = str(cls.data)
        import importlib

        import hmaps.etl.__main__ as etl_main
        importlib.reload(etl_main)  # pick up HMAPS_DATA
        cls.etl = etl_main
        for _ in range(2):  # build twice: the second build must replace the first while it is "in use"
            etl_main.main(["build", "--pbf", str(cls.pbf), "--engine", "pyosmium", "--no-enrich",
                           "--out", str(cls.data / "maps.db")])
        del sys.modules["osmium"]

    def test_versioned_publish(self):
        from hmaps.core import db

        ptr = (self.data / "maps.current").read_text(encoding="utf-8").strip()
        self.assertTrue(ptr.startswith("maps-"))
        live = db.resolve(self.data / "maps.db")
        self.assertEqual(live.name, ptr)
        con = sqlite3.connect(live)
        names = {r[0] for r in con.execute("SELECT name FROM pois")}
        self.assertIn("דיזנגוף סנטר", names)
        self.assertNotIn("מועדון", names)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM pois WHERE name = 'דיזנגוף סנטר'").fetchone()[0], 1)
        self.assertGreater(con.execute("SELECT COUNT(*) FROM streets").fetchone()[0], 10)
        self.assertEqual(con.execute("SELECT value FROM meta WHERE key = 'engine'").fetchone()[0], "pyosmium")
        self.assertTrue((self.data / "routing" / "graph.npz").exists())

    def test_outdoor_parity_with_osmium_engine(self):
        """pyosmium (two passes) and the osmium-tool path (lines + OPL relations) give the same outdoor data."""
        from hmaps.core import db
        from tests.helpers import sample_db

        native = sqlite3.connect(db.resolve(self.data / "maps.db"))
        ref = sqlite3.connect(sample_db())
        q_trails = "SELECT osm, name, mark, network, ROUND(length_m), parts FROM trails ORDER BY osm"
        self.assertEqual(native.execute(q_trails).fetchall(), ref.execute(q_trails).fetchall())
        self.assertEqual(len(native.execute(q_trails).fetchall()), 3)
        q_pois = ("SELECT sub, name, ROUND(lat, 4), ROUND(lon, 4) FROM pois WHERE sub IN "
                  "('stream','ridge','inpa_site','nature_reserve','drinking_water','trail_info','office','craft') ORDER BY 1, 2, 3")
        self.assertEqual(native.execute(q_pois).fetchall(), ref.execute(q_pois).fetchall())
        self.assertEqual(native.execute("SELECT COUNT(*) FROM poi_shapes").fetchone(),
                         ref.execute("SELECT COUNT(*) FROM poi_shapes").fetchone())

    def test_api_with_local_router(self):
        api = ApiServer(self.data, extra_env={"HMAPS_ROUTER": "local"})
        try:
            s, meta, _ = api.get("/api/meta")
            self.assertFalse(meta["dataset"]["demo"])
            self.assertTrue(all(meta["routing"].values()))
            # along Dizengoff (two OSM ways joined at a shared node)
            s, body, _ = api.get("/api/route", **{"from": "32.0900,34.77435", "to": "32.0760,34.7752", "mode": "car"})
            self.assertEqual(s, 200, body)
            r = body["routes"][0]
            self.assertGreater(r["distance"], 1000)
            self.assertTrue(r["steps"][0]["instruction"].startswith("התחילו לנסוע"))
            self.assertIn("דיזנגוף", r["steps"][0]["instruction"])
            self.assertEqual(r["steps"][-1]["maneuver"]["type"], "arrive")
            s, body, _ = api.get("/api/route", **{"from": "32.0900,34.77435", "to": "29.55,34.95", "mode": "foot"})
            self.assertEqual(s, 404)
            s, body, _ = api.get("/api/search", q="דיזנגוף 50")
            self.assertEqual(body["results"][0]["type"], "address")
        finally:
            api.stop()


if __name__ == "__main__":
    unittest.main()
