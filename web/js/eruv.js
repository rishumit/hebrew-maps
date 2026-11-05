// Eruvin (עירובין) and תחום שבת – data of מוקד העירוב (via the "גבולות עירוב" plugin, with their permission).
// web/eruv/eruv.json is built by tools/import_eruv.py. The lines on the map are an approximation traced
// from the מוקד's scans: the scan and the local person in charge decide, never the line.

import { svg } from './icons.js';
import { head, wireHead } from './views.js';
import { announce, copyText, esc } from './util.js';

export const ERUV_NOTICE = 'המפות להתמצאות בלבד. עירוב יכול להיפסל בכל שבוע, ולכן לפני שבת יש לוודא מול האחראי המקומי.';
const LINE_NOTE = 'הקו משורטט בקירוב לפי מפת המוקד. הקובע הוא המפה המקורית והאחראי המקומי.';
const NEAR_M = 250;

let DATA = null;
let loading = null;
let ABROAD = null;

/** web/eruv/eruv.json (once). Resolves to null when the file is missing – the feature then stays hidden. */
export function loadEruv() {
  if (!loading) {
    loading = fetch('/eruv/eruv.json')
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => (DATA = d))
      .catch(() => null);
  }
  return loading;
}
export const eruvData = () => DATA;

function loadAbroad() {
  if (ABROAD) return Promise.resolve(ABROAD);
  return fetch('/eruv/abroad.json').then((r) => (r.ok ? r.json() : null)).then((d) => (ABROAD = d)).catch(() => null);
}

// ----------------------------------------------------------------- map layers (installed by MapView)
export const ERUV_CLICK_LAYERS = ['eruv-line'];

export function eruvLayers(dark) {
  const color = dark ? '#C4B5FD' : '#6D28D9';
  return [
    { id: 'eruv-fill', type: 'fill', source: 'eruv', minzoom: 11, filter: ['==', ['geometry-type'], 'Polygon'],
      paint: { 'fill-color': color, 'fill-opacity': dark ? 0.1 : 0.07 } },
    { id: 'eruv-line', type: 'line', source: 'eruv', minzoom: 11,
      layout: { 'line-join': 'round', 'line-cap': 'round' },
      paint: { 'line-color': color, 'line-opacity': 0.9, 'line-dasharray': ['literal', [2, 1.4]],
        'line-width': ['interpolate', ['linear'], ['zoom'], 11, 1.2, 14, 2.2, 17, 3.5] } },
    { id: 'eruv-label', type: 'symbol', source: 'eruv', minzoom: 14,
      layout: { 'text-field': ['concat', 'עירוב ', ['get', 'name']], 'text-font': ['Noto Sans Regular'], 'text-size': 11,
        'symbol-placement': 'line', 'symbol-spacing': 500, 'text-max-angle': 30, 'text-offset': [0, 0.9] },
      paint: { 'text-color': color, 'text-halo-color': dark ? '#121418' : '#FFFFFF', 'text-halo-width': 1.5 } },
  ];
}

export const eruvLines = () => DATA?.lines || { type: 'FeatureCollection', features: [] };

