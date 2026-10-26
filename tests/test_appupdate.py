"""Self-update of the program (hmaps/api/appupdate.py): versions, app-manifest.json, the URL
allow-list, the checked download of the installer, the Inno Setup command line (never run here),
the release tooling (tools/release.py app-manifest, tools/set_version.py) and the API.

    python -m unittest tests.test_appupdate
"""

import hashlib
import json
import os
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from hmaps import __version__
from hmaps.api import appupdate, updates
from tests import test_updates as tu
from tests.helpers import ApiServer, sample_db
from tools import release, set_version

SETUP = "HebrewMaps-Setup-9.1.0.exe"


class VersionTest(unittest.TestCase):
    def test_semver_order(self):
        order = ["0.9.9", "1.0.0-rc.1", "1.0.0-rc.2", "1.0.0", "1.0.1", "1.2.0", "1.10.0", "2.0.0"]
        self.assertEqual(sorted(reversed(order), key=appupdate.version_key), order)
        self.assertTrue(appupdate.is_newer("1.10.0", "1.9.3"))
        self.assertTrue(appupdate.is_newer("v1.2.0", "1.1.9"))
        self.assertFalse(appupdate.is_newer("1.1.0", "1.1.0"))
        self.assertFalse(appupdate.is_newer("1.0.0-rc.1", "1.0.0"))
        self.assertFalse(appupdate.is_newer("garbage", "1.0.0"))
        self.assertFalse(appupdate.is_newer(None, "1.0.0"))

    def test_one_version_everywhere(self):
        cur = set_version.current()
        self.assertEqual(set(cur.values()), {__version__}, cur)
        self.assertEqual(set_version.main(["--check"]), 0)

    def test_set_version_rewrites_every_file(self):
        root = Path(tempfile.mkdtemp(prefix="hmaps-ver-"))
        files = {}
        for attr in ("INIT", "TAURI", "CARGO", "LOCK"):
            src = getattr(set_version, attr)
            dst = root / src.name
            dst.write_bytes(src.read_bytes())
            files[attr] = dst
        with mock.patch.multiple(set_version, **files):
            set_version.set_version("v3.4.5")
            self.assertEqual(set(set_version.current().values()), {"3.4.5"})
            self.assertEqual(json.loads(files["TAURI"].read_text(encoding="utf-8"))["version"], "3.4.5")
            self.assertIn('version = "2"', files["CARGO"].read_text(encoding="utf-8"), "dependencies untouched")
            with self.assertRaises(SystemExit):
                set_version.set_version("not-a-version")


