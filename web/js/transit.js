// Public transport mode of the route planner: time selection, itinerary list, leg-by-leg card,
// map layers (coloured rides, dashed walking), the stop card (departures + every line serving the stop)
// and the line view (ordered stops of one direction, drawn on the map).

import { api } from './api.js';
import { svg } from './icons.js';
import { head, wireHead } from './views.js';
import { announce, bus, esc } from './util.js';

const MODE_ICON = {
  bus: 'bus', rail: 'train', light_rail: 'tram', subway: 'train', funicular: 'tram', cable_car: 'tram',
  share_taxi: 'taxi', ferry: 'wave', walk: 'walk',
};
const TAGS = {
  earliest_arrival: 'הגעה מוקדמת', latest_departure: 'יציאה מאוחרת', fastest: 'הכי מהיר',
  fewest_transfers: 'הכי מעט החלפות', least_walking: 'הכי מעט הליכה', walk_only: 'הליכה בלבד',
};
const SRC = 'transit-legs';
const SRC_STOPS = 'transit-stops';
const SRC_LINE = 'transit-line';
const SRC_LINE_STOPS = 'transit-line-stops';
const LINE_KEY = 'transit-line:';
const EMPTY = { type: 'FeatureCollection', features: [] };

function localInputValue(d = new Date()) {
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}

export function badge(leg, { small = false } = {}) {
  if (leg.type === 'walk') {
    return `<span class="tr-badge tr-walk${small ? ' sm' : ''}">${svg('walk', { size: small ? 14 : 16 })}<span>${esc(leg.duration_text)}</span></span>`;
  }
  const label = leg.route_short_name || (leg.type === 'rail' ? 'רכבת' : leg.mode_he);
  return `<span class="tr-badge${small ? ' sm' : ''}" style="background:${esc(leg.color)};color:${esc(leg.text_color)}">${svg(MODE_ICON[leg.type] || 'bus', { size: small ? 14 : 16 })}<span>${esc(label)}</span></span>`;
}

/** A line badge that opens the line view (route id + the stop it is boarded at). */
export function lineButton(x, fromStop, { small = true, direction } = {}) {
  if (!x.route_id) return badge(x, { small });
  const name = x.line || x.route_short_name || x.mode_he;
  const label = `${name}${x.agency ? ', ' + x.agency : ''}${x.headsign ? ' לכיוון ' + x.headsign : ''} – כל התחנות של הקו`;
  return `<button type="button" class="tr-line-btn" data-line="${esc(x.route_id)}"${fromStop ? ` data-from="${esc(fromStop)}"` : ''}${direction != null ? ` data-dir="${esc(direction)}"` : ''} aria-label="${esc(label)}">${badge(x, { small })}</button>`;
}

/** After a view re-renders with its data, the focused skeleton element is gone: move focus to the title. */
function keepFocus(body) {
  if (body.contains(document.activeElement) || (document.activeElement && document.activeElement !== document.body && document.activeElement.isConnected)) return;
  const h = body.querySelector('h2');
  if (!h) return;
  h.setAttribute('tabindex', '-1');
  h.focus({ preventScroll: true });
}

function offsetText(secs) {
  const m = Math.round(secs / 60);
  if (m < 60) return `${m} דק׳`;
  return `${Math.floor(m / 60)}:${String(m % 60).padStart(2, '0')} ש׳`;
}

function transfersText(n) {
  return n === 0 ? 'ללא החלפות' : n === 1 ? 'החלפה אחת' : `${n} החלפות`;
}

export class TransitPlanner {
  constructor(app, dirs) {
    this.app = app;
    this.dirs = dirs;
    this.timeMode = 'now'; // now | depart | arrive
    this.when = localInputValue();
    this.result = null;
    this.selected = -1; // -1 = list, otherwise the itinerary card
    this.status = null;
    this._styleHooked = false;
    this.line = null; // the line view's data while it is the current panel view
    this._lineSeq = 0;
    // the line overlay belongs to the line view: drop it when any other view takes the panel
    bus.on('panel:view', (view) => { if (!view?.key?.startsWith(LINE_KEY)) this.clearLine(); });
    bus.on('panel:close', () => this.clearLine());
  }

