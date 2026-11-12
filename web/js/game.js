// Game mode ("מצב משחק"): drive a car through the real map in 3D.
// The streets, junctions and roundabouts come from the basemap itself (game-world.js); the car, the
// traffic lights, lamps, trees and signs are drawn into the map by a small WebGL layer (game-gl.js).
// The driver is scored by the traffic rules: speed limits, red lights, one-way streets, roundabouts.
/* global maplibregl */

import { World, ROAD, signalState } from './game-world.js';
import { GameLayer, M4, rgb, beaconMesh, brakeMesh, carMesh, carShadowMesh, headlightMesh, sceneMeshes, signAtlas,
  signalLampMesh, starMesh, wheelMesh } from './game-gl.js';
import { announce, store, toast } from './util.js';

const D2R = Math.PI / 180;
const CAMERAS = [
  { name: 'מאחורי הרכב', zoom: 21.7, pitch: 73, look: 6 },
  { name: 'מרחוק', zoom: 20.6, pitch: 64, look: 10 },
  { name: 'מלמעלה', zoom: 19.6, pitch: 35, look: 3 },
];
const HANDLERS = ['scrollZoom', 'boxZoom', 'dragRotate', 'dragPan', 'keyboard', 'doubleClickZoom', 'touchZoomRotate', 'touchPitch'];
const NPC_COLORS = [0xE9ECEF, 0x2B2F36, 0xB8232F, 0x2456B8, 0x8A8F98, 0x1F7A4D, 0xF3F3F0];
const MAX_NPC = 9;
const KEYMAP = {
  ArrowUp: 'up', KeyW: 'up', ArrowDown: 'down', KeyS: 'down', ArrowLeft: 'left', KeyA: 'left',
  ArrowRight: 'right', KeyD: 'right', Space: 'hand',
};

const angDiff = (a, b) => ((((a - b) % 360) + 540) % 360) - 180; // a - b in (-180, 180]
const clamp = (v, lo, hi) => (v < lo ? lo : v > hi ? hi : v);

function pointInRing(x, y, ring) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const xi = ring[i][0], yi = ring[i][1], xj = ring[j][0], yj = ring[j][1];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

export class DriveGame {
  /**
   * @param app  the application (web/js/app.js)
   * @param opts for the test page: { source, sourceLayer, nameLayer } of the road data
   */
  constructor(app, opts = {}) {
    this.app = app;
    this.mv = app.map;
    this.map = app.map.map;
    this.opts = { source: 'openmaptiles', sourceLayer: 'transportation', nameLayer: 'transportation_name', ...opts };
    this.on = false;
    this._frame = this._frame.bind(this);
    this._key = this._key.bind(this);
  }

