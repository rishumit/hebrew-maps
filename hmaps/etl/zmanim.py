"""Prayer times (זמני תפילות / מניינים) of synagogues from Nedarim Plus' national project.

Source: https://www.matara.pro/nedarimplus/zmanim/ ("פרוייקט זמני תפילות הארצי").
Three public JSON endpoints of ``Zmanim/Manage.aspx`` are used:

* ``Action=GetLocation`` – every listed synagogue ("mosad"): ``MosadId`` and ``lat*lon``
  (a mosad may appear several times with slightly different locations).
* ``Action=GetMosadMinyan&MosadId=`` – the weekly timetable of one mosad: ``T`` prayer,
  ``H`` time, ``D`` day letters (א..ו, ש), ``L`` hall, ``C`` note, plus ``ShortId`` /
  ``DirectId`` (phone line) and ``Message``. No name or address.
* ``Action=GetNearestMinyan&lat=&lng=&radius=KM`` – the minyanim of the next 24 hours
  around a point (≤40 rows) WITH the synagogue name ``L``, address ``A``, ``M`` mosad id
  and ``W`` location. It is the only source of names, so the fetch sweeps it around every
  mosad (first a 30 m circle that often names several neighbours at once, then a 1 m circle
  for the rest). Mosads with nothing in the next 24 hours (e.g. Shabbat-only) keep the name
  of the previous snapshot; run the fetch on different days to fill them in.

Two steps (``python -m hmaps.etl zmanim``)::

    --fetch   network -> data/sources/zmanim/zmanim.json   (snapshot, cached HTTP in data/cache/)
    --apply   snapshot -> live maps.db: synagogues_ext + minyanim, link / add synagogue POIs

Logos (images.matara.pro) are deliberately not used.
"""

from __future__ import annotations

import difflib
import json
import logging
import math
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

from ..core import db
from ..core.hebrew import normalize
from .rich import USER_AGENT, name_match

log = logging.getLogger(__name__)

BASE = "https://www.matara.pro/nedarimplus/Zmanim/Manage.aspx"
SOURCE_HE = "נדרים פלוס – פרוייקט זמני תפילות הארצי"
SOURCE_URL = "https://www.matara.pro/nedarimplus/zmanim/"
PHONE_MAIN = "03-7630585"
OSM_PREFIX = "zm:"            # pois.osm of synagogues added from this source
SUBS = ("synagogue", "yeshiva")  # OSM POIs a mosad may be linked to
DAY_LETTERS = "אבגדהוש"       # Sunday .. Shabbat
_TIME_RE = re.compile(r"^\s*(\d{1,2})[:.](\d{2})\s*$")


def snapshot_path(data_dir: Path) -> Path:
    return Path(data_dir) / "sources" / "zmanim" / "zmanim.json"


# =========================================================================== HTTP

class Fetcher:
    """Polite GET with an on-disk cache: ≤ *workers* parallel requests, ≥ *interval* s between starts."""

    def __init__(self, cache_path: Path | None, ttl_days: float = 3.0, interval: float = 0.2,
                 opener: Callable[[str], str] | None = None):
        self.ttl = ttl_days * 86400
        self.interval = interval
        self._open = opener or self._urlopen
        self._lock = threading.Lock()
        self._next = 0.0
        self.requests = self.hits = self.errors = 0
        self._db = None
        if cache_path is not None:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(cache_path, check_same_thread=False)
            self._db.execute("CREATE TABLE IF NOT EXISTS http (k TEXT PRIMARY KEY, ts REAL, body BLOB)")
            self._db.commit()

    @staticmethod
    def _urlopen(url: str) -> str:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT + " zmanim-sync",
                                                   "Accept": "application/json, text/plain, */*"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8-sig", "replace")

    def get(self, params: dict) -> str | None:
        url = BASE + "?" + urllib.parse.urlencode(params)
        if self._db is not None:
            with self._lock:
                row = self._db.execute("SELECT ts, body FROM http WHERE k = ?", (url,)).fetchone()
            if row and time.time() - row[0] < self.ttl:
                self.hits += 1
                return zlib.decompress(row[1]).decode("utf-8")
        for attempt in range(3):
            with self._lock:
                wait = self._next - time.time()
                self._next = max(time.time(), self._next) + self.interval
            if wait > 0:
                time.sleep(wait)
            try:
                self.requests += 1
                body = self._open(url)
                break
            except urllib.error.HTTPError as e:
                if e.code in (403, 418, 451):
                    self.errors += 1
                    raise RuntimeError(f"blocked ({e.code}) – {BASE}")
                time.sleep(2 + 3 * attempt)
            except Exception:  # noqa: BLE001 – best effort
                time.sleep(2 + 3 * attempt)
        else:
            self.errors += 1
            return None
        if self._db is not None:
            with self._lock:
                self._db.execute("INSERT OR REPLACE INTO http VALUES (?, ?, ?)",
                                 (url, time.time(), zlib.compress(body.encode("utf-8"))))
                self._db.commit()
        return body

    def json(self, params: dict) -> dict | None:
        body = self.get(params)
        if not body or not body.lstrip().startswith("{"):
            return None  # "" = no data, "Wrong radius" …
        try:
            return json.loads(body)
        except ValueError:
            return None

    def close(self) -> None:
        if self._db is not None:
            self._db.close()


