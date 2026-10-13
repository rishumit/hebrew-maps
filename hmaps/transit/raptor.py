"""RAPTOR (Delling, Pajor & Werneck 2012) – vectorised with numpy.

A query first cuts a *window* out of the timetable: every trip instance
(trip × service day, so yesterday's after-midnight trips and tomorrow's early
ones are included with a ±24h offset) that overlaps the time range of
interest. The window is laid out column-major per pattern:

    element e = ebase[pattern] + stop_index * n_trips(pattern) + trip_rank

so the departures of one pattern at one stop form a sorted run. Each run is
offset by ``position * BIG`` into one global key array, which lets one
``np.searchsorted`` call find "the earliest trip I can catch" at every
pattern-stop position at once. "Once on board, stay on the earliest trip"
becomes a segmented exclusive running minimum (``np.minimum.accumulate``
with per-pattern offsets), so a whole RAPTOR round is a handful of numpy
calls instead of a Python loop over ~250k pattern-stop positions.

Arrive-by queries run the same code on the *reversed* window (times negated,
stop order and trip order flipped), which turns "latest departure" into
"earliest arrival".
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

INF = np.int64(1) << 40
BIG = np.int64(1) << 23      # key stride per position (8.4M s ≈ 97 days)
OFF = np.int64(1) << 21      # time shift so keys stay positive (times ≥ −24 days)
ACCESS = -2


class Window:
    """Trip instances (trip, day offset) overlapping [t_lo, t_hi] (seconds from the query day's midnight)."""

    def __init__(self, data, day_k: int, t_lo: int, t_hi: int):
        trips, offs = [], []
        for dd in (-1, 0, 1):
            act = data.active(day_k + dd)
            if act is None:
                continue
            off = dd * 86400
            m = act & (data.trip_start <= t_hi - off) & (data.trip_end >= t_lo - off)
            idx = np.flatnonzero(m)
            trips.append(idx.astype(np.int32))
            offs.append(np.full(len(idx), off, dtype=np.int32))
        trip = np.concatenate(trips) if trips else np.zeros(0, np.int32)
        off = np.concatenate(offs) if offs else np.zeros(0, np.int32)
        pat = data.trip_pat[trip]
        o = np.lexsort((data.trip_start[trip] + off, pat))
        self.trip, self.off = trip[o], off[o]
        pat = pat[o]
        self.upat, self.inst_first, self.m = np.unique(pat, return_index=True, return_counts=True)
        self.data = data
        self.t_lo, self.t_hi = t_lo, t_hi

    def __len__(self) -> int:
        return len(self.trip)


class Table:
    """Pattern-stop positions + column-major departure/arrival runs, in one search direction."""

    def __init__(self, *, pos_stop, board_ok, alight_ok, pos_m, pos_wf, pos_i, seg_first, dep, arr, reversed_):
        self.pos_stop, self.board_ok, self.alight_ok = pos_stop, board_ok, alight_ok
        self.pos_m = pos_m.astype(np.int64)
        self.pos_wf, self.pos_i, self.seg_first = pos_wf, pos_i, seg_first
        self.arr = arr
        self.reversed = reversed_
        R = len(pos_stop)
        self.R = R
        self.col_start = np.zeros(R, dtype=np.int64)
        if R:
            self.col_start[1:] = np.cumsum(self.pos_m)[:-1]
        seg_id = np.cumsum(seg_first) - 1
        firsts = np.flatnonzero(seg_first)
        self.seg_start = firsts[seg_id] if R else np.zeros(0, np.int64)
        nseg = len(firsts)
        bigt = np.int64(int(self.pos_m.max(initial=0)) + 2)
        self.segoff = (nseg - 1 - seg_id).astype(np.int64) * bigt
        self.sentinel_first = (self.segoff + self.pos_m)[seg_first]
        e_pos = np.repeat(np.arange(R, dtype=np.int64), self.pos_m)
        key = e_pos * BIG + (dep.astype(np.int64) + OFF)
        # suffix minimum per run: guarantees "found trip departs ≥ query" even if trips overtake
        self.key = np.minimum.accumulate(key[::-1])[::-1]
        # stop → positions (only where boarding is allowed)
        bpos = np.flatnonzero(board_ok)
        o = np.argsort(pos_stop[bpos], kind="stable")
        self.sp_pos = bpos[o]
        self.sp_stop = pos_stop[bpos][o]

    def positions_of(self, stops: np.ndarray) -> np.ndarray:
        lo = np.searchsorted(self.sp_stop, stops, side="left")
        hi = np.searchsorted(self.sp_stop, stops, side="right")
        c = hi - lo
        tot = int(c.sum())
        if not tot:
            return np.zeros(0, np.int64)
        return self.sp_pos[np.repeat(lo, c) + np.arange(tot) - np.repeat(np.cumsum(c) - c, c)]

    def local_trip_forward(self, r: int, t: int) -> int:
        return int(self.pos_m[r]) - 1 - t if self.reversed else t


def tables(win: Window) -> tuple[Table, callable]:
    """Forward table for *win*, and a lazy builder for the reversed one."""
    d = win.data
    W = len(win.upat)
    L = d.pat_len[win.upat]
    R = int(L.sum())
    seg_start_w = np.cumsum(L) - L
    pos_wf = np.repeat(np.arange(W, dtype=np.int32), L)
    pos_i = (np.arange(R) - np.repeat(seg_start_w, L)).astype(np.int32)
    gpos = d.pat_ptr[win.upat][pos_wf] + pos_i
    pos_stop = d.pat_stops[gpos]
    fl = d.pat_flags[gpos]
    board_ok, alight_ok = (fl & 1) == 0, (fl & 2) == 0
    pos_m = win.m[pos_wf].astype(np.int64)
    col_start = np.zeros(R, dtype=np.int64)
    if R:
        col_start[1:] = np.cumsum(pos_m)[:-1]
    E = int(pos_m.sum())
    e_pos = np.repeat(np.arange(R, dtype=np.int32), pos_m)
    ig = (win.inst_first[pos_wf[e_pos]] + (np.arange(E, dtype=np.int64) - col_start[e_pos])).astype(np.int64)
    trip = win.trip[ig]
    pidx = d.prof_ptr[d.trip_prof[trip]] + pos_i[e_pos]
    base = d.trip_start[trip] + win.off[ig]
    del ig, trip, e_pos
    dep = (base + d.prof_dep[pidx]).astype(np.int64)
    arr = (base + d.prof_arr[pidx]).astype(np.int64)
    del base, pidx
    seg_first = pos_i == 0
    fwd = Table(pos_stop=pos_stop, board_ok=board_ok, alight_ok=alight_ok, pos_m=pos_m, pos_wf=pos_wf, pos_i=pos_i,
                seg_first=seg_first, dep=dep, arr=arr, reversed_=False)

    def reverse() -> Table:
        last = np.zeros(R, dtype=bool)
        if R:
            last[np.cumsum(L) - 1] = True
        return Table(pos_stop=pos_stop[::-1].copy(), board_ok=alight_ok[::-1].copy(), alight_ok=board_ok[::-1].copy(),
                     pos_m=pos_m[::-1].copy(), pos_wf=pos_wf[::-1].copy(), pos_i=pos_i[::-1].copy(),
                     seg_first=last[::-1].copy(), dep=-arr[::-1], arr=-dep[::-1], reversed_=True)

    return fwd, reverse


@dataclass
class Round:
    B: np.ndarray       # earliest time one can board at each stop (after transfers)
    Bfrom: np.ndarray   # stop whose vehicle arrival produced B (itself = same-stop change), ACCESS, or -1
    V: np.ndarray       # earliest vehicle arrival at each stop with ≤ k rides
    Vpos: np.ndarray    # alighting position of that ride
    Vtrip: np.ndarray   # local trip rank in the position's run
    Vround: np.ndarray  # round in which V was set
    idx: np.ndarray | None  # earliest catchable trip per position in this round (for reconstruction)


@dataclass
class Result:
    rounds: list
    dest: list          # per round k: (arrival, egress stop) or None


def run(T: Table, n_stops: int, acc_s, acc_t, egr_s, egr_t, t0: int, transfers, *, max_rides: int = 5,
        change: int = 60, walk_factor: float = 1.0, limit: int | None = None) -> Result:
    """Earliest-arrival RAPTOR from the access stops (boardable at t0 + acc_t) to the egress stops."""
    tr_ptr, tr_to, tr_sec = transfers
    acc_s = np.asarray(acc_s, dtype=np.int64)
    egr_s = np.asarray(egr_s, dtype=np.int64)
    egr_t = np.asarray(egr_t, dtype=np.int64)
    B = np.full(n_stops, INF, dtype=np.int64)
    Bfrom = np.full(n_stops, -1, dtype=np.int32)
    B[acc_s] = t0 + np.asarray(acc_t, dtype=np.int64)
    Bfrom[acc_s] = ACCESS
    V = np.full(n_stops, INF, dtype=np.int64)
    Vpos = np.full(n_stops, -1, dtype=np.int64)
    Vtrip = np.full(n_stops, -1, dtype=np.int64)
    Vround = np.zeros(n_stops, dtype=np.int8)
    rounds = [Round(B, Bfrom, V, Vpos, Vtrip, Vround, None)]
    dest = [None]
    best_dest = INF if limit is None else np.int64(limit)
    marked = acc_s
    lo, hi = -OFF, BIG - OFF - 1
    for k in range(1, max_rides + 1):
        pos = T.positions_of(marked)
        idx = T.pos_m.copy()
        if len(pos):
            q = np.clip(B[T.pos_stop[pos]], lo, hi)
            found = np.searchsorted(T.key, pos.astype(np.int64) * BIG + q + OFF)
            idx[pos] = found - T.col_start[pos]
        v = idx + T.segoff
        vs = np.empty_like(v)
        vs[1:] = v[:-1]
        vs[T.seg_first] = T.sentinel_first
        cm = np.minimum.accumulate(vs) - T.segoff
        r = np.flatnonzero((cm < T.pos_m) & T.alight_ok)
        a = T.arr[T.col_start[r] + cm[r]]
        s = T.pos_stop[r]
        good = a < np.minimum(V[s], best_dest)
        r, a, s = r[good], a[good], s[good]
        if not len(r):
            dest.append(dest[-1])
            break
        Vn = np.full(n_stops, INF, dtype=np.int64)
        np.minimum.at(Vn, s, a)
        win = a == Vn[s]
        uniq, first = np.unique(s[win], return_index=True)
        rw = r[win][first]
        V, Vpos, Vtrip, Vround = V.copy(), Vpos.copy(), Vtrip.copy(), Vround.copy()
        V[uniq] = Vn[uniq]
        Vpos[uniq] = rw
        Vtrip[uniq] = cm[rw]
        Vround[uniq] = k
        # destination
        if len(egr_s):
            cand = V[egr_s] + egr_t
            j = int(np.argmin(cand))
            if cand[j] < best_dest:
                best_dest = cand[j]
                dest.append((int(cand[j]), int(egr_s[j])))
            else:
                dest.append(dest[-1])
        else:
            dest.append(None)
        # transfers: same stop (+ change time) and foot paths
        Bn, Bfn = B.copy(), Bfrom.copy()
        cs = Vn[uniq] + change
        better = cs < Bn[uniq]
        Bn[uniq[better]] = cs[better]
        Bfn[uniq[better]] = uniq[better]
        deg = tr_ptr[uniq + 1] - tr_ptr[uniq]
        tot = int(deg.sum())
        if tot:
            src = np.repeat(uniq, deg)
            eidx = np.repeat(tr_ptr[uniq], deg) + np.arange(tot) - np.repeat(np.cumsum(deg) - deg, deg)
            dst = tr_to[eidx].astype(np.int64)
            tt = Vn[src] + np.ceil(tr_sec[eidx] * walk_factor).astype(np.int64) + change
            Wt = np.full(n_stops, INF, dtype=np.int64)
            np.minimum.at(Wt, dst, tt)
            imp = np.flatnonzero(Wt < Bn)
            if len(imp):
                w2 = (tt == Wt[dst]) & (Wt[dst] < Bn[dst])
                du, df = np.unique(dst[w2], return_index=True)
                Bn[du] = Wt[du]
                Bfn[du] = src[w2][df]
        marked = np.flatnonzero(Bn < B)
        B, Bfrom = Bn, Bfn
        rounds.append(Round(B, Bfrom, V, Vpos, Vtrip, Vround, idx))
        if not len(marked):
            break
    return Result(rounds, dest)


def journey(T: Table, res: Result, k: int, egress_stop: int) -> list[tuple]:
    """Legs in search order: ('access', stop) ('ride', board_pos, alight_pos, trip_rank) ('walk', a, b) ('egress', stop)."""
    legs: list[tuple] = [("egress", egress_stop)]
    s, r = egress_stop, k
    for _ in range(64):
        rd = res.rounds[r]
        rr = int(rd.Vround[s])
        j = int(rd.Vpos[s])
        t = int(rd.Vtrip[s])
        idx = res.rounds[rr].idx
        seg0 = int(T.seg_start[j])
        cand = np.flatnonzero(idx[seg0:j] == t)
        i = seg0 + int(cand[-1])
        legs.append(("ride", i, j, t))
        b = int(T.pos_stop[i])
        f = int(res.rounds[rr - 1].Bfrom[b])
        if f == ACCESS:
            legs.append(("access", b))
            break
        if f != b:
            legs.append(("walk", f, b))
        s, r = f, rr - 1
    legs.reverse()
    return legs
