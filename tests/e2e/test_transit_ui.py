"""Browser test of the public transport mode (Playwright + Chromium, MapLibre stub).

    python -m unittest tests.e2e.test_transit_ui
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests import gtfs_fixture
from tests.e2e.test_ui import AUDIT_JS, HERE, SHOTS, wait_js
from tests.helpers import ApiServer, sample_db


class TransitUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from hmaps.transit import gtfs

        SHOTS.mkdir(parents=True, exist_ok=True)
        cls.data = Path(tempfile.mkdtemp(prefix="hmaps-ui-transit-"))
        db = sample_db()
        shutil.copy(db, cls.data / "maps.db")
        gtfs.build(gtfs_fixture.write(cls.data / "src" / "gtfs.zip"), cls.data / "transit")
        cls.api = ApiServer(cls.data)
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.api.stop()

    def new_page(self, dark=False):
        ctx = self.browser.new_context(viewport={"width": 1366, "height": 860}, locale="he-IL",
                                       color_scheme="dark" if dark else "light", timezone_id="Asia/Jerusalem")
        page = ctx.new_page()
        stub = (HERE / "maplibre-stub.js").read_text(encoding="utf-8")
        page.route("**/vendor/maplibre-gl.js", lambda r: r.fulfill(body=stub, content_type="application/javascript"))
        page.route("**/vendor/maplibre-gl.css", lambda r: r.fulfill(body="", content_type="text/css"))
        page.route("**/vendor/mapbox-gl-rtl-text.js", lambda r: r.fulfill(body="", content_type="application/javascript"))
        page.route("**/fonts-ui/**", lambda r: r.fulfill(status=404, body=""))
        self.errors = []
        page.on("pageerror", lambda e: self.errors.append(str(e)))
        page.on("console", lambda m: m.type == "error" and self.errors.append(m.text))
        page.goto(self.api.url("/map.html?nosw=1#map=14/32.005/34.815&dir=32.00000,34.80000;32.01000,34.83000;transit"))
        wait_js(page, "!!window.__map")
        return page

    def test_transit_mode(self):
        page = self.new_page()
        expect(page.locator('.mode[data-mode="transit"]')).to_have_attribute("aria-checked", "true")
        expect(page.locator(".tr-estimated").first).to_contain_text("זמנים משוערים לפי לוח הזמנים")
        page.locator('.tr-radio:has(input[value="depart"])').click()
        page.locator("#tr-when").fill(f"{gtfs_fixture.WEDNESDAY}T07:55")
        page.locator("#tr-when").dispatch_event("change")
        expect(page.locator(".tr-opt").first).to_be_visible()
        first = page.locator(".tr-opt button").first
        expect(first).to_contain_text("08:00")
        expect(first).to_contain_text("08:30")
        self.assertIn("החלפה אחת", first.get_attribute("aria-label"))
        page.screenshot(path=str(SHOTS / "20-transit-list.png"))
        # map: coloured ride + dashed walk layers
        wait_js(page, "window.__sourceData && window.__sourceData['transit-legs'] && window.__sourceData['transit-legs'].features.length >= 3")
        types = page.evaluate("window.__sourceData['transit-legs'].features.map(f => f.properties.type)")
        self.assertIn("bus", types)
        self.assertIn("walk", types)
        first.click()
        expect(page.locator(".tr-legs")).to_contain_text("קו 1 לכיוון גימל")
        expect(page.locator(".tr-legs")).to_contain_text("הליכה")
        expect(page.locator('[data-act="tr-back"]')).to_be_focused()
        page.screenshot(path=str(SHOTS / "21-transit-card.png"))
        issues = page.evaluate(AUDIT_JS)
        self.assertEqual(issues, [], issues)
        # stop card with departures
        page.locator(".tr-stop-btn").first.click()
        expect(page.locator("#panel-title")).to_contain_text("אלף/הרצל")
        expect(page.locator(".tr-deps li").first).to_contain_text("08:00")
        page.screenshot(path=str(SHOTS / "22-transit-stop.png"))
        page.locator('#panel [data-act="back"]').click()
        expect(page.locator(".tr-legs")).to_be_visible()
        # back to the list, then switch to car clears the transit layers
        page.locator('[data-act="tr-back"]').click()
        expect(page.locator(".tr-opt").first).to_be_visible()
        page.locator('.mode[data-mode="car"]').click()
        wait_js(page, "window.__sourceData['transit-legs'].features.length === 0")
        real = [e for e in self.errors if "404" not in e and "Failed to load resource" not in e and "503" not in e]
        self.assertEqual(real, [])

    def test_stop_lines_and_line_view(self):
        page = self.new_page()
        page.locator('.tr-radio:has(input[value="depart"])').click()
        page.locator("#tr-when").fill(f"{gtfs_fixture.WEDNESDAY}T07:55")
        page.locator("#tr-when").dispatch_event("change")
        page.locator(".tr-opt button").first.click()
        # the line badge of the first ride opens the line view, boarding stop highlighted
        page.locator(".tr-legs .tr-line-btn").first.click()
        expect(page.locator("#panel-title")).to_contain_text("קו 1")
        expect(page.locator(".tr-line-head")).to_contain_text("לכיוון גימל")
        expect(page.locator(".tr-ls")).to_have_count(3)
        expect(page.locator(".tr-ls.from")).to_contain_text("אלף/הרצל")
        expect(page.locator('.tr-ls.from button')).to_have_attribute("aria-current", "location")
        expect(page.locator(".tr-ls").nth(2)).to_contain_text("10 דק׳")
        wait_js(page, "window.__sourceData['transit-line'] && window.__sourceData['transit-line'].features.length === 1")
        self.assertEqual(page.evaluate("window.__sourceData['transit-line-stops'].features.length"), 3)
        # back to the itinerary removes the overlay
        page.locator('#panel [data-act="back"]').click()
        expect(page.locator(".tr-legs")).to_be_visible()
        wait_js(page, "window.__sourceData['transit-line'].features.length === 0")
        # stop card: every line serving the stop, grouped by mode, with each direction
        page.locator(".tr-stop-btn").first.click()
        expect(page.locator("#panel-title")).to_contain_text("אלף/הרצל")
        sec = page.locator(".tr-routes-sec")
        expect(sec).to_contain_text("אוטובוסים")
        expect(page.locator(".tr-route")).to_have_count(2)  # lines 1 and 3, naturally sorted
        expect(page.locator(".tr-route").first).to_contain_text("לכיוון גימל")
        expect(page.locator(".tr-route").first).to_contain_text("לכיוון בית")
        expect(page.locator(".tr-route").first).to_contain_text("אגד")
        expect(page.locator(".tr-route").nth(1)).to_contain_text("לכיוון הא")
        expect(page.locator(".tr-lines .tr-line-btn")).to_have_count(2)
        issues = page.evaluate(AUDIT_JS)
        self.assertEqual(issues, [], issues)
        page.screenshot(path=str(SHOTS / "24-transit-stop-lines.png"))
        # a direction → the line view; the return route is offered in the direction selector
        page.locator(".tr-route").first.locator(".tr-dir-btn", has_text="לכיוון גימל").click()
        expect(page.locator(".tr-ls")).to_have_count(3)
        sel = page.locator("#tr-dir-sel")
        expect(sel.locator("option")).to_have_count(2)
        page.screenshot(path=str(SHOTS / "25-transit-line.png"))
        issues = page.evaluate(AUDIT_JS)
        self.assertEqual(issues, [], issues)
        sel.select_option(index=1)
        expect(page.locator(".tr-line-head")).to_contain_text("לכיוון אלף")
        expect(page.locator(".tr-ls").first).to_contain_text("גימל/מרכז")
        expect(sel).to_be_focused()
        # keyboard: a stop in the list opens its card; back returns to the line, then to the stop card
        page.locator(".tr-ls button").first.focus()
        page.keyboard.press("Enter")
        expect(page.locator("#panel-title")).to_contain_text("גימל/מרכז")
        wait_js(page, "window.__sourceData['transit-line'].features.length === 0")
        page.locator('#panel [data-act="back"]').click()
        expect(page.locator(".tr-line-head")).to_contain_text("לכיוון אלף")
        wait_js(page, "window.__sourceData['transit-line'].features.length === 1")
        page.keyboard.press("Escape")
        expect(page.locator("#panel-title")).to_contain_text("אלף/הרצל")
        # departures board badges open the line too
        page.locator(".tr-deps .tr-line-btn").first.click()
        expect(page.locator(".tr-ls.from")).to_contain_text("אלף/הרצל")
        real = [e for e in self.errors if "404" not in e and "Failed to load resource" not in e and "503" not in e]
        self.assertEqual(real, [])

    def test_shabbat_notice(self):
        page = self.new_page(dark=True)
        page.locator('.tr-radio:has(input[value="depart"])').click()
        page.locator("#tr-when").fill("2026-10-17T10:00")
        page.locator("#tr-when").dispatch_event("change")
        expect(page.locator(".tr-notice")).to_contain_text("שבת")
        expect(page.locator(".tr-opt").first).to_contain_text("08:00")
        page.screenshot(path=str(SHOTS / "23-transit-shabbat-dark.png"))


if __name__ == "__main__":
    unittest.main()