# =========================================================================== parsing

def _latlon(text: str | None) -> tuple[float, float] | None:
    try:
        a, b = str(text).split("*")
        lat, lon = float(a), float(b)
    except (ValueError, AttributeError):
        return None
    if not (29.0 <= lat <= 33.6 and 34.0 <= lon <= 36.0):
        return None
    return round(lat, 6), round(lon, 6)


def _clean(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("‏", "").replace("‎", "")).strip()


_TAG_SUFFIX = re.compile(r"(?:\s*-\s*(?:נ\.?\s*פ|מ\.?\s*ש\.?\s*ג\.?\s*ב|מ\.?\s*ה|א\.?\s*ח|אח לאח)\.?)+\s*$")


def clean_name(name: str | None) -> str | None:
    n = _clean(name)
    n = _TAG_SUFFIX.sub("", n)  # " - נ.פ", " - אח לאח" … (campaign labels of the source, not part of the name)
    n = re.sub(r"\s*-\s*-\s*", " - ", n).strip(" -")
    return n or None


def norm_time(t: str | None) -> str | None:
    m = _TIME_RE.match(t or "")
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if h > 24 or mi > 59:
        return None
    return f"{h % 24:02d}:{mi:02d}"


def norm_days(d: str | None) -> str:
    return "".join(c for c in DAY_LETTERS if c in (d or ""))


def parse_minyanim(data: dict | None) -> tuple[list[dict], str | None, str | None]:
    """GetMosadMinyan answer -> (items, phone line, message)."""
    if not data:
        return [], None, None
    items = []
    for r in data.get("data") or []:
        t, days = norm_time(r.get("H")), norm_days(r.get("D"))
        prayer = _clean(r.get("T"))
        if not t or not days or not prayer:
            continue
        items.append({"prayer": prayer, "time": t, "days": days,
                      "hall": _clean(r.get("L")) or None, "note": _clean(r.get("C")) or None})
    items.sort(key=lambda m: (m["time"], m["prayer"]))
    short, direct = _clean(str(data.get("ShortId") or "")), _clean(str(data.get("DirectId") or ""))
    phone = f"{direct} שלוחה 5" if direct else (f"{PHONE_MAIN} שלוחה {short}" if short else None)
    return items, phone, _clean(data.get("Message")) or None


# =========================================================================== fetch

