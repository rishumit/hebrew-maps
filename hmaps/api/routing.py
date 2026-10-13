"""Routing through local OSRM servers + Hebrew turn-by-turn instructions."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any

from ..core.hebrew import format_distance, format_duration

log = logging.getLogger(__name__)

MODES = {"car": "רכב", "foot": "הליכה", "bike": "אופניים"}

_MODIFIER = {
    "left": "שמאלה", "right": "ימינה", "slight left": "קלות שמאלה", "slight right": "קלות ימינה",
    "sharp left": "חדות שמאלה", "sharp right": "חדות ימינה", "straight": "ישר", "uturn": "פניית פרסה",
}
_SIDE = {"left": "משמאל", "right": "מימין", "slight left": "משמאל", "slight right": "מימין",
         "sharp left": "משמאל", "sharp right": "מימין"}
_KEEP = {"left": "שמאלה", "right": "ימינה", "slight left": "שמאלה", "slight right": "ימינה", "straight": "ישר"}
_ORDINAL = ["הראשונה", "השנייה", "השלישית", "הרביעית", "החמישית", "השישית", "השביעית", "השמינית"]
_COMPASS = ["צפון", "צפון-מזרח", "מזרח", "דרום-מזרח", "דרום", "דרום-מערב", "מערב", "צפון-מערב"]
_STREET_TYPES = ("רחוב", "שדרות", "שד'", "דרך", "כביש", "סמטת", "סמ'", "כיכר", "ככר", "שביל", "מעלה", "מעלות", "טיילת",
                 "גשר", "מחלף", "צומת", "רחבת", "מדרחוב", "שכונת", "שער", "נתיב", "משעול")


def _road(step: dict) -> str | None:
    """Readable road name: "רחוב הרצל" / "כביש 4" / "דרך מנחם בגין (כביש 4)"."""
    name = (step.get("name") or "").strip()
    ref = (step.get("ref") or "").strip().split(";")[0]
    if name and ref and ref not in name:
        return f"{name} (כביש {ref})"
    if name:
        return name
    if ref:
        return f"כביש {ref}"
    return None


def _to(road: str) -> str:
    """'ל' + road with a type word when needed: "לרחוב דיזנגוף", "לשדרות רוטשילד", "לכביש 1"."""
    if road.startswith(_STREET_TYPES):
        return "ל" + road
    return "לרחוב " + road


def _in(road: str) -> str:
    if road.startswith(_STREET_TYPES):
        return "ב" + road
    return "ברחוב " + road


def _compass(bearing: float | None) -> str:
    if bearing is None:
        return ""
    return _COMPASS[int(((bearing % 360) + 22.5) // 45) % 8]


def instruction(step: dict, mode: str = "car") -> str:
    """Hebrew instruction for one OSRM step (plural imperative, gender neutral)."""
    m = step.get("maneuver", {})
    t = m.get("type", "")
    mod = m.get("modifier", "")
    road = _road(step)
    dest = (step.get("destinations") or "").split(",")[0].strip() or None
    exit_n = m.get("exit")
    turn = _MODIFIER.get(mod, "")

    if t == "depart":
        verb = "התחילו ללכת" if mode == "foot" else ("התחילו לרכוב" if mode == "bike" else "התחילו לנסוע")
        direction = _compass(m.get("bearing_after"))
        s = f"{verb} לכיוון {direction}" if direction else verb
        return f"{s} {_in(road)}" if road else s
    if t == "arrive":
        side = _SIDE.get(mod)
        return f"הגעתם ליעד, היעד {side}" if side else "הגעתם ליעד"
    if t in ("roundabout", "rotary", "roundabout turn"):
        ordinal = _ORDINAL[exit_n - 1] if exit_n and 0 < exit_n <= len(_ORDINAL) else None
        rot = step.get("rotary_name")
        where = f"בכיכר {rot}" if rot else "בכיכר"
        if ordinal:
            s = f"{where} צאו ביציאה {ordinal}"
        else:
            s = f"{where} המשיכו {turn}" if turn else f"היכנסו לכיכר"
        return f"{s} {_to(road)}" if road else s
    if t in ("exit roundabout", "exit rotary"):
        return f"צאו מהכיכר {_to(road)}" if road else "צאו מהכיכר"
    if t == "merge":
        return f"השתלבו {_in(road)}" if road else "השתלבו בתנועה"
    if t == "on ramp":
        if dest:
            return f"עלו על הכביש לכיוון {dest}"
        return f"עלו {_to(road)}" if road else "עלו על הכביש"
    if t == "off ramp":
        s = f"צאו מהכביש {turn}".strip() if mod not in ("straight", "") else "צאו מהכביש"
        if dest:
            return f"{s} לכיוון {dest}"
        return f"{s} {_to(road)}" if road else s
    if t == "fork":
        side = _KEEP.get(mod, "")
        s = f"בפיצול הישארו {side}".strip()
        if dest:
            return f"{s} לכיוון {dest}"
        return f"{s} {_to(road)}" if road else s
    if t == "end of road":
        return f"בסוף הדרך פנו {turn} {_to(road)}" if road else f"בסוף הדרך פנו {turn}"
    if t == "new name":
        return f"המשיכו {_in(road)}" if road else "המשיכו ישר"
    if t == "continue":
        if mod == "uturn":
            return "בצעו פניית פרסה"
        if mod in ("straight", ""):
            return f"המשיכו ישר {_in(road)}" if road else "המשיכו ישר"
        return f"המשיכו {turn} {_in(road)}" if road else f"המשיכו {turn}"
    if t == "notification" or t == "use lane":
        return f"המשיכו {_in(road)}" if road else "המשיכו ישר"
    # turn (and anything unknown)
    if mod == "uturn":
        return "בצעו פניית פרסה" + (f" {_to(road)}" if road else "")
    if mod == "straight":
        return f"המשיכו ישר {_in(road)}" if road else "המשיכו ישר"
    return f"פנו {turn} {_to(road)}" if road else f"פנו {turn}"


def _summary(route: dict) -> str:
    names: list[str] = []
    for leg in route.get("legs", []):
        for st in sorted(leg.get("steps", []), key=lambda s: -s.get("distance", 0))[:2]:
            r = _road(st)
            if r and r not in names:
                names.append(r)
    return "דרך " + " ו".join(names[:2]) if names else ""


def shape(osrm: dict, mode: str) -> dict:
    routes = []
    for r in osrm.get("routes", []):
        steps = []
        for leg in r.get("legs", []):
            for st in leg.get("steps", []):
                m = st.get("maneuver", {})
                steps.append({
                    "instruction": instruction(st, mode),
                    "name": _road(st),
                    "distance": round(st.get("distance", 0), 1),
                    "distance_text": format_distance(st.get("distance", 0)) if st.get("distance") else "",
                    "duration": round(st.get("duration", 0), 1),
                    "maneuver": {"type": m.get("type"), "modifier": m.get("modifier"), "location": m.get("location"),
                                 "exit": m.get("exit")},
                })
        routes.append({
            "distance": round(r.get("distance", 0), 1), "duration": round(r.get("duration", 0), 1),
            "distance_text": format_distance(r.get("distance", 0)), "duration_text": format_duration(r.get("duration", 0)),
            "summary": _summary(r), "geometry": r.get("geometry"), "steps": steps,
        })
    return {"mode": mode, "mode_he": MODES.get(mode, mode), "routes": routes}


class RoutingError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def route(base_urls: dict[str, str], mode: str, points: list[tuple[float, float]], alternatives: bool = True,
          timeout: float = 15.0) -> dict:
    """Query OSRM. *points* are (lat, lon) pairs: origin, [via...], destination."""
    if mode not in base_urls:
        raise RoutingError(400, "אמצעי תחבורה לא נתמך")
    if len(points) < 2:
        raise RoutingError(400, "יש לבחור נקודת מוצא ויעד")
    coords = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in points)
    profile = {"car": "driving", "foot": "walking", "bike": "cycling"}[mode]
    url = (f"{base_urls[mode].rstrip('/')}/route/v1/{profile}/{coords}?overview=full&geometries=geojson&steps=true"
           f"&alternatives={'true' if alternatives and len(points) == 2 else 'false'}&continue_straight=default")
    try:
        with urllib.request.urlopen(urllib.request.Request(url), timeout=timeout) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            data = json.loads(e.read())
        except Exception:  # noqa: BLE001
            raise RoutingError(502, "שירות הניווט החזיר שגיאה") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        log.warning("routing backend unavailable (%s): %s", mode, e)
        raise RoutingError(503, "שירות הניווט אינו זמין כרגע. ייתכן שהוא עדיין בהכנה – נסו שוב בעוד כמה דקות.") from e
    code = data.get("code")
    if code == "NoRoute":
        raise RoutingError(404, "לא נמצא מסלול בין הנקודות שנבחרו")
    if code == "NoSegment":
        raise RoutingError(404, "אחת הנקודות רחוקה מדי מדרך – נסו לבחור נקודה קרובה יותר לכביש או לשביל")
    if code != "Ok":
        raise RoutingError(502, f"שגיאה בחישוב המסלול ({code})")
    return shape(data, mode)


def health(base_urls: dict[str, str], timeout: float = 2.0) -> dict[str, bool]:
    out = {}
    for mode, base in base_urls.items():
        url = f"{base.rstrip('/')}/nearest/v1/x/34.7818,32.0853"
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                out[mode] = json.loads(resp.read()).get("code") == "Ok"
        except Exception:  # noqa: BLE001
            out[mode] = False
    return out


def any_value(d: dict[str, Any]) -> bool:
    return any(d.values())


# ---------------------------------------------------------------------------- back-end selection

def _local(graph_path, mode: str, points: list[tuple[float, float]]) -> dict:
    from ..route.engine import NoRoute, get_router

    router = get_router(graph_path)
    if router is None:
        raise RoutingError(503, "שירות הניווט אינו זמין כרגע. ייתכן שהוא עדיין בהכנה – נסו שוב בעוד כמה דקות.")
    try:
        data = router.route(mode, points)
    except NoRoute as e:
        if e.code == "NoSegment":
            raise RoutingError(404, "אחת הנקודות רחוקה מדי מדרך – נסו לבחור נקודה קרובה יותר לכביש או לשביל") from e
        raise RoutingError(404, "לא נמצא מסלול בין הנקודות שנבחרו") from e
    return shape(data, mode)


def route_any(settings, mode: str, points: list[tuple[float, float]], alternatives: bool = True) -> dict:
    """Route with OSRM and/or the built-in router according to ``HMAPS_ROUTER``."""
    if mode not in MODES:
        raise RoutingError(400, "אמצעי תחבורה לא נתמך")
    if settings.router == "local":
        return _local(settings.graph_path, mode, points)
    try:
        return route(settings.osrm, mode, points, alternatives)
    except RoutingError as e:
        if settings.router == "auto" and e.status == 503 and settings.graph_path.exists():
            return _local(settings.graph_path, mode, points)
        raise


def status(settings) -> dict[str, bool]:
    local = settings.graph_path.exists()
    if settings.router == "local":
        return {m: local for m in MODES}
    st = health(settings.osrm, timeout=1.0)
    if settings.router == "auto" and local:
        return {m: True for m in MODES}
    return st
