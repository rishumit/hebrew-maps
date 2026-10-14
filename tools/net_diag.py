"""Network check for the enrichment sources: status, content type, timing, first bytes."""
import sys, time, urllib.request, urllib.error, urllib.parse, json, os
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "data" / "reports" / "net-diag.txt"
UAS = {"new": "HebrewMaps/1.1 (self-hosted open-data map of Israel; rich-content enrichment)",
       "old": "HebrewMaps/1.0 (self-hosted open-data map of Israel; https://github.com/)"}
q = urllib.parse.urlencode
URLS = [
    ("hewiki simple", "https://he.wikipedia.org/w/api.php?" + q({"action": "query", "format": "json", "titles": "עין גדי", "prop": "info"})),
    ("hewiki extracts v2 maxlag", "https://he.wikipedia.org/w/api.php?" + q({"action": "query", "format": "json", "formatversion": 2, "maxlag": 5, "prop": "extracts", "exintro": 1, "explaintext": 1, "titles": "עין גדי"})),
    ("hewiki geosearch", "https://he.wikipedia.org/w/api.php?" + q({"action": "query", "format": "json", "formatversion": 2, "generator": "geosearch", "ggscoord": "31.4625|35.3875", "ggsradius": 1000, "ggslimit": 5})),
    ("commons geosearch", "https://commons.wikimedia.org/w/api.php?" + q({"action": "query", "format": "json", "formatversion": 2, "generator": "geosearch", "ggscoord": "31.4625|35.3875", "ggsradius": 300, "ggslimit": 5, "ggsnamespace": 6, "prop": "imageinfo", "iiprop": "url|size|mime"})),
    ("commons simple", "https://commons.wikimedia.org/w/api.php?action=query&format=json&meta=siteinfo"),
    ("wikidata sparql", "https://query.wikidata.org/sparql?" + q({"query": "SELECT ?x WHERE { wd:Q1 wdt:P31 ?x } LIMIT 1", "format": "json"})),
    ("hewikivoyage", "https://he.wikivoyage.org/w/api.php?action=query&format=json&meta=siteinfo"),
    ("upload image", "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a9/Example.jpg/120px-Example.jpg"),
]
lines = [time.strftime("%Y-%m-%d %H:%M:%S"), f"python {sys.version.split()[0]}",
         "proxy env: " + ", ".join(f"{k}={v}" for k, v in os.environ.items() if "proxy" in k.lower())]
for name, url in URLS:
    for ua_name, ua in UAS.items():
        t0 = time.time()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                body = r.read(400)
                lines.append(f"[{name} / {ua_name}] {r.status} {r.headers.get('Content-Type')} {time.time()-t0:.1f}s :: {body[:300]!r}")
        except urllib.error.HTTPError as e:
            lines.append(f"[{name} / {ua_name}] HTTP {e.code} {e.headers.get('Content-Type')} {time.time()-t0:.1f}s :: {e.read(300)!r}")
        except Exception as e:
            lines.append(f"[{name} / {ua_name}] ERROR {type(e).__name__}: {e} {time.time()-t0:.1f}s")
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text("\n".join(lines), encoding="utf-8")
print("\n".join(lines))
