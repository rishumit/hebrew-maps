"""Import the places of "מדריך מקומות בתנ״ך" (Otzaria plugin by חד בדרא and נתנאל26).

Reads data-sources/bible-places/places.js (or the file given as the first argument) – a JS file with
`const CATS = [...]`, `const DATA = [...]` – evaluates it with node, and writes web/bible/places.json:
  cats    the categories (id, label, blurb)
  places  one entry per place: name, cat, region, aliases, methods (with geo [lat, lon, zoom] and a
          normalised confidence level), verses, midrash, and the border / refuge / levite flags

Run:  python tools/import_bible_places.py [places.js]
"""
from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "web" / "bible" / "places.json"

_NODE = r"""
const src = require('fs').readFileSync(process.argv[1], 'utf8');
const r = Function(src + '\n;return { CATS, DATA };')();
process.stdout.write(JSON.stringify(r));
"""

# the plugin's confidence words -> three levels for the map (high: solid, mid: lighter, low: hollow)
LEVEL = {"ודאי": "high", "ודאי (מהגמרא עצמה)": "high", "מקובל": "high",
         "סביר": "mid", "משוער": "mid", "שנוי": "low", "לא ודאי": "low"}


def _text(s: str) -> str:
    """Verse / note text: entities decoded (&thinsp; …), tags dropped, spaces tidied."""
    s = html.unescape(re.sub(r"<[^>]+>", "", s or ""))
    return re.sub(r"[ \t]+", " ", s.replace(" ", " ")).strip()


def main() -> None:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data-sources" / "bible-places" / "places.js"
    node = shutil.which("node")
    if not node:
        raise SystemExit("node is needed to read places.js")
    res = subprocess.run([node, "-e", _NODE, str(src)], capture_output=True, text=True, encoding="utf-8", check=True)
    raw = json.loads(res.stdout)

    places, seen = [], {}
    for d in raw["DATA"]:
        n = seen[d["name"]] = seen.get(d["name"], 0) + 1
        pid = d["name"] if n == 1 else f"{d['name']} ({n})"
        methods = []
        for m in d.get("methods", []):
            geo = m.get("geo")
            methods.append({
                "label": m.get("label", ""), "confidence": m.get("confidence", ""),
                "level": LEVEL.get(m.get("confidence", ""), "mid"),
                "explanation": _text(m.get("explanation", "")), "modern": _text(m.get("modern", "")),
                "geo": [round(geo[0], 5), round(geo[1], 5), geo[2] if len(geo) > 2 else 12] if geo else None,
            })
        p = {
            "id": pid, "name": d["name"], "cat": d["cat"], "aliases": d.get("aliases", []), "methods": methods,
            "verses": [{"ref": v.get("ref", ""), "text": _text(v.get("text", ""))} for v in d.get("verses", [])],
            "midrash": [{"source": x.get("source", ""), "note": _text(x.get("note", ""))} for x in d.get("midrash", [])],
        }
        if d.get("region"):
            p["region"] = d["region"]
        for flag in ("border", "refuge", "levite"):
            if d.get(flag):
                p[flag] = True
        places.append(p)

    cats = [{"id": c["id"], "label": c["label"], "blurb": c.get("blurb", "")} for c in raw["CATS"]]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"cats": cats, "places": places}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    on_map = sum(1 for p in places if any(m["geo"] for m in p["methods"]))
    print(f"bible places: {len(places)} places ({on_map} on the map), {len(cats)} categories -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
