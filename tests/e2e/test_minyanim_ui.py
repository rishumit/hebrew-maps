"""Browser test of the prayer times section of a synagogue card and the "מניינים קרובים" list.

    python -m unittest tests.e2e.test_minyanim_ui
"""

import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from hmaps.etl import zmanim
from tests.e2e.test_ui import AUDIT_JS, HERE, SHOTS, wait_js
from tests.helpers import ApiServer, sample_db
from tests.test_zmanim import fake_opener


class MinyanimUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        SHOTS.mkdir(parents=True, exist_ok=True)
        cls.data = Path(tempfile.mkdtemp(prefix="hmaps-mn-ui-"))
        db = sample_db()
        shutil.copy(db, cls.data / "maps.db")
        shutil.copytree(db.parent / "reports", cls.data / "reports")
        snap = zmanim.fetch(cls.data / "src", fetcher=zmanim.Fetcher(None, interval=0, opener=fake_opener([])))
        con = sqlite3.connect(cls.data / "maps.db")
        zmanim.apply(con, snap)
        cls.great = con.execute("SELECT poi_id FROM synagogues_ext WHERE mosad_id = '100'").fetchone()[0]
        con.close()
        cls.api = ApiServer(cls.data)
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.api.stop()

    def page(self, mobile=False, dark=False):
        ctx = self.browser.new_context(viewport={"width": 390, "height": 820} if mobile else {"width": 1366, "height": 900},
                                       is_mobile=mobile, has_touch=mobile, locale="he-IL", timezone_id="Asia/Jerusalem",
                                       color_scheme="dark" if dark else "light")
        page = ctx.new_page()
        stub = (HERE / "maplibre-stub.js").read_text(encoding="utf-8")
        page.route("**/vendor/maplibre-gl.js", lambda r: r.fulfill(body=stub, content_type="application/javascript"))
        page.route("**/vendor/maplibre-gl.css", lambda r: r.fulfill(body="", content_type="text/css"))
        page.route("**/vendor/mapbox-gl-rtl-text.js", lambda r: r.fulfill(body="", content_type="application/javascript"))
        page.route("**/fonts-ui/**", lambda r: r.fulfill(status=404, body=""))
        self.errors = []
        page.on("pageerror", lambda e: self.errors.append(str(e)))
        page.goto(self.api.url("/?nosw=1"))
        page.wait_for_selector('#chips [data-chip="__more"]')
        wait_js(page, "!!window.__map")
        return page

    def tearDown(self):
        for ctx in self.browser.contexts:
            ctx.close()

    def test_card_and_nearby(self):
        page = self.page()
        page.evaluate(f"location.hash = 'item=poi-{self.great}'")
        expect(page.locator("#panel-title")).to_have_text("בית הכנסת הגדול")
        sec = page.locator("section.minyanim")
        expect(sec).to_be_visible()
        expect(sec.locator("#mn-title")).to_have_text("זמני תפילות")
        expect(sec.locator('[role="tab"]')).to_have_count(3)
        expect(sec.locator('[role="tab"][aria-selected="true"]')).to_have_count(1)
        expect(sec).to_contain_text("03-7630585")
        expect(sec).to_contain_text("נדרים פלוס")
        expect(sec).to_contain_text("עודכן ב־")
        # Shabbat tab via the keyboard (RTL: the left arrow moves forward)
        sat = sec.locator('[data-tab="sat"]')
        sat.click()
        expect(sat).to_have_attribute("aria-selected", "true")
        expect(sec.locator("#mn-panel")).to_contain_text("08:00")
        expect(sec.locator("#mn-panel")).not_to_contain_text("13:30")
        page.keyboard.press("Home")
        week = sec.locator('[data-tab="week"]')
        expect(week).to_have_attribute("aria-selected", "true")
        expect(week).to_be_focused()
        expect(sec.locator("#mn-panel")).to_contain_text("06:30")
        expect(sec.locator("#mn-panel")).to_contain_text("ק.הודו")
        expect(sec.locator(".mn-group").first).to_have_text("שחרית")
        page.keyboard.press("ArrowLeft")
        expect(sec.locator('[data-tab="fri"]')).to_have_attribute("aria-selected", "true")
        self.assertEqual(page.evaluate(AUDIT_JS), [])
        sec.scroll_into_view_if_needed()
        page.screenshot(path=str(SHOTS / "minyanim-01-card.png"))
        # nearby minyanim
        sec.locator('[data-act="minyan-near"]').click()
        expect(page.locator("#panel-title")).to_contain_text("מניינים קרובים")
        items = page.locator(".mn-near-list .result button")
        expect(items.first).to_be_visible()
        self.assertGreaterEqual(items.count(), 1)
        self.assertEqual(page.evaluate(AUDIT_JS), [])
        page.screenshot(path=str(SHOTS / "minyanim-02-near.png"))
        page.locator('[data-prayer="שחרית"]').click()  # every day of the week has a שחרית in the fixture
        expect(page.locator('[data-prayer="שחרית"]')).to_have_attribute("aria-pressed", "true")
        expect(page.locator(".mn-near-list .r-name").first).to_contain_text("שחרית")
        page.locator(".mn-near-list .result button", has_text="בית הכנסת הגדול").first.click()
        expect(page.locator("#panel-title")).to_have_text("בית הכנסת הגדול")
        self.assertEqual(self.errors, [])

    def test_mobile_dark(self):
        page = self.page(mobile=True, dark=True)
        page.evaluate(f"location.hash = 'item=poi-{self.great}'")
        expect(page.locator("section.minyanim")).to_be_visible()
        page.locator("section.minyanim").scroll_into_view_if_needed()
        page.wait_for_timeout(300)
        page.screenshot(path=str(SHOTS / "minyanim-03-mobile-dark.png"))
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()
