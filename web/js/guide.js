// Hiking-route guide (מדריך מסלול): summary chips, elevation profile, a numbered stage timeline
// (click a stage → the map zooms to that leg and highlights it), sights, practical & safety
// information and credits. Data: /api/hikes/{id}/guide, /api/trails/{id}/guide, POST /api/guide.
// Also the "מסלולי טיול באזור" list (/api/hikes near the map centre + marked trails on the map).
// The layout prints cleanly ("הדפסה / שמירה כ-PDF") for offline use – see css/guide.css.

import { profileHtml } from './elevation.js';
import { galleryHtml, wireGallery } from './gallery.js';
import { svg } from './icons.js';
import { $, announce, bus, esc, fmtDistance, isMobile } from './util.js';
import { errorView, head, iconCircle, loadingView, wireHead } from './views.js';

const EMPTY = { type: 'FeatureCollection', features: [] };
const STAGE_SRC = 'guide-stage';
const STAGE_COLOR = '#7C3AED';
const TIP_ICON = { flood: 'stream', heat: 'alert', long: 'clock', shuttle: 'car', steep: 'mountain', navigation: 'signpost',
  general: 'info', source: 'check' };

let stageData = EMPTY;
let installed = null; // map the style.load hook was attached to

// ------------------------------------------------------------------ data

async function getJson(url, { signal, method = 'GET', body } = {}) {
  let resp;
  try {
    resp = await fetch(url, { method, signal, headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined });
  } catch (e) {
    if (e.name === 'AbortError') throw e;
    throw new Error('אין חיבור לשרת. בדקו את החיבור ונסו שוב.');
  }
  let data = null;
  try {
    data = await resp.json();
  } catch {
    /* empty */
  }
  if (!resp.ok) throw new Error(data?.error?.message || 'אירעה שגיאה. נסו שוב.');
  return data;
}

export function fetchGuide(src, signal) {
  if (src.kind === 'hike') return getJson(`/api/hikes/${encodeURIComponent(src.id)}/guide`, { signal });
  if (src.kind === 'trail') return getJson(`/api/trails/${encodeURIComponent(src.id)}/guide`, { signal });
  return getJson('/api/guide', { signal, method: 'POST', body: { coordinates: src.coordinates, name: src.name } });
}

// ------------------------------------------------------------------ map layers (selected leg + stage numbers)

function installLayers(m) {
  try {
    if (!m.getSource(STAGE_SRC)) m.addSource(STAGE_SRC, { type: 'geojson', data: stageData });
    const add = (layer) => { if (!m.getLayer(layer.id)) m.addLayer(layer); };
    add({ id: 'guide-stage-casing', type: 'line', source: STAGE_SRC, filter: ['==', ['get', 'role'], 'leg'],
      layout: { 'line-join': 'round', 'line-cap': 'round' }, paint: { 'line-color': '#FFFFFF', 'line-width': 10, 'line-opacity': 0.9 } });
    add({ id: 'guide-stage-line', type: 'line', source: STAGE_SRC, filter: ['==', ['get', 'role'], 'leg'],
      layout: { 'line-join': 'round', 'line-cap': 'round' },
      paint: { 'line-color': ['coalesce', ['get', 'color'], STAGE_COLOR], 'line-width': 6 } });
    add({ id: 'guide-stops', type: 'circle', source: STAGE_SRC, filter: ['==', ['get', 'role'], 'stop'],
      paint: { 'circle-radius': 10, 'circle-color': ['case', ['==', ['get', 'selected'], true], STAGE_COLOR, '#FFFFFF'],
        'circle-stroke-color': STAGE_COLOR, 'circle-stroke-width': 2 } });
    add({ id: 'guide-stops-label', type: 'symbol', source: STAGE_SRC, filter: ['==', ['get', 'role'], 'stop'],
      layout: { 'text-field': ['to-string', ['get', 'n']], 'text-font': ['Noto Sans Regular'], 'text-size': 12, 'text-allow-overlap': true },
      paint: { 'text-color': ['case', ['==', ['get', 'selected'], true], '#FFFFFF', STAGE_COLOR] } });
  } catch {
    /* style not loaded yet: style.load installs it */
  }
}

