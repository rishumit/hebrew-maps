"""Download the web assets and build the demo database (installation without Docker).

    python tools/fetch_assets.py

* web/vendor: MapLibre GL JS + the RTL text plugin (required for the map);
* web/fonts-ui: Heebo UI font (optional);
* web/fonts: Noto Sans map label glyphs (optional – bundled Hebrew fallback glyphs exist);
* data/sample/maps.db: demo data so the app works before the real data is built.

Each step is skipped when its files already exist, so the script can be re-run.
"""

from __future__ import annotations

import io
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
UA = {"User-Agent": "HebrewMaps-setup/1.0"}

VENDOR = {
    "maplibre-gl.js": ["https://cdn.jsdelivr.net/npm/maplibre-gl@4.7.1/dist/maplibre-gl.js",
                       "https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.js"],
    "maplibre-gl.css": ["https://cdn.jsdelivr.net/npm/maplibre-gl@4.7.1/dist/maplibre-gl.css",
                        "https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.css"],
    "mapbox-gl-rtl-text.js": ["https://cdn.jsdelivr.net/npm/@mapbox/mapbox-gl-rtl-text@0.2.3/mapbox-gl-rtl-text.min.js",
                              "https://unpkg.com/@mapbox/mapbox-gl-rtl-text@0.2.3/mapbox-gl-rtl-text.min.js"],
}
HEEBO = "https://github.com/google/fonts/raw/main/ofl/heebo/Heebo%5Bwght%5D.ttf"
FONTS_ZIP = "https://github.com/protomaps/basemaps-assets/archive/refs/heads/main.zip"


def fetch(url: str, timeout: int = 120) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read()


def fetch_first(urls: list[str]) -> bytes:
    last = None
    for u in urls:
        try:
            data = fetch(u)
            if data:
                return data
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError(f"download failed: {urls[0]} ({last})")


def vendor() -> None:
    out = WEB / "vendor"
    out.mkdir(parents=True, exist_ok=True)
    for name, urls in VENDOR.items():
        p = out / name
        if p.exists() and p.stat().st_size > 1000:
            continue
        print(f"  downloading {name}")
        p.write_bytes(fetch_first(urls))


def ui_font() -> None:
    p = WEB / "fonts-ui" / "Heebo.ttf"
    if p.exists():
        return
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(fetch(HEEBO))
        print("  UI font (Heebo) ready")
    except Exception as e:  # noqa: BLE001
        print(f"  (optional) UI font not downloaded: {e}")


def map_fonts() -> None:
    target = WEB / "fonts" / "Noto Sans Regular"
    if target.exists():
        return
    try:
        print("  downloading map label fonts (~20 MB)")
        z = zipfile.ZipFile(io.BytesIO(fetch(FONTS_ZIP, timeout=300)))
        prefix = "basemaps-assets-main/fonts/"
        for info in z.infolist():
            if info.filename.startswith(prefix) and not info.is_dir():
                rel = info.filename[len(prefix):]
                dest = WEB / "fonts" / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info) as src, open(dest, "wb") as dst:
                    shutil.copyfileobj(src, dst)
        print("  map fonts ready")
    except Exception as e:  # noqa: BLE001
        print(f"  (optional) map fonts not downloaded, using the bundled Hebrew glyphs: {e}")


def demo_db() -> None:
    from hmaps.core import db as dbmod

    out = ROOT / "data" / "sample" / "maps.db"
    if dbmod.resolve(out).exists():
        return
    sys.path.insert(0, str(ROOT))
    from hmaps.etl import build, osm, quality, search_index  # noqa: F401
    from tools.sample_data import extras, generate

    src = ROOT / "data" / "sample" / "src"
    poi, streets = generate(src)
    lines, rels = extras(src)
    out.parent.mkdir(parents=True, exist_ok=True)
    con = dbmod.create(out.with_suffix(".building"))
    build.run(con, osm.iter_features(poi), osm.iter_features(streets),
              line_features=osm.iter_features(lines), relations=osm.iter_relations(rels))
    search_index.build(con)
    con.execute("INSERT OR REPLACE INTO meta VALUES ('built', 'demo')")
    con.commit()
    dbmod.finalize(con)
    con.close()
    import os
    os.replace(out.with_suffix(".building"), out)
    shutil.rmtree(src, ignore_errors=True)
    print("  demo data ready")


def main() -> int:
    sys.path.insert(0, str(ROOT))
    print("Web assets:")
    try:
        vendor()
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: could not download the map library: {e}")
        return 1
    ui_font()
    map_fonts()
    demo_db()
    print("Assets OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
