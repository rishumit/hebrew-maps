// Public transport mode of the route planner: time selection, itinerary list, leg-by-leg card,
// map layers (coloured rides, dashed walking) and the stop departures card.

import { api } from './api.js';
import { svg } from './icons.js';
import { head, wireHead } from './views.js';
import { announce, esc } from './util.js';

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
      m.on('style.load', () => this.draw(false)); // theme / style switches drop custom layers
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
          <div class="tr-leg-top">${badge(l)}${!walk && l.agency ? `<span class="tr-agency">${esc(l.agency)}</span>` : ''}</div>
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
    if (this._refocus) {
      body.querySelector(this._refocus)?.focus({ preventScroll: false });
      this._refocus = null;
    }
  }

  // ------------------------------------------------------------------ stop card
  async openStop(id) {
    const app = this.app;
    const time = this.timeMode === 'now' ? undefined : `${this.when}:00`;
    let data = null;
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
        body.innerHTML = `${head(app, s.name, { sub })}
          <p class="tr-estimated">${svg('clock', { size: 16 })}<span>${esc(data.estimated_text)}</span></p>
          ${data.feed?.warning ? `<div class="tr-warning" role="status">${svg('alert', { size: 18 })}<div>${esc(data.feed.warning)}</div></div>` : ''}
          ${data.lines.length ? `<div class="tr-lines" aria-label="קווים בתחנה">${data.lines.map((l) => `<span class="tr-badge sm" style="background:${esc(l.color)};color:${esc(l.text_color)}">${esc(l.line.replace(/^קו /, ''))}</span>`).join('')}</div>` : ''}
          <h3 class="subtitle">היציאות הקרובות</h3>
          ${data.departures.length ? `<ul class="tr-deps">${data.departures.map((x) => `<li>
              <span class="tr-dep-time"><bdi>${esc(x.time_text)}</bdi><small>${x.minutes <= 0 ? 'עכשיו' : x.minutes < 60 ? `בעוד ${x.minutes} דק׳` : ''}</small></span>
              ${badge({ type: x.type, route_short_name: x.route_short_name, mode_he: x.mode_he, color: x.color, text_color: x.text_color }, { small: true })}
              <span class="tr-dep-head">${x.headsign ? 'לכיוון ' + esc(x.headsign) : ''}<small>${esc(x.agency)}</small></span>
            </li>`).join('')}</ul>` : '<p class="p-empty">אין יציאות בשלוש השעות הקרובות.</p>'}`;
        wireHead(body, app);
      },
    });
    app.panel.show(view());
    try {
      data = await api.transitStop(id, time);
      app.map.flyTo({ lat: data.stop.lat, lon: data.stop.lon }, 17);
      announce(`${data.stop.name}: ${data.departures.length} יציאות קרובות`);
    } catch (e) {
      error = e.message || 'שגיאה בטעינת לוח הזמנים';
    }
    app.panel.update(view());
  }
}
