// Node test of web/js/game-world.js:  node tests/test_game_world.mjs
import assert from 'node:assert/strict';
import { World, fitCircle, signalState, SIGNAL_CYCLE } from '../web/js/game-world.js';

function seg(w, cls, a, b, extra = {}, step = 20) {
  const n = Math.max(1, Math.round(Math.hypot(b[0] - a[0], b[1] - a[1]) / step));
  const pts = [];
  for (let i = 0; i <= n; i++) pts.push([a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n]);
  return w.addLine(pts, { class: cls, ...extra });
}

function town() {
  const w = new World();
  seg(w, 'primary', [-400, 0], [0, 0]); seg(w, 'primary', [0, 0], [300, 0]);
  seg(w, 'secondary', [0, -300], [0, 0]); seg(w, 'secondary', [0, 0], [0, 300]);
  for (const y of [-240, -160, -80, 80, 160, 240]) { seg(w, 'minor', [-400, y], [0, y]); seg(w, 'minor', [0, y], [300, y]); }
  for (const x of [-300, -200, -100, 100, 200]) { seg(w, 'minor', [x, -300], [x, 0]); seg(w, 'minor', [x, 0], [x, 300]); }
  for (let q = 0; q < 4; q++) { // roundabout, 4 arcs, counter-clockwise
    const pts = [];
    for (let i = 0; i <= 6; i++) { const a = Math.PI + (q + i / 6) * Math.PI / 2; pts.push([314 + 14 * Math.cos(a), 14 * Math.sin(a)]); }
    w.addLine(pts, { class: 'primary' });
  }
  seg(w, 'primary', [328, 0], [600, 0]);
  return w;
}

// circle fit
{
  const xs = [], ys = [];
  for (let i = 0; i < 8; i++) { xs.push(5 + 10 * Math.cos(i / 4)); ys.push(-3 + 10 * Math.sin(i / 4)); }
  const f = fitCircle(xs, ys);
  assert.ok(Math.abs(f.cx - 5) < 1e-6 && Math.abs(f.cy + 3) < 1e-6 && Math.abs(f.r - 10) < 1e-6 && f.sweep > 0);
  assert.equal(fitCircle([0, 1, 2], [0, 0, 0]), null);
}

const w = town();
const nRoads = w.roads.length;
// the same tile twice adds nothing
assert.equal(seg(w, 'primary', [-400, 0], [0, 0]), 0);
assert.equal(w.roads.length, nRoads);
// a line that overlaps a known road only adds its new part
assert.equal(seg(w, 'primary', [-460, 0], [-360, 0]), 1);

const sc = w.analyze(0, 0, 700);
// one roundabout, ring made one-way counter-clockwise
assert.equal(sc.roundabouts.length, 1);
const rb = sc.roundabouts[0];
assert.ok(Math.hypot(rb.x - 314, rb.y) < 0.5 && Math.abs(rb.r - 14) < 0.5);
assert.ok(w.roads.filter((r) => r.rb === 0).every((r) => r.oneway === 1));
// traffic lights only where main roads cross: (0,0); 4 approaches, two groups
assert.equal(sc.signals.length, 1);
const sig = sc.signals[0];
assert.ok(Math.hypot(sig.x, sig.y) < 1);
assert.equal(sig.approaches.length, 4);
assert.equal(sig.approaches.filter((a) => a.group === 0).length, 2);
for (const a of sig.approaches) { // inbound direction points at the junction; the pole is on the driver's right
  assert.ok(a.dx * (0 - a.x) + a.dy * (0 - a.y) > 0);
  assert.ok((a.lx - a.x) * a.dy - (a.ly - a.y) * a.dx > 0);
}
// never both groups green; each gets green in a cycle
let g0 = 0, g1 = 0;
for (let t = 0; t < SIGNAL_CYCLE; t += 0.25) {
  const a = signalState(sig, 0, t), b = signalState(sig, 1, t);
  assert.ok(!(a !== 'r' && b !== 'r'));
  g0 += a === 'g'; g1 += b === 'g';
}
assert.ok(g0 > 20 && g1 > 20);
// speed limits: town streets 50, roundabout 30, service 30
const n1 = w.nearest(-150, 1.5, 30);
assert.equal(n1.road.cls, 'primary');
assert.equal(w.limitFor(n1.road, -150, 0), 50);
assert.equal(w.limitFor(w.roads.find((r) => r.rb === 0), 314, 14), 30);
// out of town: 80 / 110
const o = new World();
seg(o, 'primary', [0, 0], [2000, 0]); seg(o, 'motorway', [0, 500], [2000, 500]);
assert.equal(o.limitFor(o.roads[0], 1000, 0), 80);
assert.equal(o.limitFor(o.roads[1], 1000, 500), 110);
// furniture exists and stays off the carriageways
assert.ok(sc.lamps.length > 10 && sc.trees.length > 30 && sc.stars.length > 5);
assert.ok(sc.signs.some((s) => s.kind === 'yield') && sc.signs.some((s) => s.kind === '50'));
for (const p of [...sc.lamps, ...sc.trees]) { const n = w.nearest(p.x, p.y, 12); assert.ok(!n || n.d > n.road.spec.hw + 0.4); }
// deterministic
assert.deepEqual(w.analyze(0, 0, 700).trees, sc.trees);
// mission target
const spot = w.randomSpot(0, 0, 150, 400);
assert.ok(spot && Math.hypot(spot.x, spot.y) >= 150);
console.log('game-world: ok');
