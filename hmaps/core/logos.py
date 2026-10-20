"""Operator brands: organisation name (GTFS agency, OSM ``operator=*``) -> logo / text badge.

Logo files are genuine files from Wikimedia Commons, bundled under ``web/logos/`` and served
by our own server (never hot-linked – NetFree blocks external images); sources and licences
are listed in ``web/logos/ATTRIBUTION.md`` and ``web/logos/logos.json``. An operator without
a usable logo file gets a neutral text badge (short name on a colour chip) – the colour is
our own choice, not an imitation of the operator's logo.

    >>> brand("אגד")["logo"]
    '/logos/egged.svg'
"""

from __future__ import annotations

import re
from dataclasses import dataclass

LOGO_URL = "/logos/"


@dataclass(frozen=True)
class Brand:
    key: str
    name_he: str
    short: str  # text of the fallback badge (and alt text fallback)
    color: str  # background of the fallback badge
    file: str | None = None  # web/logos/<file>; None = text badge only
    aliases: tuple[str, ...] = ()
    #: which file license applies (the full record lives in web/logos/logos.json)
    license: str | None = None
    source: str | None = None
    author: str | None = None  # required credit for CC BY / BY-SA files
    note: str | None = None    # changes we made to the file (CC BY-SA: indicate modifications)


_C = "https://commons.wikimedia.org/wiki/File:"

