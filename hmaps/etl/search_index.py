"""Building the full-text search index (FTS5) and the spelling vocabulary."""

from __future__ import annotations

import json
import logging
import sqlite3
from collections import Counter

from ..core.hebrew import normalize

log = logging.getLogger(__name__)


def _article_variants(text_n: str) -> list[str]:
    """For each word starting with the definite article ה add the bare word,
    so that "כותל" finds "הכותל המערבי" and "בימה" finds "הבימה"."""
    out = []
    for w in text_n.split(" "):
        if len(w) >= 4 and w[0] == "ה":
            out.append(w[1:])
    return out


def build(con: sqlite3.Connection) -> int:
    con.execute("DELETE FROM search_items")
    con.execute("DELETE FROM search_fts")
    con.execute("DELETE FROM vocab")
    con.execute("DELETE FROM vocab_tri")
    items: list[tuple] = []
    vocab: Counter[str] = Counter()

    def add(kind: str, ref: int, name: str, alts: list[str], context: str | None, cat: str | None,
            sub: str | None, lat: float, lon: float, importance: float) -> None:
        name_n = normalize(name)
        if not name_n:
            return
        alt_parts = [normalize(a) for a in alts if a]
        alt_parts += _article_variants(name_n)
        for a in list(alt_parts):
            alt_parts += _article_variants(a)
        alt_n = " ".join(dict.fromkeys(p for p in alt_parts if p and p != name_n))
        ctx_n = normalize(context) if context else ""
        boost = 0.3 if kind == "place" else (0.05 if kind == "street" else 0.0)
        items.append(((kind, ref, name, context, cat, sub, lat, lon, importance), (name_n, alt_n, ctx_n), importance + boost))
        for w in (name_n + " " + alt_n).split():
            if len(w) >= 2:
                vocab[w] += 1

    for r in con.execute("SELECT id, name, name_en, alt_names, kind, parent, district, lat, lon, importance FROM places"):
        alts = json.loads(r[3]) if r[3] else []
        if r[2]:
            alts.append(r[2])
        add("place", r[0], r[1], alts, r[5] or r[6], None, r[4], r[7], r[8], r[9])

    for r in con.execute("SELECT id, name, name_en, city, lat, lon, importance FROM streets"):
        add("street", r[0], r[1], [r[2]] if r[2] else [], r[3], None, None, r[4], r[5], r[6])

    for r in con.execute(
        "SELECT id, name, name_en, city, category, sub, lat, lon, importance, extra, street FROM pois WHERE name IS NOT NULL"
    ):
        alts = [r[2]] if r[2] else []
        if r[9]:
            extra = json.loads(r[9])
            alts += extra.get("alt_names", [])
            if extra.get("brand"):
                alts.append(extra["brand"])
        context = " ".join(x for x in (r[10], r[3]) if x) or None
        add("poi", r[0], r[1], alts, context, r[4], r[5], r[6], r[7], r[8])
        # city names also make sense as vocabulary for spelling correction
    # marked trails (named only; unnamed local trails are drawn on the map but not searchable)
    try:
        trail_rows = con.execute("SELECT id, name, name_en, route, network, lat, lon, importance, extra FROM trails "
                                 "WHERE name IS NOT NULL").fetchall()
    except sqlite3.OperationalError:
        trail_rows = []
    for r in trail_rows:
        alts = [r[2]] if r[2] else []
        if r[8]:
            extra = json.loads(r[8])
            alts += [extra[k] for k in ("name:ar",) if extra.get(k)]
        add("trail", r[0], r[1], alts, None, None, r[3], r[5], r[6], r[7])
    for r in con.execute("SELECT DISTINCT city FROM pois WHERE city IS NOT NULL"):
        for w in normalize(r[0]).split():
            vocab[w] += 1

    # name matches weigh far more than alternative names, which weigh more than context (city)
    con.execute("INSERT INTO search_fts(search_fts, rank) VALUES ('rank', 'bm25(10.0, 3.0, 1.0)')")
    # rowids follow importance, so "first N matches" (cheap) are also the most important ones
    items.sort(key=lambda it: -it[2])
    con.executemany("INSERT INTO search_items VALUES (?,?,?,?,?,?,?,?,?,?)",
                    ((i, *it[0]) for i, it in enumerate(items, start=1)))
    con.executemany("INSERT INTO search_fts(rowid, name_n, alt_n, context_n) VALUES (?,?,?,?)",
                    ((i, *it[1]) for i, it in enumerate(items, start=1)))
    con.executemany("INSERT INTO vocab VALUES (?, ?)", vocab.items())
    con.executemany("INSERT INTO vocab_tri(word) VALUES (?)", ((w,) for w in vocab if len(w) >= 3))
    con.commit()
    log.info("search index: %d items, %d vocabulary words", len(items), len(vocab))
    return len(items)
