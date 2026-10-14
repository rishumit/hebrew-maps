"""Offline POI images: URL mapping, the /media endpoint and the downloader."""

from __future__ import annotations

import http.server
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from hmaps.core import media
from tests.helpers import ROOT, ApiServer, sample_db

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 200
PLACEHOLDER = (Path(__file__).parent / "fixtures" / "netfree-placeholder.jpg").read_bytes()
COMMONS = "https://commons.wikimedia.org/wiki/Special:FilePath/Western_Wall.jpg"
MISSING = "https://commons.wikimedia.org/wiki/Special:FilePath/Not_downloaded.jpg"


class MediaCoreTest(unittest.TestCase):
    def test_commons_urls(self):
        self.assertEqual(media.download_url(COMMONS, 500), COMMONS + "?width=500")
        self.assertEqual(media.download_url(COMMONS + "?width=640", 330), COMMONS + "?width=330")
        self.assertEqual(media.commons_file("https://commons.wikimedia.org/wiki/File:Tel Aviv beach.jpg"),
                         "Tel_Aviv_beach.jpg")
        self.assertEqual(media.commons_file(
            "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Haifa%20port.jpg/800px-Haifa%20port.jpg"),
            "Haifa_port.jpg")
        self.assertIsNone(media.commons_file("https://example.com/a.jpg"))
        self.assertEqual(media.download_url("https://example.com/a.jpg"), "https://example.com/a.jpg")

    def test_key_and_sniff(self):
        self.assertTrue(media.KEY_RE.match(media.key(COMMONS)))
        self.assertEqual(media.key(COMMONS), media.key(COMMONS + "  "))
        self.assertEqual(media.sniff(JPEG), "image/jpeg")
        self.assertEqual(media.sniff(b"\x89PNG\r\n\x1a\n...."), "image/png")
        self.assertIsNone(media.sniff(b"<html>blocked</html>"))

    def test_filter_placeholder(self):
        try:
            from PIL import Image, ImageDraw
        except ImportError:
            self.skipTest("Pillow not installed")
        import io
        import random
        self.assertTrue(media.is_filter_placeholder(PLACEHOLDER))
        self.assertFalse(media.is_filter_placeholder(JPEG))  # not decodable -> not a placeholder
        rnd = random.Random(1)
        im = Image.new("RGB", (500, 330))
        d = ImageDraw.Draw(im)
        for _ in range(400):  # a "photo": lots of detail everywhere
            x, y = rnd.randrange(500), rnd.randrange(330)
            d.ellipse((x, y, x + rnd.randrange(5, 60), y + rnd.randrange(5, 60)),
                      fill=tuple(rnd.randrange(256) for _ in range(3)))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=85)
        self.assertFalse(media.is_filter_placeholder(buf.getvalue()))
        flat = io.BytesIO()
        Image.new("RGB", (300, 200), "white").save(flat, "PNG")  # flat logo: left alone
        self.assertFalse(media.is_filter_placeholder(flat.getvalue()))


def _db_with_images(data: Path) -> tuple[int, int]:
    db = sample_db()
    shutil.copy(db, data / "maps.db")
    con = sqlite3.connect(data / "maps.db")
    ids = [r[0] for r in con.execute("SELECT id FROM pois WHERE name IS NOT NULL ORDER BY id LIMIT 2")]
    con.execute("UPDATE pois SET image = ? WHERE id = ?", (COMMONS, ids[0]))
    con.execute("UPDATE pois SET image = ? WHERE id = ?", (MISSING, ids[1]))
    con.commit()
    con.close()
    return ids[0], ids[1]


class MediaApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = Path(tempfile.mkdtemp(prefix="hmaps-media-"))
        cls.with_img, cls.without = _db_with_images(cls.data)
        (cls.data / "media").mkdir()
        (cls.data / "media" / media.key(COMMONS)).write_bytes(JPEG)
        cls.api = ApiServer(cls.data, extra_env={"HMAPS_REMOTE_IMAGES": "0"})  # fully offline mode

    @classmethod
    def tearDownClass(cls):
        cls.api.stop()

    def test_local_image_in_card(self):
        s, poi, _ = self.api.get(f"/api/poi/poi-{self.with_img}")
        self.assertEqual(s, 200)
        self.assertEqual(poi["media"]["images"], [f"/media/{media.key(COMMONS)}"])
        self.assertEqual(poi["media"]["thumbnail"], f"/media/{media.key(COMMONS)}")

    def test_not_downloaded_image_is_hidden_offline(self):
        s, poi, _ = self.api.get(f"/api/poi/poi-{self.without}")
        self.assertEqual(poi["media"]["images"], [])
        self.assertIsNone(poi["media"]["thumbnail"])

    def test_media_endpoint(self):
        s, body, h = self.api.get(f"/media/{media.key(COMMONS)}")
        self.assertEqual(s, 200)
        self.assertEqual(body, JPEG)
        self.assertEqual(h["content-type"], "image/jpeg")
        self.assertIn("max-age", h["cache-control"])
        self.assertEqual(self.api.get(f"/media/{media.key(MISSING)}")[0], 404)
        self.assertEqual(self.api.get("/media/..%2Fmaps.db")[0], 404)
        self.assertEqual(self.api.get("/media/maps.db")[0], 404)

    def test_csp_has_no_external_hosts(self):
        _, _, h = self.api.get("/")
        self.assertNotIn("wikimedia", h["content-security-policy"])