  // ------------------------------------------------------------------ data
  async loadStatus() {
    if (this.status) return this.status;
    try {
      this.status = await api.transitStatus();
    } catch {
      this.status = { available: false, message: 'לא ניתן לבדוק את נתוני התחבורה הציבורית.' };
    }
    return this.status;
  }

  async compute(signal) {
    const d = this.dirs;
    const st = await this.loadStatus();
    if (!st.available) {
      d.loading = false;
      d.error = st.message || 'נתוני התחבורה הציבורית אינם זמינים.';
      return;
    }
    const time = this.timeMode === 'now' ? undefined : `${this.when}:00`;
    const res = await api.transit(d.from, d.to, { time, mode: this.timeMode === 'arrive' ? 'arrive' : 'depart' }, signal);
    this.result = res;
    this.selected = -1;
    d.loading = false;
    d.error = null;
    const its = res.itineraries || [];
    if (!its.length) d.error = res.notice || 'לא נמצא מסלול בתחבורה ציבורית';
    this.draw(true);
    if (its.length) {
      const i = its[0];
      announce(`נמצאו ${its.length} אפשרויות. הראשונה: יציאה ${i.departure_text}, הגעה ${i.arrival_text}, ${transfersText(i.transfers)}${res.notice ? '. ' + res.notice : ''}`);
    }
  }

  // ------------------------------------------------------------------ map
  _layers() {
    const m = this.app.map?.map;
    if (!m) return null;
    if (!this._styleHooked) {
      this._styleHooked = true;
      m.on('style.load', () => { // theme / style switches drop custom layers
        this.draw(false);
        if (this.line) this.drawLine(this.line, false);
      });
    }
    try {
      if (!m.getSource(SRC)) m.addSource(SRC, { type: 'geojson', data: EMPTY });
      if (!m.getSource(SRC_STOPS)) m.addSource(SRC_STOPS, { type: 'geojson', data: EMPTY });
    } catch {
      return null; // style still loading – the style.load hook redraws
    }
    const before = m.getLayer('endpoints') ? 'endpoints' : undefined;
    const add = (layer) => {
      try {
        if (!m.getLayer(layer.id)) m.addLayer(layer, before);
      } catch (e) {
        console.warn('transit layer failed', layer.id, e);
      }
    };
    const w = (a, b) => ['interpolate', ['linear'], ['zoom'], 8, a, 16, b];
    add({ id: 'transit-casing', type: 'line', source: SRC, filter: ['!=', ['get', 'type'], 'walk'],
      layout: { 'line-join': 'round', 'line-cap': 'round' }, paint: { 'line-color': '#FFFFFF', 'line-width': w(6, 12) } });
    add({ id: 'transit-ride', type: 'line', source: SRC, filter: ['!=', ['get', 'type'], 'walk'],
      layout: { 'line-join': 'round', 'line-cap': 'round' }, paint: { 'line-color': ['get', 'color'], 'line-width': w(4, 8) } });
    add({ id: 'transit-walk', type: 'line', source: SRC, filter: ['==', ['get', 'type'], 'walk'],
      layout: { 'line-join': 'round', 'line-cap': 'round' },
      paint: { 'line-color': '#4B5563', 'line-width': w(3, 5), 'line-dasharray': [0.2, 2] } });
    add({ id: 'transit-stop', type: 'circle', source: SRC_STOPS,
      paint: { 'circle-radius': w(4, 7), 'circle-color': '#FFFFFF', 'circle-stroke-width': 3, 'circle-stroke-color': ['get', 'color'] } });
    return m;
  }

