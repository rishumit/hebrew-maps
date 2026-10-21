"""App icons: generated locally from web/icons/icon.svg (tools/make_icons.py), referenced correctly."""

import json
import struct
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _png_size(path: Path) -> tuple[int, int]:
    head = path.read_bytes()[:24]
    assert head[:8] == b"\x89PNG\r\n\x1a\n", path
    return struct.unpack(">II", head[16:24])


class IconsTest(unittest.TestCase):
    def test_manifest_icons_exist_with_declared_size(self):
        man = json.loads((ROOT / "web" / "manifest.webmanifest").read_text(encoding="utf-8"))
        purposes = set()
        for icon in man["icons"]:
            path = ROOT / "web" / icon["src"].lstrip("/")
            self.assertTrue(path.is_file(), icon["src"])
            self.assertFalse(icon["src"].startswith("http"), "icons must be local")
            if icon["type"] == "image/png":
                w, h = (int(v) for v in icon["sizes"].split("x"))
                self.assertEqual(_png_size(path), (w, h), icon["src"])
            purposes.add(icon.get("purpose", "any"))
        self.assertIn("maskable", purposes)

    def test_tauri_icon_set(self):
        sizes = {"32x32.png": 32, "128x128.png": 128, "128x128@2x.png": 256, "icon.png": 512}
        for name, px in sizes.items():
            self.assertEqual(_png_size(ROOT / "src-tauri" / "icons" / name), (px, px), name)
        ico = (ROOT / "src-tauri" / "icons" / "icon.ico").read_bytes()
        self.assertEqual(ico[:4], b"\x00\x00\x01\x00")
        self.assertGreaterEqual(struct.unpack("<H", ico[4:6])[0], 7)
        conf_path = ROOT / "src-tauri" / "tauri.conf.json"
        conf = json.loads(conf_path.read_text(encoding="utf-8")) if conf_path.is_file() else {"bundle": {"icon": []}}
        for rel in conf["bundle"]["icon"]:
            self.assertTrue((ROOT / "src-tauri" / rel).is_file(), rel)

    def test_html_references_local_icons(self):
        for page in ("map.html",):
            html = (ROOT / "web" / page).read_text(encoding="utf-8")
            self.assertIn('href="/icons/icon.svg"', html, page)
            self.assertIn('href="/icons/icon-192.png"', html, page)

    def test_maskable_variant_keeps_artwork_in_safe_zone(self):
        import xml.etree.ElementTree as ET

        from tools.make_icons import MASKABLE_SCALE, MASTER, _clean_svg, maskable_svg

        svg = maskable_svg(_clean_svg(MASTER.read_text(encoding="utf-8")))
        root = ET.fromstring(svg)
        ns = "{http://www.w3.org/2000/svg}"
        rect = root.find(f"{ns}rect")
        self.assertIsNotNone(rect)
        self.assertIsNone(rect.get("rx"), "background must bleed to the edges")
        group = root.find(f"{ns}g")
        self.assertIn(f"scale({MASKABLE_SCALE})", group.get("transform"))
        self.assertLessEqual(MASKABLE_SCALE, 0.8)

    def test_icons_are_up_to_date(self):
        try:
            import resvg_py  # noqa: F401
        except ImportError:
            self.skipTest("resvg-py not installed (pip install -r requirements-icons.txt)")
        from tools.make_icons import build

        self.assertEqual(build(check=True), 0)


if __name__ == "__main__":
    unittest.main()
