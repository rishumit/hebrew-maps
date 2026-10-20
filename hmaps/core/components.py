"""The downloadable data components ("חבילות עדכון") of a weekly data release.

One catalog shared by the release tooling (``tools/release.py``, used by
``.github/workflows/weekly-data.yml``) and the online updater
(``hmaps/api/updates.py``), so the CI and the client always agree on the
component ids, the ZIP layout and the Hebrew texts shown to the user.

Release layout (GitHub Releases of the repository)::

    data-YYYY-MM-DD/                 one dated release per weekly run
        hmaps-<component>-<YYYYMMDD>.zip
        manifest.json
    data-latest/                     rolling pointer: only manifest.json

``manifest.json`` always lists every component; components that were not
rebuilt in a run point at the asset of an earlier dated release
(``"status": "unchanged"``). Every ZIP holds a small ``component.json``
next to the payload members listed below.
"""

from __future__ import annotations

from dataclasses import dataclass, field

MANIFEST_SCHEMA = 1
#: name of the per-ZIP descriptor
DESCRIPTOR = "component.json"
#: the oldest app version that understands the ZIP layout of this catalog
MIN_APP_VERSION = "1.0.0"


@dataclass(frozen=True)
class Component:
    id: str
    name_he: str
    description_he: str
    #: members the ZIP must contain (paths inside the ZIP)
    required: tuple[str, ...]
    #: optional members / prefixes (a trailing "/" means a directory tree)
    optional: tuple[str, ...] = ()
    #: rebuilt by the weekly schedule (terrain only monthly – the DEM never changes)
    weekly: bool = True
    min_app_version: str = MIN_APP_VERSION
    extra: dict = field(default_factory=dict)


COMPONENTS: tuple[Component, ...] = (
    Component(
        "maps", "מפה ונקודות עניין",
        "מאגר המקומות, הרחובות, הכתובות והחיפוש, שבילים מסומנים, טקסטים ועובדות מוויקיפדיה ומוויקינתונים, "
        "וגרף הניווט המובנה. מבוסס OpenStreetMap ומתעדכן כל שבוע.",
        required=("maps.db",), optional=("routing/graph.npz", "routing/graph.json", "reports/"),
    ),
    Component(
        "basemap", "מפת בסיס",
        "אריחי המפה המלאה: כבישים, מבנים, שטחים פתוחים ושמות – OpenStreetMap (Planetiler). מתעדכן כל שבוע.",
        required=("israel.mbtiles",),
    ),
    Component(
        "terrain", "נתוני גובה וטופוגרפיה",
        "קווי גובה, הצללת תבליט, תלת-ממד ופרופיל גובה למסלולים – Copernicus DEM. משתנה לעיתים רחוקות "
        "(נבנה מחדש פעם בחודש).",
        required=("contours.mbtiles", "terrain.mbtiles"), weekly=False,
    ),
    Component(
        "transit", "תחבורה ציבורית",
        "לוחות הזמנים של התחבורה הציבורית ממשרד התחבורה (GTFS) לתכנון נסיעות ללא אינטרנט. מתעדכן כל שבוע.",
        required=("transit.npz", "transit.json"),
    ),
    Component(
        "media", "תמונות ותוכן",
        "תמונות של אתרים ושבילים מוויקישיתוף, לצפייה גם ללא חיבור לאינטרנט. מתווספות לתמונות הקיימות.",
        required=(), optional=("media/",),
    ),
    Component(
        "zmanim", "זמני תפילות",
        "זמני התפילות בבתי הכנסת (פרויקט זמני התפילות הארצי של נדרים פלוס). מתעדכן כל שבוע, ביום חמישי בלילה.",
        required=("zmanim.json",),
    ),
)

BY_ID: dict[str, Component] = {c.id: c for c in COMPONENTS}
IDS: tuple[str, ...] = tuple(BY_ID)


def get(component_id: str) -> Component:
    try:
        return BY_ID[component_id]
    except KeyError:
        raise KeyError(f"unknown component {component_id!r} (known: {', '.join(IDS)})") from None


def asset_name(component_id: str, version: str) -> str:
    """``hmaps-<component>-<YYYYMMDD>.zip``"""
    return f"hmaps-{component_id}-{version}.zip"


def parse_version(v: str | None) -> tuple[int, ...]:
    """'1.2.10' -> (1, 2, 10); tolerant of missing / non-numeric parts."""
    out = []
    for part in str(v or "0").split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out)


def member_allowed(component: Component, name: str) -> bool:
    """Whether a ZIP member belongs to the component layout (path traversal is rejected separately)."""
    if name == DESCRIPTOR or name in component.required:
        return True
    for opt in component.optional:
        if opt.endswith("/") and name.startswith(opt):
            return True
        if name == opt:
            return True
    return False
