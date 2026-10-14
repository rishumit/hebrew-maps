"""Check Hamichlol (hamichlol.org.il) as a source: robots.txt, API extensions, extracts, geosearch."""
import json, time, urllib.parse, urllib.request, urllib.error
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "data" / "reports" / "michlol-diag.txt"
UA = "HebrewMaps/1.1 (https://github.com/rishumit/hebrew-maps; self-hosted open-data map of Israel) python-urllib"
lines = [time.strftime("%Y-%m-%d %H:%M:%S")]


def get(name, url, n=3000):
    t0 = time.time()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=30) as r:
            body = r.read().decode("utf-8", "replace")
            lines.append(f"== {name}: {r.status} {r.headers.get('Content-Type')} {time.time()-t0:.1f}s url={r.geturl()}")
            lines.append(body[:n])
            return body
    except urllib.error.HTTPError as e:
        lines.append(f"== {name}: HTTP {e.code} {time.time()-t0:.1f}s :: {e.read(400)!r}")
    except Exception as e:
        lines.append(f"== {name}: ERROR {type(e).__name__}: {e}")
    return None


q = urllib.parse.urlencode
get("robots", "https://www.hamichlol.org.il/robots.txt", 4000)
for api in ("https://www.hamichlol.org.il/w/api.php", "https://www.hamichlol.org.il/api.php"):
    b = get(f"siteinfo {api}", api + "?" + q({"action": "query", "meta": "siteinfo", "siprop": "general|extensions|rightsinfo", "format": "json"}), 200)
    if b and b.lstrip().startswith("{"):
        try:
            d = json.loads(b)
            g = d["query"]["general"]
            lines.append("general: " + json.dumps({k: g.get(k) for k in ("sitename", "server", "scriptpath", "articlepath", "generator", "lang")}, ensure_ascii=False))
            lines.append("rights: " + json.dumps(d["query"].get("rightsinfo"), ensure_ascii=False))
            lines.append("extensions: " + ", ".join(sorted(e.get("name", "") for e in d["query"].get("extensions", []))))
        except Exception as e:
            lines.append(f"parse error {e}")
        get("extracts", api + "?" + q({"action": "query", "format": "json", "formatversion": 2, "prop": "extracts|pageimages|pageprops|coordinates",
                                         "exintro": 1, "explaintext": 1, "redirects": 1, "titles": "עין גדי|שמורת עין גדי|מצדה"}), 2500)
        get("geosearch", api + "?" + q({"action": "query", "format": "json", "formatversion": 2, "list": "geosearch",
                                          "gscoord": "31.4625|35.3875", "gsradius": 3000, "gslimit": 10}), 1200)
        get("search", api + "?" + q({"action": "query", "format": "json", "formatversion": 2, "list": "search",
                                       "srsearch": "עין גדי", "srlimit": 5}), 1200)
        break
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text("\n".join(lines), encoding="utf-8")
print("\n".join(lines))
