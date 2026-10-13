// Elevation profile for walking / cycling routes (data: GET/POST /api/elevation, Copernicus DEM).
// Rendered as a small SVG chart plus a text summary, which is also what screen readers get.

import { esc, fmtDistance } from './util.js';

const MODES = new Set(['foot', 'bike']);
const cache = new WeakMap(); // route object → profile | null (no data) | Promise

const fmtM = (v) => `${Math.round(v).toLocaleString('he-IL')} מ׳`;

function wanted(meta, mode, route) {
  return MODES.has(mode) && !!meta?.terrain?.elevation && route?.geometry?.coordinates?.length > 1;
}

/** Placeholder (or the cached result) for the route card. */
export function elevationSlot(meta, mode, route) {
  if (!wanted(meta, mode, route)) return '';
  const p = cache.get(route);
  const inner = p && !(p instanceof Promise) ? profileHtml(p) : '<p class="r-sub">טוען פרופיל גובה…</p>';
  return `<section class="elev" id="elev-profile" aria-labelledby="elev-title" aria-live="polite">
    <h3 class="subtitle" id="elev-title">פרופיל גובה</h3><div class="elev-body">${p === null ? '' : inner}</div></section>`;
}

/** Fetch the profile of `route` (once) and fill the slot rendered by elevationSlot. */
export async function fillElevation(root, meta, mode, route) {
  if (!wanted(meta, mode, route)) return;
  let p = cache.get(route);
  if (p === undefined) {
    p = fetchProfile(route.geometry.coordinates);
    cache.set(route, p);
  }
  try {
    p = await p;
  } catch {
    p = null;
  }
  cache.set(route, p);
  const slot = root.querySelector('#elev-profile');
  if (!slot) return;
  if (!p) {
    slot.remove();
    return;
  }
  slot.querySelector('.elev-body').innerHTML = profileHtml(p);
}

export async function fetchProfile(coords, signal) {
  // long routes: thin the vertices, the server resamples anyway
  const step = Math.ceil(coords.length / 4000);
  const pts = step > 1 ? coords.filter((_, i) => i % step === 0 || i === coords.length - 1) : coords;
  const resp = await fetch('/api/elevation', {
    method: 'POST', signal, headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ coordinates: pts.map(([lon, lat]) => [+lon.toFixed(6), +lat.toFixed(6)]), samples: 160 }),
  });
  if (!resp.ok) return null;
  return resp.json();
}

export function summaryText(p) {
  return `עלייה מצטברת ${fmtM(p.ascent)}, ירידה מצטברת ${fmtM(p.descent)}. `
    + `גובה במוצא ${fmtM(p.start)}, ביעד ${fmtM(p.end)}, נמוך ביותר ${fmtM(p.min)}, גבוה ביותר ${fmtM(p.max)}.`;
}

export function profileHtml(p) {
  if (!p?.profile?.length) return '';
  const W = 320, H = 96, PAD_T = 8, PAD_B = 16, PAD_X = 34;
  const d = p.profile.map((r) => r[0]);
  const e = p.profile.map((r) => r[1]);
  const total = d[d.length - 1] || 1;
  let lo = Math.min(...e), hi = Math.max(...e);
  const span = Math.max(hi - lo, 20); // flat routes: don't magnify noise
  lo -= (span - (hi - lo)) / 2;
  hi = lo + span;
  // RTL: the start of the route is on the right
  const x = (dist) => W - PAD_X - (dist / total) * (W - 2 * PAD_X);
  const y = (ele) => PAD_T + (1 - (ele - lo) / (hi - lo)) * (H - PAD_T - PAD_B);
  const pts = p.profile.map((r) => `${x(r[0]).toFixed(1)},${y(r[1]).toFixed(1)}`);
  const line = `M${pts.join('L')}`;
  const area = `${line}L${x(total).toFixed(1)},${H - PAD_B}L${x(0).toFixed(1)},${H - PAD_B}Z`;
  const summary = summaryText(p);
  return `<ul class="elev-stats">
      <li>עלייה <b>${fmtM(p.ascent)}</b></li><li>ירידה <b>${fmtM(p.descent)}</b></li>
      <li>הנקודה הגבוהה <b>${fmtM(p.max)}</b></li></ul>
    <svg class="elev-chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(`תרשים גובה לאורך ${fmtDistance(total)}`)}">
      <line class="grid" x1="${PAD_X}" x2="${W - PAD_X}" y1="${y(hi).toFixed(1)}" y2="${y(hi).toFixed(1)}"/>
      <line class="grid" x1="${PAD_X}" x2="${W - PAD_X}" y1="${H - PAD_B}" y2="${H - PAD_B}"/>
      <path class="area" d="${area}"/><path class="line" d="${line}"/>
      <text class="axis" x="${W - 2}" y="${(y(hi) + 4).toFixed(1)}" text-anchor="end">${Math.round(hi)}</text>
      <text class="axis" x="${W - 2}" y="${H - PAD_B + 4}" text-anchor="end">${Math.round(lo)}</text>
      <text class="axis" x="${W - PAD_X}" y="${H - 3}" text-anchor="middle">מוצא</text>
      <text class="axis" x="${PAD_X}" y="${H - 3}" text-anchor="middle" direction="rtl" unicode-bidi="embed">${esc(fmtDistance(total))}</text>
    </svg>
    <p class="sr-only">${esc(summary)}</p>`;
}
