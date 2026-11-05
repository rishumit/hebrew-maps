"""Import the eruv data of the "גבולות עירוב" plugin (data from מוקד העירוב, used with their permission).

Reads the plugin's data files from data-sources/eruv/ (or the folder given as the first argument):
  data.js        window.ERUV_DATA   – cities, maps (scans on hiruv.com), areas with the people in charge
  eruv-lines.js  window.ERUV_LINES  – approximate eruv lines traced from the scans ([lat, lon])
  docs.js        window.ERUV_DOCS   – תחום שבת articles and booklets
  extra.js       window.ERUV_EXTRA  – library + eruvin abroad
  app.js         the plugin's app – hospitals / organisations live in it as JS literals (read with node)

Writes:
  web/eruv/eruv.json         everything the map needs at start (small)
  web/eruv/abroad.json  eruvin abroad + library (loaded only when opened)

Run:  python tools/import_eruv.py [source-folder]
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "web" / "eruv"


def _js_json(path: Path, var: str):
    """`window.VAR = {...};` -> the parsed JSON value."""
    text = path.read_text(encoding="utf-8")
    m = re.search(r"window\." + re.escape(var) + r"\s*=\s*", text)
    if not m:
        raise SystemExit(f"{path.name}: window.{var} not found")
    body = text[m.end():].strip()
    body = body[: body.rstrip().rfind(";")] if body.rstrip().endswith(";") else body
    return json.loads(body)


# the literals inside the plugin's app.js that hold hand-checked data (read with node: they are JS, not JSON)
_NODE = r"""
const fs = require('fs');
const src = fs.readFileSync(process.argv[1], 'utf8');
function grab(name) {
  const i = src.indexOf('const ' + name + ' =');
  if (i < 0) return null;
  let j = src.indexOf('=', i) + 1, depth = 0, k = j, q = null;
  for (; k < src.length; k++) {
    const c = src[k];
    if (q) { if (c === '\\') { k++; continue; } if (c === q) q = null; continue; }
    if (c === '/' && src[k + 1] === '/') { k = src.indexOf('\n', k); continue; }
    if (c === '/' && src[k + 1] === '*') { k = src.indexOf('*/', k) + 1; continue; }
    if (c === "'" || c === '"' || c === '`') { q = c; continue; }
    if (c === '[' || c === '{') depth++;
    else if (c === ']' || c === '}') { depth--; if (depth === 0) { k++; break; } }
    else if (c === ';' && depth === 0) break;
  }
  return Function('return (' + src.slice(j, k) + ')')();
}
const out = {};
for (const n of ['HOSPITALS', 'ORGS', 'MORE_SOURCES', 'HOSP_EZER', 'AREA_LINKS']) out[n] = grab(n);
process.stdout.write(JSON.stringify(out));
"""


def _app_literals(app_js: Path) -> dict:
    node = shutil.which("node")
    if not node or not app_js.exists():
        print("  ! node or app.js missing – hospitals are kept from the previous import")
        return {}
    res = subprocess.run([node, "-e", _NODE, str(app_js)], capture_output=True, text=True, encoding="utf-8", check=True)
    return json.loads(res.stdout)


def _phone(p: str) -> str:
    """0527634515 -> 052-763-4515 style (as the plugin shows them); other formats unchanged."""
    d = re.sub(r"\D", "", p or "")
    if len(d) == 10 and d.startswith("05"):
        return f"{d[:3]}-{d[3:]}"
    if len(d) == 9 and d[0] == "0":
        return f"{d[:2]}-{d[2:]}"
    return p


def main() -> None:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data-sources" / "eruv"
    print(f"eruv: reading {src}")
    data = _js_json(src / "data.js", "ERUV_DATA")
    lines = _js_json(src / "eruv-lines.js", "ERUV_LINES")
    docs = _js_json(src / "docs.js", "ERUV_DOCS")
    extra = _js_json(src / "extra.js", "ERUV_EXTRA")
    lit = _app_literals(src / "app.js")

    areas = data["areas"]
    for a in areas:
        a["phones"] = [_phone(p) for p in a.get("phones", [])]
    # the plugin adds this one itself (given by the plugin's author, not by the national מוקד)
    if not any("050-4154005" in a["phones"] for a in areas):
        areas.append({"city": "בני ברק", "n": "", "area": "ועד העירובים השכונתיים",
                      "names": ['רפאל סטפנסקי (אחראי בפ"כ)'], "phones": ["050-4154005"],
                      "emails": [["ועד העירובים השכונתיים", "eruv.bbc@gmail.com"]], "maps": []})
    links = lit.get("AREA_LINKS") or {}
    for a in areas:
        key = f"{a['city']}|{a['area']}"
        if key in links:
            a["links"] = links[key]

    # lines: [lat, lon] -> GeoJSON [lon, lat]; closed lines become polygons (for "inside the eruv?")
    features = []
    for mid, ln in lines.items():
        ring = [[round(lon, 6), round(lat, 6)] for lat, lon in ln["coords"]]
        closed = bool(ln.get("closed"))
        if closed and ring[0] != ring[-1]:
            ring.append(ring[0])
        features.append({
            "type": "Feature", "id": mid,
            "geometry": {"type": "Polygon", "coordinates": [ring]} if closed else {"type": "LineString", "coordinates": ring},
            "properties": {"map": mid, "city": ln.get("city", ""), "name": ln.get("name", ""),
                           "resp": ln.get("resp", ""), "closed": closed},
        })

    previous = {}
    target = OUT / "eruv.json"
    if target.exists():
        previous = json.loads(target.read_text(encoding="utf-8"))
    hosp = {"hospitals": lit.get("HOSPITALS"), "orgs": lit.get("ORGS"),
            "more": lit.get("MORE_SOURCES"), "ezer": lit.get("HOSP_EZER")}
    if not hosp["hospitals"]:
        hosp = previous.get("hospitals_info") or {}

    for d in docs:
        d.pop("dims", None)  # page sizes are for the plugin's own PDF viewer
    out = {
        "source": data["source"], "built": data.get("built"),
        "cities": data["cities"], "maps": data["maps"], "areas": areas,
        "lines": {"type": "FeatureCollection", "features": features},
        "docs": docs, "hospitals_info": hosp,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (OUT / "abroad.json").write_text(json.dumps(extra, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"  {len(data['cities'])} cities, {len(areas)} areas, {len(data['maps'])} maps, {len(features)} lines, "
          f"{len(docs)} docs, {len(hosp.get('hospitals') or [])} hospitals, {len(extra.get('abroad', []))} abroad")
    print(f"  -> {target.relative_to(ROOT)}, web/eruv/abroad.json")


if __name__ == "__main__":
    main()