def fetch(data_dir: Path, workers: int = 4, fetcher: Fetcher | None = None, limit: int | None = None) -> dict:
    """Download everything and write the snapshot. Returns the snapshot."""
    data_dir = Path(data_dir)
    own = fetcher is None
    fetcher = fetcher or Fetcher(data_dir / "cache" / "zmanim-http.sqlite")
    workers = max(1, min(4, workers))
    out_path = snapshot_path(data_dir)
    previous = {}
    if out_path.exists():
        try:
            previous = {m["id"]: m for m in json.loads(out_path.read_text(encoding="utf-8")).get("mosads", [])}
        except (ValueError, OSError):
            previous = {}
    t0 = time.time()
    try:
        loc = fetcher.json({"Action": "GetLocation"})
        if not loc or not loc.get("Details"):
            raise RuntimeError("GetLocation returned nothing – is the site reachable?")
        mosads: dict[str, dict] = {}
        for d in loc["Details"]:
            mid, ll = str(d.get("MosadId") or "").strip(), _latlon(d.get("Loc"))
            if not mid.isdigit() or int(mid) <= 0 or ll is None:  # "0" is a test entry
                continue
            m = mosads.setdefault(mid, {"id": mid, "name": None, "address": None, "locations": [],
                                        "phone": None, "message": None, "minyanim": []})
            if ll not in m["locations"]:
                m["locations"].append(ll)
        ids = sorted(mosads, key=int)
        if limit:
            ids = ids[:limit]
            mosads = {k: mosads[k] for k in ids}
        log.info("zmanim: %d mosads at %d locations", len(ids), sum(len(m["locations"]) for m in mosads.values()))

        def timetable(mid: str):
            return mid, fetcher.json({"Action": "GetMosadMinyan", "MosadId": mid})

        with ThreadPoolExecutor(workers) as pool:
            for n, (mid, data) in enumerate(pool.map(timetable, ids), start=1):
                m = mosads[mid]
                m["minyanim"], m["phone"], m["message"] = parse_minyanim(data)
                if n % 250 == 0:
                    log.info("  timetables %d / %d", n, len(ids))

        # names / addresses: sweep "nearest minyan" around the mosads
        names: dict[str, dict] = {}
        lock = threading.Lock()

        def nearest(job):
            (lat, lon), radius = job
            data = fetcher.json({"Action": "GetNearestMinyan", "lat": lat, "lng": lon, "radius": radius})
            found = 0
            for r in (data or {}).get("data") or []:
                mid, name = str(r.get("M") or ""), clean_name(r.get("L"))
                if not mid or not name:
                    continue
                with lock:
                    e = names.setdefault(mid, {"name": name, "address": _clean(r.get("A")) or None, "locations": []})
                    ll = _latlon(r.get("W"))
                    if ll and ll not in e["locations"]:
                        e["locations"].append(ll)
                found += 1
            return found

        def sweep(radius: float, all_locations: bool) -> int:
            """Query around unnamed mosads; in chunks, so that names found early save requests later."""
            todo = [mid for mid in ids if mid not in names and mosads[mid]["minyanim"]]
            sent = 0
            with ThreadPoolExecutor(workers) as pool:
                for i in range(0, len(todo), workers * 8):
                    part = [mid for mid in todo[i:i + workers * 8] if mid not in names]
                    jobs = [(ll, radius) for mid in part
                            for ll in (mosads[mid]["locations"] if all_locations else mosads[mid]["locations"][:1])]
                    sent += len(jobs)
                    list(pool.map(nearest, jobs))
            return sent

        r1 = sweep(0.03, False)
        log.info("  names after 30 m sweep: %d (%d requests)", len(names), r1)
        r2 = sweep(0.001, True)
        log.info("  names after 1 m sweep: %d (%d requests)", len(names), r2)

        if fetcher.errors > max(20, len(ids) // 10):
            raise RuntimeError(f"{fetcher.errors} requests failed – keeping the previous snapshot")
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        from_prev = 0
        for mid in ids:
            m = mosads[mid]
            e = names.get(mid)
            if e:
                m["name"], m["address"] = e["name"], e["address"]
                m["name_seen"] = today
            elif previous.get(mid, {}).get("name"):
                p = previous[mid]
                m["name"], m["address"], m["name_seen"] = p["name"], p.get("address"), p.get("name_seen")
                from_prev += 1
        stats = {
            "mosads": len(ids),
            "locations": sum(len(m["locations"]) for m in mosads.values()),
            "with_name": sum(1 for m in mosads.values() if m["name"]),
            "names_from_previous": from_prev,
            "with_minyanim": sum(1 for m in mosads.values() if m["minyanim"]),
            "minyanim": sum(len(m["minyanim"]) for m in mosads.values()),
            "with_phone": sum(1 for m in mosads.values() if m["phone"]),
            "requests": fetcher.requests, "cache_hits": fetcher.hits, "errors": fetcher.errors,
        }
        snap = {
            "source": SOURCE_HE, "url": SOURCE_URL,
            "fetched": datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d"),
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "stats": stats,
            "mosads": [{**mosads[mid], "locations": [list(ll) for ll in mosads[mid]["locations"]]} for mid in ids],
        }
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = out_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(snap, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        tmp.replace(out_path)
        log.info("zmanim snapshot %s (%.1f MB) in %.0fs: %s", out_path, out_path.stat().st_size / 1e6,
                 time.time() - t0, stats)
        return snap
    finally:
        if own:
            fetcher.close()


# =========================================================================== apply

_SYN_GENERIC = {normalize(w) for w in (
    "בית", "כנסת", "הכנסת", "בית הכנסת", "בית כנסת", "ביהכנ", "ביכנ", "קהילת", "קהילה", "קהל", "ישיבת", "ישיבה",
    "בית מדרש", "מדרש", "המדרש", "מנין", "מניין", "המרכזי", "מרכזי", "הגדול", "שול", "ספרדי", "הספרדי", "אשכנזי",
    "האשכנזי", "תימני", "התימני", "שכונתי", "השכונתי", "עירוני", "העירוני", "ביהכנס", "ביהמד", "ער", "synagogue",
    "synagogues", "beit", "knesset", "מוסדות", "עמותת", "עמותה", "כולל", "עש", "אברכים", "רחוב")}
#: place names are not distinctive either ("… חיפה" / "… חיפה"); the database's own places are added in apply()
_PLACE_WORDS = {normalize(w) for w in (
    "ירושלים", "בני ברק", "ב\"ב", "חיפה", "בית שמש", "ביתר", "עילית", "מודיעין", "אלעד", "אשדוד", "פתח תקווה", "פ\"ת",
    "תל אביב", "רחובות", "צפת", "טבריה", "נתיבות", "אופקים", "רכסים", "גרין פארק", "קרית ספר", "נוה", "נווה", "רמות",
    "הר נוף", "גבעת", "שכונת", "רמת", "קרית")}


_PLACE_PHRASES = tuple(p for p in _PLACE_WORDS if " " in p)


def syn_core(name: str | None, places: frozenset = frozenset()) -> str:
    n = f" {normalize(name or '')} "
    for p in (*_PLACE_PHRASES, *places):  # multi-word place names
        if " " in p and f" {p} " in n:
            n = n.replace(f" {p} ", " ")
    toks = [t for t in n.split() if t not in _SYN_GENERIC and t not in _PLACE_WORDS and t not in places]
    return " ".join(toks)


def syn_name_score(a: str | None, b: str | None, places: frozenset = frozenset()) -> float:
    """0..1 similarity of two synagogue names, ignoring "בית הכנסת", "קהילת", place names …"""
    ca, cb = syn_core(a, places), syn_core(b, places)
    if not ca or not cb:
        return 0.0
    # name_match lets a token match inside a longer one; 1-2 letter words would match almost anything
    la = " ".join(t for t in ca.split() if len(t) >= 3) or ca
    lb = " ".join(t for t in cb.split() if len(t) >= 3) or cb
    tokens = max(name_match(la, lb), name_match(lb, la) * 0.9)
    ratio = difflib.SequenceMatcher(None, ca, cb).ratio()  # spelling variants: "זכור ליצחק" / "זכר ליצחק"
    return max(tokens, ratio if ratio >= 0.75 else 0.0)


def is_generic_name(name: str | None) -> bool:
    """No distinctive word left: "בית כנסת ספרדי", "בית הכנסת המרכזי" …"""
    return not syn_core(name)


_ADDR_RE = re.compile(r"^(?P<street>\D+?)\s+(?P<num>\d{1,4}[א-ת]?)(?:\s*[/\\-]\s*\S+)?(?:\s*,)?\s*(?P<rest>.*)$")


def parse_address(text: str | None, places: Iterable[str] = ()) -> dict:
    """"אביר יעקב 30/8 ירושלים" -> street, housenumber, city (city only when it is a known place)."""
    t = _clean((text or "").replace(",", " , "))
    t = re.sub(r"\s+,", ",", t).strip(" ,")
    out = {"street": None, "housenumber": None, "city": None}
    if not t:
        return out
    known = {normalize(p): p for p in places if p}
    m = _ADDR_RE.match(t)
    rest = t
    if m:
        street = m.group("street").strip(" ,")
        if len(street) >= 2 and not normalize(street) in known:
            out["street"], out["housenumber"] = street, m.group("num")
            rest = m.group("rest")
    rest = rest.strip(" ,")
    if rest:
        words = rest.replace(",", " ").split()
        for i in range(len(words)):  # longest known suffix: "נוה יעקב ירושלים" -> ירושלים
            cand = normalize(" ".join(words[i:]))
            if cand in known:
                out["city"] = known[cand]
                break
    return out


def _dist_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dy = (lat2 - lat1) * 111_320
    dx = (lon2 - lon1) * 111_320 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(dx, dy)


def _box(lat: float, lon: float, m: float) -> tuple[float, float, float, float]:
    dlat = m / 111_320
    dlon = m / (111_320 * math.cos(math.radians(lat)))
    return lon - dlon, lon + dlon, lat - dlat, lat + dlat


def _city_at(con: sqlite3.Connection, lat: float, lon: float) -> str | None:
    rows = con.execute(
        "SELECT p.name, p.kind, (p.max_lon - p.min_lon) * (p.max_lat - p.min_lat) AS area, p.lat, p.lon FROM places_rtree r "
        "JOIN places p ON p.id = r.id WHERE r.min_lon <= ? AND r.max_lon >= ? AND r.min_lat <= ? AND r.max_lat >= ? "
        "AND p.kind IN ('city','town','village','hamlet','suburb','neighbourhood','quarter','isolated_dwelling','locality')",
        (lon, lon, lat, lat)).fetchall()
    rows = [r for r in rows if r[1] in ("city", "town", "village", "hamlet")] or []
    if rows:
        return min(rows, key=lambda r: r[2] or 0)[0]
    best = None
    for name, kind, _a, plat, plon in con.execute(
            "SELECT p.name, p.kind, 0, p.lat, p.lon FROM places_rtree r JOIN places p ON p.id = r.id "
            "WHERE r.max_lon >= ? AND r.min_lon <= ? AND r.max_lat >= ? AND r.min_lat <= ? "
            "AND p.kind IN ('city','town','village')", _box(lat, lon, 3000)):
        d = _dist_m(lat, lon, plat, plon)
        if d <= 3000 and (best is None or d < best[0]):
            best = (d, name)
    return best[1] if best else None


MATCH_RADIUS = 60.0       # metres, any synagogue
NAME_RADIUS = 200.0       # metres, when the names agree


def _candidates(con: sqlite3.Connection, m: dict, places: frozenset = frozenset()) -> list[tuple[float, int]]:
    """(score, poi_id) for OSM synagogue / beit-midrash POIs near the mosad."""
    best: dict[int, float] = {}
    for lat, lon in m["locations"]:
        q = ",".join("?" * len(SUBS))
        for pid, name, plat, plon in con.execute(
                f"SELECT p.id, p.name, p.lat, p.lon FROM pois_rtree r JOIN pois p ON p.id = r.id "
                f"WHERE r.max_lon >= ? AND r.min_lon <= ? AND r.max_lat >= ? AND r.min_lat <= ? "
                f"AND p.sub IN ({q}) AND (p.osm IS NULL OR p.osm NOT LIKE '{OSM_PREFIX}%')",
                (*_box(lat, lon, NAME_RADIUS), *SUBS)):
            d = _dist_m(lat, lon, plat, plon)
            sim = syn_name_score(m.get("name"), name, places) if m.get("name") else 0.0
            if sim >= 0.6 and d <= NAME_RADIUS:
                score = 2.0 + sim - d / NAME_RADIUS
            elif d <= MATCH_RADIUS and (sim >= 0.5 or is_generic_name(name) or is_generic_name(m.get("name"))):
                # distance only when a name is missing / generic: two distinctive, different names in
                # neighbouring buildings are two congregations (common in Bnei Brak, Jerusalem …)
                score = 1.0 + sim - d / MATCH_RADIUS
            else:
                continue
            best[pid] = max(best.get(pid, -1), score)
    return sorted(((s, pid) for pid, s in best.items()), reverse=True)


def _search_add(con: sqlite3.Connection, poi_id: int, name: str, context: str | None, lat: float, lon: float,
                importance: float) -> None:
    from .search_index import _article_variants

    name_n = normalize(name)
    if not name_n:
        return
    alt = []
    alt += _article_variants(name_n)
    alt_n = " ".join(dict.fromkeys(a for a in alt if a and a != name_n))
    ctx_n = normalize(context) if context else ""
    nid = (con.execute("SELECT COALESCE(MAX(id), 0) FROM search_items").fetchone()[0] or 0) + 1
    con.execute("INSERT INTO search_items VALUES (?,?,?,?,?,?,?,?,?,?)",
                (nid, "poi", poi_id, name, context, "religion", "synagogue", lat, lon, importance))
    con.execute("INSERT INTO search_fts(rowid, name_n, alt_n, context_n) VALUES (?,?,?,?)", (nid, name_n, alt_n, ctx_n))
    for w in (name_n + " " + alt_n).split():
        if len(w) >= 2:
            if con.execute("UPDATE vocab SET freq = freq + 1 WHERE word = ?", (w,)).rowcount == 0:
                con.execute("INSERT INTO vocab VALUES (?, 1)", (w,))
                if len(w) >= 3:
                    con.execute("INSERT INTO vocab_tri(word) VALUES (?)", (w,))


def _search_drop(con: sqlite3.Connection, poi_id: int) -> None:
    for (sid,) in con.execute("SELECT id FROM search_items WHERE kind = 'poi' AND ref_id = ?", (poi_id,)).fetchall():
        con.execute("DELETE FROM search_fts WHERE rowid = ?", (sid,))
        con.execute("DELETE FROM search_items WHERE id = ?", (sid,))


def apply(con: sqlite3.Connection, snap: dict) -> dict:
    """Load *snap* into *con* (in place): timetables, links to POIs, new synagogue POIs."""
    con.executescript(db.RICH_DDL)
    updated = snap.get("fetched") or time.strftime("%Y-%m-%d")
    mosads = [m for m in snap.get("mosads", []) if m.get("locations")]
    places = [r[0] for r in con.execute(
        "SELECT DISTINCT name FROM places WHERE kind IN ('city','town','village','hamlet','suburb','neighbourhood','quarter')")]
    place_set = frozenset(normalize(p) for p in places if p)
    has_search = bool(con.execute("SELECT 1 FROM sqlite_master WHERE name = 'search_items'").fetchone())

    # 1. one-to-one matching, best pairs first
    pairs = []
    for m in mosads:
        if not m.get("name") and not m.get("minyanim"):
            continue  # nothing to show
        for score, pid in _candidates(con, m, place_set):
            pairs.append((score, m["id"], pid))
    pairs.sort(reverse=True)
    link: dict[str, int] = {}
    taken: set[int] = set()
    for score, mid, pid in pairs:
        if mid in link or pid in taken:
            continue
        link[mid] = pid
        taken.add(pid)

    # 2. earlier additions from this source
    own = {r[1][len(OSM_PREFIX):]: r[0] for r in con.execute(
        f"SELECT id, osm FROM pois WHERE osm LIKE '{OSM_PREFIX}%'")}
    keep_own: set[int] = set()
    next_id = (con.execute("SELECT COALESCE(MAX(id), 0) FROM pois").fetchone()[0] or 0) + 1
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    stats = {"mosads": len(mosads), "matched_osm": 0, "new_pois": 0, "updated_new_pois": 0, "removed_pois": 0,
             "names_filled": 0, "addresses_filled": 0, "minyanim": 0, "no_name_skipped": 0}

    con.execute("DELETE FROM synagogues_ext")
    con.execute("DELETE FROM minyanim")
    for m in mosads:
        lat, lon = m["locations"][0]
        name = clean_name(m.get("name"))
        addr = parse_address(m.get("address"), places)
        pid = link.get(m["id"])
        if pid is not None:
            stats["matched_osm"] += 1
            r = con.execute("SELECT name, street, housenumber, city FROM pois WHERE id = ?", (pid,)).fetchone()
            if not r[0] and name:
                con.execute("UPDATE pois SET name = ? WHERE id = ?", (name, pid))
                stats["names_filled"] += 1
                if has_search:
                    _search_drop(con, pid)
                    _search_add(con, pid, name, " ".join(x for x in (addr["street"] or r[1], addr["city"] or r[3]) if x) or None,
                                lat, lon, 0.5)
            if not r[1] and addr["street"]:
                con.execute("UPDATE pois SET street = ?, housenumber = COALESCE(housenumber, ?) WHERE id = ?",
                            (addr["street"], addr["housenumber"], pid))
                stats["addresses_filled"] += 1
            if not r[3] and addr["city"]:
                con.execute("UPDATE pois SET city = ? WHERE id = ?", (addr["city"], pid))
        else:
            if not name and not m.get("minyanim"):
                stats["no_name_skipped"] += 1
                continue
            city = addr["city"] or _city_at(con, lat, lon)
            extra = json.dumps({"source": "nedarimplus", "mosad_id": m["id"]}, ensure_ascii=False)
            importance = 0.47 if name else 0.42
            pid = own.get(m["id"])
            if pid is not None:
                con.execute("UPDATE pois SET name = ?, lat = ?, lon = ?, street = ?, housenumber = ?, city = ?, extra = ?, "
                            "updated = ? WHERE id = ?",
                            (name, lat, lon, addr["street"], addr["housenumber"], city, extra, now, pid))
                con.execute("UPDATE pois_rtree SET min_lon = ?, max_lon = ?, min_lat = ?, max_lat = ? WHERE id = ?",
                            (lon, lon, lat, lat, pid))
                stats["updated_new_pois"] += 1
            else:
                pid = next_id
                next_id += 1
                con.execute(f"INSERT INTO pois (id, osm, name, category, sub, kind_he, lat, lon, street, housenumber, city, "
                            f"extra, importance, updated) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (pid, OSM_PREFIX + m["id"], name, "religion", "synagogue", "בית כנסת", lat, lon,
                             addr["street"], addr["housenumber"], city, extra, importance, now))
                con.execute("INSERT INTO pois_rtree VALUES (?,?,?,?,?)", (pid, lon, lon, lat, lat))
                stats["new_pois"] += 1
            keep_own.add(pid)
            if has_search:
                _search_drop(con, pid)
                if name:
                    _search_add(con, pid, name, " ".join(x for x in (addr["street"], city) if x) or None, lat, lon, importance)
        con.execute("INSERT OR REPLACE INTO synagogues_ext VALUES (?,?,?,?,?,?,?,?)",
                    (m["id"], pid, name, _clean(m.get("address")) or None, lat, lon, m.get("phone"), updated))
        rows = [(m["id"], x["prayer"], x.get("hall"), x["time"], x["days"], x.get("note"))
                for x in m.get("minyanim") or [] if norm_time(x.get("time")) and x.get("days")]
        con.executemany("INSERT INTO minyanim VALUES (?,?,?,?,?,?)", rows)
        stats["minyanim"] += len(rows)
    # synagogues this source added earlier but no longer lists
    for mid, pid in own.items():
        if pid not in keep_own:
            con.execute("DELETE FROM pois WHERE id = ?", (pid,))
            con.execute("DELETE FROM pois_rtree WHERE id = ?", (pid,))
            if has_search:
                _search_drop(con, pid)
            stats["removed_pois"] += 1
    con.execute("INSERT OR REPLACE INTO meta VALUES ('zmanim', ?)",
                (json.dumps({**stats, "fetched": updated, "source": SOURCE_HE}, ensure_ascii=False),))
    con.commit()
    log.info("zmanim applied: %s", stats)
    return stats


def load_snapshot(data_dir: Path) -> dict:
    p = snapshot_path(data_dir)
    if not p.exists():
        raise SystemExit(f"no prayer-times snapshot at {p} – run with --fetch first")
    return json.loads(p.read_text(encoding="utf-8"))


# =========================================================================== CLI

def cmd(args) -> None:
    """``python -m hmaps.etl zmanim [--fetch] [--apply]`` (both when neither is given)."""
    import shutil

    from . import quality

    data_dir = Path(args.db).parent
    do_fetch, do_apply = args.fetch, args.apply
    if not do_fetch and not do_apply:
        do_fetch = do_apply = True
    if do_fetch:
        fetch(data_dir, workers=args.workers, limit=args.limit)
    if do_apply:
        snap = load_snapshot(data_dir)
        logical = Path(args.db)
        live = db.resolve(logical)
        if not live.exists():
            raise SystemExit(f"no database at {logical} – run the build first")
        tmp = logical.with_suffix(".building")
        shutil.copyfile(live, tmp)
        con = sqlite3.connect(tmp)
        try:
            apply(con, snap)
            report = quality.compute(con)
            con.execute("VACUUM")
        finally:
            con.close()
        final = db.publish(tmp, logical)
        quality.write(report, logical.parent / "reports")
        log.info("published %s – the running map picks it up within a few seconds", final.name)
