"""Performance benchmark on a synthetic, country-scale dataset.

    python -m tests.perf_bench [N_POIS]

Targets (docs/01-requirements.md §6): autocomplete < 200 ms, POI card < 500 ms.
"""
import statistics
import sys
import time
from pathlib import Path

from hmaps.api.pois import poi_detail
from hmaps.api.search import SearchEngine, nearby, reverse
from hmaps.api.store import Dataset
from hmaps.api import tiles
from hmaps.core import geo
from tests.helpers import sample_db

QUERIES = ["ירו", "דיזנגוף", "דיזינגוף", "מסעדה לדוגמה 1234", "בית קפה", "מסעדות בתל אביב", "בתי כנסת ליד",
           "רחוב לדוגמה 77", "דיזנגוף 50 תל אביב", "פלאפל", "מרכול לדוגמה", "חיפה", "בני ב", "גן שעשועים לדוגמה 5"]


def bench(name, fn, n=1):
    times = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        times.append((time.perf_counter() - t0) * 1000)
    return times


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 120_000
    t0 = time.perf_counter()
    db = sample_db(bulk=n)
    print(f"ETL for {n:,} synthetic POIs: {time.perf_counter() - t0:.1f}s, db {db.stat().st_size / 1e6:.1f} MB")
    ds = Dataset(Path(db))
    se = SearchEngine(ds)
    report = {}
    for q in QUERIES:
        for prefix in range(2, len(q) + 1, 2):
            part = q[:prefix]
            report.setdefault("autocomplete", []).extend(bench(part, lambda: se.autocomplete(part, 32.08, 34.78)))
        report.setdefault("search", []).extend(bench(q, lambda: se.search(q, 32.08, 34.78)))
    con = ds.conn()
    for pid in range(1, 2000, 50):
        report.setdefault("poi_card", []).extend(bench("poi", lambda: poi_detail(con, pid)))
    for lat, lon in [(32.08, 34.78), (31.77, 35.21), (32.79, 34.99)]:
        report.setdefault("nearby", []).extend(bench("nearby", lambda: nearby(con, lat, lon, "food", limit=50)))
        report.setdefault("reverse", []).extend(bench("reverse", lambda: reverse(con, lat, lon)))
        for z in (10, 13, 15, 17):
            x, y = geo.lonlat_to_tile(lon, lat, z)
            report.setdefault("poi_tile", []).extend(bench("tile", lambda: tiles.poi_tile(con, z, x, y)))
            report.setdefault("poi_tile_filtered", []).extend(bench("tile", lambda: tiles.poi_tile(con, z, x, y, ["food"])))
            report.setdefault("fallback_tile", []).extend(bench("tile", lambda: tiles.fallback_tile(con, z, x, y)))
    print(f"{'operation':<20}{'n':>6}{'p50 ms':>10}{'p95 ms':>10}{'max ms':>10}")
    for k, v in report.items():
        v = sorted(v)
        p95 = v[int(len(v) * 0.95) - 1] if len(v) > 1 else v[0]
        print(f"{k:<20}{len(v):>6}{statistics.median(v):>10.1f}{p95:>10.1f}{max(v):>10.1f}")


if __name__ == "__main__":
    main()