  // ------------------------------------------------------------------ local frame (metres, x east, y north)
  _setOrigin(lng, lat) {
    const mc = maplibregl.MercatorCoordinate.fromLngLat([lng, lat]);
    this.ox = mc.x;
    this.oy = mc.y;
    this.ms = mc.meterInMercatorCoordinateUnits();
    this.lat0 = lat;
    this.toLocal = (lo, la) => {
      const s = Math.sin(la * D2R);
      return [((lo + 180) / 360 - this.ox) / this.ms, (this.oy - (0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI))) / this.ms];
    };
  }

  toLngLat(x, y) {
    const mx = this.ox + x * this.ms;
    const my = this.oy - y * this.ms;
    return [mx * 360 - 180, (360 / Math.PI) * Math.atan(Math.exp((180 - my * 360) * D2R)) - 90];
  }

  // ------------------------------------------------------------------ start / stop
  /** Can the game run on this basemap? It needs the vector roads. */
  available() {
    return !!this.map.getSource(this.opts.source);
  }

  async start(at = null) {
    if (this.on || this.starting) return false;
    const map = this.map;
    if (!this.available()) {
      toast('מצב המשחק זמין רק עם מפת הבסיס המלאה (כבישים וקטוריים).', { kind: 'error', ms: 5000 });
      return false;
    }
    this.starting = true;
    const c = at || map.getCenter();
    const lng = c.lng ?? c.lon;
    this._setOrigin(lng, c.lat);
    this.saved = { center: map.getCenter(), zoom: map.getZoom(), bearing: map.getBearing(), was3d: this.mv.is3d,
      maxZoom: map.getMaxZoom(), handlers: HANDLERS.filter((h) => map[h]?.isEnabled?.()) };
    document.body.classList.add('game-on');
    this.mv.quiet = true;
    this._buildHud();
    this._setLoading('טוען את הכבישים…');
    for (const h of HANDLERS) map[h]?.disable?.();
    map.stop();

    // 3D buildings and sky of the regular 3D view; the ground stays flat so the wheels touch the road
    if (!this.mv.is3d) this.mv.set3D(true, { animate: false });
    try {
      if (map.getTerrain?.()) map.setTerrain(null);
    } catch { /* no terrain */ }
    map.setMaxZoom(23);
    map.setMaxPitch(80);
    map.jumpTo({ center: [lng, c.lat], zoom: Math.max(16, Math.min(map.getZoom(), 17)), pitch: 45 });
    await this._idle(7000);

    this.world = new World();
    this._ingestAll();
    let spot = this.world.nearest(0, 0, 400);
    if (!spot) { // tiles may still be on their way
      await this._idle(4000);
      this._ingestAll();
      spot = this.world.nearest(0, 0, 400);
    }
    if (!spot) {
      this.starting = false;
      this._teardown();
      toast('לא נמצא כביש בקרבת מקום. הזיזו את המפה לאזור עם כבישים ונסו שוב.', { kind: 'error', ms: 5500 });
      return false;
    }

    this.night = this.mv._resolvedTheme?.() === 'dark';
    const dir = spot.road.oneway === -1 ? -1 : 1;
    const lane = spot.road.oneway ? 0 : Math.min(spot.road.spec.hw * 0.5, 1.8);
    this.car = { x: spot.px + spot.uy * dir * lane, y: spot.py - spot.ux * dir * lane,
      hd: Math.atan2(spot.ux * dir, spot.uy * dir) / D2R, v: 0, steer: 0, spin: 0, braking: false };
    this.keys = {};
    this.t = 0;
    this.cam = { mode: store.get('game:cam', 0) % CAMERAS.length, bearing: this.car.hd, zoom: map.getZoom(), pitch: map.getPitch() };
    this.score = 0;
    this.best = store.get('game:best', 0);
    this.stats = { dist: 0, clean: 0, violations: 0, missions: 0, stars: 0 };
    this.rule = { speeding: 0, wrongWay: 0, offroad: 0, rbWrong: 0, crashCool: 0, redStop: new Map(), prev: new Map() };
    this.collected = new Set();
    this.npcs = [];
    this.blds = [];
    this.bldTimer = 0;
    this.info = { limit: 50, onRoad: true, name: '', road: spot.road };
    this.sound = store.get('game:sound', true);

    this._addLayers();
    this._rebuildScene();
    this.lastIngest = { x: this.car.x, y: this.car.y, t: 0 };
    this._newMission();

    window.addEventListener('keydown', this._key, true);
    window.addEventListener('keyup', this._key, true);
    this._blur = () => { this.keys = {}; };
    window.addEventListener('blur', this._blur);

    this.on = true;
    this.starting = false;
    this.running = false; // until the driver presses "התחל"
    this._setLoading(null);
    this._showIntro();
    this.last = performance.now();
    this.raf = requestAnimationFrame(this._frame);
    announce('מצב משחק הופעל. חיצים או WASD לנהיגה, Esc ליציאה');
    return true;
  }

  stop() {
    if (!this.on) return;
    this.on = false;
    cancelAnimationFrame(this.raf);
    window.removeEventListener('keydown', this._key, true);
    window.removeEventListener('keyup', this._key, true);
    window.removeEventListener('blur', this._blur);
    if (this.score > this.best) store.set('game:best', Math.round(this.score));
    const here = this.toLngLat(this.car.x, this.car.y);
    this._teardown(here);
    announce('מצב משחק הסתיים');
  }

  _teardown(center = null) {
    const map = this.map;
    for (const id of ['game-3d', 'game-road-center', 'game-road', 'game-road-curb']) if (map.getLayer(id)) map.removeLayer(id);
    this.layer = null;
    const s = this.saved;
    map.setMaxZoom(s.maxZoom);
    for (const h of s.handlers) map[h]?.enable?.();
    if (s.was3d) {
      this.mv._apply3D?.(); // the relief comes back
      map.jumpTo({ center: center || s.center, zoom: Math.min(s.zoom, 17), bearing: 0, pitch: 60 });
      map.setMaxPitch(75);
    } else {
      map.jumpTo({ center: center || s.center, zoom: Math.min(s.zoom, 17), bearing: 0, pitch: 0 });
      this.mv.set3D(false, { animate: false });
    }
    this.mv._applyLayers?.();
    this.mv.quiet = false;
    this.root?.remove();
    this.root = null;
    document.body.classList.remove('game-on');
    try { this.audio?.close(); } catch { /* already closed */ }
    this.audio = null;
    this.world = null;
  }

  _idle(ms) {
    return new Promise((res) => {
      let done = false;
      const fin = () => { if (!done) { done = true; res(); } };
      this.map.once('idle', fin);
      setTimeout(fin, ms);
    });
  }

  // ------------------------------------------------------------------ world data
  _query(layer) {
    try {
      return this.map.querySourceFeatures(this.opts.source, layer ? { sourceLayer: layer } : {});
    } catch {
      return [];
    }
  }

  _ingestAll() {
    for (const f of this._query(this.opts.sourceLayer)) this.world.ingestFeature(f, this.toLocal);
    for (const f of this._query(this.opts.nameLayer)) this.world.ingestFeature(f, this.toLocal, 'name');
  }

  /** While driving: new tiles ahead bring new streets. Read in slices so no frame stalls. */
  _ingestStep() {
    const q = this.queue;
    if (q) {
      const end = Math.min(q.i + 500, q.list.length);
      for (; q.i < end; q.i++) q.added += this.world.ingestFeature(q.list[q.i], this.toLocal, q.kind);
      if (q.i >= q.list.length) {
        if (q.kind === 'road') this.queue = { list: this._query(this.opts.nameLayer), i: 0, kind: 'name', added: q.added };
        else {
          this.queue = null;
          this._rebuildScene();
        }
      }
      return;
    }
    const li = this.lastIngest;
    const moved = Math.hypot(this.car.x - li.x, this.car.y - li.y);
    if ((moved > 110 && this.t - li.t > 1) || this.t - li.t > 8) {
      this.lastIngest = { x: this.car.x, y: this.car.y, t: this.t };
      this.queue = { list: this._query(this.opts.sourceLayer), i: 0, kind: 'road', added: 0 };
    }
  }

  _rebuildScene() {
    const sc = this.world.analyze(this.car.x, this.car.y, 380);
    sc.stars = sc.stars.filter((s) => !this.collected.has(s.id));
    this.scene = sc;
    const m = sceneMeshes(sc, this.night);
    this.layer.setMesh('solid', m.solid);
    this.layer.setMesh('decals', m.decals);
    this.layer.setMesh('glow', m.glow);
    this.lampSig = '';
    // a light that just appeared must not fine a driver who is already past its stop line
    this.rule.prev = new Map();
  }

  _addLayers() {
    const map = this.map;
    const o = this.opts;
    const night = this.night;
    const before = map.getLayer('building-3d') ? 'building-3d'
      : (map.getStyle().layers.find((l) => l.type === 'symbol')?.id);
    const mpp = (z) => (78271.517 * Math.cos(this.lat0 * D2R)) / 2 ** z;
    const classes = Object.keys(ROAD);
    const widthOf = (extra) => ['match', ['get', 'class'], ...classes.flatMap((c) => [c, ROAD[c].hw * 2 + extra]), 6];
    const metres = (expr) => ['interpolate', ['exponential', 2], ['zoom'], 12, ['*', expr, 1 / mpp(12)], 24, ['*', expr, 1 / mpp(24)]];
    const base = { type: 'line', source: o.source, ...(o.sourceLayer ? { 'source-layer': o.sourceLayer } : {}) };
    const drivable = ['in', ['get', 'class'], ['literal', classes]];
    const add = (l) => { try { map.addLayer(l, before); } catch (e) { console.warn('game layer', l.id, e); } };
    add({ ...base, id: 'game-road-curb', filter: drivable, layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: { 'line-color': night ? '#5B606B' : '#C4C7CC', 'line-width': metres(widthOf(2.6)) } });
    add({ ...base, id: 'game-road', filter: drivable, layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: { 'line-color': night ? '#2A2D34' : '#4B4F58', 'line-width': metres(widthOf(0)) } });
    add({ ...base, id: 'game-road-center',
      filter: ['all', ['in', ['get', 'class'], ['literal', ['trunk', 'primary', 'secondary', 'tertiary', 'minor']]],
        ['!=', ['coalesce', ['get', 'oneway'], 0], 1], ['!=', ['coalesce', ['get', 'oneway'], 0], -1], ['!=', ['coalesce', ['get', 'ramp'], 0], 1]],
      layout: { 'line-cap': 'butt', 'line-join': 'round' },
      paint: { 'line-color': night ? '#C9CCD2' : '#F2F2EE', 'line-width': metres(0.16), 'line-dasharray': [19, 28] } });

    this.layer = new GameLayer('game-3d', (L) => this._draw(L));
    this.layer.night = night;
    this.layer.setAtlas(signAtlas());
    this.layer.setMesh('car', carMesh(rgb(0xD62839)));
    this.layer.setMesh('wheel', wheelMesh());
    this.layer.setMesh('brake', brakeMesh());
    this.layer.setMesh('shadow', carShadowMesh());
    this.layer.setMesh('beams', headlightMesh());
    this.layer.setMesh('star', starMesh());
    this.layer.setMesh('beacon', beaconMesh());
    NPC_COLORS.forEach((c, i) => this.layer.setMesh(`npc${i}`, carMesh(rgb(c), { wheels: true, taxi: i === 0 })));
    const firstLabel = map.getStyle().layers.find((l) => l.type === 'symbol' && l.source !== undefined)?.id;
    try { map.addLayer(this.layer, firstLabel); } catch (e) { console.warn('game 3d layer', e); }
    for (const id of ['pois', 'pois-cluster', 'pois-cluster-count']) if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', 'none');
  }

  // ------------------------------------------------------------------ input
  _key(e) {
    if (!this.on) return;
    const down = e.type === 'keydown';
    const k = KEYMAP[e.code];
    if (k) {
      this.keys[k] = down;
      e.preventDefault();
      e.stopPropagation();
      if (down && !this.running && !this.paused) this._begin();
      return;
    }
    if (!down || e.ctrlKey || e.metaKey || e.altKey) return;
    e.stopPropagation();
    switch (e.code) {
      case 'Escape': e.preventDefault(); this.stop(); break;
      case 'KeyC': this._nextCamera(); break;
      case 'KeyH': this._horn(); break;
      case 'KeyR': this._reset(); break;
      case 'KeyM': this._toggleSound(); break;
      case 'KeyP': this._togglePause(); break;
      case 'Enter': if (!this.running && !this.paused) this._begin(); break;
      default:
    }
  }

  _begin() {
    this.running = true;
    this.root.querySelector('.game-intro').hidden = true;
    this._flash('בהצלחה! נהיגה זהירה', 'good');
  }

  _togglePause() {
    if (!this.running && !this.paused) return;
    this.paused = !this.paused;
    this.running = !this.paused;
    this.root.querySelector('.game-paused').hidden = !this.paused;
  }

  _nextCamera() {
    this.cam.mode = (this.cam.mode + 1) % CAMERAS.length;
    store.set('game:cam', this.cam.mode);
    this._flash(`מצלמה: ${CAMERAS[this.cam.mode].name}`, 'info');
  }

  _reset() {
    const n = this.world.nearest(this.car.x, this.car.y, 500, (r) => r.spec.rank >= 1);
    if (!n) return;
    const dir = n.road.oneway === -1 ? -1 : 1;
    const lane = n.road.oneway ? 0 : Math.min(n.road.spec.hw * 0.5, 1.8);
    Object.assign(this.car, { x: n.px + n.uy * dir * lane, y: n.py - n.ux * dir * lane, hd: Math.atan2(n.ux * dir, n.uy * dir) / D2R, v: 0 });
    this.rule.prev = new Map();
    this._flash('הרכב הוחזר לכביש', 'info');
  }

  // ------------------------------------------------------------------ sound (generated, no audio files)
  _tone(freq, dur, type = 'sine', vol = 0.08, when = 0) {
    if (!this.sound) return;
    try {
      this.audio = this.audio || new (window.AudioContext || window.webkitAudioContext)();
      const t0 = this.audio.currentTime + when;
      const o = this.audio.createOscillator();
      const g = this.audio.createGain();
      o.type = type;
      o.frequency.value = freq;
      g.gain.setValueAtTime(vol, t0);
      g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
      o.connect(g).connect(this.audio.destination);
      o.start(t0);
      o.stop(t0 + dur);
    } catch { /* no audio */ }
  }

  _horn() { this._tone(415, 0.45, 'square', 0.06); this._tone(520, 0.45, 'square', 0.06); }

  _toggleSound() {
    this.sound = !this.sound;
    store.set('game:sound', this.sound);
    this.root.querySelector('[data-g="sound"]').setAttribute('aria-pressed', String(this.sound));
    this._flash(this.sound ? 'הצלילים הופעלו' : 'הצלילים הושתקו', 'info');
  }

  // ------------------------------------------------------------------ driving
  _physics(dt) {
    const car = this.car;
    const k = this.keys;
    const near = this.world.nearest(car.x, car.y, 40);
    const onRoad = !!near && near.d <= near.road.spec.hw + 1.0;
    const prevX = car.x;
    const prevY = car.y;

    // steering eases in and returns to the centre; less lock at speed
    const want = (k.right ? 1 : 0) - (k.left ? 1 : 0);
    car.steer += clamp(want - car.steer, -dt * 3.2, dt * 3.2);
    const lock = 0.6 / (1 + (Math.abs(car.v) / 9) ** 1.25);

    let a = 0;
    car.braking = false;
    if (k.up) a = car.v >= -0.2 ? 5.2 * (1 - Math.max(0, car.v) / 50) : 10;
    else if (k.down) {
      if (car.v > 0.4) { a = -10.5; car.braking = true; } else a = car.v > -7 ? -3.2 : 0;
    }
    if (k.hand) { a = -Math.sign(car.v) * 14; car.braking = true; }
    // rolling resistance and air
    a -= Math.sign(car.v) * (0.45 + 0.012 * car.v * car.v * 0.25);
    if (!onRoad) a -= Math.sign(car.v) * (Math.abs(car.v) > 7 ? 9 : 1.2); // grass and gravel hold the car back
    const v0 = car.v;
    car.v += a * dt;
    if ((k.hand || (!k.up && !k.down)) && v0 * car.v < 0) car.v = 0;
    car.v = clamp(car.v, -8, 50);

    car.hd += ((car.v / 2.6) * Math.tan(car.steer * lock) * dt) / D2R;
    const fx = Math.sin(car.hd * D2R);
    const fy = Math.cos(car.hd * D2R);
    car.x += fx * car.v * dt;
    car.y += fy * car.v * dt;
    car.spin += (car.v * dt) / 0.33;

    // walls: a building stops the car
    this.rule.crashCool -= dt;
    const nose = car.v >= 0 ? 2.0 : -2.0;
    if (this._inBuilding(car.x + fx * nose, car.y + fy * nose)) {
      const hard = Math.abs(car.v) > 3;
      car.x = prevX - fx * Math.sign(car.v || 1) * 0.3;
      car.y = prevY - fy * Math.sign(car.v || 1) * 0.3;
      car.v = -car.v * 0.25;
      if (hard && this.rule.crashCool <= 0) { this._violate('התנגשות בבניין', 20); this.rule.crashCool = 2; }
    }

    this.stats.dist += Math.abs(car.v) * dt;
    const road = onRoad ? near.road : null;
    this.info.onRoad = onRoad;
    this.info.near = near;
    this.info.road = road;
    if (road) this.info.limit = this.world.limitFor(road, car.x, car.y);
    this.nameTimer = (this.nameTimer || 0) - dt;
    if (this.nameTimer <= 0) {
      this.nameTimer = 0.4;
      this.info.name = road ? (this.world.nameAt(car.x, car.y) || '') : '';
    }
  }

  _inBuilding(x, y) {
    for (const b of this.blds) {
      if (x < b.minx || x > b.maxx || y < b.miny || y > b.maxy) continue;
      if (pointInRing(x, y, b.ring)) return true;
    }
    return false;
  }

  /** Footprints of the buildings around the car (from the map itself), refreshed a few times a second. */
  _scanBuildings() {
    const map = this.map;
    if (!map.getLayer('building-3d')) return;
    const p = map.project(this.toLngLat(this.car.x, this.car.y));
    let feats = [];
    try {
      feats = map.queryRenderedFeatures([[p.x - 260, p.y - 260], [p.x + 260, p.y + 200]], { layers: ['building-3d'] });
    } catch { /* style is changing */ }
    const out = [];
    for (const f of feats) {
      const g = f.geometry;
      const polys = g.type === 'Polygon' ? [g.coordinates] : g.type === 'MultiPolygon' ? g.coordinates : [];
      for (const poly of polys) {
        const ring = poly[0].map((c) => this.toLocal(c[0], c[1]));
        let minx = Infinity, miny = Infinity, maxx = -Infinity, maxy = -Infinity;
        for (const q of ring) { minx = Math.min(minx, q[0]); maxx = Math.max(maxx, q[0]); miny = Math.min(miny, q[1]); maxy = Math.max(maxy, q[1]); }
        if (Math.abs((minx + maxx) / 2 - this.car.x) < 60 && Math.abs((miny + maxy) / 2 - this.car.y) < 60) out.push({ ring, minx, miny, maxx, maxy });
      }
    }
    this.blds = out;
  }

  // ------------------------------------------------------------------ other cars
  _npcs(dt) {
    const w = this.world;
    const car = this.car;
    if (this.npcs.length < MAX_NPC && Math.random() < dt * 1.5) this._spawnNpc();
    for (const n of this.npcs) {
      const road = n.road;
      const tx = n.ux * n.dir;
      const ty = n.uy * n.dir;
      let target = Math.min(22, (w.limitFor(road, n.x, n.y) / 3.6) * n.pace);
      const stopDist = 5 + (n.v * n.v) / 7;
      // red and yellow lights
      for (const sig of this.scene.signals) {
        if (Math.abs(sig.x - n.x) > 70 || Math.abs(sig.y - n.y) > 70) continue;
        for (const ap of sig.approaches) {
          if (tx * ap.dx + ty * ap.dy < 0.8) continue;
          const along = (ap.x - n.x) * ap.dx + (ap.y - n.y) * ap.dy;
          const side = Math.abs((ap.x - n.x) * ap.dy - (ap.y - n.y) * ap.dx);
          if (along > 1 && along < stopDist + 2 && side < ap.hw + 1 && signalState(sig, ap.group, this.t) !== 'g') target = 0;
        }
      }
      // keep a gap to whoever is in front (the player included)
      const others = [car, ...this.npcs];
      for (const o of others) {
        if (o === n) continue;
        const rx = o.x - n.x;
        const ry = o.y - n.y;
        const along = rx * tx + ry * ty;
        if (along > 0.5 && along < stopDist + 4 && Math.abs(rx * ty - ry * tx) < 2.1) target = Math.min(target, along < 7 ? 0 : Math.max(0, (o.v || 0) * 0.8));
      }
      n.v += clamp(target - n.v, -7 * dt, 2.4 * dt);
      if (n.v < 0) n.v = 0;
      n.s += n.dir * n.v * dt;
      if (n.s < 0 || n.s > road.len) {
        if (!this._npcTurn(n)) { n.dead = true; continue; }
      }
      const p = w.pointAt(n.road, n.s);
      n.ux = p.ux;
      n.uy = p.uy;
      const lane = n.road.oneway ? 0 : Math.min(n.road.spec.hw * 0.5, 1.8);
      const gx = p.x + p.uy * n.dir * lane;
      const gy = p.y - p.ux * n.dir * lane;
      // glide to the lane (after a turn the lane jumps sideways)
      const kk = Math.min(1, dt * 4);
      n.x += (gx - n.x) * kk + p.ux * n.dir * n.v * dt * (1 - kk);
      n.y += (gy - n.y) * kk + p.uy * n.dir * n.v * dt * (1 - kk);
      const hd = Math.atan2(p.ux * n.dir, p.uy * n.dir) / D2R;
      n.hd += angDiff(hd, n.hd) * Math.min(1, dt * 5);
      const d = Math.hypot(n.x - car.x, n.y - car.y);
      if (d > 330) n.dead = true;
      else if (d < 2.5 && this.rule.crashCool <= 0) {
        this._violate('התנגשות ברכב אחר', 25);
        this.rule.crashCool = 2.5;
        car.v *= -0.3;
        n.v = 0;
        const push = 2.6 - d;
        car.x -= ((n.x - car.x) / (d || 1)) * push;
        car.y -= ((n.y - car.y) / (d || 1)) * push;
      }
    }
    this.npcs = this.npcs.filter((n) => !n.dead);
  }

  _spawnNpc() {
    const car = this.car;
    const spot = this.world.randomSpot(car.x, car.y, 70, 240);
    if (!spot) return;
    const road = spot.road;
    const near = this.world.nearest(spot.x, spot.y, 5);
    if (!near) return;
    const dir = road.oneway || (Math.random() < 0.5 ? 1 : -1);
    if (this.npcs.some((n) => Math.hypot(n.x - spot.x, n.y - spot.y) < 14)) return;
    this.npcs.push({ road, s: near.s, dir, v: 4, x: spot.x, y: spot.y, ux: near.ux, uy: near.uy,
      hd: Math.atan2(near.ux * dir, near.uy * dir) / D2R, mesh: Math.floor(Math.random() * NPC_COLORS.length), pace: 0.7 + Math.random() * 0.25 });
  }

  /** End of a street: carry on into one of the streets that meet there. */
  _npcTurn(n) {
    const w = this.world;
    const road = n.road;
    const vi = n.dir > 0 ? road.xs.length - 1 : 0;
    const node = w.nodes[road.nodes[vi]];
    const tx = n.ux * n.dir;
    const ty = n.uy * n.dir;
    const opts = [];
    for (const a of node.arms) {
      const r = w.roads[a.road];
      if (r === road && a.vi === vi) continue;
      if (r.oneway && r.oneway !== a.step) continue;
      const straight = a.dx * tx + a.dy * ty;
      if (straight < -0.6) continue; // no U-turns
      opts.push({ a, r, wgt: 0.4 + straight + 1 + r.spec.rank * 0.2 });
    }
    if (!opts.length) return false;
    let pick = Math.random() * opts.reduce((s, o) => s + o.wgt, 0);
    let ch = opts[0];
    for (const o of opts) { pick -= o.wgt; if (pick <= 0) { ch = o; break; } }
    n.road = ch.r;
    n.dir = ch.a.step;
    n.s = ch.r.cum[ch.a.vi] + ch.a.step * 0.05;
    return true;
  }

  // ------------------------------------------------------------------ the traffic rules
  _violate(text, points) {
    this.score -= points;
    this.stats.violations++;
    this.stats.clean = 0;
    this.cleanMission = false;
    this._flash(`${text}  −${points}`, 'bad');
    this._tone(170, 0.35, 'sawtooth', 0.07);
  }

  _reward(text, points) {
    this.score += points;
    this._flash(`${text}  +${points}`, 'good');
    this._tone(880, 0.12, 'sine', 0.07);
    this._tone(1320, 0.2, 'sine', 0.07, 0.1);
  }

  _rules(dt) {
    const car = this.car;
    const r = this.rule;
    const info = this.info;
    const kmh = Math.abs(car.v) * 3.6;
    const fx = Math.sin(car.hd * D2R);
    const fy = Math.cos(car.hd * D2R);

    // 1. speed limit
    const over = info.onRoad && kmh > info.limit + 5;
    r.speeding = over ? r.speeding + dt : 0;
    if (r.speeding > 2.5) { r.speeding = 0; this._violate(`מהירות מופרזת! המותר כאן ${info.limit} קמ״ש`, kmh > info.limit + 30 ? 15 : 8); }

    // 2. off the road
    r.offroad = !info.onRoad && kmh > 8 ? r.offroad + dt : 0;
    if (r.offroad > 2) { r.offroad = 0; this._violate('נסיעה מחוץ לכביש', 5); }

    // 3. against a one-way street / against the roundabout
    const road = info.road;
    let wrong = false;
    if (road && car.v > 2.5) {
      if (road.rb >= 0) {
        const q = this.world.roundabouts[road.rb];
        if (q) wrong = (car.x - q.x) * fy - (car.y - q.y) * fx < -0.3 * Math.hypot(car.x - q.x, car.y - q.y);
      } else if (road.oneway) {
        wrong = (info.near.ux * fx + info.near.uy * fy) * road.oneway < -0.5;
      }
    }
    r.wrongWay = wrong ? r.wrongWay + dt : 0;
    if (r.wrongWay > 1.5) {
      r.wrongWay = 0;
      this._violate(road.rb >= 0 ? 'נסיעה נגד כיוון התנועה בכיכר' : 'נסיעה נגד כיוון התנועה ברחוב חד־סטרי', 12);
    }

    // 4. traffic lights: crossing a stop line on red; a bonus for waiting properly
    let ahead = null;
    for (const sig of this.scene.signals) {
      if (Math.abs(sig.x - car.x) > 90 || Math.abs(sig.y - car.y) > 90) continue;
      sig.approaches.forEach((ap, i) => {
        const key = `${sig.key}:${i}`;
        const along = (car.x - ap.x) * ap.dx + (car.y - ap.y) * ap.dy; // < 0 before the line
        const side = (car.x - ap.x) * ap.dy - (car.y - ap.y) * ap.dx; // > 0: right of the centre line
        const inLane = ap.oneway ? Math.abs(side) < ap.hw + 1 : side > -1.2 && side < ap.hw + 1.2;
        const facing = fx * ap.dx + fy * ap.dy > 0.5;
        const st = signalState(sig, ap.group, this.t);
        const prev = r.prev.get(key);
        if (prev !== undefined && prev < -1.5 && along >= -1.5 && inLane && facing && car.v > 0.5) {
          if (st === 'r') this._violate('מעבר באור אדום!', 30);
          else if (r.redStop.get(key)) { this._reward('המתנה כחוק ברמזור', 5); }
          r.redStop.delete(key);
        }
        r.prev.set(key, along);
        if (inLane && facing && along < -1.5 && along > -45) {
          if (st === 'r' && kmh < 1.5 && along > -14) r.redStop.set(key, true);
          if (!ahead || along > ahead.along) ahead = { along, st };
        }
      });
    }
    this.info.light = ahead ? ahead.st : null;

    // 5. law-abiding driving pays
    if (info.onRoad && !over && !wrong && car.v > 1) {
      this.stats.clean += car.v * dt;
      if (this.stats.clean >= 100) { this.stats.clean -= 100; this.score += 2; }
    }

    // stars
    for (const s of this.scene.stars) {
      if (!s.got && Math.abs(s.x - car.x) < 2.4 && Math.abs(s.y - car.y) < 2.4) {
        s.got = true;
        this.collected.add(s.id);
        this.stats.stars++;
        this.score += 5;
        this._tone(1175, 0.1, 'triangle', 0.07);
        this._tone(1568, 0.16, 'triangle', 0.07, 0.08);
      }
    }

    // mission
    const m = this.mission;
    if (m) {
      const d = Math.hypot(m.x - car.x, m.y - car.y);
      m.dist = d;
      if (d < 7 && kmh < 30) {
        this.stats.missions++;
        const bonus = this.cleanMission ? 30 : 0;
        this._reward(bonus ? 'הגעת ליעד בנהיגה למופת!' : 'הגעת ליעד!', 50 + bonus);
        this._newMission();
      } else if (d > 1500) this._newMission();
    } else if ((this.missionRetry = (this.missionRetry || 0) - dt) <= 0) {
      this.missionRetry = 3;
      this._newMission();
    }
  }

  _newMission() {
    const car = this.car;
    const spot = this.world.randomSpot(car.x, car.y, 220, 650) || this.world.randomSpot(car.x, car.y, 90, 900);
    this.cleanMission = true;
    if (!spot) { this.mission = null; return; }
    const name = this.world.nameAt(spot.x, spot.y, 20);
    const kinds = ['איסוף נוסע', 'משלוח חבילה', 'הסעת תלמיד', 'מסירת מכתב', 'איסוף קניות'];
    this.mission = { x: spot.x, y: spot.y, name, kind: kinds[this.stats.missions % kinds.length], dist: 0 };
  }

  // ------------------------------------------------------------------ frame
  _frame(now) {
    if (!this.on) return;
    const dt = Math.min(0.05, Math.max(0.001, (now - this.last) / 1000));
    this.last = now;
    this.t += dt;
    if (this.running) {
      this._physics(dt);
      this._npcs(dt);
      this._rules(dt);
      this.bldTimer -= dt;
      if (this.bldTimer <= 0) { this.bldTimer = 0.25; this._scanBuildings(); }
    }
    this._ingestStep();
    this._lamps();
    this._camera(dt);
    this._hud();
    this.map.triggerRepaint();
    this.raf = requestAnimationFrame(this._frame);
  }

  _lamps() {
    const sigs = this.scene.signals;
    const states = sigs.map((s) => [signalState(s, 0, this.t), signalState(s, 1, this.t)]);
    const sig = states.map((s) => s.join('')).join('');
    if (sig !== this.lampSig) {
      this.lampSig = sig;
      this.layer.setMesh('lamps', signalLampMesh(sigs, states));
    }
  }

  _camera(dt) {
    const car = this.car;
    const c = CAMERAS[this.cam.mode];
    const cam = this.cam;
    const k = Math.min(1, dt * 3.2);
    cam.bearing += angDiff(car.hd, cam.bearing) * Math.min(1, dt * 4.5);
    cam.zoom += (c.zoom - Math.min(0.55, Math.abs(car.v) / 55) - cam.zoom) * k;
    cam.pitch += (c.pitch - cam.pitch) * k;
    const look = c.look + Math.max(0, car.v) * 0.25;
    const bx = Math.sin(cam.bearing * D2R);
    const by = Math.cos(cam.bearing * D2R);
    this.map.jumpTo({ center: this.toLngLat(car.x + bx * look, car.y + by * look), bearing: cam.bearing, zoom: cam.zoom, pitch: cam.pitch });
  }

  _draw(L) {
    if (!this.on || !this.car) return;
    const base = M4.chain(L.proj, M4.trans(this.ox, this.oy, 0), M4.scale(this.ms, -this.ms, this.ms));
    const car = this.car;
    const carM = M4.mul(M4.trans(car.x, car.y, 0), M4.rotZ(-car.hd * D2R));
    L.draw('decals', base);
    L.draw('solid', base);
    L.draw('lamps', base);
    L.draw('car', base, carM);
    if (car.braking) L.draw('brake', base, carM);
    const lock = 0.6 / (1 + (Math.abs(car.v) / 9) ** 1.25);
    for (const sx of [-1, 1]) {
      for (const front of [true, false]) {
        const wm = M4.chain(carM, M4.trans(sx * 0.8, front ? 1.36 : -1.3, 0.33), M4.rotZ(front ? -car.steer * lock : 0),
          M4.rotX(-car.spin), M4.scale(sx, 1, 1));
        L.draw('wheel', base, wm);
      }
    }
    for (const n of this.npcs) L.draw(`npc${n.mesh}`, base, M4.mul(M4.trans(n.x, n.y, 0), M4.rotZ(-n.hd * D2R)));
    const spin = this.t * 2.4;
    for (const s of this.scene.stars) {
      if (s.got || Math.abs(s.x - car.x) > 220 || Math.abs(s.y - car.y) > 220) continue;
      L.draw('star', base, M4.chain(M4.trans(s.x, s.y, 1.15 + Math.sin(this.t * 3 + s.x) * 0.12), M4.rotZ(spin + s.x)));
    }
    // transparent things last
    L.draw('glow', base, null, { blend: true });
    L.draw('shadow', base, carM, { blend: true });
    for (const n of this.npcs) L.draw('shadow', base, M4.mul(M4.trans(n.x, n.y, 0), M4.rotZ(-n.hd * D2R)), { blend: true });
    if (this.night) L.draw('beams', base, carM, { blend: true });
    const m = this.mission;
    if (m) L.draw('beacon', base, M4.trans(m.x, m.y, 0), { blend: true, alpha: 0.55 + 0.25 * Math.sin(this.t * 4) });
  }

  // ------------------------------------------------------------------ HUD
  _buildHud() {
    const el = document.createElement('div');
    el.id = 'game-root';
    el.className = 'game';
    el.dir = 'rtl';
    const ticks = [];
    for (let v = 0; v <= 180; v += 10) {
      const a = (-120 + (v / 180) * 240) * D2R;
      const big = v % 20 === 0;
      const r0 = big ? 70 : 75;
      ticks.push(`<line x1="${(100 + r0 * Math.sin(a)).toFixed(1)}" y1="${(100 - r0 * Math.cos(a)).toFixed(1)}" x2="${(100 + 82 * Math.sin(a)).toFixed(1)}" y2="${(100 - 82 * Math.cos(a)).toFixed(1)}" class="${big ? 'tk big' : 'tk'}"/>`);
      if (v % 40 === 0) ticks.push(`<text x="${(100 + 57 * Math.sin(a)).toFixed(1)}" y="${(100 - 57 * Math.cos(a) + 4).toFixed(1)}" class="tl">${v}</text>`);
    }
    el.innerHTML = `
      <div class="game-top">
        <div class="game-card game-score" aria-live="off">
          <div class="gs-main"><span class="gs-label">ניקוד</span><b data-g="score">0</b></div>
          <div class="gs-row"><span title="כוכבים">★ <b data-g="stars">0</b></span><span title="יעדים">⚑ <b data-g="missions">0</b></span><span title="עבירות">⚠ <b data-g="viol">0</b></span><span><b data-g="km">0.0</b> ק״מ</span></div>
        </div>
        <div class="game-card game-mission" data-g="mission" hidden>
          <span class="gm-arrow" data-g="arrow" aria-hidden="true"><svg viewBox="0 0 24 24" width="30" height="30"><path d="M12 2l7 18-7-4-7 4z" fill="currentColor"/></svg></span>
          <div><div class="gm-kind" data-g="mkind"></div><div class="gm-dist"><b data-g="mdist"></b><span data-g="mname"></span></div></div>
        </div>
        <div class="game-btns">
          <button type="button" data-g="exit" class="game-btn game-exit" title="יציאה ממצב המשחק (Esc)">✕ יציאה</button>
          <button type="button" data-g="cam" class="game-btn" title="החלפת מצלמה (C)">מצלמה</button>
          <button type="button" data-g="sound" class="game-btn" aria-pressed="true" title="צלילים (M)">צליל</button>
          <button type="button" data-g="reset" class="game-btn" title="החזרת הרכב לכביש (R)">לכביש</button>
          <button type="button" data-g="help" class="game-btn" title="עזרה">?</button>
        </div>
      </div>
      <div class="game-msgs" data-g="msgs" role="status" aria-live="polite"></div>
      <div class="game-dash">
        <div class="game-light" data-g="light" hidden><i class="r"></i><i class="y"></i><i class="g"></i></div>
        <div class="game-speedo">
          <svg viewBox="0 0 200 200" aria-hidden="true">
            <circle cx="100" cy="100" r="92" class="sp-bg"/>
            <path d="M 28.4 141.5 A 82 82 0 1 1 171.6 141.5" class="sp-arc"/>
            <path d="" class="sp-over" data-g="over"/>
            ${ticks.join('')}
            <g data-g="needle" class="sp-needle"><path d="M100 100 L97.5 100 L100 26 L102.5 100 Z"/></g>
            <circle cx="100" cy="100" r="7" class="sp-hub"/>
          </svg>
          <div class="sp-num"><b data-g="kmh">0</b><span>קמ״ש</span></div>
          <div class="sp-gear" data-g="gear">P</div>
        </div>
        <div class="game-limit" title="המהירות המותרת"><b data-g="limit">50</b></div>
        <div class="game-street" data-g="street" hidden></div>
      </div>
      <div class="game-touch" aria-hidden="true">
        <div class="gt-side"><button type="button" data-k="left">◀</button><button type="button" data-k="right">▶</button></div>
        <div class="gt-side"><button type="button" data-k="down" class="gt-brake">בלם</button><button type="button" data-k="up" class="gt-gas">גז</button></div>
      </div>
      <div class="game-loading" data-g="loading" hidden><div class="gl-spin"></div><p data-g="loadtext"></p></div>
      <div class="game-paused" hidden><div class="game-panel"><h2>המשחק מושהה</h2><p>לחצו P כדי להמשיך</p></div></div>
      <div class="game-intro" hidden>
        <div class="game-panel" role="dialog" aria-labelledby="game-title">
          <h2 id="game-title">מצב משחק – נוהגים במפה</h2>
          <p class="gi-sub">הרחובות, הכיכרות והצמתים הם של המפה האמיתית. נסעו ליעד המסומן בקרן הירוקה, אספו כוכבים, ושמרו על חוקי התנועה.</p>
          <div class="gi-grid">
            <div><h3>נהיגה</h3><ul>
              <li><kbd>↑</kbd> / <kbd>W</kbd> גז</li><li><kbd>↓</kbd> / <kbd>S</kbd> בלם ונסיעה לאחור</li>
              <li><kbd>←</kbd> <kbd>→</kbd> / <kbd>A</kbd> <kbd>D</kbd> הגה</li><li><kbd>רווח</kbd> בלם יד</li>
              <li><kbd>C</kbd> מצלמה · <kbd>H</kbd> צופר · <kbd>R</kbd> חזרה לכביש</li><li><kbd>P</kbd> השהיה · <kbd>M</kbd> צליל · <kbd>Esc</kbd> יציאה</li></ul></div>
            <div><h3>חוקי התנועה</h3><ul>
              <li>מהירות: 50 בעיר, 80 מחוץ לעיר, 90 בדרך ראשית, 110 בדרך מהירה, 30 בכיכר</li>
              <li>עוצרים באור אדום לפני קו העצירה</li><li>בכיכר נוסעים נגד כיוון השעון</li>
              <li>לא נוסעים נגד הכיוון ברחוב חד־סטרי</li><li>נשארים על הכביש ושומרים מרחק</li></ul></div>
          </div>
          <p class="gi-best" data-g="best"></p>
          <button type="button" class="game-start" data-g="start">התחל לנסוע</button>
        </div>
      </div>`;
    (document.getElementById('main') || document.body).appendChild(el);
    this.root = el;
    this.ui = {};
    el.querySelectorAll('[data-g]').forEach((n) => { this.ui[n.dataset.g] = n; });
    this.shown = {};
    this.ui.exit.addEventListener('click', () => this.stop());
    this.ui.cam.addEventListener('click', () => this._nextCamera());
    this.ui.sound.addEventListener('click', () => this._toggleSound());
    this.ui.reset.addEventListener('click', () => this._reset());
    this.ui.help.addEventListener('click', () => { if (this.running) { this.running = false; this._showIntro(); } });
    this.ui.start.addEventListener('click', () => this._begin());
    el.querySelectorAll('[data-k]').forEach((b) => {
      const set = (v) => (e) => {
        e.preventDefault();
        this.keys[b.dataset.k] = v;
        if (v && !this.running && !this.paused && this.on) this._begin();
      };
      b.addEventListener('pointerdown', set(true));
      for (const ev of ['pointerup', 'pointercancel', 'pointerleave']) b.addEventListener(ev, set(false));
    });
    el.addEventListener('contextmenu', (e) => e.preventDefault());
  }

  _setLoading(text) {
    if (!this.root) return;
    this.ui.loading.hidden = !text;
    if (text) this.ui.loadtext.textContent = text;
  }

  _showIntro() {
    this.ui.sound.setAttribute('aria-pressed', String(this.sound));
    this.ui.best.textContent = this.best > 0 ? `השיא שלך: ${this.best} נקודות` : '';
    this.root.querySelector('.game-intro').hidden = false;
    this.ui.start.focus({ preventScroll: true });
  }

  _flash(text, kind) {
    const host = this.ui?.msgs;
    if (!host) return;
    const m = document.createElement('div');
    m.className = `game-msg ${kind}`;
    m.textContent = text;
    host.prepend(m);
    while (host.children.length > 3) host.lastChild.remove();
    setTimeout(() => m.classList.add('out'), 2600);
    setTimeout(() => m.remove(), 3100);
  }

  _set(key, value, fn) {
    if (this.shown[key] === value) return;
    this.shown[key] = value;
    fn(this.ui[key], value);
  }

  _hud() {
    const car = this.car;
    const info = this.info;
    const kmh = Math.abs(car.v) * 3.6;
    const text = (n, v) => { n.textContent = v; };
    this._set('kmh', Math.round(kmh), text);
    this._set('needle', Math.round(clamp(kmh, 0, 180) * 2) / 2, (n, v) => n.setAttribute('transform', `rotate(${-120 + (v / 180) * 240} 100 100)`));
    this._set('limit', info.limit, (n, v) => {
      n.textContent = v;
      // the red zone of the dial starts at the limit
      const a0 = (-120 + (v / 180) * 240) * D2R;
      const a1 = 120 * D2R;
      const p = (a) => `${(100 + 82 * Math.sin(a)).toFixed(1)} ${(100 - 82 * Math.cos(a)).toFixed(1)}`;
      this.ui.over.setAttribute('d', `M ${p(a0)} A 82 82 0 ${a1 - a0 > Math.PI ? 1 : 0} 1 ${p(a1)}`);
    });
    const over = info.onRoad && kmh > info.limit + 5;
    this._set('gear', car.v < -0.3 ? 'R' : kmh < 0.5 ? 'P' : 'D', text);
    if (this.shown.over !== over) { this.shown.over = over; this.root.classList.toggle('speeding', over); }
    this._set('score', Math.round(this.score), text);
    this._set('stars', this.stats.stars, text);
    this._set('missions', this.stats.missions, text);
    this._set('viol', this.stats.violations, text);
    this._set('km', (this.stats.dist / 1000).toFixed(1), text);
    this._set('street', info.onRoad ? info.name : 'מחוץ לכביש', (n, v) => { n.textContent = v; n.hidden = !v; });
    this._set('light', info.light || '', (n, v) => { n.hidden = !v; n.dataset.s = v; });
    const m = this.mission;
    this._set('mission', !!m, (n, v) => { n.hidden = !v; });
    if (m) {
      const d = m.dist || Math.hypot(m.x - car.x, m.y - car.y);
      this._set('mkind', m.kind, text);
      this._set('mname', m.name ? ` · ${m.name}` : '', text);
      this._set('mdist', d >= 1000 ? `${(d / 1000).toFixed(1)} ק״מ` : `${Math.round(d / 10) * 10} מ׳`, text);
      const brg = Math.atan2(m.x - car.x, m.y - car.y) / D2R;
      this._set('arrow', Math.round(angDiff(brg, this.cam.bearing) / 3) * 3, (n, v) => { n.style.transform = `rotate(${v}deg)`; });
    }
  }
}
