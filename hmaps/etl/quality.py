"""Data quality report (KPIs from docs/03-data-collection.md §9)."""

from __future__ import annotations

import html
import json
import sqlite3
from datetime import datetime
from pathlib import Path

from ..core import categories


def _one(con: sqlite3.Connection, sql: str, *args) -> int:
    return con.execute(sql, args).fetchone()[0] or 0


NOTABLE_IMAGE_TARGET = 30
NOTABLE_DESC_TARGET = 40


def compute(con: sqlite3.Connection, build_stats: dict | None = None) -> dict:
    pois = _one(con, "SELECT COUNT(*) FROM pois")
    named = _one(con, "SELECT COUNT(*) FROM pois WHERE name IS NOT NULL")

    def pct(n: int, d: int) -> float:
        return round(100.0 * n / d, 1) if d else 0.0

    settlements = _one(con, "SELECT COUNT(*) FROM places WHERE kind IN ('city','town','village','hamlet','isolated_dwelling')")
    official = _one(con, "SELECT COUNT(*) FROM official_settlements")
    official_matched = _one(con, "SELECT COUNT(*) FROM official_settlements WHERE matched = 1")
    off_streets = _one(con, "SELECT COUNT(*) FROM official_streets")
    off_streets_matched = _one(con, "SELECT COUNT(*) FROM official_streets WHERE matched = 1")
    streets = _one(con, "SELECT COUNT(*) FROM streets")

    by_cat = {}
    for cat in categories.CATEGORIES:
        by_cat[cat.name_he] = _one(con, "SELECT COUNT(*) FROM pois WHERE category = ?", cat.id)
    by_sub = {
        categories.SUB_BY_ID[s].plural_he: n
        for s, n in con.execute("SELECT sub, COUNT(*) FROM pois GROUP BY sub ORDER BY COUNT(*) DESC")
    }

    # photo / description coverage: measured on notable places (see categories.NOTABLE_SUBS)
    all_img = _one(con, "SELECT COUNT(*) FROM pois WHERE name IS NOT NULL AND image IS NOT NULL")
    all_desc = _one(con, "SELECT COUNT(*) FROM pois WHERE name IS NOT NULL AND description IS NOT NULL")
    media_cov: dict[str, dict] = {}
    n_notable = n_img = n_desc = 0
    for s, n, ni, nd in con.execute(
        "SELECT sub, COUNT(*), COUNT(image), COUNT(description) FROM pois WHERE name IS NOT NULL GROUP BY sub"
        " ORDER BY COUNT(*) DESC"
    ):
        if s in categories.NOTABLE_SUBS:
            media_cov[categories.SUB_BY_ID[s].plural_he] = {"pois": n, "image": pct(ni, n), "description": pct(nd, n)}
            n_notable, n_img, n_desc = n_notable + n, n_img + ni, n_desc + nd

    kpis = [
        {"name": "נקודות עניין (POI)", "value": pois, "target": 50000, "unit": "", "ok": pois >= 50000},
        {"name": "יישובים במאגר", "value": settlements, "target": 1200, "unit": "", "ok": settlements >= 1200},
        {"name": "רחובות", "value": streets, "target": None, "unit": "", "ok": streets > 0},
        {"name": "כיסוי יישובים רשמיים (data.gov.il)", "value": pct(official_matched, official), "target": 95, "unit": "%",
         "ok": official == 0 or pct(official_matched, official) >= 95, "note": "" if official else "המאגר הרשמי לא הורד"},
        {"name": "כיסוי רחובות רשמיים (data.gov.il)", "value": pct(off_streets_matched, off_streets), "target": 95, "unit": "%",
         "ok": off_streets == 0 or pct(off_streets_matched, off_streets) >= 95, "note": "" if off_streets else "המאגר הרשמי לא הורד"},
        {"name": "POI עם עיר משויכת", "value": pct(_one(con, "SELECT COUNT(*) FROM pois WHERE city IS NOT NULL"), pois), "target": 98, "unit": "%",
         "ok": pct(_one(con, "SELECT COUNT(*) FROM pois WHERE city IS NOT NULL"), pois) >= 98},
        {"name": "תמונה – אתרים ונקודות ציון", "value": pct(n_img, n_notable), "target": NOTABLE_IMAGE_TARGET, "unit": "%",
         "ok": pct(n_img, n_notable) >= NOTABLE_IMAGE_TARGET,
         "note": f"{n_img:,} מתוך {n_notable:,} (תיירות, טבע, אתרי רט\"ג, מוזיאונים, מלונות…) · Wikidata/Commons"},
        {"name": "תיאור – אתרים ונקודות ציון", "value": pct(n_desc, n_notable), "target": NOTABLE_DESC_TARGET, "unit": "%",
         "ok": pct(n_desc, n_notable) >= NOTABLE_DESC_TARGET,
         "note": f"{n_desc:,} מתוך {n_notable:,} · ויקיפדיה / OSM"},
        {"name": "POI עם תמונה (כלל הנקודות)", "value": pct(all_img, named), "target": None, "unit": "%", "ok": True,
         "note": "לידיעה בלבד – כולל תחנות אוטובוס, חנויות וברזיות שאין להן תמונה"},
        {"name": "POI עם תיאור (כלל הנקודות)", "value": pct(all_desc, named), "target": None, "unit": "%", "ok": True,
         "note": "לידיעה בלבד"},
        {"name": "POI עם טלפון", "value": pct(_one(con, "SELECT COUNT(*) FROM pois WHERE phone IS NOT NULL"), named), "target": None, "unit": "%", "ok": True},
        {"name": "POI עם שעות פתיחה", "value": pct(_one(con, "SELECT COUNT(*) FROM pois WHERE opening_hours IS NOT NULL"), named), "target": None, "unit": "%", "ok": True},
        {"name": "שעות פתיחה שפוענחו", "value": pct(_one(con, "SELECT COUNT(*) FROM pois WHERE hours_json IS NOT NULL"),
                                                   _one(con, "SELECT COUNT(*) FROM pois WHERE opening_hours IS NOT NULL")),
         "target": 85, "unit": "%", "ok": True},
    ]

    # --- outdoor: marked trails, INPA sites, streams (schema 4; older databases report zeros)
    try:
        trails = _one(con, "SELECT COUNT(*) FROM trails")
        trails_named = _one(con, "SELECT COUNT(*) FROM trails WHERE name IS NOT NULL")
        trails_km = round((con.execute("SELECT SUM(length_m) FROM trails").fetchone()[0] or 0) / 1000)
        broken = _one(con, "SELECT COUNT(*) FROM trails WHERE parts > 1")
        int_row = con.execute("SELECT length_m, parts FROM trails WHERE network = 'nwn' AND route = 'hiking' AND "
                              "(name LIKE '%שביל ישראל%' OR name_en LIKE '%Israel National Trail%') "
                              "ORDER BY length_m DESC LIMIT 1").fetchone()
    except sqlite3.OperationalError:
        trails = trails_named = trails_km = broken = 0
        int_row = None
    int_km = round(int_row[0] / 1000, 1) if int_row else 0.0
    kpis += [
        {"name": "שבילים מסומנים (OSM)", "value": trails, "target": None, "unit": "", "ok": trails > 0,
         "note": f"{trails_named:,} עם שם · {trails_km:,} ק\"מ בסך הכול"},
        {"name": "אורך שביל ישראל", "value": int_km, "target": 1000, "unit": " ק\"מ", "ok": 950 <= int_km <= 1150,
         "note": f"{int_row[1]} מקטעים מנותקים" if int_row and int_row[1] > 1 else ("רציף" if int_row else "לא נמצא")},
        {"name": "אתרי רשות הטבע והגנים", "value": _one(con, "SELECT COUNT(*) FROM pois WHERE sub = 'inpa_site'"), "target": None,
         "unit": "", "ok": True, "note": "לפי תגית מפעיל, או שם מוכרז (גן לאומי / שמורת טבע)"},
        {"name": "נחלים עם שם", "value": _one(con, "SELECT COUNT(*) FROM pois WHERE sub = 'stream'"), "target": None,
         "unit": "", "ok": True},
    ]

    # --- rich content (schema 5: galleries / texts / facts of trip sites and trails)
    try:
        from .rich import TRIP_SUBS
        subs = sorted(TRIP_SUBS)
        marks = ",".join("?" * len(subs))
        n_sites = _one(con, f"SELECT COUNT(*) FROM pois WHERE name IS NOT NULL AND sub IN ({marks})", *subs)
        with_gallery = _one(con, "SELECT COUNT(DISTINCT ref_id) FROM media WHERE entity = 'poi'")
        photos = _one(con, "SELECT COUNT(*) FROM media WHERE entity = 'poi'")
        with_text = _one(con, "SELECT COUNT(DISTINCT ref_id) FROM texts WHERE entity = 'poi'")
        voy = _one(con, "SELECT COUNT(*) FROM texts WHERE entity = 'poi' AND source = 'wikivoyage'")
        mly = _one(con, "SELECT COUNT(*) FROM media WHERE source = 'mapillary'")
        tr_gallery = _one(con, "SELECT COUNT(DISTINCT ref_id) FROM media WHERE entity = 'trail'")
        tr_text = _one(con, "SELECT COUNT(*) FROM texts WHERE entity = 'trail'")
        kpis += [
            {"name": "אתרי טיול עם גלריית תמונות", "value": pct(with_gallery, n_sites), "target": None, "unit": "%",
             "ok": True, "note": f"{with_gallery:,} מתוך {n_sites:,} · {photos:,} תמונות (Wikimedia Commons"
                                 f"{f' + {mly:,} Mapillary' if mly else ''})"},
            {"name": "אתרי טיול עם טקסט מורחב", "value": pct(with_text, n_sites), "target": None, "unit": "%", "ok": True,
             "note": f"{with_text:,} אתרים · ויקיפדיה{f' · {voy:,} מוויקימסע' if voy else ''}"},
            {"name": "שבילים עם תמונות", "value": tr_gallery, "target": None, "unit": "", "ok": True,
             "note": f"{tr_text:,} עם ערך בוויקיפדיה"},
        ]
    except sqlite3.OperationalError:
        pass

    errors = {
        "POI מחוץ לגבולות ישראל": _one(con, "SELECT COUNT(*) FROM pois WHERE lat NOT BETWEEN 29.4 AND 33.45 OR lon NOT BETWEEN 34.15 AND 35.95"),
        "POI ללא שם בקטגוריה שמחייבת שם": sum(
            n for s, n in con.execute("SELECT sub, COUNT(*) FROM pois WHERE name IS NULL GROUP BY sub")
            if not categories.SUB_BY_ID[s].allow_unnamed
        ),
        "כתובות ללא עיר": _one(con, "SELECT COUNT(*) FROM addresses WHERE city IS NULL"),
        "שעות פתיחה שלא פוענחו": _one(con, "SELECT COUNT(*) FROM pois WHERE opening_hours IS NOT NULL AND hours_json IS NULL"),
        "שבילים מסומנים שאינם רציפים": broken,
    }
    meta = dict(con.execute("SELECT key, value FROM meta").fetchall())
    return {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "source_date": meta.get("osm_date"),
        "totals": {
            "pois": pois, "named_pois": named, "settlements": settlements, "places": _one(con, "SELECT COUNT(*) FROM places"),
            "streets": streets, "addresses": _one(con, "SELECT COUNT(*) FROM addresses"),
            "search_items": _one(con, "SELECT COUNT(*) FROM search_items"),
            "trails": trails, "trails_km": trails_km,
        },
        "kpis": kpis,
        "by_category": by_cat,
        "by_sub_category": by_sub,
        "media_coverage": media_cov,
        "validation": errors,
        "build": build_stats or {},
    }


