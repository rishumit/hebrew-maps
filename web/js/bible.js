// Places of the Tanach, the Talmud and the Midrash – data of "מדריך מקומות בתנ״ך" (Otzaria plugin by
// חד בדרא and נתנאל26). web/bible/places.json is built by tools/import_bible_places.py.
// Every identification method of a place is a point on the map; the locations are estimates.

import { svg } from './icons.js';
import { head, wireHead } from './views.js';
import { announce, esc } from './util.js';

const CREDIT = 'מתוך "מדריך מקומות בתנ״ך" (תוסף לאוצריא) – חד בדרא מפתחי אוצריא';
export const CAT_COLORS = {
  cities: '#B45309', villages: '#A16207', mountains: '#7C5E3C', water: '#2563EB', valleys: '#15803D',
  deserts: '#C2410C', lands: '#7C3AED', stations: '#DB2777', regions: '#0F766E',
};
const LEVEL_HE = { high: 'ודאי', mid: 'סביר', low: 'שנוי במחלוקת' };
const NEAR_KM = 5;

let DATA = null;
let loading = null;
let GEO = null;

/** web/bible/places.json (once); resolves to null when it is missing. */
export function loadBible() {
  if (!loading) {
    loading = fetch('/bible/places.json')
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        DATA = d;
        GEO = null;
        return d;
      })
      .catch(() => null);
  }
  return loading;
}

const catOf = (id) => DATA?.cats.find((c) => c.id === id);
const placeById = (id) => DATA?.places.find((p) => p.id === id);
const firstGeo = (p) => p.methods.find((m) => m.geo);

/** One point per identification method that has a location. */
export function bibleGeo() {
  if (GEO) return GEO;
  const features = [];
  for (const p of DATA?.places || []) {
    p.methods.forEach((m, i) => {
      if (!m.geo) return;
      features.push({
        type: 'Feature', geometry: { type: 'Point', coordinates: [m.geo[1], m.geo[0]] },
        properties: { id: p.id, m: i, name: p.name, cat: p.cat, level: m.level, z: m.geo[2] },
      });
    });
  }
  GEO = { type: 'FeatureCollection', features };
  return GEO;
}

// ----------------------------------------------------------------- map layers (installed by MapView)
export const BIBLE_CLICK_LAYERS = ['bible-points'];

export function bibleLayers(dark) {
  const color = ['match', ['get', 'cat'], ...Object.entries(CAT_COLORS).flat(), '#6B7280'];
  return [
    { id: 'bible-points', type: 'circle', source: 'bible',
      paint: {
        'circle-radius': ['interpolate', ['linear'], ['zoom'], 5, 3.5, 10, 5.5, 15, 8],
        'circle-color': ['case', ['==', ['get', 'level'], 'low'], dark ? '#1F2937' : '#FFFFFF', color],
        'circle-opacity': ['case', ['==', ['get', 'level'], 'mid'], 0.75, 1],
        'circle-stroke-color': ['case', ['==', ['get', 'level'], 'low'], color, dark ? '#111418' : '#FFFFFF'],
        'circle-stroke-width': ['case', ['==', ['get', 'level'], 'low'], 2, 1.2],
      } },
    { id: 'bible-labels', type: 'symbol', source: 'bible', minzoom: 7,
      layout: { 'text-field': ['get', 'name'], 'text-font': ['Noto Sans Regular'], 'text-size': 12,
        'text-anchor': 'top', 'text-offset': [0, 0.7], 'text-max-width': 8, 'symbol-sort-key': ['get', 'z'] },
      paint: { 'text-color': dark ? '#E9D5B5' : '#6B3F12', 'text-halo-color': dark ? '#121418' : '#FFFFFF', 'text-halo-width': 1.5 } },
  ];
}

// ----------------------------------------------------------------- helpers
function distKm(lat1, lon1, lat2, lon2) {
  const kx = 111.32 * Math.cos((lat1 * Math.PI) / 180);
  return Math.hypot((lon2 - lon1) * kx, (lat2 - lat1) * 110.54);
}

function dot(cat) {
  return `<span class="r-icon" style="background:${esc(CAT_COLORS[cat] || '#6B7280')}" aria-hidden="true">${svg('book', { size: 18 })}</span>`;
}

function subLine(p) {
  return [catOf(p.cat)?.label, p.region ? `נחלת ${p.region}` : '', p.aliases?.length ? `נקרא גם: ${p.aliases.join(', ')}` : '']
    .filter(Boolean).join(' · ');
}

/** Turn the layer on (and refresh the check-box) – opening a place shows it on the map. */
function ensureLayer(app) {
  if (!app.map.layerOn('bible')) {
    app.map.setLayer('bible', true);
    app._redrawLayerChecks?.();
  }
}

