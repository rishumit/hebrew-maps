"""Optional enrichment from Wikidata, Hebrew Wikipedia and data.gov.il.

Every step is best-effort: network problems are logged and the build goes
on with the OpenStreetMap data alone.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
import urllib.parse
import urllib.request
from typing import Any, Iterable

from ..core.hebrew import normalize

log = logging.getLogger(__name__)

USER_AGENT = "HebrewMaps/1.0 (self-hosted open-data map of Israel; https://github.com/)"
WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"
HEWIKI_API = "https://he.wikipedia.org/w/api.php"
DATAGOV_API = "https://data.gov.il/api/3/action/datastore_search"
#: data.gov.il CKAN resources (can be overridden through environment variables)
SETTLEMENTS_RESOURCE = "5c78e9fa-c2e2-4771-93ff-7f400a12f7ba"
STREETS_RESOURCE = "9ad3862c-8391-4b2f-84a4-2d4c68625f4b"


def _http(url: str, data: dict | None = None, accept: str = "application/json", retries: int = 3, timeout: int = 60) -> Any:
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    last: Exception | None = None
    for attempt in range(retries):
        req = urllib.request.Request(url, data=body, headers={"User-Agent": USER_AGENT, "Accept": accept})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8-sig"))
        except Exception as e:  # noqa: BLE001 - network is best effort
            last = e
            time.sleep(2 ** attempt)
    raise RuntimeError(f"request failed: {url} ({last})")


def _chunks(seq: list, n: int) -> Iterable[list]:
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


# --------------------------------------------------------------------------- wiki

def enrich_wikidata(con: sqlite3.Connection, max_items: int = 20000) -> int:
    """Images (P18) and Hebrew Wikipedia titles for objects with a wikidata tag."""
    rows = con.execute("SELECT id, wikidata FROM pois WHERE wikidata IS NOT NULL ORDER BY importance DESC").fetchall()
    qids: dict[str, list[int]] = {}
    for pid, wd in rows:
        for q in re.findall(r"Q\d+", wd or ""):
            qids.setdefault(q, []).append(pid)
            break
    keys = list(qids)[:max_items]
    if not keys:
        return 0
    updated = 0
    for batch in _chunks(keys, 150):
        values = " ".join(f"wd:{q}" for q in batch)
        query = f"""