// ----------------------------------------------------------------- geometry
function inRing(lon, lat, ring) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i], [xj, yj] = ring[j];
    if ((yi > lat) !== (yj > lat) && lon < ((xj - xi) * (lat - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

/** Distance in metres from a point to a line (equirectangular – fine at city scale). */
function distToLine(lon, lat, pts) {
  const kx = 111320 * Math.cos((lat * Math.PI) / 180), ky = 110540;
  let best = Infinity;
  for (let i = 1; i < pts.length; i++) {
    const ax = (pts[i - 1][0] - lon) * kx, ay = (pts[i - 1][1] - lat) * ky;
    const bx = (pts[i][0] - lon) * kx, by = (pts[i][1] - lat) * ky;
    const dx = bx - ax, dy = by - ay, len = dx * dx + dy * dy;
    const t = len ? Math.max(0, Math.min(1, -(ax * dx + ay * dy) / len)) : 0;
    best = Math.min(best, Math.hypot(ax + t * dx, ay + t * dy));
  }
  return best;
}

const coordsOf = (f) => (f.geometry.type === 'Polygon' ? f.geometry.coordinates[0] : f.geometry.coordinates);

function bboxOf(f) {
  let minx = 180, miny = 90, maxx = -180, maxy = -90;
  for (const [x, y] of coordsOf(f)) {
    minx = Math.min(minx, x); maxx = Math.max(maxx, x); miny = Math.min(miny, y); maxy = Math.max(maxy, y);
  }
  return [minx, miny, maxx, maxy];
}

/** The approximate eruv lines at a point: { inside: [features], near: {feature, dist} | null }. */
export function eruvAt(lat, lon) {
  const out = { inside: [], near: null };
  for (const f of eruvLines().features) {
    const pts = coordsOf(f);
    if (f.geometry.type === 'Polygon' && inRing(lon, lat, pts)) out.inside.push(f);
    else {
      const d = distToLine(lon, lat, pts);
      if (d <= NEAR_M && (!out.near || d < out.near.dist)) out.near = { feature: f, dist: d };
    }
  }
  return out;
}

/** "לוד" matches "לוד (אחיסמך)", "קרית יערים" matches "קרית יערים (טלזסטון)". */
function matchCity(name) {
  if (!DATA || !name) return null;
  const n = String(name).replace(/["״']/g, '').replace('קריית', 'קרית').replace('תקווה', 'תקוה').trim();
  return DATA.cities.find((c) => {
    const base = c.replace(/\s*\(.*\)$/, '').replace(/["״']/g, '');
    return base === n || c === name || n.startsWith(base + ' ');
  }) || null;
}

// ----------------------------------------------------------------- small HTML helpers
const ext = (url, label) => `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(label)}${svg('external', { size: 14 })}<span class="sr-only"> (נפתח בחלון חדש)</span></a>`;
const notice = () => `<p class="eruv-notice" role="note">${svg('alert', { size: 16 })}<span>${esc(ERUV_NOTICE)}</span></p>`;
const phoneChip = (p) => `<span class="eruv-phone"><a href="tel:${esc(p.replace(/[^\d+]/g, ''))}" dir="ltr">${esc(p)}</a><button type="button" class="ir-btn" data-copy-phone="${esc(p)}" aria-label="העתקת המספר ${esc(p)}">${svg('copy', { size: 16 })}</button></span>`;
const mokedLine = () => DATA ? `<p class="eruv-src">מקור: ${ext(DATA.source.site, DATA.source.name)} · ${phoneChip(DATA.source.phone)}<br>${esc(DATA.source.note || '')}</p>` : '';
const mapById = (id) => DATA?.maps.find((m) => m.id === id);
const areasOfMap = (id) => DATA ? DATA.areas.filter((a) => a.maps.includes(id)) : [];
const docsOfCity = (city) => DATA ? DATA.docs.filter((d) => d.cities.includes(city) || d.cities.includes(city.replace(/\s*\(.*\)$/, ''))) : [];
const linesOfCity = (city) => eruvLines().features.filter((f) => f.properties.city === city);

function areaHtml(a) {
  const names = a.names?.length ? a.names.join(', ') : '';
  return `<li class="eruv-area">
    <div class="r-name">${a.n ? `<span class="eruv-n">${esc(a.n)}</span>` : ''}${esc(a.area || 'אזור')}</div>
    ${names ? `<div class="r-sub">${esc(names)}</div>` : ''}
    ${a.phones?.length ? `<div class="eruv-phones">${a.phones.map(phoneChip).join('')}</div>` : ''}
    ${(a.emails || []).map(([label, mail]) => `<div class="r-sub"><a href="mailto:${esc(mail)}">${esc(mail)}</a> (${esc(label)})</div>`).join('')}
    ${(a.links || []).map(([label, url]) => `<div class="r-sub">${ext(url, label)}</div>`).join('')}
  </li>`;
}

function mapRowHtml(m) {
  const line = eruvLines().features.find((f) => f.properties.map === m.id);
  return `<li class="eruv-map">
    <div class="r-name">${esc(m.title)}</div>
    <div class="eruv-links">${ext(m.imageUrl, 'מפת העירוב (תמונה)')} · ${ext(m.pageUrl, 'בעמוד המוקד')}
      ${line ? ` · <button type="button" class="link-btn" data-eruv-fit="${esc(m.id)}">${svg('pin', { size: 14 })}הצגת הקו במפה</button>` : ''}</div>
  </li>`;
}

function techumHtml(city) {
  const ds = docsOfCity(city);
  if (!ds.length) {
    return `<p class="r-sub eruv-pad">אין עדיין מאמר תחום שבת לעיר זו במאגר. לבירור התחום ועירוב תחומין: מוקד העירוב ${phoneChip(DATA.source.phone)}</p>`;
  }
  return `<ul class="eruv-list">${ds.map((d) => {
    const part = d.parts?.find((p) => p.cities.includes(city));
    const sm = d.summary;
    return `<li class="eruv-map"><div class="r-name">${esc(d.title)}</div>
      ${part ? `<div class="r-sub">ראו ${esc(part.title)} (עמ' ${part.from}–${part.to})</div>` : ''}
      ${sm ? `${sm.intro ? `<p class="r-sub">${esc(sm.intro)}</p>` : ''}<dl class="eruv-sum">
        ${sm.near ? `<dt>מותר להגיע (בתוך התחום)</dt><dd>${esc(sm.near)}</dd>` : ''}
        ${sm.eruv ? `<dt>בעירוב תחומין</dt><dd>${esc(sm.eruv)}</dd>` : ''}
        ${sm.far ? `<dt>מחוץ לתחום</dt><dd>${esc(sm.far)}</dd>` : ''}</dl>` : ''}
      <div class="eruv-links">${ext(d.url, d.kind === 'booklet' ? 'החוברת (PDF)' : 'המאמר באתר המוקד')}</div></li>`;
  }).join('')}</ul>
  <p class="r-sub eruv-pad">החישוב תלוי בשיטות שונות – לשאלה למעשה יש לפנות לרב או למוקד.</p>`;
}

/** Shared wiring of every eruv view: the head buttons, plus one delegated click handler on the panel body
 * (the body element is reused by every view, so the handler is installed once). */
function wire(body, app) {
  wireHead(body, app);
  delegate(body, app);
}

function delegate(body, app) {
  if (body.dataset.eruvWired) return;
  body.dataset.eruvWired = '1';
  body.addEventListener('click', (e) => {
    const t = e.target.closest('[data-copy-phone],[data-eruv-city],[data-eruv-map],[data-eruv-fit],[data-eruv-view],[data-eruv-search]');
    if (!t) return;
    if (t.dataset.copyPhone) copyText(t.dataset.copyPhone, 'מספר הטלפון הועתק');
    else if (t.dataset.eruvCity) app.panel.show(eruvCityView(app, t.dataset.eruvCity));
    else if (t.dataset.eruvMap) app.panel.show(eruvMapView(app, t.dataset.eruvMap));
    else if (t.dataset.eruvFit) showLine(app, t.dataset.eruvFit);
    else if (t.dataset.eruvSearch) app.search(t.dataset.eruvSearch, {});
    else if (t.dataset.eruvView === 'home') app.panel.show(eruvHomeView(app));
    else if (t.dataset.eruvView === 'hosp') app.panel.show(hospitalsView(app));
    else if (t.dataset.eruvView === 'abroad') app.panel.show(abroadView(app));
    else if (t.dataset.eruvView === 'library') app.panel.show(libraryView(app));
  });
}

/** Fit the map to an eruv line, highlight it and make sure the layer is shown. */
export function showLine(app, mapId) {
  const f = eruvLines().features.find((x) => x.properties.map === mapId);
  if (!f) return;
  if (!app.map.layerOn('eruv')) {
    app.map.setLayer('eruv', true);
    app._redrawLayerChecks?.();
  }
  app.map.setHighlight(f.geometry);
  app.map.fitBounds(bboxOf(f), 17);
  announce(`מוצג קו העירוב המשוער: ${f.properties.name}`);
}

// ----------------------------------------------------------------- the "מה יש כאן?" / address card section
/** Eruv section for a point card ('' when nothing is known about the place; NaN lat/lon = the city only). */
export function eruvPointHtml(lat, lon, city) {
  if (!DATA) return '';
  const at = eruvAt(lat, lon);
  const c = matchCity(city) || (at.inside[0] || at.near?.feature)?.properties.city || null;
  if (!at.inside.length && !at.near && !c) return '';
  const rows = [];
  for (const f of at.inside) {
    rows.push(`<li><b>בתוך קו העירוב המשוער</b> – ${esc(f.properties.name)}${f.properties.resp ? ` <span class="r-sub">(${esc(f.properties.resp)})</span>` : ''}
      <button type="button" class="link-btn" data-eruv-map="${esc(f.properties.map)}">פרטים</button></li>`);
  }
  if (!at.inside.length && at.near) {
    const f = at.near.feature;
    rows.push(`<li><b>סמוך לקו העירוב המשוער</b> (כ-${Math.round(at.near.dist / 10) * 10} מ') – ${esc(f.properties.name)}
      <button type="button" class="link-btn" data-eruv-map="${esc(f.properties.map)}">פרטים</button></li>`);
  }
  if (c) {
    const n = DATA.areas.filter((a) => a.city === c).length;
    const maps = DATA.maps.filter((x) => x.city === c).length;
    const what = n ? (n === 1 ? 'אזור עירוב אחד' : `${n} אזורי עירוב`) : (maps === 1 ? 'מפת עירוב אחת' : `${maps} מפות עירוב`);
    rows.push(`<li>ב${esc(c)} ${what} במאגר מוקד העירוב
      <button type="button" class="link-btn" data-eruv-city="${esc(c)}">אחראים ומפות</button></li>`);
  }
  return `<section class="eruv-here" aria-labelledby="eruv-here-t"><h3 class="subtitle" id="eruv-here-t">עירוב ותחום שבת</h3>
    <ul class="eruv-here-list">${rows.join('')}</ul>
    ${at.inside.length || at.near ? `<p class="r-sub eruv-pad">${esc(LINE_NOTE)}</p>` : ''}${notice()}</section>`;
}

/** Wire the section above inside a card that the views module rendered. */
export function wireEruvPoint(body, app) {
  if (body.querySelector('.eruv-here')) delegate(body, app);
}

// ----------------------------------------------------------------- panel views
export function eruvHomeView(app) {
  return {
    key: 'eruv:home', label: 'עירובין ותחום שבת',
    render(body) {
      if (!DATA) {
        body.innerHTML = `${head(app, 'עירובין ותחום שבת')}<p class="p-empty">נתוני העירובין אינם זמינים.</p>`;
        wireHead(body, app);
        return;
      }
      const count = (c) => DATA.areas.filter((a) => a.city === c).length;
      const cityBtn = (c) => `<li class="result"><button type="button" data-eruv-city="${esc(c)}" data-name="${esc(c)}">
        <span class="r-icon neutral" aria-hidden="true">${svg('synagogue', { size: 18 })}</span>
        <span class="r-body"><span class="r-name">${esc(c)}</span><span class="r-sub" style="display:block">${count(c) ? (count(c) === 1 ? 'אזור אחד' : `${count(c)} אזורים`) : `${DATA.maps.filter((x) => x.city === c).length} מפות`}${docsOfCity(c).length ? ' · יש מאמר תחום שבת' : ''}${linesOfCity(c).length ? ' · קו במפה' : ''}</span></span></button></li>`;
      body.innerHTML = `${head(app, 'עירובין ותחום שבת', { sub: `${DATA.cities.length} ערים` })}
        ${notice()}
        <div class="eruv-tabs">
          <button type="button" class="filter-btn" data-eruv-view="hosp">${svg('hospital', { size: 16 })}בתי חולים בשבת</button>
          <button type="button" class="filter-btn" data-eruv-view="abroad">${svg('globe', { size: 16 })}עירובין בחו"ל</button>
          <button type="button" class="filter-btn" data-eruv-view="library">${svg('book', { size: 16 })}חוברות וספרייה</button>
        </div>
        <label class="eruv-filter"><span class="sr-only">סינון ערים</span>
          <input type="search" id="eruv-q" placeholder="סינון לפי עיר…" autocomplete="off" data-autofocus></label>
        <ul class="results" id="eruv-cities">${DATA.cities.map(cityBtn).join('')}</ul>
        ${mokedLine()}`;
      wire(body, app);
      const q = body.querySelector('#eruv-q');
      q.addEventListener('input', () => {
        const v = q.value.trim();
        let shown = 0;
        body.querySelectorAll('#eruv-cities .result').forEach((li) => {
          const ok = !v || li.querySelector('button').dataset.name.includes(v);
          li.hidden = !ok;
          if (ok) shown++;
        });
        announce(`${shown} ערים`);
      });
    },
  };
}

export function eruvCityView(app, city) {
  return {
    key: 'eruv:city:' + city, label: `עירובין ב${city}`,
    render(body) {
      const maps = DATA.maps.filter((m) => m.city === city);
      const areas = DATA.areas.filter((a) => a.city === city);
      body.innerHTML = `${head(app, city, { sub: 'עירובין ותחום שבת' })}
        ${notice()}
        <div class="eruv-tabs"><button type="button" class="filter-btn" data-eruv-search="${esc(city.replace(/\s*\(.*\)$/, ''))}">${svg('search', { size: 16 })}הצגת העיר במפה</button></div>
        ${maps.length ? `<h3 class="subtitle">מפות העירוב</h3><ul class="eruv-list">${maps.map(mapRowHtml).join('')}</ul>` : ''}
        <h3 class="subtitle">אזורים ואחראים</h3>
        ${areas.length ? `<ul class="eruv-list">${areas.map(areaHtml).join('')}</ul>`
          : linesOfCity(city).some((f) => f.properties.resp)
            ? `<ul class="eruv-list">${linesOfCity(city).filter((f) => f.properties.resp).map((f) => `<li class="eruv-area"><div class="r-name">${esc(f.properties.name)}</div><div class="r-sub">${esc(f.properties.resp)}</div></li>`).join('')}</ul>`
            : '<p class="p-empty">אין פרטים.</p>'}
        <h3 class="subtitle">תחום שבת</h3>${techumHtml(city)}
        ${mokedLine()}`;
      wire(body, app);
    },
  };
}

/** Card of one eruv map (also what a click on its line opens). */
export function eruvMapView(app, mapId) {
  return {
    key: 'eruv:map:' + mapId, label: 'פרטי עירוב',
    render(body) {
      const m = mapById(mapId);
      const line = eruvLines().features.find((f) => f.properties.map === mapId);
      const city = m?.city || line?.properties.city || '';
      const title = line?.properties.name || m?.title || 'עירוב';
      const areas = areasOfMap(mapId);
      body.innerHTML = `${head(app, title, { sub: city })}
        ${notice()}
        ${line ? `<p class="r-sub eruv-pad">${esc(LINE_NOTE)}</p>` : ''}
        ${line?.properties.resp && !areas.length ? `<h3 class="subtitle">אחראי</h3><p class="eruv-pad">${esc(line.properties.resp)}</p>` : ''}
        ${m ? `<h3 class="subtitle">המפה המקורית</h3><ul class="eruv-list">${mapRowHtml(m)}</ul>` : ''}
        ${areas.length ? `<h3 class="subtitle">אזורים ואחראים</h3><ul class="eruv-list">${areas.map(areaHtml).join('')}</ul>` : ''}
        ${city ? `<div class="eruv-tabs"><button type="button" class="filter-btn" data-eruv-city="${esc(city)}">כל העירובין ב${esc(city)}</button></div>` : ''}
        ${mokedLine()}`;
      wire(body, app);
    },
  };
}

function hospitalsView(app) {
  return {
    key: 'eruv:hosp', label: 'בתי חולים בשבת',
    render(body) {
      const info = DATA.hospitals_info || {};
      const hs = info.hospitals || [];
      const card = (h) => `<li class="eruv-map">
        <div class="r-name">${esc(h.name)} <span class="r-sub">· ${esc(h.city)}</span></div>
        ${(h.contacts || []).map((c) => `<div class="eruv-contact"><span class="r-sub">${esc(c.role)}${c.name ? ` – ${esc(c.name)}` : ''}</span>
          ${c.phone ? phoneChip(c.phone) : ''}${c.phone2 ? phoneChip(c.phone2) : ''}${c.email ? ` <a href="mailto:${esc(c.email)}">${esc(c.email)}</a>` : ''}</div>`).join('')}
        ${h.eruv ? `<p class="eruv-txt"><b>עירוב:</b> ${esc(h.eruv)}</p>` : ''}
        ${h.techum ? `<p class="eruv-txt"><b>תחום שבת:</b> ${esc(h.techum)}</p>` : ''}
        <div class="eruv-links">${h.src ? ext(h.src, h.srcName || 'מקור') : ''}${h.abroad ? '' : ` · <button type="button" class="link-btn" data-eruv-search="${esc(h.name + ' ' + h.city)}">${svg('search', { size: 14 })}במפה</button>`}</div>
      </li>`;
      body.innerHTML = `${head(app, 'בתי חולים בשבת')}
        <p class="r-sub eruv-pad">רב בית החולים, שירותי הדת, העירוב במתחם ותחום שבת – רק ממקורות רשמיים של בתי החולים.</p>
        <h3 class="subtitle">בישראל</h3><ul class="eruv-list">${hs.filter((h) => !h.abroad).map(card).join('')}</ul>
        ${hs.some((h) => h.abroad) ? `<h3 class="subtitle">בחו"ל</h3><ul class="eruv-list">${hs.filter((h) => h.abroad).map(card).join('')}</ul>` : ''}
        ${info.ezer ? `<p class="eruv-txt eruv-pad">${esc(info.ezer)}</p>` : ''}
        ${(info.orgs || []).length ? `<h3 class="subtitle">ארגונים</h3><ul class="eruv-list">${info.orgs.map((o) => `<li class="eruv-map">
          <div class="r-name">${esc(o.name)}</div><div class="r-sub">${esc(o.what || '')}</div>
          ${o.phone ? `<div class="eruv-phones">${phoneChip(o.phone)}</div>` : ''}${o.url ? `<div class="eruv-links">${ext(o.url, o.src || 'אתר')}</div>` : ''}</li>`).join('')}</ul>` : ''}
        ${(info.more || []).length ? `<h3 class="subtitle">עמודי שירותי דת נוספים</h3><ul class="eruv-list">${info.more.map(([t, u]) => `<li class="eruv-map">${ext(u, t)}</li>`).join('')}</ul>` : ''}
        ${notice()}`;
      wire(body, app);
    },
  };
}

function abroadView(app) {
  return {
    key: 'eruv:abroad', label: 'עירובין בחו"ל',
    render(body) {
      body.innerHTML = `${head(app, 'עירובין בחו"ל')}<div class="skeleton"><div class="sk-line w80"></div><div class="sk-line w60"></div></div>`;
      wireHead(body, app);
      loadAbroad().then((x) => {
        if (app.panel.current?.key !== 'eruv:abroad') return;
        const list = x?.abroad || [];
        const byCountry = new Map();
        for (const e of list) {
          const k = e.ch || e.c || 'אחר';
          if (!byCountry.has(k)) byCountry.set(k, []);
          byCountry.get(k).push(e);
        }
        const row = (e) => `<li class="eruv-map" data-name="${esc([e.ch, e.c, e.p, e.r].join(' ').toLowerCase())}">
          <div class="r-name" dir="auto">${esc(e.p)}${e.r ? ` <span class="r-sub">· ${esc(e.r)}</span>` : ''}</div>
          ${e.in ? `<div class="r-sub" dir="auto">${esc(e.in)}</div>` : ''}
          ${e.ph ? `<div class="eruv-phones">${phoneChip(e.ph)}</div>` : ''}
          <div class="eruv-links">${[e.u && ext(e.u, 'אתר העירוב'), e.m && ext(e.m, 'מפה'), e.eo && e.eo !== e.u && ext(e.eo, 'eruv.org'),
            e.em && `<a href="mailto:${esc(e.em)}">${esc(e.em)}</a>`].filter(Boolean).join(' · ')}</div></li>`;
        body.innerHTML = `${head(app, 'עירובין בחו"ל', { sub: `${list.length} מקומות` })}
          <label class="eruv-filter"><span class="sr-only">סינון</span><input type="search" id="eruv-aq" placeholder="סינון לפי ארץ או עיר…" autocomplete="off" data-autofocus></label>
          ${[...byCountry].map(([k, es]) => `<section class="eruv-country"><h3 class="subtitle">${esc(k)}</h3><ul class="eruv-list">${es.map(row).join('')}</ul></section>`).join('')}
          <p class="r-sub eruv-pad">לפני שבת יש לבדוק את מצב העירוב באתר או בקו הסטטוס המקומי.</p>`;
        wire(body, app);
        const q = body.querySelector('#eruv-aq');
        q.focus({ preventScroll: true });
        q.addEventListener('input', () => {
          const v = q.value.trim().toLowerCase();
          body.querySelectorAll('.eruv-country').forEach((sec) => {
            let any = false;
            sec.querySelectorAll('[data-name]').forEach((li) => {
              const ok = !v || li.dataset.name.includes(v) || sec.querySelector('h3').textContent.includes(v);
              li.hidden = !ok;
              any ||= ok;
            });
            sec.hidden = !any;
          });
        });
      });
    },
  };
}

function libraryView(app) {
  return {
    key: 'eruv:library', label: 'חוברות וספרייה',
    render(body) {
      const booklets = DATA.docs.filter((d) => d.kind === 'booklet');
      body.innerHTML = `${head(app, 'חוברות וספרייה')}
        <h3 class="subtitle">חוברות תחום שבת</h3>
        <ul class="eruv-list">${booklets.map((d) => `<li class="eruv-map"><div class="r-name">${esc(d.title)}</div><div class="eruv-links">${ext(d.url, 'PDF')}</div></li>`).join('')}</ul>
        <div id="eruv-lib"><div class="skeleton"><div class="sk-line w80"></div></div></div>
        ${mokedLine()}`;
      wire(body, app);
      loadAbroad().then((x) => {
        const host = body.querySelector('#eruv-lib');
        if (!host) return;
        const groups = new Map();
        for (const it of x?.library || []) {
          if (!groups.has(it.g)) groups.set(it.g, []);
          groups.get(it.g).push(it);
        }
        host.innerHTML = [...groups].map(([g, items]) => `<h3 class="subtitle">${esc(g)}</h3>
          <ul class="eruv-list">${items.map((it) => `<li class="eruv-map">${ext(it.u, it.t)}</li>`).join('')}</ul>`).join('');
      });
    },
  };
}