function fly(app, p, mIdx) {
  const m = p.methods[mIdx] || firstGeo(p);
  if (!m?.geo) return;
  ensureLayer(app);
  app.map.setSelection({ lat: m.geo[0], lon: m.geo[1] });
  app.map.flyTo({ lat: m.geo[0], lon: m.geo[1] }, Math.min(m.geo[2] || 12, 15));
  announce(`${p.name}${p.methods.length > 1 ? ` – ${m.label}` : ''}`);
}

/** One delegated click handler on the panel body (the element is shared by every view). */
function delegate(body, app) {
  if (body.dataset.bibleWired) return;
  body.dataset.bibleWired = '1';
  body.addEventListener('click', (e) => {
    const t = e.target.closest('[data-bible-id],[data-bible-fly]');
    if (!t) return;
    if (t.dataset.bibleFly) {
      const p = placeById(t.dataset.bibleFly);
      if (p) fly(app, p, Number(t.dataset.m || 0));
    } else {
      openBiblePlace(app, t.dataset.bibleId, t.dataset.m != null ? Number(t.dataset.m) : null);
    }
  });
}

/** Open a place card; `mIdx` = the method whose point was clicked (null: fly to the first one). */
export function openBiblePlace(app, id, mIdx = null, { fly: doFly = true } = {}) {
  loadBible().then(() => {
    const p = placeById(id);
    if (!p) return;
    app.panel.show(biblePlaceView(app, p, mIdx));
    if (doFly) fly(app, p, mIdx ?? p.methods.indexOf(firstGeo(p)));
  });
}

// ----------------------------------------------------------------- "מה יש כאן?" section
/** Places of the Tanach near a point ('' when there are none or the data is not loaded yet). */
export function biblePointHtml(lat, lon) {
  if (!DATA || !Number.isFinite(lat)) return '';
  const near = [];
  for (const p of DATA.places) {
    p.methods.forEach((m, i) => {
      if (!m.geo || (m.geo[2] || 0) < 10) return; // only specific sites, not lands or regions
      const d = distKm(lat, lon, m.geo[0], m.geo[1]);
      if (d <= NEAR_KM) near.push({ p, i, d });
    });
  }
  if (!near.length) return '';
  near.sort((a, b) => a.d - b.d);
  const seen = new Set();
  const rows = near.filter((x) => !seen.has(x.p.id) && seen.add(x.p.id)).slice(0, 4);
  return `<section class="bible-here" aria-labelledby="bible-here-t"><h3 class="subtitle" id="bible-here-t">מקומות מהתנ״ך בסביבה</h3>
    <ul class="results">${rows.map(({ p, i, d }) => `<li class="result"><button type="button" data-bible-id="${esc(p.id)}" data-m="${i}">${dot(p.cat)}
      <span class="r-body"><span class="r-name">${esc(p.name)}</span><span class="r-sub" style="display:block">${esc(catOf(p.cat)?.label || '')} · ${d < 1 ? `${Math.round(d * 1000)} מ'` : `${d.toFixed(1)} ק"מ`}
      ${p.methods.length > 1 ? ` · לפי ${esc(p.methods[i].label)}` : ''}</span></span></button></li>`).join('')}</ul></section>`;
}

export function wireBiblePoint(body, app) {
  if (body.querySelector('.bible-here')) delegate(body, app);
}

