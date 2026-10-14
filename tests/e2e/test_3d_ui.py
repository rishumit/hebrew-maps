"""3D mode: tilt, terrain (raster-dem + setTerrain), extruded buildings, card buttons, persistence.

The API's /api/meta is rewritten in the browser to announce a DEM and an OpenMapTiles
basemap, so the full 3D path runs against the MapLibre stub without building terrain data.

    python -m unittest tests.e2e.test_3d_ui
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests.e2e.test_ui import AUDIT_JS, HERE, SHOTS, wait_js
from tests.helpers import ApiServer, sample_db


class ThreeDTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        SHOTS.mkdir(parents=True, exist_ok=True)
        cls.data = Path(tempfile.mkdtemp(prefix="hmaps-3d-"))
        db = sample_db()
        shutil.copy(db, cls.data / "maps.db")
        shutil.copytree(db.parent / "reports", cls.data / "reports")
        cls.api = ApiServer(cls.data)
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.api.stop()

    def page(self, terrain=True, storage=None):
        ctx = self.browser.new_context(viewport={"width": 1366, "height": 860}, locale="he-IL")
        page = ctx.new_page()
        stub = (HERE / "maplibre-stub.js").read_text(encoding="utf-8")
        page.route("**/vendor/maplibre-gl.js", lambda r: r.fulfill(body=stub, content_type="application/javascript"))
        page.route("**/vendor/maplibre-gl.css", lambda r: r.fulfill(body="", content_type="text/css"))
        page.route("**/vendor/mapbox-gl-rtl-text.js", lambda r: r.fulfill(body="", content_type="application/javascript"))
        page.route("**/fonts-ui/**", lambda r: r.fulfill(status=404, body=""))

        def meta(route):
            body = route.fetch().json()
            base = self.api.url("")
            body["basemap"].update(type="openmaptiles", tilejson=f"{base}/tiles/base.json")
            if terrain:
                body["terrain"]["dem"] = {"tiles": f"{base}/tiles/terrain/{{z}}/{{x}}/{{y}}.png", "minzoom": 5, "maxzoom": 12,
                                          "bounds": [34.1, 29.3, 36, 33.5], "encoding": "terrarium"}
                body["terrain"]["elevation"] = True
            route.fulfill(body=json.dumps(body), content_type="application/json")
        page.route("**/api/meta", meta)
        if storage:
            page.add_init_script(f"for (const [k, v] of Object.entries({json.dumps(storage)})) localStorage.setItem('hmaps:' + k, JSON.stringify(v));")
        self.errors = []
        page.on("pageerror", lambda e: self.errors.append(str(e)))
        page.goto(self.api.url("/?nosw=1"))
        page.wait_for_selector('#chips [data-chip="__more"]')
        wait_js(page, "!!window.__map")
        page.wait_for_timeout(100)
        return page

    def tearDown(self):
        for ctx in self.browser.contexts:
            ctx.close()

    def layer_ids(self, page):
        return page.evaluate("window.__map._layers.map(l => l.id)")

    def test_toggle_with_terrain(self):
        page = self.page()
        btn = page.locator("#btn-3d")
        expect(btn).to_have_attribute("aria-pressed", "false")
        self.assertEqual(page.evaluate("window.__map.getMaxPitch()"), 0)  # no tilting in 2D
        btn.click()
        expect(btn).to_have_attribute("aria-pressed", "true")
        wait_js(page, "window.__map.getPitch() === 60")
        self.assertEqual(page.evaluate("window.__terrain && window.__terrain.source"), "terrain-3d")
        ids = self.layer_ids(page)
        self.assertIn("building-3d", ids)
        self.assertIn("hillshade-3d", ids)
        self.assertLess(ids.index("hillshade-3d"), ids.index("building-3d"))
        self.assertEqual(page.evaluate("window.__map.getLayer('building').layout.visibility"), "none")
        self.assertTrue(page.evaluate("!!window.__sky"))
        self.assertEqual(page.evaluate("window.__styleErrors"), [])
        # theme change keeps 3D
        page.locator("#btn-layers").click()
        page.locator('input[name="maptype"][value="dark"]').check(force=True)
        page.wait_for_timeout(150)
        self.assertIn("building-3d", self.layer_ids(page))
        self.assertEqual(page.evaluate("window.__terrain && window.__terrain.source"), "terrain-3d")
        page.keyboard.press("Escape")
        # keyboard shortcut turns it off
        page.locator("body").click(position={"x": 700, "y": 400})
        page.keyboard.press("t")
        expect(btn).to_have_attribute("aria-pressed", "false")
        wait_js(page, "window.__map.getPitch() === 0")
        self.assertIsNone(page.evaluate("window.__terrain"))
        self.assertNotIn("building-3d", self.layer_ids(page))
        self.assertEqual(page.evaluate("window.__map.getLayer('building').layout.visibility"), "visible")
        self.assertEqual(page.evaluate(AUDIT_JS), [])
        self.assertEqual(self.errors, [])

    def test_without_terrain_notice(self):
        page = self.page(terrain=False)
        page.locator("#btn-3d").click()
        expect(page.locator(".toast")).to_contain_text("build-terrain")
        self.assertIsNone(page.evaluate("window.__terrain"))
        self.assertIn("building-3d", self.layer_ids(page))
        self.assertNotIn("hillshade-3d", self.layer_ids(page))
        self.assertEqual(self.errors, [])

    def test_persisted_and_trail_button(self):
        page = self.page(storage={"3d": True})
        expect(page.locator("#btn-3d")).to_have_attribute("aria-pressed", "true")
        wait_js(page, "window.__map.getPitch() === 60")
        page.evaluate("location.hash = 'item=trail-1'")
        expect(page.locator("#panel-title")).to_have_text("שביל ישראל")
        page.locator('[data-act="view3d"]').click()
        wait_js(page, "window.__lastCamera.pitch === 62")
        page.screenshot(path=str(SHOTS / "3d-01-trail.png"))
        page.evaluate("location.hash = 'item=poi-43'")
        expect(page.locator("#panel-title")).to_have_text("שמורת עין גדי")
        expect(page.locator('[data-act="view3d"]')).to_be_visible()
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()