  draw(fit) {
    const its = this.result?.itineraries || [];
    const it = its[Math.max(0, this.selected)];
    const m = this._layers();
    if (!m) return;
    const legs = this.dirs.mode === 'transit' && it ? it.legs : [];
    m.getSource(SRC)?.setData({ type: 'FeatureCollection',
      features: legs.map((l) => ({ type: 'Feature', properties: { type: l.type, color: l.color || '#4B5563' }, geometry: l.geometry })) });
    const stops = [];
    for (const l of legs) {
      if (l.type === 'walk') continue;
      for (const s of [l.from, l.to]) stops.push({ type: 'Feature', properties: { color: l.color }, geometry: { type: 'Point', coordinates: [s.lon, s.lat] } });
    }
    m.getSource(SRC_STOPS)?.setData({ type: 'FeatureCollection', features: stops });
    if (legs.length) this.app.map.setRoutes([], 0, 'transit');
    if (fit && legs.length) {
      const pts = legs.flatMap((l) => l.geometry.coordinates.map(([lon, lat]) => ({ lat, lon })));
      this.app.map.fitPoints(pts, 16);
    }
  }

  _lineLayers() {
    const m = this._layers();
    if (!m) return null;
    try {
      if (!m.getSource(SRC_LINE)) m.addSource(SRC_LINE, { type: 'geojson', data: EMPTY });
      if (!m.getSource(SRC_LINE_STOPS)) m.addSource(SRC_LINE_STOPS, { type: 'geojson', data: EMPTY });
    } catch {
      return null;
    }
    const before = m.getLayer('endpoints') ? 'endpoints' : undefined;
    const w = (a, b) => ['interpolate', ['linear'], ['zoom'], 8, a, 16, b];
    const add = (layer) => {
      try {
        if (!m.getLayer(layer.id)) m.addLayer(layer, before);
      } catch (e) {
        console.warn('transit layer failed', layer.id, e);
      }
    };
    add({ id: 'transit-line-casing', type: 'line', source: SRC_LINE, layout: { 'line-join': 'round', 'line-cap': 'round' },
      paint: { 'line-color': '#FFFFFF', 'line-width': w(7, 13) } });
    add({ id: 'transit-line-path', type: 'line', source: SRC_LINE, layout: { 'line-join': 'round', 'line-cap': 'round' },
      paint: { 'line-color': ['get', 'color'], 'line-width': w(4, 8) } });
    add({ id: 'transit-line-stop', type: 'circle', source: SRC_LINE_STOPS,
      paint: { 'circle-radius': ['case', ['get', 'from'], 8, 4.5], 'circle-color': ['case', ['get', 'from'], ['get', 'color'], '#FFFFFF'],
        'circle-stroke-width': ['case', ['get', 'from'], 3, 2.5], 'circle-stroke-color': ['case', ['get', 'from'], '#FFFFFF', ['get', 'color']] } });
    return m;
  }

  drawLine(line, fit) {
    this.line = line;
    const m = this._lineLayers();
    if (!m) return;
    const color = line.route.color;
    m.getSource(SRC_LINE)?.setData({ type: 'FeatureCollection',
      features: [{ type: 'Feature', properties: { color, route: line.route.id }, geometry: line.geometry }] });
    m.getSource(SRC_LINE_STOPS)?.setData({ type: 'FeatureCollection', features: line.stops.map((s, i) => ({
      type: 'Feature', properties: { color, name: s.name, from: i === line.from_index }, geometry: { type: 'Point', coordinates: [s.lon, s.lat] } })) });
    if (fit) this.app.map.fitPoints(line.geometry.coordinates.map(([lon, lat]) => ({ lat, lon })), 16);
  }

  clearLine() {
    if (!this.line) return;
    this.line = null;
    const m = this.app.map?.map;
    m?.getSource(SRC_LINE)?.setData(EMPTY);
    m?.getSource(SRC_LINE_STOPS)?.setData(EMPTY);
  }

  clearMap() {
    const m = this.app.map?.map;
    if (!m) return;
    m.getSource(SRC)?.setData(EMPTY);
    m.getSource(SRC_STOPS)?.setData(EMPTY);
  }

