// מפות בעברית – application controller.

import { api, ApiError } from './api.js';
import { Combobox } from './combobox.js';
import { Directions } from './directions.js';
import { PoiHoverCard } from './hovercard.js';
import { svg } from './icons.js';
import { MapView } from './map.js';
import { Panel } from './panel.js';
import { trailView } from './trails.js';
import { $, $$, announce, bus, copyText, debounce, esc, isMobile, store, toast, trapFocus } from './util.js';
import { errorView, loadingView, placeView, pointView, poiView, resultsView, streetView, visibleListView } from './views.js';

const CHIPS = [
  ['restaurant', 'מסעדות'], ['cafe', 'בתי קפה'], ['supermarket', 'סופרמרקטים'], ['synagogue', 'בתי כנסת'],
  ['pharmacy', 'בתי מרקחת'], ['fuel', 'תחנות דלק'], ['attraction', 'אטרקציות'], ['park', 'פארקים'],
  ['parking', 'חניונים'], ['hospital', 'בתי חולים'], ['atm', 'כספומטים'], ['hotel', 'מלונות'], ['mikveh', 'מקוואות'],
];
const FINALS = { כ: 'ך', מ: 'ם', נ: 'ן', פ: 'ף', צ: 'ץ' };

const app = {
  meta: null,
  categories: [],
  catColor: {},
  subs: {},
  map: null,
  panel: null,
  directions: null,
  myLocation: null,
  activeChip: null,
  lastSearch: null,
  _watch: null,
  _searchCtrl: null,
  _itemCtrl: null,

  // ------------------------------------------------------------------ boot
  async init() {
    applyUiTheme(store.get('uitheme', 'auto'));
    hydrateIcons(document);
    this.panel = new Panel($('#panel'));
    if (typeof maplibregl === 'undefined') {
      fatal('רכיב המפה לא נטען. ודאו שההתקנה הושלמה (תיקיית vendor) ורעננו את הדף.');
      return;
    }
    try {
      const [meta, cats] = await Promise.all([api.meta(), api.categories()]);
      this.meta = meta;
      this.categories = cats.categories;
    } catch (e) {
      fatal(e instanceof ApiError ? e.message : 'לא ניתן להתחבר לשרת המפות.', true);
      return;
    }
    this.catColor = Object.fromEntries(this.categories.map((c) => [c.id, c.color]));
    for (const c of this.categories) for (const s of c.sub_categories) this.subs[s.id] = { ...s, category: c.id, color: c.color };

    const hash = parseHash();
    const saved = store.get('view');
    const initial = {
      theme: effectiveMapTheme(),
      center: hash.center || saved?.center,
      zoom: hash.zoom ?? saved?.zoom,
    };
    this.map = new MapView($('#map'), this.meta, this.categories, initial);
    this.directions = new Directions(this);
    this.hoverCard = new PoiHoverCard(this, $('#map'));
    this._kosherOnly = store.get('kosherOnly', false);

    this._initSearch();
    this._initChips();
    this._initControls();
    this._initMenus();
    this._initEvents();
    this._initBanner();
    $('#data-stamp').textContent = this.meta.dataset.built
      ? `נתונים: ${this.meta.dataset.osm_date || ''} · נבנו ${new Date(this.meta.dataset.built).toLocaleDateString('he-IL')}` : '';

    bus.on('map:ready', () => {
      if (this._kosherOnly) this.map.setPoiFilter({ cats: this.activeChip ? [this.activeChip] : [], kosher: true });
      if (this._restored) return;
      this._restored = true;
      if (hash.dir) this._restoreDirections(hash.dir);
      else if (hash.item) this.openItem({ id: hash.item }, { fly: !hash.center });
      else if (hash.q) this.search(hash.q, {});
    });
    registerServiceWorker();
  },

  // ------------------------------------------------------------------ search
  _initSearch() {
    const input = $('#q');
    const clear = $('#q-clear');
    this.searchBox = new Combobox(input, $('#q-list'), {
      fetch: async (q, signal) => (await api.autocomplete(q, this.map.center(), signal)).suggestions || [],
      empty: () => this.recent().slice(0, 6).map((r) => ({ ...r, type: 'history', _orig: r })),
      onSelect: (s) => {
        input.value = s.type === 'category' ? s.name : s.name;
        clear.hidden = !input.value;
        if (s.type === 'history') return this.openItem(s._orig);
        if (s.type === 'category') return this.search(s.query || s.name, {});
        this.remember(s);
        this.openItem(s);
      },
      onSubmit: (text) => text && this.search(text, {}),
    });
    input.addEventListener('input', () => (clear.hidden = !input.value));
    clear.addEventListener('click', () => {
      input.value = '';
      clear.hidden = true;
      input.focus();
      this.searchBox.close();
    });
    $('#search-form').addEventListener('submit', (e) => {
      e.preventDefault();
      const q = input.value.trim();
      if (q) {
        this.searchBox.cancel();
        this.search(q, {});
      }
    });
  },

  async search(q, filters = {}, { replace = false } = {}) {
    if (!q) return;
    if (this._searchCtrl) this._searchCtrl.abort();
    this._searchCtrl = new AbortController();
    const filt = { ...filters, kosher: filters.kosher ?? (this._kosherOnly || undefined) };
    this.lastSearch = { q, filters: filt };
    this.panel.show({ ...loadingView('results:' + q, `מחפש: ${q}`), replace });
    announce(`מחפש ${q}`);
    try {
      const data = await api.search(q, {
        center: this.map.center(), user: this.myLocation, kosher: filt.kosher, openNow: filt.openNow, category: filt.category,
      }, this._searchCtrl.signal);
      const results = data.results || [];
      this._rememberQuery(q);
      const it = data.interpretation || {};
      if (it.type === 'category') {
        const sub = it.sub || it.category;
        this._setChip(sub, false);
        this.map.setPoiFilter({ cats: [sub], kosher: !!filt.kosher });
      }
      // a single, clearly best place/address -> open it directly
      const top = results[0];
      const direct = top && ['place', 'street', 'address', 'coordinate'].includes(top.type) && it.type !== 'category'
        && (results.length === 1 || top.type === 'address' || (top.score != null && results[1]?.score != null && top.score - results[1].score > 1.6));
      if (direct) {
        this.panel.current = null; // the loading view should not stay in the back stack
        return this.openItem(top, { replace: true });
      }
      this.map.setResults(results);
      this.panel.show({ ...resultsView(this, q, data, filt), replace: true });
      const pts = results.filter((r) => r.location).slice(0, 25).map((r) => r.location);
      if (pts.length) this.map.fitPoints(pts, 16);
      this.updateHash();
    } catch (e) {
      if (e.name === 'AbortError') return;
      this.panel.show({ ...errorView(this, 'results:' + q, e.message, () => this.search(q, filters)), replace: true });
    }
  },

  // ------------------------------------------------------------------ items
  async openItem(item, { fly = true, replace = false } = {}) {
    if (!item?.id) return;
    if (item.type === 'category') return this.search(item.query || item.name, {});
    if (this._itemCtrl) this._itemCtrl.abort();
    this._itemCtrl = new AbortController();
    const signal = this._itemCtrl.signal;
    const id = item.id;
    const kind = id.split('-')[0];
    this.searchBox?.cancel();
    if (item.location) this.map.setSelection(item.location);
    this.map.setHighlight(null);
    try {
      if (kind === 'poi') {
        this.panel.show({ ...loadingView('poi:' + id, item.name ? `טוען: ${item.name}` : 'טוען'), replace });
        const poi = await api.poi(id, signal);
        this.map.setSelection(poi.location);
        if (poi.geometry) this.map.setHighlight(poi.geometry); // stream, ridge, nature reserve
        if (poi.bbox && fly) this.map.fitBounds(poi.bbox, 17);
        else if (fly) this.map.flyTo(poi.location, Math.max(16, Math.min(17, this.map.map.getZoom())));
        this.panel.show({ ...poiView(this, poi), replace: true });
      } else if (kind === 'place') {
        this.panel.show({ ...loadingView('place:' + id), replace });
        const place = await api.place(id, signal);
        this.map.setSelection(null);
        this.map.setHighlight(place.geometry);
        if (fly) this.map.fitBounds(place.bbox, place.kind === 'city' ? 13 : 15);
        this.panel.show({ ...placeView(this, place), replace: true });
      } else if (kind === 'trail') {
        this.panel.show({ ...loadingView('trail:' + id, item.name ? `טוען: ${item.name}` : 'טוען שביל'), replace });
        const trail = await api.trail(id, signal);
        this.map.setSelection(null);
        this.map.setHighlight(trail.geometry);
        if (fly) this.map.fitBounds(trail.bbox, 15);
        this.panel.show({ ...trailView(this, trail), replace: true });
      } else if (kind === 'street') {
        this.panel.show({ ...loadingView('street:' + id), replace });
        const street = await api.street(id, signal);
        if (item.type === 'address' && item.location) {
          // a house number that was not found: show the street and the approximate point
          this.panel.show({ ...pointView(this, item), replace: true });
          this._loadNearby(item);
        } else {
          this.map.setSelection(null);
          this.panel.show({ ...streetView(this, street), replace: true });
        }
        this.map.setHighlight(street.geometry);
        if (fly) this.map.fitBounds(street.bbox, 17);
      } else {
        // address / coordinate / generic point
        if (fly && item.location) this.map.flyTo(item.location, 17);
        this.panel.show({ ...pointView(this, item), replace });
        this._loadNearby(item);
      }
      this._currentItem = id;
      this.updateHash();
    } catch (e) {
      if (e.name === 'AbortError') return;
      this.panel.show({ ...errorView(this, 'err:' + id, e.message, () => this.openItem(item, { fly })), replace: true });
    }
  },

  async _loadNearby(item) {
    try {
      const res = await api.nearby(item.location.lat, item.location.lon, { radius: 400, limit: 8 });
      this.panel.update(pointView(this, item, res.results || []));
    } catch {
      this.panel.update(pointView(this, item, []));
    }
  },

  async whatsHere(lat, lon) {
    this.map.setSelection({ lat, lon });
    const base = { id: `coord-${lat.toFixed(6)},${lon.toFixed(6)}`, type: 'coordinate', name: 'נקודה במפה', location: { lat, lon } };
    this.panel.show(loadingView('point:' + base.id, 'מאתר כתובת'));
    try {
      const rev = await api.reverse(lat, lon);
      const item = { ...base, name: rev.address || 'נקודה במפה', secondary: rev.neighbourhood || (rev.address ? '' : rev.city || '') };
      this.panel.show({ ...pointView(this, item), replace: true });
      this._loadNearby(item);
    } catch (e) {
      this.panel.show({ ...pointView(this, base), replace: true });
      this._loadNearby(base);
    }
  },

  closePanel() {
    this.panel.close();
  },

  _onPanelClosed(view) {
    this.map.setSelection(null);
    this.map.setHighlight(null);
    this.map.setResults([]);
    if (view?.key === 'directions' || this.directions.from || this.directions.to) this.directions.reset();
    if (this.activeChip) this._setChip(null);
    this._currentItem = null;
    this.updateHash();
    $('#q').focus({ preventScroll: true });
  },

  // ------------------------------------------------------------------ chips
  _initChips() {
    const host = $('#chips');
    const chips = CHIPS.filter(([id]) => this.subs[id]);
    host.innerHTML = chips.map(([id, label]) => {
      const s = this.subs[id];
      return `<button type="button" class="chip" data-chip="${id}" aria-pressed="false">
        <span class="chip-dot" style="background:${esc(s.color)}" aria-hidden="true">${svg(s.icon, { size: 14 })}</span>${esc(label)}</button>`;
    }).join('') + `<button type="button" class="chip chip-more" data-chip="__more" aria-haspopup="dialog">${svg('filter', { size: 16 })}עוד</button>`;
    // show only the chips that fit in one row; the rest are reachable through "עוד"
    const fit = () => {
      const items = $$('[data-chip]:not([data-chip="__more"])', host);
      const more = host.querySelector('[data-chip="__more"]');
      items.forEach((b) => (b.hidden = false));
      const gap = parseFloat(getComputedStyle(host).columnGap) || 8;
      const avail = host.clientWidth - more.offsetWidth - gap - 4;
      let used = 0;
      let hiddenCount = 0;
      for (const b of items) {
        const w = b.offsetWidth + gap;
        if (used + w > avail || hiddenCount) {
          b.hidden = true;
          hiddenCount++;
        } else {
          used += w;
        }
      }
      more.setAttribute('aria-label', hiddenCount ? `עוד קטגוריות (${hiddenCount} נוספות)` : 'כל הקטגוריות');
    };
    fit();
    if ('ResizeObserver' in window) new ResizeObserver(() => fit()).observe(host);
    else window.addEventListener('resize', fit);
    if (document.fonts?.ready) document.fonts.ready.then(fit);
    host.addEventListener('click', (e) => {
      const b = e.target.closest('[data-chip]');
      if (!b) return;
      const id = b.dataset.chip;
      if (id === '__more') return this._categoriesDialog();
      if (this.activeChip === id) {
        this._setChip(null);
        this.closePanel();
        return;
      }
      const label = b.textContent.trim();
      this.search(label, { category: id });
    });
  },

  _setChip(id, applyFilter = true) {
    this.activeChip = id;
    $$('#chips [data-chip]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.chip === id)));
    if (applyFilter) this.map.setPoiFilter({ cats: id ? [id] : [], kosher: this._kosherOnly });
  },

  _categoriesDialog() {
    const body = this.categories.map((c) => `
      <h3><span class="chip-dot" style="display:inline-grid;place-items:center;width:22px;height:22px;border-radius:50%;color:#fff;background:${esc(c.color)};vertical-align:middle">${svg(c.icon, { size: 14 })}</span> ${esc(c.name_he)}</h3>
      <div class="mini-chips" style="padding:0">${c.sub_categories.map((s) => `<button type="button" class="chip" data-sub="${s.id}">${esc(s.plural_he)}</button>`).join('')}</div>`).join('');
    openDialog('כל הקטגוריות', body, (dlg) => {
      dlg.querySelectorAll('[data-sub]').forEach((b) => b.addEventListener('click', () => {
        closeDialog();
        this.search(b.textContent.trim(), { category: b.dataset.sub });
      }));
    });
  },

  // ------------------------------------------------------------------ controls
  _initControls() {
    $('#btn-zoom-in').addEventListener('click', () => this.map.zoomBy(1));
    $('#btn-zoom-out').addEventListener('click', () => this.map.zoomBy(-1));
    $('#btn-north').addEventListener('click', () => this.map.resetNorth());
    $('#btn-locate').addEventListener('click', () => this.toggleLocate());
    $('#btn-directions').addEventListener('click', () => this.directions.open({}));

    const pop = $('#layers-pop');
    const btn = $('#btn-layers');
    const closePop = () => {
      pop.hidden = true;
      btn.setAttribute('aria-expanded', 'false');
    };
    btn.addEventListener('click', () => {
      const open = pop.hidden;
      pop.hidden = !open;
      btn.setAttribute('aria-expanded', String(open));
      if (open) pop.querySelector('input:checked')?.focus();
    });
    pop.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        closePop();
        btn.focus();
      }
    });
    document.addEventListener('pointerdown', (e) => {
      if (!pop.hidden && !pop.contains(e.target) && !btn.contains(e.target)) closePop();
    });
    const mapType = store.get('maptype', 'light');
    pop.querySelector(`input[name="maptype"][value="${mapType}"]`).checked = true;
    pop.querySelectorAll('input[name="maptype"]').forEach((r) => r.addEventListener('change', () => {
      store.set('maptype', r.value);
      this.map.setTheme(effectiveMapTheme());
      announce(`סוג המפה: ${r.nextElementSibling.nextElementSibling.textContent}`);
    }));
    const ui = store.get('uitheme', 'auto');
    pop.querySelector(`input[name="uitheme"][value="${ui}"]`).checked = true;
    pop.querySelectorAll('input[name="uitheme"]').forEach((r) => r.addEventListener('change', () => {
      store.set('uitheme', r.value);
      applyUiTheme(r.value);
      this.map.setTheme(effectiveMapTheme());
    }));
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
      if (store.get('uitheme', 'auto') === 'auto') this.map.setTheme(effectiveMapTheme());
    });
    $('#toggle-pois').addEventListener('change', (e) => this.map.setPoisVisible(e.target.checked));
    $('#toggle-trails')?.addEventListener('change', (e) => this.map.setOutdoorVisible(e.target.checked));
    const kosher = $('#toggle-kosher');
    kosher.checked = !!store.get('kosherOnly', false);
    kosher.addEventListener('change', () => {
      this._kosherOnly = kosher.checked;
      store.set('kosherOnly', kosher.checked);
      this.map.setPoiFilter({ cats: this.activeChip ? [this.activeChip] : [], kosher: kosher.checked });
      announce(kosher.checked ? 'מוצגים רק מקומות כשרים' : 'מוצגים כל המקומות');
    });
  },

  // ------------------------------------------------------------------ geolocation
  locate({ fly = true } = {}) {
    return new Promise((resolve) => {
      if (!('geolocation' in navigator)) {
        toast('הדפדפן אינו תומך בזיהוי מיקום', { kind: 'error' });
        return resolve(null);
      }
      if (!window.isSecureContext) {
        toast('זיהוי מיקום דורש חיבור מאובטח (HTTPS) או גישה דרך localhost', { kind: 'error', ms: 6000 });
        return resolve(null);
      }
      const btn = $('#btn-locate');
      btn.setAttribute('aria-busy', 'true');
      navigator.geolocation.getCurrentPosition((pos) => {
        btn.removeAttribute('aria-busy');
        this.myLocation = { lat: pos.coords.latitude, lon: pos.coords.longitude, accuracy: pos.coords.accuracy };
        this.map.setLocation(this.myLocation);
        if (fly) this.map.flyTo(this.myLocation, 16);
        announce('המיקום שלך מוצג במפה');
        resolve(this.myLocation);
      }, (err) => {
        btn.removeAttribute('aria-busy');
        const msg = err.code === 1 ? 'הגישה למיקום נחסמה. ניתן לאשר אותה בהגדרות הדפדפן.'
          : err.code === 3 ? 'זיהוי המיקום לקח יותר מדי זמן. נסו שוב.' : 'לא ניתן לזהות את המיקום כרגע.';
        toast(msg, { kind: 'error', ms: 5000 });
        resolve(null);
      }, { enableHighAccuracy: true, timeout: 12000, maximumAge: 30000 });
    });
  },

  async toggleLocate() {
    const btn = $('#btn-locate');
    if (this._watch != null) {
      navigator.geolocation.clearWatch(this._watch);
      this._watch = null;
      btn.setAttribute('aria-pressed', 'false');
      btn.classList.remove('tracking');
      this.map.setLocation(null);
      this.myLocation = null;
      announce('מעקב המיקום הופסק');
      return;
    }
    const loc = await this.locate();
    if (!loc) return;
    btn.setAttribute('aria-pressed', 'true');
    btn.classList.add('tracking');
    this._watch = navigator.geolocation.watchPosition((pos) => {
      this.myLocation = { lat: pos.coords.latitude, lon: pos.coords.longitude, accuracy: pos.coords.accuracy };
      this.map.setLocation(this.myLocation);
    }, () => {}, { enableHighAccuracy: true, maximumAge: 10000 });
  },

  // ------------------------------------------------------------------ menus & dialogs
  _initMenus() {
    const menu = $('#menu');
    const scrim = $('#scrim');
    const btn = $('#btn-menu');
    let release = null;
    const open = () => {
      menu.hidden = false;
      scrim.hidden = false;
      btn.setAttribute('aria-expanded', 'true');
      release = trapFocus(menu);
      menu.querySelector('button, a').focus();
    };
    const close = () => {
      menu.hidden = true;
      scrim.hidden = true;
      btn.setAttribute('aria-expanded', 'false');
      release?.();
      btn.focus();
    };
    this._closeMenu = close;
    btn.addEventListener('click', open);
    scrim.addEventListener('click', close);
    menu.querySelector('[data-close]').addEventListener('click', close);
    menu.addEventListener('keydown', (e) => e.key === 'Escape' && close());
    menu.addEventListener('click', (e) => {
      const b = e.target.closest('[data-menu]');
      if (!b) return;
      close();
      const act = b.dataset.menu;
      if (act === 'nearby-list') this.panel.show(visibleListView(this, this.map.visiblePois(), this.map.visibleTrails()));
      else if (act === 'directions') this.directions.open({});
      else if (act === 'recent') this._recentDialog();
      else if (act === 'shortcuts') shortcutsDialog();
      else if (act === 'data') this._dataDialog();
      else if (act === 'accessibility') accessibilityDialog();
    });

    // context menu (right click / long press)
    const ctx = $('#ctx-menu');
    this._ctxPoint = null;
    ctx.addEventListener('click', (e) => {
      const b = e.target.closest('[data-act]');
      if (!b || !this._ctxPoint) return;
      const { lat, lon } = this._ctxPoint;
      hideCtx();
      const pt = { lat, lon, name: `${lat.toFixed(5)}, ${lon.toFixed(5)}` };
      switch (b.dataset.act) {
        case 'here': this.whatsHere(lat, lon); break;
        case 'from': this.directions.open({ from: pt }); break;
        case 'to': this.directions.open({ to: pt }); break;
        case 'nearby': this._nearbyResults(lat, lon); break;
        case 'copy': copyText(`${lat.toFixed(6)}, ${lon.toFixed(6)}`, 'הקואורדינטות הועתקו'); break;
      }
    });
    ctx.addEventListener('keydown', (e) => {
      const items = $$('button', ctx);
      const i = items.indexOf(document.activeElement);
      if (e.key === 'ArrowDown') { e.preventDefault(); items[(i + 1) % items.length].focus(); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); items[(i - 1 + items.length) % items.length].focus(); }
      else if (e.key === 'Escape' || e.key === 'Tab') { e.preventDefault(); hideCtx(); this.map.map.getCanvas().focus(); }
    });
    const hideCtx = () => (ctx.hidden = true);
    this._hideCtx = hideCtx;
    bus.on('map:context', ({ lat, lon, point }) => {
      this._ctxPoint = { lat, lon };
      ctx.hidden = false;
      const r = $('#map').getBoundingClientRect();
      const w = ctx.offsetWidth, h = ctx.offsetHeight;
      ctx.style.left = `${Math.min(Math.max(8, point.x), r.width - w - 8)}px`;
      ctx.style.top = `${Math.min(Math.max(8, point.y), r.height - h - 8)}px`;
      ctx.querySelector('button').focus({ preventScroll: true });
    });
    document.addEventListener('pointerdown', (e) => {
      if (!ctx.hidden && !ctx.contains(e.target)) hideCtx();
    });
  },

  async _nearbyResults(lat, lon) {
    this.panel.show(loadingView('results:nearby', 'מחפש מקומות בסביבה'));
    try {
      const res = await api.nearby(lat, lon, { radius: 600, limit: 30 });
      const data = { results: res.results, interpretation: { type: 'nearby', near: 'הנקודה שנבחרה' } };
      this.map.setResults(res.results);
      this.panel.show({ ...resultsView(this, 'מה יש בסביבה', data, {}), replace: true });
    } catch (e) {
      this.panel.show({ ...errorView(this, 'results:nearby', e.message), replace: true });
    }
  },

  _recentDialog() {
    const items = this.recent();
    openDialog('חיפושים אחרונים', items.length
      ? `<ul class="results">${items.map((r, i) => `<li class="result"><button type="button" data-i="${i}">
          <span class="r-icon neutral" aria-hidden="true">${svg('history', { size: 18 })}</span><span class="r-body"><span class="r-name">${esc(r.name)}</span>
          ${r.secondary ? `<span class="r-sub" style="display:block">${esc(r.secondary)}</span>` : ''}</span></button></li>`).join('')}</ul>
         <p><button type="button" class="btn btn-secondary btn-sm" data-clear>ניקוי ההיסטוריה</button></p>`
      : '<p>אין חיפושים אחרונים.</p>', (dlg) => {
      dlg.querySelectorAll('[data-i]').forEach((b) => b.addEventListener('click', () => {
        closeDialog();
        this.openItem(items[Number(b.dataset.i)]);
      }));
      dlg.querySelector('[data-clear]')?.addEventListener('click', () => {
        store.set('recent', []);
        closeDialog();
        toast('ההיסטוריה נוקתה');
      });
    });
  },

  _dataDialog() {
    const d = this.meta.dataset;
    openDialog('מקורות הנתונים', `
      <p>המפה מבוססת על נתונים פתוחים, ומעובדת ומוגשת כולה מהשרת המקומי.</p>
      <ul>
        <li><strong>OpenStreetMap</strong> – מפת הבסיס, הרחובות, הכתובות ונקודות העניין (רישיון ODbL).</li>
        <li><strong>data.gov.il</strong> – רשימת היישובים ומאגר הרחובות הרשמיים (רישיון ממשלתי פתוח).</li>
        <li><strong>Wikidata / ויקיפדיה / Wikimedia Commons</strong> – תיאורים ותמונות (CC0 / CC BY-SA).</li>
      </ul>
      <h3>מצב הנתונים</h3>
      <ul>
        <li>תאריך נתוני OSM: ${esc(d.osm_date || 'לא ידוע')}</li>
        <li>נבנו: ${d.built ? esc(new Date(d.built).toLocaleString('he-IL')) : 'לא ידוע'}</li>
        <li>מפת בסיס: ${this.meta.basemap.type === 'openmaptiles' ? 'מלאה' : 'פשוטה (המפה המלאה טרם נבנתה)'}</li>
        <li>ניווט: ${Object.entries(this.meta.routing || {}).map(([m, ok]) => `${{ car: 'רכב', foot: 'הליכה', bike: 'אופניים' }[m]} ${ok ? '✓' : '✗'}`).join(' · ')}</li>
        ${d.demo ? '<li><strong>מצב הדגמה – מוצגים נתוני דוגמה בלבד</strong></li>' : ''}
      </ul>
      <p><a href="/reports/quality-report.html" target="_blank" rel="noopener">לדוח איכות הנתונים המלא</a></p>
      <p class="r-sub">${this.meta.attribution}</p>`);
  },

  // ------------------------------------------------------------------ events & keyboard
  _initEvents() {
    bus.on('select', (item) => this.openItem(item));
    bus.on('panel:close', (view) => this._onPanelClosed(view));
    bus.on('map:moved', debounce((v) => {
      store.set('view', { center: v.center, zoom: v.zoom });
      this.updateHash();
    }, 400));
    bus.on('map:rotate', (bearing) => {
      const b = $('#btn-north');
      b.hidden = Math.abs(bearing) < 1;
      b.querySelector('.compass').style.transform = `rotate(${-bearing}deg)`;
    });
    bus.on('map:click', () => {
      this._hideCtx?.();
      this.searchBox.close();
    });
    bus.on('panel:back', (view) => {
      if (view.key.startsWith('results:')) {
        this.map.setSelection(null);
        this.map.setHighlight(null);
      }
    });
    document.addEventListener('keydown', (e) => {
      const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName);
      if ((e.key === '/' && !typing) || ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k')) {
        e.preventDefault();
        $('#q').focus();
        $('#q').select();
      } else if (e.key === '?' && !typing) {
        e.preventDefault();
        shortcutsDialog();
      } else if (e.key === 'Escape' && !e.defaultPrevented) {
        if (!$('#dialog').hidden) closeDialog();
        else if (!$('#ctx-menu').hidden) this._hideCtx();
        else if (!$('#layers-pop').hidden) $('#btn-layers').click();
        else if (this.panel.isOpen && !typing) this.panel.back();
      } else if (!typing && !e.ctrlKey && !e.metaKey && !e.altKey) {
        if (e.key === 'l' || e.key === 'ך') this.toggleLocate();
        else if (e.key === 'd' || e.key === 'ג') this.directions.open({});
        else if (e.key === 'n' || e.key === 'מ') this.map.resetNorth();
      }
    });
    window.addEventListener('hashchange', () => {
      if (this._writingHash) return;
      const h = parseHash();
      if (h.item && h.item !== this._currentItem) this.openItem({ id: h.item });
    });
  },

  _initBanner() {
    const b = $('#banner');
    const msgs = [];
    if (this.meta.dataset.demo) msgs.push('מצב הדגמה: מוצגים נתוני דוגמה. להפעלת המפה המלאה יש להריץ את ההתקנה (setup).');
    else if (this.meta.basemap.type !== 'openmaptiles') msgs.push('מפת הבסיס המלאה עדיין בבנייה – בינתיים מוצגת מפה פשוטה.');
    if (!msgs.length || store.get('bannerDismissed') === msgs[0]) return;
    b.innerHTML = `${svg('info', { size: 18 })}<span>${esc(msgs[0])}</span><button type="button" aria-label="סגירת ההודעה">${svg('x', { size: 16 })}</button>`;
    b.hidden = false;
    b.querySelector('button').addEventListener('click', () => {
      b.hidden = true;
      store.set('bannerDismissed', msgs[0]);
    });
  },

  // ------------------------------------------------------------------ history
  recent() {
    return store.get('recent', []);
  },

  remember(item) {
    if (!item?.id || item.type === 'category' || item.type === 'history') return;
    const entry = { id: item.id, type: item.type, name: item.name, secondary: item.secondary, location: item.location };
    const list = this.recent().filter((r) => r.id !== entry.id);
    list.unshift(entry);
    store.set('recent', list.slice(0, 12));
  },

  _rememberQuery(q) {
    const entry = { id: `q:${q}`, type: 'category', name: q, query: q, secondary: 'חיפוש' };
    const list = this.recent().filter((r) => r.id !== entry.id);
    list.unshift(entry);
    store.set('recent', list.slice(0, 12));
  },

  // ------------------------------------------------------------------ url state & sharing
  updateHash: debounce(function () {
    if (!app.map) return;
    const v = app.map.view();
    const parts = [`map=${v.zoom.toFixed(2)}/${v.center.lat.toFixed(5)}/${v.center.lon.toFixed(5)}`];
    const dir = app.directions?.active ? app.directions.hashState() : null;
    if (dir) parts.push(`dir=${dir}`);
    else if (app._currentItem && app.panel.isOpen) parts.push(`item=${encodeURIComponent(app._currentItem)}`);
    else if (app.lastSearch && app.panel.current?.key?.startsWith('results:')) parts.push(`q=${encodeURIComponent(app.lastSearch.q)}`);
    app._writingHash = true;
    history.replaceState(null, '', '#' + parts.join('&'));
    setTimeout(() => (app._writingHash = false), 0);
  }, 250),

  async share(item) {
    const url = new URL(location.href);
    url.hash = `map=17.00/${item.location.lat.toFixed(5)}/${item.location.lon.toFixed(5)}&item=${encodeURIComponent(item.id)}`;
    const data = { title: item.name, text: `${item.name} – מפות בעברית`, url: url.toString() };
    if (navigator.share) {
      try {
        await navigator.share(data);
        return;
      } catch (e) {
        if (e.name === 'AbortError') return;
      }
    }
    copyText(data.url, 'הקישור הועתק');
  },

  _restoreDirections(dir) {
    const [a, b, mode] = dir.split(';');
    const pt = (s) => {
      const [lat, lon] = (s || '').split(',').map(Number);
      return isFinite(lat) && isFinite(lon) ? { lat, lon, name: `${lat.toFixed(5)}, ${lon.toFixed(5)}` } : null;
    };
    this.directions.open({ from: pt(a), to: pt(b), mode: ['car', 'foot', 'bike', 'transit'].includes(mode) ? mode : 'car' });
  },

  // ------------------------------------------------------------------ helpers for views
  displayCorrected(text) {
    return text.split(' ').map((w) => (w.length > 1 && FINALS[w.at(-1)] ? w.slice(0, -1) + FINALS[w.at(-1)] : w)).join(' ');
  },

  subName(id) {
    return this.subs[id]?.name_he || '';
  },
};