def write(report: dict, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    jpath = out_dir / "quality-report.json"
    jpath.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    e = html.escape

    def fmt(v, unit=""):
        return f"{v:,}{unit}" if isinstance(v, int) else f"{v}{unit}"

    kpi_rows = "".join(
        f"<tr><td>{e(k['name'])}</td><td>{fmt(k['value'], k['unit'])}</td>"
        f"<td>{fmt(k['target'], k['unit']) if k['target'] is not None else '—'}</td>"
        f"<td class={'ok' if k['ok'] else 'bad'}>{'✓ עומד ביעד' if k['ok'] else '✗ מתחת ליעד'}</td>"
        f"<td>{e(k.get('note', ''))}</td></tr>"
        for k in report["kpis"]
    )
    cat_rows = "".join(f"<tr><td>{e(n)}</td><td>{v:,}</td></tr>" for n, v in report["by_category"].items())
    sub_rows = "".join(f"<tr><td>{e(n)}</td><td>{v:,}</td></tr>" for n, v in report["by_sub_category"].items())
    media_rows = "".join(
        f"<tr><td>{e(n)}</td><td>{v['pois']:,}</td><td>{v['image']}%</td><td>{v['description']}%</td></tr>"
        for n, v in report.get("media_coverage", {}).items()
    )
    val_rows = "".join(f"<tr><td>{e(n)}</td><td>{v:,}</td></tr>" for n, v in report["validation"].items())
    tot = report["totals"]
    page = f"""<!doctype html><html lang="he" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>דוח איכות נתונים</title>
<style>body{{font-family:Heebo,'Segoe UI',Arial,sans-serif;margin:0;padding:24px;background:#F9FAFB;color:#1F2937}}
main{{max-width:960px;margin:auto}}h1{{font-size:1.8rem}}table{{border-collapse:collapse;width:100%;background:#fff;margin:12px 0 28px;box-shadow:0 1px 3px rgba(0,0,0,.1);border-radius:8px;overflow:hidden}}
td,th{{padding:8px 12px;border-bottom:1px solid #E5E7EB;text-align:right}}th{{background:#F3F4F6}}.ok{{color:#047857;font-weight:600}}.bad{{color:#B91C1C;font-weight:600}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}}.tile{{background:#fff;border-radius:12px;padding:16px;box-shadow:0 1px 3px rgba(0,0,0,.1)}}
.tile b{{display:block;font-size:1.6rem;color:#1E40AF}}</style></head><body><main>
<h1>דוח איכות נתונים – מפות ישראל</h1><p>נוצר: {e(report['generated'])} · תאריך נתוני OSM: {e(str(report.get('source_date') or 'לא ידוע'))}</p>
<div class="tiles"><div class="tile"><b>{tot['pois']:,}</b>נקודות עניין</div><div class="tile"><b>{tot['settlements']:,}</b>יישובים</div>
<div class="tile"><b>{tot['streets']:,}</b>רחובות</div><div class="tile"><b>{tot['addresses']:,}</b>כתובות</div></div>
<h2>מדדי איכות</h2><table><tr><th>מדד</th><th>ערך</th><th>יעד</th><th>סטטוס</th><th>הערה</th></tr>{kpi_rows}</table>
<h2>כיסוי תמונות ותיאורים – אתרים ונקודות ציון</h2><table><tr><th>תת-קטגוריה</th><th>נקודות עם שם</th><th>עם תמונה</th><th>עם תיאור</th></tr>{media_rows}</table>
<h2>בדיקות תקינות</h2><table><tr><th>בדיקה</th><th>מספר רשומות</th></tr>{val_rows}</table>
<h2>לפי קטגוריה</h2><table><tr><th>קטגוריה</th><th>כמות</th></tr>{cat_rows}</table>
<h2>לפי תת-קטגוריה</h2><table><tr><th>תת-קטגוריה</th><th>כמות</th></tr>{sub_rows}</table>
</main></body></html>"""
    hpath = out_dir / "quality-report.html"
    hpath.write_text(page, encoding="utf-8")
    return jpath, hpath
