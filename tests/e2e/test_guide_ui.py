"""Browser test of the hiking-route guide (מדריך מסלול): hike guide with stage timeline, the guide of a
marked trail and of a planned walking route, the "מסלולי טיול באזור" list, print layout, mobile.

    python -m unittest tests.e2e.test_guide_ui
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests import fake_osrm
from tests.e2e.test_ui import AUDIT_JS, HERE, SHOTS, wait_js
from tests.helpers import ApiServer, free_port, sample_db


class GuideUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        SHOTS.mkdir(parents=True, exist_ok=True)
        cls.data = Path(tempfile.mkdtemp(prefix="hmaps-guide-ui-"))
        db = sample_db()
        shutil.copy(db, cls.data / "maps.db")
        shutil.copytree(db.parent / "reports", cls.data / "reports")
        cls.osrm_port = free_port()
        cls.osrm = fake_osrm.serve(cls.osrm_port)
        cls.api = ApiServer(cls.data, cls.osrm_port)
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.api.stop()
        cls.osrm.shutdown()

    def page(self, mobile=False, dark=False, hash_=""):
        ctx = self.browser.new_context(viewport={"width": 390, "height": 820} if mobile else {"width": 1366, "height": 900},
                                       is_mobile=mobile, has_touch=mobile, locale="he-IL",
                                       color_scheme="dark" if dark else "light")
        page = ctx.new_page()
        stub = (HERE / "maplibre-stub.js").read_text(encoding="utf-8")
        page.route("**/vendor/maplibre-gl.js", lambda r: r.fulfill(body=stub, content_type="application/javascript"))
        page.route("**/vendor/maplibre-gl.css", lambda r: r.fulfill(body="", content_type="text/css"))
        page.route("**/vendor/mapbox-gl-rtl-text.js", lambda r: r.fulfill(body="", content_type="application/javascript"))
        page.route("**/fonts-ui/**", lambda r: r.fulfill(status=404, body=""))
        self.errors = []
        page.on("pageerror", lambda e: self.errors.append(str(e)))
        page.goto(self.api.url("/?nosw=1" + hash_))
        page.wait_for_selector('#chips [data-chip="__more"]')
        wait_js(page, "!!window.__map")
        return page

    def tearDown(self):
        for ctx in self.browser.contexts:
            ctx.close()

    def test_hike_guide(self):
        page = self.page()
        page.evaluate("location.hash = 'item=hike-1'")
        expect(page.locator("#panel-title")).to_contain_text("מחניון הדרום אל עין דוד")
        view = page.locator(".guide-view")
        expect(view.locator(".gd-chips")).to_contain_text("6.5 ק״מ")
        expect(view.locator(".gd-chips")).to_contain_text("בינוני")
        expect(view.locator(".gd-chips")).to_contain_text("שביל ישראל")
        stages = view.locator(".gd-stage")
        self.assertGreaterEqual(stages.count(), 3)
        expect(stages.first).to_contain_text("שלב 1")
        expect(stages.first.locator(".gd-instr")).to_contain_text("יוצאים מחניון הדרום")
        expect(stages.first).to_contain_text("מצפה הדקלים")
        expect(view).to_contain_text("מידע מעשי ובטיחות")
        expect(view).to_contain_text("112")
        expect(view).to_contain_text("השקיעה היום")
        expect(view.locator(".gd-credits")).to_contain_text("OpenStreetMap")
        expect(view.locator(".gd-desc")).to_contain_text("מסלול לדוגמה")
        self.assertEqual(page.evaluate(AUDIT_JS), [])
        self.assertEqual(page.evaluate("window.__styleErrors"), [])
        page.screenshot(path=str(SHOTS / "guide-01-hike.png"))
        # stage click: zooms to the leg and highlights it on the map
        btn = stages.nth(1).locator(".gd-stage-btn")
        btn.click()
        expect(btn).to_have_attribute("aria-pressed", "true")
        expect(stages.nth(1)).to_have_attribute("aria-current", "step")
        wait_js(page, "window.__sourceData && window.__sourceData['guide-stage']?.features.some(f => f.properties.role === 'leg' && f.properties.n === 2)")
        self.assertTrue(page.evaluate("!!window.__map.getLayer('guide-stage-line')"))
        fit = page.evaluate("window.__lastFit")
        self.assertAlmostEqual(fit[0][1], 31.43, delta=0.002)  # stage 2 starts at the viewpoint
        # keyboard: arrows move between stages
        btn.focus()
        page.keyboard.press("ArrowDown")
        self.assertEqual(page.evaluate("document.activeElement.dataset.stage"), "2")
        page.keyboard.press("Enter")
        expect(stages.nth(2)).to_have_attribute("aria-current", "step")
        page.screenshot(path=str(SHOTS / "guide-02-stage.png"))
        # a sight mini-card opens the site, back returns to the guide
        sight = view.locator(".gd-sight", has_text="עין דוד").first
        sight.click()
        expect(page.locator("#panel-title")).to_have_text("עין דוד")
        wait_js(page, "!window.__sourceData['guide-stage'].features.length")  # layer cleared under another card
        page.locator('[data-act="back"]').click()
        expect(page.locator("#panel-title")).to_contain_text("מחניון הדרום")
        # print layout: only the guide is printed
        page.emulate_media(media="print")
        self.assertEqual(page.evaluate("getComputedStyle(document.querySelector('#map')).display"), "none")
        self.assertEqual(page.evaluate("getComputedStyle(document.querySelector('.gd-actions')).display"), "none")
        self.assertNotEqual(page.evaluate("getComputedStyle(document.querySelector('.gd-stages')).display"), "none")
        page.screenshot(path=str(SHOTS / "guide-03-print.png"), full_page=True)
        page.emulate_media(media="screen")
        page.locator('[data-act="close"]').click()
        wait_js(page, "!window.__sourceData['guide-stage'].features.length")
        self.assertEqual(self.errors, [])

    def test_trail_card_button(self):
        page = self.page()
        page.evaluate("location.hash = 'item=trail-1'")
        expect(page.locator("#panel-title")).to_have_text("שביל ישראל")
        page.locator('[data-act="guide"]').click()
        expect(page.locator("#panel-title")).to_contain_text("שביל ישראל")
        expect(page.locator(".guide-view .gd-stage")).to_have_count(3)  # 6.3 km: split into legs ≤ 4 km
        expect(page.locator(".guide-view")).to_contain_text("אין נתוני גובה")
        self.assertEqual(page.evaluate(AUDIT_JS), [])
        page.locator('[data-act="back"]').click()
        expect(page.locator("#panel-title")).to_have_text("שביל ישראל")
        self.assertEqual(self.errors, [])

    def test_hikes_near_list(self):
        page = self.page(hash_="#map=13.00/31.4400/35.3800")
        page.locator("#btn-layers").click()
        page.locator("#btn-hikes-near").click()
        expect(page.locator("#panel-title")).to_contain_text("מסלולי טיול באזור")
        items = page.locator('.results [data-hike]')
        expect(items).to_have_count(2)
        self.assertEqual(page.evaluate("window.__sourceData.highlight.features[0].geometry.type"), "MultiLineString")
        self.assertEqual(page.evaluate(AUDIT_JS), [])
        page.screenshot(path=str(SHOTS / "guide-04-near.png"))
        items.filter(has_text="לולאת עין דוד").click()
        expect(page.locator("#panel-title")).to_contain_text("לולאת עין דוד")
        expect(page.locator(".gd-chips")).to_contain_text("מעגלי")
        expect(page.locator(".gd-tip-flood")).to_be_visible()  # stream route
        wait_js(page, "location.hash.includes('item=hike-2')")
        # the menu entry opens the same list
        page.locator("#btn-menu").click()
        page.locator('[data-menu="hikes"]').click()
        expect(page.locator("#panel-title")).to_contain_text("מסלולי טיול באזור")
        self.assertEqual(self.errors, [])

    def test_walking_route_guide(self):
        page = self.page(hash_="#dir=31.42000,35.37000;31.44000,35.38000;foot")
        btn = page.locator('[data-act="guide"]')
        expect(btn).to_be_visible()
        btn.click()
        expect(page.locator("#panel-title")).to_contain_text("מדריך מסלול")
        self.assertGreaterEqual(page.locator(".guide-view .gd-stage").count(), 1)
        page.locator('[data-act="back"]').click()
        expect(page.locator("#panel-title")).to_have_text("תכנון מסלול")
        self.assertEqual(self.errors, [])

    def test_mobile_dark(self):
        page = self.page(mobile=True, dark=True)
        page.evaluate("location.hash = 'item=hike-1'")
        expect(page.locator(".guide-view .gd-stage").first).to_be_visible()
        page.locator("#sheet-toggle").click()
        page.wait_for_timeout(300)
        page.screenshot(path=str(SHOTS / "guide-05-mobile-dark.png"))
        no_overflow = page.evaluate("document.querySelector('.panel-body').scrollWidth <= document.querySelector('.panel-body').clientWidth + 1")
        self.assertTrue(no_overflow)
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()