  reset() {
    this.result = null;
    this.selected = -1;
    this.clearMap();
  }

  // ------------------------------------------------------------------ rendering
  timeControls() {
    const opt = (id, label) => `<label class="tr-radio"><input type="radio" name="tr-time" value="${id}" ${this.timeMode === id ? 'checked' : ''}><span>${label}</span></label>`;
    return `<fieldset class="tr-time">
      <legend class="sr-only">זמן הנסיעה</legend>
      <div class="tr-radios">${opt('now', 'יציאה עכשיו')}${opt('depart', 'יציאה ב-')}${opt('arrive', 'הגעה עד')}</div>
      ${this.timeMode !== 'now' ? `<label class="sr-only" for="tr-when">${this.timeMode === 'arrive' ? 'שעת הגעה' : 'שעת יציאה'}</label>
        <input id="tr-when" class="tr-when" type="datetime-local" value="${esc(this.when)}">` : ''}
    </fieldset>`;
  }

  html() {
    const st = this.status;
    const res = this.result;
    const its = res?.itineraries || [];
    const warn = (res?.feed?.warning || st?.warning);
    let out = this.timeControls();
    out += `<p class="tr-estimated">${svg('clock', { size: 16 })}<span>${esc(res?.estimated_text || 'זמנים משוערים לפי לוח הזמנים')}</span></p>`;
    if (warn) out += `<div class="tr-warning" role="status">${svg('alert', { size: 18 })}<div>${esc(warn)}</div></div>`;
    if (this.dirs.loading || !res) return out;
    if (res.notice && its.length) out += `<div class="tr-notice" role="status">${svg('info', { size: 18 })}<div>${esc(res.notice)}</div></div>`;
    if (!its.length) return out;
    if (this.selected >= 0 && its[this.selected]) return out + this._card(its[this.selected]);
    out += `<ul class="tr-list" aria-label="אפשרויות נסיעה">`;
    its.forEach((it, i) => {
      const rides = it.legs.filter((l) => l.type !== 'walk');
      const lines = rides.map((l) => l.line).join(', ');
      const tags = it.tags.map((t) => TAGS[t]).filter(Boolean);
      const aria = `אפשרות ${i + 1}: יציאה ${it.departure_text}, הגעה ${it.arrival_text}, ${it.duration_text}, ${rides.length ? transfersText(it.transfers) + ', ' + lines : 'הליכה בלבד'}, הליכה ${it.walk_distance_text}${tags.length ? ', ' + tags.join(', ') : ''}`;
      out += `<li class="tr-opt"><button type="button" data-itin="${i}" aria-label="${esc(aria)}">
        <span class="tr-times"><bdi>${esc(it.departure_text)}</bdi> – <bdi>${esc(it.arrival_text)}</bdi></span>
        <span class="tr-dur">${esc(it.duration_text)}</span>
        <span class="tr-badges" aria-hidden="true">${it.legs.filter((l) => l.type !== 'walk' || l.duration >= 120).map((l) => badge(l, { small: true })).join('<span class="tr-sep">‹</span>')}</span>
        <span class="tr-meta">${rides.length ? esc(transfersText(it.transfers)) + ' · ' : ''}הליכה ${esc(it.walk_distance_text)}${tags.length ? ` · <span class="tr-tag">${esc(tags[0])}</span>` : ''}</span>
      </button></li>`;
    });
    return out + '</ul>';
  }

