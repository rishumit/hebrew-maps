// Route planner: two autocomplete fields, travel mode, alternatives and Hebrew steps.

import { api } from './api.js';
import { Combobox } from './combobox.js';
import { elevationSlot, fillElevation } from './elevation.js';
import { openGuide } from './guide.js';
import { maneuverIcon, svg } from './icons.js';
import { head, wireHead } from './views.js';
import { TransitPlanner } from './transit.js';
import { announce, esc, fmtDistance, store } from './util.js';

const MODES = [
  { id: 'car', label: 'רכב', icon: 'car' },
  { id: 'foot', label: 'הליכה', icon: 'walk' },
  { id: 'bike', label: 'אופניים', icon: 'bike' },
  { id: 'transit', label: 'תחבורה ציבורית', icon: 'bus' },
];

// "avoid" options of the router (hmaps/route/avoid.py) and the travel modes they apply to
export const AVOID = [
  { id: 'toll', label: 'כבישי אגרה', modes: ['car'] },
  { id: 'motorway', label: 'כבישים מהירים', modes: ['car'] },
  { id: 'unpaved', label: 'דרכי עפר ארוכות', modes: ['car', 'bike', 'foot'] },
  { id: 'junctions', label: 'צמתים מאתגרים', modes: ['car'] },
  { id: 'pa', label: 'שטחי A/B של הרשות הפלסטינית', modes: ['car', 'bike', 'foot'] },
  { id: 'kohen', label: 'מצב כהן – הימנעות מקברים', modes: ['car', 'bike', 'foot'] },
];

// Kohen mode: levels of the grave areas (gps.hakohanim.org) – hmaps/route/kohen.py
export const KOHEN_LEVELS = [
  { id: 'א', label: 'ודאי' },
  { id: 'ב', label: 'ספק' },
  { id: 'ג', label: 'חשש קל' },
];
const ALL_KOHEN = KOHEN_LEVELS.map((l) => l.id);

export class Directions {
  constructor(app) {
    this.app = app;
    this.from = null;
    this.to = null;
    this.mode = 'car';
    this.routes = [];
    this.selected = 0;
    this.error = null;
    this.loading = false;
    this.ctrl = null;
    this.avoid = new Set(store.get('route:avoid', []).filter((a) => AVOID.some((o) => o.id === a)));
    const kl = store.get('route:kohen-levels', ALL_KOHEN).filter((l) => ALL_KOHEN.includes(l));
    this.kohenLevels = new Set(kl.length ? kl : ALL_KOHEN);
    this.avoidOpen = false;
    this.transit = new TransitPlanner(app, this);
  }

  /** Avoid options that apply to the current mode. */
  avoidFor(mode = this.mode) {
    return AVOID.filter((o) => o.modes.includes(mode) && this.avoid.has(o.id)).map((o) => o.id);
  }

  /** Kohen-mode levels for the API ('' = all, the server's default). */
  kohenLevelsParam() {
    if (!this.avoidFor().includes('kohen') || this.kohenLevels.size === ALL_KOHEN.length) return '';
    return ALL_KOHEN.filter((l) => this.kohenLevels.has(l)).join('');
  }

  setAvoid(list) {
    this.avoid = new Set(list || []);
    store.set('route:avoid', [...this.avoid]);
  }

  get active() {
    return this.app.panel.current?.key === 'directions';
  }

  open({ from, to, mode, avoid } = {}) {
    if (from !== undefined) this.from = from;
    if (to !== undefined) this.to = to;
    if (mode) this.mode = mode;
    if (avoid) this.setAvoid(avoid);
    if (!this.from && this.app.myLocation) this.from = { ...this.app.myLocation, name: 'המיקום שלי', mine: true };
    this.app.map.setSelection(null);
    this.app.map.setResults([]);
    this.app.panel.show(this._view(true));
    this._compute();
  }

  clear() {
    this.routes = [];
    this.error = null;
    this.app.map.setRoutes([], 0, this.mode);
    this.app.map.setEndpoints(null, null);
    this.transit.reset();
  }

