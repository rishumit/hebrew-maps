"""Online data updates: release tooling (plan / pack / verify / manifest / prune), the updater
(check, download with resume, SHA-256, atomic install) and the /api/updates endpoints.

A local HTTP server stands in for GitHub Releases (with Range support and request logging)."""

import contextlib
import hashlib
import http.server
import io
import json
import os
import shutil
import sqlite3
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import zipfile
from datetime import datetime
from email.message import Message
from pathlib import Path
from unittest import mock

from hmaps.api import updates
from hmaps.core import components as catalog
from hmaps.core import db
from tests.helpers import ApiServer, make_mbtiles, sample_db
from tools import release

VERSION, DATE, TAG = "20261022", "2026-10-22", "data-2026-10-22"
SNAPSHOT = {"fetched": "2026-10-22", "mosads": [{
    "id": "100", "locations": [[32.06885, 34.77172]], "name": "בית הכנסת הגדול", "address": "אלנבי 110 תל אביב-יפו",
    "phone": None, "minyanim": [{"prayer": "שחרית", "time": "06:30", "days": "אבגדהו", "hall": None, "note": None}]}]}


class _Handler(http.server.SimpleHTTPRequestHandler):
    """Static files with HTTP Range (206) support; records the requests."""

    log: list = []
    fail: dict = {}
    redirect: dict = {}

    def log_message(self, *a):  # quiet
        pass

    def do_GET(self):  # noqa: N802
        _Handler.log.append((self.path, self.headers.get("Range")))
        if self.path in _Handler.redirect:
            self.send_response(302)
            self.send_header("Location", _Handler.redirect[self.path])
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        code = _Handler.fail.get(self.path)
        if code:
            self.send_response(code)
            if code == 403:
                self.send_header("X-RateLimit-Remaining", "0")
                self.send_header("X-RateLimit-Reset", str(int(time.time()) + 600))
            self.end_headers()
            return
        path = Path(self.translate_path(self.path))
        if not path.is_file():
            self.send_error(404)
            return
        data = path.read_bytes()
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes="):
            start = int(rng[6:].split("-")[0])
            if start >= len(data):
                self.send_response(416)
                self.end_headers()
                return
            body = data[start:]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
        else:
            body = data
            self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Type", "application/octet-stream")
        self.end_headers()
        self.wfile.write(body)


def _age(path: Path) -> None:
    """Local data from before the release (its version is taken from the file time)."""
    t = datetime(2026, 1, 1).timestamp()
    os.utime(path, (t, t))


def serve(root: Path) -> tuple[http.server.ThreadingHTTPServer, str]:
    handler = lambda *a, **kw: _Handler(*a, directory=str(root), **kw)  # noqa: E731
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def build_source_data(root: Path) -> Path:
    """A data directory as the CI jobs leave it (versioned DB, timestamped tiles, media, snapshot)."""
    data = root / "src-data"
    built = data / "maps.building"
    data.mkdir(parents=True)
    shutil.copy(sample_db(), built)
    con = sqlite3.connect(built)
    con.execute("INSERT OR REPLACE INTO meta VALUES ('built', '2026-10-22T20:00:00+00:00')")
    con.commit()
    con.close()
    db.publish(built, data / "maps.db")
    (data / "reports").mkdir()
    (data / "reports" / "quality-report.json").write_text("{}", encoding="utf-8")
    make_mbtiles(data / "tiles" / "israel-20261022-200000.mbtiles")
    make_mbtiles(data / "tiles" / "contours-20261022-200000.mbtiles")
    make_mbtiles(data / "tiles" / "terrain-20261022-200000.mbtiles")
    (data / "media" / "ab").mkdir(parents=True)
    (data / "media" / "ab" / "abc.jpg").write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")
    (data / "media" / "failed.txt").write_text("x\n", encoding="utf-8")
    snap = data / "sources" / "zmanim" / "zmanim.json"
    snap.parent.mkdir(parents=True)
    snap.write_text(json.dumps(SNAPSHOT, ensure_ascii=False), encoding="utf-8")
    return data


