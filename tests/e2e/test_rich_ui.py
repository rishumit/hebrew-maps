"""Browser test of the rich site / trail cards: gallery + viewer, long texts, facts, nearby lists.

The photos are local stand-ins (served from /media/<key>, exactly like images
downloaded by tools/media.py), so the test needs no internet.

    python -m unittest tests.e2e.test_rich_ui
"""

import io
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from hmaps.core import media
from hmaps.etl import rich as rich_etl
from tests.e2e.test_ui import AUDIT_JS, HERE, SHOTS, wait_js
from tests.helpers import ApiServer, sample_db

COLORS = [(66, 135, 97), (196, 160, 98), (88, 132, 189), (161, 92, 74), (120, 120, 60), (60, 90, 130)]


def _jpeg(i: int) -> bytes:
    from PIL import Image, ImageDraw

    im = Image.new("RGB", (800, 530), COLORS[i % len(COLORS)])
    d = ImageDraw.Draw(im)
    for k in range(12):
        d.ellipse((40 * k + (i * 7) % 40, 300 - 15 * k, 40 * k + 200, 420 + 5 * k), fill=tuple(min(255, c + 40) for c in COLORS[(i + k) % 6]))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=80)
    return buf.getvalue()


def make_rich_data(data: Path) -> dict:
    """Sample DB + rich rows for שמורת עין גדי (gallery, texts, facts) and שביל ישראל (photos, article)."""
    db = sample_db()
    shutil.copy(db, data / "maps.db")
    shutil.copytree(db.parent / "reports", data / "reports")
    con = sqlite3.connect(data / "maps.db")
    rich_etl.ensure_tables(con)
    site = con.execute("SELECT id FROM pois WHERE name = 'שמורת עין גדי'").fetchone()[0]
    trail = con.execute("SELECT id FROM trails WHERE name = 'שביל ישראל'").fetchone()[0]
    (data / "media").mkdir(exist_ok=True)
    for entity, ref, n in (("poi", site, 5), ("trail", trail, 3)):
        for i in range(n):
            file = f"Test_{entity}_{i}.jpg"
            (data / "media" / media.key("https://commons.wikimedia.org/wiki/Special:FilePath/" + file)).write_bytes(_jpeg(i + ref))
            con.execute("INSERT INTO media (entity, ref_id, rank, source, file, width, height, title, author, license,"
                        " license_url, page_url) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (entity, ref, i, "commons", file, 800, 530, f"נוף {i + 1}", "צלם לדוגמה", "CC BY-SA 4.0",
                         "https://creativecommons.org/licenses/by-sa/4.0/", f"https://commons.wikimedia.org/wiki/File:{file}"))
    con.execute("INSERT INTO texts VALUES ('poi', ?, 'wikipedia', 'עין גדי', ?, 'https://he.wikipedia.org/wiki/עין_גדי', 'CC BY-SA 4.0')",
                (site, "שמורת עין גדי היא שמורת טבע בחוף המערבי של ים המלח.\n\n"
                       + "בשמורה שני נחלים עם מים זורמים כל השנה, נחל דוד ונחל ערוגות, ומעיינות רבים. " * 4
                       + "\n\nפסקה שלישית על הצומח והחי בשמורה."))
    con.execute("INSERT INTO texts VALUES ('poi', ?, 'wikivoyage', 'עין גדי', 'מדריך קצר למטייל: מומלץ להגיע מוקדם בבוקר.', "
                "'https://he.wikivoyage.org/wiki/עין_גדי', 'CC BY-SA 4.0')", (site,))
    con.executemany("INSERT INTO facts VALUES ('poi', ?, ?, ?)",
                    [(site, "heritage", "שמורת טבע"), (site, "inception", "1971"), (site, "area", "14.3"),
                     (site, "commons_category", "Ein Gedi")])
    # a spring right on the trail, for "לאורך השביל"
    con.execute("INSERT INTO pois (id, name, category, sub, lat, lon, importance) VALUES (9001, 'עין לדוגמה', 'nature', 'spring', 31.44, 35.38, 0.5)")
    con.execute("INSERT INTO pois_rtree VALUES (9001, 35.38, 35.38, 31.44, 31.44)")
    con.execute("INSERT INTO texts VALUES ('trail', ?, 'wikipedia', 'שביל ישראל', 'שביל ישראל הוא שביל הליכה ארצי.', "
                "'https://he.wikipedia.org/wiki/שביל_ישראל', 'CC BY-SA 4.0')", (trail,))
    con.commit()
    con.close()
    return {"site": site, "trail": trail}


class RichUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        SHOTS.mkdir(parents=True, exist_ok=True)
        cls.data = Path(tempfile.mkdtemp(prefix="hmaps-rich-"))
        cls.ids = make_rich_data(cls.data)
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
        page.goto(self.api.url("/map.html?nosw=1"))
        page.wait_for_selector('#chips [data-chip="__more"]')
        wait_js(page, "!!window.__map")
        return page

    def tearDown(self):
        for ctx in self.browser.contexts:
            ctx.close()

    def open_item(self, page, item_id, name):
        page.evaluate(f"location.hash = 'item={item_id}'")
        expect(page.locator("#panel-title")).to_have_text(name)

    def test_site_card(self):
        page = self.page()
        self.open_item(page, f"poi-{self.ids['site']}", "שמורת עין גדי")
        strip = page.locator(".g-strip img")
        expect(strip).to_have_count(5)
        wait_js(page, "[...document.querySelectorAll('.g-strip img')].every(i => i.complete && i.naturalWidth > 0)")
        expect(page.locator(".gallery .card-credit")).to_contain_text("צלם לדוגמה")
        expect(page.locator(".rich-text")).to_have_count(2)
        expect(page.locator(".rich-text").first).to_contain_text("על המקום")
        expect(page.locator(".rich-text").last).to_contain_text("עוד מויקימסע")
        expect(page.locator(".info-rows")).to_contain_text("הוקם / נוסד: 1971")
        expect(page.locator(".info-rows")).to_contain_text("מעמד: שמורת טבע")
        expect(page.locator(".results.nearby").first).to_contain_text("שביל ישראל")
        expect(page.locator("text=עוד תמונות בוויקישיתוף")).to_be_visible()
        michlol = page.locator('a[href*="hamichlol.org.il"]')
        expect(michlol).to_have_count(1)
        self.assertIn("search=%D7%A9%D7%9E%D7%95%D7%A8%D7%AA", michlol.get_attribute("href"))
        self.assertEqual(page.evaluate(AUDIT_JS), [])
        page.screenshot(path=str(SHOTS / "rich-01-site.png"))
        # read more
        more = page.locator('.rich-text [data-more]').last
        if more.count():
            more.click()
            expect(more).to_have_attribute("aria-expanded", "true")
        page.locator(".rich-text").last.scroll_into_view_if_needed()
        page.screenshot(path=str(SHOTS / "rich-02-site-texts.png"))
        # viewer
        page.locator(".g-strip .g-open").nth(1).click()
        viewer = page.locator("dialog.g-viewer")
        expect(viewer).to_be_visible()
        expect(viewer.locator(".gv-pos")).to_have_text("2 / 5")
        page.keyboard.press("ArrowLeft")
        expect(viewer.locator(".gv-pos")).to_have_text("3 / 5")
        expect(viewer.locator(".gv-credit")).to_contain_text("CC BY-SA 4.0")
        page.screenshot(path=str(SHOTS / "rich-03-viewer.png"))
        page.keyboard.press("Escape")
        expect(viewer).to_have_count(0)
        self.assertEqual(self.errors, [])

    def test_trail_card(self):
        page = self.page()
        self.open_item(page, f"trail-{self.ids['trail']}", "שביל ישראל")
        expect(page.locator(".g-strip img")).to_have_count(3)
        expect(page.locator(".info-rows")).to_contain_text("זמן הליכה משוער")
        along = page.locator(".results.nearby")
        expect(along).to_contain_text("עין לדוגמה")
        expect(page.locator(".rich-text")).to_contain_text("שביל הליכה ארצי")
        self.assertEqual(page.evaluate(AUDIT_JS), [])
        page.screenshot(path=str(SHOTS / "rich-04-trail.png"))
        along.locator("button").first.click()
        expect(page.locator("#panel-title")).to_have_text("עין לדוגמה")
        self.assertEqual(self.errors, [])

    def test_site_card_mobile_dark(self):
        page = self.page(mobile=True, dark=True)
        self.open_item(page, f"poi-{self.ids['site']}", "שמורת עין גדי")
        expect(page.locator(".g-strip img")).to_have_count(5)
        page.wait_for_timeout(300)
        page.screenshot(path=str(SHOTS / "rich-05-site-mobile-dark.png"))
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()