  reset() {
    if (this.ctrl) this.ctrl.abort();
    this.from = this.to = null;
    this.clear();
  }

  setPoint(which, point) {
    this[which] = point;
    this._refresh();
    // keep keyboard users moving: next empty field, otherwise the panel
    const body = this.app.panel.body;
    const next = !this.to ? body.querySelector('#dir-to') : !this.from ? body.querySelector('#dir-from') : null;
    if (next) next.focus();
    else if (this.active) this.app.panel.el.focus({ preventScroll: true });
    this._compute();
  }

  swap() {
    [this.from, this.to] = [this.to, this.from];
    this._refresh();
    this._compute();
    announce('נקודות המוצא והיעד הוחלפו');
  }

  _refresh(focus = false) {
    if (this.active) this.app.panel.update(this._view(focus));
  }

  async _compute() {
    this.app.map.setEndpoints(this.from, this.to);
    if (!this.from || !this.to) {
      this.routes = [];
      this.transit.reset();
      this.app.map.setRoutes([], 0, this.mode);
      this._refresh();
      return;
    }
    if (this.ctrl) this.ctrl.abort();
    this.ctrl = new AbortController();
    this.loading = true;
    this.error = null;
    this._refresh();
    if (this.mode === 'transit') return this._computeTransit(this.ctrl.signal);
    try {
      const res = await api.route(this.from, this.to, this.mode, this.ctrl.signal, this.avoidFor(), this.kohenLevelsParam());
      this.routes = res.routes || [];
      this.avoidIgnored = res.avoid_ignored || [];
      this.selected = 0;
      this.loading = false;
      if (!this.routes.length) this.error = 'לא נמצא מסלול';
      
      // Save to recent routes
      if (this.routes.length) {
        this.app._rememberRoute(this.from, this.to, this.mode);
      }
      
      this._draw(true);
      this._refresh();
      if (this.routes.length) {
        const r = this.routes[0];
        announce(`נמצא מסלול: ${r.duration_text}, ${r.distance_text}${r.summary ? ', ' + r.summary : ''}`);
      }
      this.app.updateHash();
    } catch (e) {
      if (e.name === 'AbortError') return;
      this.loading = false;
      this.routes = [];
      this.error = e.message || 'שגיאה בחישוב המסלול';
      this.app.map.setRoutes([], 0, this.mode);
      this._refresh();
      announce(this.error, true);
    }
  }

  async _computeTransit(signal) {
    this.routes = [];
    this.app.map.setRoutes([], 0, this.mode);
    try {
      await this.transit.compute(signal);
      this.app.updateHash();
    } catch (e) {
      if (e.name === 'AbortError') return;
      this.loading = false;
      this.error = e.message || 'שגיאה בחישוב המסלול';
      this.transit.reset();
      announce(this.error, true);
    }
    this._refresh();
  }

  _draw(fit) {
    this.app.map.setRoutes(this.routes, this.selected, this.mode);
    const r = this.routes[this.selected];
    if (fit && r?.geometry?.coordinates?.length) {
      const pts = r.geometry.coordinates.map(([lon, lat]) => ({ lat, lon }));
      this.app.map.fitPoints(pts, 17);
    }
  }

  _field(which, label, point) {
    const val = point ? point.name || `${point.lat.toFixed(5)}, ${point.lon.toFixed(5)}` : '';
    return `<div class="dir-field">
      <span class="marker ${which}" aria-hidden="true"></span>
      <label for="dir-${which}" class="sr-only">${label}</label>
      <input id="dir-${which}" type="text" value="${esc(val)}" placeholder="${which === 'from' ? 'נקודת מוצא' : 'לאן נוסעים?'}" autocomplete="off"
        spellcheck="false" ${!point && (which === 'to' || !this.from) ? 'data-autofocus' : ''}>
      <ul id="dir-${which}-list" class="suggestions" aria-label="הצעות עבור ${label}" hidden></ul>
    </div>`;
  }

