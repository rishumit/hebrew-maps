"""הורדת פוליגוני אזורי הקברים מ-gps.hakohanim.org בזמן הבנייה (לא בזמן ריצה)."""
from __future__ import annotations

import json
import logging
import os
import urllib.request
from pathlib import Path

log = logging.getLogger(__name__)

KOHEN_AREAS_URL = os.environ.get("HMAPS_KOHEN_AREAS_URL", "https://gps.hakohanim.org/api/areas")
TIMEOUT_S = 60
MIN_FEATURES = 50  # הגנה מפני תשובה ריקה/חלקית


def _round_coords(c, nd=6):
    if isinstance(c[0], (int, float)):
        return [round(c[0], nd), round(c[1], nd)]
    return [_round_coords(x, nd) for x in c]


def parse(payload: dict) -> dict | None:
    """מחזיר FeatureCollection מצומצם (Polygon/MultiPolygon + level בלבד), או None אם התשובה לא תקינה."""
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict) or data.get("type") != "FeatureCollection":
        return None
    feats = []
    for f in data.get("features", []):
        g = f.get("geometry") or {}
        if g.get("type") not in ("Polygon", "MultiPolygon") or not g.get("coordinates"):
            continue
        feats.append({
            "type": "Feature",
            "properties": {"level": (f.get("properties") or {}).get("level")},
            "geometry": {"type": g["type"], "coordinates": _round_coords(g["coordinates"])},
        })
    if len(feats) < MIN_FEATURES:
        return None
    return {"type": "FeatureCollection", "features": feats}


def fetch(url: str = KOHEN_AREAS_URL) -> dict | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "hebrew-maps-etl"})
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            return parse(json.loads(r.read().decode("utf-8")))
    except Exception as e:  # noqa: BLE001 – כל כשל ברשת לא צריך להפיל את הבנייה
        log.warning("kohen areas: fetch failed (%s)", e)
        return None


def load_previous(path: Path) -> dict | None:
    """fallback: הנתונים מהבנייה הקודמת, אם הקובץ קיים."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8")).get("kohen")
    except (OSError, ValueError):
        return None


def get(avoid_areas_path: Path) -> dict | None:
    """נתונים חדשים; אם נכשל – הישנים; אם אין – None (המפתח "kohen" לא ייכתב)."""
    if os.environ.get("HMAPS_KOHEN_AREAS", "1") == "0":
        return None
    fresh = fetch()
    if fresh:
        log.info("kohen areas: %d polygons", len(fresh["features"]))
        return fresh
    old = load_previous(avoid_areas_path)
    if old:
        log.warning("kohen areas: using previous data")
        _ci_warning("מצב כהן: gps.hakohanim.org לא ענה – נשארו אזורי הקברים מהגרסה הקודמת")
    else:
        log.error("kohen areas: no fresh and no previous data – Kohen mode will be unavailable")
        _ci_warning("מצב כהן: אין אזורי קברים בחבילה הזו (gps.hakohanim.org לא ענה, ואין נתונים קודמים)")
    return old


def _ci_warning(msg: str) -> None:
    """A visible annotation on the GitHub Actions run (the build itself goes on)."""
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::warning title=Kohen areas::{msg}", flush=True)
