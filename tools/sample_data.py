"""Generate a small but realistic sample dataset in osmium's GeoJSONSeq format.

Used by the automated tests and for the "demo mode" (running the app before
the real Israel extract has been processed). Landmarks have approximate real
coordinates; generated shops are clearly labelled as examples ("לדוגמה").

    python tools/sample_data.py OUT_DIR [--bulk N]
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

_ids = {"n": 1000, "w": 5000, "r": 9000}


def _fid(kind: str) -> int:
    _ids[kind] += 1
    return _ids[kind]


def node(lat: float, lon: float, **tags) -> dict:
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"@type": "node", "@id": _fid("n"), **tags}}


def way(coords: list[tuple[float, float]], **tags) -> dict:
    closed = coords[0] == coords[-1] and len(coords) > 3
    geom = {"type": "Polygon", "coordinates": [[list(c) for c in coords]]} if closed and tags.pop("_area", True) \
        else {"type": "LineString", "coordinates": [list(c) for c in coords]}
    return {"type": "Feature", "geometry": geom, "properties": {"@type": "way", "@id": _fid("w"), **tags}}


def relation_area(rings: list[list[tuple[float, float]]], **tags) -> dict:
    return {"type": "Feature", "geometry": {"type": "MultiPolygon", "coordinates": [[[list(c) for c in r] for r in rings]]},
            "properties": {"@type": "relation", "@id": _fid("r"), **tags}}


def box(minx: float, miny: float, maxx: float, maxy: float) -> list[tuple[float, float]]:
    return [(minx, miny), (maxx, miny), (maxx, maxy), (minx, maxy), (minx, miny)]


CITIES = [
    # name, name_en, kind, lat, lon, population, admin box
    ("תל אביב-יפו", "Tel Aviv-Yafo", "city", 32.0853, 34.7818, 474530, (34.739, 32.030, 34.800, 32.130)),
    ("ירושלים", "Jerusalem", "city", 31.7683, 35.2137, 981711, (35.150, 31.720, 35.260, 31.830)),
    ("חיפה", "Haifa", "city", 32.7940, 34.9896, 290306, (34.950, 32.750, 35.080, 32.840)),
    ("בני ברק", "Bnei Brak", "city", 32.0807, 34.8338, 218357, (34.825, 32.070, 34.845, 32.100)),
    ("רמת גן", "Ramat Gan", "city", 32.0684, 34.8248, 170000, (34.800, 32.040, 34.825, 32.100)),
    ("פתח תקווה", "Petah Tikva", "city", 32.0840, 34.8878, 255000, (34.850, 32.060, 34.930, 32.120)),
    ("באר שבע", "Beersheba", "city", 31.2520, 34.7915, 214000, (34.740, 31.200, 34.850, 31.300)),
    ("מודיעין עילית", "Modi'in Illit", "city", 31.9333, 35.0417, 82000, (35.025, 31.920, 35.060, 31.945)),
    ("רחובות", "Rehovot", "city", 31.8928, 34.8113, 150000, (34.780, 31.870, 34.840, 31.920)),
    ("אילת", "Eilat", "city", 29.5577, 34.9519, 53000, (34.900, 29.500, 34.990, 29.600)),
    ("קרית שמונה", "Kiryat Shmona", "town", 33.2075, 35.5697, 22000, None),
    ("עין גדי", "Ein Gedi", "village", 31.4537, 35.3838, 600, None),
    ("מצפה רמון", "Mitzpe Ramon", "town", 30.6101, 34.8015, 5300, None),
]

NEIGHBOURHOODS = [
    ("פלורנטין", "suburb", 32.0560, 34.7700),
    ("רמת אביב", "suburb", 32.1130, 34.7980),
    ("רחביה", "suburb", 31.7730, 35.2110),
    ("הדר הכרמל", "suburb", 32.8100, 35.0000),
]

STREETS = [
    ("דיזנגוף", "Dizengoff Street", "primary", [(34.7742, 32.0920), (34.7745, 32.0850), (34.7748, 32.0780), (34.7760, 32.0745), (34.7800, 32.0730)]),
    ("שדרות רוטשילד", "Rothschild Boulevard", "secondary", [(34.7700, 32.0610), (34.7740, 32.0640), (34.7775, 32.0670), (34.7800, 32.0705)]),
    ("אבן גבירול", "Ibn Gabirol Street", "primary", [(34.7810, 32.0680), (34.7815, 32.0800), (34.7830, 32.0950)]),
    ("הרצל", "Herzl Street", "tertiary", [(34.7700, 32.0640), (34.7690, 32.0570), (34.7680, 32.0520)]),
    ("יפו", "Jaffa Road", "primary", [(35.2050, 31.7890), (35.2140, 31.7840), (35.2210, 31.7820), (35.2270, 31.7790)]),
    ("קינג ג'ורג'", "King George Street", "secondary", [(35.2160, 31.7820), (35.2170, 31.7770), (35.2180, 31.7730)]),
    ("רבי עקיבא", "Rabbi Akiva Street", "secondary", [(34.8290, 32.0870), (34.8330, 32.0830), (34.8380, 32.0790)]),
    ("ז'בוטינסקי", "Jabotinsky Road", "primary", [(34.8120, 32.0850), (34.8300, 32.0880), (34.8600, 32.0900)]),
    ("שדרות הנשיא", "HaNassi Boulevard", "secondary", [(34.9850, 32.8070), (34.9900, 32.8050), (34.9950, 32.8040)]),
    ("הרצל", "Herzl Street", "secondary", [(35.0000, 32.8160), (35.0040, 32.8145), (35.0080, 32.8130)]),
    ("רבי יהודה הנשיא", "Rabbi Yehuda HaNasi", "residential", [(35.0300, 31.9310), (35.0350, 31.9320), (35.0400, 31.9330)]),
    ("נתיבות המשפט", "Netivot HaMishpat", "residential", [(35.0420, 31.9330), (35.0450, 31.9350), (35.0480, 31.9370)]),
    ("רגר", "Rager Boulevard", "primary", [(34.7960, 31.2400), (34.7980, 31.2500), (34.8000, 31.2600)]),
    ("הרצל", "Herzl Street", "primary", [(34.8100, 31.8850), (34.8110, 31.8930), (34.8120, 31.9000)]),
]

LANDMARKS = [
    # lat, lon, tags
    (32.0776, 34.7867, {"tourism": "museum", "name": "מוזיאון תל אביב לאמנות", "name:en": "Tel Aviv Museum of Art", "wikidata": "Q1199400",
                        "wikipedia": "he:מוזיאון תל אביב לאמנות", "opening_hours": "Mo,We,Sa 10:00-18:00; Tu,Th 10:00-21:00; Fr 10:00-14:00", "website": "www.tamuseum.org.il",
                        "addr:street": "שדרות שאול המלך", "addr:housenumber": "27", "addr:city": "תל אביב-יפו", "wheelchair": "yes"}),
    (32.0753, 34.7752, {"shop": "mall", "name": "דיזנגוף סנטר", "name:en": "Dizengoff Center", "opening_hours": "Su-Th 09:30-22:00; Fr 09:00-15:00",
                        "addr:street": "דיזנגוף", "addr:housenumber": "50", "phone": "+972 3 621 2400"}),
    (32.0745, 34.7920, {"shop": "mall", "name": "קניון עזריאלי", "name:en": "Azrieli Mall", "alt_name": "מרכז עזריאלי", "addr:street": "דרך מנחם בגין", "addr:housenumber": "132"}),
    (32.0688, 34.7717, {"amenity": "place_of_worship", "religion": "jewish", "name": "בית הכנסת הגדול", "addr:street": "אלנבי", "addr:housenumber": "110"}),
    (32.0802, 34.7898, {"amenity": "hospital", "name": "המרכז הרפואי תל אביב", "alt_name": "איכילוב", "name:en": "Tel Aviv Sourasky Medical Center",
                        "emergency": "yes", "phone": "03-6974444", "wikidata": "Q2918563"}),
    (32.0838, 34.7982, {"railway": "station", "name": "תל אביב סבידור מרכז", "name:en": "Tel Aviv Savidor Center", "operator": "רכבת ישראל"}),
    (32.0700, 34.7800, {"railway": "station", "station": "light_rail", "name": "קרליבך", "name:en": "Carlebach"}),
    (32.0855, 34.7680, {"natural": "beach", "name": "חוף גורדון", "name:en": "Gordon Beach"}),
    (32.0779, 34.7746, {"shop": "chemist", "name": "סופר-פארם", "brand": "סופר-פארם", "opening_hours": "Su-Th 08:00-23:00; Fr 08:00-15:00; Sa 19:00-23:00",
                        "addr:street": "דיזנגוף", "addr:housenumber": "52"}),
    (32.0790, 34.7751, {"amenity": "cafe", "name": "ארומה", "brand": "ארומה", "diet:kosher": "yes", "opening_hours": "Su-Th 07:00-23:00; Fr 07:00-15:00; Sa off",
                        "addr:street": "דיזנגוף", "addr:housenumber": "54"}),
    (32.0702, 34.7762, {"amenity": "restaurant", "name": "מסעדה לדוגמה רוטשילד", "cuisine": "italian", "diet:kosher": "only",
                        "opening_hours": "Su-Th 12:00-23:00; Sa 20:00-01:00", "addr:street": "שדרות רוטשילד", "addr:housenumber": "10"}),
    (32.0702, 34.7762, {"amenity": "nightclub", "name": "מועדון"}),
    (32.0560, 34.7710, {"amenity": "place_of_worship", "religion": "christian", "name": "כנסייה"}),
    (32.0731, 34.7810, {"amenity": "atm", "operator": "בנק לאומי"}),
    (32.0763, 34.7840, {"amenity": "parking", "parking": "underground", "name": "חניון הבימה", "fee": "yes"}),
    (32.0720, 34.7780, {"highway": "bus_stop", "name": "רוטשילד/אלנבי", "ref": "20915"}),
    (32.0730, 34.7765, {"amenity": "pharmacy", "name": "בית מרקחת שדרות", "opening_hours": "24/7"}),
    (32.0650, 34.7710, {"amenity": "school", "name": "בית ספר לדוגמה הרצל"}),
    (32.0662, 34.7720, {"amenity": "kindergarten", "name": "גן ילדים לדוגמה"}),
    # Jerusalem
    (31.7767, 35.2345, {"tourism": "attraction", "historic": "yes", "name": "הכותל המערבי", "name:en": "Western Wall", "wikidata": "Q134821",
                        "wikipedia": "he:הכותל המערבי", "opening_hours": "24/7"}),
    (31.7762, 35.2283, {"tourism": "museum", "name": "מגדל דוד", "name:en": "Tower of David", "wikidata": "Q1194117",
                        "phone": "02-6265333", "website": "https://www.tod.org.il",
                        "opening_hours": "Su-Th 09:00-17:00; Fr 09:00-14:00; Sa 09:00-17:00"}),
    (31.7849, 35.2122, {"amenity": "marketplace", "name": "שוק מחנה יהודה", "name:en": "Mahane Yehuda Market", "opening_hours": "Su-Th 08:00-19:00; Fr 08:00-15:00; Sa off"}),
    (31.7725, 35.2042, {"tourism": "museum", "name": "מוזיאון ישראל", "name:en": "Israel Museum", "wikidata": "Q46815"}),
    (31.7893, 35.2033, {"amenity": "bus_station", "name": "התחנה המרכזית ירושלים", "name:en": "Jerusalem Central Bus Station"}),
    (31.7739, 35.1795, {"amenity": "hospital", "name": "המרכז הרפואי שערי צדק", "name:en": "Shaare Zedek Medical Center"}),
    (31.7775, 35.2177, {"amenity": "place_of_worship", "religion": "jewish", "name": "בית הכנסת הגדול ירושלים"}),
    (31.7880, 35.2200, {"amenity": "school", "name": "ישיבת מיר", "name:en": "Mir Yeshiva"}),
    (31.7900, 35.2160, {"amenity": "mikveh", "name": "מקווה טהרה גאולה"}),
    (31.7840, 35.2135, {"amenity": "restaurant", "name": "מסעדה לדוגמה מחנה יהודה", "diet:kosher": "yes"}),
    # Bnei Brak
    (32.0723, 34.8345, {"amenity": "hospital", "name": "מעייני הישועה", "name:en": "Mayanei Hayeshua Medical Center"}),
    (32.0840, 34.8320, {"amenity": "place_of_worship", "religion": "jewish", "name": "בית הכנסת לדוגמה רבי עקיבא"}),
    (32.0830, 34.8335, {"amenity": "restaurant", "name": "מסעדה לדוגמה בני ברק", "diet:kosher": "only"}),
    (32.0810, 34.8350, {"shop": "supermarket", "name": "סופרמרקט לדוגמה", "opening_hours": "Su-Th 07:00-23:00; Fr 07:00-14:00"}),
    # Haifa
    (32.7767, 35.0231, {"amenity": "university", "name": "הטכניון", "official_name": "הטכניון - מכון טכנולוגי לישראל", "name:en": "Technion"}),
    (32.7940, 34.9580, {"natural": "beach", "name": "חוף בת גלים", "name:en": "Bat Galim Beach"}),
    (32.8150, 35.0050, {"amenity": "townhall", "name": "עיריית חיפה"}),
    # Modiin Illit
    (31.9320, 35.0380, {"amenity": "place_of_worship", "religion": "jewish", "name": "בית הכנסת לדוגמה קרית ספר"}),
    (31.9310, 35.0390, {"amenity": "post_office", "name": "דואר מודיעין עילית"}),
    (31.9340, 35.0410, {"amenity": "school", "name": "תיכון מאיר", "addr:street": "נתיבות המשפט", "addr:housenumber": "12"}),
    # nature
    (30.6106, 34.8010, {"tourism": "viewpoint", "name": "תצפית מכתש רמון"}),
    (31.4650, 35.3880, {"natural": "spring", "name": "עין דוד"}),
    (31.7680, 35.1500, {"natural": "spring", "name": "עין חמד"}),
    (29.5040, 34.9180, {"tourism": "attraction", "name": "המצפה התת ימי", "name:en": "Coral World Underwater Observatory"}),
]

#: hiker-relevant points (new POI types)
OUTDOOR_POINTS = [
    (31.4640, 35.3850, {"amenity": "drinking_water"}),
    (31.4642, 35.3852, {"amenity": "toilets", "wheelchair": "yes"}),
    (31.4644, 35.3846, {"amenity": "shelter", "shelter_type": "picnic_shelter"}),
    (32.0725, 34.7782, {"amenity": "shelter", "shelter_type": "public_transport"}),  # bus shelter: not imported
    (31.4630, 35.3800, {"tourism": "information", "information": "guidepost", "name": "צומת שבילים נחל דוד"}),
    (31.4632, 35.3802, {"tourism": "information", "information": "guidepost"}),  # unnamed guidepost: not imported
    (31.9300, 35.0420, {"man_made": "water_tower", "name": "מגדל המים הישן"}),
    (31.4600, 35.3700, {"man_made": "water_well"}),
    (32.0805, 34.7810, {"historic": "memorial", "name": "אנדרטת הנופלים לדוגמה"}),
    (32.0800, 34.7800, {"office": "lawyer", "name": "משרד עורכי דין לדוגמה"}),
    (32.0802, 34.7805, {"craft": "carpenter", "name": "נגריה לדוגמה"}),
    (32.0804, 34.7808, {"craft": "winery", "name": "יקב לדוגמה"}),  # excluded (alcohol)
    (31.3155, 35.3535, {"natural": "saddle", "name": "אוכף לדוגמה"}),
]

#: open ways: named streams / ridges (lines.geojsonseq)
OUTDOOR_LINES = [
    # two touching segments of the same stream -> one POI; a far-away namesake -> a second POI
    ([(35.370, 31.4650), (35.380, 31.4630), (35.390, 31.4620)], {"waterway": "stream", "name": "נחל דוד", "name:en": "Nahal David"}),
    ([(35.390, 31.4620), (35.395, 31.4600)], {"waterway": "stream", "name": "נחל דוד", "intermittent": "yes"}),
    ([(34.950, 32.5000), (34.960, 32.5050)], {"waterway": "stream", "name": "נחל דוד"}),
    ([(34.9800, 32.7500), (35.0000, 32.7300), (35.0200, 32.7000)], {"natural": "ridge", "name": "רכס הכרמל לדוגמה"}),
    ([(35.0100, 32.6000), (35.0200, 32.6100)], {"waterway": "stream"}),  # unnamed: not imported
]

#: marked trails: member ways (lines.geojsonseq) + route relations (relations.jsonl)
TRAIL_WAYS = {
    # Israel National Trail stretch near Ein Gedi; the second way is digitised backwards
    1: [(35.360, 31.400), (35.365, 31.410), (35.370, 31.420)],
    2: [(35.380, 31.440), (35.375, 31.430), (35.370, 31.420)],
    3: [(35.380, 31.440), (35.385, 31.452)],
    # a local blue trail sharing way 3, and a detached piece (not continuous)
    4: [(35.385, 31.452), (35.390, 31.456)],
    5: [(35.300, 31.300), (35.305, 31.305)],
    # mountain-bike route
    6: [(35.000, 32.700), (35.010, 32.710), (35.020, 32.715)],
}
TRAIL_RELATIONS = [
    {"id": 282071, "tags": {"type": "route", "route": "hiking", "network": "nwn", "name": "שביל ישראל", "name:en": "Israel National Trail",
                            "osmc:symbol": "orange:orange:white_right:blue_stripe", "colour": "orange", "operator": "ועדת השבילים"},
     "members": [["w", 1, ""], ["w", 2, ""], ["w", 3, ""]]},
    {"id": 900001, "tags": {"type": "route", "route": "hiking", "network": "lwn", "osmc:symbol": "blue:white:blue_stripe"},
     "members": [["w", 3, ""], ["w", 4, ""], ["w", 5, ""]]},
    {"id": 900002, "tags": {"type": "route", "route": "mtb", "network": "rcn", "name": "סינגל הכרמל לדוגמה", "colour": "#e11e26"},
     "members": [["w", 6, ""]]},
    {"id": 900003, "tags": {"type": "route", "route": "bus", "name": "קו 1"}, "members": [["w", 6, ""]]},  # not a trail
    {"id": 900004, "tags": {"type": "route", "route": "hiking", "state": "proposed", "name": "שביל עתידי"},
     "members": [["w", 1, ""]]},
]
TRAIL_WAY_TAGS = {1: {"highway": "path"}, 2: {"highway": "track", "name": "דרך הנחל"}, 3: {"highway": "path"},
                  4: {}, 5: {"highway": "path"}, 6: {"highway": "track"}}

ADDRESSES = [
    (32.0775, 34.7745, "דיזנגוף", "50"), (32.0779, 34.7746, "דיזנגוף", "52"), (32.0783, 34.7746, "דיזנגוף", "54"),
    (32.0795, 34.7746, "דיזנגוף", "60"), (32.0850, 34.7745, "דיזנגוף", "120"),
    (32.0628, 34.7720, "שדרות רוטשילד", "1"), (32.0655, 34.7757, "שדרות רוטשילד", "20"),
    (31.7835, 35.2150, "יפו", "97"), (31.7838, 35.2140, "יפו", "99"),
    (32.0835, 34.8330, "רבי עקיבא", "100"), (31.9325, 35.0340, "רבי יהודה הנשיא", "8"),
]

CATS = [
    {"amenity": "restaurant"}, {"amenity": "cafe"}, {"amenity": "fast_food"}, {"shop": "supermarket"}, {"shop": "clothes"},
    {"amenity": "pharmacy"}, {"amenity": "bank"}, {"amenity": "school"}, {"amenity": "kindergarten"}, {"leisure": "playground"},
    {"amenity": "place_of_worship", "religion": "jewish"}, {"highway": "bus_stop"}, {"shop": "bakery"}, {"amenity": "clinic"},
]
SAMPLE_NAMES = {"restaurant": "מסעדה", "cafe": "בית קפה", "fast_food": "פלאפל", "supermarket": "מרכול", "clothes": "חנות בגדים",
                "pharmacy": "בית מרקחת", "bank": "בנק", "school": "בית ספר", "kindergarten": "גן", "playground": "גן שעשועים",
                "place_of_worship": "בית כנסת", "bus_stop": "תחנה", "bakery": "מאפייה", "clinic": "מרפאה"}


def generate(out: Path, bulk: int = 0, seed: int = 7) -> tuple[Path, Path]:
    out.mkdir(parents=True, exist_ok=True)
    rnd = random.Random(seed)
    poi: list[dict] = []
    streets: list[dict] = []
    for name, name_en, kind, lat, lon, pop, bb in CITIES:
        poi.append(node(lat, lon, place=kind, name=name, **{"name:en": name_en, "population": str(pop)}))
        if bb:
            poi.append(relation_area([box(*bb)], boundary="administrative", admin_level="8", name=name, **{"name:en": name_en}))
    for name, kind, lat, lon in NEIGHBOURHOODS:
        poi.append(node(lat, lon, place=kind, name=name))
    # a village mapped as an area too (place node + polygon should merge)
    poi.append(way(box(35.375, 31.445, 35.392, 31.462), place="village", name="עין גדי"))
    for lat, lon, tags in LANDMARKS:
        poi.append(node(lat, lon, **tags))
    # same mall mapped as a building polygon -> must be de-duplicated
    poi.append(way(box(34.7745, 32.0748, 34.7760, 32.0758), shop="mall", name="דיזנגוף סנטר", building="retail"))
    # a park polygon
    poi.append(way(box(34.7735, 32.0720, 34.7760, 32.0745), leisure="park", name="גן מאיר", **{"name:en": "Meir Park"}))
    poi.append(way(box(35.380, 31.455, 35.395, 31.470), leisure="nature_reserve", name="שמורת עין גדי"))
    # INPA sites: by operator tag, and by declared name; plus a reserve run by someone else
    poi.append(relation_area([box(35.345, 31.305, 35.360, 31.320)], boundary="protected_area", protect_class="2",
                             name="גן לאומי מצדה", operator="רשות הטבע והגנים", fee="yes", wheelchair="limited",
                             opening_hours="Su-Th 08:00-17:00; Fr 08:00-16:00", website="https://www.parks.org.il",
                             phone="08-6584207", **{"name:en": "Masada National Park"}))
    poi.append(way(box(35.100, 32.850, 35.110, 32.858), boundary="national_park", name="גן לאומי עין אפק"))
    poi.append(way(box(35.050, 32.650, 35.060, 32.660), leisure="nature_reserve", name="שמורת טבע לדוגמה",
                   operator="קרן קיימת לישראל"))
    # one reserve mapped as two separate polygons -> one site with both parts
    poi.append(way(box(34.700, 30.900, 34.710, 30.910), boundary="protected_area", protect_class="1", name="שמורת טבע מצוק לדוגמה"))
    poi.append(way(box(34.712, 30.900, 34.720, 30.905), boundary="protected_area", protect_class="1", name="שמורת טבע מצוק לדוגמה"))
    for lat, lon, tags in OUTDOOR_POINTS:
        poi.append(node(lat, lon, **tags))
    for lat, lon, street, num in ADDRESSES:
        poi.append(node(lat, lon, **{"addr:street": street, "addr:housenumber": num}))
    for name, name_en, hw, coords in STREETS:
        mid = len(coords) // 2
        streets.append(way(coords[:mid + 1], highway=hw, name=name, **{"name:en": name_en}))
        streets.append(way(coords[mid:], highway=hw, name=name, **{"name:en": name_en}))
    streets.append(way([(34.7700, 32.0560), (34.7720, 32.0565)], highway="residential", name="פלורנטין"))

    boxes = [c[6] for c in CITIES if c[6]]
    for i in range(bulk):
        minx, miny, maxx, maxy = rnd.choice(boxes)
        tags = dict(rnd.choice(CATS))
        key = next(v for k, v in tags.items() if k != "religion")
        tags["name"] = f"{SAMPLE_NAMES[key]} לדוגמה {i + 1}"
        if rnd.random() < 0.3:
            tags["opening_hours"] = "Su-Th 08:00-20:00; Fr 08:00-14:00; Sa off"
        if rnd.random() < 0.2:
            tags["diet:kosher"] = "yes"
        poi.append(node(rnd.uniform(miny, maxy), rnd.uniform(minx, maxx), **tags))
        if i % 10 == 0:
            lon0, lat0 = rnd.uniform(minx, maxx), rnd.uniform(miny, maxy)
            streets.append(way([(lon0, lat0), (lon0 + 0.003, lat0 + 0.001)], highway="residential",
                               name=f"רחוב לדוגמה {i // 10 + 1}"))

    # open ways and route relations, as osm.extract_lines() writes them next to poi/streets
    lines = [way(coords, **tags) for coords, tags in OUTDOOR_LINES]
    for wid, coords in TRAIL_WAYS.items():
        f = {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [list(c) for c in coords]},
             "properties": {"@type": "way", "@id": 700000 + wid, **TRAIL_WAY_TAGS[wid]}}
        lines.append(f)
        if f["properties"].get("name"):
            streets.append(f)  # named highways are streets too (osmium exports them twice)
    with open(out / "lines.geojsonseq", "w", encoding="utf-8") as f:
        for feat in lines:
            f.write(json.dumps(feat, ensure_ascii=False) + "\n")
    with open(out / "relations.jsonl", "w", encoding="utf-8") as f:
        for rel in TRAIL_RELATIONS:
            rel = {**rel, "members": [[t, 700000 + ref if t == "w" else ref, role] for t, ref, role in rel["members"]]}
            f.write(json.dumps(rel, ensure_ascii=False) + "\n")

    poi_path, streets_path = out / "poi.geojsonseq", out / "streets.geojsonseq"
    with open(poi_path, "w", encoding="utf-8") as f:
        for feat in poi:
            f.write(json.dumps(feat, ensure_ascii=False) + "\n")
    with open(streets_path, "w", encoding="utf-8") as f:
        for feat in streets:
            f.write("\x1e" + json.dumps(feat, ensure_ascii=False) + "\n")  # RS-prefixed like osmium can emit
    return poi_path, streets_path


#: described hiking routes (``hikes`` table, normally filled from guide sources such as Amud Anan);
#: invented sample content for tests and the demo map
SAMPLE_HIKES = [
    {"src_id": "1", "name": "מחניון הדרום אל עין דוד (מסלול לדוגמה)", "difficulty": "moderate", "duration_min": 180,
     "circular": 0, "season": "סתיו, חורף ואביב",
     "description": "מסלול לדוגמה לאורך שביל ישראל במדבר יהודה, מהחניון הדרומי ועד מעיין עין דוד.\n\n"
                    "המסלול עולה בהדרגה אל מצפה הדקלים ומשם יורד אל הנחל.",
     "coords": [(35.365, 31.410), (35.370, 31.420), (35.375, 31.430), (35.380, 31.440), (35.385, 31.452),
                (35.3865, 31.4585), (35.3878, 31.4645)],
     "extra": {"waypoints": [{"name": "חניון הדרום (לדוגמה)", "lat": 31.410, "lon": 35.365, "note": "חניה מוסדרת"},
                             {"name": "מצפה הדקלים (לדוגמה)", "lat": 31.430, "lon": 35.375, "note": "תצפית לים המלח"},
                             {"name": "עין דוד", "lat": 31.4648, "lon": 35.3878}],
               "tips": ["בקיץ מומלץ לצאת עם שחר."], "water": "ברזייה ליד הכניסה לשמורה", "parking": "חניון מוסדר בתחילת המסלול"}},
    {"src_id": "2", "name": "לולאת עין דוד (מסלול לדוגמה)", "difficulty": "קל", "duration_min": 75, "circular": 1,
     "season": "כל השנה", "description": "מסלול מעגלי קצר לדוגמה לאורך נחל דוד ובחזרה דרך המעיין.",
     "coords": [(35.3850, 31.4638), (35.3800, 31.4632), (35.3750, 31.4642), (35.3720, 31.4648), (35.3760, 31.4660),
                (35.3820, 31.4655), (35.3880, 31.4652), (35.3870, 31.4642), (35.3850, 31.4638)],
     "extra": {}},
]


def add_sample_hikes(con) -> int:
    """Insert :data:`SAMPLE_HIKES` into a built database (needs the ``hikes`` table of db.RICH_DDL)."""
    from hmaps.core import geo

    n = 0
    for h in SAMPLE_HIKES:
        c = h["coords"]
        minx, miny, maxx, maxy = geo.bbox_of(c)
        cur = con.execute(
            "INSERT OR REPLACE INTO hikes (source, src_id, name, description, difficulty, duration_min, length_m, circular, season,"
            " geom, lat, lon, min_lon, min_lat, max_lon, max_lat, url, license, extra) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("sample", h["src_id"], h["name"], h["description"], h["difficulty"], h["duration_min"], round(geo.line_length_m(c), 1),
             h["circular"], h["season"], geo.encode_polyline(c), c[0][1], c[0][0], minx, miny, maxx, maxy, None,
             "תוכן לדוגמה", json.dumps(h["extra"], ensure_ascii=False)))
        con.execute("INSERT OR REPLACE INTO hikes_rtree VALUES (?,?,?,?,?)", (cur.lastrowid, minx, maxx, miny, maxy))
        n += 1
    return n


def extras(out: Path) -> tuple[Path, Path]:
    """``lines.geojsonseq`` and ``relations.jsonl`` written by :func:`generate` next to the other files."""
    return out / "lines.geojsonseq", out / "relations.jsonl"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--bulk", type=int, default=0)
    a = ap.parse_args()
    print(*generate(Path(a.out), a.bulk), *extras(Path(a.out)))