// ------------------------------------------------------------------ module helpers
function hydrateIcons(root) {
  root.querySelectorAll('[data-icon]').forEach((el) => {
    el.outerHTML = svg(el.dataset.icon, { size: 22 });
  });
}

function applyUiTheme(t) {
  document.documentElement.dataset.theme = t;
}

function effectiveMapTheme() {
  const mapType = store.get('maptype', 'light');
  const ui = store.get('uitheme', 'auto');
  const dark = ui === 'dark' || (ui === 'auto' && window.matchMedia('(prefers-color-scheme: dark)').matches);
  return mapType === 'light' && dark ? 'dark' : mapType;
}

function parseHash() {
  const out = {};
  const params = new URLSearchParams(location.hash.slice(1));
  const m = params.get('map');
  if (m) {
    const [z, lat, lon] = m.split('/').map(Number);
    if ([z, lat, lon].every(Number.isFinite)) {
      out.zoom = z;
      out.center = { lat, lon };
    }
  }
  if (params.get('item')) out.item = params.get('item');
  if (params.get('q')) out.q = params.get('q');
  if (params.get('dir')) out.dir = params.get('dir');
  return out;
}

let dialogRelease = null;
let dialogReturn = null;
function openDialog(title, bodyHtml, wire) {
  const dlg = $('#dialog');
  dialogReturn = document.activeElement;
  $('#dialog-title').textContent = title;
  $('#dialog-body').innerHTML = bodyHtml;
  dlg.hidden = false;
  dialogRelease = trapFocus(dlg);
  dlg.querySelector('[data-close]').onclick = closeDialog;
  dlg.onclick = (e) => e.target === dlg && closeDialog();
  wire?.(dlg);
  dlg.querySelector('[data-close]').focus();
}

