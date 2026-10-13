"""Hebrew text analysis for search.

The search engine never sees raw user text. Both indexed names and queries go
through :func:`normalize`, which makes spelling variants collide:

* niqqud / cantillation marks are removed;
* final letters (ך ם ן ף ץ) become their regular forms;
* geresh / gershayim and ASCII quotes are dropped (ת"א -> תא);
* maqaf / hyphen become spaces;
* doubled vav / yod (כתיב מלא) collapse (קריית -> קרית, תקווה -> תקוה);
* Latin text is lower-cased and accents are removed.

On the query side, :func:`query_variants` adds prefix-stripped alternatives
(בתל -> תל, והמוזיאון -> מוזיאון) and :data:`ABBREVIATIONS` expands the usual
Israeli abbreviations. :func:`fix_keyboard_layout` converts text typed with the
wrong keyboard layout (e.g. "sh,bdu;" -> "דיזנגוף").
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

FINAL_LETTERS = str.maketrans({"ך": "כ", "ם": "מ", "ן": "נ", "ף": "פ", "ץ": "צ"})

# Hebrew points & cantillation: U+0591..U+05C7 except letters/punctuation we keep.
_MARKS_RE = re.compile("[֑-ׇֽֿׁׂׅׄ]")
_QUOTES_RE = re.compile("[\"'׳״‘’“”`´]")
_SEPARATORS_RE = re.compile(r"[־\-‐-―_/\\,.;:!?()\[\]{}|+*&#@~<>=]+")
_SPACES_RE = re.compile(r"\s+")
_HEBREW_RE = re.compile("[א-ת]")
_LATIN_RE = re.compile("[a-z]")

#: Prefix letters ("אותיות השימוש") that may be glued to a Hebrew word.
_PREFIXES = (
    "וכש", "ושה", "וכשה", "שב", "שה", "של", "שמ", "וב", "וה", "ול", "ומ", "וש", "וכ",
    "מה", "בה", "לה", "כש", "כשה", "מש", "כה", "ב", "ה", "ו", "ל", "מ", "ש", "כ",
)
_PREFIXES = tuple(sorted(set(_PREFIXES), key=len, reverse=True))

#: Abbreviations, keyed by their *normalized* form (quotes already removed).
ABBREVIATIONS: dict[str, str] = {
    "תא": "תל אביב",
    "תלא": "תל אביב",
    "בש": "באר שבע",
    "פת": "פתח תקוה",
    "רג": "רמת גן",
    "ראשלצ": "ראשון לציון",
    "רשלצ": "ראשון לציון",
    "קש": "קרית שמונה",
    "בב": "בני ברק",
    "ביח": "בית חולים",
    "ביכנ": "בית כנסת",
    "בכנ": "בית כנסת",
    "ביס": "בית ספר",
    "קופח": "קופת חולים",
    "קח": "קופת חולים",
    "שד": "שדרות",
    "שדר": "שדרות",
    "סמ": "סמטת",
    "כיכ": "כיכר",
    "ככ": "כיכר",
    "אונ": "אוניברסיטה",
    "tlv": "tel aviv",
    "jlm": "jerusalem",
    "st": "street",
    "ave": "avenue",
    "blvd": "boulevard",
    "rd": "road",
}

#: Generic words that should not be *required* to match (dropped from queries).
QUERY_STOPWORDS = frozenset({
    "רחוב", "רח", "מספר", "עיר", "העיר", "ישוב", "יישוב", "את", "של", "על",
    "street", "the", "of", "in",
})


def _strip_accents_latin(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch) or _HEBREW_RE.match(ch))


@lru_cache(maxsize=65536)
def normalize(text: str | None) -> str:
    """Return the canonical searchable form of *text* (may be empty)."""
    if not text:
        return ""
    t = unicodedata.normalize("NFKC", text)  # presentation forms -> base letters
    t = _MARKS_RE.sub("", t)
    t = t.lower()
    t = _strip_accents_latin(t)
    t = _MARKS_RE.sub("", t)
    t = _QUOTES_RE.sub("", t)
    t = _SEPARATORS_RE.sub(" ", t)
    t = t.translate(FINAL_LETTERS)
    t = t.replace("וו", "ו").replace("יי", "י")
    return _SPACES_RE.sub(" ", t).strip()


def tokens(text: str | None) -> list[str]:
    """Normalized tokens of *text*."""
    n = normalize(text)
    return n.split(" ") if n else []


def is_hebrew(text: str) -> bool:
    return bool(_HEBREW_RE.search(text or ""))


def is_latin(text: str) -> bool:
    return bool(_LATIN_RE.search((text or "").lower())) and not is_hebrew(text)


def strip_prefixes(token: str) -> list[str]:
    """Possible base forms of a Hebrew *token* without grammatical prefixes.

    The token itself is always returned first. Only variants of 2+ letters
    are produced, so short names are not destroyed.
    """
    out = [token]
    if not _HEBREW_RE.match(token[:1] or " "):
        return out
    for p in _PREFIXES:
        if token.startswith(p) and len(token) - len(p) >= 2:
            base = token[len(p):]
            if base not in out:
                out.append(base)
    return out


def expand_abbreviations(toks: list[str]) -> list[str]:
    """Replace known abbreviations by their full (normalized) form."""
    out: list[str] = []
    for t in toks:
        full = ABBREVIATIONS.get(t)
        if full:
            out.extend(normalize(full).split(" "))
        else:
            out.append(t)
    return out


def query_tokens(text: str) -> list[str]:
    """Tokens of a user query: normalized, abbreviations expanded, stopwords dropped."""
    toks = expand_abbreviations(tokens(text))
    kept = [t for t in toks if t not in QUERY_STOPWORDS]
    return kept or toks


def query_variants(token: str) -> list[str]:
    """Alternatives that a single query token may match (itself first)."""
    return strip_prefixes(token)


# --- keyboard layout -------------------------------------------------------

_EN_TO_HE = dict(zip(
    "qwertyuiopasdfghjkl;'zxcvbnm,./",
    "/'קראטוןםפשדגכעיחלךף,זסבהנמצתץ.",
))
_HE_TO_EN = {v: k for k, v in _EN_TO_HE.items() if v not in "/'.,"}


def fix_keyboard_layout(text: str) -> str | None:
    """If *text* looks like Hebrew typed on an English layout (or vice versa),
    return the converted text; otherwise ``None``."""
    if not text:
        return None
    low = text.lower()
    if is_latin(low) and not is_hebrew(low):
        conv = "".join(_EN_TO_HE.get(ch, ch) for ch in low)
        return conv if is_hebrew(conv) else None
    if is_hebrew(text) and not _LATIN_RE.search(low):
        conv = "".join(_HE_TO_EN.get(ch, ch) for ch in text)
        return conv if _LATIN_RE.search(conv) else None
    return None


# --- fuzzy matching ---------------------------------------------------------

#: Letter pairs that Hebrew speakers commonly confuse (cheaper substitution).
_SIMILAR = {frozenset(p) for p in ("טת", "כק", "סש", "אע", "חכ", "בו", "צס", "גק")}


def edit_distance(a: str, b: str, limit: float = 3.0) -> float:
    """Weighted Damerau-Levenshtein distance with Hebrew-aware substitutions.

    Similar-sounding letters cost 0.5, transpositions 1. Returns a value
    larger than *limit* as soon as the distance is known to exceed it.
    """
    if a == b:
        return 0.0
    la, lb = len(a), len(b)
    if abs(la - lb) > limit:
        return limit + 1
    prev2: list[float] | None = None
    prev = [float(j) for j in range(lb + 1)]
    for i in range(1, la + 1):
        cur = [float(i)] + [0.0] * lb
        row_min = cur[0]
        ca = a[i - 1]
        for j in range(1, lb + 1):
            cb = b[j - 1]
            if ca == cb:
                sub = 0.0
            elif frozenset((ca, cb)) in _SIMILAR:
                sub = 0.5
            else:
                sub = 1.0
            v = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + sub)
            if prev2 is not None and i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb:
                v = min(v, prev2[j - 2] + 1)
            cur[j] = v
            row_min = min(row_min, v)
        if row_min > limit:
            return limit + 1
        prev2, prev = prev, cur
    return prev[lb]


def max_typos(token: str) -> float:
    """How many edits we tolerate for a token of this length."""
    n = len(token)
    if n <= 3:
        return 0.0
    if n <= 5:
        return 1.0
    return 2.0


def trigrams(word: str) -> set[str]:
    padded = f" {word} "
    return {padded[i:i + 3] for i in range(len(padded) - 2)}


# --- display helpers --------------------------------------------------------

_TO_FINAL = {"כ": "ך", "מ": "ם", "נ": "ן", "פ": "ף", "צ": "ץ"}


def restore_finals(text: str) -> str:
    """Put final letters back at word ends of normalized text (for display)."""
    words = []
    for w in text.split(" "):
        if len(w) > 1 and w[-1] in _TO_FINAL:
            w = w[:-1] + _TO_FINAL[w[-1]]
        words.append(w)
    return " ".join(words)


def format_distance(meters: float) -> str:
    """Hebrew human readable distance (e.g. ``450 מ׳`` / ``3.2 ק״מ``)."""
    if meters < 1000:
        return f"{int(round(meters / 10.0) * 10)} מ׳"
    km = meters / 1000.0
    return f"{km:.1f} ק״מ" if km < 10 else f"{int(round(km))} ק״מ"


def format_duration(seconds: float) -> str:
    """Hebrew human readable duration (e.g. ``שעה ו-5 דק׳``)."""
    minutes = int(round(seconds / 60.0))
    if minutes < 1:
        return "פחות מדקה"
    if minutes < 60:
        return "דקה" if minutes == 1 else f"{minutes} דק׳"
    hours, mins = divmod(minutes, 60)
    h = "שעה" if hours == 1 else ("שעתיים" if hours == 2 else f"{hours} שעות")
    return h if mins == 0 else f"{h} ו-{mins} דק׳"
