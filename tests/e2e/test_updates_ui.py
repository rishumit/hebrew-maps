"""Browser test of the "עדכונים" panel: list, "עדכן הכול", progress → installed, weekly notice,
"עדכון מקובץ" (offline update file) without internet.

    python -m unittest tests.e2e.test_updates_ui
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests import test_updates as tu
from tests.e2e.test_ui import AUDIT_JS, HERE, SHOTS, wait_js
from tests.helpers import ApiServer, sample_db
from tools import release


class UpdatesUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        SHOTS.mkdir(parents=True, exist_ok=True)
        cls.root = Path(tempfile.mkdtemp(prefix="hmaps-upd-ui-"))
        src = tu.build_source_data(cls.root)
        rel = cls.root / "release"
        for c in ("basemap", "zmanim"):
            release.pack(c, src, rel, tu.VERSION)
        cls.srv, base = tu.serve(rel)
        m = release.build_manifest(rel, tu.TAG, tu.VERSION, tu.DATE, None, repo="o/r")
        for e in m["components"].values():
            if e.get("file"):
                e["url"] = f"{base}/{e['file']}"
        (rel / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
        cls.bundle = release.bundle(release.build_manifest(rel, tu.TAG, tu.VERSION, tu.DATE, None, repo="o/r"),
                                    [rel], cls.root / "offline", tu.DATE)[0]
        data = cls.root / "data"
        data.mkdir()
        shutil.copy(sample_db(), data / "maps.db")
        cls.api = ApiServer(data, extra_env={"HMAPS_UPDATES_MANIFEST": f"{base}/manifest.json"})
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.api.stop()
        cls.srv.shutdown()

    def page(self, mobile=False, dark=False, init_script=None):
        ctx = self.browser.new_context(viewport={"width": 390, "height": 820} if mobile else {"width": 1366, "height": 900},
                                       is_mobile=mobile, has_touch=mobile, locale="he-IL", timezone_id="Asia/Jerusalem",
                                       color_scheme="dark" if dark else "light")
        if init_script:
            ctx.add_init_script(init_script)
        page = ctx.new_page()
        stub = (HERE / "maplibre-stub.js").read_text(encoding="utf-8")
        page.route("**/vendor/maplibre-gl.js", lambda r: r.fulfill(body=stub, content_type="application/javascript"))
        page.route("**/vendor/maplibre-gl.css", lambda r: r.fulfill(body="", content_type="text/css"))
        page.route("**/vendor/mapbox-gl-rtl-text.js", lambda r: r.fulfill(body="", content_type="application/javascript"))
        page.route("**/fonts-ui/**", lambda r: r.fulfill(status=404, body=""))
        self.errors = []
        page.on("pageerror", lambda e: self.errors.append(str(e)))
        page.goto(self.api.url("/map.html?nosw=1"))
        wait_js(page, "!!window.__map")
        return page

    def tearDown(self):
        for ctx in self.browser.contexts:
            ctx.close()

    def test_1_panel_and_update_all(self):
        page = self.page()
        page.click("#btn-menu")
        page.click('[data-menu="updates"]')
        expect(page.locator("#panel-title")).to_have_text("עדכונים")
        items = page.locator(".upd-item")
        expect(items).to_have_count(6)
        expect(items.locator(".upd-name")).to_have_text(["מפה ונקודות עניין", "מפת בסיס", "נתוני גובה וטופוגרפיה",
                                                         "תחבורה ציבורית", "תמונות ותוכן", "זמני תפילות"])
        basemap = page.locator('.upd-item[data-id="basemap"]')
        expect(basemap.locator(".upd-badge")).to_have_text("זמין להתקנה")
        expect(basemap.locator('[data-act="install"]')).to_be_enabled()
        expect(page.locator('.upd-item[data-id="transit"] .upd-badge')).to_have_text("עדיין לא זמין בגרסה המקוונת")
        expect(page.locator('[data-act="all"]')).to_contain_text("2 חבילות")
        self.assertEqual(page.evaluate(AUDIT_JS), [])
        page.screenshot(path=str(SHOTS / "updates-desktop.png"))
        page.click('[data-act="all"]')
        expect(basemap.locator(".upd-badge")).to_have_text("מעודכן", timeout=15000)
        expect(page.locator('.upd-item[data-id="zmanim"] .upd-badge')).to_have_text("מעודכן", timeout=15000)
        expect(page.locator('[data-act="all"]')).to_be_disabled()
        expect(page.locator("#sr-status")).to_have_text("העדכון הסתיים בהצלחה")
        expect(page.locator(".toast")).to_contain_text("רעננו את הדף")
        self.assertEqual(self.errors, [])

    def test_0_weekly_notice(self):
        # a week since the last automatic check: a small notice, "לפרטים" opens the panel
        page = self.page(mobile=True, dark=True, init_script="localStorage.setItem('hmaps:updates:lastCheck', '0')")
        notice = page.locator("#upd-notice")
        expect(notice).to_contain_text("יש 2 עדכוני נתונים חדשים", timeout=15000)
        self.assertGreater(page.evaluate("JSON.parse(localStorage.getItem('hmaps:updates:lastCheck'))"), 0)
        page.screenshot(path=str(SHOTS / "updates-notice-mobile.png"))
        notice.get_by_role("button", name="לפרטים").click()
        expect(page.locator("#panel-title")).to_have_text("עדכונים")
        expect(notice).to_have_count(0)
        self.assertEqual(self.errors, [])
    def test_2_offline_file(self):
        # a computer without internet: the manifest is unreachable, the update comes from a USB file
        data = self.root / "data-offline"
        data.mkdir()
        shutil.copy(sample_db(), data / "maps.db")
        api = ApiServer(data, extra_env={"HMAPS_UPDATES_MANIFEST": "http://127.0.0.1:9/manifest.json"})
        try:
            self.api, keep = api, self.api
            page = self.page(mobile=True)
            self.api = keep
            page.click("#btn-menu")
            page.click('[data-menu="updates"]')
            expect(page.locator("#panel-title")).to_have_text("עדכונים")
            expect(page.locator(".upd-offline")).to_contain_text("מחשב בלי אינטרנט?")
            self.assertEqual(page.evaluate(AUDIT_JS), [])
            page.set_input_files('[data-act="file"]', str(self.bundle))
            basemap = page.locator('.upd-item[data-id="basemap"]')
            expect(basemap.locator(".upd-badge")).to_have_text("הותקן מקובץ עדכון", timeout=15000)
            expect(page.locator('.upd-item[data-id="zmanim"] .upd-badge')).to_have_text("הותקן מקובץ עדכון")
            expect(page.locator("#sr-status")).to_have_text("העדכון הסתיים בהצלחה")
            page.screenshot(path=str(SHOTS / "updates-offline-mobile.png"), full_page=True)
            st = json.loads((data / "components.json").read_text(encoding="utf-8"))["components"]
            self.assertEqual(st["basemap"]["source"], "offline")
            # the same file again: nothing newer
            page.set_input_files('[data-act="file"]', str(self.bundle))
            expect(page.locator("#sr-status")).to_contain_text("כבר עדכניים", timeout=15000)
            self.assertEqual(self.errors, [])
        finally:
            api.stop()

if __name__ == "__main__":
    unittest.main()
