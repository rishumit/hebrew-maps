"""tools/make_icons.py – render every app icon locally from web/icons/icon.svg.

The SVG is the single master. Nothing is ever downloaded: content filters
(NetFree) block external images, so all PNG/ICO files are rendered here, on
the build machine, and the PNGs are committed to git.

Outputs
  web/icons/icon-192.png, icon-512.png       PWA / apple-touch icons
  web/icons/icon-maskable-512.png            full-bleed background, artwork inside the 80% safe zone
  installer/icon.ico                         16…256 px (PyInstaller EXE + Inno Setup), git-ignored
  src-tauri/icons/32x32.png, 128x128.png, 128x128@2x.png, icon.png, icon.ico   Tauri desktop app

Renderer: resvg (``resvg-py`` wheel, pure binary wheel for Windows/Linux/macOS, no
Cairo/GTK DLLs). Pinned in requirements-icons.txt. If it is missing, Playwright's
Chromium (requirements-dev.txt) is used as a fallback.

The script is idempotent: a file is rewritten only when its bytes change.

Usage:
  python tools/make_icons.py            # (re)generate everything
  python tools/make_icons.py --check    # exit 1 if a committed PNG is out of date (CI)
"""

from __future__ import annotations

import argparse
import io
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MASTER = ROOT / "web" / "icons" / "icon.svg"

WEB_ICONS = ROOT / "web" / "icons"
INSTALLER_ICO = ROOT / "installer" / "icon.ico"
TAURI_ICONS = ROOT / "src-tauri" / "icons"

ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
# Maskable icons: the platform may crop to a circle of radius 40% (W3C "safe zone").
MASKABLE_SCALE = 0.76
MASKABLE_BG = "#2563EB"  # theme_color in manifest.webmanifest – used if the master has no background rect

SVG_NS = "http://www.w3.org/2000/svg"


# ----------------------------------------------------------------------------- SVG helpers

def _clean_svg(text: str) -> str:
    """Drop <metadata> (large C2PA blob) – irrelevant for rendering."""
    return re.sub(r"<metadata>.*?</metadata>", "", text, flags=re.S)


def maskable_svg(master: str) -> str:
    """Build a maskable variant: background bleeds to the edges, artwork scaled into the safe zone.

    The master's first full-size <rect> is taken as the background (its rounded corners
    removed); every other drawing element is wrapped in a group scaled around the centre.
    If no such rect exists, the whole icon is scaled onto a solid theme-colour square.
    """
    ET.register_namespace("", SVG_NS)
    root = ET.fromstring(master)
    vb = [float(v) for v in (root.get("viewBox") or "0 0 512 512").replace(",", " ").split()]
    x0, y0, w, h = vb
    cx, cy = x0 + w / 2, y0 + h / 2
    tag = lambda el: el.tag.split("}")[-1]  # noqa: E731

    children = list(root)
    bg = None
    for el in children:
        if tag(el) == "rect" and float(el.get("width", "0")) >= w and float(el.get("height", "0")) >= h:
            bg = el
            break

    s = MASKABLE_SCALE
    group = ET.Element(f"{{{SVG_NS}}}g",
                       {"transform": f"translate({cx} {cy}) scale({s}) translate({-cx} {-cy})"})
    if bg is not None:
        for attr in ("rx", "ry"):
            bg.attrib.pop(attr, None)
        bg.set("x", str(x0))
        bg.set("y", str(y0))
        for el in children:
            if el is bg or tag(el) in ("defs", "metadata", "title", "desc", "style"):
                continue
            root.remove(el)
            group.append(el)
        root.insert(list(root).index(bg) + 1, group)
    else:
        for el in children:
            if tag(el) in ("defs", "metadata", "title", "desc", "style"):
                continue
            root.remove(el)
            group.append(el)
        root.append(ET.Element(f"{{{SVG_NS}}}rect", {"x": str(x0), "y": str(y0), "width": str(w),
                                                     "height": str(h), "fill": MASKABLE_BG}))
        root.append(group)
    return ET.tostring(root, encoding="unicode")


# ----------------------------------------------------------------------------- renderers

def _render_resvg(svg: str, size: int) -> bytes:
    import resvg_py  # type: ignore[import-not-found]

    return bytes(resvg_py.svg_to_bytes(svg_string=svg, width=size, height=size,
                                       skip_system_fonts=True))


def _render_playwright(svg: str, size: int) -> bytes:
    from playwright.sync_api import sync_playwright  # type: ignore[import-not-found]

    html = ("<!doctype html><html><head><style>html,body{margin:0;background:transparent}"
            f"svg{{display:block;width:{size}px;height:{size}px}}</style></head><body>{svg}</body></html>")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": size, "height": size})
        page.set_content(html)
        png = page.screenshot(omit_background=True, clip={"x": 0, "y": 0, "width": size, "height": size})
        browser.close()
    return png