class ManifestTest(unittest.TestCase):
    def good(self, **kw):
        m = {"version": "9.1.0", "file": SETUP, "size": 10, "sha256": "ab" * 32, "notes_he": "שיפורים"}
        m.update(kw)
        return m

    def test_parse(self):
        m = appupdate.parse_manifest(self.good())
        self.assertEqual(m["url"], f"https://github.com/rishumit/hebrew-maps/releases/download/v9.1.0/{SETUP}")
        self.assertEqual(m["tag"], "v9.1.0")
        self.assertEqual(m["notes_he"], "שיפורים")
        for bad in (self.good(version="latest"), self.good(file="evil.exe"), self.good(file="../HebrewMaps-Setup-1.exe"),
                    self.good(size=0), self.good(sha256=None), self.good(sha256="xyz"),
                    self.good(url="https://evil.example/HebrewMaps-Setup-9.1.0.exe"),
                    self.good(url="http://github.com/x/HebrewMaps-Setup-9.1.0.exe"), "not a dict"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                appupdate.parse_manifest(bad)

    def test_url_allow_list(self):
        ok = ["https://github.com/rishumit/hebrew-maps/releases/download/v1/x.exe",
              "https://objects.githubusercontent.com/github-production-release-asset/1",
              "https://release-assets.githubusercontent.com/github-production-release-asset/1?sig=x",
              "https://api.github.com/repos/rishumit/hebrew-maps/releases/latest"]
        bad = ["http://github.com/x", "https://github.com.evil.com/x", "https://evil.com/github.com",
               "https://user:pw@github.com/x", "file:///C:/x.exe", "C:\\x.exe", "ftp://github.com/x",
               "https://raw.githubusercontent.com/x", ""]
        for u in ok:
            self.assertTrue(appupdate.url_allowed(u), u)
        for u in bad:
            self.assertFalse(appupdate.url_allowed(u), u)

    def test_release_tool_writes_a_manifest_the_client_accepts(self):
        root = Path(tempfile.mkdtemp(prefix="hmaps-appman-"))
        exe = root / SETUP
        exe.write_bytes(b"MZ installer")
        notes = root / "notes.md"
        notes.write_text("תיקון הניווט\nעדכון חלקי של הנתונים", encoding="utf-8")
        out = root / "app-manifest.json"
        self.assertEqual(release.main(["app-manifest", "--installer", str(exe), "--version", "v9.1.0", "--notes",
                                       str(notes), "--out", str(out)]), 0)
        m = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual((m["version"], m["tag"], m["file"], m["size"]), ("9.1.0", "v9.1.0", SETUP, 12))
        self.assertEqual(m["sha256"], hashlib.sha256(b"MZ installer").hexdigest())
        self.assertTrue(m["url"].endswith(f"/releases/download/v9.1.0/{SETUP}"))
        self.assertIn("תיקון הניווט", m["notes_he"])
        self.assertEqual(appupdate.parse_manifest(m)["version"], "9.1.0")
        m2 = release.app_manifest(exe, "9.1.0")
        self.assertIn("9.1.0", m2["notes_he"], "a default Hebrew note without a notes file")

    def test_installer_command(self):
        cmd = appupdate.installer_command(Path("C:/T/HebrewMaps-update") / SETUP,
                                          Path("C:/T/HebrewMaps-update/install-9.1.0.log"))
        self.assertTrue(cmd[0].endswith(SETUP))
        for flag in ("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS", "/AUTOUPDATE=1"):
            self.assertIn(flag, cmd)
        self.assertTrue(any(a.startswith("/LOG=") and a.endswith("install-9.1.0.log") for a in cmd))
        self.assertNotIn("/DIR", " ".join(cmd), "the installer keeps the existing folder (and its data)")

    def test_installer_script_matches(self):
        iss = (Path(__file__).resolve().parents[1] / "installer" / "hebrew-maps.iss").read_text(encoding="utf-8-sig")
        self.assertIn("{param:AUTOUPDATE|0}", iss)
        self.assertIn("Check: IsAutoUpdate", iss)
        self.assertIn("CloseApplications=force", iss)
        for line in iss.splitlines():
            if line.startswith("Source:") and "..\\data\\" in line and "sample" not in line:
                self.assertIn("onlyifdoesntexist", line, "an upgrade must never overwrite downloaded data: " + line)
        self.assertNotIn("/T /IM", iss, "taskkill /T would kill the installer started by the server")


class FetchAndDownloadTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp(prefix="hmaps-app-rel-"))
        cls.rel = cls.root / "rel"
        cls.rel.mkdir()
        cls.blob = os.urandom(300_000)
        (cls.rel / SETUP).write_bytes(cls.blob)
        cls.srv, cls.base = tu.serve(cls.rel)
        cls.host = cls.base.split("//", 1)[1]
        os.environ["HMAPS_UPDATES_NO_SLEEP"] = "1"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        tu._Handler.log.clear()
        tu._Handler.fail.clear()
        tu._Handler.redirect.clear()
        appupdate._cache = None
        appupdate._job = None
        self.dl = Path(tempfile.mkdtemp(prefix="dl-", dir=self.root))
        self.env = mock.patch.dict(os.environ, {"HMAPS_APP_MANIFEST": f"{self.base}/app-manifest.json",
                                                "HMAPS_APP_DOWNLOAD_DIR": str(self.dl)})
        self.env.start()
        self.hosts = mock.patch.object(appupdate, "TEST_HOSTS", {self.host})
        self.hosts.start()
        self.write(version="9.1.0")

    def tearDown(self):
        self.env.stop()
        self.hosts.stop()

    def write(self, name="app-manifest.json", **kw):
        m = {"schema": 1, "version": "9.1.0", "tag": "v9.1.0", "date": "2026-11-02", "file": SETUP, "size": len(self.blob),
             "sha256": hashlib.sha256(self.blob).hexdigest(), "url": f"{self.base}/{SETUP}", "notes_he": "גרסה חדשה"}
        m.update(kw)
        (self.rel / name).write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
        return m

    def test_check_reports_a_newer_version(self):
        info = appupdate.check_app(force=True)
        self.assertIsNone(info["error"])
        self.assertTrue(info["update_available"])
        self.assertEqual(info["installed"], __version__)
        self.assertEqual(info["available"]["version"], "9.1.0")
        self.assertEqual(info["available"]["notes_he"], "גרסה חדשה")
        self.assertFalse(info["can_install"], "only the installed Windows program updates itself")
        self.assertTrue(info["reason"])
        self.write(version=__version__)
        self.assertFalse(appupdate.check_app(force=True)["update_available"])
        # the data check carries the program check along
        self.assertIn("app", updates.check(data=self.dl))

    def test_errors_are_cached_and_hebrew(self):
        tu._Handler.fail["/app-manifest.json"] = 404
        info = appupdate.check_app(force=True)
        self.assertEqual(info["error"]["code"], "not_found")
        n = len(tu._Handler.log)
        appupdate.check_app()
        self.assertEqual(len(tu._Handler.log), n, "a failed check is not repeated at once")
        self.write(url="https://evil.example/HebrewMaps-Setup-9.1.0.exe")
        tu._Handler.fail.clear()
        info = appupdate.check_app(force=True)
        self.assertEqual(info["error"]["code"], "bad_manifest")

    def test_api_fallback(self):
        api = {"tag_name": "v9.2.0", "body": "הערות", "published_at": "2026-11-09T10:00:00Z", "assets": [
            {"name": "HebrewMaps-Setup.exe", "size": 5, "browser_download_url": f"{self.base}/HebrewMaps-Setup.exe",
             "digest": "sha256:" + "11" * 32},
            {"name": "HebrewMaps-Setup-9.2.0.exe", "size": len(self.blob), "browser_download_url": f"{self.base}/{SETUP}",
             "digest": "sha256:" + hashlib.sha256(self.blob).hexdigest()}]}
        (self.rel / "latest.json").write_text(json.dumps(api), encoding="utf-8")
        tu._Handler.fail["/app-manifest.json"] = 404
        with mock.patch.dict(os.environ, {"HMAPS_APP_API": f"{self.base}/latest.json"}):
            info = appupdate.fetch(force=True)
            self.assertEqual((info["version"], info["file"], info["notes_he"]), ("9.2.0", "HebrewMaps-Setup-9.2.0.exe", "הערות"))
            self.assertEqual(info["sha256"], hashlib.sha256(self.blob).hexdigest())
            # a release asset without a digest: nothing is installed without a checksum
            for a in api["assets"]:
                a.pop("digest")
            (self.rel / "latest.json").write_text(json.dumps(api), encoding="utf-8")
            with self.assertRaises(updates.UpdateError) as cm:
                appupdate.fetch(force=True)
            self.assertEqual(cm.exception.code, "bad_manifest")
            # the API prefers a published app-manifest.json
            api["assets"].append({"name": "app-manifest.json", "browser_download_url": f"{self.base}/am2.json"})
            self.write("am2.json", version="9.3.0")
            (self.rel / "latest.json").write_text(json.dumps(api), encoding="utf-8")
            self.assertEqual(appupdate.fetch(force=True)["version"], "9.3.0")

    def test_download_verifies_and_resumes(self):
        info = appupdate.fetch(force=True)
        part = self.dl / (SETUP + ".part")
        self.dl.mkdir(exist_ok=True)
        part.write_bytes(self.blob[:1000])
        job = appupdate.AppJob("9.1.0")
        path = appupdate.download_installer(info, job)
        self.assertEqual(path.read_bytes(), self.blob)
        self.assertEqual(tu._Handler.log[-1], (f"/{SETUP}", "bytes=1000-"))
        self.assertEqual(job.as_dict()["percent"], 100.0)
        # a complete, verified copy is reused
        n = len(tu._Handler.log)
        appupdate.download_installer(info, job)
        self.assertEqual(len(tu._Handler.log), n)

    def test_sha_mismatch_is_rejected(self):
        self.write(sha256="00" * 32)
        info = appupdate.fetch(force=True)
        with self.assertRaises(updates.UpdateError) as cm:
            appupdate.download_installer(info)
        self.assertEqual(cm.exception.code, "checksum")
        self.assertFalse(list(self.dl.glob("*.exe")), "the bad installer is deleted, never started")

    def test_redirect_to_another_host_is_refused(self):
        tu._Handler.redirect[f"/{SETUP}"] = "http://evil.invalid:81/HebrewMaps-Setup-9.1.0.exe"
        info = appupdate.fetch(force=True)
        with self.assertRaises(updates.UpdateError) as cm:
            appupdate.download_installer(info)
        self.assertEqual(cm.exception.code, "bad_url")
        tu._Handler.redirect[f"/{SETUP}"] = f"{self.base}/real/{SETUP}"
        (self.rel / "real").mkdir(exist_ok=True)
        (self.rel / "real" / SETUP).write_bytes(self.blob)
        self.assertEqual(appupdate.download_installer(info).read_bytes(), self.blob, "allowed redirects are followed")

    def test_install_launches_the_installer_detached(self):
        launched = []
        with mock.patch.object(appupdate, "can_self_update", return_value=(True, None)), \
                mock.patch.object(appupdate, "spawn", side_effect=launched.append):
            job = appupdate.AppJob("")
            info = appupdate.install(job)
        self.assertEqual(info["version"], "9.1.0")
        self.assertEqual(job.state, "launched")
        (cmd,) = launched
        self.assertEqual(Path(cmd[0]), self.dl / SETUP)
        self.assertIn("/VERYSILENT", cmd)
        self.assertIn(f"/LOG={self.dl / 'install-9.1.0.log'}", cmd)
        # nothing newer: nothing is downloaded or started
        self.write(version="0.0.1")
        with mock.patch.object(appupdate, "can_self_update", return_value=(True, None)), \
                mock.patch.object(appupdate, "spawn", side_effect=launched.append), \
                self.assertRaises(updates.UpdateError) as cm:
            appupdate.install()
        self.assertEqual(cm.exception.code, "up_to_date")
        self.assertEqual(len(launched), 1)

    def test_spawn_breaks_away_and_falls_back_to_task_scheduler(self):
        exe = self.dl / SETUP
        cmd = appupdate.installer_command(exe, self.dl / "x.log")
        with mock.patch.object(appupdate.sys, "platform", "win32"), \
                mock.patch.object(appupdate.subprocess, "Popen") as popen:
            appupdate.spawn(cmd)
        flags = popen.call_args.kwargs["creationflags"]
        self.assertTrue(flags & appupdate.CREATE_BREAKAWAY_FROM_JOB and flags & appupdate.DETACHED_PROCESS)
        with mock.patch.object(appupdate.sys, "platform", "win32"), \
                mock.patch.object(appupdate.subprocess, "Popen", side_effect=PermissionError(5, "denied")), \
                mock.patch.object(appupdate.subprocess, "run") as run:
            appupdate.spawn(cmd)
        self.assertEqual([c.args[0][:2] for c in run.call_args_list], [["schtasks", "/Create"], ["schtasks", "/Run"]])
        wrapper = (self.dl / "run-update.cmd").read_text(encoding="utf-8")
        self.assertIn("/AUTOUPDATE=1", wrapper)
        with self.assertRaises(updates.UpdateError):
            appupdate.spawn(cmd)  # not Windows

    def test_cli(self):
        self.assertEqual(updates.cli(["--app", "--check"]), 0)
        self.assertEqual(updates.cli(["--app"]), 1, "not the installed Windows program: points to the site")


class AppApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp(prefix="hmaps-app-api-"))
        rel = cls.root / "rel"
        rel.mkdir()
        blob = b"MZ" * 1000
        (rel / SETUP).write_bytes(blob)
        cls.srv, base = tu.serve(rel)
        (rel / "app-manifest.json").write_text(json.dumps({
            "version": "9.1.0", "file": SETUP, "size": len(blob), "sha256": hashlib.sha256(blob).hexdigest(),
            "url": f"{base}/{SETUP}", "notes_he": "חדש"}), encoding="utf-8")
        data = cls.root / "data"
        data.mkdir()
        (data / "maps.db").write_bytes(sample_db().read_bytes())
        cls.api = ApiServer(data, extra_env={"HMAPS_APP_MANIFEST": f"{base}/app-manifest.json",
                                             "HMAPS_APP_TEST_HOSTS": base.split("//", 1)[1],
                                             "HMAPS_UPDATES_MANIFEST": "http://127.0.0.1:9/manifest.json"})

    @classmethod
    def tearDownClass(cls):
        cls.api.stop()
        cls.srv.shutdown()

    def post(self, path, header=True):
        req = urllib.request.Request(self.api.url(path), data=b"", method="POST",
                                     headers={"X-HMaps-Update": "1"} if header else {})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_endpoints(self):
        s, body, h = self.api.get("/api/updates/app")
        self.assertEqual((s, h.get("cache-control")), (200, "no-store"))
        self.assertEqual((body["installed"], body["available"]["version"]), (__version__, "9.1.0"))
        self.assertTrue(body["update_available"])
        s, body, _ = self.api.get("/api/updates")
        self.assertEqual(body["app"]["available"]["version"], "9.1.0", "the data check carries the app check")
        self.assertFalse(body["online"])
        s, body, _ = self.api.get("/api/meta")
        self.assertEqual(body["version"], __version__)
        s, body = self.post("/api/updates/app", header=False)
        self.assertEqual(s, 400)
        s, body = self.post("/api/updates/app")
        if sys.platform != "win32":
            self.assertEqual((s, body["error"]["code"]), (400, "unsupported"))
        s, body, _ = self.api.get("/api/updates/status")
        self.assertIn("app", body)


if __name__ == "__main__":
    unittest.main()
