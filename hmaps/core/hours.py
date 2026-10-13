"""Parser for the common subset of the OSM ``opening_hours`` syntax.

Supported: ``24/7``; rules separated by ``;`` (or ``||``); weekday ranges and
lists (``Su-Th``, ``Mo,We``); several time spans (``08:00-13:00,16:00-19:00``);
spans that cross midnight (``20:00-02:00``); ``off`` / ``closed``; rules with
no weekday (= every day); ``PH`` rules are ignored. Anything else (months,
weeks, sunrise/sunset...) makes the parser give up and return ``None`` so
the UI can show the raw value instead of a wrong one.

The week is represented Israeli style, Sunday first.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

DAY_KEYS = ("su", "mo", "tu", "we", "th", "fr", "sa")
DAY_NAMES_HE = ("ראשון", "שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת")
_OSM_DAYS = {"Su": 0, "Mo": 1, "Tu": 2, "We": 3, "Th": 4, "Fr": 5, "Sa": 6}
TZ = ZoneInfo("Asia/Jerusalem")

_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\+?$")
_DAY_TOKEN_RE = re.compile(r"^(Su|Mo|Tu|We|Th|Fr|Sa)(?:-(Su|Mo|Tu|We|Th|Fr|Sa))?$")

Span = tuple[int, int]  # minutes from the start of the day; end may exceed 1440


def _parse_days(sel: str) -> list[int] | None:
    days: list[int] = []
    for part in sel.split(","):
        part = part.strip()
        if part == "PH" or part == "SH":
            continue
        m = _DAY_TOKEN_RE.match(part)
        if not m:
            return None
        a = _OSM_DAYS[m.group(1)]
        b = _OSM_DAYS[m.group(2)] if m.group(2) else a
        d = a
        while True:
            if d not in days:
                days.append(d)
            if d == b:
                break
            d = (d + 1) % 7
    return days


def _parse_times(sel: str) -> list[Span] | None:
    spans: list[Span] = []
    for part in sel.split(","):
        m = _TIME_RE.match(part.strip())
        if not m:
            return None
        h1, m1, h2, m2 = (int(x) for x in m.groups())
        if h1 > 24 or h2 > 48 or m1 > 59 or m2 > 59:
            return None
        start, end = h1 * 60 + m1, h2 * 60 + m2
        if end <= start:
            end += 1440  # crosses midnight
        spans.append((start, end))
    return spans


def parse(raw: str | None) -> dict[int, list[Span]] | None:
    """Return ``{weekday: [spans]}`` (weekday 0 = Sunday) or ``None``."""
    if not raw:
        return None
    text = raw.strip()
    if text in {"24/7", "00:00-24:00", "Mo-Su 00:00-24:00", "Su-Sa 00:00-24:00"}:
        return {d: [(0, 1440)] for d in range(7)}
    week: dict[int, list[Span]] = {}
    rules = [r.strip() for r in re.split(r";|\|\|", text) if r.strip()]
    if not rules:
        return None
    for rule in rules:
        rule = re.sub(r"\s+", " ", rule.replace("–", "-"))
        if rule.startswith("PH") and not rule.startswith(("PH,", "PH ,")):
            continue  # public-holiday rules are informational only
        tokens = rule.split(" ", 1)
        days: list[int] | None
        rest: str
        if _DAY_TOKEN_RE.match(tokens[0].split(",")[0]) or tokens[0].startswith("PH"):
            days = _parse_days(tokens[0])
            rest = tokens[1].strip() if len(tokens) > 1 else ""
        else:
            days = list(range(7))
            rest = rule
        if days is None:
            return None
        rest = rest.removesuffix(" open").strip()
        if rest in {"off", "closed"}:
            for d in days:
                week[d] = []
            continue
        if rest == "24/7" or rest == "00:00-24:00":
            spans: list[Span] | None = [(0, 1440)]
        else:
            spans = _parse_times(rest.replace(" ", "")) if rest else None
        if spans is None:
            return None
        for d in days:
            week[d] = list(spans)  # later rules override earlier ones
    for d in range(7):
        week.setdefault(d, [])
    return week


def _fmt(minutes: int) -> str:
    minutes %= 1440
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def to_json(week: dict[int, list[Span]] | None) -> dict | None:
    """Serializable form used by the API and the frontend."""
    if week is None:
        return None
    return {DAY_KEYS[d]: [[s, e] for s, e in week[d]] for d in range(7)}


def describe_he(week: dict[int, list[Span]] | None) -> list[dict] | None:
    """Rows for display: ``[{"day": "ראשון", "text": "08:00–17:00"}, ...]``."""
    if week is None:
        return None
    rows = []
    for d in range(7):
        spans = week[d]
        if not spans:
            text = "סגור"
        elif spans == [(0, 1440)]:
            text = "פתוח 24 שעות"
        else:
            text = ", ".join(f"{_fmt(s)}–{_fmt(e)}" for s, e in spans)
        rows.append({"day": DAY_NAMES_HE[d], "text": text})
    return rows


def is_open(week: dict[int, list[Span]] | None, when: datetime | None = None) -> bool | None:
    """Whether the place is open at *when* (default: now in Israel)."""
    if week is None:
        return None
    when = (when or datetime.now(TZ)).astimezone(TZ)
    day = (when.weekday() + 1) % 7  # Python Monday=0 -> Sunday=0
    minute = when.hour * 60 + when.minute
    for s, e in week[day]:
        if s <= minute < e:
            return True
    prev = (day - 1) % 7
    for s, e in week[prev]:  # spans of yesterday that cross midnight
        if e > 1440 and minute < e - 1440:
            return True
    return False


def next_change(week: dict[int, list[Span]] | None, when: datetime | None = None) -> datetime | None:
    """Next opening/closing moment within a week (for "נסגר ב-17:00")."""
    if week is None:
        return None
    when = (when or datetime.now(TZ)).astimezone(TZ).replace(second=0, microsecond=0)
    state = is_open(week, when)
    t = when
    for _ in range(7 * 24 * 4):  # 15 minute steps are plenty for display
        t += timedelta(minutes=15)
        if is_open(week, t) != state:
            # refine to the exact minute
            back = t - timedelta(minutes=15)
            while is_open(week, back) == state:
                back += timedelta(minutes=1)
            return back
    return None