function ensureLayers(app) {
  const m = app.map?.map;
  if (!m) return;
  if (installed !== m) {
    installed = m;
    m.on('style.load', () => installLayers(m));
  }
  installLayers(m);
}

function setStageData(app, data) {
  stageData = data;
  ensureLayers(app);
  app.map?.map?.getSource(STAGE_SRC)?.setData(data);
}

function stagesFeatures(g, selected) {
  const feats = [];
  if (selected != null && g.stages[selected]) {
    const s = g.stages[selected];
    feats.push({ type: 'Feature', properties: { role: 'leg', n: s.n, color: s.color || STAGE_COLOR }, geometry: s.geometry });
  }
  g.stages.forEach((s, i) => feats.push({ type: 'Feature', properties: { role: 'stop', n: s.n, selected: i === selected },
    geometry: { type: 'Point', coordinates: s.geometry.coordinates[0] } }));
  return { type: 'FeatureCollection', features: feats };
}

// ------------------------------------------------------------------ formatting

const fmtM = (v) => `${Math.round(v).toLocaleString('he-IL')} מ׳`;
const km = (m) => `ק״מ ${(m / 1000).toFixed(1)}`;

function swatch(color) {
  return `<span class="gd-swatch" style="background:${esc(color || '#6B7280')}" aria-hidden="true"></span>`;
}

function markLabel(m) {
  if (m.named && (m.network === 'nwn' || m.network === 'rwn')) return m.name;
  return m.mark_he ? `סימון ${m.mark_he}` : m.name;
}

function chipsHtml(g) {
  const s = g.summary;
  const chips = [
    [svg('road', { size: 16 }), s.length_text, 'אורך'],
    [svg('clock', { size: 16 }), s.duration_text, `זמן הליכה משוער (${s.time_formula_he})`],
  ];
  if (s.ascent != null) chips.push([svg('mountain', { size: 16 }), `↑${fmtM(s.ascent)} ↓${fmtM(s.descent)}`, 'עלייה וירידה מצטברות']);
  chips.push([svg('hiker', { size: 16 }), s.difficulty_he, s.difficulty_source === 'estimated' ? 'דרגת קושי (הערכה)' : 'דרגת קושי']);
  chips.push([svg(s.loop ? 'history' : 'start', { size: 16 }), s.loop ? 'מעגלי' : 'קווי', 'סוג המסלול']);
  for (const m of s.marks.slice(0, 3)) chips.push([swatch(m.color), markLabel(m), 'סימון שבילים']);
  return `<ul class="gd-chips" aria-label="תקציר המסלול">${chips.map(([icon, text, label]) =>
    `<li class="gd-chip" title="${esc(label)}">${icon}<span class="sr-only">${esc(label)}: </span>${esc(text)}</li>`).join('')}</ul>`;
}

function sightsHtml(items, stageIdx) {
  if (!items?.length) return '';
  return `<ul class="gd-sights" aria-label="מה בדרך">${items.map((p, j) => `<li><button type="button" class="gd-sight" data-sight="${stageIdx}:${j}">
      ${iconCircle(p, 'r-icon gd-sicon')}<span class="gd-sbody"><span class="gd-sname">${esc(p.name)}</span>
      <span class="r-sub">${p.named ? `${esc(p.sub_category_he)} · ` : ''}${esc(km(p.at_m))}${p.distance > 40 ? ` · ${esc(fmtDistance(p.distance))} מהשביל` : ''}</span>
      ${p.text ? `<span class="gd-stext">${esc(p.text.text)}</span>` : ''}</span>
      ${p.thumbnail ? `<img class="gd-sthumb" src="${esc(p.thumbnail)}" alt="" loading="lazy" referrerpolicy="no-referrer">` : ''}
    </button></li>`).join('')}</ul>`;
}

