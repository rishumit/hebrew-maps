"""POI taxonomy: OpenStreetMap tags -> unified Hebrew categories.

Single source of truth used by the ETL (classification), the API
(``/api/categories``, search synonyms) and indirectly by the frontend
(which receives colours and icon ids from the API).

Each sub-category declares the OSM tag rules that select it. Rules are
evaluated in declaration order; the first match wins, so specific rules
(e.g. ``railway=station`` + ``station=light_rail``) come before generic ones.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable, Mapping

from .hebrew import normalize

Tags = Mapping[str, str]
Predicate = Callable[[Tags], bool]


@dataclass(frozen=True)
class Rule:
    key: str
    values: frozenset[str] | None = None  # None = any value except "no"
    when: Predicate | None = None

    def matches(self, tags: Tags) -> bool:
        v = tags.get(self.key)
        if v is None or v == "no":
            return False
        if self.values is not None and v not in self.values:
            return False
        return self.when(tags) if self.when else True


def R(key: str, *values: str, when: Predicate | None = None) -> Rule:
    return Rule(key, frozenset(values) if values else None, when)


@dataclass(frozen=True)
class SubCategory:
    id: str
    name_he: str
    plural_he: str
    name_en: str
    icon: str
    rules: tuple[Rule, ...]
    importance: float = 0.3
    synonyms: tuple[str, ...] = ()
    allow_unnamed: bool = False
    min_zoom: int = 15  # zoom from which single POIs are drawn individually
    fallback: bool = False  # catch-all: evaluated only after every specific rule


@dataclass(frozen=True)
class Category:
    id: str
    name_he: str
    name_en: str
    color: str
    icon: str
    subs: tuple[SubCategory, ...] = field(default_factory=tuple)
    synonyms: tuple[str, ...] = ()


# --- predicates ---------------------------------------------------------------

def _jewish(t: Tags) -> bool:
    rel = t.get("religion")
    if rel == "jewish":
        return True
    if rel:
        return False
    name = normalize(t.get("name", ""))
    return "בית כנסת" in name or "בית הכנסת" in name or name.startswith("כנסת ")


def _mikveh_bath(t: Tags) -> bool:
    return t.get("bath:type") == "mikveh" or "מקוה" in normalize(t.get("name", ""))


def _yeshiva(t: Tags) -> bool:
    if t.get("school") == "yeshiva" or t.get("amenity") == "yeshiva":
        return True
    name = normalize(t.get("name", ""))
    return name.startswith(("ישיבת ", "ישיבה ", "כולל ", "מכון ", "בית מדרש ", "בית המדרש "))


def _light_rail(t: Tags) -> bool:
    return t.get("station") in {"light_rail", "subway", "tram"} or t.get("light_rail") == "yes"


def _heavy_rail(t: Tags) -> bool:
    return not _light_rail(t) and t.get("station") not in {"funicular"}


def _bus_platform(t: Tags) -> bool:
    return t.get("bus") == "yes" or t.get("highway") == "bus_stop"


def _useful_parking(t: Tags) -> bool:
    if t.get("access") in {"private", "no", "permissive_private"}:
        return False
    if t.get("name"):
        return True
    if t.get("parking") in {"multi-storey", "underground"}:
        return True
    try:
        return int(t.get("capacity", "0")) >= 50
    except ValueError:
        return False


def _airport(t: Tags) -> bool:
    return bool(t.get("iata") or t.get("icao")) and t.get("aerodrome:type") != "private"


def _named(t: Tags) -> bool:
    return bool(t.get("name") or t.get("name:he"))


# --- רשות הטבע והגנים (INPA) ------------------------------------------------------
# OSM rarely carries operator=* on Israeli reserves/parks (measured: ~1 boundary in
# 2026), so a declared-site name ("גן לאומי …", "שמורת טבע …") is accepted as a
# second, weaker signal. The source is kept so the UI can say which one applied.
_INPA_OPERATORS_EN = (
    "israel nature and parks authority", "nature and parks authority", "israel nature & parks authority",
    "nature & parks authority", "israel nature and national parks protection authority",
    "israel national parks authority", "nature reserves authority", "national parks authority",
)
_INPA_OPERATORS_HE = ("רשות הטבע והגנים", "רשות הטבע והגנים הלאומיים", "רשות שמורות הטבע", "רשות הגנים הלאומיים")
_INPA_SHORT = {"inpa", "npa", "רטג", "רט\"ג", "רט״ג"}
_INPA_NAME_PREFIXES = ("גן לאומי ", "גן הלאומי ", "שמורת טבע ", "שמורת הטבע ")
_PROTECTED = {"national_park", "protected_area"}


def inpa_source(t: Tags) -> str | None:
    """``"operator"`` / ``"name"`` when the site is run by the Israel Nature and Parks Authority."""
    for k in ("operator", "operator:he", "operator:en", "operator:short", "owner"):
        v = (t.get(k) or "").strip()
        if not v:
            continue
        low = v.lower()
        if low in _INPA_SHORT or any(op in low for op in _INPA_OPERATORS_EN):
            return "operator"
        nv = normalize(v)
        if nv in _INPA_SHORT or any(normalize(op) in nv for op in _INPA_OPERATORS_HE):
            return "operator"
    if t.get("operator"):
        return None  # explicitly operated by someone else (KKL, a council, …)
    name = (t.get("name:he") or t.get("name") or "").strip()
    if name.startswith(_INPA_NAME_PREFIXES):
        return "name"
    return None


def _inpa(t: Tags) -> bool:
    is_site = t.get("boundary") in _PROTECTED or t.get("leisure") == "nature_reserve"
    return is_site and inpa_source(t) is not None


def _named_line_or_point(t: Tags) -> bool:
    return _named(t) and t.get("area") != "yes"


# Shops that are never included in the dataset.
EXCLUDED_SHOPS = frozenset({
    "alcohol", "wine", "erotic", "cannabis", "tobacco", "e-cigarette", "weapons",
    "pawnbroker", "vacant", "no", "yes", "disused", "religion", "lottery", "bookmaker",
})


def _regular_shop(t: Tags) -> bool:
    return t.get("shop") not in EXCLUDED_SHOPS


#: same policy as EXCLUDED_SHOPS (no alcohol / tattoo businesses)
EXCLUDED_CRAFTS = frozenset({"winery", "brewery", "distillery", "tattoo", "yes", "no"})


def _regular_craft(t: Tags) -> bool:
    return _named(t) and t.get("craft") not in EXCLUDED_CRAFTS and not t.get("shop")


def _regular_office(t: Tags) -> bool:
    return _named(t) and t.get("office") not in {"government", "no", "vacant", "disused"}


def _info_point(t: Tags) -> bool:
    return t.get("information") in {"board", "guidepost", "map"} and _named(t)


OFFICE_KINDS_HE: dict[str, str] = {
    "lawyer": "עורך דין", "estate_agent": "תיווך נדל\"ן", "insurance": "ביטוח", "accountant": "רואה חשבון",
    "tax_advisor": "יועץ מס", "notary": "נוטריון", "architect": "אדריכל", "company": "חברה", "it": "מחשוב",
    "ngo": "עמותה", "association": "עמותה", "charity": "צדקה וחסד", "foundation": "קרן", "union": "איגוד",
    "diplomatic": "נציגות דיפלומטית", "educational_institution": "מוסד חינוך", "research": "מחקר",
    "telecommunication": "תקשורת", "employment_agency": "השמה", "travel_agent": "סוכנות נסיעות",
    "coworking": "חלל עבודה משותף", "newspaper": "עיתון", "advertising_agency": "פרסום", "consulting": "ייעוץ",
    "financial": "פיננסים", "water_utility": "תאגיד מים", "energy_supplier": "חשמל ואנרגיה",
    "religion": "מוסד דת", "political_party": "מפלגה", "physician": "רופא", "moving_company": "הובלות",
    "guide": "מורה דרך", "security": "אבטחה", "administrative": "מנהלה",
}
CRAFT_KINDS_HE: dict[str, str] = {
    "carpenter": "נגר", "tailor": "חייט", "dressmaker": "תפירה", "blacksmith": "נפח", "photographer": "צלם",
    "sculptor": "פסל", "caterer": "קייטרינג", "electrician": "חשמלאי", "confectionery": "ממתקים",
    "glaziery": "זגג", "hvac": "מיזוג אוויר", "shoemaker": "סנדלר", "window_construction": "חלונות",
    "jeweller": "צורף", "locksmith": "מנעולן", "upholsterer": "רפד", "stonemason": "סתת",
    "metal_construction": "מסגר", "print_shop": "דפוס", "watchmaker": "שען", "bakery": "מאפייה",
    "joiner": "נגר", "key_cutter": "שכפול מפתחות", "clockmaker": "שען", "handicraft": "אומנות",
    "plumber": "אינסטלטור", "painter": "צבעי", "gardener": "גנן", "pottery": "קדרות", "electronics_repair": "תיקון מכשירים",
}


SHOP_KINDS_HE: dict[str, str] = {
    "clothes": "ביגוד", "shoes": "נעליים", "books": "ספרים", "hardware": "כלי עבודה",
    "doityourself": "עשה זאת בעצמך", "electronics": "אלקטרוניקה", "mobile_phone": "סלולר",
    "optician": "אופטיקה", "florist": "פרחים", "hairdresser": "מספרה", "beauty": "קוסמטיקה",
    "jewelry": "תכשיטים", "toys": "צעצועים", "furniture": "רהיטים", "greengrocer": "ירקן",
    "butcher": "קצביה", "kiosk": "קיוסק", "department_store": "כלבו", "car": "סוכנות רכב",
    "car_repair": "מוסך", "car_parts": "חלקי חילוף", "tyres": "צמיגים", "bicycle": "אופניים",
    "stationery": "כלי כתיבה", "gift": "מתנות", "laundry": "מכבסה", "dry_cleaning": "ניקוי יבש",
    "pet": "חיות מחמד", "variety_store": "כלבו", "sports": "ציוד ספורט", "computer": "מחשבים",
    "travel_agency": "סוכנות נסיעות", "deli": "מעדנייה", "confectionery": "ממתקים",
    "seafood": "דגים", "beverages": "משקאות", "tailor": "חייט", "copyshop": "צילום מסמכים",
    "garden_centre": "משתלה", "photo": "צילום", "houseware": "כלי בית", "fabric": "בדים",
    "baby_goods": "מוצרי תינוקות", "bag": "תיקים", "cosmetics": "קוסמטיקה", "perfumery": "בשמים",
    "dairy": "מוצרי חלב", "spices": "תבלינים", "nuts": "פיצוחים", "coffee": "קפה",
    "tea": "תה", "chocolate": "שוקולד", "pastry": "קונדיטוריה", "frozen_food": "קפואים",
    "farm": "תוצרת חקלאית", "interior_decoration": "עיצוב הבית", "kitchen": "מטבחים",
    "lighting": "תאורה", "paint": "צבעים", "bed": "מצעים", "appliance": "מוצרי חשמל",
    "electrical": "חשמל", "hearing_aids": "מכשירי שמיעה", "medical_supply": "ציוד רפואי",
    "musical_instrument": "כלי נגינה", "video_games": "משחקי מחשב", "watches": "שעונים",
    "second_hand": "יד שנייה", "charity": "יד שנייה", "craft": "יצירה", "art": "אמנות",
    "outdoor": "ציוד מחנאות", "locksmith": "מנעולן", "massage": "עיסוי", "tattoo": "",
    "funeral_directors": "", "money_lender": "", "trade": "סחר", "wholesale": "סיטונאות",
}


# --- taxonomy -------------------------------------------------------------------

CATEGORIES: tuple[Category, ...] = (
    Category("tourism", "אתרי תיירות", "Tourism", "#D63C3C", "landmark", synonyms=("תיירות", "אטרקציות", "אתרים", "טיולים"), subs=(
        SubCategory("museum", "מוזיאון", "מוזיאונים", "Museum", "museum", (R("tourism", "museum"),), 0.8, ("מוזאון",)),
        SubCategory("archaeological", "אתר עתיקות", "אתרי עתיקות", "Archaeological site", "columns",
                    (R("historic", "archaeological_site"),), 0.7, ("עתיקות", "ארכיאולוגיה", "חפירות")),
        SubCategory("zoo", "גן חיות", "גני חיות", "Zoo", "paw", (R("tourism", "zoo", "aquarium"),), 0.75, ("ספארי", "אקווריום")),
        SubCategory("theme_park", "פארק שעשועים", "פארקי שעשועים", "Theme park", "star", (R("tourism", "theme_park"), R("leisure", "water_park")), 0.65, ("לונה פארק", "פארק מים")),
        SubCategory("attraction", "אטרקציה", "אטרקציות", "Attraction", "star", (R("tourism", "attraction"),), 0.6, ("אתר תיירות",)),
        SubCategory("memorial", "אנדרטה / אתר הנצחה", "אנדרטאות ואתרי הנצחה", "Memorial", "memorial",
                    (R("historic", "memorial", when=_named),), 0.45, ("אנדרטה", "אנדרטאות", "הנצחה", "גלעד", "memorial")),
        SubCategory("historic", "אתר היסטורי", "אתרים היסטוריים", "Historic site", "landmark",
                    (R("historic", "monument", "ruins", "castle", "fort", "city_gate", "tower", "aqueduct", "tomb", when=_named),),
                    0.5, ("היסטוריה", "מצודה")),
        SubCategory("gallery", "גלריה", "גלריות", "Gallery", "frame", (R("tourism", "gallery"),), 0.4, ()),
        SubCategory("tourist_info", "מרכז מבקרים", "מרכזי מבקרים", "Visitor centre", "info",
                    (R("tourism", "information", when=lambda t: t.get("information") in {"office", "visitor_centre"}),), 0.4, ("מודיעין לתייר", "מידע לתייר")),
    )),
    Category("religion", "בתי כנסת ומקוואות", "Synagogues & mikvaot", "#9A6A12", "synagogue", synonyms=("בתי כנסת",), subs=(
        SubCategory("synagogue", "בית כנסת", "בתי כנסת", "Synagogue", "synagogue",
                    (R("amenity", "place_of_worship", when=_jewish), R("building", "synagogue", when=_jewish)), 0.5,
                    ("בית הכנסת", "שול", "מנין", "מניין", "תפילה", "synagogue"), allow_unnamed=True),
        SubCategory("mikveh", "מקווה", "מקוואות", "Mikveh", "drop",
                    (R("amenity", "mikveh"), R("amenity", "public_bath", when=_mikveh_bath), R("mikveh", "yes")), 0.5,
                    ("מקוה", "מקווה טהרה", "mikveh", "mikvah"), allow_unnamed=True),
        SubCategory("yeshiva", "ישיבה / בית מדרש", "ישיבות ובתי מדרש", "Yeshiva", "book",
                    (R("amenity", "school", "college", "yeshiva", when=_yeshiva),), 0.45, ("ישיבה", "ישיבות", "כולל", "בית מדרש", "תורה")),
    )),
    Category("food", "מזון", "Food", "#D9620B", "utensils", synonyms=("אוכל", "מזון", "לאכול", "food"), subs=(
        SubCategory("restaurant", "מסעדה", "מסעדות", "Restaurant", "utensils", (R("amenity", "restaurant"),), 0.6, ("restaurant", "מסעדת")),
        SubCategory("cafe", "בית קפה", "בתי קפה", "Cafe", "cup", (R("amenity", "cafe"),), 0.55, ("קפה", "קפיטריה", "cafe", "coffee")),
        SubCategory("fast_food", "מזון מהיר", "מזון מהיר", "Fast food", "burger", (R("amenity", "fast_food", "food_court"),), 0.45,
                    ("פלאפל", "שווארמה", "פיצה", "פיצריה", "המבורגר", "סושי", "טייק אווי", "fast food", "pizza")),
        SubCategory("bakery", "מאפייה", "מאפיות", "Bakery", "bread", (R("shop", "bakery", "pastry", "confectionery"),), 0.45, ("מאפה", "לחם", "קונדיטוריה", "bakery")),
        SubCategory("ice_cream", "גלידרייה", "גלידריות", "Ice cream", "icecream", (R("amenity", "ice_cream"), R("shop", "ice_cream")), 0.4, ("גלידה", "גלידריה")),
    )),
    Category("lodging", "לינה", "Lodging", "#8B35D6", "bed", synonyms=("לינה", "מקום לישון", "אירוח"), subs=(
        SubCategory("hotel", "מלון", "מלונות", "Hotel", "bed", (R("tourism", "hotel", "motel"),), 0.6, ("בית מלון", "hotel")),
        SubCategory("guest_house", "צימר / בית הארחה", "צימרים ובתי הארחה", "Guest house", "house",
                    (R("tourism", "guest_house", "chalet", "apartment"),), 0.45, ("צימר", "צימרים", "בית הארחה", "בקתה")),
        SubCategory("hostel", "אכסניה", "אכסניות", "Hostel", "bed", (R("tourism", "hostel"),), 0.45, ("הוסטל", "אכסנייה", "hostel")),
        SubCategory("camp_site", "חניון לילה", "חניוני לילה", "Camp site", "tent", (R("tourism", "camp_site", "caravan_site"),), 0.4, ("קמפינג", "חניון לילה")),
    )),
    Category("shopping", "מסחר ושירותים", "Shopping & services", "#C9306F", "bag", synonyms=("קניות", "חנויות", "מסחר", "shopping"), subs=(
        SubCategory("mall", "קניון", "קניונים", "Shopping mall", "bag", (R("shop", "mall"),), 0.7, ("מרכז קניות", "מרכז מסחרי", "mall")),
        SubCategory("market", "שוק", "שווקים", "Market", "basket", (R("amenity", "marketplace"),), 0.6, ("שוק", "market")),
        SubCategory("supermarket", "סופרמרקט", "סופרמרקטים", "Supermarket", "cart", (R("shop", "supermarket", "wholesale"),), 0.55,
                    ("סופר", "מרכול", "רשת שיווק", "supermarket")),
        SubCategory("convenience", "מכולת", "מכולות", "Convenience store", "cart", (R("shop", "convenience", "kiosk"),), 0.35,
                    ("פיצוציה", "מינימרקט", "קיוסק", "מכולת")),
        SubCategory("bank", "בנק", "בנקים", "Bank", "bank", (R("amenity", "bank"),), 0.5, ("סניף בנק", "bank")),
        SubCategory("atm", "כספומט", "כספומטים", "ATM", "cash", (R("amenity", "atm"),), 0.3, ("מכשיר בנק", "מזומן", "atm"), allow_unnamed=True),
        SubCategory("exchange", "חלפן כספים", "חלפני כספים", "Currency exchange", "cash", (R("amenity", "bureau_de_change"),), 0.3, ("המרת מטבע", "צינג")),
        SubCategory("shop", "חנות", "חנויות", "Shop", "bag", (R("shop", when=_regular_shop),), 0.25, ("חנות",), fallback=True),
        SubCategory("office", "משרד", "משרדים", "Office", "briefcase", (R("office", when=_regular_office),), 0.2, ("משרדים",), fallback=True),
        SubCategory("craft", "בעל מלאכה", "בעלי מלאכה", "Craft", "wrench", (R("craft", when=_regular_craft),), 0.2,
                    ("בעלי מקצוע", "סדנה"), fallback=True),
    )),
    Category("education", "חינוך", "Education", "#4F46E5", "graduation", synonyms=("חינוך", "לימודים", "education"), subs=(
        SubCategory("university", "אוניברסיטה", "אוניברסיטאות", "University", "graduation", (R("amenity", "university"),), 0.75, ("אונ", "קמפוס", "university")),
        SubCategory("college", "מכללה", "מכללות", "College", "graduation", (R("amenity", "college"),), 0.55, ("סמינר", "college")),
        SubCategory("school", "בית ספר", "בתי ספר", "School", "school", (R("amenity", "school"),), 0.45, ("ביס", "תלמוד תורה", "תת", "school")),
        SubCategory("kindergarten", "גן ילדים", "גני ילדים", "Kindergarten", "blocks", (R("amenity", "kindergarten", "childcare"),), 0.3,
                    ("גנון", "מעון", "מעון יום", "פעוטון")),
        SubCategory("library", "ספרייה", "ספריות", "Library", "book", (R("amenity", "library"),), 0.5, ("ספריה", "library")),
    )),
    Category("health", "בריאות", "Health", "#0B8A80", "health", synonyms=("בריאות", "רפואה", "רופא", "health"), subs=(
        SubCategory("hospital", "בית חולים", "בתי חולים", "Hospital", "hospital", (R("amenity", "hospital"), R("healthcare", "hospital")), 0.8,
                    ("ביח", "מרכז רפואי", "hospital")),
        SubCategory("emergency", "מוקד חירום רפואי", "מוקדי חירום רפואיים", "Emergency medical", "ambulance",
                    (R("emergency", "ambulance_station"), R("healthcare", "emergency"), R("amenity", "emergency_service")), 0.55,
                    ("מדא", "מגן דוד אדום", "טרם", "מוקד רפואי", "חדר מיון")),
        SubCategory("clinic", "מרפאה", "מרפאות", "Clinic", "health", (R("amenity", "clinic", "doctors"), R("healthcare", "clinic", "doctor", "centre")), 0.5,
                    ("קופת חולים", "רופא", "מכבי", "כללית", "מאוחדת", "לאומית", "clinic")),
        SubCategory("pharmacy", "בית מרקחת", "בתי מרקחת", "Pharmacy", "pill", (R("amenity", "pharmacy"), R("healthcare", "pharmacy"), R("shop", "chemist")), 0.5,
                    ("פארם", "סופר פארם", "תרופות", "pharmacy"), allow_unnamed=True),
        SubCategory("dentist", "רופא שיניים", "רופאי שיניים", "Dentist", "tooth", (R("amenity", "dentist"), R("healthcare", "dentist")), 0.35, ("מרפאת שיניים", "שיניים")),
    )),
    Category("transport", "תחבורה", "Transport", "#2160D8", "bus", synonyms=("תחבורה", "תחבורה ציבורית", "transport"), subs=(
        SubCategory("airport", "שדה תעופה", "שדות תעופה", "Airport", "plane", (R("aeroway", "aerodrome", when=_airport),), 0.85, ("נמל תעופה", "נתבג", "airport")),
        SubCategory("light_rail", "תחנת רכבת קלה", "תחנות רכבת קלה", "Light rail station", "tram",
                    (R("railway", "station", "halt", when=_light_rail), R("railway", "tram_stop")), 0.45, ("רכבת קלה", "רקל", "light rail")),
        SubCategory("train_station", "תחנת רכבת", "תחנות רכבת", "Train station", "train", (R("railway", "station", "halt", when=_heavy_rail),), 0.75,
                    ("רכבת", "רכבת ישראל", "train")),
        SubCategory("bus_station", "תחנה מרכזית", "תחנות מרכזיות", "Bus terminal", "bus", (R("amenity", "bus_station"),), 0.6, ("מסוף אוטובוסים", "תחנה מרכזית")),
        SubCategory("bus_stop", "תחנת אוטובוס", "תחנות אוטובוס", "Bus stop", "bus",
                    (R("highway", "bus_stop"), R("public_transport", "platform", when=_bus_platform)), 0.15,
                    ("אוטובוס", "תחנה", "bus stop"), allow_unnamed=True, min_zoom=16),
        SubCategory("fuel", "תחנת דלק", "תחנות דלק", "Fuel", "fuel", (R("amenity", "fuel"),), 0.45, ("דלק", "פז", "סונול", "דור אלון", "gas station"), allow_unnamed=True),
        SubCategory("charging", "עמדת טעינה", "עמדות טעינה", "EV charging", "bolt", (R("amenity", "charging_station"),), 0.3, ("טעינה", "טעינת רכב חשמלי"), allow_unnamed=True),
        SubCategory("parking", "חניון", "חניונים", "Parking", "parking", (R("amenity", "parking", when=_useful_parking),), 0.25, ("חניה", "חנייה", "parking"), allow_unnamed=True),
        SubCategory("taxi", "תחנת מוניות", "תחנות מוניות", "Taxi stand", "taxi", (R("amenity", "taxi"),), 0.25, ("מונית", "מוניות"), allow_unnamed=True),
        SubCategory("bicycle_rental", "השכרת אופניים", "השכרת אופניים", "Bicycle rental", "bike", (R("amenity", "bicycle_rental"),), 0.2, ("תל אופן", "אופניים שיתופיים"), allow_unnamed=True),
    )),
    Category("leisure", "פנאי וספורט", "Leisure & sport", "#1E8F3E", "tree", synonyms=("פנאי", "ספורט", "בילוי", "leisure"), subs=(
        SubCategory("beach", "חוף", "חופים", "Beach", "wave", (R("natural", "beach"), R("leisure", "beach_resort")), 0.65, ("חוף ים", "beach")),
        SubCategory("park", "פארק / גן ציבורי", "פארקים וגנים", "Park", "tree", (R("leisure", "park", "garden", when=lambda t: t.get("access") not in {"private", "no"}),), 0.45,
                    ("פארק", "גינה", "גן ציבורי", "גן", "park")),
        SubCategory("playground", "גן שעשועים", "גני שעשועים", "Playground", "swing", (R("leisure", "playground"),), 0.25, ("מגלשות", "נדנדות", "playground"), allow_unnamed=True),
        SubCategory("stadium", "אצטדיון", "אצטדיונים", "Stadium", "stadium", (R("leisure", "stadium"),), 0.5, ("איצטדיון", "היכל ספורט")),
        SubCategory("sports_centre", "מרכז ספורט", "מרכזי ספורט", "Sports centre", "dumbbell", (R("leisure", "sports_centre", "fitness_centre", "sports_hall"),), 0.35,
                    ("חדר כושר", "מכון כושר", "כושר", "gym")),
        SubCategory("swimming_pool", "בריכה", "בריכות", "Swimming pool", "swim", (R("leisure", "swimming_pool", when=lambda t: t.get("access") not in {"private", "no"} and bool(t.get("name"))),), 0.35,
                    ("בריכת שחייה", "בריכה עירונית", "pool")),
        SubCategory("pitch", "מגרש ספורט", "מגרשי ספורט", "Sports pitch", "ball", (R("leisure", "pitch", "track"),), 0.2, ("מגרש", "מגרש כדורגל", "מגרש כדורסל")),
    )),
    Category("nature", "טבע ונוף", "Nature", "#0E7490", "mountain", synonyms=("טבע", "נוף", "טיול", "nature"), subs=(
        SubCategory("inpa_site", "אתר רט\"ג", "אתרי רט\"ג", "Nature and Parks Authority site", "leaf",
                    (R("boundary", "national_park", "protected_area", when=_inpa), R("leisure", "nature_reserve", when=_inpa)), 0.8,
                    ("רשות הטבע והגנים", "אתרי רשות הטבע והגנים", "רטג", "אתר רטג", "inpa")),
        SubCategory("nature_reserve", "שמורת טבע / גן לאומי", "שמורות טבע וגנים לאומיים", "Nature reserve / national park", "leaf",
                    (R("boundary", "national_park"), R("leisure", "nature_reserve"), R("boundary", "protected_area", when=_named)), 0.75,
                    ("גן לאומי", "גנים לאומיים", "שמורה", "שמורות", "שמורת טבע", "שמורות טבע", "national park")),
        SubCategory("viewpoint", "תצפית", "תצפיות", "Viewpoint", "eye", (R("tourism", "viewpoint"),), 0.45, ("נקודת תצפית", "מצפה", "viewpoint"), allow_unnamed=True),
        SubCategory("spring", "מעיין", "מעיינות", "Spring", "drop", (R("natural", "spring"),), 0.5, ("מעין", "spring"), allow_unnamed=True),
        SubCategory("waterfall", "מפל", "מפלים", "Waterfall", "drop", (R("waterway", "waterfall"), R("natural", "waterfall")), 0.5, ("מפל מים",)),
        SubCategory("peak", "הר / פסגה", "הרים ופסגות", "Peak", "mountain", (R("natural", "peak", "volcano", when=_named),), 0.45, ("פסגה",)),
        SubCategory("cave", "מערה", "מערות", "Cave", "cave", (R("natural", "cave_entrance"),), 0.4, ("מערת",)),
        SubCategory("picnic", "חניון יום", "חניוני יום", "Picnic site", "tree", (R("tourism", "picnic_site"),), 0.3, ("פיקניק", "חניון פיקניק", "על האש"), allow_unnamed=True),
        SubCategory("stream", "נחל / ואדי", "נחלים", "Stream / wadi", "stream",
                    (R("waterway", "stream", "wadi", "river", when=_named_line_or_point),), 0.45, ("ואדי", "וואדי", "נהר", "נהרות", "wadi", "stream")),
        SubCategory("ridge", "רכס", "רכסים", "Ridge", "mountain", (R("natural", "ridge", "arete", when=_named),), 0.4, ("שלוחה",)),
        SubCategory("saddle", "אוכף", "אוכפים", "Saddle", "mountain", (R("natural", "saddle", "mountain_pass", when=_named),), 0.35, ("מעבר הררי",)),
        SubCategory("cliff", "מצוק", "מצוקים", "Cliff", "cliff", (R("natural", "cliff", when=_named),), 0.4, ("צוק", "מצוקים")),
        SubCategory("valley", "עמק / גיא", "עמקים וגאיות", "Valley / gorge", "valley", (R("natural", "valley", "gorge", when=_named),), 0.4,
                    ("ערוץ", "גאיות")),
        SubCategory("well", "באר מים", "בארות מים", "Water well", "well", (R("man_made", "water_well", "well"),), 0.25,
                    ("בארות",), allow_unnamed=True, min_zoom=16),
    )),
    Category("facilities", "מתקנים לציבור", "Public amenities", "#3F6212", "tap", synonyms=("מתקנים", "שירותים למטיילים"), subs=(
        SubCategory("drinking_water", "ברזיית מים", "ברזיות מים", "Drinking water", "tap", (R("amenity", "drinking_water", "water_point"),), 0.15,
                    ("ברזייה", "ברזיה", "מי שתייה", "מים לשתייה", "drinking water"), allow_unnamed=True, min_zoom=16),
        SubCategory("toilets", "שירותים", "שירותים", "Public toilets", "toilets",
                    (R("amenity", "toilets", when=lambda t: t.get("access") not in {"private", "no", "customers"}),), 0.2,
                    ("בית שימוש", "נוחיות", "תאי שירותים", "toilets", "wc"), allow_unnamed=True, min_zoom=16),
        SubCategory("shelter", "מחסה / סככה", "מחסות וסככות", "Shelter", "shelter",
                    (R("amenity", "shelter", when=lambda t: t.get("shelter_type") != "public_transport" and t.get("bus") != "yes"),), 0.2,
                    ("סככות", "מחסות"), allow_unnamed=True, min_zoom=16),
        SubCategory("trail_info", "שלט הסבר / תמרור שבילים", "שלטי הסבר ותמרורי שבילים", "Information board / guidepost", "signpost",
                    (R("tourism", "information", when=_info_point),), 0.2, ("שלטי הסבר", "תמרור", "guidepost")),
        SubCategory("water_tower", "מגדל מים", "מגדלי מים", "Water tower", "watertower", (R("man_made", "water_tower", when=_named),), 0.3,
                    ("מגדל המים",)),
    )),
    Category("government", "שירותי ציבור", "Public services", "#4A5568", "building", synonyms=("ממשל", "שירותים ציבוריים", "רשויות"), subs=(
        SubCategory("townhall", "עירייה / מועצה", "עיריות ומועצות", "Town hall", "building", (R("amenity", "townhall"),), 0.65,
                    ("עירייה", "עיריה", "מועצה", "מועצה מקומית", "מועצה אזורית", "בניין העירייה")),
        SubCategory("post_office", "סניף דואר", "סניפי דואר", "Post office", "mail", (R("amenity", "post_office"),), 0.45, ("דואר", "דואר ישראל", "post office"), allow_unnamed=True),
        SubCategory("police", "תחנת משטרה", "תחנות משטרה", "Police", "shield", (R("amenity", "police"),), 0.5, ("משטרה", "police"), allow_unnamed=True),
        SubCategory("fire_station", "תחנת כיבוי אש", "תחנות כיבוי אש", "Fire station", "flame", (R("amenity", "fire_station"),), 0.4, ("כבאות", "מכבי אש", "כיבוי אש"), allow_unnamed=True),
        SubCategory("courthouse", "בית משפט", "בתי משפט", "Courthouse", "scale", (R("amenity", "courthouse"),), 0.5, ("משפט",)),
        SubCategory("government_office", "משרד ממשלתי", "משרדי ממשלה", "Government office", "building",
                    (R("office", "government"), R("government", when=lambda t: t.get("office") is not None)), 0.4,
                    ("משרד הפנים", "ביטוח לאומי", "לשכה", "משרד ממשלה")),
        SubCategory("community_centre", "מתנ\"ס / מרכז קהילתי", "מתנ\"סים ומרכזים קהילתיים", "Community centre", "people", (R("amenity", "community_centre"),), 0.4,
                    ("מתנס", "מרכז קהילתי", "מועדון")),
    )),
)

CATEGORY_BY_ID: dict[str, Category] = {c.id: c for c in CATEGORIES}
SUB_BY_ID: dict[str, SubCategory] = {s.id: s for c in CATEGORIES for s in c.subs}
PARENT_OF: dict[str, str] = {s.id: c.id for c in CATEGORIES for s in c.subs}


def classify(tags: Tags) -> tuple[str, str] | None:
    """Return ``(category_id, sub_id)`` for an OSM object, or ``None``.

    Objects without a name are kept only for sub-categories that allow it.
    """
    named = bool(tags.get("name") or tags.get("name:he"))
    for fallback_pass in (False, True):
        for cat in CATEGORIES:
            for sub in cat.subs:
                if sub.fallback != fallback_pass:
                    continue
                for rule in sub.rules:
                    if rule.matches(tags):
                        if not named and not sub.allow_unnamed:
                            return None
                        return cat.id, sub.id
    return None


def is_kosher(tags: Tags) -> bool | None:
    v = (tags.get("diet:kosher") or tags.get("kosher") or "").lower()
    if v in {"yes", "only"}:
        return True
    if v == "no":
        return False
    if "kosher" in (tags.get("cuisine") or "").lower():
        return True
    return None


def shop_kind_he(tags: Tags) -> str | None:
    kind = SHOP_KINDS_HE.get(tags.get("shop", ""))
    return kind or None


def kind_he(tags: Tags, sub: str) -> str | None:
    """Specific Hebrew label inside a broad sub-category ("חנות" → "ספרים", "משרד" → "עורך דין")."""
    if sub == "shop":
        return shop_kind_he(tags)
    if sub == "office":
        return OFFICE_KINDS_HE.get(tags.get("office", "")) or None
    if sub == "craft":
        return CRAFT_KINDS_HE.get(tags.get("craft", "")) or None
    if sub == "valley" and tags.get("natural") == "gorge":
        return "ערוץ / קניון"
    if sub == "stream" and tags.get("waterway") == "river":
        return "נהר"
    return None


#: sub-categories that are mapped mostly as (open) lines; segments are merged per name
LINE_SUBS = frozenset({"stream", "ridge", "valley", "cliff"})
#: sub-categories whose polygon is kept and drawn (parks layer)
SHAPE_SUBS = frozenset({"nature_reserve", "inpa_site"})
#: a category filter also includes these narrower sub-categories
SUB_INCLUDES: dict[str, tuple[str, ...]] = {"nature_reserve": ("inpa_site",)}
#: "notable" sub-categories – places one expects to find on Wikipedia / Commons
#: (sites, landmarks, nature). The photo / description KPIs are measured on these;
#: bus stops, shops, playgrounds, drinking fountains … will never have an article.
NOTABLE_SUBS = frozenset({
    # tourism
    "museum", "archaeological", "zoo", "theme_park", "attraction", "memorial", "historic", "tourist_info",
    # nature
    "inpa_site", "nature_reserve", "waterfall", "peak", "cave", "stream", "ridge", "valley", "cliff", "spring",
    # landmarks
    "beach", "stadium", "university", "hospital", "airport", "train_station", "mall", "market", "hotel",
})


def expand_sub(sub: str) -> tuple[str, ...]:
    return (sub, *SUB_INCLUDES.get(sub, ()))


# --- search vocabulary ------------------------------------------------------------

def _phrases(c: Category | SubCategory) -> Iterable[str]:
    if isinstance(c, SubCategory):
        yield from (c.name_he, c.plural_he, c.name_en, *c.synonyms)
        for part in (c.name_he, c.plural_he):
            if " / " in part:
                yield from part.split(" / ")
    else:
        yield from (c.name_he, c.name_en, *c.synonyms)


def build_phrase_index() -> dict[str, tuple[str, str | None]]:
    """Normalized phrase -> (category_id, sub_id or None)."""
    idx: dict[str, tuple[str, str | None]] = {}
    for cat in CATEGORIES:
        for p in _phrases(cat):
            n = normalize(p)
            if n:
                idx.setdefault(n, (cat.id, None))
        for sub in cat.subs:
            for p in _phrases(sub):
                n = normalize(p)
                if n:
                    idx[n] = (cat.id, sub.id)  # sub-category wins over category
    return idx


PHRASES: dict[str, tuple[str, str | None]] = build_phrase_index()
MAX_PHRASE_WORDS = max(len(p.split(" ")) for p in PHRASES)


def as_json() -> list[dict]:
    """Category tree for the public API."""
    return [
        {
            "id": c.id,
            "name_he": c.name_he,
            "name_en": c.name_en,
            "color": c.color,
            "icon": c.icon,
            "sub_categories": [
                {"id": s.id, "name_he": s.name_he, "plural_he": s.plural_he, "name_en": s.name_en,
                 "icon": s.icon, "min_zoom": s.min_zoom}
                for s in c.subs
            ],
        }
        for c in CATEGORIES
    ]
