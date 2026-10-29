// Print maps and route sheets through the browser's native print dialog.
import { esc, toast, trapFocus } from './util.js';

const dateText = () => new Intl.DateTimeFormat('he-IL', { dateStyle: 'long', timeStyle: 'short' }).format(new Date());
const transferText = (n) => Number(n) === 0 ? 'ללא החלפות' : Number(n) === 1 ? 'החלפה אחת' : `${n} החלפות`;
const point = (p, name) => p && Number.isFinite(p.lat) && Number.isFinite(p.lon) ? { lat: p.lat, lon: p.lon, name: p.name || name } : null;

export class PrintManager {
  constructor(app) {
    this.app = app;
    this.overlay = null;
    this.releaseFocus = null;
    this.returnFocus = null;
  }

  async openMap() {
    try {
      const image = await this._capture(this._activeRouteOptions());
      this._show({ title: 'מפה להדפסה', image, landscape: true,
        caption: 'המפה מוצגת בסגנון בהיר, עם צפון למעלה. נקודות העניין משקפות את מה שמופיע כרגע במפה.' });
    } catch (e) { toast(e.message || 'לא ניתן להכין את המפה להדפסה.', { kind: 'error' }); }
  }

  _activeRouteOptions() {
    const d = this.app.directions;
    if (d.mode === 'transit') {
      const it = d.transit.result?.itineraries?.[Math.max(0, d.transit.selected)];
      if (!it) return {};
      return {
        routeFeatures: (it.legs || []).filter((leg) => leg.geometry?.coordinates?.length).map((leg) => ({
          type: 'Feature', properties: { kind: leg.type === 'walk' ? 'walk' : 'ride', color: leg.color || '#2563EB' }, geometry: leg.geometry,
        })),
        endpoints: [point(it.legs?.[0]?.from, 'מוצא'), point(it.legs?.at(-1)?.to, 'יעד')].filter(Boolean),
        stopPoints: (it.legs || []).filter((leg) => leg.type !== 'walk').flatMap((leg) => [leg.from, leg.to]
          .filter((stop) => Number.isFinite(stop?.lat) && Number.isFinite(stop?.lon))
          .map((stop) => ({ ...stop, color: leg.color || '#2563EB' }))),
      };
    }
    const route = d.routes[d.selected];
    if (!route?.geometry) return {};
    return {
      routeFeatures: [{ type: 'Feature', properties: { color: '#2563EB' }, geometry: route.geometry }],
      endpoints: [point(d.from, 'מוצא'), point(d.to, 'יעד')].filter(Boolean),
    };
  }

  async openDirections() {
    const d = this.app.directions;
    const route = d.routes[d.selected];
    if (!route) return toast('אין מסלול זמין להדפסה.', { kind: 'error' });
    try {
      const endpoints = [point(d.from, 'מוצא'), point(d.to, 'יעד')].filter(Boolean);
      const image = await this._capture({ routeFeatures: [{ type: 'Feature', properties: { color: '#2563EB' }, geometry: route.geometry }], endpoints, fitRoute: true, includePois: false });
      const steps = (route.steps || []).map((step, i) => `<li><span class="print-step-number">${i + 1}</span><span>${esc(step.instruction)}</span>${step.distance_text ? `<small>${esc(step.distance_text)}</small>` : ''}</li>`).join('');
      const title = `מסלול ${d.from?.name || 'מוצא'} – ${d.to?.name || 'יעד'}`;
      this._show({ title, image, landscape: false, details: `<p class="print-summary">${esc(route.duration_text)} · ${esc(route.distance_text)}${route.summary ? ` · ${esc(route.summary)}` : ''}</p><h2>הוראות הגעה</h2><ol class="print-steps">${steps}</ol>` });
    } catch (e) { toast(e.message || 'לא ניתן להכין את המסלול להדפסה.', { kind: 'error' }); }
  }