function closeDialog() {
  const dlg = $('#dialog');
  if (dlg.hidden) return;
  dlg.hidden = true;
  dialogRelease?.();
  dialogReturn?.focus?.();
}

function shortcutsDialog() {
  const rows = [
    ['/', 'מעבר לתיבת החיפוש'], ['Ctrl + K', 'מעבר לתיבת החיפוש'], ['↑ ↓', 'מעבר בין הצעות החיפוש'], ['Enter', 'בחירה / חיפוש'],
    ['Esc', 'סגירה / חזרה'], ['D (ג)', 'תכנון מסלול'], ['L (ך)', 'הצגת המיקום שלי'], ['N (מ)', 'איפוס כיוון לצפון'],
    ['?', 'הצגת קיצורי המקלדת'],
  ];
  const mapRows = [['חיצים', 'הזזת המפה (כשהמפה בפוקוס)'], ['+ / -', 'הגדלה / הקטנה'], ['Shift + חיצים', 'סיבוב והטיית המפה']];
  const table = (rs) => `<table class="kbd-table"><tbody>${rs.map(([k, d]) => `<tr><td><kbd>${esc(k)}</kbd></td><td>${esc(d)}</td></tr>`).join('')}</tbody></table>`;
  openDialog('קיצורי מקלדת', `<h3>כללי</h3>${table(rows)}<h3>במפה</h3>${table(mapRows)}
    <p class="r-sub">לחיצה ימנית (או לחיצה ארוכה במגע) על המפה פותחת תפריט: "מה יש כאן?", "מסלול מכאן/לכאן" ועוד.</p>`);
}

