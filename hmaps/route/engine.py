"""Built-in A* router over ``graph.npz``.

Produces OSRM-compatible responses (routes → legs → steps with maneuvers),
so the Hebrew instruction generator in :mod:`hmaps.api.routing` is shared by
both routing back-ends.
"""

from __future__ import annotations

import heapq
import logging
import math
import threading
from array import array
from pathlib import Path

import numpy as np

from .graph import MAX_SPEED_MS, PROFILES

log = logging.getLogger(__name__)

PROFILE_ALIASES = {"car": "car", "driving": "car", "foot": "foot", "walking": "foot", "bike": "bike", "cycling": "bike"}
CELL = 0.004  # ~400 m grid for snapping
MAX_SNAP_M = 1500.0
MAX_SETTLED = 2_500_000


class NoRoute(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _bearing(lon1, lat1, lon2, lat2) -> float:
    y = math.sin(math.radians(lon2 - lon1)) * math.cos(math.radians(lat2))
    x = (math.cos(math.radians(lat1)) * math.sin(math.radians(lat2))
         - math.sin(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.cos(math.radians(lon2 - lon1)))
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def _hav(lon1, lat1, lon2, lat2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 12_742_017.6 * math.asin(min(1.0, math.sqrt(a)))


def _modifier(delta: float) -> str:
    a = abs(delta)
    side = "right" if delta > 0 else "left"
    if a < 25:
        return "straight"
    if a < 55:
        return f"slight {side}"
    if a < 135:
        return side
    if a < 170:
        return f"sharp {side}"
    return "uturn"


class _Profile:
    """CSR adjacency for one profile (compact arrays, fast scalar access)."""

    def __init__(self, g: dict, name: str):
        u, v = g["u"], g["v"]
        cf, cb = g[f"cost_{name}_f"], g[f"cost_{name}_b"]
        okf, okb = np.isfinite(cf), np.isfinite(cb)
        src = np.concatenate([u[okf], v[okb]])
        dst = np.concatenate([v[okf], u[okb]])
        edge = np.concatenate([np.nonzero(okf)[0], np.nonzero(okb)[0]]).astype(np.int32)
        fwd = np.concatenate([np.ones(okf.sum(), np.int8), np.zeros(okb.sum(), np.int8)])
        cost = np.concatenate([cf[okf], cb[okb]]).astype(np.float64)
        order = np.argsort(src, kind="stable")
        n = len(g["vlon"])
        indptr = np.zeros(n + 1, dtype=np.int64)
        np.add.at(indptr, src + 1, 1)
        indptr = np.cumsum(indptr)
        self.indptr = array("q", indptr.tobytes())
        self.dst = array("i", dst[order].astype(np.int32).tobytes())
        self.edge = array("i", edge[order].tobytes())
        self.fwd = array("b", fwd[order].tobytes())
        self.cost = array("d", cost[order].tobytes())
        self.edge_ok_f = okf
        self.edge_ok_b = okb
        self.cf, self.cb = cf, cb
        self.max_speed = MAX_SPEED_MS[name]


class Router:
    def __init__(self, path: Path):
        self.path = path
        self.mtime = path.stat().st_mtime_ns
        with np.load(path) as z:
            g = {k: z[k] for k in z.files}
        self.g = g
        self.names = bytes(g.pop("names")).decode("utf-8").split("\n")
        self.vlon = array("d", g["vlon"].tobytes())
        self.vlat = array("d", g["vlat"].tobytes())
        self.profiles: dict[str, _Profile] = {}
        self._lock = threading.Lock()
        self._grid = None
        # vertex degree (all profiles) – used to decide whether a junction is a real choice
        n = len(g["vlon"])
        deg = np.zeros(n, dtype=np.int32)
        np.add.at(deg, g["u"], 1)
        np.add.at(deg, g["v"], 1)
        self.degree = deg
        log.info("router loaded: %d vertices, %d edges", n, len(g["u"]))

    # ------------------------------------------------------------------ setup
    def profile(self, name: str) -> _Profile:
        if name not in self.profiles:
            with self._lock:
                if name not in self.profiles:
                    self.profiles[name] = _Profile(self.g, name)
        return self.profiles[name]

    def _snap_index(self):
        if self._grid is None:
            with self._lock:
                if self._grid is None:
                    pts = self.g["geom"].astype(np.float64)
                    gs = self.g["geom_start"]
                    # edge index of every geometry point
                    edge_of = np.repeat(np.arange(len(gs) - 1, dtype=np.int32), np.diff(gs))
                    cx = np.floor(pts[:, 0] / CELL).astype(np.int64)
                    cy = np.floor(pts[:, 1] / CELL).astype(np.int64)
                    key = cx * 1_000_003 + cy
                    order = np.argsort(key, kind="stable")
                    self._grid = (key[order], order, pts, edge_of)
        return self._grid

    def snap(self, lat: float, lon: float, prof: _Profile) -> tuple[int, float, int]:
        """Nearest usable edge (by true point-to-segment distance): (edge, fraction, 0)."""
        keys, order, pts, edge_of = self._snap_index()
        gs = self.g["geom_start"]
        cx, cy = math.floor(lon / CELL), math.floor(lat / CELL)
        kx = math.cos(math.radians(lat))
        for ring in (2, 4):
            cand = []
            for dx in range(-ring, ring + 1):
                for dy in range(-ring, ring + 1):
                    k = (cx + dx) * 1_000_003 + (cy + dy)
                    lo, hi = np.searchsorted(keys, k), np.searchsorted(keys, k, side="right")
                    if hi > lo:
                        cand.append(order[lo:hi])
            if not cand:
                continue
            idx = np.concatenate(cand)
            e = edge_of[idx]
            usable = prof.edge_ok_f[e] | prof.edge_ok_b[e]
            idx = idx[usable]
            if not len(idx):
                continue
            # candidate edges: those owning the 40 nearest geometry points
            d2 = ((pts[idx, 0] - lon) * kx) ** 2 + (pts[idx, 1] - lat) ** 2
            near = idx[np.argsort(d2)[:40]]
            best = (math.inf, -1, 0.0)
            for e in dict.fromkeys(edge_of[near].tolist()):
                a, b = int(gs[e]), int(gs[e + 1])
                seg = pts[a:b]
                x = (seg[:, 0] - lon) * kx
                y = seg[:, 1] - lat
                x0, y0, x1, y1 = x[:-1], y[:-1], x[1:], y[1:]
                dx, dy = x1 - x0, y1 - y0
                L = dx * dx + dy * dy
                t = np.clip(np.where(L > 0, -(x0 * dx + y0 * dy) / np.where(L > 0, L, 1), 0), 0, 1)
                px, py = x0 + t * dx, y0 + t * dy
                dd = px * px + py * py
                i = int(np.argmin(dd))
                if dd[i] < best[0]:
                    seglen = np.sqrt(L)
                    cum = float(seglen[:i].sum() + seglen[i] * t[i])
                    total = float(seglen.sum())
                    best = (float(dd[i]), e, cum / total if total > 0 else 0.0)
            if best[1] >= 0:
                dist_m = math.sqrt(best[0]) * 111_195.0
                if dist_m > MAX_SNAP_M:
                    break
                return best[1], best[2], 0
        raise NoRoute("NoSegment")

    # ------------------------------------------------------------------ search
    def route(self, profile: str, points: list[tuple[float, float]]) -> dict:
        """OSRM-style response for (lat, lon) points (origin, [via...], destination)."""
        name = PROFILE_ALIASES.get(profile, profile)
        if name not in PROFILES:
            raise ValueError(profile)
        prof = self.profile(name)
        legs, coords, total_d, total_t = [], [], 0.0, 0.0
        for (a, b) in zip(points, points[1:]):
            leg = self._leg(prof, a, b)
            legs.append(leg)
            if coords and leg["coords"]:
                leg["coords"] = leg["coords"][1:]
            coords += leg.pop("coords")
            total_d += leg["distance"]
            total_t += leg["duration"]
        return {"code": "Ok", "routes": [{"distance": total_d, "duration": total_t,
                                           "geometry": {"type": "LineString", "coordinates": coords}, "legs": legs}]}

    def _leg(self, prof: _Profile, a: tuple[float, float], b: tuple[float, float]) -> dict:
        g = self.g
        e1, f1, _ = self.snap(a[0], a[1], prof)
        e2, f2, _ = self.snap(b[0], b[1], prof)
        u, v = g["u"], g["v"]
        cf, cb = prof.cf, prof.cb
        # same edge, reachable directly along it
        if e1 == e2 and ((f2 >= f1 and np.isfinite(cf[e1])) or (f2 <= f1 and np.isfinite(cb[e1]))):
            fwd = f2 >= f1
            cost = abs(f2 - f1) * float(cf[e1] if fwd else cb[e1])
            path = [(e1, fwd, f1, f2)]
            return self._describe(prof, path, cost, a, b)

        heads: dict[int, tuple[float, tuple]] = {}
        # sources: leave the start edge towards either end
        starts: list[tuple[float, int, tuple]] = []
        if np.isfinite(cf[e1]):
            starts.append(((1 - f1) * float(cf[e1]), int(v[e1]), (e1, True, f1, 1.0)))
        if np.isfinite(cb[e1]):
            starts.append((f1 * float(cb[e1]), int(u[e1]), (e1, False, f1, 0.0)))
        # targets: arrive at an end of the target edge, then go along it
        targets: dict[int, tuple[float, tuple]] = {}
        if np.isfinite(cf[e2]):
            targets[int(u[e2])] = (f2 * float(cf[e2]), (e2, True, 0.0, f2))
        if np.isfinite(cb[e2]):
            tv = int(v[e2])
            c = (1 - f2) * float(cb[e2])
            if tv not in targets or c < targets[tv][0]:
                targets[tv] = (c, (e2, False, 1.0, f2))
        if not starts or not targets:
            raise NoRoute("NoRoute")

        tlon, tlat = b[1], b[0]
        kx = math.cos(math.radians(tlat))
        inv_speed = 1.0 / prof.max_speed
        vlon, vlat = self.vlon, self.vlat
        indptr, dst, edge, fwdf, cost = prof.indptr, prof.dst, prof.edge, prof.fwd, prof.cost

        def h(n: int) -> float:
            dx = (vlon[n] - tlon) * kx
            dy = vlat[n] - tlat
            return math.sqrt(dx * dx + dy * dy) * 111_195.0 * inv_speed

        dist: dict[int, float] = {}
        prev: dict[int, tuple[int, int, bool]] = {}
        heap: list[tuple[float, float, int]] = []
        first_leg: dict[int, tuple] = {}
        for c, n, part in starts:
            if c < dist.get(n, math.inf):
                dist[n] = c
                first_leg[n] = part
                heapq.heappush(heap, (c + h(n), c, n))
        best_total, best_node = math.inf, -1
        settled = 0
        while heap:
            fcost, d, n = heapq.heappop(heap)
            if d > dist.get(n, math.inf):
                continue
            if fcost >= best_total:
                break
            if n in targets:
                tot = d + targets[n][0]
                if tot < best_total:
                    best_total, best_node = tot, n
            settled += 1
            if settled > MAX_SETTLED:
                break
            for i in range(indptr[n], indptr[n + 1]):
                m = dst[i]
                nd = d + cost[i]
                if nd < dist.get(m, math.inf):
                    dist[m] = nd
                    prev[m] = (n, edge[i], fwdf[i] == 1)
                    heapq.heappush(heap, (nd + h(m), nd, m))
        if best_node < 0:
            raise NoRoute("NoRoute")
        # reconstruct
        chain = []
        n = best_node
        while n in prev:
            p, e, fwd = prev[n]
            chain.append((e, fwd, 0.0 if fwd else 1.0, 1.0 if fwd else 0.0))
            n = p
        chain.reverse()
        path = [first_leg[n], *chain, targets[best_node][1]]
        return self._describe(prof, path, best_total, a, b)

    # ------------------------------------------------------------------ description
    def _edge_coords(self, e: int, fwd: bool, fa: float, fb: float) -> list[list[float]]:
        g = self.g
        gs = g["geom_start"]
        pts = g["geom"][int(gs[e]):int(gs[e + 1])].astype(np.float64)
        if len(pts) < 2:
            return pts.tolist()
        seg = np.hypot(np.diff(pts[:, 0]) * 0.85, np.diff(pts[:, 1]))
        cum = np.concatenate([[0.0], np.cumsum(seg)])
        total = cum[-1] or 1.0

        def at(f: float) -> list[float]:
            t = f * total
            i = int(np.searchsorted(cum, t, side="right") - 1)
            i = max(0, min(i, len(pts) - 2))
            r = (t - cum[i]) / (cum[i + 1] - cum[i] or 1.0)
            return [float(pts[i, 0] + (pts[i + 1, 0] - pts[i, 0]) * r), float(pts[i, 1] + (pts[i + 1, 1] - pts[i, 1]) * r)]

        lo, hi = min(fa, fb), max(fa, fb)
        inner = [p.tolist() for p, c in zip(pts, cum) if lo * total < c < hi * total]
        out = [at(lo), *inner, at(hi)]
        return out if fwd else out[::-1]

    def _describe(self, prof: _Profile, path: list[tuple], total_cost: float, a, b) -> dict:
        g = self.g
        lengths = g["length"]
        pieces = []
        for e, fwd, fa, fb in path:
            frac = abs(fb - fa)
            if frac <= 1e-9 and len(path) > 1:
                continue
            coords = self._edge_coords(e, fwd, fa, fb)
            c = float(prof.cf[e] if fwd else prof.cb[e]) * frac
            pieces.append({"e": e, "fwd": fwd, "coords": coords, "dist": float(lengths[e]) * frac, "time": c,
                           "name": self.names[int(g["name"][e])], "ref": self.names[int(g["ref"][e])],
                           "round": bool(g["roundabout"][e]),
                           "end_vertex": int(g["v"][e] if fwd else g["u"][e]) if (fb in (0.0, 1.0)) else -1})
        if not pieces:
            raise NoRoute("NoRoute")
        # a point exactly on a junction may snap onto a crossing street: ignore such slivers
        while len(pieces) > 1 and pieces[-1]["dist"] < 3:
            tail = pieces.pop()
            pieces[-1]["time"] += tail["time"]
        while len(pieces) > 1 and pieces[0]["dist"] < 3:
            head = pieces.pop(0)
            pieces[0]["coords"] = [head["coords"][0], *pieces[0]["coords"]]

        def bearing_start(p):
            c = p["coords"]
            return _bearing(c[0][0], c[0][1], c[min(1, len(c) - 1)][0], c[min(1, len(c) - 1)][1]) if len(c) > 1 else 0.0

        def bearing_end(p):
            c = p["coords"]
            return _bearing(c[-2][0], c[-2][1], c[-1][0], c[-1][1]) if len(c) > 1 else 0.0

        steps = []
        first = pieces[0]
        cur = {"maneuver": {"type": "depart", "location": first["coords"][0], "bearing_after": round(bearing_start(first))},
               "name": first["name"], "ref": first["ref"], "distance": 0.0, "duration": 0.0}
        in_round = False
        exits = 0
        for i, p in enumerate(pieces):
            if i > 0:
                prevp = pieces[i - 1]
                vtx = prevp["end_vertex"]
                delta = (bearing_start(p) - bearing_end(prevp) + 540) % 360 - 180
                choice = vtx >= 0 and self.degree[vtx] > 2
                new = None
                if p["round"] and not prevp["round"]:
                    in_round, exits = True, 0
                    steps.append(cur)
                    cur = {"maneuver": {"type": "roundabout", "location": p["coords"][0], "modifier": _modifier(delta)},
                           "name": "", "ref": "", "distance": 0.0, "duration": 0.0}
                elif prevp["round"] and p["round"]:
                    if choice:
                        exits += 1
                elif prevp["round"] and not p["round"]:
                    in_round = False
                    cur["maneuver"]["exit"] = exits + 1
                    cur["name"], cur["ref"] = p["name"], p["ref"]
                elif p["name"] != prevp["name"] and (p["name"] or abs(delta) >= 25):
                    new = "turn" if abs(delta) >= 25 else "new name"
                elif choice and abs(delta) >= 40:
                    new = "turn"
                if new:
                    steps.append(cur)
                    cur = {"maneuver": {"type": new, "modifier": _modifier(delta), "location": p["coords"][0]},
                           "name": p["name"], "ref": p["ref"], "distance": 0.0, "duration": 0.0}
            cur["distance"] += p["dist"]
            cur["duration"] += p["time"]
        steps.append(cur)
        last = pieces[-1]["coords"]
        side = None
        if len(last) >= 2:
            dest = (b[1], b[0])
            ax, ay = last[-2]
            bx, by = last[-1]
            cross = (bx - ax) * (dest[1] - ay) - (by - ay) * (dest[0] - ax)
            if _hav(bx, by, dest[0], dest[1]) > 3:
                side = "left" if cross > 0 else "right"
        arrive = {"maneuver": {"type": "arrive", "location": last[-1]}, "name": "", "ref": "", "distance": 0.0, "duration": 0.0}
        if side:
            arrive["maneuver"]["modifier"] = side
        steps.append(arrive)
        coords: list[list[float]] = []
        for p in pieces:
            c = p["coords"]
            coords += c[1:] if coords else c
        dist = sum(p["dist"] for p in pieces)
        return {"distance": round(dist, 1), "duration": round(total_cost, 1),
                "steps": [{**s, "distance": round(s["distance"], 1), "duration": round(s["duration"], 1)} for s in steps],
                "coords": [[round(x, 6), round(y, 6)] for x, y in coords]}


_ROUTERS: dict[str, Router] = {}
_LOCK = threading.Lock()


def get_router(path: Path) -> Router | None:
    """Cached router, reloaded when the graph file changes."""
    if not path.exists():
        return None
    key = str(path)
    r = _ROUTERS.get(key)
    if r is None or r.mtime != path.stat().st_mtime_ns:
        with _LOCK:
            r = _ROUTERS.get(key)
            if r is None or r.mtime != path.stat().st_mtime_ns:
                r = Router(path)
                _ROUTERS[key] = r
    return r
