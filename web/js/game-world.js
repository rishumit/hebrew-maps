// Game mode – the road world: reads the roads of the basemap (OpenMapTiles "transportation") into a
// local metric frame and derives everything the game needs from them: speed limits, junctions with
// traffic lights, roundabouts, and where the street furniture (lamps, trees, signs) stands.
// Pure logic, no DOM / WebGL: tests/test_game_world.mjs runs it under Node.

/**
 * Drivable road classes. hw = half width of the carriageway (m); limit / urban = legal speed (km/h)
 * outside / inside a built-up area, by the Israeli traffic regulations (תקנה 54): 50 in town,
 * 80 out of town, 90 with a median, 110 on a freeway.
 */
export const ROAD = {
  motorway: { hw: 7.5, limit: 110, urban: 90, rank: 6 },
  trunk: { hw: 6.5, limit: 90, urban: 70, rank: 5 },
  primary: { hw: 5.6, limit: 80, urban: 50, rank: 4 },
  secondary: { hw: 5.0, limit: 80, urban: 50, rank: 3 },
  tertiary: { hw: 4.4, limit: 80, urban: 50, rank: 2 },
  minor: { hw: 3.6, limit: 80, urban: 50, rank: 1 },
  service: { hw: 2.6, limit: 30, urban: 30, rank: 0 },
};
export const LIMIT_ROUNDABOUT = 30;
export const LIMIT_RAMP = 60;
export const SIGNAL_CYCLE = 26; // seconds

const CELL = 60;
const cellKey = (cx, cy) => (cx + 50000) * 100000 + (cy + 50000);

/** Deterministic pseudo-random in [0,1) from a position – the same tree stands in the same place every time. */
export function hash01(a, b, salt = 0) {
  let h = Math.imul(Math.round(a * 4) ^ 0x9E3779B9, 0x85EBCA6B) ^ Math.imul(Math.round(b * 4) + salt * 7919, 0xC2B2AE35);
  h ^= h >>> 15;
  h = Math.imul(h, 0x2C1B3C6D);
  h ^= h >>> 12;
  h = Math.imul(h, 0x297A2D39);
  h ^= h >>> 15;
  return (h >>> 0) / 4294967296;
}

/** Light of one group of a signal at time t: 'g' | 'y' | 'r'. */
export function signalState(sig, group, t) {
  const ph = (((t + sig.offset) % SIGNAL_CYCLE) + SIGNAL_CYCLE) % SIGNAL_CYCLE;
  if (group === 0) return ph < 10 ? 'g' : ph < 12.5 ? 'y' : 'r';
  return ph < 13 ? 'r' : ph < 23 ? 'g' : ph < 25.5 ? 'y' : 'r';
}

/** Least-squares circle through points (Kåsa). Returns null when the points are (almost) on a line. */
export function fitCircle(xs, ys) {
  const n = xs.length;
  if (n < 3) return null;
  let mx = 0;
  let my = 0;
  for (let i = 0; i < n; i++) { mx += xs[i]; my += ys[i]; }
  mx /= n;
  my /= n;
  let suu = 0, suv = 0, svv = 0, suuu = 0, svvv = 0, suvv = 0, svuu = 0;
  for (let i = 0; i < n; i++) {
    const u = xs[i] - mx;
    const v = ys[i] - my;
    suu += u * u; suv += u * v; svv += v * v;
    suuu += u * u * u; svvv += v * v * v; suvv += u * v * v; svuu += v * u * u;
  }
  const det = suu * svv - suv * suv;
  if (Math.abs(det) < 1e-6) return null;
  const a = 0.5 * (suuu + suvv);
  const b = 0.5 * (svvv + svuu);
  const uc = (a * svv - b * suv) / det;
  const vc = (b * suu - a * suv) / det;
  const r = Math.sqrt(uc * uc + vc * vc + (suu + svv) / n);
  const cx = uc + mx;
  const cy = vc + my;
  let maxRes = 0;
  let sweep = 0;
  let prev = 0;
  for (let i = 0; i < n; i++) {
    const dx = xs[i] - cx;
    const dy = ys[i] - cy;
    maxRes = Math.max(maxRes, Math.abs(Math.hypot(dx, dy) - r));
    const ang = Math.atan2(dy, dx);
    if (i) {
      let d = ang - prev;
      while (d > Math.PI) d -= 2 * Math.PI;
      while (d < -Math.PI) d += 2 * Math.PI;
      sweep += d;
    }
    prev = ang;
  }
  return { cx, cy, r, maxRes, sweep }; // sweep > 0: the points run counter-clockwise
}