function stagesHtml(g) {
  return `<ol class="gd-stages" aria-label="שלבי המסלול (${g.stages.length})">${g.stages.map((s, i) => {
    const climb = s.ascent != null ? ` · ↑${s.ascent} ↓${s.descent} מ׳` : '';
    const marks = s.marks.map((m) => `<span class="gd-mark">${swatch(m.color)}${esc(markLabel(m))}</span>`).join('');
    return `<li class="gd-stage" style="--stage:${esc(s.color || STAGE_COLOR)}">
      <button type="button" class="gd-stage-btn" data-stage="${i}" aria-pressed="false">
        <span class="gd-num" aria-hidden="true">${s.n}</span>
        <span class="gd-stage-title"><span class="gd-step">שלב ${s.n}</span>
          <span class="gd-route">${esc(s.from.name)} <span aria-hidden="true">←</span><span class="sr-only">עד</span> ${esc(s.to.name)}</span>
          <span class="gd-meta">${esc(s.distance_text)} · ${esc(s.duration_text)}${esc(climb)}</span></span>
      </button>
      <div class="gd-stage-body">
        <p class="gd-instr">${esc(s.instruction)}</p>
        ${marks || s.stream ? `<div class="gd-marks">${marks}${s.stream ? `<span class="gd-mark">${svg('stream', { size: 14 })}${esc(s.stream)}</span>` : ''}</div>` : ''}
        ${s.to.note ? `<p class="gd-note">${svg('info', { size: 14 })}${esc(s.to.name)}: ${esc(s.to.note)}</p>` : ''}
        ${sightsHtml(s.sights, i)}
      </div></li>`;
  }).join('')}</ol>`;
}

function poiLine(p) {
  return `${esc(p.name)}${p.at_m != null ? ` (${esc(km(p.at_m))})` : ''}`;
}

function practicalHtml(g) {
  const p = g.practical;
  const rows = [];
  const row = (icon, title, body) => rows.push(`<li class="info-row">${svg(icon)}<div class="ir-body"><b>${esc(title)}:</b> ${body}</div></li>`);
  const w = p.water;
  row('tap', 'מים', `${esc(w.note)}${w.points.length ? ` ברזיות: ${w.points.map(poiLine).join(', ')}.` : ''}`
    + `${w.springs.length ? ` מעיינות: ${w.springs.map(poiLine).join(', ')}.` : ''}${w.source_note ? ` <span class="r-sub">(${esc(w.source_note)})</span>` : ''}`);
  if (p.toilets.length) row('toilets', 'שירותים', p.toilets.map(poiLine).join(', '));
  if (p.parking.length || p.parking_note) {
    row('parking', 'חניה', [p.parking.map(poiLine).join(', '), p.parking_note ? esc(p.parking_note) : ''].filter(Boolean).join(' · '));
  }
  if (p.shade) row('shelter', 'צל ומחסה', esc(p.shade));
  if (p.season) row('leaf', 'עונה מומלצת', esc(p.season));
  if (p.daylight?.note) row('clock', 'שעות אור', esc(p.daylight.note));
  const phones = p.emergency.phones.map((x) => `<a href="tel:${esc(x.number)}" dir="ltr">${esc(x.number)}</a> ${esc(x.label)}`).join(' · ');
  const near = p.emergency.nearby.map((x) => `${esc(x.name)} (${esc(x.sub_category_he)}, ${esc(fmtDistance(x.distance))})`).join(', ');
  row('phone', 'חירום', `${phones}${near ? `<br><span class="r-sub">בסביבה: ${near}</span>` : ''}<br><span class="r-sub">${esc(p.emergency.note)}</span>`);
  const tips = p.tips.map((t) => `<li class="gd-tip gd-tip-${esc(t.kind)}">${svg(TIP_ICON[t.kind] || 'info', { size: 16 })}<span>${esc(t.text)}</span></li>`).join('');
  return `<section class="gd-section" aria-labelledby="gd-practical-title">
      <h3 class="subtitle" id="gd-practical-title">מידע מעשי ובטיחות</h3>
      <ul class="info-rows">${rows.join('')}</ul>
      ${tips ? `<h4 class="gd-h4">לפני שיוצאים</h4><ul class="gd-tips">${tips}</ul>` : ''}
    </section>`;
}