SELECT ?item ?image ?article ?desc WHERE {{
  VALUES ?item {{ {values} }}
  OPTIONAL {{ ?item wdt:P18 ?image. }}
  OPTIONAL {{ ?article schema:about ?item; schema:isPartOf <https://he.wikipedia.org/>. }}
  OPTIONAL {{ ?item schema:description ?desc. FILTER(LANG(?desc) = "he") }}
}}"""
        try:
            data = _http(WIKIDATA_SPARQL, {"query": query, "format": "json"}, accept="application/sparql-results+json")
        except RuntimeError as e:
            log.warning("wikidata: %s", e)
            return updated
        seen = set()
        for b in data.get("results", {}).get("bindings", []):
            q = b["item"]["value"].rsplit("/", 1)[-1]
            if q in seen:
                continue
            seen.add(q)
            image = b.get("image", {}).get("value")
            if image:
                image = image.replace("http://", "https://") + "?width=640"
            article = b.get("article", {}).get("value")
            title = urllib.parse.unquote(article.rsplit("/", 1)[-1]).replace("_", " ") if article else None
            desc = b.get("desc", {}).get("value")
            for pid in qids.get(q, []):
                con.execute(
                    "UPDATE pois SET image = COALESCE(image, ?), wikipedia = COALESCE(wikipedia, ?),"
                    " description = COALESCE(description, ?) WHERE id = ?",
                    (image, f"he:{title}" if title else None, desc, pid),
                )
                updated += 1
        con.commit()
        time.sleep(1.0)
    log.info("wikidata: updated %d POIs", updated)
    return updated


def enrich_wikipedia(con: sqlite3.Connection, max_items: int = 8000) -> int:
    """Short Hebrew descriptions (first sentences of the article)."""
    rows = con.execute(
        "SELECT id, wikipedia FROM pois WHERE wikipedia LIKE 'he:%' ORDER BY importance DESC LIMIT ?", (max_items,)
    ).fetchall()
    by_title: dict[str, list[int]] = {}
    for pid, wp in rows:
        by_title.setdefault(wp[3:].strip(), []).append(pid)
    titles = list(by_title)
    done = 0
    for batch in _chunks(titles, 20):
        params = {
            "action": "query", "prop": "extracts", "exintro": 1, "explaintext": 1, "exsentences": 2,
            "exlimit": 20, "redirects": 1, "format": "json", "formatversion": 2, "titles": "|".join(batch),
        }
        try:
            data = _http(HEWIKI_API + "?" + urllib.parse.urlencode(params))
        except RuntimeError as e:
            log.warning("wikipedia: %s", e)
            return done
        q = data.get("query", {})
        redirects = {r["to"]: r["from"] for r in q.get("redirects", [])}
        normalized = {n["to"]: n["from"] for n in q.get("normalized", [])}
        for page in q.get("pages", []):
            extract = (page.get("extract") or "").strip()
            if not extract:
                continue
            title = page["title"]
            orig = redirects.get(title, title)
            orig = normalized.get(orig, orig)
            text = re.sub(r"\s+", " ", extract)
            if len(text) > 320:
                text = text[:317].rsplit(" ", 1)[0] + "…"
            for pid in by_title.get(orig, by_title.get(title, [])):
                con.execute("UPDATE pois SET description = ? WHERE id = ?", (text, pid))
                done += 1
        con.commit()
        time.sleep(0.3)
    log.info("wikipedia: %d descriptions", done)
    return done


# --------------------------------------------------------------------------- data.gov.il

def _ckan_records(resource: str, page: int = 32000) -> list[dict]:
    out: list[dict] = []
    offset = 0
    while True:
        data = _http(f"{DATAGOV_API}?resource_id={resource}&limit={page}&offset={offset}")
        recs = data.get("result", {}).get("records", [])
        out.extend(recs)
        if len(recs) < page:
            return out
        offset += page


def _field(rec: dict, *candidates: str) -> Any:
    for c in candidates:
        for k, v in rec.items():
            if k.strip().replace(" ", "_") == c:
                return v
    return None


def _clean(v: Any) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()


def _settle_key(name: str) -> str:
    return normalize(name).replace(" ", "")


def enrich_gov(con: sqlite3.Connection, settlements_resource: str = SETTLEMENTS_RESOURCE,
               streets_resource: str = STREETS_RESOURCE) -> dict:
    """Official settlement list (codes, English names, districts) and street registry."""
    result = {"settlements": 0, "settlements_matched": 0, "streets": 0, "streets_matched": 0}
    try:
        recs = _ckan_records(settlements_resource)
    except RuntimeError as e:
        log.warning("data.gov.il settlements: %s", e)
        return result
    places = {}
    for pid, name, alt in con.execute("SELECT id, name, alt_names FROM places WHERE kind IN ('city','town','village','hamlet','isolated_dwelling')"):
        places.setdefault(_settle_key(name), pid)
        for a in json.loads(alt) if alt else []:
            places.setdefault(_settle_key(a), pid)
    for rec in recs:
        code = _field(rec, "סמל_ישוב")
        name = _clean(_field(rec, "שם_ישוב"))
        if not code or not name or name in {"לא רשום"}:
            continue
        try:
            code = int(code)
        except (TypeError, ValueError):
            continue
        name_en = _clean(_field(rec, "שם_ישוב_לועזי")).title() or None
        district = _clean(_field(rec, "שם_נפה", "נפה")) or None
        council = _clean(_field(rec, "שם_מועצה")) or None
        pid = places.get(_settle_key(name))
        con.execute("INSERT OR REPLACE INTO official_settlements VALUES (?,?,?,?,?,?)",
                    (code, name, name_en, district, council, 1 if pid else 0))
        result["settlements"] += 1
        if pid:
            result["settlements_matched"] += 1
            con.execute(
                "UPDATE places SET settlement_code = ?, name_en = COALESCE(name_en, ?), district = COALESCE(district, ?) WHERE id = ?",
                (code, name_en, district, pid),
            )
    con.commit()

    try:
        srecs = _ckan_records(streets_resource)
    except RuntimeError as e:
        log.warning("data.gov.il streets: %s", e)
        return result
    known = {(r[0], r[1]) for r in con.execute("SELECT DISTINCT city_norm, name_norm FROM streets WHERE city_norm IS NOT NULL")}
    rows = []
    for rec in srecs:
        city = _clean(_field(rec, "שם_ישוב"))
        street = _clean(_field(rec, "שם_רחוב"))
        if not city or not street:
            continue
        matched = (normalize(city), normalize(street)) in known
        rows.append((_field(rec, "סמל_ישוב"), city, _field(rec, "סמל_רחוב"), street, 1 if matched else 0))
        result["streets_matched"] += matched
    con.executemany("INSERT INTO official_streets VALUES (?,?,?,?,?)", rows)
    result["streets"] = len(rows)
    con.commit()
    log.info("data.gov.il: %s", result)
    return result
