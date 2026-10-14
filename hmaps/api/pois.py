"""Full POI / place / street records for the info card."""

from __future__ import annotations

import json
import sqlite3
import urllib.parse
from datetime import datetime

from ..core import categories, hours
from ..core.text import tel_href
from . import rich
from .images import local_image
from .search import KIND_LABEL, format_address

CUISINE_HE = {
    "italian": "איטלקי", "pizza": "פיצה", "burger": "המבורגרים", "sushi": "סושי", "japanese": "יפני",
    "chinese": "סיני", "asian": "אסייתי", "thai": "תאילנדי", "indian": "הודי", "mexican": "מקסיקני",
    "french": "צרפתי", "american": "אמריקאי", "mediterranean": "ים תיכוני", "middle_eastern": "מזרח תיכוני",
    "israeli": "ישראלי", "falafel": "פלאפל", "shawarma": "שווארמה", "hummus": "חומוס", "kebab": "קבב",
    "fish": "דגים", "seafood": "דגים", "steak_house": "בשרים", "grill": "גריל", "dairy": "חלבי",
    "vegetarian": "צמחוני", "vegan": "טבעוני", "coffee_shop": "קפה", "ice_cream": "גלידה", "bakery": "מאפים",
    "sandwich": "כריכים", "salad": "סלטים", "breakfast": "ארוחות בוקר", "noodle": "נודלס", "georgian": "גאורגי",
    "ethiopian": "אתיופי", "moroccan": "מרוקאי", "yemenite": "תימני", "kurdish": "כורדי", "regional": "מקומי",
}
WHEELCHAIR_HE = {"yes": "נגיש לכיסאות גלגלים", "limited": "נגישות חלקית", "no": "לא נגיש לכיסאות גלגלים"}
DAY_SHORT = ["א׳", "ב׳", "ג׳", "ד׳", "ה׳", "ו׳", "שבת"]


def _osm_url(ref: str | None) -> str | None:
    if not ref or len(ref) < 2:
        return None
    kind = {"n": "node", "w": "way", "r": "relation"}.get(ref[0])
    return f"https://www.openstreetmap.org/{kind}/{ref[1:]}" if kind else None


def _wiki_url(wp: str | None) -> str | None:
    if not wp or ":" not in wp:
        return None
    lang, title = wp.split(":", 1)
    return f"https://{lang}.wikipedia.org/wiki/{urllib.parse.quote(title.replace(' ', '_'))}"


def hours_block(raw: str | None, hours_json: str | None, now: datetime | None = None) -> dict | None:
    if not raw:
        return None
    if not hours_json:
        return {"raw": raw, "weekly": None, "rows": None, "open_now": None, "status": None}
    weekly = json.loads(hours_json)
    week = {i: [tuple(s) for s in spans] for i, spans in enumerate(weekly.values())}
    now = now or datetime.now(hours.TZ)
    is_open = hours.is_open(week, now)
    nxt = hours.next_change(week, now)
    status = "פתוח עכשיו" if is_open else "סגור עכשיו"
    if nxt:
        same_day = nxt.date() == now.date()
        when = nxt.strftime("%H:%M") if same_day else f"ביום {DAY_SHORT[(nxt.weekday() + 1) % 7]} ב-{nxt.strftime('%H:%M')}"
        if is_open:
            status += f" · נסגר {'ב-' if same_day else ''}{when}"
        else:
            status += f" · נפתח {'ב-' if same_day else ''}{when}"
    return {"raw": raw, "weekly": weekly, "rows": hours.describe_he(week), "open_now": is_open, "status": status,
            "today": (now.weekday() + 1) % 7}