export class World {
  constructor() {
    this.roads = [];
    this.grid = new Map();
    this.nodes = [];
    this.nodeGrid = new Map();
    this.segSeen = new Set();
    this.names = [];
    this.nameGrid = new Map();
    this.nameSeen = new Set();
    this.signals = new Map(); // key -> signal (the phase offset survives rebuilds)
    this.roundabouts = [];
    this.urbanCache = new Map();
  }

  // ------------------------------------------------------------------ ingest
  _node(x, y) {
    const cx = Math.floor(x);
    const cy = Math.floor(y);
    for (let dx = -1; dx <= 1; dx++) {
      for (let dy = -1; dy <= 1; dy++) {
        const list = this.nodeGrid.get(cellKey(cx + dx, cy + dy));
        if (!list) continue;
        for (const id of list) {
          const n = this.nodes[id];
          if (Math.abs(n.x - x) < 0.6 && Math.abs(n.y - y) < 0.6) return n;
        }
      }
    }
    const n = { id: this.nodes.length, x, y, arms: [] };
    this.nodes.push(n);
    const k = cellKey(cx, cy);
    const list = this.nodeGrid.get(k);
    if (list) list.push(n.id); else this.nodeGrid.set(k, [n.id]);
    return n;
  }

  /**
   * One road line (local metres). Tiles overlap and repeat each other, so every segment is taken
   * once only: a line is cut where it runs over segments that are already known.
   * @returns number of roads added
   */
  addLine(pts, props) {
    const spec = ROAD[props.class];
    if (!spec || pts.length < 2) return 0;
    let added = 0;
    let run = [];
    let prev = null;
    const flush = () => {
      if (run.length >= 2) { this._addRoad(run, props, spec); added++; }
      run = [];
    };
    for (const p of pts) {
      const n = this._node(p[0], p[1]);
      if (prev) {
        if (n === prev) continue;
        const k = prev.id < n.id ? prev.id * 4194304 + n.id : n.id * 4194304 + prev.id;
        if (this.segSeen.has(k)) flush();
        else {
          this.segSeen.add(k);
          if (!run.length) run.push(prev);
          run.push(n);
        }
      }
      prev = n;
    }
    flush();
    return added;
  }

  _addRoad(run, props, spec) {
    const n = run.length;
    const xs = new Float64Array(n);
    const ys = new Float64Array(n);
    const cum = new Float64Array(n);
    let minx = Infinity, miny = Infinity, maxx = -Infinity, maxy = -Infinity;
    for (let i = 0; i < n; i++) {
      xs[i] = run[i].x;
      ys[i] = run[i].y;
      if (i) cum[i] = cum[i - 1] + Math.hypot(xs[i] - xs[i - 1], ys[i] - ys[i - 1]);
      minx = Math.min(minx, xs[i]); maxx = Math.max(maxx, xs[i]);
      miny = Math.min(miny, ys[i]); maxy = Math.max(maxy, ys[i]);
    }
    const ow = Number(props.oneway) || 0;
    const road = {
      id: this.roads.length, cls: props.class, spec, xs, ys, cum, len: cum[n - 1], nodes: run.map((r) => r.id),
      oneway: ow === 1 ? 1 : ow === -1 ? -1 : 0, ramp: !!Number(props.ramp), tunnel: props.brunnel === 'tunnel',
      bridge: props.brunnel === 'bridge', rb: -1, minx, miny, maxx, maxy,
    };
    this.roads.push(road);
    for (let i = 0; i < n; i++) {
      const node = run[i];
      for (const step of [-1, 1]) {
        const j = i + step;
        if (j < 0 || j >= n) continue;
        const d = Math.hypot(xs[j] - xs[i], ys[j] - ys[i]) || 1;
        node.arms.push({ road: road.id, vi: i, step, dx: (xs[j] - xs[i]) / d, dy: (ys[j] - ys[i]) / d });
      }
      if (i < n - 1) this._gridSeg(this.grid, road.id, i, xs[i], ys[i], xs[i + 1], ys[i + 1]);
    }
    this.urbanCache.clear();
  }