  _card(it) {
    let out = `<div class="tr-card-head">
      <button type="button" class="btn btn-secondary btn-sm" data-act="tr-back">${svg('back', { size: 16 })}כל האפשרויות</button>
      <div class="tr-card-sum"><strong><bdi>${esc(it.departure_text)}</bdi> – <bdi>${esc(it.arrival_text)}</bdi></strong>
        <span>${esc(it.duration_text)} · ${esc(transfersText(it.transfers))} · הליכה ${esc(it.walk_distance_text)}</span></div>
    </div>
    <h3 class="subtitle">פירוט הנסיעה</h3>
    <ol class="tr-legs">`;
    it.legs.forEach((l, i) => {
      const walk = l.type === 'walk';
      out += `<li class="tr-leg ${walk ? 'walk' : 'ride'}" style="--leg-color:${esc(walk ? '#6B7280' : l.color)}">
        <div class="tr-leg-time"><bdi>${esc(l.departure_text)}</bdi></div>
        <div class="tr-leg-body">
          <div class="tr-leg-top">${walk ? badge(l) : lineButton(l, l.from.id, { small: false })}${!walk && l.agency ? `<span class="tr-agency">${esc(l.agency)}</span>` : ''}</div>
          <p class="tr-leg-text">${esc(l.instruction)}</p>
          ${!walk ? `<div class="tr-leg-stops">
            <button type="button" class="tr-stop-btn" data-stop="${esc(l.from.id)}" aria-label="לוח זמנים בתחנה ${esc(l.from.name)}">${svg('bus', { size: 14 })}${esc(l.from.name)}${l.from.code ? ` <span class="tr-code">(${esc(l.from.code)})</span>` : ''}</button>
            <details class="tr-inter"><summary>${esc(l.num_stops === 1 ? 'תחנה אחת' : l.num_stops + ' תחנות')} · ${esc(l.duration_text)}</summary>
              <ol>${l.stops.map((s) => `<li><bdi>${esc(s.time_text)}</bdi> ${esc(s.name)}</li>`).join('')}</ol></details>
            <button type="button" class="tr-stop-btn" data-stop="${esc(l.to.id)}" aria-label="לוח זמנים בתחנה ${esc(l.to.name)}">${svg('pin', { size: 14 })}${esc(l.to.name)} · <bdi>${esc(l.arrival_text)}</bdi></button>
          </div>` : `<button type="button" class="tr-zoom" data-leg="${i}">הצגה במפה</button>`}
        </div></li>`;
    });
    out += `<li class="tr-leg end"><div class="tr-leg-time"><bdi>${esc(it.arrival_text)}</bdi></div><div class="tr-leg-body"><p class="tr-leg-text">הגעה ליעד</p></div></li></ol>`;
    return out;
  }

  wire(body) {
    body.querySelectorAll('input[name="tr-time"]').forEach((r) => r.addEventListener('change', () => {
      this.timeMode = r.value;
      if (this.timeMode !== 'now' && !this.when) this.when = localInputValue();
      this._refocus = `input[name="tr-time"][value="${r.value}"]`;
      this.dirs._compute();
    }));
    const when = body.querySelector('#tr-when');
    when?.addEventListener('change', () => {
      if (!when.value) return;
      this.when = when.value;
      this._refocus = '#tr-when';
      this.dirs._compute();
    });
    body.querySelectorAll('[data-itin]').forEach((b) => b.addEventListener('click', () => {
      this.selected = Number(b.dataset.itin);
      this._refocus = '[data-act="tr-back"]';
      this.draw(true);
      this.dirs._refresh();
      const it = this.result.itineraries[this.selected];
      announce(`פירוט אפשרות ${this.selected + 1}: ${it.legs.map((l) => l.instruction).join('. ')}`);
    }));
    body.querySelector('[data-act="tr-back"]')?.addEventListener('click', () => {
      const prev = this.selected;
      this.selected = -1;
      this._refocus = `[data-itin="${prev}"]`;
      this.draw(false);
      this.dirs._refresh();
    });
    const it = this.result?.itineraries?.[this.selected];
    body.querySelectorAll('[data-leg]').forEach((b) => b.addEventListener('click', () => {
      const l = it?.legs[Number(b.dataset.leg)];
      if (l) this.app.map.fitPoints(l.geometry.coordinates.map(([lon, lat]) => ({ lat, lon })), 18);
    }));
    body.querySelectorAll('[data-stop]').forEach((b) => b.addEventListener('click', () => this.openStop(b.dataset.stop)));
    this._wireLines(body);
    if (this._refocus) {
      body.querySelector(this._refocus)?.focus({ preventScroll: false });
      this._refocus = null;
    }
  }