class ReleaseToolTest(unittest.TestCase):
    def test_plan_schedule_and_dispatch(self):
        thu = datetime(2026, 10, 22)
        prev = {"components": {c: {"file": "x.zip", "status": "full"} for c in catalog.IDS}}
        p = release.plan("schedule", {}, prev, thu)
        self.assertFalse(p["terrain"], "terrain only in the first week of the month")
        self.assertTrue(all(p[c] for c in ("maps", "basemap", "transit", "media", "zmanim")))
        self.assertTrue(release.plan("schedule", {}, prev, datetime(2026, 11, 5))["terrain"])
        self.assertTrue(release.plan("schedule", {}, None, thu)["terrain"], "bootstrap: no terrain released yet")
        p = release.plan("workflow_dispatch", {"maps": "false", "transit": "true", "terrain": True}, prev, thu)
        self.assertEqual({c for c, b in p.items() if b}, {"transit", "terrain"})

    def test_plan_dispatch_builds_never_published_components(self):
        thu = datetime(2026, 10, 22)
        defaults = {"maps": True, "basemap": True, "terrain": False, "transit": True, "media": True, "zmanim": True}
        p = release.plan("workflow_dispatch", defaults, None, thu)
        self.assertTrue(all(p.values()), "first manual run with the default inputs builds everything")
        prev = {"components": {c: {"file": "x.zip", "status": "full"} for c in catalog.IDS}}
        prev["components"]["terrain"] = {"file": None, "status": "unavailable"}
        p = release.plan("workflow_dispatch", {"transit": True}, prev, thu)
        self.assertEqual({c for c, b in p.items() if b}, {"transit", "terrain"})

    def test_basemap_duration_args_get_a_unit(self):
        from tools import basemap
        self.assertEqual(basemap.fix_durations(["--http_timeout=120", "--http_retries=3", "--http_timeout=2m"]),
                         ["--http_timeout=120s", "--http_retries=3", "--http_timeout=2m"])

    def test_cmd_plan_reports_previous_manifest(self):
        out = Path(tempfile.mkdtemp(prefix="hmaps-plan-")) / "out.txt"
        with mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(out), "INPUTS_JSON": ""}), \
                contextlib.redirect_stdout(io.StringIO()):
            release.main(["plan", "--event", "schedule", "--previous", str(out.parent / "missing.json")])
        lines = dict(l.split("=", 1) for l in out.read_text(encoding="utf-8").splitlines())
        self.assertEqual(lines["previous"], "false")
        self.assertEqual(lines["terrain"], "true")

    def test_manifest_carries_unchanged_components_forward(self):
        dist = Path(tempfile.mkdtemp(prefix="hmaps-dist-"))
        (dist / catalog.asset_name("transit", VERSION)).write_bytes(b"zipbytes")
        prev = {"components": {"terrain": {"status": "full", "version": "20261001", "date": "2026-10-01",
                                           "release": "data-2026-10-01", "file": "hmaps-terrain-20261001.zip",
                                           "size": 5, "sha256": "ab" * 32, "url": "https://x/t.zip"}}}
        m = release.build_manifest(dist, TAG, VERSION, DATE, prev, repo="o/r")
        self.assertEqual(set(m["components"]), set(catalog.IDS), "the manifest always lists every component")
        t = m["components"]["transit"]
        self.assertEqual((t["status"], t["file"], t["size"]), ("full", "hmaps-transit-20261022.zip", 8))
        self.assertEqual(t["sha256"], hashlib.sha256(b"zipbytes").hexdigest())
        self.assertEqual(t["url"], f"https://github.com/o/r/releases/download/{TAG}/hmaps-transit-20261022.zip")
        self.assertTrue(t["description_he"] and t["min_app_version"])
        tr = m["components"]["terrain"]
        self.assertEqual((tr["status"], tr["release"], tr["url"]), ("unchanged", "data-2026-10-01", "https://x/t.zip"))
        self.assertEqual(m["components"]["maps"]["status"], "unavailable")
        self.assertIn("תחבורה ציבורית", release.notes(m))

    def test_prune_keeps_referenced_releases(self):
        m = {"release": "data-2026-10-22", "components": {"terrain": {"release": "data-2026-08-06"}}}
        tags = ["v1.0.0", "data-latest"] + [f"data-2026-{mo:02d}-{d:02d}" for mo, d in
                                            [(8, 6), (8, 13), (8, 20), (9, 3), (9, 10), (10, 15), (10, 22)]]
        self.assertEqual(release.prune(m, tags, keep=3), ["data-2026-09-03", "data-2026-08-20", "data-2026-08-13"])


class UpdaterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp(prefix="hmaps-upd-"))
        src = build_source_data(cls.root)
        cls.rel = cls.root / "release"
        for cid in ("maps", "basemap", "terrain", "media", "zmanim"):
            release.pack(cid, src, cls.rel, VERSION)
        cls.srv, cls.base = serve(cls.rel)
        os.environ["HMAPS_UPDATES_NO_SLEEP"] = "1"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        os.environ.pop("HMAPS_UPDATES_MANIFEST", None)

    def setUp(self):
        _Handler.log.clear()
        _Handler.fail.clear()
        updates._manifest_cache = None
        self.data = Path(tempfile.mkdtemp(prefix="hmaps-upd-data-", dir=self.root))
        self.write_manifest()

    def write_manifest(self, tamper: dict | None = None) -> dict:
        m = release.build_manifest(self.rel, TAG, VERSION, DATE, None, repo="o/r")
        for e in m["components"].values():
            if e.get("file"):
                e["url"] = f"{self.base}/{e['file']}"
        for cid, patch in (tamper or {}).items():
            m["components"][cid].update(patch)
        (self.rel / "manifest.json").write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
        os.environ["HMAPS_UPDATES_MANIFEST"] = f"{self.base}/manifest.json"
        updates._manifest_cache = None
        return m

    # ------------------------------------------------------------------ packaging
    def test_packages_have_the_catalog_layout_and_verify(self):
        with zipfile.ZipFile(self.rel / f"hmaps-maps-{VERSION}.zip") as zf:
            names = set(zf.namelist())
            self.assertEqual(json.loads(zf.read("component.json"))["component"], "maps")
        self.assertIn("maps.db", names)  # resolved through maps.current, not the missing data/maps.db
        self.assertIn("reports/quality-report.json", names)
        with zipfile.ZipFile(self.rel / f"hmaps-media-{VERSION}.zip") as zf:
            self.assertEqual(sorted(zf.namelist()), ["component.json", "media/ab/abc.jpg"])
        with zipfile.ZipFile(self.rel / f"hmaps-terrain-{VERSION}.zip") as zf:
            self.assertTrue({"contours.mbtiles", "terrain.mbtiles"} <= set(zf.namelist()))
        for z in sorted(self.rel.glob("hmaps-*.zip")):
            facts = release.verify(z)
            self.assertEqual(facts["component"], z.name.split("-")[1])
        self.assertGreater(release.verify(self.rel / f"hmaps-maps-{VERSION}.zip")["pois"], 0)
        with self.assertRaises(SystemExit):
            release.verify(self.rel / f"hmaps-maps-{VERSION}.zip", min_pois=10 ** 9)

    def test_pack_fails_without_data(self):
        with self.assertRaises(SystemExit):
            release.pack("transit", self.data, self.root / "nothing", VERSION)

    # ------------------------------------------------------------------ check
    def test_check_compares_installed_and_available(self):
        info = updates.check(force=True, data=self.data)
        self.assertTrue(info["online"])
        self.assertIsNone(info["error"])
        self.assertEqual(info["release"], TAG)
        by = {c["id"]: c for c in info["components"]}
        self.assertEqual(list(by), list(catalog.IDS))
        self.assertTrue(by["basemap"]["update_available"])
        self.assertIsNone(by["basemap"]["installed"])
        self.assertEqual(by["basemap"]["available"]["date"], DATE)
        self.assertEqual(by["basemap"]["name_he"], "מפת בסיס")
        self.assertFalse(by["transit"]["update_available"], "not in the release -> nothing to install")
        self.assertEqual(by["transit"]["available"]["status"], "unavailable")
        self.assertEqual(info["updates_available"], 5)
        self.assertEqual(info["total_size"], sum(by[c]["available"]["size"] for c in by if by[c]["update_available"]))
        self.assertIn("last_check", json.loads((self.data / "components.json").read_text(encoding="utf-8")))

    def test_newer_local_build_is_not_downgraded(self):
        make_mbtiles(self.data / "tiles" / "israel-x.mbtiles")
        st = {"schema": 1, "components": {"basemap": {"version": "20261101", "date": "2026-11-01"}}}
        updates.save_state(self.data, st)
        by = {c["id"]: c for c in updates.check(data=self.data)["components"]}
        self.assertFalse(by["basemap"]["update_available"])
        self.assertTrue(updates.needs_update({"version": "20261001"}, {"version": VERSION, "file": "f"}))
        self.assertFalse(updates.needs_update({"version": "1", "sha256": "s"}, {"version": "2", "sha256": "s", "file": "f"}))

    def test_min_app_version(self):
        self.write_manifest({"basemap": {"min_app_version": "99.0"}})
        by = {c["id"]: c for c in updates.check(data=self.data)["components"]}
        self.assertFalse(by["basemap"]["compatible"])
        self.assertFalse(by["basemap"]["update_available"])
        with self.assertRaises(updates.UpdateError) as cm:
            updates.install("basemap", self.data)
        self.assertEqual(cm.exception.code, "app_too_old")

    def test_offline_and_rate_limit_errors_are_hebrew(self):
        os.environ["HMAPS_UPDATES_MANIFEST"] = "http://127.0.0.1:9/manifest.json"
        updates._manifest_cache = None
        info = updates.check(data=self.data)
        self.assertFalse(info["online"])
        self.assertEqual(info["error"]["code"], "offline")
        self.assertIn("אין חיבור", info["error"]["message"])
        self.assertEqual(len(info["components"]), len(catalog.IDS), "installed state is still listed")
        h = Message()
        h["X-RateLimit-Remaining"] = "0"
        h["X-RateLimit-Reset"] = str(int(time.time()) + 600)
        err = updates._http_error(urllib.error.HTTPError("u", 403, "rate", h, None))
        self.assertEqual(err.code, "rate_limited")
        self.assertIn("GitHub", err.message)

    def test_api_fallback_when_pointer_is_missing(self):
        os.environ.pop("HMAPS_UPDATES_MANIFEST")
        rels = [{"tag_name": "v1.0.0", "assets": [{"name": "Setup.exe", "browser_download_url": "x"}]},
                {"tag_name": TAG, "assets": [{"name": "manifest.json", "browser_download_url": f"{self.base}/manifest.json"}]}]
        (self.rel / "releases.json").write_text(json.dumps(rels), encoding="utf-8")
        old = updates.manifest_url
        updates.manifest_url = lambda: f"{self.base}/missing/manifest.json"
        os.environ["HMAPS_UPDATES_API"] = f"{self.base}/releases.json"
        try:
            self.assertEqual(updates.fetch_manifest(force=True)["release"], TAG)
            _Handler.fail["/releases.json"] = 403
            with self.assertRaises(updates.UpdateError) as cm:
                updates.fetch_manifest(force=True)
            self.assertEqual(cm.exception.code, "rate_limited")
        finally:
            updates.manifest_url = old
            os.environ.pop("HMAPS_UPDATES_API")

    # ------------------------------------------------------------------ install
    def test_install_maps_swaps_atomically_and_keeps_prayer_times(self):
        shutil.copy(sample_db(), self.data / "maps.db")  # legacy un-versioned file, as old installs have
        _age(self.data / "maps.db")
        snap = self.data / "sources" / "zmanim" / "zmanim.json"
        snap.parent.mkdir(parents=True)
        snap.write_text(json.dumps(SNAPSHOT, ensure_ascii=False), encoding="utf-8")
        rec = updates.install("maps", self.data)
        self.assertEqual((rec["version"], rec["release"], rec["source"]), (VERSION, TAG, "online"))
        live = db.resolve(self.data / "maps.db")
        self.assertNotEqual(live.name, "maps.db")
        self.assertEqual((self.data / "maps.current").read_text(encoding="utf-8").strip(), live.name)
        con = sqlite3.connect(live)
        meta = dict(con.execute("SELECT key, value FROM meta"))
        con.close()
        self.assertEqual(meta["built"], "2026-10-22T20:00:00+00:00")
        self.assertIn("zmanim", meta, "the local prayer-times snapshot is re-applied to the new database")
        st = json.loads((self.data / "components.json").read_text(encoding="utf-8"))
        self.assertEqual(st["components"]["maps"]["sha256"], self.write_manifest()["components"]["maps"]["sha256"])
        self.assertFalse(any((self.data / "updates" / "staging").iterdir()))
        self.assertFalse(any((self.data / "updates" / "downloads").iterdir()), "the ZIP is removed after install")
        self.assertFalse((self.data / "updates" / ".lock").exists())
        by = {c["id"]: c for c in updates.check(data=self.data)["components"]}
        self.assertFalse(by["maps"]["update_available"])
        self.assertEqual(by["maps"]["installed"]["version"], VERSION)

    def test_install_tiles_media_zmanim(self):
        from hmaps.api.store import Basemap
        bm = Basemap(self.data / "tiles" / "israel.mbtiles")
        self.assertFalse(bm.available)
        updates.install("basemap", self.data)
        updates.install("terrain", self.data)
        bm.reload()
        self.assertTrue(bm.available)
        self.assertRegex(bm.path.name, r"^israel-\d{8}-\d{6}\.mbtiles$")
        self.assertTrue(list((self.data / "tiles").glob("contours-*.mbtiles")))
        self.assertTrue(list((self.data / "tiles").glob("terrain-*.mbtiles")))
        (self.data / "media").mkdir()
        (self.data / "media" / "own.jpg").write_bytes(b"x")
        _age(self.data / "media")
        updates.install("media", self.data)
        self.assertTrue((self.data / "media" / "ab" / "abc.jpg").exists())
        self.assertTrue((self.data / "media" / "own.jpg").exists(), "images are merged, not replaced")
        self.assertFalse((self.data / "media" / "failed.txt").exists())
        updates.install("zmanim", self.data)  # no database yet: only the snapshot is stored
        self.assertEqual(json.loads((self.data / "sources" / "zmanim" / "zmanim.json").read_text(encoding="utf-8")),
                         SNAPSHOT)

    def test_sha256_mismatch_is_rejected(self):
        self.write_manifest({"basemap": {"sha256": "0" * 64}})
        with self.assertRaises(updates.UpdateError) as cm:
            updates.install("basemap", self.data)
        self.assertEqual(cm.exception.code, "checksum")
        self.assertFalse(list(self.data.glob("tiles/*.mbtiles")), "nothing installed")
        self.assertFalse(list((self.data / "updates" / "downloads").glob("*")), "the bad file is deleted")
        self.assertNotIn("basemap", updates.load_state(self.data)["components"])

    def test_size_mismatch_is_rejected(self):
        self.write_manifest({"terrain": {"size": 10}})
        with self.assertRaises(updates.UpdateError) as cm:
            updates.install("terrain", self.data)
        self.assertEqual(cm.exception.code, "size_mismatch")

    def test_download_resumes_with_range(self):
        name = f"hmaps-basemap-{VERSION}.zip"
        blob = (self.rel / name).read_bytes()
        dest = self.data / "dl" / name
        dest.parent.mkdir()
        cut = len(blob) // 2
        (dest.parent / (name + ".part")).write_bytes(blob[:cut])
        updates.download(f"{self.base}/{name}", dest, len(blob))
        self.assertEqual(dest.read_bytes(), blob)
        self.assertEqual(_Handler.log, [(f"/{name}", f"bytes={cut}-")])
        # a stale partial file longer than the asset starts over
        (dest.parent / (name + ".part")).write_bytes(b"x" * (len(blob) + 5))
        updates.download(f"{self.base}/{name}", dest, len(blob))
        self.assertEqual(dest.read_bytes(), blob)

    def test_zip_with_path_traversal_or_wrong_component_is_rejected(self):
        evil = self.root / "evil.zip"
        with zipfile.ZipFile(evil, "w") as zf:
            zf.writestr("israel.mbtiles", b"x")
            zf.writestr("../escape.txt", b"x")
        with self.assertRaises(updates.UpdateError) as cm:
            updates.install_zip("basemap", evil, self.data)
        self.assertEqual(cm.exception.code, "bad_zip")
        self.assertFalse((self.data / "updates" / "escape.txt").exists())
        with self.assertRaises(updates.UpdateError) as cm:
            updates.install_zip("terrain", self.rel / f"hmaps-basemap-{VERSION}.zip", self.data)
        self.assertEqual(cm.exception.code, "wrong_component")

    def test_lock_blocks_concurrent_installs(self):
        lock = self.data / "updates" / ".lock"
        lock.parent.mkdir(parents=True)
        lock.write_text("1")
        with self.assertRaises(updates.UpdateError) as cm:
            updates.install("basemap", self.data)
        self.assertEqual(cm.exception.code, "busy")

    def test_cli_check_and_update(self):
        self.assertEqual(updates.cli(["--check", "--data", str(self.data)]), 0)
        self.assertFalse(list(self.data.glob("tiles/*")))
        self.assertEqual(updates.cli(["--data", str(self.data), "-c", "zmanim"]), 0)
        self.assertIn("zmanim", updates.load_state(self.data)["components"])

    def test_background_jobs(self):
        jobs = updates.start(["basemap"], data=self.data)
        self.assertEqual(jobs[0]["component"], "basemap")
        for _ in range(100):
            st = updates.status()
            if not st["busy"]:
                break
            time.sleep(0.05)
        job = updates.status()["jobs"]["basemap"]
        self.assertEqual(job["state"], "done", job)
        self.assertEqual(job["percent"], 100.0)

    # ------------------------------------------------------------------ offline update file
    def make_bundle(self, limit: int | None = None) -> list[Path]:
        m = release.build_manifest(self.rel, TAG, VERSION, DATE, None, repo="o/r")
        out = Path(tempfile.mkdtemp(prefix="bundle-", dir=self.root))
        kw = {"limit": limit} if limit else {}
        return release.bundle(m, [self.rel], out, DATE, **kw)

    def test_offline_bundle_is_built_and_installed(self):
        m = release.build_manifest(self.rel, TAG, VERSION, DATE, None, repo="o/r")
        self.assertEqual(release.bundle_missing(m, [self.root / "nowhere"])[0][0], TAG)
        self.assertEqual(release.bundle_missing(m, [self.rel]), [])
        (bundle,) = self.make_bundle()
        self.assertEqual(bundle.name, f"HebrewMaps-Update-{DATE}.zip")
        with zipfile.ZipFile(bundle) as zf:
            names = set(zf.namelist())
            bm = json.loads(zf.read("manifest.json"))
            self.assertIn("דיסק-און-קי", zf.read("README.txt").decode("utf-8"))
        self.assertTrue({"manifest.json", "README.txt", f"hmaps-basemap-{VERSION}.zip"} <= names)
        self.assertEqual(bm["bundle"], {"part": 1, "parts": 1, "date": DATE})
        self.assertEqual(set(updates.read_bundle(bundle)["components"]), set(bm["components"]))

        # no internet at all: the manifest URL is unreachable
        os.environ["HMAPS_UPDATES_MANIFEST"] = "http://127.0.0.1:9/manifest.json"
        self.assertEqual(updates.cli(["--data", str(self.data), "--bundle", str(bundle), "-c", "basemap", "-c", "zmanim"]), 0)
        st = updates.load_state(self.data)["components"]
        self.assertEqual((st["basemap"]["source"], st["basemap"]["release"], st["basemap"]["version"]),
                         ("offline", TAG, VERSION))
        self.assertTrue(list((self.data / "tiles").glob("israel-*.mbtiles")))
        self.assertNotIn("terrain", st)
        self.assertNotIn("basemap", updates.bundle_plan(updates.read_bundle(bundle), self.data))
        self.assertIn("terrain", updates.bundle_plan(updates.read_bundle(bundle), self.data))
        self.assertFalse(any((self.data / "updates" / "downloads").iterdir()))

    def test_offline_bundle_split_and_rejects(self):
        sizes = sorted(p.stat().st_size for p in self.rel.glob("hmaps-*.zip"))
        parts = self.make_bundle(limit=sizes[-1] + 10)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(p.name.startswith(f"HebrewMaps-Update-{DATE}-part") for p in parts))
        self.assertEqual(release.stable_name(parts[1].name), "HebrewMaps-Update-part2.zip")
        seen = []
        for p in parts:
            seen += list(updates.read_bundle(p)["components"])
        self.assertEqual(sorted(seen), sorted(c for c in catalog.IDS if c != "transit"))
        with self.assertRaises(SystemExit):
            self.make_bundle(limit=10)

        junk = self.root / "junk.zip"
        with zipfile.ZipFile(junk, "w") as zf:
            zf.writestr("hello.txt", "x")
        with self.assertRaises(updates.UpdateError) as cm:
            updates.read_bundle(junk)
        self.assertEqual(cm.exception.code, "bad_bundle")
        half = self.root / "half.zip"
        half.write_bytes(parts[0].read_bytes()[:200])
        with self.assertRaises(updates.UpdateError):
            updates.read_bundle(half)

        # a tampered component inside a well-formed file is rejected by its SHA-256
        (bundle,) = self.make_bundle()
        bad = self.root / "tampered.zip"
        with zipfile.ZipFile(bundle) as src, zipfile.ZipFile(bad, "w") as dst:
            m = json.loads(src.read("manifest.json"))
            m["components"]["zmanim"]["sha256"] = "0" * 64
            dst.writestr("manifest.json", json.dumps(m))
            dst.writestr(m["components"]["zmanim"]["file"], src.read(m["components"]["zmanim"]["file"]))
        with self.assertRaises(updates.UpdateError) as cm:
            updates.install_from_bundle("zmanim", bad, self.data)
        self.assertEqual(cm.exception.code, "checksum")
        self.assertNotIn("zmanim", updates.load_state(self.data)["components"])


class UpdatesApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp(prefix="hmaps-upd-api-"))
        src = build_source_data(cls.root)
        rel = cls.root / "release"
        release.pack("basemap", src, rel, VERSION)
        cls.srv, base = serve(rel)
        m = release.build_manifest(rel, TAG, VERSION, DATE, None, repo="o/r")
        m["components"]["basemap"]["url"] = f"{base}/hmaps-basemap-{VERSION}.zip"
        (rel / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
        cls.data = cls.root / "data"
        cls.data.mkdir()
        shutil.copy(sample_db(), cls.data / "maps.db")
        cls.api = ApiServer(cls.data, extra_env={"HMAPS_UPDATES_MANIFEST": f"{base}/manifest.json",
                                                 "HMAPS_UPDATES_NO_SLEEP": "1"})

    @classmethod
    def tearDownClass(cls):
        cls.api.stop()
        cls.srv.shutdown()

    def post(self, path: str, header: bool = True) -> tuple[int, dict]:
        req = urllib.request.Request(self.api.url(path), data=b"", method="POST",
                                     headers={"X-HMaps-Update": "1"} if header else {})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_offline_upload(self):
        bdir = self.root / "bundle"
        m = release.build_manifest(self.root / "release", TAG, VERSION, DATE, None, repo="o/r")
        (bundle,) = release.bundle(m, [self.root / "release"], bdir, DATE)
        def up(body: bytes, header: bool = True):
            req = urllib.request.Request(self.api.url("/api/updates/offline"), data=body, method="POST",
                                         headers={"X-HMaps-Update": "1"} if header else {})
            try:
                with urllib.request.urlopen(req, timeout=20) as r:
                    return r.status, json.loads(r.read())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read())
        for _ in range(100):  # let a job of another test finish first
            if not self.api.get("/api/updates/status")[1]["busy"]:
                break
            time.sleep(0.1)
        s, body = up(b"not a zip at all")
        self.assertEqual((s, body["error"]["code"]), (400, "bad_bundle"))
        s, body = up(bundle.read_bytes(), header=False)
        self.assertEqual(s, 400)
        s, body = up(bundle.read_bytes() + b"", header=True)
        self.assertIn(s, (200, 202), body)
        self.assertEqual(body["release"], TAG)
        for _ in range(100):
            s, st, _ = self.api.get("/api/updates/status")
            if not st["busy"]:
                break
            time.sleep(0.1)
        if body["jobs"]:
            self.assertTrue(all(j["source"] == "file" for j in body["jobs"]))
            for j in body["jobs"]:
                self.assertEqual(st["jobs"][j["component"]]["state"], "done", st)
        self.assertFalse(list((self.data / "updates" / "offline").glob("upload-*")), "the uploaded copy is removed")
        s, body = up(bundle.read_bytes())
        self.assertEqual((s, body["jobs"]), (200, []))

    def test_check_install_status(self):
        s, body, h = self.api.get("/api/updates")
        self.assertEqual(s, 200)
        self.assertEqual(h.get("cache-control"), "no-store")
        by = {c["id"]: c for c in body["components"]}
        self.assertTrue(by["basemap"]["update_available"])
        self.assertEqual(by["maps"]["installed"]["source"], "local")
        self.assertEqual(body["updates_available"], 1)
        s, body, _ = self.api.get("/api/meta")
        self.assertEqual(body["basemap"]["type"], "fallback")

        s, body = self.post("/api/updates/basemap", header=False)
        self.assertEqual(s, 400)
        s, body = self.post("/api/updates/nope")
        self.assertEqual(s, 404)
        self.assertIn("לא מוכר", body["error"]["message"])
        s, body = self.post("/api/updates/all")
        self.assertEqual(s, 202)
        self.assertEqual([j["component"] for j in body["jobs"]], ["basemap"])
        for _ in range(100):
            s, st, _ = self.api.get("/api/updates/status")
            if not st["busy"]:
                break
            time.sleep(0.1)
        self.assertEqual(st["jobs"]["basemap"]["state"], "done", st)
        # the running server switched to the new basemap right away (reload hook)
        s, body, _ = self.api.get("/api/meta")
        self.assertEqual(body["basemap"]["type"], "openmaptiles")
        s, body, _ = self.api.get("/api/updates")
        by = {c["id"]: c for c in body["components"]}
        self.assertFalse(by["basemap"]["update_available"])
        self.assertEqual(by["basemap"]["installed"]["version"], VERSION)
        self.assertEqual(by["basemap"]["job"]["state"], "done")
        s, body = self.post("/api/updates/all")
        self.assertEqual((s, body["jobs"]), (200, []))


if __name__ == "__main__":
    unittest.main()