  _view(focus) {
    return {
      key: 'directions', label: 'תכנון מסלול', focus, snap: this.routes.length ? 'half' : 'half',
      render: (body) => {
        const r = this.routes[this.selected];
        body.innerHTML = `${head(this.app, 'תכנון מסלול')}
          <div class="modes" role="radiogroup" aria-label="אמצעי תחבורה">
            ${MODES.map((m) => `<button type="button" class="mode" role="radio" aria-checked="${m.id === this.mode}" data-mode="${m.id}"
              tabindex="${m.id === this.mode ? 0 : -1}">${svg(m.icon, { size: 22 })}<span>${m.label}</span></button>`).join('')}
          </div>
          <div class="dir-form">
            <div class="dir-fields">${this._field('from', 'נקודת מוצא', this.from)}${this._field('to', 'יעד', this.to)}</div>
            <button type="button" class="icon-btn" data-act="swap" aria-label="החלפה בין המוצא ליעד">${svg('swap')}</button>
          </div>
          ${this._avoidHtml()}
          ${!this.from || !this.to ? `<div class="dir-quick">
            <button type="button" class="btn btn-secondary btn-sm" data-act="mine">${svg('locate', { size: 16 })}מהמיקום שלי</button>
            <span class="r-sub" style="align-self:center">אפשר גם ללחוץ לחיצה ימנית (או ארוכה) על המפה</span></div>` : ''}
          <div aria-live="polite">
          ${this.loading ? '<div class="skeleton" aria-busy="true" aria-label="מחשב מסלול"><div class="sk-line w80"></div><div class="sk-line w60"></div><div class="sk-line w40"></div></div>' : ''}
          ${this.error ? `<div class="alert" role="alert">${svg('alert')}<div>${esc(this.error)}</div></div>` : ''}
          ${this.mode === 'transit' ? this.transit.html() : ''}
          ${!this.loading && this.routes.length ? `
            <ul class="routes" aria-label="מסלולים אפשריים">
              ${this.routes.map((rt, i) => `<li class="route-opt" ${i === this.selected ? 'aria-current="true"' : ''}>
                <button type="button" data-route="${i}" aria-label="מסלול ${i + 1}: ${esc(rt.duration_text)}, ${esc(rt.distance_text)} ${esc(rt.summary)}">
                  <span class="route-dur">${esc(rt.duration_text)}</span><span class="route-dist">${esc(rt.distance_text)}</span>
                  ${rt.summary ? `<span class="route-sum">${esc(rt.summary)}</span>` : ''}
                </button></li>`).join('')}
            </ul>
            ${this._avoidNotice(r)}
            ${elevationSlot(this.app.meta, this.mode, r)}
            <div class="p-section dir-actions">
              <button type="button" class="btn btn-secondary btn-sm" data-act="save-route">${svg('star', { size: 16 })}שמירת המסלול</button>
              <button type="button" class="btn btn-secondary btn-sm" data-act="print-route">${svg('print', { size: 16 })}הדפסת מסלול והוראות</button>
            </div>
            ${this.mode === 'foot' ? `<div class="p-section"><button type="button" class="btn btn-secondary btn-sm" data-act="guide">${svg('signpost', { size: 16 })}מדריך מסלול: שלבים, מים ובטיחות</button></div>` : ''}
            <h3 class="subtitle">הוראות הגעה</h3>
            <ol class="steps">
              ${r.steps.map((s, i) => `<li class="step"><button type="button" data-step="${i}">
                <span class="st-icon">${svg(maneuverIcon(s.maneuver), { size: 22 })}</span>
                <span class="st-text">${esc(s.instruction)}</span>
                ${s.distance_text ? `<span class="st-dist">${esc(s.distance_text)}</span>` : ''}
              </button></li>`).join('')}
            </ol>` : ''}
          </div>`;
        wireHead(body, this.app);
        this._wire(body);
        if (this.mode === 'transit') this.transit.wire(body);
      },
    };
  }

