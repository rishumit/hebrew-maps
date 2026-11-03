"""Estimated fares of a public-transport itinerary (Israel, "רפורמת צדק תחבורתי" distance bands).

A ride costs by the air-line distance between where you board and where you get off; within the
15 km band (yellow) buses / light rail / Metronit / Carmelit / cable car include free transfers for
90 minutes from the first validation. The train has its own table and no free transfer to buses.
Consecutive train legs are one train ride (no new validation when changing trains).

The table is the single-ride price with Rav-Kav "ערך צבור" (VAT included), as published by the
Ministry of Transport (rav-pass price list). Prices change once or twice a year – update
:data:`TABLE` / :data:`UPDATED`; the app always says the price is an estimate.
Monthly / daily passes and discounts (students, seniors …) are not taken into account.
"""

from __future__ import annotations

import math
from datetime import datetime

UPDATED = "2026-06"
SOURCE_URL = "https://s3-eu-west-1.amazonaws.com/static.hopon.co.il/mot/ravPassPrices.html"
#: (max km, bus / light rail ₪, train ₪ or None)
TABLE = (
    (15, 8.0, 11.5),
    (40, 14.5, 21.0),
    (75, 19.0, 27.0),
    (120, 19.0, 30.5),
    (225, 30.5, 52.5),
    (math.inf, 87.32, None),
)
TRANSFER_MINUTES = 90
FREE_TRANSFER_KM = 15
BUS_LIKE = {"bus", "light_rail", "subway", "tram", "cable_car", "share_taxi", "trolleybus", "funicular"}


def air_km(a: dict, b: dict) -> float:
    p1, p2 = math.radians(a["lat"]), math.radians(b["lat"])
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(b["lon"] - a["lon"]) / 2) ** 2
    return 12_742.0176 * math.asin(min(1.0, math.sqrt(h)))


def band(km: float, train: bool = False) -> float | None:
    for limit, bus, rail in TABLE:
        if km <= limit:
            return rail if train else bus
    return None


def _minutes(a: str, b: str) -> float:
    try:
        return (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds() / 60
    except (TypeError, ValueError):
        return math.inf


def estimate(itinerary: dict) -> dict | None:
    """``{"total", "text", "parts": [{"label", "price"}], "estimated": True}``; None for a walk-only trip."""
    rides = [lg for lg in itinerary.get("legs", []) if lg.get("type") != "walk"]
    if not rides:
        return None
    # group the rides: consecutive trains are one train ride; buses in the yellow band share one ticket
    groups: list[list[dict]] = []
    for lg in rides:
        train = lg["type"] == "rail"
        if groups and train and groups[-1][-1]["type"] == "rail":
            groups[-1].append(lg)
            continue
        if groups and not train and groups[-1][-1]["type"] in BUS_LIKE:
            g = groups[-1]
            if (air_km(g[0]["from"], lg["to"]) <= FREE_TRANSFER_KM and air_km(g[0]["from"], g[-1]["to"]) <= FREE_TRANSFER_KM
                    and _minutes(g[0]["departure"], lg["departure"]) <= TRANSFER_MINUTES):
                g.append(lg)
                continue
        groups.append([lg])
    parts = []
    total = 0.0
    for g in groups:
        train = g[0]["type"] == "rail"
        km = air_km(g[0]["from"], g[-1]["to"])
        price = band(km, train)
        if price is None:
            return None
        lines = " + ".join(dict.fromkeys(lg.get("line") or lg.get("mode_he") or "" for lg in g))
        parts.append({"label": lines, "price": price, "km": round(km, 1),
                      "note": "כולל מעבר חינם" if len(g) > 1 and not train else ""})
        total += price
    return {"total": round(total, 2), "text": f"₪{_fmt(total)}", "parts": parts, "estimated": True,
            "updated": UPDATED, "source": SOURCE_URL}


def _fmt(v: float) -> str:
    return f"{v:.2f}".rstrip("0").rstrip(".")