function creditsHtml(g) {
  return `<section class="gd-section gd-credits" aria-labelledby="gd-credits-title">
    <h3 class="subtitle" id="gd-credits-title">מקורות וקרדיטים</h3>
    <ul>${g.credits.map((c) => `<li>${esc(c.role)}: ${c.url
      ? `<a href="${esc(c.url)}" target="_blank" rel="noopener">${esc(c.name)}<span class="sr-only"> (נפתח בלשונית חדשה)</span></a>` : esc(c.name)}${c.license ? ` · ${esc(c.license)}` : ''}</li>`).join('')}</ul>
    <p class="r-sub">${esc(g.disclaimer)}</p></section>`;
}

// ------------------------------------------------------------------ the view

export function guideView(app, g, src) {
  const key = `guide:${g.ref || src.id || 'route'}`;
  const photos = g.media?.gallery || [];
  let selected = null;
  const fit = (bbox) => app.map.fitBounds(bbox, 16);
  const select = (body, i, { zoom = true } = {}) => {
    selected = i;
    body.querySelectorAll('.gd-stage').forEach((li, k) => {
      if (k === i) li.setAttribute('aria-current', 'step');
      else li.removeAttribute('aria-current');
      li.querySelector('.gd-stage-btn').setAttribute('aria-pressed', String(k === i));
    });
    setStageData(app, stagesFeatures(g, i));
    if (i != null && zoom) {
      const s = g.stages[i];
      if (isMobile() && app.panel.el.dataset.snap === 'full') app.panel.setSnap('half'); // let the leg show
      fit(s.bbox);
      announce(`שלב ${s.n}: ${s.from.name} עד ${s.to.name}, ${s.distance_text}`);
    }
  };
  return {
    key, label: `מדריך מסלול: ${g.name}`, snap: 'half',
    render(body) {
      const s = g.summary;
      const desc = g.paragraphs?.length
        ? `<div class="desc gd-desc">${g.paragraphs.map((p) => `<p>${esc(p)}</p>`).join('')}
            ${g.source_he ? `<p class="src">${g.url ? `<a href="${esc(g.url)}" target="_blank" rel="noopener">מתוך: ${esc(g.source_he)}<span class="sr-only"> (נפתח בלשונית חדשה)</span></a>` : `מתוך: ${esc(g.source_he)}`}</p>` : ''}</div>`
        : '';
      body.innerHTML = `${head(app, g.name, { sub: 'מדריך מסלול' })}
        <article class="guide-view" aria-labelledby="panel-title">
          ${galleryHtml(photos, g.name)}
          ${chipsHtml(g)}
          <p class="gd-ends">${svg('start', { size: 16 })}<span>מוצא: <b>${esc(g.start.name)}</b>${s.loop ? ' (מסלול מעגלי)' : ` · סיום: <b>${esc(g.end.name)}</b>`}</span></p>
          <div class="actions gd-actions" role="group" aria-label="פעולות">
            <button type="button" class="action primary" data-act="to-start"><span class="a-circle">${svg('directions')}</span>ניווט למוצא</button>
            <button type="button" class="action" data-act="fit"><span class="a-circle">${svg('locate')}</span>כל המסלול</button>
            <button type="button" class="action" data-act="print"><span class="a-circle">${svg('copy')}</span>הדפסה / PDF</button>
            ${g.ref ? `<button type="button" class="action" data-act="share"><span class="a-circle">${svg('share')}</span>שיתוף</button>` : ''}
          </div>
          ${desc}
          ${g.elevation ? `<section class="gd-section elev" aria-labelledby="gd-elev-title"><h3 class="subtitle" id="gd-elev-title">פרופיל גובה</h3>
            <div class="elev-body">${profileHtml(g.elevation)}</div></section>`
            : '<p class="r-sub gd-pad">אין נתוני גובה לאזור זה: הזמנים מחושבים לפי מרחק בלבד.</p>'}
          <section class="gd-section" aria-labelledby="gd-stages-title">
            <h3 class="subtitle" id="gd-stages-title">שלבי המסלול</h3>
            <p class="r-sub gd-pad">לחיצה על שלב מציגה אותו במפה. זמן ההליכה: ${esc(s.time_formula_he)}${s.duration_source_min ? ` · לפי המקור: ${esc(fmtMin(s.duration_source_min))}` : ''}.</p>
            ${stagesHtml(g)}
          </section>
          ${practicalHtml(g)}
          ${creditsHtml(g)}
        </article>`;
      wireHead(body, app);
      wireGallery(body, photos, g.name);
      app.map.setSelection(null);
      app.map.setHighlight(g.geometry);
      select(body, selected, { zoom: false });
      body.querySelector('[data-act="to-start"]')?.addEventListener('click', () =>
        app.directions.open({ to: { lat: g.start.lat, lon: g.start.lon, name: g.start.name } }));
      body.querySelector('[data-act="fit"]')?.addEventListener('click', () => {
        select(body, null, { zoom: false });
        fit(g.bbox);
      });
      body.querySelector('[data-act="print"]')?.addEventListener('click', () => window.print());
      body.querySelector('[data-act="share"]')?.addEventListener('click', () =>
        app.share({ id: g.ref, name: g.name, location: { lat: g.start.lat, lon: g.start.lon } }));
      const btns = [...body.querySelectorAll('.gd-stage-btn')];
      btns.forEach((b, k) => {
        b.addEventListener('click', () => select(body, selected === k ? null : k));
        b.addEventListener('keydown', (e) => {
          const d = e.key === 'ArrowDown' ? 1 : e.key === 'ArrowUp' ? -1 : 0;
          if (e.key === 'Home' || e.key === 'End') {
            e.preventDefault();
            btns[e.key === 'Home' ? 0 : btns.length - 1].focus();
          } else if (d) {
            e.preventDefault();
            btns[Math.max(0, Math.min(btns.length - 1, k + d))].focus();
          }
        });
      });
      body.querySelectorAll('[data-sight]').forEach((b) => {
        const [i, j] = b.dataset.sight.split(':').map(Number);
        const p = g.stages[i].sights[j];
        b.addEventListener('click', () => app.openItem({ ...p, type: 'poi' }));
        b.addEventListener('mouseenter', () => app.map.setSelection(p.location));
      });
      body.querySelectorAll('.gd-sthumb').forEach((img) => img.addEventListener('error', () => img.remove(), { once: true }));
      announce(`מדריך מסלול: ${g.name}, ${s.length_text}, ${g.stages.length} שלבים`);
    },
  };
}