  _avoidHtml() {
    const opts = AVOID.filter((o) => o.modes.includes(this.mode));
    if (!opts.length) return '';
    const n = this.avoidFor().length;
    return `<details class="dir-avoid" ${this.avoidOpen ? 'open' : ''}>
      <summary>${svg('filter', { size: 16 })}אפשרויות מסלול${n ? ` <span class="badge-count">הימנעות מ-${n}</span>` : ''}</summary>
      <fieldset><legend>להימנע מ:</legend>
        ${opts.map((o) => `<label class="check"><input type="checkbox" data-avoid="${o.id}" ${this.avoid.has(o.id) ? 'checked' : ''}>
          <span>${esc(o.label)}</span></label>${o.id === 'kohen' && this.avoid.has('kohen') ? this._kohenLevelsHtml() : ''}`).join('')}
        ${opts.some((o) => o.id === 'pa') ? '<p class="r-sub">שטחי A/B מסומנים בקירוב, סביב יישובים פלסטיניים (לפי OpenStreetMap). אין זו מפה רשמית – הקפידו על השילוט בדרך.</p>' : ''}
      </fieldset>
    </details>`;
  }

  _kohenLevelsHtml() {
    return `<div class="kohen-levels" role="group" aria-labelledby="kohen-levels-title">
      <span class="kohen-levels-title" id="kohen-levels-title">אזורים להימנעות:</span>
      ${KOHEN_LEVELS.map((l) => `<label class="check"><input type="checkbox" data-kohen-level="${l.id}" ${
        this.kohenLevels.has(l.id) ? 'checked' : ''}><span>${esc(l.label)}</span></label>`).join('')}
    </div>`;
  }

  _avoidNotice(r) {
    const out = [];
    if (r?.avoid_unmet?.length) {
      out.push(`<div class="alert alert-warn" role="status">${svg('alert')}<div>לא ניתן היה להימנע לגמרי מ${
        esc(r.avoid_unmet.map((a) => a.name_he).join(', '))} – אין דרך אחרת סבירה.</div></div>`);
    }
    if (this.avoidIgnored?.length) {
      out.push(`<p class="r-sub">${esc(this.avoidIgnored.map((a) => a.name_he).join(', '))}: עדיין אין נתונים במחשב – יתווספו בעדכון הנתונים הבא.</p>`);
    }
    return out.join('');
  }

