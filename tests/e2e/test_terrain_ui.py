"""Browser tests of the topographic style and the elevation profile (synthetic DEM around Tel Aviv).

    python -m unittest tests.e2e.test_terrain_ui
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests import fake_osrm
from tests.e2e import test_ui as base
from tests.e2e.test_ui import AUDIT_JS, SHOTS, wait_js
from tests.helpers import ApiServer, free_port, make_mbtiles, sample_db
from tests.test_terrain import make_grid
from tools import terrain as T


class TerrainUiTest(unittest.TestCase):
    new_page = base.UiTest.new_page  # same page setup (MapLibre stub that validates every style layer)
    tearDown = base.UiTest.tearDown
    assert_clean = base.UiTest.assert_clean

    @classmethod
    def setUpClass(cls):
        SHOTS.mkdir(parents=True, exist_ok=True)
        cls.tmp = Path(tempfile.mkdtemp(prefix="hmaps-ui-terrain-"))
        grid = make_grid(cls.tmp, west=34.68, north=32.17)
        cls.data = cls.tmp / "data"
        (cls.data / "tiles").mkdir(parents=True)
        shutil.copy(sample_db(), cls.data / "maps.db")
        bbox = (34.72, 32.0, 34.85, 32.12)
        T.build_contours(grid, cls.data / "tiles" / "contours.mbtiles", bbox, {12: (50, 100)})
        T.build_terrain(grid, cls.data / "tiles" / "terrain.mbtiles", bbox, range(11, 13))
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
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _pick_topo(self, page):
        page.locator("#btn-layers").click()
        expect(page.locator("#layers-pop")).to_be_visible()
        page.locator('input[name="maptype"][value="topo"] + .swatch').click()

    def test_topo_style_on_fallback_basemap(self):
        page = self.new_page()
        self._pick_topo(page)
        wait_js(page, "window.__lastStyle.name === 'hmaps-fallback-topo'")
        ids = page.evaluate("window.__lastStyle.layers.map((l) => l.id)")
        for lid in ("hillshade", "contour-minor", "contour-index", "contour-label", "spot-height"):
            self.assertIn(lid, ids)
        self.assertLess(ids.index("hillshade"), ids.index("streets-motorway"))
        self.assertGreater(ids.index("contour-label"), ids.index("streets-motorway"))
        src = page.evaluate("window.__lastStyle.sources")
        self.assertEqual(src["terrain-dem"]["type"], "raster-dem")
        self.assertEqual(src["terrain-dem"]["encoding"], "terrarium")
        self.assertIn("/tiles/contours/", src["contours"]["tiles"][0])
        page.screenshot(path=str(SHOTS / "20-topo-layers.png"))
        self.assert_clean(page)

    def test_foot_route_elevation_profile(self):
        page = self.new_page()
        page.evaluate("window.__map.__clickPoi({id: 2, name: 'x'}, [34.7752, 32.0753])")
        expect(page.locator("#panel-title")).to_have_text("דיזנגוף סנטר")
        page.locator('#panel [data-act="route"]').click()
        frm = page.locator("#dir-from")
        frm.fill("")
        frm.type("רוטש", delay=30)
        page.locator("#dir-from-list [role=option]").first.click()
        expect(page.locator(".steps .step").first).to_be_visible()
        expect(page.locator("#elev-profile")).to_have_count(0)  # by car: no profile
        page.locator('.mode[data-mode="foot"]').click()
        expect(page.locator("#elev-profile .elev-chart")).to_be_visible()
        expect(page.locator("#elev-profile .elev-stats")).to_contain_text("עלייה")
        expect(page.locator("#elev-profile .sr-only")).to_contain_text("עלייה מצטברת")
        expect(page.locator("#elev-profile svg")).to_have_attribute("role", "img")
        page.screenshot(path=str(SHOTS / "21-foot-elevation.png"))
        self.assertEqual(page.evaluate(AUDIT_JS), [])
        self.assert_clean(page)

    def test_topo_style_on_full_basemap(self):
        data = Path(tempfile.mkdtemp(prefix="hmaps-ui-topo-base-"))
        shutil.copytree(self.data, data, dirs_exist_ok=True)
        make_mbtiles(data / "tiles" / "israel.mbtiles")
        api = ApiServer(data, self.osrm_port)
        try:
            orig = self.api
            self.api = api
            page = self.new_page()
            self.api = orig
            self._pick_topo(page)
            wait_js(page, "window.__lastStyle.name === 'hmaps-topo'")
            ids = page.evaluate("window.__lastStyle.layers.map((l) => l.id)")
            self.assertLess(ids.index("hillshade"), ids.index("road-motorway"))
            self.assertLess(ids.index("contour-label"), ids.index("peak"))
            self.assertIn("openmaptiles", page.evaluate("Object.keys(window.__lastStyle.sources)"))
            self.assert_clean(page)
        finally:
            api.stop()
            shutil.rmtree(data, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