  _gridSeg(grid, id, i, x0, y0, x1, y1) {
    const cx0 = Math.floor(Math.min(x0, x1) / CELL);
    const cx1 = Math.floor(Math.max(x0, x1) / CELL);
    const cy0 = Math.floor(Math.min(y0, y1) / CELL);
    const cy1 = Math.floor(Math.max(y0, y1) / CELL);
    for (let cx = cx0; cx <= cx1; cx++) {
      for (let cy = cy0; cy <= cy1; cy++) {
        const k = cellKey(cx, cy);
        const list = grid.get(k);
        if (list) list.push(id, i); else grid.set(k, [id, i]);
      }
    }
  }

  /** Street name line (OpenMapTiles "transportation_name"), only used to tell the driver where he is. */
  addName(pts, name) {
    if (!name || pts.length < 2) return;
    const a = pts[0];
    const b = pts[pts.length - 1];
    const k = `${name}|${Math.round(a[0])}|${Math.round(a[1])}|${Math.round(b[0])}|${Math.round(b[1])}`;
    if (this.nameSeen.has(k)) return;
    this.nameSeen.add(k);
    const id = this.names.length;
    const xs = Float64Array.from(pts, (p) => p[0]);
    const ys = Float64Array.from(pts, (p) => p[1]);
    this.names.push({ name, xs, ys });
    for (let i = 0; i < xs.length - 1; i++) this._gridSeg(this.nameGrid, id, i, xs[i], ys[i], xs[i + 1], ys[i + 1]);
  }

  /** A GeoJSON feature of map.querySourceFeatures(); toLocal(lon, lat) -> [x, y]. */
  ingestFeature(f, toLocal, kind = 'road') {
    const g = f.geometry;
    if (!g) return 0;
    const lines = g.type === 'LineString' ? [g.coordinates] : g.type === 'MultiLineString' ? g.coordinates : null;
    if (!lines) return 0;
    const p = f.properties || {};
    let added = 0;
    for (const line of lines) {
      const pts = line.map((c) => toLocal(c[0], c[1]));
      if (kind === 'name') this.addName(pts, p['name:he'] || p.name || p['name:latin']);
      else added += this.addLine(pts, p);
    }
    return added;
  }

  // ------------------------------------------------------------------ queries
  /**
   * The road under / next to a point: the one whose surface is closest (distance to the centre
   * line minus the half width), so a wide avenue wins over the lane that branches off it.
   */
  nearest(x, y, maxD = 60, filter = null) {
    const c0x = Math.floor((x - maxD) / CELL);
    const c1x = Math.floor((x + maxD) / CELL);
    const c0y = Math.floor((y - maxD) / CELL);
    const c1y = Math.floor((y + maxD) / CELL);
    let best = null;
    let bestScore = Infinity;
    for (let cx = c0x; cx <= c1x; cx++) {
      for (let cy = c0y; cy <= c1y; cy++) {
        const list = this.grid.get(cellKey(cx, cy));
        if (!list) continue;
        for (let k = 0; k < list.length; k += 2) {
          const road = this.roads[list[k]];
          if (filter && !filter(road)) continue;
          const i = list[k + 1];
          const ax = road.xs[i];
          const ay = road.ys[i];
          const bx = road.xs[i + 1] - ax;
          const by = road.ys[i + 1] - ay;
          const l2 = bx * bx + by * by;
          let t = l2 ? ((x - ax) * bx + (y - ay) * by) / l2 : 0;
          t = t < 0 ? 0 : t > 1 ? 1 : t;
          const px = ax + bx * t;
          const py = ay + by * t;
          const d = Math.hypot(x - px, y - py);
          if (d > maxD) continue;
          const score = d - road.spec.hw;
          if (score < bestScore) {
            bestScore = score;
            const l = Math.sqrt(l2) || 1;
            best = { road, i, t, d, px, py, ux: bx / l, uy: by / l, s: road.cum[i] + l * t };
          }
        }
      }
    }
    return best;
  }