def poi_detail(con: sqlite3.Connection, poi_id: int) -> dict | None:
    r = con.execute("SELECT * FROM pois WHERE id = ?", (poi_id,)).fetchone()
    if r is None:
        return None
    sub = categories.SUB_BY_ID[r["sub"]]
    cat = categories.CATEGORY_BY_ID[r["category"]]
    extra = json.loads(r["extra"]) if r["extra"] else {}
    tags: list[str] = []
    if r["kind_he"]:
        tags.append(r["kind_he"])
    for c in (extra.get("cuisine") or "").split(";"):
        c = c.strip()
        if c in CUISINE_HE and CUISINE_HE[c] not in tags:
            tags.append(CUISINE_HE[c])
    if r["kosher"]:
        tags.append("כשר")
    if r["wheelchair"] == "yes":
        tags.append("נגיש")
    if extra.get("takeaway") in ("yes", "only"):
        tags.append("טייק אווי")
    if extra.get("delivery") == "yes":
        tags.append("משלוחים")
    if extra.get("internet_access") in ("wlan", "yes", "wifi"):
        tags.append("Wi-Fi")
    if extra.get("outdoor_seating") == "yes":
        tags.append("ישיבה בחוץ")
    if extra.get("air_conditioning") == "yes":
        tags.append("ממוזג")
    phone = r["phone"]
    gallery = rich.gallery(con, "poi", r["id"], r["image"])
    main = local_image(r["image"], 640)
    images = [main] if main else [g["src"] for g in gallery[:1]]
    full_address = format_address(r["street"], r["housenumber"], r["city"]) or None
    name = r["name"] or sub.name_he
    return {
        "id": f"poi-{r['id']}",
        "type": "poi",
        "name": name,
        "name_he": r["name"],
        "name_en": r["name_en"],
        "category": cat.id, "category_he": cat.name_he,
        "sub_category": sub.id, "sub_category_he": r["kind_he"] or sub.name_he,
        "icon": sub.icon, "color": cat.color,
        "location": {"lat": r["lat"], "lon": r["lon"]},
        "bbox": extra.get("bbox"),
        "address": {"street": r["street"], "house_number": r["housenumber"], "city": r["city"],
                    "postal_code": r["postcode"], "full": full_address},
        "contact": {"phone": phone, "phone_href": tel_href(phone) if phone else None, "email": r["email"],
                    "website": r["website"]},
        "opening_hours": hours_block(r["opening_hours"], r["hours_json"]),
        "media": {"images": images, "thumbnail": images[0] if images else None, "gallery": gallery},
        "metadata": {
            "rating": None, "review_count": None,
            "wheelchair_accessible": {"yes": True, "no": False}.get(r["wheelchair"] or ""),
            "wheelchair_he": WHEELCHAIR_HE.get(r["wheelchair"] or ""),
            "kosher": bool(r["kosher"]) if r["kosher"] is not None else None,
            "tags": tags,
            "operator": extra.get("operator"), "brand": extra.get("brand"),
            "elevation": extra.get("ele"), "denomination": extra.get("nusach") or None,
            "last_updated": r["updated"],
            "wikipedia_url": _wiki_url(r["wikipedia"]),
            "osm_url": _osm_url(r["osm"]),
            "source": "נדרים פלוס" if (r["osm"] or "").startswith("zm:") else "OpenStreetMap",
        },
        "description_he": r["description"] or extra.get("description:he") or extra.get("description"),
        "description_en": None,
        **_outdoor_fields(con, r["id"], r["sub"], extra),
        **_rich_fields(con, r, extra),
        **_minyanim_fields(con, r),
    }


def _minyanim_fields(con: sqlite3.Connection, r) -> dict:
    """Prayer times (Nedarim Plus) of linked synagogues – see hmaps/api/minyanim.py."""
    if r["sub"] not in ("synagogue", "yeshiva"):
        return {}
    from .minyanim import poi_block

    block = poi_block(con, r["id"], r["lat"], r["lon"])
    return {"minyanim": block} if block else {}


def _rich_fields(con: sqlite3.Connection, r, extra: dict) -> dict:
    """Long texts, Wikidata facts, trails passing by and trip sites around (trip sites only)."""
    fact_rows, raw = rich.facts(con, "poi", r["id"], {"elevation": extra.get("ele"), "length_m": extra.get("length_m")})
    out: dict = {"texts": rich.texts(con, "poi", r["id"]), "facts": fact_rows}
    if raw.get("website") and not r["website"]:
        out["official_website"] = raw["website"]
    if raw.get("commons_category"):
        out["commons_category_url"] = ("https://commons.wikimedia.org/wiki/Category:"
                                       + urllib.parse.quote(raw["commons_category"].replace(" ", "_")))
    if r["sub"] in rich.TRIP_SUBS:
        bbox = extra.get("bbox")
        area = tuple(bbox) if bbox and len(bbox) == 4 else None  # reserves, parks: trails in / at the edge of the area
        out["nearby_trails"] = rich.nearby_trails(con, r["lat"], r["lon"], 400.0 if area else 300.0, bbox=area)
        out["nearby_sites"] = rich.nearby_sites(con, r["id"], r["lat"], r["lon"])
    return out


