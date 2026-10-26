"""Browser tests of the desktop shell (web/desktop.html): tabs in the title bar, one map iframe per tab.

    python -m unittest tests.e2e.test_desktop_tabs_ui
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


def wait_js(page, expr, timeout=8.0):
    """Poll a JS expression (wait_for_function would need 'unsafe-eval', which our CSP forbids)."""
    import time
    end = time.time() + timeout
    while time.time() < end:
        if page.evaluate(f"() => {{ try {{ return !!({expr}); }} catch (e) {{ return false; }} }}"):
            return
        page.wait_for_timeout(50)
    raise AssertionError(f"timeout waiting for {expr}")


class DesktopTabsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        SHOTS.mkdir(parents=True, exist_ok=True)
        cls.data = Path(tempfile.mkdtemp(prefix="hmaps-desk-"))
        db = sample_db()
        shutil.copy(db, cls.data / "maps.db")
        shutil.copytree(db.parent / "reports", cls.data / "reports")
        osrm_port = free_port()
        cls.osrm = fake_osrm.serve(osrm_port)
        cls.api = ApiServer(cls.data, osrm_port)
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.api.stop()
        cls.osrm.shutdown()

    def setUp(self):
        # no service worker: it would serve the map iframes' vendor files past the routes below
        self.ctx = self.browser.new_context(viewport={"width": 1280, "height": 760}, locale="he-IL", service_workers="block",
            permissions=["geolocation"], geolocation={"latitude": 32.078, "longitude": 34.775})
        stub = (HERE / "maplibre-stub.js").read_text(encoding="utf-8")
        # context-wide, so the map iframes get the stub too
        self.ctx.route("**/vendor/maplibre-gl.js", lambda r: r.fulfill(body=stub, content_type="application/javascript"))
        self.ctx.route("**/vendor/maplibre-gl.css", lambda r: r.fulfill(body="", content_type="text/css"))
        self.ctx.route("**/vendor/mapbox-gl-rtl-text.js", lambda r: r.fulfill(body="", content_type="application/javascript"))
        self.ctx.route("**/fonts-ui/**", lambda r: r.fulfill(status=404, body=""))
        self.errors = []

    def tearDown(self):
        self.ctx.close()

    def open(self, path="/desktop.html"):
        page = self.ctx.new_page()
        page.on("pageerror", lambda e: self.errors.append(str(e)))
        page.on("console", lambda m: m.type == "error" and self.errors.append(m.text))
        page.goto(self.api.url(path))
        page.wait_for_selector(".tab.active")
        self.wait_map(page)
        return page

    def active_frame(self, page):
        return page.frame_locator(".view.active iframe")

    def wait_map(self, page):
        wait_js(page, "document.querySelector('.view.active iframe')?.contentWindow?.__map")
        wait_js(page, "!document.querySelector('.tab.active').classList.contains('loading')")

    def titles(self, page):
        return page.locator(".tab .tab-title").all_inner_texts()

    def assert_clean(self):
        real = [e for e in self.errors if "404" not in e and "Failed to load resource" not in e]
        self.assertEqual(real, [], "console errors")

    # ------------------------------------------------------------------ tests
    def test_tabs_open_switch_close_and_titles(self):
        page = self.open()
        expect(page.locator("html")).to_have_attribute("dir", "rtl")
        expect(page.locator("#winctl")).to_be_hidden()  # not in Tauri
        expect(page.locator(".tab")).to_have_count(1)
        self.assertEqual(self.titles(page), ["מפה"])
        expect(page).to_have_title("מפות בעברית")

        # search -> tab title + document titles
        f = self.active_frame(page)
        f.locator("#q").fill("מסעדות בתל אביב")
        f.locator("#q").press("Enter")
        expect(f.locator(".results .result").first).to_be_visible()
        expect(page.locator(".tab.active .tab-title")).to_have_text("מסעדות בתל אביב")
        expect(page).to_have_title("מסעדות בתל אביב – מפות בעברית")
        expect(page.locator(".tab.active")).to_have_attribute("data-kind", "search")  # favicon follows the map's state
        wait_js(page, "JSON.parse(localStorage.getItem('hmaps:desktop-tabs')).tabs[0].hash.includes('q=')")

        # new tab (button), select a place
        page.locator("#btn-new-tab").click()
        expect(page.locator(".tab")).to_have_count(2)
        self.wait_map(page)
        expect(page.locator(".tab.active .tab-title")).to_have_text("מפה")
        wait_js(page, "document.querySelector('.view.active iframe').contentDocument.activeElement?.id === 'q'")
        page.evaluate("document.querySelector('.view.active iframe').contentWindow.__map.__clickPoi({id: 2, name: 'x'}, [34.7752, 32.0753])")
        f = self.active_frame(page)
        expect(f.locator("#panel-title")).to_have_text("דיזנגוף סנטר")
        expect(page.locator(".tab.active .tab-title")).to_have_text("דיזנגוף סנטר")
        expect(page.locator(".tab.active")).to_have_attribute("data-kind", "place")

        # directions -> "A ← B"
        f.locator('#panel [data-act="route"]').click()
        frm = f.locator("#dir-from")
        frm.fill("")
        frm.type("רוטש", delay=30)
        f.locator("#dir-from-list [role=option]").first.click()
        expect(f.locator(".steps .step").first).to_be_visible()
        expect(page.locator(".tab.active .tab-title")).to_contain_text("← דיזנגוף סנטר")
        expect(page.locator(".tab.active")).to_have_attribute("data-kind", "directions")
        page.screenshot(path=str(SHOTS / "desktop-01-tabs.png"))

        # only the active map is visible; the other keeps its state
        self.assertEqual(page.locator(".view.active").count(), 1)
        page.locator(".tab").first.click()
        expect(self.active_frame(page).locator(".results .result").first).to_be_visible()
        self.assertEqual(page.evaluate("getComputedStyle(document.querySelectorAll('.view')[1]).visibility"), "hidden")

        # keyboard: Ctrl+T, Ctrl+Tab, Ctrl+Shift+Tab, Ctrl+1/9, Ctrl+W (also from inside the map)
        page.keyboard.press("Control+t")
        expect(page.locator(".tab")).to_have_count(3)
        self.wait_map(page)
        page.keyboard.press("Control+1")
        expect(page.locator(".tab").nth(0)).to_have_class("tab active")
        page.keyboard.press("Control+Tab")
        expect(page.locator(".tab").nth(1)).to_have_class("tab active")
        page.keyboard.press("Control+Shift+Tab")
        expect(page.locator(".tab").nth(0)).to_have_class("tab active")
        page.keyboard.press("Control+9")
        expect(page.locator(".tab").nth(2)).to_have_class("tab active")
        self.wait_map(page)
        self.active_frame(page).locator("#q").click()
        page.keyboard.press("Control+w")  # focus inside the iframe
        expect(page.locator(".tab")).to_have_count(2)
        expect(page.locator(".tab").nth(1)).to_have_class("tab active")
        page.keyboard.press("Control+Shift+t")  # reopen
        expect(page.locator(".tab")).to_have_count(3)

        # close button and middle click
        page.locator(".tab").nth(2).locator(".tab-close").click()
        expect(page.locator(".tab")).to_have_count(2)
        page.locator(".tab").nth(1).click(button="middle")
        expect(page.locator(".tab")).to_have_count(1)
        self.assertEqual(page.locator("iframe").count(), 1)
        self.assert_clean()

    def test_restore_tabs_after_reload(self):
        page = self.open()
        f = self.active_frame(page)
        f.locator("#q").fill("מסעדות בתל אביב")
        f.locator("#q").press("Enter")
        expect(page.locator(".tab.active .tab-title")).to_have_text("מסעדות בתל אביב")
        page.locator("#btn-new-tab").click()
        self.wait_map(page)
        page.evaluate("document.querySelector('.view.active iframe').contentWindow.__map.__clickPoi({id: 2, name: 'x'}, [34.7752, 32.0753])")
        expect(page.locator(".tab.active .tab-title")).to_have_text("דיזנגוף סנטר")
        wait_js(page, "JSON.parse(localStorage.getItem('hmaps:desktop-tabs')).tabs[1].hash.includes('item=')")

        page.reload()
        page.wait_for_selector(".tab.active")
        self.wait_map(page)
        # (retrying: while it loads, the restored map may report a default title for a moment)
        expect(page.locator(".tab .tab-title")).to_have_text(["מסעדות בתל אביב", "דיזנגוף סנטר"])
        expect(page.locator(".tab").nth(1)).to_have_class("tab active")
        self.assertEqual(page.evaluate("[...document.querySelectorAll('.tab')].map(t => t.dataset.kind)"), ["search", "place"])
        expect(self.active_frame(page).locator("#panel-title")).to_have_text("דיזנגוף סנטר")
        # the background tab loads lazily, on first activation
        self.assertEqual(page.locator("iframe").count(), 1)
        page.locator(".tab").first.click()
        self.wait_map(page)
        expect(self.active_frame(page).locator(".results .result").first).to_be_visible()
        self.assertEqual(page.locator("iframe").count(), 2)
        self.assert_clean()

    def test_map_links_open_in_a_new_tab(self):
        page = self.open()
        page.evaluate("""() => {
          const d = document.querySelector('.view.active iframe').contentDocument;
          const a = d.createElement('a');
          a.href = '/map.html#map=15.00/31.77670/35.23450&q=' + encodeURIComponent('ירושלים');
          a.target = '_blank'; a.id = 'x-link'; a.textContent = 'x';
          d.body.append(a);
          a.click();
        }""")
        expect(page.locator(".tab")).to_have_count(2)
        self.wait_map(page)
        wait_js(page, "document.querySelector('.view.active iframe').contentWindow.location.hash.includes('q=')")
        self.assertEqual(len(self.ctx.pages), 1)  # no browser window/tab was opened
        # desktop.html#… opens that map in a tab
        page2 = self.open("/desktop.html#map=12.00/32.07000/34.78000")
        wait_js(page2, "document.querySelector('.view.active iframe').contentWindow.location.hash.includes('32.07')")
        self.assertEqual(page2.evaluate("location.hash"), "")
        self.assert_clean()

    def test_chrome_style_strip_states(self):
        page = self.open()
        for _ in range(3):
            page.locator("#btn-new-tab").click()
        expect(page.locator(".tab")).to_have_count(4)
        self.wait_map(page)
        page.locator(".tab").nth(1).click()
        expect(page.locator(".tab").nth(1)).to_have_class("tab active")
        page.mouse.move(5, 300)  # nothing hovered
        page.wait_for_timeout(200)
        sep = "[...document.querySelectorAll('.tab')].map(t => getComputedStyle(t, '::before'))"
        # separators: none before the first tab, hidden on both sides of the active tab, shown elsewhere
        ops = page.evaluate(f"{sep}.map(s => s.content === 'none' ? null : +s.opacity)")
        self.assertEqual(ops, [None, 0, 0, 1])
        # the active tab is the content colour, has the concave feet, and its close button is always shown;
        # background tabs are transparent and show their close button on hover only
        st = page.evaluate("""() => {
          const [a, b] = [document.querySelector('.tab.active'), document.querySelectorAll('.tab')[3]];
          const cs = (e, p) => getComputedStyle(e, p);
          return {
            activeBg: cs(a).backgroundColor, inactiveBg: cs(b).backgroundColor,
            body: cs(document.body).backgroundColor,
            feet: [...a.querySelectorAll('.foot')].map(f => cs(f).display),
            bFeet: [...b.querySelectorAll('.foot')].map(f => cs(f).display),
            aClose: cs(a.querySelector('.tab-close')).opacity, bClose: cs(b.querySelector('.tab-close')).opacity,
            radius: cs(a).borderTopLeftRadius,
          };
        }""")
        self.assertEqual(st["activeBg"], "rgb(255, 255, 255)")
        self.assertEqual(st["activeBg"], st["body"])
        self.assertEqual(st["inactiveBg"], "rgba(0, 0, 0, 0)")
        self.assertEqual(st["feet"], ["block", "block"])
        self.assertEqual(st["bFeet"], ["none", "none"])
        self.assertEqual((st["aClose"], st["bClose"]), ("1", "0"))
        self.assertEqual(st["radius"], "8px")
        # hover on a background tab: the pill shows, its close button appears, the separators around it hide
        page.locator(".tab").nth(3).hover()
        page.wait_for_timeout(250)
        self.assertEqual(page.evaluate("getComputedStyle(document.querySelectorAll('.tab')[3], '::after').opacity"), "1")
        self.assertEqual(page.evaluate("getComputedStyle(document.querySelectorAll('.tab')[3].querySelector('.tab-close')).opacity"), "1")
        self.assertEqual(page.evaluate(f"{sep}[3].opacity"), "0")
        # the active tab's bottom meets the map: no gap between the bar and the content
        geo = page.evaluate("""() => ({tab: document.querySelector('.tab.active').getBoundingClientRect().bottom,
            view: document.querySelector('.view.active').getBoundingClientRect().top})""")
        self.assertEqual(geo["tab"], geo["view"])
        # narrow tabs: titles go, background tabs keep only the favicon, the active one keeps its close button
        for _ in range(12):
            page.locator("#btn-new-tab").click()
        expect(page.locator(".tab")).to_have_count(16)
        page.set_viewport_size({"width": 900, "height": 760})
        page.mouse.move(5, 300)
        page.wait_for_timeout(300)
        nar = page.evaluate("""() => {
          const tabs = [...document.querySelectorAll('.tab')];
          const a = document.querySelector('.tab.active'), b = tabs.find(t => t !== a);
          const d = (e) => getComputedStyle(e).display;
          return {w: Math.max(...tabs.map(t => t.getBoundingClientRect().width)),
            title: d(b.querySelector('.tab-title')), close: d(b.querySelector('.tab-close')),
            icon: d(b.querySelector('.tab-icon')), aClose: d(a.querySelector('.tab-close'))};
        }""")
        self.assertLessEqual(nar["w"], 72)
        self.assertEqual((nar["title"], nar["close"], nar["icon"], nar["aClose"]), ("none", "none", "grid", "grid"))
        # a close animation leaves no stray elements behind
        page.locator(".tab.active .tab-close").click()
        expect(page.locator(".tab")).to_have_count(15)
        self.assertEqual(page.locator('[role="tab"]').count(), 15)
        self.assert_clean()

    def test_overflow_reorder_keyboard_and_dark(self):
        page = self.open()
        for _ in range(13):
            page.locator("#btn-new-tab").click()
        expect(page.locator(".tab")).to_have_count(14)
        self.wait_map(page)
        # tabs shrink first, then the strip scrolls; the "+" stays reachable
        widths = page.evaluate("[...document.querySelectorAll('.tab')].map(t => t.getBoundingClientRect().width)")
        self.assertLess(max(widths), 120)
        self.assertGreaterEqual(min(widths), 47)
        expect(page.locator("#btn-new-tab")).to_be_in_viewport()
        # live maps are capped (background tabs unloaded, reloaded from their hash)
        self.assertLessEqual(page.locator("iframe").count(), 8)
        page.screenshot(path=str(SHOTS / "desktop-02-many-tabs.png"))
        # keyboard on the tab list (RTL: ArrowLeft = next)
        page.locator(".tab.active .tab-main").focus()
        page.keyboard.press("Home")
        expect(page.locator(".tab").nth(0)).to_have_class("tab active")
        page.keyboard.press("ArrowLeft")
        expect(page.locator(".tab").nth(1)).to_have_class("tab active")
        expect(page.locator(".tab").nth(1).locator(".tab-main")).to_be_focused()
        page.keyboard.press("Delete")
        expect(page.locator(".tab")).to_have_count(13)
        # drag to reorder: move the first tab two places to the left
        ids = page.evaluate("[...document.querySelectorAll('.tab')].map(t => t.dataset.id)")
        page.keyboard.press("Home")
        first = page.locator(".tab").nth(0).bounding_box()
        third = page.locator(".tab").nth(2).bounding_box()
        page.mouse.move(first["x"] + first["width"] / 2, first["y"] + 10)
        page.mouse.down()
        page.mouse.move(first["x"] + first["width"] / 2 - 10, first["y"] + 10, steps=2)
        page.mouse.move(third["x"] + third["width"] / 2 - 4, first["y"] + 10, steps=8)
        page.mouse.up()
        after = page.evaluate("[...document.querySelectorAll('.tab')].map(t => t.dataset.id)")
        self.assertEqual(after[2], ids[0])
        self.assertEqual(sorted(after), sorted(ids))
        # closing tabs down to one, then a fresh map remains
        for _ in range(13):
            page.keyboard.press("Control+w")
        expect(page.locator(".tab")).to_have_count(1)
        self.wait_map(page)
        # theme follows the map's "מראה הממשק" (shared localStorage)
        f = self.active_frame(page)
        f.locator("#btn-layers").click()
        f.locator('input[name="uitheme"][value="dark"]').check(force=True)
        expect(page.locator("html")).to_have_attribute("data-theme", "dark")
        f.locator("#q").fill("מסעדות בתל אביב")
        f.locator("#q").press("Enter")
        page.locator("#btn-new-tab").click()
        self.wait_map(page)
        page.locator(".tab").first.hover()
        page.screenshot(path=str(SHOTS / "desktop-03-dark.png"))
        self.assert_clean()


if __name__ == "__main__":
    unittest.main()