  async openTransit() {
    const tr = this.app.directions.transit;
    const it = tr.result?.itineraries?.[tr.selected];
    if (!it) return toast('בחרו מסלול בתחבורה ציבורית לפני ההדפסה.', { kind: 'error' });
    try {
      const routeFeatures = [];
      const endpoints = [];
      const stops = [];
      for (const leg of it.legs || []) {
        if (leg.geometry?.coordinates?.length) routeFeatures.push({ type: 'Feature', properties: { kind: leg.type === 'walk' ? 'walk' : 'ride', color: leg.color || '#2563EB' }, geometry: leg.geometry });
        if (leg.type !== 'walk') {
          for (const [which, stop] of [['from', leg.from], ['to', leg.to]]) {
            if (!stop || !Number.isFinite(stop.lat) || !Number.isFinite(stop.lon)) continue;
            const name = stop.name || (which === 'from' ? 'תחנת עלייה' : 'תחנת ירידה');
            stops.push({ ...stop, name, color: leg.color || '#2563EB' });
          }
        }
      }
      const first = it.legs?.[0], last = it.legs?.[it.legs.length - 1];
      if (first?.from) endpoints.push(point(first.from, 'מוצא'));
      if (last?.to) endpoints.push(point(last.to, 'יעד'));
      const image = await this._capture({ routeFeatures, endpoints: endpoints.filter(Boolean), stopPoints: stops, fitRoute: true, includePois: false });
      const legs = (it.legs || []).map((leg) => {
        const ride = leg.type !== 'walk';
        return `<li class="print-transit-leg"><div class="print-leg-time"><bdi>${esc(leg.departure_text || '')}</bdi>${leg.arrival_text ? ` – <bdi>${esc(leg.arrival_text)}</bdi>` : ''}</div><div><strong>${esc(ride ? (leg.route_short_name || leg.line || leg.mode_he || 'תחבורה ציבורית') : 'הליכה')}</strong>${leg.agency ? ` <small>${esc(leg.agency)}</small>` : ''}<p>${esc(leg.instruction || '')}</p>${ride ? `<p class="print-stop"><b>עלייה:</b> ${esc(leg.from?.name || '')}${leg.from?.code ? ` (${esc(leg.from.code)})` : ''}</p><p class="print-stop"><b>ירידה:</b> ${esc(leg.to?.name || '')}${leg.to?.code ? ` (${esc(leg.to.code)})` : ''}</p>` : ''}</div></li>`;
      }).join('');
      const disclaimer = [tr.result?.estimated_text || 'הזמנים מבוססים על לוח הזמנים ועשויים להשתנות.', tr.result?.feed?.warning].filter(Boolean).join(' ');
      const journeyDate = new Intl.DateTimeFormat('he-IL', { dateStyle: 'full' }).format(tr.timeMode === 'now' ? new Date() : new Date(tr.when));
      const title = `מסלול בתחבורה ציבורית: ${this.app.directions.from?.name || 'מוצא'} – ${this.app.directions.to?.name || 'יעד'}`;
      const details = `<p class="print-summary">${esc(journeyDate)} · <bdi>${esc(it.departure_text)}</bdi> – <bdi>${esc(it.arrival_text)}</bdi> · ${esc(it.duration_text)} · ${esc(transferText(it.transfers))} · הליכה ${esc(it.walk_distance_text)}</p><ol class="print-transit-legs">${legs}</ol><p class="print-disclaimer">${esc(disclaimer)}</p>`;
      this._show({ title, image, landscape: false, details });
    } catch (e) { toast(e.message || 'לא ניתן להכין את מסלול התחבורה הציבורית להדפסה.', { kind: 'error' }); }
  }

  _capture(options) {
    if (!this.app.map?.map?.loaded()) throw new Error('המפה עדיין נטענת. נסו שוב בעוד רגע.');
    return this.app.map.capturePrintMap(options);
  }

  _show({ title, image, landscape, caption = '', details = '' }) {
    this.close();
    this.returnFocus = document.activeElement;
    const root = document.createElement('section');
    root.id = 'print-preview';
    root.className = 'print-preview';
    root.setAttribute('role', 'dialog');
    root.setAttribute('aria-modal', 'true');
    root.setAttribute('aria-labelledby', 'print-title');
    root.dataset.orientation = landscape ? 'landscape' : 'portrait';
    root.innerHTML = `<div class="print-toolbar"><h1 id="print-title">תצוגה מקדימה להדפסה</h1><div><button type="button" class="btn btn-primary" data-print-now>הדפסה / שמירה כ־PDF</button><button type="button" class="btn btn-secondary" data-print-close>סגירה</button></div></div><article class="print-sheet"><header class="print-header"><div><h1>${esc(title)}</h1><p>${esc(dateText())}</p></div><span class="print-brand">מפות ישראל</span></header><img class="print-map-image" src="${image}" alt="${esc(title)} על גבי מפה בהירה"><p class="print-caption">${esc(caption)}</p>${details ? `<section class="print-details">${details}</section>` : ''}<footer class="print-footer">מפות ישראל · © OpenStreetMap contributors</footer></article></section>`;
    document.body.append(root);
    this.overlay = root;
    root.querySelector('[data-print-now]').addEventListener('click', () => window.print());
    root.querySelector('[data-print-close]').addEventListener('click', () => this.close());
    root.addEventListener('keydown', (e) => { if (e.key === 'Escape') this.close(); });
    this.releaseFocus = trapFocus(root);
    root.querySelector('[data-print-now]').focus();
  }

  close() {
    if (!this.overlay) return;
    this.releaseFocus?.();
    this.overlay.remove();
    this.overlay = null;
    this.returnFocus?.focus?.({ preventScroll: true });
    this.returnFocus = null;
  }
}