  _wire(body) {
    const app = this.app;
    const det = body.querySelector('.dir-avoid');
    det?.addEventListener('toggle', () => { this.avoidOpen = det.open; });
    body.querySelectorAll('[data-avoid]').forEach((c) => c.addEventListener('change', () => {
      const set = new Set(this.avoid);
      if (c.checked) set.add(c.dataset.avoid); else set.delete(c.dataset.avoid);
      this.setAvoid([...set]);
      announce(c.checked ? `הימנעות מ${c.nextElementSibling.textContent} הופעלה` : `הימנעות מ${c.nextElementSibling.textContent} בוטלה`);
      this._compute();
      requestAnimationFrame(() => this.app.panel.body.querySelector(`[data-avoid="${c.dataset.avoid}"]`)?.focus({ preventScroll: true }));
    }));
    body.querySelectorAll('[data-kohen-level]').forEach((c) => c.addEventListener('change', () => {
      const lvl = c.dataset.kohenLevel;
      const label = c.nextElementSibling.textContent;
      if (!c.checked && this.kohenLevels.size === 1) {
        c.checked = true; // at least one level – to avoid none, turn Kohen mode off
        announce('יש לבחור לפחות רמה אחת');
        return;
      }
      if (c.checked) this.kohenLevels.add(lvl); else this.kohenLevels.delete(lvl);
      store.set('route:kohen-levels', ALL_KOHEN.filter((l) => this.kohenLevels.has(l)));
      announce(c.checked ? `הימנעות מאזורי ${label} הופעלה` : `הימנעות מאזורי ${label} בוטלה`);
      this._compute();
      requestAnimationFrame(() => this.app.panel.body.querySelector(`[data-kohen-level="${lvl}"]`)?.focus({ preventScroll: true }));
    }));
    body.querySelector('[data-act="save-route"]')?.addEventListener('click', () => app.saved?.saveRoute());
    for (const which of ['from', 'to']) {
      const input = body.querySelector(`#dir-${which}`);
      const list = body.querySelector(`#dir-${which}-list`);
      new Combobox(input, list, {
        fetch: async (q, signal) => {
          const favs = app.saved.matchFavorites(q);
          const res = await api.autocomplete(q, app.map.center(), signal);
          const ids = new Set(favs.map((f) => f.id));
          return [...favs, ...(res.suggestions || []).filter((s) => s.type !== 'category' && s.location && !ids.has(s.id))];
        },
        empty: () => {
          const out = [];
          if ('geolocation' in navigator) out.push({ type: 'mylocation', name: 'המיקום שלי', icon: 'locate' });
          out.push(...app.saved.items.favorites.slice(0, 4).map((f) => app.saved.asSuggestion(f)));
          return out.concat(app.recent().filter((r) => r.location && !app.saved.isFavorite(r.id)).slice(0, 4)
            .map((r) => ({ ...r, type: 'history', secondary: r.secondary })));
        },
        onSelect: async (s) => {
          if (s.type === 'mylocation') {
            const loc = await app.locate({ fly: false });
            if (loc) this.setPoint(which, { ...loc, name: 'המיקום שלי', mine: true });
            return;
          }
          this.setPoint(which, { lat: s.location.lat, lon: s.location.lon, name: s.name, id: s.id });
          if (s.type !== 'favorite') app.remember(s);
        },
        onSubmit: async (text) => {
          if (!text) return;
          const res = await api.autocomplete(text, app.map.center());
          const first = (res.suggestions || []).find((s) => s.location && s.type !== 'category');
          if (first) this.setPoint(which, { lat: first.location.lat, lon: first.location.lon, name: first.name, id: first.id });
          else announce('המקום לא נמצא', true);
        },
      });
    }
    body.querySelector('[data-act="swap"]')?.addEventListener('click', () => this.swap());
    body.querySelector('[data-act="mine"]')?.addEventListener('click', async () => {
      const loc = await app.locate({ fly: false });
      if (loc) this.setPoint('from', { ...loc, name: 'המיקום שלי', mine: true });
    });
    const modes = [...body.querySelectorAll('[data-mode]')];
    modes.forEach((b, i) => {
      b.addEventListener('click', () => this._setMode(b.dataset.mode));
      b.addEventListener('keydown', (e) => {
        const dir = e.key === 'ArrowLeft' ? 1 : e.key === 'ArrowRight' ? -1 : 0; // RTL: left = next
        if (!dir) return;
        e.preventDefault();
        const next = modes[(i + dir + modes.length) % modes.length];
        this._setMode(next.dataset.mode);
        this.app.panel.body.querySelector(`[data-mode="${next.dataset.mode}"]`)?.focus();
      });
    });
    body.querySelectorAll('[data-route]').forEach((b) => b.addEventListener('click', () => {
      this.selected = Number(b.dataset.route);
      this._draw(true);
      this._refresh();
    }));
    const r = this.routes[this.selected];
    body.querySelector('[data-act="print-route"]')?.addEventListener('click', () => app.printing.openDirections());
    if (r) fillElevation(body, app.meta, this.mode, r);
    body.querySelector('[data-act="guide"]')?.addEventListener('click', () => openGuide(app, {
      kind: 'route', coordinates: r.geometry.coordinates,
      name: this.from?.name && this.to?.name ? `${this.from.name} – ${this.to.name}` : 'מסלול הליכה',
    }, { fly: false }).catch(() => {}));
    body.querySelectorAll('[data-step]').forEach((b) => b.addEventListener('click', () => {
      const loc = r.steps[Number(b.dataset.step)].maneuver.location;
      if (loc) app.map.flyTo({ lon: loc[0], lat: loc[1] }, 17);
    }));
  }

  _setMode(mode) {
    if (mode === this.mode) return;
    if (this.mode === 'transit') this.transit.reset();
    this.mode = mode;
    this._refresh();
    this._compute();
  }

  hashState() {
    if (!this.from || !this.to) return null;
    return `${this.from.lat.toFixed(5)},${this.from.lon.toFixed(5)};${this.to.lat.toFixed(5)},${this.to.lon.toFixed(5)};${this.mode}`;
  }
}