function fmtMin(min) {
  const h = Math.floor(min / 60), m = min % 60;
  if (!h) return `${m} דק׳`;
  return `${h === 1 ? 'שעה' : h === 2 ? 'שעתיים' : `${h} שעות`}${m ? ` ו-${m} דק׳` : ''}`;
}

/** Load and show a guide. src: {kind: 'hike'|'trail', id, name} | {kind: 'route', coordinates, name}. */
export async function openGuide(app, src, { signal, fly = true, replace = false } = {}) {
  const key = `guide:${src.id || 'route'}`;
  app.panel.show({ ...loadingView(key, `טוען מדריך מסלול${src.name ? `: ${src.name}` : ''}`), replace });
  try {
    const g = await fetchGuide(src, signal);
    if (fly) app.map.fitBounds(g.bbox, 15);
    app.panel.show({ ...guideView(app, g, src), replace: true });
    return g;
  } catch (e) {
    if (e.name === 'AbortError') throw e;
    app.panel.show({ ...errorView(app, 'err:' + key, e.message, () => openGuide(app, src, { fly })), replace: true });
    return null;
  }
}

// ------------------------------------------------------------------ hikes near the map centre

export function hikesNearView(app, hikes, trails) {
  return {
    key: 'hikes-near', label: 'מסלולי טיול באזור',
    render(body) {
      body.innerHTML = `${head(app, 'מסלולי טיול באזור', { sub: hikes.length ? `(${hikes.length})` : '' })}
        ${hikes.length ? `<ul class="results" aria-label="מסלולי טיול מתוארים">${hikes.map((h, i) => `<li class="result">
            <button type="button" data-hike="${i}">${iconCircle({ ...h, type: 'trail' })}<span class="r-body">
              <span class="r-name">${esc(h.name)}</span><span class="r-sub" style="display:block">${esc(h.secondary || '')}</span>
              <span class="r-sub" style="display:block">${h.distance > 200 ? `במרחק ${esc(fmtDistance(h.distance))}` : 'כאן'}${h.source_he ? ` · ${esc(h.source_he)}` : ''}</span>
            </span>${h.thumbnail ? `<img class="r-thumb" src="${esc(h.thumbnail)}" alt="" loading="lazy" referrerpolicy="no-referrer">` : ''}</button></li>`).join('')}</ul>`
          : `<div class="p-empty">${svg('hiker', { size: 40 })}<p>לא נמצאו מסלולי טיול מתוארים באזור.</p><p>הזיזו את המפה לאזור אחר ונסו שוב, או בחרו שביל מסומן מהרשימה.</p></div>`}
        ${trails.length ? `<h3 class="subtitle">מדריך לשבילים המסומנים שבמפה</h3><ul class="results" aria-label="שבילים מסומנים">${trails.map((t, i) =>
          `<li class="result"><button type="button" data-trail="${i}">${iconCircle({ icon: 'hiker', type: 'trail', color: t.color })}<span class="r-body">
            <span class="r-name">${esc(t.name)}</span><span class="r-sub" style="display:block">מדריך מסלול</span></span></button></li>`).join('')}</ul>` : ''}`;
      wireHead(body, app);
      const lines = hikes.filter((h) => h.geometry).map((h) => h.geometry.coordinates);
      app.map.setHighlight(lines.length ? { type: 'MultiLineString', coordinates: lines } : null);
      body.querySelectorAll('[data-hike]').forEach((b) => {
        const h = hikes[Number(b.dataset.hike)];
        b.addEventListener('click', () => app.openItem(h));
        b.addEventListener('mouseenter', () => app.map.setSelection(h.location));
      });
      body.querySelectorAll('[data-trail]').forEach((b) => {
        const t = trails[Number(b.dataset.trail)];
        b.addEventListener('click', () => openGuide(app, { kind: 'trail', id: t.id, name: t.name }).catch(() => {}));
      });
      announce(hikes.length ? `נמצאו ${hikes.length} מסלולי טיול באזור` : 'לא נמצאו מסלולי טיול באזור');
    },
  };
}

