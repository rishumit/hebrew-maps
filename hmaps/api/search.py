"""Hebrew search engine: autocomplete, free-text search, category and address queries.

Pipeline for a query:

1. :mod:`hmaps.core.query` interprets it (category intent, address, coords);
2. candidates come from the FTS5 index (prefix match on every token, with
   Hebrew prefix-letter variants) – or, for category intents, from the
   spatial index around the resolved place;
3. tokens unknown to the vocabulary are corrected with trigram candidates +
   Hebrew-aware edit distance; a query typed in the wrong keyboard layout is
   converted;
4. candidates are re-ranked with text similarity, importance and distance.
"""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from ..core import categories, geo, hours
from ..core.hebrew import (edit_distance, fix_keyboard_layout, max_typos, normalize, query_variants,
                           restore_finals, trigrams)
from ..core.query import ParsedQuery, parse
from .store import Dataset

KIND_LABEL = {
    "city": "עיר", "town": "יישוב עירוני", "village": "יישוב", "hamlet": "יישוב", "isolated_dwelling": "נקודת יישוב",
    "suburb": "שכונה", "quarter": "שכונה", "neighbourhood": "שכונה", "locality": "מקום", "borough": "רובע",
}
SETTLEMENT_KINDS = ("city", "town", "village", "hamlet", "isolated_dwelling")


@dataclass
class Candidate:
    item_id: int
    kind: str
    ref_id: int
    name: str
    context: str | None
    category: str | None
    sub: str | None
    lat: float
    lon: float
    importance: float
    bm25: float = 0.0
    score: float = 0.0
    distance: float | None = None
    extra: dict = field(default_factory=dict)


def _fts_term(tok: str, prefix: bool) -> str:
    variants = query_variants(tok)
    parts = [f'"{v}"' + ("*" if prefix else "") for v in variants]
    return "(" + " OR ".join(parts) + ")" if len(parts) > 1 else parts[0]


def _match_expr(token_groups: list[list[str]], prefix_last_only: bool = False) -> str:
    """AND over tokens, OR over each token's alternatives (corrections, variants)."""
    clauses = []
    for i, group in enumerate(token_groups):
        is_last = i == len(token_groups) - 1
        prefix = True if not prefix_last_only else is_last
        alts = [_fts_term(t, prefix or len(t) >= 3) for t in group]
        clauses.append(alts[0] if len(alts) == 1 else "(" + " OR ".join(alts) + ")")
    return " AND ".join(clauses)


