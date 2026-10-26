import unittest

from tools import build_changelog as bc


class ChangelogTest(unittest.TestCase):
    def test_site_page_is_up_to_date(self):
        """site/changelog.html must contain every docs/release-notes/vX.md (run tools/build_changelog.py)."""
        self.assertEqual(bc.build(), bc.PAGE.read_text(encoding="utf-8"))

    def test_render_escapes_and_links(self):
        out = bc.render([{"version": "9.9.9", "title": "כותרת <b>", "date": "2026-11-05",
                          "items": ["קישור [כאן](download.html#offline) ו-`code`"]}])
        self.assertIn("נובמבר 2026", out)
        self.assertIn("&lt;b&gt;", out)
        self.assertIn('<a href="download.html#offline">כאן</a>', out)
        self.assertIn('<code dir="ltr">code</code>', out)