export async function openHikesNear(app) {
  const c = app.map.center();
  app.panel.show(loadingView('hikes-near', 'מחפש מסלולי טיול באזור'));
  const trails = (app.map.visibleTrails?.() || []).filter((t) => t.name && !/^שביל מסומן/.test(t.name)).slice(0, 10);
  try {
    const res = await getJson(`/api/hikes?lat=${c.lat.toFixed(5)}&lon=${c.lon.toFixed(5)}&radius=25000&limit=30&geometry=1`);
    app.panel.show({ ...hikesNearView(app, res.results || [], trails), replace: true });
  } catch (e) {
    app.panel.show({ ...errorView(app, 'err:hikes-near', e.message, () => openHikesNear(app)), replace: true });
  }
}

// ------------------------------------------------------------------ wiring

/** Called once by app.init(): entry points + clearing the stage layer when another view takes the panel. */
export function initGuide(app) {
  bus.on('panel:view', (view) => {
    if (!view?.key?.startsWith('guide:') && stageData !== EMPTY) setStageData(app, EMPTY);
  });
  bus.on('panel:close', () => { if (stageData !== EMPTY) setStageData(app, EMPTY); });
  $('#btn-hikes-near')?.addEventListener('click', () => {
    const pop = $('#layers-pop');
    if (pop && !pop.hidden) $('#btn-layers')?.click();
    openHikesNear(app);
  });
}