  // ------------------------------------------------------------------ stop card
  _wireLines(body) {
    body.querySelectorAll('[data-line]').forEach((b) => b.addEventListener('click', () => this.openLine(b.dataset.line, {
      fromStop: b.dataset.from || undefined, direction: b.dataset.dir === undefined ? undefined : Number(b.dataset.dir),
    })));
  }

  /** Every line serving the stop, grouped by mode; each boarding direction is its own button. */
  _routesHtml(routes, stopId) {
    if (!routes?.count) return '';
    const groups = routes.groups.map((g) => `<h4 class="tr-mode-h">${svg(MODE_ICON[g.type] || 'bus', { size: 16 })}<span>${esc(g.mode_he)}</span></h4>
      <ul class="tr-routes" aria-label="${esc(g.mode_he)}">${g.lines.map((l) => {
        const name = l.route_short_name ? `${l.line}` : `${l.mode_he}`;
        const dirs = l.directions.length
          ? l.directions.map((d) => `<button type="button" class="tr-dir-btn" data-line="${esc(d.route_id)}" data-dir="${esc(d.direction)}" data-from="${esc(stopId)}"
              aria-label="${esc(`${name}, ${l.agency}, לכיוון ${d.headsign} – כל התחנות של הקו`)}"><span aria-hidden="true">←</span><span>לכיוון ${esc(d.headsign)}</span></button>`).join('')
          : `<button type="button" class="tr-dir-btn" data-line="${esc(l.id)}" data-from="${esc(stopId)}"
              aria-label="${esc(`${name}, ${l.agency}, מסיים בתחנה – כל התחנות של הקו`)}"><span>מסיים בתחנה</span></button>`;
        return `<li class="tr-route${l.terminates ? ' ends' : ''}"><span class="tr-route-badge" aria-hidden="true">${badge(l, { small: true })}</span>
          <div class="tr-route-body"><span class="tr-agency">${esc(l.agency)}</span><div class="tr-route-dirs">${dirs}</div></div></li>`;
      }).join('')}</ul>`).join('');
    return `<h3 class="subtitle" id="tr-routes-h">כל הקווים בתחנה <span class="p-sub">(${routes.count})</span></h3>
      <section class="tr-routes-sec" aria-labelledby="tr-routes-h">${groups}</section>`;
  }