def pick_renderer():
    try:
        import resvg_py  # noqa: F401  # type: ignore[import-not-found]
        return "resvg", _render_resvg
    except ImportError:
        pass
    try:
        import playwright  # noqa: F401  # type: ignore[import-not-found]
        return "playwright", _render_playwright
    except ImportError:
        pass
    sys.exit("make_icons: no SVG renderer found. Install it with:\n"
             "  pip install -r requirements-icons.txt")


# ----------------------------------------------------------------------------- outputs

def _normalise_png(data: bytes) -> bytes:
    """Re-encode through Pillow without metadata so output is stable across renderer versions."""
    from PIL import Image

    im = Image.open(io.BytesIO(data)).convert("RGBA")
    out = io.BytesIO()
    im.save(out, format="PNG", optimize=True)
    return out.getvalue()


def _ico(images: dict[int, bytes]) -> bytes:
    from PIL import Image

    frames = [Image.open(io.BytesIO(images[s])).convert("RGBA") for s in sorted(images)]
    largest = frames[-1]
    out = io.BytesIO()
    # Pillow uses an appended frame whose size matches exactly instead of downscaling.
    largest.save(out, format="ICO", sizes=[(s, s) for s in sorted(images)], append_images=frames[:-1])
    return out.getvalue()


def _frames(data: bytes):
    """Decoded RGBA frames of a PNG/ICO (every size of an ICO)."""
    from PIL import Image

    im = Image.open(io.BytesIO(data))
    if im.format == "ICO":
        return [im.ico.getimage(s).convert("RGBA") for s in sorted(im.ico.sizes())]
    return [im.convert("RGBA")]


def same_image(old: bytes | None, new: bytes, tolerance: int = 8) -> bool:
    """True when two icon files look the same.

    Byte-for-byte comparison is too strict: different Pillow/zlib/resvg builds (Windows vs
    Linux CI) encode the same picture slightly differently, which used to leave the committed
    icons "modified" after every local build. Pixels may differ by a few levels of anti-aliasing.
    """
    if old is None:
        return False
    if old == new:
        return True
    from PIL import ImageChops

    try:
        a, b = _frames(old), _frames(new)
    except Exception:  # noqa: BLE001 – unreadable old file -> rewrite it
        return False
    if len(a) != len(b):
        return False
    for x, y in zip(a, b):
        if x.size != y.size:
            return False
        if max(hi for _, hi in ImageChops.difference(x, y).getextrema()) > tolerance:
            return False
    return True


def plan(master_svg: str) -> list[tuple[Path, str, int | tuple[int, ...]]]:
    """(path, svg-variant, size or ico-sizes)."""
    plain, mask = "plain", "maskable"
    return [
        (WEB_ICONS / "icon-192.png", plain, 192),
        (WEB_ICONS / "icon-512.png", plain, 512),
        (WEB_ICONS / "icon-maskable-512.png", mask, 512),
        (INSTALLER_ICO, plain, ICO_SIZES),
        (TAURI_ICONS / "32x32.png", plain, 32),
        (TAURI_ICONS / "128x128.png", plain, 128),
        (TAURI_ICONS / "128x128@2x.png", plain, 256),
        (TAURI_ICONS / "icon.png", plain, 512),
        (TAURI_ICONS / "icon.ico", plain, ICO_SIZES),
    ]


def build(check: bool = False) -> int:
    master = _clean_svg(MASTER.read_text(encoding="utf-8"))
    variants = {"plain": master, "maskable": maskable_svg(master)}
    name, render = pick_renderer()
    print(f"make_icons: master={MASTER.relative_to(ROOT)} renderer={name}")

    cache: dict[tuple[str, int], bytes] = {}

    def png(variant: str, size: int) -> bytes:
        key = (variant, size)
        if key not in cache:
            cache[key] = _normalise_png(render(variants[variant], size))
        return cache[key]

    stale = 0
    for path, variant, size in plan(master):
        data = _ico({s: png(variant, s) for s in size}) if isinstance(size, tuple) else png(variant, size)
        rel = path.relative_to(ROOT)
        old = path.read_bytes() if path.is_file() else None
        if same_image(old, data):
            print(f"  ok        {rel}")
            continue
        if check:
            # The ICO in installer/ is a git-ignored build product – never stale for --check.
            if path == INSTALLER_ICO:
                continue
            print(f"  STALE     {rel}")
            stale += 1
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        print(f"  written   {rel}")
    if check and stale:
        print("make_icons: icons are out of date – run: python tools/make_icons.py")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="only verify; exit 1 if a file would change")
    args = ap.parse_args(argv)
    return build(check=args.check)


if __name__ == "__main__":
    raise SystemExit(main())