  nameAt(x, y, maxD = 14) {
    let best = null;
    let bd = maxD;
    const c0x = Math.floor((x - maxD) / CELL);
    const c1x = Math.floor((x + maxD) / CELL);
    const c0y = Math.floor((y - maxD) / CELL);
    const c1y = Math.floor((y + maxD) / CELL);
    for (let cx = c0x; cx <= c1x; cx++) {
      for (let cy = c0y; cy <= c1y; cy++) {
        const list = this.nameGrid.get(cellKey(cx, cy));
        if (!list) continue;
        for (let k = 0; k < list.length; k += 2) {
          const nm = this.names[list[k]];
          const i = list[k + 1];
          const ax = nm.xs[i];
          const ay = nm.ys[i];
          const bx = nm.xs[i + 1] - ax;
          const by = nm.ys[i + 1] - ay;
          const l2 = bx * bx + by * by;
          let t = l2 ? ((x - ax) * bx + (y - ay) * by) / l2 : 0;
          t = t < 0 ? 0 : t > 1 ? 1 : t;
          const d = Math.hypot(x - ax - bx * t, y - ay - by * t);
          if (d < bd) { bd = d; best = nm.name; }
        }
      }
    }
    return best;
  }

  /** Point at arc length s of a road: position and unit direction (along the vertex order). */
  pointAt(road, s) {
    const n = road.xs.length;
    s = s < 0 ? 0 : s > road.len ? road.len : s;
    let i = 0;
    while (i < n - 2 && road.cum[i + 1] < s) i++;
    const l = road.cum[i + 1] - road.cum[i] || 1;
    const t = (s - road.cum[i]) / l;
    const bx = road.xs[i + 1] - road.xs[i];
    const by = road.ys[i + 1] - road.ys[i];
    return { x: road.xs[i] + bx * t, y: road.ys[i] + by * t, ux: bx / l, uy: by / l, i };
  }

  /** Built-up area? Many local streets around = a town. */
  urbanAt(x, y) {
    const cx = Math.floor(x / CELL);
    const cy = Math.floor(y / CELL);
    const k = cellKey(cx, cy);
    let v = this.urbanCache.get(k);
    if (v === undefined) {
      const seen = new Set();
      for (let dx = -2; dx <= 2; dx++) {
        for (let dy = -2; dy <= 2; dy++) {
          const list = this.grid.get(cellKey(cx + dx, cy + dy));
          if (!list) continue;
          for (let i = 0; i < list.length; i += 2) if (this.roads[list[i]].spec.rank <= 1) seen.add(list[i]);
        }
      }
      v = seen.size >= 9;
      this.urbanCache.set(k, v);
    }
    return v;
  }

  /** Legal speed (km/h) on a road at a point. */
  limitFor(road, x, y) {
    if (road.rb >= 0) return LIMIT_ROUNDABOUT;
    const urban = this.urbanAt(x, y);
    if (road.ramp) return Math.min(LIMIT_RAMP, urban ? road.spec.urban : road.spec.limit);
    return urban ? road.spec.urban : road.spec.limit;
  }

  /** Arms of a node that really point in different directions (tile seams leave overlapping stubs). */
  distinctArms(node) {
    const out = [];
    for (const a of node.arms) {
      const r = this.roads[a.road];
      const same = out.find((o) => o.dx * a.dx + o.dy * a.dy > 0.92);
      if (!same) out.push(a);
      else if (r.spec.rank > this.roads[same.road].spec.rank) out[out.indexOf(same)] = a;
    }
    return out;
  }

  /** Walk `dist` metres along a road from vertex vi in direction step. */
  walk(road, vi, step, dist) {
    let i = vi;
    let left = dist;
    const passed = [];
    const n = road.xs.length;
    while (true) {
      const j = i + step;
      if (j < 0 || j >= n) {
        const k = i - step;
        const l = Math.hypot(road.xs[i] - road.xs[k], road.ys[i] - road.ys[k]) || 1;
        return { x: road.xs[i], y: road.ys[i], ux: (road.xs[i] - road.xs[k]) / l, uy: (road.ys[i] - road.ys[k]) / l, done: dist - left, passed, end: true };
      }
      const bx = road.xs[j] - road.xs[i];
      const by = road.ys[j] - road.ys[i];
      const l = Math.hypot(bx, by) || 1;
      if (l >= left) {
        return { x: road.xs[i] + (bx / l) * left, y: road.ys[i] + (by / l) * left, ux: bx / l, uy: by / l, done: dist, passed, end: false };
      }
      left -= l;
      i = j;
      passed.push(road.nodes[i]);
    }
  }

