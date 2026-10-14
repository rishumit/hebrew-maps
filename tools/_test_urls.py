"""Test all 15 DEM tiles needed for Israel to see which are blocked by NetFree."""
import ssl
import urllib.request

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

UA = {"User-Agent": "HebrewMaps/1.0"}
BASE = "https://copernicus-dem-30m.s3.amazonaws.com/{n}/{n}.tif"

def tile_name(lat, lon):
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    return f"Copernicus_DSM_COG_10_{ns}{abs(lat):02d}_00_{ew}{abs(lon):03d}_00_DEM"

for lat in range(29, 34):
    for lon in range(34, 37):
        n = tile_name(lat, lon)
        url = BASE.format(n=n)
        try:
            req = urllib.request.Request(url, headers=UA, method="HEAD")
            r = urllib.request.urlopen(req, timeout=15, context=ctx)
            mb = int(r.headers.get("Content-Length", 0)) // 1_000_000
            print(f"OK    {n}  ({mb} MB)")
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):
                print(f"SEA   {n}  (no tile)")
            else:
                print(f"BLOCK {n}  HTTP {e.code} {e.reason}")
        except Exception as e:
            print(f"FAIL  {n}  {e}")
