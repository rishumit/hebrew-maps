"""POI declutter (Google-Maps-like): thinned through z17, zoom-stable, edge-consistent."""

import random
import sqlite3
import unittest

from hmaps.api import tiles


def _db(n=4000, seed=3, box=(34.770, 32.070, 34.780, 32.080)):
    """A dense synthetic city centre: ``n`` places in ~1 km² (a busy market area)."""
    rnd = random.Random(seed)
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE pois (id INTEGER PRIMARY KEY, sub TEXT, lat REAL, lon REAL, importance REAL)")
    con.executemany("INSERT INTO pois VALUES (?,?,?,?,?)",
                    [(i, "restaurant", rnd.uniform(box[1], box[3]), rnd.uniform(box[0], box[2]),
                      round(rnd.uniform(0.0, 0.8), 3)) for i in range(1, n + 1)])
    return con


class DeclutterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.con = _db()
        cls.zooms = tiles.compute_poi_zooms(cls.con)

    def visible(self, z):
        return sum(1 for v in self.zooms.values() if v <= z)

    def test_spacing_shrinks_with_zoom_and_stops_after_last_thinned(self):
        last = tiles._LAST_THINNED_ZOOM
        sp = [tiles.poi_spacing_px(z) for z in range(8, last + 1)]
        self.assertEqual(sp, sorted(sp, reverse=True))
        self.assertGreater(tiles.poi_spacing_px(last), 0)  # z17 is still thinned
        self.assertEqual(tiles.poi_spacing_px(last + 1), 0)

    def test_zoom_17_is_thinned_not_everything(self):
        """The old version drew every POI from z17 on; now z17 is thinned too."""
        z17 = self.visible(17)
        total = self.con.execute("SELECT COUNT(*) FROM pois").fetchone()[0]
        self.assertLess(z17, total * 0.8)
        self.assertGreater(z17, self.visible(16))

    def test_even_on_screen_density(self):
        """A dense area keeps roughly the same number of icons per screen at every zoom
        (no sudden carpet of icons at some zoom - the original complaint)."""
        import math
        for z in range(14, tiles._LAST_THINNED_ZOOM + 2):
            n = sum(1 for pid in range(1, 4001) if self.zooms.get(pid, tiles._LAST_THINNED_ZOOM + 1) <= z)
            box_px = 990 * 2 ** (z - 16)  # the ~1 km test box, in screen px at zoom z (lat 32)
            per = box_px / math.sqrt(n)
            self.assertGreater(per, 38, f"too dense at z{z}: 1 icon per {per:.0f}px")

    def test_minimum_spacing_respected(self):
        import math
        z = 15
        world = 512 * (1 << z)
        pts = []
        for pid, lat, lon in self.con.execute("SELECT id, lat, lon FROM pois"):
            if self.zooms.get(pid, 99) <= z:
                s = math.sin(math.radians(lat))
                pts.append(((lon + 180) / 360 * world, (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * world))
        sp = tiles.poi_spacing_px(z)
        for i, (ax, ay) in enumerate(pts):
            for bx, by in pts[i + 1:]:
                self.assertGreaterEqual((ax - bx) ** 2 + (ay - by) ** 2, (sp - 1) ** 2)


if __name__ == "__main__":
    unittest.main()