  // ------------------------------------------------------------------ analysis
  _findRoundabouts() {
    const fits = [];
    for (const road of this.roads) {
      if (road.fit === undefined) {
        road.fit = null;
        if (road.xs.length >= 3 && road.len < 300 && road.spec.rank >= 1 && road.spec.rank <= 4 && !road.ramp) {
          const f = fitCircle(road.xs, road.ys);
          if (f && f.r >= 4.5 && f.r <= 45 && f.maxRes < Math.max(0.7, f.r * 0.08) && Math.abs(f.sweep) >= 0.6) road.fit = f;
        }
      }
      if (road.fit) fits.push(road);
    }
    const clusters = [];
    for (const road of fits) {
      const f = road.fit;
      const w = Math.abs(f.sweep);
      let c = clusters.find((q) => Math.hypot(q.cx - f.cx, q.cy - f.cy) < Math.max(3, q.r * 0.3) && f.r / q.r > 0.7 && f.r / q.r < 1.4);
      if (!c) { c = { cx: f.cx, cy: f.cy, r: f.r, sweep: 0, w: 0, ccw: 0, roads: [] }; clusters.push(c); }
      c.cx = (c.cx * c.w + f.cx * w) / (c.w + w);
      c.cy = (c.cy * c.w + f.cy * w) / (c.w + w);
      c.r = (c.r * c.w + f.r * w) / (c.w + w);
      c.w += w;
      c.sweep += w;
      c.ccw += f.sweep;
      c.roads.push(road);
    }
    for (const r of this.roads) r.rb = -1;
    this.roundabouts = [];
    for (const c of clusters) {
      if (c.sweep < 5.0 || c.sweep > 7.4) continue; // a full circle is 2π ≈ 6.28
      const id = this.roundabouts.length;
      let hw = 0;
      for (const road of c.roads) {
        road.rb = id;
        hw = Math.max(hw, road.spec.hw);
        // traffic in Israel circles counter-clockwise; make the ring one-way even when the tiles do not say so
        if (!road.oneway) road.oneway = road.fit.sweep > 0 ? 1 : -1;
      }
      const lane = Math.min(hw, c.r * 0.55);
      this.roundabouts.push({ id, x: c.cx, y: c.cy, r: c.r, hw: lane, island: Math.max(1.2, c.r - lane - 0.4) });
    }
  }

