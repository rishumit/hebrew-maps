"""Understanding free-text Hebrew map queries.

Examples handled::

    "מסעדות בתל אביב"          -> category=food/restaurant, location="תל אביב"
    "בית מרקחת פתוח ליד"        -> pharmacy, open_now, near_me
    "מסעדה כשרה באזור חיפה"     -> restaurant, kosher, location="חיפה"
    "רח' הרצל 12, רחובות"       -> street="הרצל", number="12", city="רחובות"
    "32.0853, 34.7818"          -> coordinates

The parser is purely linguistic; resolving place names against the data is
the search engine's job. Everything here is normalized text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .categories import MAX_PHRASE_WORDS, PHRASES
from .hebrew import QUERY_STOPWORDS, expand_abbreviations, normalize, strip_prefixes, tokens

KOSHER_WORDS = frozenset({"כשר", "כשרה", "כשרים", "כשרות", "בכשרות", "למהדרין", "מהדרין", "kosher"})
OPEN_WORDS = frozenset({"פתוח", "פתוחה", "פתוחים", "פתוחות", "open"})
NOW_WORDS = frozenset({"עכשיו", "כעת", "now"})
NEAR_WORDS = frozenset({
    "ליד", "לידי", "קרוב", "קרובה", "קרובים", "בקרבת", "באזור", "באיזור", "בסביבה", "בסביבת",
    "בסביבתי", "סמוך", "near", "nearby", "around", "close",
})
NEAR_ME_TAIL = frozenset({"אלי", "אליי", "אליו", "לי", "הבית", "me", "here", "כאן", "פה"})
IN_WORDS = frozenset({"ב", "in", "at"})

_COORD_RE = re.compile(r"^\s*(-?\d{1,3}(?:\.\d+)?)\s*[,\s]\s*(-?\d{1,3}(?:\.\d+)?)\s*$")
_HOUSE_RE = re.compile(r"^\d{1,4}[א-ת]?$")


@dataclass
class ParsedQuery:
    raw: str
    tokens: list[str]
    coords: tuple[float, float] | None = None  # (lat, lon)
    category: str | None = None
    sub: str | None = None
    location_text: str | None = None
    near_me: bool = False
    kosher: bool = False
    open_now: bool = False
    street_text: str | None = None
    house_number: str | None = None
    city_text: str | None = None
    name_tokens: list[str] = field(default_factory=list)  # tokens for name search
    category_hint: str | None = None  # sub/category id that boosts ranking only

    @property
    def is_category(self) -> bool:
        return self.category is not None

    @property
    def is_address(self) -> bool:
        return self.house_number is not None and bool(self.street_text)


def _parse_coords(text: str) -> tuple[float, float] | None:
    m = _COORD_RE.match(text)
    if not m:
        return None
    a, b = float(m.group(1)), float(m.group(2))
    # Israel: lat 29..34, lon 34..36 – accept both orders.
    if 29 <= a <= 34 and 34 <= b <= 36.5:
        return a, b
    if 29 <= b <= 34 and 34 <= a <= 36.5:
        return b, a
    if -90 <= a <= 90 and -180 <= b <= 180 and ("." in m.group(1) and "." in m.group(2)):
        return a, b
    return None


def _match_phrase(toks: list[str], start: int) -> tuple[int, tuple[str, str | None]] | None:
    """Longest category phrase starting at *start* (prefix-stripping the first word)."""
    for n in range(min(MAX_PHRASE_WORDS, len(toks) - start), 0, -1):
        first_variants = strip_prefixes(toks[start])
        for first in first_variants:
            phrase = " ".join([first, *toks[start + 1:start + n]])
            hit = PHRASES.get(phrase)
            if hit:
                return n, hit
    return None


def _strip_location_prefix(toks: list[str]) -> tuple[list[str], bool]:
    """Remove 'ב'/'ליד'/'באזור' from the head of a location phrase.

    Returns the cleaned tokens and whether a proximity word was present.
    """
    near = False
    while toks and (toks[0] in NEAR_WORDS or toks[0] in IN_WORDS or toks[0] in {"של", "ל", "to"}):
        near = near or toks[0] in NEAR_WORDS
        toks = toks[1:]
    if toks and toks[0][:1] in {"ב", "ל"} and len(toks[0]) > 2:
        toks = [toks[0][1:], *toks[1:]]
    return toks, near


def parse(raw: str) -> ParsedQuery:
    coords = _parse_coords(raw)
    toks_all = expand_abbreviations(tokens(raw))
    q = ParsedQuery(raw=raw, tokens=toks_all, coords=coords)
    if coords or not toks_all:
        return q

    # --- flags ---------------------------------------------------------------
    toks: list[str] = []
    for i, t in enumerate(toks_all):
        if t in KOSHER_WORDS:
            q.kosher = True
        elif t in OPEN_WORDS:
            q.open_now = True
        elif t in NOW_WORDS and q.open_now:
            continue
        else:
            toks.append(t)
    if toks and toks[-1] in NEAR_ME_TAIL and len(toks) >= 2 and toks[-2] in NEAR_WORDS:
        toks = toks[:-1]
        q.near_me = True

    # --- category intent ("<category> [ב|ליד] <place>") -------------------------
    hit = _match_phrase(toks, 0) if toks else None
    rest: list[str] = []
    suffix_form = False
    if hit:
        rest = toks[hit[0]:]
    else:
        # "<place> <category>" e.g. "חיפה מסעדות"
        for start in range(1, len(toks)):
            h = _match_phrase(toks, start)
            if h and start + h[0] == len(toks):
                hit, rest, suffix_form = h, toks[:start], True
                break
    if hit:
        _, (cat, sub) = hit
        rest = [t for t in rest if t not in QUERY_STOPWORDS]
        has_prep = bool(rest) and (
            rest[0] in NEAR_WORDS or rest[0] in IN_WORDS or (rest[0].startswith("ב") and len(rest[0]) > 2)
        )
        if not rest or has_prep or suffix_form:
            loc, near = _strip_location_prefix(rest) if not suffix_form else (rest, False)
            if loc and len(loc) == 1 and loc[0] in NEAR_ME_TAIL:
                loc, near = [], True
            q.near_me = q.near_me or (near and not loc)
            q.category, q.sub = cat, sub
            q.location_text = " ".join(loc) or None
            q.name_tokens = toks
            return q
        # "<category word> <name>" (e.g. "קפה לנדוור", "מסעדת השף") is a name query;
        # keep the category only as a ranking hint.
        q.category_hint = sub or cat

    # --- address: "<street> <number> [,] <city>" or "<city>, <street> <number>" ---
    clean = [t for t in toks if t not in QUERY_STOPWORDS]

    def split_address(ts: list[str]) -> tuple[list[str], str, list[str]] | None:
        for i, t in enumerate(ts):
            if _HOUSE_RE.match(t) and i > 0:
                return ts[:i], t, ts[i + 1:]
        return None

    if "," in raw:
        left, right = raw.split(",", 1)
        lt = [t for t in expand_abbreviations(tokens(left)) if t not in QUERY_STOPWORDS]
        rt = [t for t in expand_abbreviations(tokens(right)) if t not in QUERY_STOPWORDS]
        sl, sr = split_address(lt), split_address(rt)
        if sl:
            q.street_text, q.house_number = " ".join(sl[0]), sl[1]
            q.city_text = " ".join(_strip_location_prefix(sl[2] + rt)[0]) or None
        elif sr:
            q.street_text, q.house_number = " ".join(sr[0]), sr[1]
            q.city_text = " ".join(lt + sr[2]) or None
    else:
        sp = split_address(clean)
        if sp:
            q.street_text, q.house_number = " ".join(sp[0]), sp[1]
            q.city_text = " ".join(_strip_location_prefix(sp[2])[0]) or None

    # "<name> ב<city>" / "<name> ליד <place>" for plain name queries
    q.name_tokens = clean or toks
    for i in range(1, len(toks)):
        if toks[i] in NEAR_WORDS:
            loc, _ = _strip_location_prefix(toks[i:])
            if loc:
                q.location_text = " ".join(loc)
                q.name_tokens = toks[:i]
            break
    return q