class SearchEngine:
    def __init__(self, ds: Dataset):
        self.ds = ds

    # ------------------------------------------------------------------ helpers
    @property
    def con(self) -> sqlite3.Connection:
        return self.ds.conn()

    def _vocab_has_prefix(self, tok: str) -> bool:
        row = self.con.execute("SELECT 1 FROM vocab WHERE word >= ? AND word < ? LIMIT 1", (tok, tok + "￿")).fetchone()
        return row is not None

    def corrections(self, tok: str, limit: int = 3) -> list[str]:
        """Vocabulary words within the allowed edit distance of *tok*."""
        allowed = max_typos(tok)
        if allowed == 0:
            return []
        grams = [g.strip() for g in trigrams(tok)]
        grams = [g for g in grams if len(g) == 3]
        if not grams:
            return []
        expr = " OR ".join(f'"{g}"' for g in grams)
        try:
            rows = self.con.execute(
                "SELECT word FROM vocab_tri WHERE vocab_tri MATCH ? ORDER BY rank LIMIT 200", (expr,)
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        # short words share few trigrams: also consider words with the same first letters
        rows += self.con.execute(
            "SELECT word FROM vocab WHERE word >= ? AND word < ? LIMIT 400", (tok[:2], tok[:2] + "\uffff")
        ).fetchall()
        scored = []
        for (w,) in dict.fromkeys(rows):
            if abs(len(w) - len(tok)) > allowed:
                continue
            d = edit_distance(tok, w, allowed)
            # compare against the same-length prefix too: user is still typing
            if d > allowed and len(w) > len(tok):
                d = edit_distance(tok, w[:len(tok)], allowed) + 0.25
            if d <= allowed:
                freq = self.con.execute("SELECT freq FROM vocab WHERE word = ?", (w,)).fetchone()
                scored.append((d, -(freq[0] if freq else 0), w))
        scored.sort()
        return [w for _, _, w in scored[:limit]]

    def _fts(self, groups: list[list[str]], limit: int, kinds: tuple[str, ...] | None = None,
             prefix_last_only: bool = False) -> list[Candidate]:
        """Candidates in rowid order. Rowids were assigned by importance at build time,
        so taking the first N matches is both fast (no full bm25 sort) and sensible;
        final ordering happens in :meth:`_rank`."""
        expr = _match_expr(groups, prefix_last_only)
        pool = max(limit * 6, 600)
        sql = ("SELECT s.id, s.kind, s.ref_id, s.name, s.context, s.category, s.sub, s.lat, s.lon, s.importance "
               "FROM search_items s WHERE s.id IN (SELECT rowid FROM search_fts WHERE search_fts MATCH ? LIMIT ?)")
        args: list[Any] = [expr, pool]
        if kinds:
            sql += f" AND s.kind IN ({','.join('?' * len(kinds))})"
            args += list(kinds)
        try:
            rows = self.con.execute(sql, args).fetchall()
        except sqlite3.OperationalError:
            return []
        return [Candidate(*r[:10]) for r in rows]

    # ------------------------------------------------------------------ ranking
    def _rank(self, cands: list[Candidate], q_tokens: list[str], lat: float | None, lon: float | None,
              category_hint: str | None = None) -> list[Candidate]:
        q_joined = " ".join(q_tokens)
        for c in cands:
            name_n = normalize(c.name)
            words = name_n.split(" ")
            s = 0.0
            if name_n == q_joined:
                s += 3.0
            elif name_n.startswith(q_joined):
                s += 2.0
            elif q_joined in name_n:
                s += 0.8
            for t in q_tokens:
                vs = query_variants(t)
                if any(v in words for v in vs):
                    s += 0.6
                elif any(w.startswith(v) for w in words for v in vs):
                    s += 0.35
                elif c.context and any(v in normalize(c.context) for v in vs):
                    s += 0.15
            s += 2.2 * c.importance
            s += -0.05 * c.bm25  # bm25 rank is negative: better = more negative
            if c.kind == "place":
                s += 0.5 if c.sub in SETTLEMENT_KINDS else 0.2
            elif c.kind == "street":
                s += 0.1
            if category_hint and (c.sub == category_hint or c.category == category_hint):
                s += 0.6
            if lat is not None and lon is not None:
                c.distance = geo.haversine(lat, lon, c.lat, c.lon)
                scale = 60_000 if c.kind == "place" else 15_000
                s += (0.6 if c.kind == "place" else 1.6) * math.exp(-c.distance / scale)
            s -= 0.04 * max(0, len(words) - len(q_tokens))  # prefer concise names
            c.score = round(s, 4)
        cands.sort(key=lambda c: -c.score)
        # collapse identical name+context duplicates (e.g. street split in a city)
        seen: set[tuple] = set()
        out = []
        for c in cands:
            key = (c.kind, normalize(c.name), normalize(c.context or ""))
            if key in seen:
                continue
            seen.add(key)
            out.append(c)
        return out

    def _text_candidates(self, tokens: list[str], limit: int, kinds=None, autocomplete: bool = False) -> tuple[list[Candidate], dict]:
        """FTS candidates with typo correction. Returns (candidates, info)."""
        info: dict = {}
        groups = [[t] for t in tokens]
        cands = self._fts(groups, limit, kinds)
        if len(cands) < 3:
            corrected = False
            for i, t in enumerate(tokens):
                last = i == len(tokens) - 1
                if (autocomplete and last and self._vocab_has_prefix(t)) or (not autocomplete and self._vocab_exact(t)):
                    continue
                alts = self.corrections(t)
                if alts:
                    groups[i] = [t, *alts]
                    corrected = True
            if corrected:
                more = self._fts(groups, limit, kinds)
                known = {c.item_id for c in cands}
                cands += [c for c in more if c.item_id not in known]
                info["corrected"] = " ".join(g[1] if len(g) > 1 else g[0] for g in groups)
        return cands, info

    def _vocab_exact(self, tok: str) -> bool:
        return any(self.con.execute("SELECT 1 FROM vocab WHERE word = ?", (v,)).fetchone() for v in query_variants(tok))

    # ------------------------------------------------------------------ places
    def resolve_place(self, text: str, lat: float | None = None, lon: float | None = None) -> dict | None:
        """Best settlement / neighbourhood for a location phrase."""
        toks = normalize(text).split()
        if not toks:
            return None
        cands, _ = self._text_candidates(toks, 40, kinds=("place",))
        if not cands:
            return None
        ranked = self._rank(cands, toks, lat, lon)
        # the phrase must match the place's own name (or alternative names), not only its context
        expr = "{name_n alt_n} : (" + _match_expr([[t] for t in toks]) + ")"
        best = None
        for c in ranked[:10]:
            try:
                ok = self.con.execute("SELECT 1 FROM search_fts WHERE rowid = ? AND search_fts MATCH ?", (c.item_id, expr)).fetchone()
            except sqlite3.OperationalError:
                ok = None
            if ok:
                best = c
                break
        if best is None:
            return None
        row = self.con.execute(
            "SELECT id, name, kind, lat, lon, min_lon, min_lat, max_lon, max_lat, parent FROM places WHERE id = ?", (best.ref_id,)
        ).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------ result shaping
    def _poi_rows(self, ids: list[int]) -> dict[int, sqlite3.Row]:
        if not ids:
            return {}
        rows = self.con.execute(
            f"SELECT id, name, name_en, category, sub, kind_he, lat, lon, street, housenumber, city, hours_json, kosher, image "
            f"FROM pois WHERE id IN ({','.join('?' * len(ids))})", ids
        ).fetchall()
        return {r["id"]: r for r in rows}

    def shape(self, cands: list[Candidate]) -> list[dict]:
        poi_rows = self._poi_rows([c.ref_id for c in cands if c.kind == "poi"])
        out = []
        for c in cands:
            if c.kind == "poi":
                r = poi_rows.get(c.ref_id)
                if r is None:
                    continue
                out.append(poi_summary(r, c.distance, c.score))
            elif c.kind == "place":
                label = KIND_LABEL.get(c.sub or "", "מקום")
                if c.context:
                    label += f" · {c.context}"
                out.append({
                    "id": f"place-{c.ref_id}", "type": "place", "name": c.name, "secondary": label,
                    "place_kind": c.sub, "icon": "pin", "color": "#4B5563",
                    "location": {"lat": c.lat, "lon": c.lon}, "distance": _r(c.distance), "score": c.score,
                })
            elif c.kind == "street":
                out.append({
                    "id": f"street-{c.ref_id}", "type": "street", "name": c.name,
                    "secondary": f"רחוב · {c.context}" if c.context else "רחוב", "icon": "road", "color": "#4B5563",
                    "location": {"lat": c.lat, "lon": c.lon}, "distance": _r(c.distance), "score": c.score,
                })
            elif c.kind == "trail":
                from .outdoor import trail_summary  # local import: outdoor imports this module

                s = trail_summary(self.con, c.ref_id)
                if s:
                    out.append({**s, "location": {"lat": c.lat, "lon": c.lon}, "distance": _r(c.distance), "score": c.score})
        return out

    # ------------------------------------------------------------------ public API
    def autocomplete(self, q: str, lat: float | None = None, lon: float | None = None, limit: int = 8) -> dict:
        q = (q or "").strip()
        if len(normalize(q)) < 1:
            return {"suggestions": []}
        parsed = parse(q)
        suggestions: list[dict] = []
        if parsed.coords:
            return {"suggestions": [self._coord_result(*parsed.coords)]}
        # category suggestions ("מסע" -> "מסעדות")
        suggestions += self._category_suggestions(q, parsed)
        if parsed.is_address:
            addr = self.geocode_parsed(parsed, lat, lon, limit=3)
            suggestions += addr
        toks = [t for t in parsed.tokens if t]
        if parsed.is_category and parsed.location_text:
            toks = []  # the category suggestion already covers it
        if toks:
            cands, info = self._text_candidates(toks, 120, autocomplete=True)
            if not cands:
                conv = fix_keyboard_layout(q)
                if conv:
                    cands, info = self._text_candidates(normalize(conv).split(), 120, autocomplete=True)
                    info["converted"] = conv
            ranked = self._rank(cands, toks, lat, lon, parsed.category_hint)
            suggestions += self.shape(ranked[:limit])
        seen, uniq = set(), []
        for s in suggestions:
            if s["id"] in seen:
                continue
            seen.add(s["id"])
            uniq.append(s)
        return {"suggestions": uniq[:limit]}

    def _category_suggestions(self, q: str, parsed: ParsedQuery) -> list[dict]:
        out = []
        if parsed.is_category:
            sub = categories.SUB_BY_ID.get(parsed.sub) if parsed.sub else None
            cat = categories.CATEGORY_BY_ID[parsed.category]
            name = sub.plural_he if sub else cat.name_he
            loc_name = None
            if parsed.location_text:
                place = self.resolve_place(parsed.location_text)
                loc_name = place["name"] if place else restore_finals(parsed.location_text)
            where = f"ב{loc_name}" if loc_name else "בסביבה"
            text = f"{name} {where}"
            if parsed.kosher:
                text = f"{name} כשרים {where}" if not name.endswith("ות") else f"{name} כשרות {where}"
            if parsed.open_now:
                text += " – פתוח עכשיו"
            out.append({"id": f"cat-{parsed.sub or parsed.category}-{parsed.location_text or ''}", "type": "category",
                        "name": text, "secondary": "חיפוש לפי קטגוריה", "query": q,
                        "category": parsed.category, "sub": parsed.sub, "icon": (sub or cat).icon, "color": cat.color})
            return out
        nq = normalize(q)
        if len(nq) >= 2:
            seen = set()
            for phrase, (cat_id, sub_id) in categories.PHRASES.items():
                if phrase.startswith(nq) and (cat_id, sub_id) not in seen and sub_id:
                    seen.add((cat_id, sub_id))
                    sub = categories.SUB_BY_ID[sub_id]
                    cat = categories.CATEGORY_BY_ID[cat_id]
                    out.append({"id": f"cat-{sub_id}-", "type": "category", "name": f"{sub.plural_he} בסביבה",
                                "secondary": "חיפוש לפי קטגוריה", "query": sub.plural_he, "category": cat_id, "sub": sub_id,
                                "icon": sub.icon, "color": cat.color})
                if len(out) >= 2:
                    break
        return out

    def search(self, q: str, lat: float | None = None, lon: float | None = None, limit: int = 20, offset: int = 0,
               category: str | None = None, kosher: bool = False, open_now: bool = False,
               bbox: tuple[float, float, float, float] | None = None, user_lat: float | None = None,
               user_lon: float | None = None) -> dict:
        """Full search. ``lat/lon`` = map center (bias), ``user_*`` = device location for "near me"."""
        parsed = parse(q or "")
        interp: dict = {"query": q}
        if category:
            parsed.category = categories.PARENT_OF.get(category, category if category in categories.CATEGORY_BY_ID else None)
            parsed.sub = category if category in categories.SUB_BY_ID else None
        parsed.kosher = parsed.kosher or kosher
        parsed.open_now = parsed.open_now or open_now

        if parsed.coords:
            return {"results": [self._coord_result(*parsed.coords)], "meta": {"total": 1}, "interpretation": {"type": "coordinates"}}

        results: list[dict] = []
        if parsed.is_category:
            res, cinfo = self.category_search(parsed, lat, lon, user_lat, user_lon, limit + offset, bbox)
            interp.update(cinfo)
            results = res
        if parsed.is_address:
            addr = self.geocode_parsed(parsed, lat, lon, limit=5)
            if addr:
                interp.setdefault("type", "address")
                results = addr + results
        if not parsed.is_category:
            toks = parsed.name_tokens or parsed.tokens
            bias_lat, bias_lon = lat, lon
            place = None
            if parsed.location_text:
                place = self.resolve_place(parsed.location_text, lat, lon)
                if place:
                    bias_lat, bias_lon = place["lat"], place["lon"]
                    interp["near"] = place["name"]
            cands, info = self._text_candidates(toks, 300)
            if not cands and not results:
                conv = fix_keyboard_layout(q)
                if conv:
                    toks = normalize(conv).split()
                    cands, info = self._text_candidates(toks, 300)
                    info["converted"] = conv
            interp.update(info)
            ranked = self._rank(cands, toks, bias_lat, bias_lon, parsed.category_hint)
            if parsed.kosher:
                ranked = [c for c in ranked if c.kind != "poi" or self._is_kosher(c.ref_id)]
            text_results = self.shape(ranked)
            if parsed.open_now:
                text_results = [r for r in text_results if r.get("open_now") is not False]
            results += text_results
            interp.setdefault("type", "text")
        # de-dup
        seen, uniq = set(), []
        for r in results:
            if r["id"] not in seen:
                seen.add(r["id"])
                uniq.append(r)
        if lat is not None and lon is not None:
            for r in uniq:
                if r.get("distance") is None:
                    r["distance"] = _r(geo.haversine(lat, lon, r["location"]["lat"], r["location"]["lon"]))
        page = uniq[offset:offset + limit]
        return {"results": page, "meta": {"total": len(uniq), "returned": len(page), "offset": offset}, "interpretation": interp}

    def _is_kosher(self, poi_id: int) -> bool:
        r = self.con.execute("SELECT kosher FROM pois WHERE id = ?", (poi_id,)).fetchone()
        return bool(r and r[0])

    def category_search(self, parsed: ParsedQuery, lat, lon, user_lat, user_lon, limit: int,
                        bbox=None) -> tuple[list[dict], dict]:
        info: dict = {"type": "category", "category": parsed.category, "sub": parsed.sub}
        center = None
        area_bbox = None
        if parsed.location_text:
            place = self.resolve_place(parsed.location_text, lat, lon)
            if place:
                center = (place["lat"], place["lon"])
                area_bbox = (place["min_lon"], place["min_lat"], place["max_lon"], place["max_lat"])
                info["near"] = place["name"]
            else:
                info["location_not_found"] = parsed.location_text
        if center is None and parsed.near_me and user_lat is not None:
            center = (user_lat, user_lon)
            info["near"] = "המיקום שלך"
        if center is None and lat is not None:
            center = (lat, lon)
            info.setdefault("near", "אזור המפה")
        if center is None:
            center = (31.78, 35.0)  # middle of the country
        pois = nearby(self.con, center[0], center[1], category=parsed.sub or parsed.category, limit=limit,
                      kosher=parsed.kosher, open_now=parsed.open_now, within=area_bbox or bbox)
        info["center"] = {"lat": center[0], "lon": center[1]}
        return pois, info

    # ------------------------------------------------------------------ geocoding
    def _coord_result(self, lat: float, lon: float) -> dict:
        rev = reverse(self.con, lat, lon)
        return {"id": f"coord-{lat:.6f},{lon:.6f}", "type": "coordinate", "name": f"{lat:.6f}, {lon:.6f}",
                "secondary": rev.get("address") or "נקודה במפה", "icon": "pin", "color": "#4B5563",
                "location": {"lat": lat, "lon": lon}}

    def geocode_parsed(self, parsed: ParsedQuery, lat=None, lon=None, limit: int = 5) -> list[dict]:
        street_n = normalize(parsed.street_text or "")
        if not street_n:
            return []
        city = None
        if parsed.city_text:
            city = self.resolve_place(parsed.city_text, lat, lon)
        city_n = normalize(city["name"]) if city else None
        streets = self._find_streets(street_n, city_n, lat, lon)
        out = []
        for st in streets[:limit]:
            res = locate_house(self.con, st, parsed.house_number)
            out.append(res)
        return out

    def _find_streets(self, street_n: str, city_n: str | None, lat, lon) -> list[dict]:
        rows: list[sqlite3.Row] = []
        variants = [street_n] + [v for v in query_variants(street_n) if v != street_n]
        for v in variants:
            sql = "SELECT * FROM streets WHERE name_norm = ?"
            args: list[Any] = [v]
            if city_n:
                sql += " AND city_norm = ?"
                args.append(city_n)
            rows = self.con.execute(sql, args).fetchall()
            if rows:
                break
        if not rows:
            # fuzzy: FTS over streets (and typo correction)
            toks = street_n.split()
            cands, _ = self._text_candidates(toks, 60, kinds=("street",))
            ids = [c.ref_id for c in cands]
            if ids:
                rows = self.con.execute(f"SELECT * FROM streets WHERE id IN ({','.join('?' * len(ids))})", ids).fetchall()
                if city_n:
                    in_city = [r for r in rows if r["city_norm"] == city_n]
                    rows = in_city or ([] if city_n else rows)
        result = [dict(r) for r in rows]

        def key(r):
            exact = 0 if r["name_norm"] == street_n else 1
            d = geo.haversine(lat, lon, r["lat"], r["lon"]) if lat is not None else 0
            return (exact, d, -r["importance"])

        result.sort(key=key)
        return result


# ---------------------------------------------------------------------- shared helpers

def _r(v: float | None) -> int | None:
    return None if v is None else int(round(v))


def poi_label(r: sqlite3.Row | dict) -> str:
    sub = categories.SUB_BY_ID.get(r["sub"])
    kind = r["kind_he"] or (sub.name_he if sub else "")
    addr = format_address(r["street"], r["housenumber"], r["city"])
    return " · ".join(x for x in (kind, addr) if x)


def format_address(street: str | None, number: str | None, city: str | None) -> str:
    s = " ".join(x for x in (street, number) if x)
    return ", ".join(x for x in (s, city) if x)


def poi_summary(r: sqlite3.Row, distance: float | None = None, score: float | None = None) -> dict:
    sub = categories.SUB_BY_ID.get(r["sub"])
    cat = categories.CATEGORY_BY_ID.get(r["category"])
    week = None
    if r["hours_json"]:
        week = {i: [tuple(s) for s in spans] for i, spans in enumerate(json.loads(r["hours_json"]).values())}
    return {
        "id": f"poi-{r['id']}", "type": "poi",
        "name": r["name"] or (sub.name_he if sub else ""), "name_he": r["name"], "name_en": r["name_en"],
        "secondary": poi_label(r), "category": r["category"], "sub_category": r["sub"],
        "icon": sub.icon if sub else "pin", "color": cat.color if cat else "#4B5563",
        "location": {"lat": r["lat"], "lon": r["lon"]},
        "address": format_address(r["street"], r["housenumber"], r["city"]) or None,
        "open_now": hours.is_open(week) if week else None,
        "kosher": bool(r["kosher"]) if r["kosher"] is not None else None,
        "thumbnail": r["image"],
        "distance": _r(distance), "score": score,
    }


def nearby(con: sqlite3.Connection, lat: float, lon: float, category: str | None = None, radius: float | None = None,
           limit: int = 50, offset: int = 0, kosher: bool = False, open_now: bool = False,
           within: tuple[float, float, float, float] | None = None, tags: list[str] | None = None) -> list[dict]:
    """POIs near a point, expanding the radius until enough are found."""
    cat_col = "sub" if category in categories.SUB_BY_ID else "category"
    r = radius or 1500.0
    max_r = radius or 60_000.0
    rows: list[sqlite3.Row] = []
    while True:
        if within:
            minx, miny, maxx, maxy = within
            pad = 0.002
            minx, miny, maxx, maxy = minx - pad, miny - pad, maxx + pad, maxy + pad
        else:
            minx, miny, maxx, maxy = geo.bbox_around(lat, lon, r)
        sql = ("SELECT p.id, p.name, p.name_en, p.category, p.sub, p.kind_he, p.lat, p.lon, p.street, p.housenumber, p.city, "
               "p.hours_json, p.kosher, p.image, p.importance, p.extra FROM pois_rtree t JOIN pois p ON p.id = t.id "
               "WHERE t.min_lon <= ? AND t.max_lon >= ? AND t.min_lat <= ? AND t.max_lat >= ?")
        args: list[Any] = [maxx, minx, maxy, miny]
        if category and cat_col == "sub":
            subs = categories.expand_sub(category)  # "שמורות טבע" also lists the INPA sites
            sql += f" AND p.sub IN ({','.join('?' * len(subs))})"
            args += list(subs)
        elif category:
            sql += f" AND p.{cat_col} = ?"
            args.append(category)
        if kosher:
            sql += " AND p.kosher = 1"
        rows = con.execute(sql, args).fetchall()
        if within or radius or len(rows) >= limit + offset or r >= max_r:
            break
        r *= 2.5
    items = []
    for row in rows:
        d = geo.haversine(lat, lon, row["lat"], row["lon"])
        if radius and d > radius:
            continue
        if tags:
            blob = (row["extra"] or "") + " " + (row["kind_he"] or "")
            if not all(t in blob for t in tags):
                continue
        s = poi_summary(row, d)
        if open_now and s["open_now"] is not True:
            continue
        # distance first, nudged by importance (a museum 300 m away beats a kiosk 250 m away)
        s["_rank"] = d * (1.4 - row["importance"])
        items.append(s)
    items.sort(key=lambda s: s["_rank"])
    for s in items:
        s.pop("_rank", None)
    return items[offset:offset + limit]


def reverse(con: sqlite3.Connection, lat: float, lon: float) -> dict:
    """Coordinates -> nearest address / street, city and neighbourhood."""
    out: dict[str, Any] = {"lat": lat, "lon": lon}
    # nearest exact address within 60 m
    minx, miny, maxx, maxy = geo.bbox_around(lat, lon, 60)
    best_addr = None
    for r in con.execute(
        "SELECT a.* FROM addresses_rtree t JOIN addresses a ON a.id = t.id "
        "WHERE t.min_lon <= ? AND t.max_lon >= ? AND t.min_lat <= ? AND t.max_lat >= ?", (maxx, minx, maxy, miny)
    ):
        d = geo.haversine(lat, lon, r["lat"], r["lon"])
        if best_addr is None or d < best_addr[0]:
            best_addr = (d, r)
    # nearest street segment within 150 m
    minx, miny, maxx, maxy = geo.bbox_around(lat, lon, 150)
    best_street = None
    for r in con.execute(
        "SELECT g.street_id, g.geom FROM street_segments_rtree t JOIN street_segments g ON g.id = t.id "
        "WHERE t.min_lon <= ? AND t.max_lon >= ? AND t.min_lat <= ? AND t.max_lat >= ?", (maxx, minx, maxy, miny)
    ):
        d, _ = geo.distance_to_line(lon, lat, geo.decode_polyline(r["geom"]))
        if d <= 150 and (best_street is None or d < best_street[0]):
            best_street = (d, r["street_id"])
    city = None
    if best_addr and best_addr[0] < 40:
        a = best_addr[1]
        out.update(street=a["street"], house_number=a["housenumber"], city=a["city"], postcode=a["postcode"],
                   accuracy="address", distance=_r(best_addr[0]))
        city = a["city"]
    elif best_street:
        s = con.execute("SELECT name, city FROM streets WHERE id = ?", (best_street[1],)).fetchone()
        out.update(street=s["name"], city=s["city"], accuracy="street", distance=_r(best_street[0]))
        city = s["city"]
    if not city:
        city = city_at(con, lat, lon)
        out["city"] = city
        out.setdefault("accuracy", "city" if city else "none")
    neigh = con.execute(
        "SELECT p.name, p.lat, p.lon FROM places_rtree t JOIN places p ON p.id = t.id "
        "WHERE t.min_lon <= ? AND t.max_lon >= ? AND t.min_lat <= ? AND t.max_lat >= ? "
        "AND p.kind IN ('suburb','quarter','neighbourhood')", (lon, lon, lat, lat)
    ).fetchall()
    if neigh:
        n = min(neigh, key=lambda r: geo.haversine(lat, lon, r["lat"], r["lon"]))
        if geo.haversine(lat, lon, n["lat"], n["lon"]) < 1500:
            out["neighbourhood"] = n["name"]
    out["address"] = format_address(out.get("street"), out.get("house_number"), out.get("city")) or None
    return out


def city_at(con: sqlite3.Connection, lat: float, lon: float) -> str | None:
    rows = con.execute(
        "SELECT a.name, a.level, a.geom FROM areas_rtree t JOIN areas a ON a.id = t.id "
        "WHERE t.min_lon <= ? AND t.max_lon >= ? AND t.min_lat <= ? AND t.max_lat >= ? AND a.level BETWEEN 7 AND 8 "
        "ORDER BY a.level DESC", (lon, lon, lat, lat)
    ).fetchall()
    for r in rows:
        polys = json.loads(r["geom"])
        if geo.point_in_polygons(lon, lat, [[[tuple(p) for p in ring] for ring in poly] for poly in polys]):
            return r["name"]
    best = None
    minx, miny, maxx, maxy = geo.bbox_around(lat, lon, 5000)
    for r in con.execute(
        "SELECT p.name, p.kind, p.lat, p.lon FROM places_rtree t JOIN places p ON p.id = t.id "
        "WHERE t.min_lon <= ? AND t.max_lon >= ? AND t.min_lat <= ? AND t.max_lat >= ? "
        "AND p.kind IN ('city','town','village','hamlet')", (maxx, minx, maxy, miny)
    ):
        d = geo.haversine(lat, lon, r["lat"], r["lon"])
        if best is None or d < best[0]:
            best = (d, r["name"])
    return best[1] if best else None


def locate_house(con: sqlite3.Connection, street: dict, number: str | None) -> dict:
    """Position of a house number on a street: exact, nearest known number, or street centre."""
    base = {
        "type": "address", "icon": "home", "color": "#1E40AF",
        "street": street["name"], "city": street["city"], "street_id": street["id"],
    }
    label_city = street["city"]
    if number:
        exact = con.execute(
            "SELECT lat, lon, housenumber FROM addresses WHERE street_norm = ? AND (city_norm = ? OR (? IS NULL AND city_norm IS NULL)) "
            "AND housenumber = ? LIMIT 1", (street["name_norm"], street["city_norm"], street["city_norm"], number)
        ).fetchone()
        if exact:
            return {**base, "id": f"addr-{street['id']}-{number}", "name": f"{street['name']} {number}",
                    "secondary": label_city or "", "house_number": number, "accuracy": "exact", "confidence": 0.98,
                    "location": {"lat": exact["lat"], "lon": exact["lon"]}}
        digits = "".join(ch for ch in number if ch.isdigit())
        if digits:
            n = int(digits)
            near = con.execute(
                "SELECT lat, lon, number_int FROM addresses WHERE street_norm = ? AND (city_norm = ? OR (? IS NULL AND city_norm IS NULL)) "
                "AND number_int IS NOT NULL ORDER BY (number_int % 2 != ? % 2), ABS(number_int - ?) LIMIT 1",
                (street["name_norm"], street["city_norm"], street["city_norm"], n, n),
            ).fetchone()
            if near:
                return {**base, "id": f"addr-{street['id']}-{number}", "name": f"{street['name']} {number}",
                        "secondary": f"{label_city or ''} · מיקום משוער (ליד מס׳ {near['number_int']})".strip(" ·"),
                        "house_number": number, "accuracy": "interpolated",
                        "confidence": 0.75 if abs(near["number_int"] - n) <= 6 else 0.55,
                        "location": {"lat": near["lat"], "lon": near["lon"]}}
    return {**base, "id": f"street-{street['id']}", "name": street["name"] + (f" {number}" if number else ""),
            "secondary": f"{label_city or ''}{' · מספר הבית לא נמצא, מוצג מרכז הרחוב' if number else ''}".strip(" ·"),
            "house_number": number, "accuracy": "street", "confidence": 0.5 if number else 0.8,
            "location": {"lat": street["lat"], "lon": street["lon"]}}