  _findSignals(cx, cy, R) {
    const cand = [];
    for (const node of this.nodes) {
      if (Math.abs(node.x - cx) > R || Math.abs(node.y - cy) > R || node.arms.length < 3) continue;
      const arms = this.distinctArms(node);
      if (arms.length < 3) continue;
      let major = 0;
      let bad = false;
      for (const a of arms) {
        const r = this.roads[a.road];
        if (r.rb >= 0 || r.cls === 'motorway' || r.tunnel) bad = true;
        if (r.spec.rank >= 2 && !r.ramp) major++;
      }
      if (bad || major < 3) continue;
      if (this.roundabouts.some((q) => Math.hypot(q.x - node.x, q.y - node.y) < q.r + 14)) continue;
      cand.push({ node, arms });
    }
    const out = [];
    const used = new Set();
    for (const c of cand) {
      if (used.has(c.node.id)) continue;
      const members = [c];
      used.add(c.node.id);
      for (const o of cand) {
        if (!used.has(o.node.id) && members.some((m) => Math.hypot(m.node.x - o.node.x, m.node.y - o.node.y) < 30)
          && Math.hypot(c.node.x - o.node.x, c.node.y - o.node.y) < 45) {
          members.push(o);
          used.add(o.node.id);
        }
      }
      const sx = members.reduce((s, m) => s + m.node.x, 0) / members.length;
      const sy = members.reduce((s, m) => s + m.node.y, 0) / members.length;
      const ids = new Set(members.map((m) => m.node.id));
      const approaches = [];
      for (const m of members) {
        const dc = Math.hypot(m.node.x - sx, m.node.y - sy);
        for (const a of m.arms) {
          const road = this.roads[a.road];
          if (road.spec.rank < 1) continue;
          if (road.oneway && road.oneway === a.step) continue; // traffic only leaves the junction on this arm
          // the arms that join the nodes of one big junction are inside it
          const probe = this.walk(road, a.vi, a.step, 32);
          if (probe.passed.some((id) => ids.has(id))) continue;
          const w = this.walk(road, a.vi, a.step, Math.max(7, road.spec.hw + 3.5) + dc * 0.4);
          if (w.done < 5) continue;
          const dx = -w.ux; // inbound driving direction
          const dy = -w.uy;
          if (approaches.some((p) => Math.hypot(p.x - w.x, p.y - w.y) < 3.5 && p.dx * dx + p.dy * dy > 0.7)) continue;
          const hw = road.spec.hw;
          approaches.push({ x: w.x, y: w.y, dx, dy, hw, oneway: !!road.oneway,
            lx: w.x + dy * (hw + 0.9), ly: w.y - dx * (hw + 0.9), group: 0, road: road.id });
        }
      }
      if (approaches.length < 2) continue;
      const base = Math.atan2(approaches[0].dy, approaches[0].dx);
      for (const p of approaches) {
        let d = Math.abs(Math.atan2(p.dy, p.dx) - base) % Math.PI;
        if (d > Math.PI / 2) d = Math.PI - d;
        p.group = d < Math.PI / 4 ? 0 : 1;
      }
      if (!approaches.some((p) => p.group === 1)) continue;
      const key = `${Math.round(sx / 15)},${Math.round(sy / 15)}`;
      const old = this.signals.get(key);
      const sig = { key, x: sx, y: sy, r: Math.max(...approaches.map((p) => Math.hypot(p.x - sx, p.y - sy))),
        offset: old ? old.offset : hash01(sx, sy, 3) * SIGNAL_CYCLE, approaches };
      this.signals.set(key, sig);
      out.push(sig);
    }
    return out;
  }