BRANDS: tuple[Brand, ...] = (
    # --- nature sites
    Brand("inpa", "רשות הטבע והגנים", "רט״ג", "#2F6B3A", "inpa.svg",
          ("רשות הטבע והגנים", "רשות הטבע והגנים הלאומיים", "רשות הגנים הלאומיים", "רשות שמורות הטבע", "רטג",
           "israel nature and parks authority", "nature and parks authority", "israel nature & parks authority",
           "nature & parks authority", "inpa", "npa"),
          "CC BY-SA 3.0", _C + "Flag_of_Israel_Nature_and_Parks_Authority.svg", "Meronim",
          "viewBox cropped to the central emblem of the flag; artwork unchanged"),
    Brand("kkl", "קרן קיימת לישראל", "קק״ל", "#3E7C3A", "kkl.svg",
          ("קרן קיימת לישראל", "קרן קימת לישראל", "קקל", "קקל-jnf", "קקל jnf", "kkl", "kkl-jnf", "kkl jnf", "jnf",
           "jewish national fund", "keren kayemeth leisrael", "keren kayemet leisrael"),
          "CC BY-SA 3.0", _C + "Flag_of_Jewish_National_Fund.svg", "Meronim"),
    # --- public transport (names as in the Israeli MOT GTFS agency.txt and OSM operator=*)
    Brand("egged", "אגד", "אגד", "#2E7D32", "egged.svg", ("אגד", "egged", "אגד אגודה שיתופית"),
          "Public domain (PD-textlogo)", _C + "Egged_logo.svg"),
    Brand("egged-taavura", "אגד תעבורה", "אגד ת׳", "#4E6E3A", None, ("אגד תעבורה", "egged taavura")),
    Brand("dan", "דן", "דן", "#1D4E89", None, ("דן", "dan", "דן חברה לתחבורה ציבורית", "dan bus company")),
    Brand("dan-south", "דן בדרום", "דן", "#1D4E89", None, ("דן בדרום", "dan badarom")),
    Brand("dan-beersheva", "דן באר שבע", "דן", "#1D4E89", None, ("דן באר שבע", "dan beersheva", "dan beer sheva")),
    Brand("dan-north", "דן צפון", "דן", "#1D4E89", None, ("דן צפון", "dan north")),
    Brand("metropoline", "מטרופולין", "מטרופולין", "#00598A", "metropoline.svg",
          ("מטרופולין", "מטרופולין תחבורה ציבורית", "metropoline"),
          "Public domain (PD-textlogo)", _C + "Metropoline_buses_logo.svg"),
    Brand("kavim", "קווים", "קווים", "#C2410C", "kavim.svg", ("קווים", "kavim"),
          "CC BY-SA 4.0", _C + "KavimLogo.svg", "סטודיו DoArt"),
    Brand("afikim", "אפיקים", "אפיקים", "#6D28D9", None, ("אפיקים", "afikim")),
    Brand("electra-afikim", "אלקטרה אפיקים", "אלקטרה", "#5B21B6", None,
          ("אלקטרה אפיקים", "אלקטרה אפיקים תחבורה", "electra afikim")),
    Brand("superbus", "סופרבוס", "סופרבוס", "#0E7490", "superbus.svg", ("סופרבוס", "superbus"),
          "Public domain (PD-textlogo)", _C + "Superbus_logo.svg"),
    Brand("nateev-express", "נתיב אקספרס", "נתיב", "#B45309", "nateev-express.svg",
          ("נתיב אקספרס", "נתיב אקספרס תחבורה ציבורית", "nateev express", "nativ express"),
          "Public domain (PD-textlogo)", _C + "Nateev_Express_Logo.svg"),
    Brand("tnufa", "תנופה", "תנופה", "#0F766E", None, ("תנופה", "tnufa")),
    Brand("galim", "גלים", "גלים", "#0369A1", "galim.png", ("גלים", "galim"),
          "Public domain (PD-textlogo)", _C + "Logo_top_1.png"),
    Brand("extra", "אקסטרה", "אקסטרה", "#BE123C", None, ("אקסטרה", "אקסטרה ירושלים", "extra")),
    Brand("beit-shemesh-express", "בית שמש אקספרס", "ב״ש אקספרס", "#9A3412", "beit-shemesh-express.svg",
          ("בית שמש אקספרס", "beit shemesh express"),
          "Public domain (PD-textlogo)", _C + "%D7%91%D7%99%D7%AA_%D7%A9%D7%9E%D7%A9_%D7%90%D7%A7%D7%A1%D7%A4%D7%A8%D7%A1.svg"),
    Brand("gb-tours", "גי.בי.טורס", "GB", "#475569", None, ("גיביטורס", "גי בי טורס", "גי.בי.טורס", "gb tours")),
    Brand("golan", "מועצה אזורית גולן", "גולן", "#57534E", None, ("מועצה אזורית גולן", "רמה תחבורה ציבורית גולן")),
    Brand("israel-railways", "רכבת ישראל", "רכבת", "#1E3A8A", "israel-railways.svg",
          ("רכבת ישראל", "israel railways", "rakevet israel"),
          "Public domain (PD-textlogo)", _C + "Israel_Railways_Logo.svg"),
    Brand("carmelit", "כרמלית", "כרמלית", "#B91C1C", "carmelit.svg", ("כרמלית", "carmelit"),
          "Public domain (PD-textlogo)", _C + "Carmelit.svg"),
    Brand("metronit", "מטרונית", "מטרונית", "#7C3AED", "metronit.svg", ("מטרונית", "metronit"),
          "Public domain (PD-textlogo)", _C + "Metronit_Logo.svg"),
    Brand("jerusalem-light-rail", "הרכבת הקלה בירושלים", "רק״ל", "#B91C1C", "jerusalem-light-rail.svg",
          ("הרכבת הקלה בירושלים", "רכבת קלה ירושלים", "ירושלים רכבת קלה", "סיטיפס", "citypass", "כפיר", "cfir",
           "jerusalem light rail"),
          "Public domain (PD-textlogo)", _C + "J-NET.svg"),
    Brand("dankal", "דנקל – הרכבת הקלה בגוש דן", "דנקל", "#B91C1C", "dankal.svg",
          ("דנקל", "dankal", "נתע", "נתיבי תחבורה עירוניים", "nta", "הרכבת הקלה בתל אביב", "רכבת קלה תל אביב",
           "tel aviv light rail"),
          "Public domain (PD-textlogo)", _C + "Dankal_Logo.svg"),
    Brand("tevel", "תבל מטרו", "תבל", "#DC2626", "tevel.svg", ("תבל", "תבל מטרו", "tevel", "tevel metro"),
          "CC0 1.0", _C + "Tevel_Metro_logo.svg", "מקף־עברי"),
)

