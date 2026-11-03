"""Favourites / saved routes and searches, the data-folder setting and the world map (hmaps/api/userdata.py)."""

import json
import os
import shutil
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from hmaps.core import userconfig
from tests.helpers import ApiServer, sample_db


def call(srv, method, path, payload=None, header=True):
    headers = {"Content-Type": "application/json"}
    if header:
        headers["X-HMaps-User"] = "1"
    req = urllib.request.Request(srv.url(path), data=json.dumps(payload).encode() if payload is not None else None,
                                 headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


class UserDataApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="hmaps-user-"))
        cls.data = cls.tmp / "data"
        cls.data.mkdir()
        shutil.copy(sample_db(), cls.data / "maps.db")
        cls.cfg = cls.tmp / "config.json"
        cls.srv = ApiServer(cls.data, extra_env={"HMAPS_CONFIG": str(cls.cfg)})

    @classmethod
    def tearDownClass(cls):
        cls.srv.stop()

    def test_roundtrip(self):
        st, body = call(self.srv, "GET", "/api/user/favorites")
        self.assertEqual((st, body["items"]), (200, []))
        items = [{"id": "poi-1", "name": "בית", "location": {"lat": 32.0, "lon": 34.8}}]
        st, body = call(self.srv, "PUT", "/api/user/favorites", {"items": items})
        self.assertEqual(st, 200)
        st, body = call(self.srv, "GET", "/api/user/favorites")
        self.assertEqual(body["items"], items)
        self.assertTrue((self.data / "user" / "favorites.json").exists())

    def test_rejects_bad_requests(self):
        self.assertEqual(call(self.srv, "PUT", "/api/user/favorites", {"items": []}, header=False)[0], 400)
        self.assertEqual(call(self.srv, "PUT", "/api/user/favorites", {"items": "x"})[0], 400)
        self.assertEqual(call(self.srv, "GET", "/api/user/passwords")[0], 404)

    def test_data_dir(self):
        st, info = call(self.srv, "GET", "/api/settings/data-dir")
        self.assertEqual(st, 200)
        self.assertEqual(Path(info["current"]), self.data)
        target = self.tmp / "elsewhere"
        st, body = call(self.srv, "POST", "/api/settings/data-dir", {"path": str(target)})
        self.assertEqual(st, 200, body)
        self.assertTrue(body["restart"])
        self.assertEqual(json.loads(self.cfg.read_text(encoding="utf-8"))["data_dir"], str(target))
        st, body = call(self.srv, "POST", "/api/settings/data-dir", {"path": "relative/dir"})
        self.assertEqual(st, 400)
        st, body = call(self.srv, "POST", "/api/settings/data-dir", {"reset": True})
        self.assertEqual(st, 200)
        self.assertNotIn("data_dir", json.loads(self.cfg.read_text(encoding="utf-8")))

    def test_meta_world(self):
        st, meta, _ = self.srv.get("/api/meta")
        self.assertIn("world", meta)  # null until web/world/world.json is built

    def test_route_avoid_param(self):
        st, body, _ = self.srv.get("/api/route", **{"from": "32.08,34.78", "to": "32.07,34.79", "mode": "car",
                                                    "avoid": "toll,motorway"})
        self.assertIn(st, (200, 404, 503))


class UserConfigTest(unittest.TestCase):
    def test_resolve(self):
        tmp = Path(tempfile.mkdtemp())
        old = {k: os.environ.get(k) for k in ("HMAPS_CONFIG", "HMAPS_DATA")}
        try:
            os.environ["HMAPS_CONFIG"] = str(tmp / "c.json")
            os.environ.pop("HMAPS_DATA", None)
            self.assertEqual(userconfig.resolve_data_dir(), userconfig.default_data_dir())
            userconfig.set_data_dir(str(tmp / "d"))
            self.assertEqual(userconfig.resolve_data_dir(), tmp / "d")
            os.environ["HMAPS_DATA"] = str(tmp / "env")
            self.assertEqual(userconfig.resolve_data_dir(), tmp / "env")
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


if __name__ == "__main__":
    unittest.main()