class RemoteImagesOptionTest(unittest.TestCase):
    def test_remote_fallback_by_default(self):
        """Not downloaded → the card loads a resized copy straight from Commons (the default)."""
        data = Path(tempfile.mkdtemp(prefix="hmaps-media-remote-"))
        _, without = _db_with_images(data)
        api = ApiServer(data)
        try:
            _, poi, _ = api.get(f"/api/poi/poi-{without}")
            self.assertEqual(poi["media"]["images"], [MISSING + "?width=640"])
            self.assertEqual(poi["media"]["gallery"][0]["full"], MISSING + "?width=1600")
            _, _, h = api.get("/")
            self.assertIn("upload.wikimedia.org", h["content-security-policy"])
        finally:
            api.stop()


class _Handler(http.server.BaseHTTPRequestHandler):
    hits: list[str] = []

    def do_GET(self):  # noqa: N802
        _Handler.hits.append(self.path)
        if "Good" in self.path:
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.end_headers()
            self.wfile.write(JPEG)
        elif "Review" in self.path:
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.end_headers()
            self.wfile.write(PLACEHOLDER)
        elif "Limited" in self.path and _Handler.hits.count(self.path) == 1:
            self.send_response(429)  # rate limited once, then fine
            self.end_headers()
        elif "Limited" in self.path:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(JPEG)
        elif "Busy" in self.path:
            self.send_response(503)
            self.end_headers()
        elif "Filtered" in self.path:
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html>blocked</html>")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *a):
        pass


class DownloaderTest(unittest.TestCase):
    def test_download_resume_and_failures(self):
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_port}"
        data = Path(tempfile.mkdtemp(prefix="hmaps-media-dl-"))
        shutil.copy(sample_db(), data / "maps.db")
        con = sqlite3.connect(data / "maps.db")
        ids = [r[0] for r in con.execute("SELECT id FROM pois ORDER BY id LIMIT 7")]
        urls = [f"{base}/Good1.jpg", f"{base}/Good2.jpg", f"{base}/Filtered.jpg", f"{base}/Gone.jpg",
                f"{base}/Review.jpg", f"{base}/Busy.jpg", f"{base}/Limited.jpg"]
        for i, u in zip(ids, urls):
            con.execute("UPDATE pois SET image = ? WHERE id = ?", (u, i))
        con.commit()
        con.close()
        # a stand-in picture saved by the first version of the downloader is cleaned up
        (data / "media").mkdir()
        (data / "media" / media.key(urls[4])).write_bytes(PLACEHOLDER)
        env = dict(os.environ, HMAPS_DATA=str(data), PYTHONPATH=str(ROOT), HMAPS_MEDIA_RETRY_DELAY="0.01")
        run = lambda *a: subprocess.run([sys.executable, str(ROOT / "tools" / "media.py"), *a], env=env,  # noqa: E731
                                        capture_output=True, text=True, timeout=120)
        try:
            r = run("--workers", "2", "--interval", "0")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("slow down", r.stdout)
            self.assertEqual((data / "media" / media.key(urls[6])).read_bytes(), JPEG)  # 429 then OK
            self.assertEqual((data / "media" / media.key(urls[0])).read_bytes(), JPEG)
            self.assertTrue((data / "media" / media.key(urls[1])).exists())
            self.assertFalse((data / "media" / media.key(urls[2])).exists())  # filter page is not an image
            failed = (data / "media" / "failed.txt").read_text(encoding="utf-8")
            self.assertIn("Filtered.jpg", failed)
            self.assertIn("HTTP 404", failed)
            self.assertIn("Removed 1", r.stdout)
            self.assertFalse((data / "media" / media.key(urls[4])).exists())  # review stand-in not saved
            self.assertIn("Review.jpg", (data / "media" / "pending.txt").read_text(encoding="utf-8"))
            self.assertNotIn("Busy.jpg", failed)  # temporary error: retried next time
            self.assertIn("pending review 1", r.stdout)
            # second run: only the pending and temporary ones are tried again
            _Handler.hits.clear()
            r = run("--interval", "0")
            self.assertEqual(sorted(set(_Handler.hits)), ["/Busy.jpg", "/Review.jpg"])
            # --retry-failed tries the failed ones again (and keeps them listed)
            r = run("--retry-failed", "--interval", "0")
            self.assertEqual(sorted(set(_Handler.hits)), ["/Busy.jpg", "/Filtered.jpg", "/Gone.jpg", "/Review.jpg"])
            self.assertIn("Gone.jpg", (data / "media" / "failed.txt").read_text(encoding="utf-8"))
        finally:
            srv.shutdown()


if __name__ == "__main__":
    unittest.main()