FEE_HE = {"yes": "כניסה בתשלום", "no": "כניסה חופשית"}
INPA_HE = {"operator": "רשות הטבע והגנים (לפי תגית המפעיל ב-OpenStreetMap)",
           "name": "רשות הטבע והגנים (משוער: אתר מוכרז לפי שמו)"}


def _outdoor_fields(con: sqlite3.Connection, poi_id: int, sub: str, extra: dict) -> dict:
    """Geometry (streams, ridges, reserves) and nature-site facts for the card."""
    from .outdoor import poi_geometry

    out: dict = {}
    if sub in categories.LINE_SUBS or sub in categories.SHAPE_SUBS:
        out["geometry"] = poi_geometry(con, poi_id)
    if extra.get("length_m"):
        out["length_m"] = extra["length_m"]
    fee = extra.get("fee")
    if fee or extra.get("inpa") or sub in categories.SHAPE_SUBS:
        out["nature_site"] = {
            "fee": {"yes": True, "no": False}.get(fee or ""),
            "fee_he": FEE_HE.get(fee or "") or (f"דמי כניסה: {fee}" if fee else None),
            "inpa": bool(extra.get("inpa")),
            "inpa_source": extra.get("inpa"),
            "inpa_he": INPA_HE.get(extra.get("inpa") or ""),
        }
    return out


def place_detail(con: sqlite3.Connection, place_id: int) -> dict | None:
    r = con.execute("SELECT * FROM places WHERE id = ?", (place_id,)).fetchone()
    if r is None:
        return None
    geom = None
    area = con.execute("SELECT geom FROM areas WHERE name = ? AND level BETWEEN 7 AND 10 ORDER BY level LIMIT 1", (r["name"],)).fetchone()
    if area:
        geom = {"type": "MultiPolygon", "coordinates": json.loads(area["geom"])}
    return {
        "id": f"place-{r['id']}", "type": "place", "name": r["name"], "name_en": r["name_en"],
        "kind": r["kind"], "kind_he": KIND_LABEL.get(r["kind"], "מקום"),
        "population": r["population"], "parent": r["parent"], "district": r["district"],
        "settlement_code": r["settlement_code"],
        "location": {"lat": r["lat"], "lon": r["lon"]},
        "bbox": [r["min_lon"], r["min_lat"], r["max_lon"], r["max_lat"]],
        "geometry": geom,
        "metadata": {"osm_url": _osm_url(r["osm"])},
    }


def street_detail(con: sqlite3.Connection, street_id: int) -> dict | None:
    from ..core.geo import decode_polyline

    r = con.execute("SELECT * FROM streets WHERE id = ?", (street_id,)).fetchone()
    if r is None:
        return None
    lines = [[list(p) for p in decode_polyline(g[0])]
             for g in con.execute("SELECT geom FROM street_segments WHERE street_id = ?", (street_id,))]
    numbers = [x[0] for x in con.execute(
        "SELECT housenumber FROM addresses WHERE street_norm = ? AND (city_norm = ? OR (? IS NULL AND city_norm IS NULL)) "
        "ORDER BY number_int LIMIT 400", (r["name_norm"], r["city_norm"], r["city_norm"]))]
    return {
        "id": f"street-{r['id']}", "type": "street", "name": r["name"], "name_en": r["name_en"], "city": r["city"],
        "location": {"lat": r["lat"], "lon": r["lon"]},
        "bbox": [r["min_lon"], r["min_lat"], r["max_lon"], r["max_lat"]],
        "length_m": r["length_m"],
        "geometry": {"type": "MultiLineString", "coordinates": lines},
        "house_numbers": numbers,
    }