BY_KEY: dict[str, Brand] = {b.key: b for b in BRANDS}

_QUOTES = re.compile(r"[\"'״׳`´‘’“”]")
_SEP = re.compile(r"[\s\-–—_/,.()]+")
_SUFFIX = re.compile(r"\s+(?:בעמ|בע\s?מ|ltd|inc)$")


def normalize(name: str | None) -> str:
    """Comparable form: lower case, no quotes / gershayim, punctuation and runs of spaces -> one space.

    ``'קק"ל'``, ``"קק״ל"`` and ``"קקל"`` all become ``"קקל"``; ``"נתיב אקספרס - תחבורה ציבורית בע\"מ"``
    becomes ``"נתיב אקספרס תחבורה ציבורית"``.
    """
    s = _QUOTES.sub("", (name or "").lower())
    s = _SEP.sub(" ", s).strip()
    return _SUFFIX.sub("", s).strip()


def _build_index() -> tuple[dict[str, str], list[tuple[str, str]]]:
    exact: dict[str, str] = {}
    for b in BRANDS:
        for a in (b.name_he, b.key, *b.aliases):
            exact.setdefault(normalize(a), b.key)
    # longest alias first, so "דן בדרום" wins over "דן" and "אגד תעבורה" over "אגד"
    prefixes = sorted(exact.items(), key=lambda kv: -len(kv[0]))
    return exact, prefixes


_EXACT, _PREFIXES = _build_index()


def match(name: str | None) -> str | None:
    """Brand key for an organisation name, or None.

    Exact (normalised) alias first, then an alias that starts the name followed by a word
    boundary (``"אגד – מחוז ירושלים"`` -> egged, but ``"דנדי"`` -> None).
    """
    n = normalize(name)
    if not n:
        return None
    if n in _EXACT:
        return _EXACT[n]
    for alias, key in _PREFIXES:
        if len(alias) >= 2 and n.startswith(alias + " "):
            return key
    return None


def logo_url(key: str | None) -> str | None:
    b = BY_KEY.get(key or "")
    return LOGO_URL + b.file if b and b.file else None


def brand_of(key: str | None, name: str | None = None) -> dict | None:
    """API shape of a brand: ``{key, name, short, color, logo}`` (logo None = draw a text badge)."""
    b = BY_KEY.get(key or "")
    if b is None:
        return None
    return {"key": b.key, "name": name or b.name_he, "short": b.short, "color": b.color, "logo": logo_url(b.key)}


def brand(name: str | None) -> dict | None:
    """Brand of an operator / agency *name* (the name itself is kept for display)."""
    return brand_of(match(name), name)


def poi_brand(extra: dict) -> dict | None:
    """Brand of a POI from its stored OSM tags (``extra``): ``operator=*`` first, then the INPA flag
    that the ETL derived for nature reserves / national parks (``extra["inpa"]``)."""
    for k in ("operator", "operator:he", "operator:en", "brand"):
        key = match(extra.get(k))
        if key:
            return brand_of(key, extra.get(k) if k != "operator:en" else None)
    if extra.get("inpa"):
        return brand_of("inpa")
    return None


def manifest() -> dict[str, dict]:
    """Machine-readable record of the bundled files (kept equal to web/logos/logos.json by a test)."""
    out: dict[str, dict] = {}
    for b in BRANDS:
        if b.file:
            rec = {"file": b.file, "name_he": b.name_he, "source": b.source, "license": b.license}
            rec.update({k: v for k, v in (("author", b.author), ("note", b.note)) if v})
            out[b.key] = rec
    return out