// ----------------------------------------------------------------- panel views
export function bibleHomeView(app) {
  let cat = '';
  return {
    key: 'bible:home', label: 'מקומות בתנ״ך',
    render(body) {
      if (!DATA) {
        body.innerHTML = `${head(app, 'מקומות בתנ״ך')}<p class="p-empty">נתוני המקומות אינם זמינים.</p>`;
        wireHead(body, app);
        return;
      }
      const on = app.map.layerOn('bible');
      body.innerHTML = `${head(app, 'מקומות בתנ״ך', { sub: `${DATA.places.length} מקומות` })}
        <p class="r-sub bible-pad">ערים, הרים, נהרות, מדבריות, ארצות ותחנות המסעות מהתנ״ך ומדברי חז״ל, עם שיטות הזיהוי. המיקומים משוערים.</p>
        <div class="bible-tools">
          <button type="button" class="btn btn-secondary btn-sm" id="bible-toggle" aria-pressed="${on}">${svg('layers', { size: 16 })}${on ? 'הסתרה מהמפה' : 'הצגה במפה'}</button>
        </div>
        <div class="bible-cats" role="group" aria-label="סינון לפי סוג">
          <button type="button" class="filter-btn" data-cat="" aria-pressed="true">הכול</button>
          ${DATA.cats.map((c) => `<button type="button" class="filter-btn" data-cat="${esc(c.id)}" aria-pressed="false">
            <span class="bible-swatch" style="background:${esc(CAT_COLORS[c.id] || '#6B7280')}" aria-hidden="true"></span>${esc(c.label)}</button>`).join('')}
        </div>
        <label class="bible-filter"><span class="sr-only">חיפוש מקום</span>
          <input type="search" id="bible-q" placeholder="חיפוש מקום או שם נוסף…" autocomplete="off" data-autofocus></label>
        <ul class="results" id="bible-list"></ul>
        <p class="r-sub bible-pad">${esc(CREDIT)}</p>`;
      wireHead(body, app);
      delegate(body, app);
      const list = body.querySelector('#bible-list');
      const q = body.querySelector('#bible-q');
      const draw = () => {
        const v = q.value.trim();
        const items = DATA.places.filter((p) => (!cat || p.cat === cat)
          && (!v || p.name.includes(v) || p.aliases.some((a) => a.includes(v)) || (p.region || '').includes(v)));
        list.innerHTML = items.length ? items.map((p) => `<li class="result"><button type="button" data-bible-id="${esc(p.id)}">${dot(p.cat)}
          <span class="r-body"><span class="r-name">${esc(p.name)}</span><span class="r-sub" style="display:block">${esc(subLine(p))}</span>
          ${firstGeo(p) ? '' : '<span class="r-sub" style="display:block">(ללא מיקום במפה)</span>'}</span></button></li>`).join('')
          : '<li class="p-empty">לא נמצאו מקומות.</li>';
        announce(`${items.length} מקומות`);
      };
      draw();
      q.addEventListener('input', draw);
      body.querySelectorAll('[data-cat]').forEach((b) => b.addEventListener('click', () => {
        cat = b.dataset.cat;
        body.querySelectorAll('[data-cat]').forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
        draw();
      }));
      body.querySelector('#bible-toggle').addEventListener('click', (e) => {
        const show = !app.map.layerOn('bible');
        app.map.setLayer('bible', show);
        app._redrawLayerChecks?.();
        e.currentTarget.setAttribute('aria-pressed', String(show));
        e.currentTarget.innerHTML = `${svg('layers', { size: 16 })}${show ? 'הסתרה מהמפה' : 'הצגה במפה'}`;
        announce(show ? 'המקומות מוצגים במפה' : 'המקומות הוסתרו מהמפה');
      });
    },
  };
}

export function biblePlaceView(app, p, mIdx = null) {
  return {
    key: 'bible:' + p.id, label: `${p.name} – מקומות בתנ״ך`,
    render(body) {
      const tags = [p.refuge && 'עיר מקלט', p.levite && 'עיר הלויים', p.border && 'מגבולות הארץ'].filter(Boolean);
      const method = (m, i) => `<li class="bible-method${i === mIdx ? ' current' : ''}">
        <div class="bible-mhead">${p.methods.length > 1 || m.label !== 'על המקום' ? `<span class="r-name">${esc(m.label)}</span>` : ''}
          <span class="bible-conf ${esc(m.level)}" title="${esc(m.confidence)}">${esc(m.confidence || LEVEL_HE[m.level])}</span></div>
        ${m.explanation ? `<p>${esc(m.explanation)}</p>` : ''}
        ${m.modern ? `<p class="r-sub"><b>היום:</b> ${esc(m.modern)}</p>` : ''}
        ${m.geo ? `<button type="button" class="link-btn" data-bible-fly="${esc(p.id)}" data-m="${i}">${svg('pin', { size: 14 })}הצגה במפה</button>` : ''}
      </li>`;
      body.innerHTML = `${head(app, p.name, { sub: catOf(p.cat)?.label || '' })}
        <div class="card-head">
          ${p.aliases.length ? `<div class="r-sub">נקרא גם: ${esc(p.aliases.join(', '))}</div>` : ''}
          ${p.region ? `<div class="r-sub">נחלת ${esc(p.region)}</div>` : ''}
          ${tags.length ? `<div class="bible-tags">${tags.map((t) => `<span class="bible-tag">${esc(t)}</span>`).join('')}</div>` : ''}
        </div>
        <h3 class="subtitle">${p.methods.length > 1 ? 'שיטות הזיהוי' : 'זיהוי המקום'}</h3>
        <ul class="bible-list">${p.methods.map(method).join('')}</ul>
        ${p.verses.length ? `<h3 class="subtitle">פסוקים</h3><ul class="bible-list">${p.verses.map((v) =>
          `<li class="bible-verse"><div class="bible-ref">${esc(v.ref)}</div><p class="bible-text">${esc(v.text)}</p></li>`).join('')}</ul>` : ''}
        ${p.midrash.length ? `<h3 class="subtitle">בדברי חז״ל</h3><ul class="bible-list">${p.midrash.map((x) =>
          `<li class="bible-verse"><div class="bible-ref">${esc(x.source)}</div><p class="bible-text">${esc(x.note)}</p></li>`).join('')}</ul>` : ''}
        <p class="r-sub bible-pad">המיקומים משוערים לפי שיטות הזיהוי. ${esc(CREDIT)}</p>`;
      wireHead(body, app);
      delegate(body, app);
    },
  };
}