  async openStop(id) {
    const app = this.app;
    const time = this.timeMode === 'now' ? undefined : `${this.when}:00`;
    let data = null;
    let routes = null;
    let error = null;
    const view = () => ({
      key: `transit-stop:${id}`, label: 'לוח זמנים בתחנה', snap: 'half',
      render: (body) => {
        if (!data && !error) {
          body.innerHTML = `${head(app, 'לוח זמנים בתחנה')}<div class="skeleton" aria-busy="true" aria-label="טוען זמני יציאה"><div class="sk-line w80"></div><div class="sk-line w60"></div></div>`;
          wireHead(body, app);
          return;
        }
        if (error) {
          body.innerHTML = `${head(app, 'לוח זמנים בתחנה')}<div class="alert" role="alert">${svg('alert')}<div>${esc(error)}</div></div>`;
          wireHead(body, app);
          return;
        }
        const s = data.stop;
        const sub = [s.code ? `תחנה ${s.code}` : '', s.city || '', s.platform ? `רציף ${s.platform}` : ''].filter(Boolean).join(' · ');
        // quick chips: every line (from /routes when loaded), each opens its line view
        const chips = routes?.count
          ? routes.groups.flatMap((g) => g.lines).map((l) => lineButton({ ...l, route_id: l.directions[0]?.route_id || l.id,
            headsign: l.directions[0]?.headsign }, s.id, { direction: l.directions[0]?.direction })).join('')
          : data.lines.map((l) => `<span class="tr-badge sm" style="background:${esc(l.color)};color:${esc(l.text_color)}">${esc(l.line.replace(/^קו /, ''))}</span>`).join('');
        body.innerHTML = `${head(app, s.name, { sub })}
          <p class="tr-estimated">${svg('clock', { size: 16 })}<span>${esc(data.estimated_text)}</span></p>
          ${data.feed?.warning ? `<div class="tr-warning" role="status">${svg('alert', { size: 18 })}<div>${esc(data.feed.warning)}</div></div>` : ''}
          ${chips ? `<div class="tr-lines" role="group" aria-label="קווים בתחנה">${chips}</div>` : ''}
          <h3 class="subtitle">היציאות הקרובות</h3>
          ${data.departures.length ? `<ul class="tr-deps">${data.departures.map((x) => `<li>
              <span class="tr-dep-time"><bdi>${esc(x.time_text)}</bdi><small>${x.minutes <= 0 ? 'עכשיו' : x.minutes < 60 ? `בעוד ${x.minutes} דק׳` : ''}</small></span>
              ${lineButton(x, s.id)}
              <span class="tr-dep-head">${x.headsign ? 'לכיוון ' + esc(x.headsign) : ''}<small>${esc(x.agency)}</small></span>
            </li>`).join('')}</ul>` : '<p class="p-empty">אין יציאות בשלוש השעות הקרובות.</p>'}
          ${this._routesHtml(routes, s.id)}`;
        wireHead(body, app);
        this._wireLines(body);
        keepFocus(body);
      },
    });
    app.panel.show(view());
    const routesP = api.transitStopRoutes(id).catch(() => null); // the departures still show without it
    try {
      data = await api.transitStop(id, time);
      routes = await routesP;
      app.map.flyTo({ lat: data.stop.lat, lon: data.stop.lon }, 17);
      announce(`${data.stop.name}: ${data.departures.length} יציאות קרובות${routes?.count ? `, ${routes.count} קווים בתחנה` : ''}`);
    } catch (e) {
      error = e.message || 'שגיאה בטעינת לוח הזמנים';
    }
    app.panel.update(view());
  }