  /**
   * Everything that stands around (cx, cy) within R metres. Deterministic: the same streets give
   * the same lights, lamps and trees.
   */
  analyze(cx, cy, R = 380) {
    this._findRoundabouts();
    const signals = this._findSignals(cx, cy, R);
    const roundabouts = this.roundabouts.filter((q) => Math.hypot(q.x - cx, q.y - cy) < R + q.r);
    const lamps = [];
    const trees = [];
    const signs = [];
    const stars = [];
    const nearJunction = (x, y, pad) => signals.some((s) => Math.hypot(s.x - x, s.y - y) < s.r + pad)
      || roundabouts.some((q) => Math.hypot(q.x - x, q.y - y) < q.r + pad);
    const clear = (x, y, pad) => {
      const n = this.nearest(x, y, 12);
      return !n || n.d > n.road.spec.hw + pad;
    };
    for (const road of this.roads) {
      if (road.maxx < cx - R || road.minx > cx + R || road.maxy < cy - R || road.miny > cy + R) continue;
      const hw = road.spec.hw;
      const rank = road.spec.rank;
      const inR = (p) => Math.abs(p.x - cx) < R && Math.abs(p.y - cy) < R;
      if (road.rb < 0 && !road.tunnel) {
        // street lamps on the main roads
        if (rank >= 2 && lamps.length < 260) {
          for (let s = 18, k = 0; s < road.len - 8; s += 42, k++) {
            const p = this.pointAt(road, s);
            if (!inR(p)) continue;
            const side = road.oneway ? 1 : (k % 2 ? 1 : -1);
            const x = p.x + p.uy * side * (hw + 1.0);
            const y = p.y - p.ux * side * (hw + 1.0);
            if (nearJunction(x, y, 6) || !clear(x, y, 0.5)) continue;
            lamps.push({ x, y, dx: -p.uy * side, dy: p.ux * side }); // (dx,dy): towards the road
          }
        }
        // trees along the streets
        if (rank >= 1 && rank <= 3 && trees.length < 520) {
          for (let s = 9; s < road.len - 6; s += 15) {
            const p = this.pointAt(road, s);
            if (!inR(p)) continue;
            for (const side of [-1, 1]) {
              const h = hash01(p.x, p.y, side + 5);
              if (h > 0.5) continue;
              const off = hw + 2.3 + h * 4;
              const x = p.x + p.uy * side * off;
              const y = p.y - p.ux * side * off;
              if (!clear(x, y, 1.6) || nearJunction(x, y, 2)) continue;
              const u = this.urbanAt(x, y);
              const kind = h < 0.07 ? 'palm' : h < (u ? 0.17 : 0.3) ? 'cypress' : 'round';
              trees.push({ x, y, kind, size: 0.8 + hash01(x, y, 9) * 0.6, tint: hash01(x, y, 11) });
            }
          }
        }
        // speed limit signs
        if (rank >= 1 && road.len > (rank >= 2 ? 90 : 170) && signs.length < 120) {
          const ends = road.oneway === 1 ? [1] : road.oneway === -1 ? [-1] : [1, -1];
          for (const dir of ends) {
            const p = this.pointAt(road, dir === 1 ? 24 : road.len - 24);
            if (!inR(p)) continue;
            const tx = p.ux * dir;
            const ty = p.uy * dir;
            const x = p.x + ty * (hw + 1.5);
            const y = p.y - tx * (hw + 1.5);
            if (nearJunction(x, y, 3) || !clear(x, y, 0.8)) continue;
            signs.push({ x, y, fx: -tx, fy: -ty, kind: String(this.limitFor(road, p.x, p.y)) });
          }
        }
        // "give way" before a roundabout
        for (const end of [0, road.xs.length - 1]) {
          const q = roundabouts.find((c) => Math.abs(Math.hypot(c.x - road.xs[end], c.y - road.ys[end]) - c.r) < 3.5);
          if (!q) continue;
          const step = end === 0 ? 1 : -1; // away from the roundabout
          if (road.oneway && road.oneway === step) continue;
          const w = this.walk(road, end, step, q.hw + 5);
          if (w.done < 4) continue;
          const tx = -w.ux;
          const ty = -w.uy;
          signs.push({ x: w.x + ty * (hw + 1.2), y: w.y - tx * (hw + 1.2), fx: -tx, fy: -ty, kind: 'yield' });
          signs.push({ x: w.x + ty * (hw + 1.2), y: w.y - tx * (hw + 1.2), fx: -tx, fy: -ty, kind: 'rb', low: true });
        }
        // stars to collect
        if (rank >= 1 && rank <= 4 && road.len > 70) {
          for (let s = 35 + hash01(road.xs[0], road.ys[0], 21) * 40; s < road.len - 20; s += 95) {
            const p = this.pointAt(road, s);
            if (!inR(p) || nearJunction(p.x, p.y, 4)) continue;
            const lane = road.oneway ? 0 : Math.min(hw * 0.5, 1.8) * (hash01(p.x, p.y, 23) < 0.5 ? 1 : -1);
            const x = p.x + p.uy * lane;
            const y = p.y - p.ux * lane;
            stars.push({ id: `${Math.round(x)},${Math.round(y)}`, x, y });
          }
        }
      }
    }
    return { cx, cy, R, signals, roundabouts, lamps, trees, signs, stars };
  }

  /** A random spot on a street between dMin and dMax metres from (x, y) – the next mission. */
  randomSpot(x, y, dMin, dMax, rnd = Math.random) {
    const pool = this.roads.filter((r) => r.spec.rank >= 1 && r.spec.rank <= 4 && r.rb < 0 && !r.tunnel && r.len > 25
      && r.maxx > x - dMax && r.minx < x + dMax && r.maxy > y - dMax && r.miny < y + dMax);
    for (let k = 0; k < 40 && pool.length; k++) {
      const road = pool[Math.floor(rnd() * pool.length)];
      const p = this.pointAt(road, road.len * (0.25 + rnd() * 0.5));
      const d = Math.hypot(p.x - x, p.y - y);
      if (d >= dMin && d <= dMax) return { x: p.x, y: p.y, road };
    }
    return null;
  }
}
