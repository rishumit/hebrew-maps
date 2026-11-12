// Game mode – the 3D part: a small WebGL renderer that draws inside the map (MapLibre custom layer),
// and the models: the car, traffic lights, street lamps, trees, signs, roundabout islands.
// Everything is built from boxes, cylinders and spheres in code – no model files, no extra library.

// ---------------------------------------------------------------- 4×4 matrices (column-major, doubles)
export const M4 = {
  mul(a, b) {
    const o = new Array(16);
    for (let c = 0; c < 4; c++) {
      for (let r = 0; r < 4; r++) {
        o[c * 4 + r] = a[r] * b[c * 4] + a[4 + r] * b[c * 4 + 1] + a[8 + r] * b[c * 4 + 2] + a[12 + r] * b[c * 4 + 3];
      }
    }
    return o;
  },
  ident: () => [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
  trans: (x, y, z) => [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, x, y, z, 1],
  scale: (x, y, z) => [x, 0, 0, 0, 0, y, 0, 0, 0, 0, z, 0, 0, 0, 0, 1],
  rotZ(a) { const c = Math.cos(a), s = Math.sin(a); return [c, s, 0, 0, -s, c, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]; },
  rotX(a) { const c = Math.cos(a), s = Math.sin(a); return [1, 0, 0, 0, 0, c, s, 0, 0, -s, c, 0, 0, 0, 0, 1]; },
  rotY(a) { const c = Math.cos(a), s = Math.sin(a); return [c, 0, -s, 0, 0, 1, 0, 0, s, 0, c, 0, 0, 0, 0, 1]; },
  chain(...ms) { return ms.reduce((a, b) => M4.mul(a, b)); },
};

export const rgb = (hex) => [((hex >> 16) & 255) / 255, ((hex >> 8) & 255) / 255, (hex & 255) / 255];
const shade = (c, k) => [Math.min(1, c[0] * k), Math.min(1, c[1] * k), Math.min(1, c[2] * k)];

// ---------------------------------------------------------------- mesh builder
// vertex = position(3) normal(3) colour(4) emissive(1) uv(2)
export const STRIDE = 13;

export class MeshBuilder {
  constructor() {
    this.d = [];
    this.m = M4.ident();
    this.stack = [];
    this.e = 0; // emissive: 1 = glows, not shaded
    this.a = 1; // alpha
  }

  push() { this.stack.push(this.m); return this; }
  pop() { this.m = this.stack.pop(); return this; }
  tf(mat) { this.m = M4.mul(this.m, mat); return this; }
  at(x, y, z = 0, rz = 0) { this.push(); this.tf(M4.trans(x, y, z)); if (rz) this.tf(M4.rotZ(rz)); return this; }

  v(x, y, z, nx, ny, nz, c, a = this.a, u = -1, w = -1) {
    const m = this.m;
    let X = m[0] * nx + m[4] * ny + m[8] * nz;
    let Y = m[1] * nx + m[5] * ny + m[9] * nz;
    let Z = m[2] * nx + m[6] * ny + m[10] * nz;
    const l = Math.hypot(X, Y, Z) || 1;
    X /= l; Y /= l; Z /= l;
    this.d.push(m[0] * x + m[4] * y + m[8] * z + m[12], m[1] * x + m[5] * y + m[9] * z + m[13], m[2] * x + m[6] * y + m[10] * z + m[14],
      X, Y, Z, c[0], c[1], c[2], a, this.e, u, w);
  }

  /** Flat quad; corners counter-clockwise seen from the front. */
  quad(p0, p1, p2, p3, c, uv = null) {
    const ax = p1[0] - p0[0], ay = p1[1] - p0[1], az = p1[2] - p0[2];
    const bx = p3[0] - p0[0], by = p3[1] - p0[1], bz = p3[2] - p0[2];
    const nx = ay * bz - az * by, ny = az * bx - ax * bz, nz = ax * by - ay * bx;
    const P = [p0, p1, p2, p0, p2, p3];
    const U = uv ? [uv[0], uv[1], uv[2], uv[0], uv[2], uv[3]] : null;
    for (let i = 0; i < 6; i++) this.v(P[i][0], P[i][1], P[i][2], nx, ny, nz, c, this.a, U ? U[i][0] : -1, U ? U[i][1] : -1);
  }

  /** Box whose top rectangle may differ from the bottom one (car bodies, roofs). rect = [x0, y0, x1, y1]. */
  frustum(z0, z1, b, t, c, { bottom = false } = {}) {
    const B = [[b[0], b[1], z0], [b[2], b[1], z0], [b[2], b[3], z0], [b[0], b[3], z0]];
    const T = [[t[0], t[1], z1], [t[2], t[1], z1], [t[2], t[3], z1], [t[0], t[3], z1]];
    for (let i = 0; i < 4; i++) { const j = (i + 1) % 4; this.quad(B[i], B[j], T[j], T[i], c); }
    this.quad(T[0], T[1], T[2], T[3], c);
    if (bottom) this.quad(B[3], B[2], B[1], B[0], c);
  }

  box(x0, y0, z0, x1, y1, z1, c) { this.frustum(z0, z1, [x0, y0, x1, y1], [x0, y0, x1, y1], c, { bottom: true }); }

  /** Cylinder / cone along Z around the origin. */
  cyl(r0, r1, z0, z1, c, seg = 10, cap = true) {
    const slope = (r0 - r1) / (z1 - z0 || 1);
    for (let i = 0; i < seg; i++) {
      const a0 = (i / seg) * 2 * Math.PI, a1 = ((i + 1) / seg) * 2 * Math.PI;
      const c0 = Math.cos(a0), s0 = Math.sin(a0), c1 = Math.cos(a1), s1 = Math.sin(a1);
      const P = [[r0 * c0, r0 * s0, z0, c0, s0], [r0 * c1, r0 * s1, z0, c1, s1], [r1 * c1, r1 * s1, z1, c1, s1], [r1 * c0, r1 * s0, z1, c0, s0]];
      for (const k of [0, 1, 2, 0, 2, 3]) this.v(P[k][0], P[k][1], P[k][2], P[k][3], P[k][4], slope, c);
      if (cap && r1 > 0.001) { this.v(0, 0, z1, 0, 0, 1, c); this.v(r1 * c0, r1 * s0, z1, 0, 0, 1, c); this.v(r1 * c1, r1 * s1, z1, 0, 0, 1, c); }
      if (cap && r0 > 0.001) { this.v(0, 0, z0, 0, 0, -1, c); this.v(r0 * c1, r0 * s1, z0, 0, 0, -1, c); this.v(r0 * c0, r0 * s0, z0, 0, 0, -1, c); }
    }
  }

  sphere(r, c, seg = 9, rings = 6, sz = 1) {
    const pt = (i, j) => {
      const th = (j / rings) * Math.PI, ph = (i / seg) * 2 * Math.PI;
      const x = Math.sin(th) * Math.cos(ph), y = Math.sin(th) * Math.sin(ph), z = Math.cos(th);
      return [x * r, y * r, z * r * sz, x, y, z / sz];
    };
    for (let j = 0; j < rings; j++) {
      for (let i = 0; i < seg; i++) {
        const P = [pt(i, j + 1), pt(i + 1, j + 1), pt(i + 1, j), pt(i, j)];
        for (const k of [0, 1, 2, 0, 2, 3]) this.v(P[k][0], P[k][1], P[k][2], P[k][3], P[k][4], P[k][5], c);
      }
    }
  }

  /** Flat disc on the ground; alpha can fade from the centre to the rim (soft shadows, light pools). */
  disc(rx, ry, z, c, aCenter = 1, aEdge = 1, seg = 18) {
    for (let i = 0; i < seg; i++) {
      const a0 = (i / seg) * 2 * Math.PI, a1 = ((i + 1) / seg) * 2 * Math.PI;
      this.v(0, 0, z, 0, 0, 1, c, aCenter);
      this.v(rx * Math.cos(a0), ry * Math.sin(a0), z, 0, 0, 1, c, aEdge);
      this.v(rx * Math.cos(a1), ry * Math.sin(a1), z, 0, 0, 1, c, aEdge);
    }
  }

  data() { return new Float32Array(this.d); }
}

// ---------------------------------------------------------------- models
const TYRE = rgb(0x17181C);
const STEEL = rgb(0x8E949E);
const DARK = rgb(0x24272E);

/** One wheel around the origin, axle along X. Drawn 4 times per car. */
export function wheelMesh() {
  const b = new MeshBuilder();
  b.push().tf(M4.rotY(Math.PI / 2));
  b.cyl(0.33, 0.33, -0.12, 0.12, TYRE, 14);
  b.cyl(0.2, 0.2, -0.125, 0.125, rgb(0xB9BEC8), 10);
  b.pop();
  // spokes, so the turning of the wheel can be seen
  for (let i = 0; i < 5; i++) {
    b.push().tf(M4.rotX((i / 5) * 2 * Math.PI));
    b.box(-0.13, -0.03, 0, 0.13, 0.03, 0.2, DARK);
    b.pop();
  }
  return b.data();
}

/** A car around the origin: +Y forward, +X right, wheels touch z=0. `wheels`: bake fixed wheels (parked / other cars). */
export function carMesh(body, { wheels = false, taxi = false } = {}) {
  const b = new MeshBuilder();
  const glass = rgb(0x1B2733);
  const trim = rgb(0x1A1C21);
  const W = 0.9; // half width
  // sills + lower body, with a nose that drops and a boot that rises
  b.frustum(0.2, 0.46, [-W + 0.04, -2.12, W - 0.04, 2.14], [-W, -2.15, W, 2.17], shade(body, 0.82), { bottom: true });
  b.frustum(0.46, 0.78, [-W, -2.15, W, 2.17], [-W + 0.05, -2.08, W - 0.05, 2.02], body);
  b.frustum(0.78, 0.86, [-W + 0.05, -2.08, W - 0.05, 2.02], [-W + 0.1, -1.95, W - 0.1, 1.0], body); // bonnet / boot line
  // cabin: glass all round, roof in the body colour
  b.frustum(0.84, 1.36, [-W + 0.08, -1.55, W - 0.08, 0.95], [-W + 0.24, -1.05, W - 0.24, 0.25], glass);
  b.frustum(1.36, 1.41, [-W + 0.24, -1.05, W - 0.24, 0.25], [-W + 0.28, -0.98, W - 0.28, 0.18], body);
  // pillars
  for (const sx of [-1, 1]) {
    b.frustum(0.84, 1.37, [sx * (W - 0.1) - 0.03, -0.34, sx * (W - 0.1) + 0.03, -0.24], [sx * (W - 0.25) - 0.03, -0.34, sx * (W - 0.25) + 0.03, -0.24], body);
    // mirrors
    b.box(sx * (W + 0.02) - 0.07, 0.72, 0.86, sx * (W + 0.02) + 0.07, 0.84, 0.98, body);
    // wheel arches
    for (const y of [1.36, -1.3]) b.box(sx * W - 0.03 * (sx > 0 ? 1 : -1) - 0.02, y - 0.42, 0.2, sx * W + 0.02, y + 0.42, 0.7, trim);
  }
  // bumpers, grille
  b.box(-W + 0.03, 2.1, 0.22, W - 0.03, 2.22, 0.5, trim);
  b.box(-W + 0.03, -2.2, 0.22, W - 0.03, -2.1, 0.5, trim);
  b.box(-0.42, 2.14, 0.5, 0.42, 2.19, 0.68, rgb(0x0E0F12));
  // number plates (yellow, as in Israel)
  b.e = 0.55;
  b.box(-0.26, 2.2, 0.3, 0.26, 2.235, 0.42, rgb(0xF7D417));
  b.box(-0.26, -2.215, 0.52, 0.26, -2.19, 0.64, rgb(0xF7D417));
  // lamps
  b.e = 0.9;
  for (const sx of [-1, 1]) {
    b.box(sx * 0.62 - 0.2, 2.1, 0.56, sx * 0.62 + 0.2, 2.2, 0.7, rgb(0xFFF6D8));
    b.e = 0.45;
    b.box(sx * 0.64 - 0.2, -2.18, 0.6, sx * 0.64 + 0.2, -2.1, 0.74, rgb(0xB3121A));
    b.e = 0.9;
  }
  b.e = 0;
  if (taxi) {
    b.e = 0.8;
    b.box(-0.22, -0.5, 1.41, 0.22, -0.3, 1.55, rgb(0xFFD21F));
    b.e = 0;
  }
  if (wheels) {
    for (const sx of [-1, 1]) {
      for (const y of [1.36, -1.3]) {
        b.at(sx * 0.8, y, 0.33).tf(M4.rotY(Math.PI / 2));
        b.cyl(0.33, 0.33, -0.12, 0.12, TYRE, 10);
        b.cyl(0.2, 0.2, -0.125, 0.125, rgb(0xB9BEC8), 8);
        b.pop();
      }
    }
  }
  return b.data();
}

/** Brake lights (drawn over the tail lamps while braking) and the reversing lamp. */
export function brakeMesh() {
  const b = new MeshBuilder();
  b.e = 1;
  for (const sx of [-1, 1]) b.box(sx * 0.64 - 0.21, -2.2, 0.59, sx * 0.64 + 0.21, -2.09, 0.75, rgb(0xFF2B2B));
  b.box(-0.3, -1.6, 1.3, 0.3, -1.56, 1.34, rgb(0xFF2B2B));
  return b.data();
}

/** Soft shadow under a car + (at night) the pools of the headlights on the road. Transparent pass. */
export function carShadowMesh() {
  const b = new MeshBuilder();
  b.disc(1.35, 2.75, 0.03, [0, 0, 0], 0.5, 0);
  return b.data();
}
export function headlightMesh() {
  const b = new MeshBuilder();
  b.e = 1;
  const c = rgb(0xFFF1C2);
  for (const sx of [-0.6, 0.6]) {
    b.v(sx, 2.2, 0.05, 0, 0, 1, c, 0.5);
    b.v(sx - 2.6, 17, 0.05, 0, 0, 1, c, 0);
    b.v(sx + 2.6, 17, 0.05, 0, 0, 1, c, 0);
  }
  return b.data();
}

/** Gold star to collect, around the origin (it spins). */
export function starMesh() {
  const b = new MeshBuilder();
  b.e = 0.75;
  const gold = rgb(0xFFC928);
  const lite = rgb(0xFFE680);
  const P = [];
  for (let i = 0; i < 10; i++) {
    const r = i % 2 ? 0.3 : 0.72;
    const a = (i / 10) * 2 * Math.PI + Math.PI / 2;
    P.push([r * Math.cos(a), 0, r * Math.sin(a)]);
  }
  for (const s of [1, -1]) {
    const apex = [0, 0.2 * s, 0];
    for (let i = 0; i < 10; i++) {
      const p = P[i], q = P[(i + 1) % 10];
      const c = i % 2 ? gold : lite;
      const [t0, t1] = s > 0 ? [q, p] : [p, q];
      const nx = (t0[1] - apex[1]) * (t1[2] - apex[2]) - (t0[2] - apex[2]) * (t1[1] - apex[1]);
      const ny = (t0[2] - apex[2]) * (t1[0] - apex[0]) - (t0[0] - apex[0]) * (t1[2] - apex[2]);
      const nz = (t0[0] - apex[0]) * (t1[1] - apex[1]) - (t0[1] - apex[1]) * (t1[0] - apex[0]);
      for (const p3 of [apex, t0, t1]) b.v(p3[0], p3[1], p3[2], nx, ny, nz, c);
    }
  }
  return b.data();
}

/** The beam that marks the mission target. Transparent pass. */
export function beaconMesh() {
  const b = new MeshBuilder();
  b.e = 1;
  const c = rgb(0x35E08A);
  const seg = 20;
  for (let i = 0; i < seg; i++) {
    const a0 = (i / seg) * 2 * Math.PI, a1 = ((i + 1) / seg) * 2 * Math.PI;
    const p = (a, z) => [3.2 * Math.cos(a), 3.2 * Math.sin(a), z];
    const P = [[p(a0, 0), 0.6], [p(a1, 0), 0.6], [p(a1, 38), 0], [p(a0, 38), 0]];
    for (const k of [0, 1, 2, 0, 2, 3]) b.v(P[k][0][0], P[k][0][1], P[k][0][2], 0, 0, 1, c, P[k][1]);
  }
  b.disc(3.2, 3.2, 0.06, c, 0.15, 0.55, 24);
  return b.data();
}

// sign faces of the texture atlas (see signAtlas)
export const SIGN_CELLS = ['30', '50', '60', '70', '80', '90', '110', 'rb', 'yield'];

/** Draws the faces of the road signs on a canvas: Israeli speed limit discs, roundabout, give way. */
export function signAtlas(doc = document) {
  const S = 128;
  const cv = doc.createElement('canvas');
  cv.width = S * SIGN_CELLS.length;
  cv.height = S;
  const g = cv.getContext('2d');
  SIGN_CELLS.forEach((k, i) => {
    g.save();
    g.translate(i * S + S / 2, S / 2);
    if (k === 'yield') {
      g.beginPath(); g.moveTo(-58, -50); g.lineTo(58, -50); g.lineTo(0, 56); g.closePath();
      g.fillStyle = '#D81E2C'; g.fill();
      g.beginPath(); g.moveTo(-36, -37); g.lineTo(36, -37); g.lineTo(0, 30); g.closePath();
      g.fillStyle = '#FFFFFF'; g.fill();
    } else if (k === 'rb') {
      g.beginPath(); g.arc(0, 0, 60, 0, 7); g.fillStyle = '#FFFFFF'; g.fill();
      g.beginPath(); g.arc(0, 0, 55, 0, 7); g.fillStyle = '#1757C2'; g.fill();
      g.strokeStyle = '#FFFFFF'; g.fillStyle = '#FFFFFF'; g.lineWidth = 9;
      for (let a = 0; a < 3; a++) { // three arrows chasing each other counter-clockwise
        g.save(); g.rotate((a * 2 * Math.PI) / 3);
        g.beginPath(); g.arc(0, 0, 30, -0.3, -1.7, true); g.stroke();
        g.translate(30 * Math.cos(-1.7), 30 * Math.sin(-1.7)); g.rotate(-1.7 - Math.PI / 2);
        g.beginPath(); g.moveTo(0, -15); g.lineTo(17, 0); g.lineTo(0, 15); g.closePath(); g.fill();
        g.restore();
      }
    } else {
      g.beginPath(); g.arc(0, 0, 60, 0, 7); g.fillStyle = '#D81E2C'; g.fill();
      g.beginPath(); g.arc(0, 0, 45, 0, 7); g.fillStyle = '#FFFFFF'; g.fill();
      g.fillStyle = '#111111'; g.textAlign = 'center'; g.textBaseline = 'middle';
      g.font = `bold ${k.length > 2 ? 44 : 56}px Arial, Helvetica, sans-serif`;
      g.fillText(k, 0, 4);
    }
    g.restore();
  });
  return cv;
}

function addTree(b, t) {
  const s = t.size;
  const trunk = rgb(0x6B4A2F);
  b.at(t.x, t.y, 0, t.tint * 6);
  if (t.kind === 'cypress') {
    b.cyl(0.14 * s, 0.12 * s, 0, 1.0 * s, trunk, 6, false);
    b.cyl(0.95 * s, 0.05, 0.8 * s, 8.5 * s, shade(rgb(0x2F6B3C), 0.85 + t.tint * 0.3), 8);
  } else if (t.kind === 'palm') {
    b.cyl(0.26, 0.17, 0, 6.2 * s, rgb(0x8A6B45), 7, false);
    const green = shade(rgb(0x3E8F45), 0.9 + t.tint * 0.2);
    for (let i = 0; i < 7; i++) {
      b.push().tf(M4.trans(0, 0, 6.2 * s)).tf(M4.rotZ((i / 7) * 2 * Math.PI)).tf(M4.rotX(-0.38));
      b.frustum(-0.04, 0.04, [-0.34, 0, 0.34, 3.1], [-0.3, 0, 0.3, 3.1], green, { bottom: true });
      b.pop();
    }
    b.push().tf(M4.trans(0, 0, 6.1 * s)); b.sphere(0.36, rgb(0x7A5A2E), 6, 4); b.pop();
  } else {
    const green = shade(rgb(0x4C9A4A), 0.78 + t.tint * 0.4);
    b.cyl(0.2 * s, 0.15 * s, 0, 2.3 * s, trunk, 6, false);
    b.push().tf(M4.trans(0, 0, 3.5 * s)); b.sphere(1.9 * s, green, 8, 5, 0.85); b.pop();
    b.push().tf(M4.trans(0.8 * s, 0.3 * s, 4.5 * s)); b.sphere(1.25 * s, shade(green, 1.12), 7, 4, 0.9); b.pop();
    b.push().tf(M4.trans(-0.7 * s, -0.5 * s, 4.2 * s)); b.sphere(1.1 * s, shade(green, 0.9), 7, 4, 0.9); b.pop();
  }
  b.pop();
}

function addLamp(b, l, night) {
  const rz = Math.atan2(l.dy, l.dx) - Math.PI / 2; // local +Y = towards the road
  b.at(l.x, l.y, 0, rz);
  b.cyl(0.11, 0.07, 0, 7.4, STEEL, 6, false);
  b.cyl(0.2, 0.14, 0, 0.5, DARK, 6);
  b.box(-0.05, 0, 7.3, 0.05, 2.1, 7.42, STEEL);
  b.box(-0.2, 1.4, 7.2, 0.2, 2.3, 7.34, DARK);
  b.e = night ? 1 : 0.25;
  b.box(-0.15, 1.5, 7.14, 0.15, 2.2, 7.2, night ? rgb(0xFFE9A8) : rgb(0xF2F2EC));
  b.e = 0;
  b.pop();
}

function addSign(b, s) {
  // (fx, fy): the way the sign looks – towards the driver who reads it
  const rz = Math.atan2(s.fy, s.fx) + Math.PI / 2; // local -Y = facing direction
  const z = s.low ? 1.55 : 2.45;
  const half = s.kind === 'yield' ? 0.45 : 0.38;
  const i = SIGN_CELLS.indexOf(s.kind);
  if (i < 0) return;
  const u0 = i / SIGN_CELLS.length;
  const u1 = (i + 1) / SIGN_CELLS.length;
  b.at(s.x, s.y, 0, rz);
  if (!s.low) b.cyl(0.045, 0.045, 0, 2.9, STEEL, 6);
  // the face, read from the -Y side
  b.quad([half, -0.06, z - half], [-half, -0.06, z - half], [-half, -0.06, z + half], [half, -0.06, z + half], [1, 1, 1],
    [[u1, 1], [u0, 1], [u0, 0], [u1, 0]]);
  b.pop();
}

function addIsland(b, q) {
  const h = (k) => { const v = Math.sin(q.x * 12.9898 + q.y * 78.233 + k * 37.7) * 43758.5453; return v - Math.floor(v); };
  b.at(q.x, q.y, 0, h(1) * 6);
  b.cyl(q.island, q.island, 0, 0.2, rgb(0xC9C9C4), 28);
  b.cyl(q.island - 0.35, q.island - 0.35, 0.2, 0.3, rgb(0x6FAE4F), 28);
  // red-white kerb stones
  const n = Math.max(12, Math.round(q.island * 4));
  for (let i = 0; i < n; i++) {
    b.push().tf(M4.rotZ((i / n) * 2 * Math.PI));
    const w = (Math.PI * q.island) / n;
    b.box(q.island - 0.32, -w, 0.2, q.island + 0.02, w, 0.24, i % 2 ? rgb(0xD63A3A) : rgb(0xF4F4F0));
    b.pop();
  }
  const style = h(2);
  if (q.island > 3.2 && style < 0.4) { // fountain
    b.cyl(q.island * 0.55, q.island * 0.55, 0.3, 0.75, rgb(0xB9B3A5), 20);
    b.e = 0.35;
    b.cyl(q.island * 0.5, q.island * 0.5, 0.75, 0.8, rgb(0x4FB3E8), 20);
    b.cyl(0.14, 0.05, 0.8, 2.9, rgb(0xCDEBFA), 6);
    b.push().tf(M4.trans(0, 0, 2.8)); b.sphere(0.5, rgb(0xDDF3FD), 7, 4, 0.5); b.pop();
    b.e = 0;
  } else if (q.island > 2) { // palms and flowers
    addTree(b, { x: 0, y: 0, kind: style < 0.75 ? 'palm' : 'round', size: 1, tint: h(3) });
    const k = Math.max(6, Math.round(q.island * 3));
    const cols = [rgb(0xE84C6A), rgb(0xFFD23F), rgb(0xF08A2E), rgb(0xB764D6)];
    for (let i = 0; i < k; i++) {
      const a = (i / k) * 2 * Math.PI;
      b.push().tf(M4.trans(Math.cos(a) * q.island * 0.62, Math.sin(a) * q.island * 0.62, 0.42));
      b.sphere(0.3, cols[i % cols.length], 6, 4, 0.7);
      b.pop();
    }
  } else {
    b.cyl(0.5, 0.2, 0.3, 1.1, rgb(0xB9B3A5), 8);
  }
  b.pop();
}

function addSignalPosts(b, sig) {
  for (const ap of sig.approaches) {
    // local frame of the post: +Y = the way the inbound driver goes, +X = his right
    const rz = Math.atan2(ap.dy, ap.dx) - Math.PI / 2;
    b.at(ap.lx, ap.ly, 0, rz);
    b.cyl(0.1, 0.085, 0, 3.6, DARK, 7, false);
    b.cyl(0.19, 0.15, 0, 0.6, rgb(0xE0B81E), 7);
    b.box(-0.2, -0.14, 2.45, 0.2, 0.14, 3.6, rgb(0x16181C)); // head
    b.box(-0.24, -0.2, 3.56, 0.24, 0.14, 3.62, rgb(0x16181C));
    if (ap.hw >= 4.4) { // arm over the road with a second head
      const reach = Math.min(ap.hw, 4.6);
      b.box(-reach, -0.05, 5.25, 0.05, 0.05, 5.37, DARK);
      b.cyl(0.085, 0.085, 3.6, 5.35, DARK, 6, false);
      b.box(-reach - 0.2, -0.14, 4.6, -reach + 0.2, 0.14, 5.75, rgb(0x16181C));
    }
    b.pop();
  }
}

/** White stripes of the pedestrian crossings and the stop lines. Ground decals. */
function addCrossings(b, sig) {
  const white = rgb(0xF2F2EE);
  for (const ap of sig.approaches) {
    const rz = Math.atan2(ap.dy, ap.dx) - Math.PI / 2;
    b.at(ap.x, ap.y, 0, rz);
    // stop line on the inbound side (the whole width of a one-way street)
    b.quad([ap.oneway ? -ap.hw + 0.3 : 0.1, -1.4, 0.035], [ap.hw - 0.3, -1.4, 0.035], [ap.hw - 0.3, -1.0, 0.035], [ap.oneway ? -ap.hw + 0.3 : 0.1, -1.0, 0.035], white);
    for (let x = -ap.hw + 0.5; x < ap.hw - 0.6; x += 1.1) b.quad([x, 0, 0.035], [x + 0.55, 0, 0.035], [x + 0.55, 3, 0.035], [x, 3, 0.035], white);
    b.pop();
  }
}

/** The static scenery of World.analyze(): returns { solid, decals } vertex arrays. */
export function sceneMeshes(scene, night) {
  const b = new MeshBuilder();
  const d = new MeshBuilder();
  for (const t of scene.trees) addTree(b, t);
  for (const l of scene.lamps) addLamp(b, l, night);
  for (const s of scene.signs) addSign(b, s);
  for (const q of scene.roundabouts) addIsland(b, q);
  for (const sig of scene.signals) { addSignalPosts(b, sig); addCrossings(d, sig); }
  if (night) { // pools of light under the lamps
    const g = new MeshBuilder();
    g.e = 1;
    for (const l of scene.lamps) { g.at(l.x + l.dx * 2, l.y + l.dy * 2); g.disc(6.5, 6.5, 0.045, rgb(0xFFDD99), 0.22, 0, 14); g.pop(); }
    return { solid: b.data(), decals: d.data(), glow: g.data() };
  }
  return { solid: b.data(), decals: d.data(), glow: new Float32Array(0) };
}

const LAMP = { r: [rgb(0xFF2A1F), rgb(0x3A0D0B)], y: [rgb(0xFFC21A), rgb(0x3B2E08)], g: [rgb(0x22E06A), rgb(0x0A3319)] };

/** The lamps of all traffic lights for the given states (rebuilt whenever a light changes). states[i][group] = 'r' | 'y' | 'g'. */
export function signalLampMesh(signals, states) {
  const b = new MeshBuilder();
  signals.forEach((sig, i) => {
    for (const ap of sig.approaches) {
      const st = states[i][ap.group];
      const rz = Math.atan2(ap.dy, ap.dx) - Math.PI / 2;
      b.at(ap.lx, ap.ly, 0, rz);
      const heads = [[0, 2.45]];
      if (ap.hw >= 4.4) heads.push([-Math.min(ap.hw, 4.6), 4.6]);
      for (const [hx, hz] of heads) {
        ['g', 'y', 'r'].forEach((k, row) => {
          const on = st === k;
          b.e = on ? 1 : 0;
          b.push().tf(M4.trans(hx, -0.15, hz + 0.2 + row * 0.36));
          b.sphere(on ? 0.15 : 0.13, LAMP[k][on ? 0 : 1], 8, 5);
          b.pop();
        });
      }
      b.e = 0;
      b.pop();
    }
  });
  return b.data();
}

// ---------------------------------------------------------------- renderer
const VS = `
attribute vec3 aPos; attribute vec3 aNrm; attribute vec4 aCol; attribute float aEmi; attribute vec2 aUv;
uniform mat4 uMvp; uniform mat3 uNrm;
varying vec3 vN; varying vec4 vCol; varying float vEmi; varying vec2 vUv;
void main() {
  vN = uNrm * aNrm; vCol = aCol; vEmi = aEmi; vUv = aUv;
  gl_Position = uMvp * vec4(aPos, 1.0);
}`;
const FS = `
precision mediump float;
uniform sampler2D uTex; uniform vec3 uSun; uniform float uAmb; uniform float uDif; uniform float uAlpha; uniform vec3 uTint;
varying vec3 vN; varying vec4 vCol; varying float vEmi; varying vec2 vUv;
void main() {
  vec3 base = vCol.rgb; float a = vCol.a * uAlpha; float emi = vEmi;
  if (vUv.x >= 0.0) {
    vec4 t = texture2D(uTex, vUv);
    if (t.a < 0.5) discard;
    base = t.rgb; emi = max(emi, 0.35);
  }
  vec3 n = normalize(vN);
  float d = max(dot(n, uSun), 0.0);
  float sky = 0.5 + 0.5 * n.z;
  vec3 lit = base * uTint * (uAmb * (0.75 + 0.25 * sky) + uDif * d);
  vec3 c = mix(lit, base, emi);
  gl_FragColor = vec4(c * a, a);
}`;

/**
 * MapLibre custom layer. The game hands it vertex arrays (setMesh) and, every frame, a draw
 * callback: draw(layer) calls layer.draw(name, modelMatrix, opts).
 */
export class GameLayer {
  constructor(id, onDraw) {
    this.id = id;
    this.type = 'custom';
    this.renderingMode = '3d';
    this.onDraw = onDraw;
    this.pending = new Map();
    this.meshes = new Map();
    this.atlas = null;
    this.night = false;
  }

  setMesh(name, data) { this.pending.set(name, data); }
  setAtlas(canvas) { this.atlasSrc = canvas; }

  onAdd(map, gl) { this.map = map; this.gl = gl; }

  onRemove(map, gl) {
    for (const m of this.meshes.values()) gl.deleteBuffer(m.buf);
    this.meshes.clear();
    if (this.prog) gl.deleteProgram(this.prog);
    if (this.atlas) gl.deleteTexture(this.atlas);
    this.prog = null;
    this.atlas = null;
  }

  _init(gl) {
    const sh = (type, src) => {
      const s = gl.createShader(type);
      gl.shaderSource(s, src);
      gl.compileShader(s);
      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
      return s;
    };
    const p = gl.createProgram();
    gl.attachShader(p, sh(gl.VERTEX_SHADER, VS));
    gl.attachShader(p, sh(gl.FRAGMENT_SHADER, FS));
    gl.linkProgram(p);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p));
    this.prog = p;
    this.loc = {};
    for (const a of ['aPos', 'aNrm', 'aCol', 'aEmi', 'aUv']) this.loc[a] = gl.getAttribLocation(p, a);
    for (const u of ['uMvp', 'uNrm', 'uTex', 'uSun', 'uAmb', 'uDif', 'uAlpha', 'uTint']) this.loc[u] = gl.getUniformLocation(p, u);
    this.white = gl.createTexture();
  }

  _upload(gl) {
    for (const [name, data] of this.pending) {
      let m = this.meshes.get(name);
      if (!m) { m = { buf: gl.createBuffer(), n: 0 }; this.meshes.set(name, m); }
      gl.bindBuffer(gl.ARRAY_BUFFER, m.buf);
      gl.bufferData(gl.ARRAY_BUFFER, data, gl.DYNAMIC_DRAW);
      m.n = data.length / STRIDE;
    }
    this.pending.clear();
    if (this.atlasSrc) {
      if (!this.atlas) this.atlas = gl.createTexture();
      gl.bindTexture(gl.TEXTURE_2D, this.atlas);
      gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
      gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, this.atlasSrc);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      this.atlasSrc = null;
    }
  }

  render(gl, a) {
    // MapLibre 4: render(gl, matrix); MapLibre 5: render(gl, { defaultProjectionData: { mainMatrix } })
    const proj = a && a.length ? a : (a?.defaultProjectionData?.mainMatrix || a?.modelViewProjectionMatrix);
    if (!proj) return;
    try {
      if (!this.prog) this._init(gl);
      this._upload(gl);
    } catch (e) {
      if (!this.failed) console.warn('game layer', e);
      this.failed = true;
      return;
    }
    this.proj = Array.from(proj);
    if (gl.bindVertexArray) gl.bindVertexArray(null);
    gl.useProgram(this.prog);
    gl.enable(gl.DEPTH_TEST);
    gl.depthFunc(gl.LEQUAL);
    gl.disable(gl.CULL_FACE);
    gl.disable(gl.STENCIL_TEST);
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, this.atlas || this.white);
    gl.uniform1i(this.loc.uTex, 0);
    const n = this.night;
    gl.uniform3f(this.loc.uSun, 0.42, -0.36, 0.83);
    gl.uniform1f(this.loc.uAmb, n ? 0.34 : 0.58);
    gl.uniform1f(this.loc.uDif, n ? 0.22 : 0.62);
    for (const k of ['aPos', 'aNrm', 'aCol', 'aEmi', 'aUv']) gl.enableVertexAttribArray(this.loc[k]);
    this._gl = gl;
    this.onDraw(this);
    for (const k of ['aPos', 'aNrm', 'aCol', 'aEmi', 'aUv']) gl.disableVertexAttribArray(this.loc[k]);
    gl.depthMask(true);
    gl.bindBuffer(gl.ARRAY_BUFFER, null);
  }

  /**
   * Draw a mesh. model: 4×4 in the game's local frame (metres); base: local frame -> clip space.
   * opts.alpha: transparency; opts.blend: transparent pass (no depth writes); opts.tint: colour multiplier.
   */
  draw(name, base, model = null, { alpha = 1, blend = false, tint = null } = {}) {
    const m = this.meshes.get(name);
    if (!m || !m.n) return;
    const gl = this._gl;
    const mvp = model ? M4.mul(base, model) : base;
    gl.uniformMatrix4fv(this.loc.uMvp, false, new Float32Array(mvp));
    const r = model || M4.ident();
    gl.uniformMatrix3fv(this.loc.uNrm, false, new Float32Array([r[0], r[1], r[2], r[4], r[5], r[6], r[8], r[9], r[10]]));
    gl.uniform1f(this.loc.uAlpha, alpha);
    const t = tint || [1, 1, 1];
    gl.uniform3f(this.loc.uTint, t[0], t[1], t[2]);
    gl.depthMask(!blend);
    gl.bindBuffer(gl.ARRAY_BUFFER, m.buf);
    const B = STRIDE * 4;
    gl.vertexAttribPointer(this.loc.aPos, 3, gl.FLOAT, false, B, 0);
    gl.vertexAttribPointer(this.loc.aNrm, 3, gl.FLOAT, false, B, 12);
    gl.vertexAttribPointer(this.loc.aCol, 4, gl.FLOAT, false, B, 24);
    gl.vertexAttribPointer(this.loc.aEmi, 1, gl.FLOAT, false, B, 40);
    gl.vertexAttribPointer(this.loc.aUv, 2, gl.FLOAT, false, B, 44);
    gl.drawArrays(gl.TRIANGLES, 0, m.n);
  }
}
