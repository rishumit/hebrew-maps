"""Formatting helpers for contact data (phones, websites)."""

from __future__ import annotations

import re

_DIGITS = re.compile(r"\D+")


def format_phone(raw: str | None) -> str | None:
    """Normalize Israeli phone numbers to the local display format.

    ``+972 3 123 4567`` -> ``03-1234567``; ``+972-52-1234567`` -> ``052-1234567``;
    ``*2345`` and ``1-700-...`` service numbers are kept readable. Several
    numbers separated by ``;`` are normalized individually and re-joined.
    """
    if not raw:
        return None
    out = []
    for part in re.split(r"[;,]", raw):
        part = part.strip()
        if not part:
            continue
        if part.startswith("*"):
            out.append("*" + _DIGITS.sub("", part))
            continue
        d = _DIGITS.sub("", part)
        if d.startswith("00972"):
            d = d[5:]
        if d.startswith("972"):
            d = d[3:]
        if d.startswith(("1700", "1800", "1599")) and len(d) == 10:
            out.append(f"{d[:4]}-{d[4:7]}-{d[7:]}")
            continue
        if not d.startswith("0") and len(d) in (8, 9):
            d = "0" + d
        if len(d) == 10 and d.startswith(("05", "07")):
            out.append(f"{d[:3]}-{d[3:]}")
        elif len(d) == 9 and d.startswith("0"):
            out.append(f"{d[:2]}-{d[2:]}")
        elif d:
            out.append(part)
    return "; ".join(dict.fromkeys(out)) or None


def tel_href(display: str) -> str:
    first = display.split(";")[0].strip()
    if first.startswith("*"):
        return "tel:" + first
    d = _DIGITS.sub("", first)
    if d.startswith("0"):
        return "tel:+972" + d[1:]
    return "tel:" + d


def normalize_url(raw: str | None) -> str | None:
    if not raw:
        return None
    url = raw.strip().split(";")[0].strip()
    if not url:
        return None
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    if " " in url or "." not in url:
        return None
    return url