  // ------------------------------------------------------------------ line view
  /** All stops of one direction of a line, its path on the map and a direction selector. */
  async openLine(routeId, { direction, fromStop } = {}) {
    const app = this.app;
    const key = `${LINE_KEY}${++this._lineSeq}`;
    let data = null;
    let error = null;
    let fitted = false;
    let refocus = null;
    let ctrl = null;
    const load = async (rid, dir) => {
      ctrl?.abort();
      ctrl = new AbortController();
      const signal = ctrl.signal;
      try {
        const res = await api.transitRoute(rid, { direction: dir, fromStop }, signal);
        if (signal.aborted) return;
        data = res;
        error = null;
        fitted = false;
        const from = data.from_index != null ? data.stops[data.from_index] : null;
        announce(`${data.route.line} לכיוון ${data.headsign}: ${data.stops.length} תחנות${from ? `, מתחנה ${from.name}` : ''}`);
      } catch (e) {
        if (signal.aborted) return;
        error = e.message || 'שגיאה בטעינת הקו';
      }
      app.panel.update(view());
    };
    const view = () => ({
      key, label: data ? `${data.route.line} – תחנות הקו` : 'תחנות הקו', snap: 'half',
      render: (body) => {
        if (!data && !error) {
          body.innerHTML = `${head(app, 'תחנות הקו')}<div class="skeleton" aria-busy="true" aria-label="טוען את תחנות הקו"><div class="sk-line w80"></div><div class="sk-line w60"></div><div class="sk-line w80"></div></div>`;
          wireHead(body, app);
          return;
        }
        if (error && !data) {
          body.innerHTML = `${head(app, 'תחנות הקו')}<div class="alert" role="alert">${svg('alert')}<div>${esc(error)}</div></div>`;
          wireHead(body, app);
          return;
        }
        const r = data.route;
        const fi = data.from_index;
        const base = fi != null ? data.stops[fi].offset : 0;
        const opts = data.directions;
        const cur = opts.findIndex((o) => o.current);
        body.innerHTML = `${head(app, r.line, { sub: r.agency })}
          <div class="tr-line-head">${badge(r)}<strong>לכיוון ${esc(data.headsign)}</strong></div>
          ${error ? `<div class="alert" role="alert">${svg('alert')}<div>${esc(error)}</div></div>` : ''}
          ${opts.length > 1 ? `<div class="tr-line-dir"><label for="tr-dir-sel">כיוון</label>
            <select id="tr-dir-sel">${opts.map((o, i) => `<option value="${i}"${i === cur ? ' selected' : ''}>לכיוון ${esc(o.headsign)} (${esc(o.from)} ← ${esc(o.to)}, ${o.num_stops} תחנות)${o.serves_stop ? '' : fromStop ? ' – לא עוצר בתחנה' : ''}</option>`).join('')}</select></div>` : ''}
          <p class="tr-estimated">${svg('clock', { size: 16 })}<span>${esc(data.estimated_text)}${fi != null ? ' · הזמנים מהתחנה המסומנת' : ' · הזמנים מתחילת הקו'}</span></p>
          <h3 class="subtitle" id="tr-ls-h">${data.stops.length} תחנות</h3>
          <ol class="tr-line-stops" aria-labelledby="tr-ls-h" style="--line-color:${esc(r.color)}">${data.stops.map((st, i) => {
            const rel = st.offset - base;
            const time = i === fi ? 'כאן' : (fi != null && i < fi) ? '' : (fi == null && i === 0) ? 'יציאה' : offsetText(rel);
            const sub = [st.code ? `תחנה ${st.code}` : '', st.city || '', !st.pickup && i === data.stops.length - 1 ? 'תחנה אחרונה' : !st.pickup ? 'ירידה בלבד' : !st.drop_off && i > 0 ? 'עלייה בלבד' : ''].filter(Boolean).join(' · ');
            const aria = `${st.name}${sub ? ', ' + sub : ''}${i === fi ? ', התחנה הנוכחית' : time === 'יציאה' ? ', תחנת המוצא של הקו' : time ? `, ${fi != null ? 'עוד ' : 'אחרי '}${time}` : ''}`;
            return `<li class="tr-ls${i === fi ? ' from' : ''}${fi != null && i < fi ? ' past' : ''}${i === 0 ? ' first' : ''}${i === data.stops.length - 1 ? ' last' : ''}">
              <button type="button" data-lstop="${i}" aria-label="${esc(aria)}"${i === fi ? ' aria-current="location"' : ''}>
                <span class="tr-ls-dot" aria-hidden="true"></span>
                <span class="tr-ls-name">${esc(st.name)}${sub ? `<small>${esc(sub)}</small>` : ''}</span>
                <span class="tr-ls-time" aria-hidden="true"><bdi>${esc(time)}</bdi></span>
              </button></li>`;
          }).join('')}</ol>`;
        wireHead(body, app);
        this.drawLine(data, !fitted);
        fitted = true;
        const sel = body.querySelector('#tr-dir-sel');
        sel?.addEventListener('change', () => {
          const o = opts[Number(sel.value)];
          if (!o) return;
          refocus = '#tr-dir-sel';
          load(o.route_id, o.direction);
        });
        body.querySelectorAll('[data-lstop]').forEach((b) => b.addEventListener('click', () => {
          const st = data.stops[Number(b.dataset.lstop)];
          app.map.flyTo({ lat: st.lat, lon: st.lon }, 17);
          this.openStop(st.id);
        }));
        if (refocus) {
          body.querySelector(refocus)?.focus();
          refocus = null;
        } else {
          keepFocus(body);
          if (fi != null) body.querySelector('.tr-ls.from')?.scrollIntoView?.({ block: 'center' });
        }
      },
    });
    app.panel.show(view());
    await load(routeId, direction);
  }
}