function accessibilityDialog() {
  openDialog('הצהרת נגישות', `
    <p>המערכת "מפות בעברית" שואפת להיות נגישה לכלל האוכלוסייה, ותוכננה בהתאם לתקן <strong>WCAG 2.1 ברמה AA</strong> ולתקן הישראלי ת"י 5568.</p>
    <h3>מה נעשה</h3>
    <ul>
      <li>ממשק מלא בעברית מימין לשמאל, עם כותרות, אזורים (landmarks) ותוויות לכל הפקדים.</li>
      <li>ניווט מלא במקלדת, כולל חיפוש עם הצעות, תפריטים, כרטיסי מידע ותכנון מסלול; סימון מיקוד בולט.</li>
      <li>הודעות קוליות לקוראי מסך על תוצאות חיפוש, מסלולים ושגיאות.</li>
      <li>חלופה טקסטואלית למפה: "רשימת המקומות שבמפה" בתפריט הראשי, והוראות הגעה כטקסט.</li>
      <li>ניגודיות צבעים לפי הדרישות, תמיכה בהגדלת טקסט ובזום, מצב כהה, וכיבוד העדפת "הפחתת תנועה".</li>
    </ul>
    <h3>מגבלות ידועות</h3>
    <ul>
      <li>תוכן המפה הגרפי עצמו (אריחי המפה) אינו נגיש לקוראי מסך; המידע זמין דרך החיפוש, הרשימות וכרטיסי המידע.</li>
      <li>תמונות מוויקימדיה מוצגות עם שם המקום כטקסט חלופי.</li>
    </ul>
    <h3>פנייה בנושא נגישות</h3>
    <p>נתקלתם בבעיית נגישות? ניתן לפנות למנהל/ת המערכת בארגון, ונשמח לתקן.</p>
    <p class="r-sub">תאריך עדכון ההצהרה: אוקטובר 2026</p>`);
}

function fatal(message, retry = false) {
  const b = $('#banner');
  b.innerHTML = `${svg('alert', { size: 18 })}<span>${esc(message)}</span>${retry ? '<button type="button">ניסיון נוסף</button>' : ''}`;
  b.hidden = false;
  b.setAttribute('role', 'alert');
  b.querySelector('button')?.addEventListener('click', () => location.reload());
}

function registerServiceWorker() {
  if ('serviceWorker' in navigator && window.isSecureContext && !location.search.includes('nosw')) {
    navigator.serviceWorker.register('/sw.js').catch(() => {});
  }
}

window.hmaps = app; // handy for debugging and tests
app.init();
