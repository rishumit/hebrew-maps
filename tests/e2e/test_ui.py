"""Browser tests of the web app (Playwright + Chromium).

The real API runs on the sample dataset with a fake OSRM; MapLibre is replaced
by tests/e2e/maplibre-stub.js, which also validates every style layer.

    python -m unittest tests.e2e.test_ui
Screenshots are written to $HMAPS_SHOTS (default: /tmp/hmaps-shots).
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests import fake_osrm
from tests.helpers import ApiServer, free_port, sample_db

HERE = Path(__file__).parent
SHOTS = Path(os.environ.get("HMAPS_SHOTS", "/tmp/hmaps-shots"))

AUDIT_JS = """() => {
  const issues = [];
  const name = (el) => (el.getAttribute('aria-label') || el.textContent || el.getAttribute('title') || '').trim();
  document.querySelectorAll('button, a[href], [role="button"], [role="menuitem"], [role="option"], [role="radio"]').forEach((el) => {
    if (el.closest('[hidden]')) return;
    if (!name(el) && !el.querySelector('img[alt]:not([alt=""])')) issues.push('no accessible name: ' + el.outerHTML.slice(0, 90));
  });
  document.querySelectorAll('input:not([type="hidden"])').forEach((el) => {
    const labelled = el.labels?.length || el.getAttribute('aria-label') || el.getAttribute('aria-labelledby');
    if (!labelled && !el.closest('label')) issues.push('input without label: ' + (el.id || el.name));
  });
  document.querySelectorAll('img').forEach((img) => { if (!img.hasAttribute('alt')) issues.push('img without alt'); });
  const ids = {};
  document.querySelectorAll('[id]').forEach((el) => { ids[el.id] = (ids[el.id] || 0) + 1; });
  Object.entries(ids).forEach(([id, n]) => { if (n > 1) issues.push('duplicate id ' + id); });
  const hs = [...document.querySelectorAll('h1,h2,h3,h4')].filter((h) => !h.closest('[hidden]')).map((h) => +h.tagName[1]);
  for (let i = 1; i < hs.length; i++) if (hs[i] - hs[i - 1] > 1) issues.push('heading level skipped: h' + hs[i - 1] + ' -> h' + hs[i]);
  if (document.querySelectorAll('h1').length !== 1) issues.push('expected exactly one h1');
  return issues;
}"""


def wait_js(page, expr, timeout=8.0):
    """Poll a JS expression (wait_for_function would need 'unsafe-eval', which our CSP forbids)."""
    import time
    end = time.time() + timeout
    while time.time() < end:
        if page.evaluate(f"() => {{ try {{ return !!({expr}); }} catch (e) {{ return false; }} }}"):
            return
        page.wait_for_timeout(50)
    raise AssertionError(f"timeout waiting for {expr}")


class UiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        SHOTS.mkdir(parents=True, exist_ok=True)
        cls.data = Path(tempfile.mkdtemp(prefix="hmaps-ui-"))
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

    def new_page(self, mobile=False, dark=False):
        ctx = self.browser.new_context(
            viewport={"width": 390, "height": 800} if mobile else {"width": 1366, "height": 860},
            is_mobile=mobile, has_touch=mobile, locale="he-IL", color_scheme="dark" if dark else "light",
            permissions=["geolocation", "clipboard-read", "clipboard-write"], geolocation={"latitude": 32.078, "longitude": 34.775},
        )
        page = ctx.new_page()
        stub = (HERE / "maplibre-stub.js").read_text(encoding="utf-8")
        page.route("**/vendor/maplibre-gl.js", lambda r: r.fulfill(body=stub, content_type="application/javascript"))
        page.route("**/vendor/maplibre-gl.css", lambda r: r.fulfill(body="", content_type="text/css"))
        page.route("**/vendor/mapbox-gl-rtl-text.js", lambda r: r.fulfill(body="", content_type="application/javascript"))
        page.route("**/fonts-ui/**", lambda r: r.fulfill(status=404, body=""))
        self.errors = []
        page.on("pageerror", lambda e: self.errors.append(str(e)))
        page.on("console", lambda m: m.type == "error" and self.errors.append(m.text))
        page.goto(self.api.url("/map.html?nosw=1"))
        page.wait_for_selector('#chips [data-chip="__more"]')
        wait_js(page, "!!window.__map")
        page.wait_for_timeout(150)
        return page

    def tearDown(self):
        for ctx in self.browser.contexts:
            ctx.close()

    def assert_clean(self, page):
        style_errors = page.evaluate("window.__styleErrors")
        self.assertEqual(style_errors, [], "style validation errors")
        real = [e for e in self.errors if "404" not in e and "Failed to load resource" not in e]
        self.assertEqual(real, [], "console errors")

    # ------------------------------------------------------------------ tests
    def test_initial_load_and_a11y(self):
        page = self.new_page()
        expect(page.locator("html")).to_have_attribute("dir", "rtl")
        expect(page.locator("#banner")).to_be_visible()  # basemap not built -> notice
        self.assertGreaterEqual(page.locator("#chips .chip").count(), 10)
        # chips fit in one row: no horizontal overflow, "עוד" always visible
        self.assertLessEqual(page.evaluate("document.querySelector('#chips').scrollWidth - document.querySelector('#chips').clientWidth"), 1)
        expect(page.locator('#chips [data-chip="__more"]')).to_be_visible()
        issues = page.evaluate(AUDIT_JS)
        self.assertEqual(issues, [])
        layer_ids = page.evaluate("window.__map._layers.map(l => l.id)")
        for lid in ("pois", "results", "route-line", "selection", "location-dot"):
            self.assertIn(lid, layer_ids)
        page.screenshot(path=str(SHOTS / "01-initial.png"))
        self.assert_clean(page)

    def test_autocomplete_keyboard_to_place_card(self):
        page = self.new_page()
        q = page.locator("#q")
        q.click()
        q.type("ירו", delay=40)
        expect(page.locator("#q-list")).to_be_visible()
        expect(page.locator("#q-list [role=option]").first).to_contain_text("ירושלים")
        expect(q).to_have_attribute("aria-expanded", "true")
        page.screenshot(path=str(SHOTS / "02-autocomplete.png"))
        q.press("ArrowDown")
        expect(q).to_have_attribute("aria-activedescendant", "q-list-o0")
        q.press("Enter")
        expect(page.locator("#panel")).to_be_visible()
        expect(page.locator("#panel-title")).to_have_text("ירושלים")
        expect(page.locator("#panel")).to_contain_text("אוכלוסייה")
        self.assertIsNotNone(page.evaluate("window.__lastFit"))
        page.screenshot(path=str(SHOTS / "03-place-card.png"))
        page.locator("#panel [data-sub=synagogue]").click()
        expect(page.locator("#panel")).to_contain_text("בית הכנסת הגדול ירושלים")
        self.assert_clean(page)

    def test_category_search_results_and_poi_card(self):
        page = self.new_page()
        q = page.locator("#q")
        q.fill("מסעדות בתל אביב")
        q.press("Enter")
        expect(page.locator(".results .result").first).to_be_visible()
        expect(page.locator("#panel .tag")).to_contain_text("תל אביב-יפו")
        expect(page.locator('#chips [data-chip="restaurant"]')).to_have_attribute("aria-pressed", "true")
        self.assertIn("cats=restaurant", page.evaluate("window.__poiTiles[0]"))
        feats = page.evaluate("window.__sourceData.results.features.length")
        self.assertGreater(feats, 0)
        page.screenshot(path=str(SHOTS / "04-results.png"))
        page.locator('[data-filter="kosher"]').click()
        expect(page.locator('[data-filter="kosher"]')).to_have_attribute("aria-pressed", "true")
        page.locator(".results .result button").first.click()
        expect(page.locator("#panel-title")).to_contain_text("מסעדה לדוגמה")
        expect(page.locator("#panel")).to_contain_text("כשר")
        page.locator(".hours-toggle").click()
        expect(page.locator("#hours-table")).to_be_visible()
        expect(page.locator("#hours-table")).to_contain_text("שבת")
        page.screenshot(path=str(SHOTS / "05-poi-card.png"))
        page.locator('[data-act="back"]').click()
        expect(page.locator(".results")).to_be_visible()
        page.keyboard.press("Escape")
        self.assert_clean(page)

    def test_typo_correction_notice(self):
        page = self.new_page()
        page.locator("#q").fill("מגדל דויד")
        page.locator("#q").press("Enter")
        expect(page.locator("#panel")).to_contain_text("מציג תוצאות עבור: מגדל דוד")
        self.assert_clean(page)

    def test_map_poi_click_and_directions(self):
        page = self.new_page()
        page.evaluate("window.__map.__clickPoi({id: 2, name: 'x'}, [34.7752, 32.0753])")
        expect(page.locator("#panel-title")).to_have_text("דיזנגוף סנטר")
        expect(page.locator("#panel a[href^='tel:']").first).to_be_visible()
        page.locator('#panel [data-act="route"]').click()
        expect(page.locator("#panel-title")).to_have_text("תכנון מסלול")
        expect(page.locator("#dir-to")).to_have_value("דיזנגוף סנטר")
        frm = page.locator("#dir-from")
        frm.fill("")
        frm.type("רוטש", delay=30)
        expect(page.locator("#dir-from-list [role=option]").first).to_be_visible()
        page.locator("#dir-from-list [role=option]").first.click()
        expect(page.locator(".steps .step").first).to_be_visible()
        expect(page.locator(".steps")).to_contain_text("פנו שמאלה לשדרות רוטשילד")
        expect(page.locator(".steps")).to_contain_text("הגעתם ליעד")
        wait_js(page, "location.hash.includes('dir=')")
        page.screenshot(path=str(SHOTS / "06-directions.png"))
        page.locator('.mode[data-mode="foot"]').click()
        expect(page.locator('.mode[data-mode="foot"]')).to_have_attribute("aria-checked", "true")
        expect(page.locator(".steps")).to_contain_text("התחילו ללכת")
        page.locator('[data-act="swap"]').click()
        expect(page.locator("#dir-from")).to_have_value("דיזנגוף סנטר")
        self.assert_clean(page)

    def test_poi_hover_card(self):
        page = self.new_page()
        card = page.locator("#poi-hover")
        page.evaluate("window.__map.__hoverPoi({id: 2, name: 'דיזנגוף סנטר', cat: 'shopping', sub: 'mall', icon: 'shop'}, [34.7752, 32.0753])")
        expect(card).to_be_visible()
        expect(card).to_have_attribute("aria-label", "דיזנגוף סנטר")
        expect(card.locator(".ph-name")).to_have_text("דיזנגוף סנטר")
        expect(card.locator(".ph-loading")).to_have_count(0)  # full record loaded from /api/poi
        expect(card.locator(".ph-status")).to_be_visible()
        expect(card.locator('[data-hact="route"]')).to_be_visible()
        page.screenshot(path=str(SHOTS / "15-poi-hover.png"))
        page.evaluate("window.__map.__leavePoi()")
        expect(card).to_be_hidden()
        # quick action: route to the place straight from the card
        page.evaluate("window.__map.__hoverPoi({id: 2, name: 'דיזנגוף סנטר'}, [34.7752, 32.0753])")
        card.locator('[data-hact="route"]').click()
        expect(page.locator("#panel-title")).to_have_text("תכנון מסלול")
        expect(page.locator("#dir-to")).to_have_value("דיזנגוף סנטר")
        expect(card).to_be_hidden()
        # clicking the name opens the full card
        page.evaluate("window.__map.__hoverPoi({id: 2, name: 'דיזנגוף סנטר'}, [34.7752, 32.0753])")
        card.locator(".ph-main").click()
        expect(page.locator("#panel-title")).to_have_text("דיזנגוף סנטר")
        expect(card).to_be_hidden()
        # the item already open in the panel does not get a hover card
        page.evaluate("window.__map.__clickPoi({id: 2, name: 'x'}, [34.7752, 32.0753])")
        expect(page.locator("#panel-title")).to_have_text("דיזנגוף סנטר")
        page.evaluate("window.__map.__hoverPoi({id: 2, name: 'x'}, [34.7752, 32.0753])")
        page.wait_for_timeout(400)
        expect(card).to_be_hidden()
        self.assert_clean(page)

    def test_address_search_opens_point(self):
        page = self.new_page()
        page.locator("#q").fill("דיזנגוף 56 תל אביב")
        page.locator("#q").press("Enter")
        expect(page.locator("#panel-title")).to_have_text("דיזנגוף 56")
        expect(page.locator("#panel")).to_contain_text("המיקום משוער")
        expect(page.locator("#nearby-list .result").first).to_be_visible()
        self.assert_clean(page)

    def test_context_menu_whats_here_and_copy(self):
        page = self.new_page()
        page.evaluate("window.__map.fire('contextmenu', {lngLat: {lng: 34.7746, lat: 32.0776}, point: {x: 600, y: 400}})")
        expect(page.locator("#ctx-menu")).to_be_visible()
        page.screenshot(path=str(SHOTS / "07-context-menu.png"))
        expect(page.locator("#ctx-menu button").first).to_be_focused()
        page.keyboard.press("ArrowDown")
        expect(page.locator('#ctx-menu [data-act="from"]')).to_be_focused()
        page.locator('#ctx-menu [data-act="here"]').click()
        expect(page.locator("#panel-title")).to_contain_text("דיזנגוף 50")
        self.assert_clean(page)

    def test_menu_dialogs_and_keyboard(self):
        page = self.new_page()
        page.locator("#btn-menu").click()
        expect(page.locator("#menu")).to_be_visible()
        page.screenshot(path=str(SHOTS / "08-menu.png"))
        page.locator('[data-menu="accessibility"]').click()
        expect(page.locator("#dialog")).to_be_visible()
        expect(page.locator("#dialog")).to_contain_text("WCAG 2.1")
        page.keyboard.press("Escape")
        expect(page.locator("#dialog")).to_be_hidden()
        page.locator("body").click(position={"x": 700, "y": 500})
        page.keyboard.press("?")
        expect(page.locator("#dialog-title")).to_have_text("קיצורי מקלדת")
        page.keyboard.press("Escape")
        page.keyboard.press("/")
        expect(page.locator("#q")).to_be_focused()
        self.assert_clean(page)

    def test_layers_theme_switch(self):
        page = self.new_page()
        page.locator("#btn-layers").click()
        expect(page.locator("#layers-pop")).to_be_visible()
        page.locator('input[name="maptype"][value="nature"] + .swatch').click()
        wait_js(page, "window.__lastStyle.name.includes('nature')")
        page.locator('input[name="uitheme"][value="dark"] + span').click()
        expect(page.locator("html")).to_have_attribute("data-theme", "dark")
        page.screenshot(path=str(SHOTS / "09-layers-dark.png"))
        page.locator("#toggle-kosher").check()
        self.assertIn("kosher=1", page.evaluate("window.__poiTiles[0]"))
        self.assert_clean(page)

    def test_satellite_whole_country_download(self):
        """Layers popover: level choice with estimates, start, progress, stop (API mocked: no imagery server)."""
        import json as _json
        page = self.new_page()
        state = {"job": None, "posted": None, "polls": 0}
        levels = [{"id": "basic", "name_he": "בסיסי", "maxzoom": 13, "tiles": 3056, "cached": 0, "missing": 3056,
                   "bytes_estimate": 76_000_000, "seconds_estimate": 611},
                  {"id": "detailed", "name_he": "מפורט", "maxzoom": 15, "tiles": 40950, "cached": 0, "missing": 40950,
                   "bytes_estimate": 1_020_000_000, "seconds_estimate": 8190},
                  {"id": "full", "name_he": "מלא", "maxzoom": 16, "tiles": 158316, "cached": 0, "missing": 158316,
                   "bytes_estimate": 3_950_000_000, "seconds_estimate": 31663}]

        def job(st="running", done=1200):
            return {"state": st, "total": 40950, "done": done, "percent": round(100 * done / 40950, 1), "fetched": done,
                    "skipped": 0, "missing": 0, "failed": 0, "bytes": done * 25000, "error": None, "area": "israel",
                    "source": "govmap", "rate": 5.0, "eta_seconds": 7000}

        def handle(route):
            url = route.request.url
            if url.endswith("/api/satellite/country"):
                body = {"levels": levels, "source": "govmap"}
            elif url.endswith("/api/satellite/download") and route.request.method == "POST":
                state["posted"] = _json.loads(route.request.post_data)
                state["job"] = job()
                body = {"job": state["job"]}
                return route.fulfill(status=202, content_type="application/json", body=_json.dumps(body))
            elif url.endswith("/api/satellite/download/cancel"):
                state["job"] = job("cancelled", 1500)
                body = {"job": state["job"]}
            else:  # status
                state["polls"] += 1
                body = {"job": state["job"], "cache": {"tiles": 0, "bytes": 0, "disk_bytes": 0, "by_zoom": {}},
                        "offline": False, "sources": []}
            route.fulfill(status=200, content_type="application/json", body=_json.dumps(body))

        page.route("**/api/satellite/**", handle)
        page.route("**/tiles/satellite/**", lambda r: r.fulfill(status=204, body=""))
        page.locator("#btn-layers").click()
        page.locator("#btn-sat-country").click()
        expect(page.locator("#sat-country")).to_be_visible()
        expect(page.locator('.sat-est[data-level="detailed"]')).to_contain_text("40,950")
        expect(page.locator('.sat-est[data-level="full"]')).to_contain_text("שעות")
        self.assertEqual(page.locator("#btn-sat-country").get_attribute("aria-expanded"), "true")
        page.locator('input[name="satlevel"][value="detailed"] + span').click()
        page.screenshot(path=str(SHOTS / "sat-country-levels.png"))
        page.locator("#btn-sat-country-go").click()
        wait_js(page, "document.querySelector('#sat-save-status').textContent.includes('לכל הארץ')")
        self.assertEqual(state["posted"], {"area": "country", "level": "detailed"})
        expect(page.locator("#sat-save-progress")).to_be_visible()
        expect(page.locator("#sat-country")).to_be_hidden()
        expect(page.locator("#btn-sat-country")).to_be_hidden()
        self.assertTrue(page.locator("#toggle-satellite").is_checked())
        expect(page.locator("#btn-sat-save")).to_contain_text("עצירת ההורדה")
        page.locator("#btn-sat-save").click()
        wait_js(page, "document.querySelector('#sat-save-status').textContent.includes('ההורדה נעצרה')")
        expect(page.locator("#btn-sat-country")).to_be_visible()
        self.assert_clean(page)

    def test_chip_and_more_categories(self):
        page = self.new_page()
        page.locator('#chips [data-chip="synagogue"]').click()
        expect(page.locator(".results .result").first).to_be_visible()
        page.locator('#chips [data-chip="__more"]').click()
        expect(page.locator("#dialog")).to_contain_text("בתי כנסת ומקוואות")
        page.locator('#dialog [data-sub="mikveh"]').click()
        expect(page.locator("#panel")).to_contain_text("מקווה")
        self.assert_clean(page)

    def test_geolocation_button(self):
        page = self.new_page()
        page.locator("#btn-locate").click()
        expect(page.locator("#btn-locate")).to_have_attribute("aria-pressed", "true")
        n = page.evaluate("window.__sourceData.location.features.length")
        self.assertEqual(n, 1)
        self.assert_clean(page)

    def test_deep_link_restores_item(self):
        page = self.new_page()
        page.goto(self.api.url("/map.html?nosw=1#map=16.00/31.77620/35.22830&item=poi-19"))
        wait_js(page, "window.__map")
        expect(page.locator("#panel-title")).to_be_visible()
        self.assertTrue(page.locator("#panel-title").inner_text())
        self.assert_clean(page)

    def test_mobile_bottom_sheet(self):
        page = self.new_page(mobile=True)
        page.screenshot(path=str(SHOTS / "10-mobile-initial.png"))
        self.assertLessEqual(page.evaluate("document.querySelector('#chips').scrollWidth - document.querySelector('#chips').clientWidth"), 1)
        self.assertGreater(page.locator("#chips [data-chip][hidden]").count(), 0)
        expect(page.locator('#chips [data-chip="__more"]')).to_be_visible()
        page.locator("#q").fill("בתי קפה")
        page.locator("#q").press("Enter")
        expect(page.locator(".results")).to_be_visible()
        box = page.locator("#panel").bounding_box()
        self.assertGreater(box["y"], 250)  # bottom sheet, not a side panel
        page.wait_for_timeout(400)  # let the sheet animation settle
        ctrl = page.locator("#btn-layers").bounding_box()
        self.assertLess(ctrl["y"] + ctrl["height"], page.locator("#panel").bounding_box()["y"])  # controls above the sheet
        page.screenshot(path=str(SHOTS / "11-mobile-results.png"))
        page.locator("#sheet-toggle").click()
        self.assertEqual(page.locator("#panel").get_attribute("data-snap"), "full")
        page.locator(".results .result button").first.click()
        expect(page.locator("#panel-title")).to_have_text("ארומה")
        page.screenshot(path=str(SHOTS / "12-mobile-card.png"))
        issues = page.evaluate(AUDIT_JS)
        self.assertEqual(issues, [])
        self.assert_clean(page)

    def test_full_basemap_styles_validate(self):
        """With an OpenMapTiles basemap present, every layer of all three themes must validate."""
        from tests.helpers import make_mbtiles
        data = Path(tempfile.mkdtemp(prefix="hmaps-ui-base-"))
        shutil.copy(sample_db(), data / "maps.db")
        make_mbtiles(data / "tiles" / "israel.mbtiles")
        api = ApiServer(data, self.osrm_port)
        try:
            orig = self.api
            self.api = api
            page = self.new_page()
            self.api = orig
            self.assertEqual(page.evaluate("window.__lastStyle.sources.openmaptiles.type"), "vector")
            n_layers = page.evaluate("window.__lastStyle.layers.length")
            self.assertGreater(n_layers, 30)
            expect(page.locator("#banner")).to_be_hidden()
            for theme in ("nature", "dark", "light"):
                page.locator("#btn-layers").click() if page.locator("#layers-pop").is_hidden() else None
                page.locator(f'input[name="maptype"][value="{theme}"] + .swatch').click()
                wait_js(page, f"window.__lastStyle.name === 'hmaps-{theme}'")
            self.assert_clean(page)
        finally:
            api.stop()

    def test_dark_mode_desktop_card(self):
        page = self.new_page(dark=True)
        page.locator("#q").fill("מגדל דוד")
        page.locator("#q").press("Enter")
        expect(page.locator("#panel-title")).to_be_visible()
        if page.locator(".results").count():
            page.locator(".results .result button").first.click()
        expect(page.locator("#panel")).to_contain_text("02-6265333")
        page.screenshot(path=str(SHOTS / "13-dark-card.png"))
        self.assertTrue(page.evaluate("window.__lastStyle.name").endswith("dark"))
        self.assert_clean(page)


if __name__ == "__main__":
    unittest.main()
