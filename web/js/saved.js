// "המועדפים והשמורים שלי": favourite places, saved routes and saved searches.
// Kept on this computer only (hmaps/api/userdata.py → <data>/user/*.json; no account, no cloud).
// When the local server cannot store them (a shared web install) the browser's storage is used.
// Favourites come first in the search suggestions; everything can be exported / imported as a JSON file.

import { svg } from './icons.js';
import { announce, bus, esc, store, toast } from './util.js';
import { head, iconCircle, wireHead } from './views.js';

const KINDS = ['favorites', 'routes', 'searches'];
const TITLES = { favorites: 'מקומות', routes: 'מסלולים', searches: 'חיפושים' };
const MODE_HE = { car: 'רכב', foot: 'הליכה', bike: 'אופניים', transit: 'תחבורה ציבורית' };
const KEY = 'saved';

const norm = (s) => String(s || '').toLowerCase().replace(/[֑-ׇ"'׳״\-.,]/g, '').replace(/\s+/g, ' ').trim();
const newId = () => `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;

export class Saved {
  constructor(app) {
    this.app = app;
    this.items = { favorites: [], routes: [], searches: [] };
    this.server = true;
    this.tab = 'favorites';
    this.ready = this.load();
  }

  // ------------------------------------------------------------------ storage
  async load() {
    for (const kind of KINDS) {
      try {
        const r = await fetch(`/api/user/${kind}`, { cache: 'no-store' });
        if (!r.ok) throw new Error(String(r.status));
        this.items[kind] = (await r.json()).items || [];
      } catch {
        this.server = false;
        this.items[kind] = store.get(`user:${kind}`, []);
      }
    }
    // first run with the server: move what an older version kept in the browser
    if (this.server) {
      for (const kind of KINDS) {
        const local = store.get(`user:${kind}`, null);
        if (local?.length && !this.items[kind].length) {
          this.items[kind] = local;
          await this._persist(kind);
        }
      }
    }
    bus.emit('saved:changed');
  }

  async _persist(kind) {
    store.set(`user:${kind}`, this.items[kind]); // also a local copy (works offline from the server)
    if (!this.server) return;
    try {
      const r = await fetch(`/api/user/${kind}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json', 'X-HMaps-User': '1' },
        body: JSON.stringify({ items: this.items[kind] }),
      });
      if (!r.ok) throw new Error(String(r.status));
    } catch {
      toast('השמירה במחשב נכשלה – נשמר בדפדפן בלבד', { kind: 'error' });
    }
  }

  _changed(kind) {
    this._persist(kind);
    bus.emit('saved:changed');
    if (this.app.panel.current?.key === KEY) this.app.panel.update(this.view(false));
  }

  // ------------------------------------------------------------------ favourites
  isFavorite(id) {
    return !!id && this.items.favorites.some((f) => f.id === id);
  }

  toggleFavorite(item) {
    if (!item?.id || !item.location) return false;
    const i = this.items.favorites.findIndex((f) => f.id === item.id);
    if (i >= 0) {
      const [f] = this.items.favorites.splice(i, 1);
      announce(`${f.label || f.name} הוסר מהמועדפים`);
      toast('הוסר מהמועדפים');
      this._changed('favorites');
      return false;
    }
    this.items.favorites.unshift({
      id: item.id, name: item.name, label: '', type: item.type || item.id.split('-')[0],
      secondary: item.secondary || item.address?.full || item.city || item.kind_he || '',
      icon: item.icon || null, color: item.color || null,
      location: { lat: +item.location.lat, lon: +item.location.lon }, added: new Date().toISOString(),
    });
    announce(`${item.name} נוסף למועדפים`);
    toast('נוסף למועדפים ★');
    this._changed('favorites');
    return true;
  }

  /** Favourites matching the text typed in a search box (shown first in the suggestions). */
  matchFavorites(q, limit = 3) {
    const n = norm(q);
    if (!n) return [];
    return this.items.favorites.filter((f) => norm(f.label).includes(n) || norm(f.name).includes(n)).slice(0, limit)
      .map((f) => this.asSuggestion(f));
  }

  asSuggestion(f) {
    return { ...f, name: f.label || f.name, secondary: `★ מועדף${(f.label ? f.name : f.secondary) ? ` · ${f.label ? f.name : f.secondary}` : ''}`, type: 'favorite',
      _fav: f, icon: f.icon || 'star', color: f.color };
  }

  // ------------------------------------------------------------------ routes & searches
  saveRoute() {
    const d = this.app.directions;
    if (!d?.from || !d?.to) return;
    const p = (x) => ({ lat: +x.lat, lon: +x.lon, name: x.mine ? 'המיקום שלי' : x.name || '', id: x.id || null, mine: !!x.mine });
    const entry = { id: newId(), from: p(d.from), to: p(d.to), mode: d.mode, avoid: d.avoidFor(),
      name: `${p(d.from).name || 'מוצא'} ← ${p(d.to).name || 'יעד'}`, added: new Date().toISOString() };
    const dup = this.items.routes.find((r) => r.mode === entry.mode && same(r.from, entry.from) && same(r.to, entry.to)
      && (r.avoid || []).join() === entry.avoid.join());
    if (dup) {
      toast('המסלול כבר שמור');
      return;
    }
    this.items.routes.unshift(entry);
    toast('המסלול נשמר ★');
    announce('המסלול נשמר ברשימת המסלולים השמורים');
    this._changed('routes');
  }

  openRoute(r) {
    const pt = (x) => (x.mine ? null : { lat: x.lat, lon: x.lon, name: x.name, id: x.id || undefined });
    this.app.directions.open({ from: pt(r.from), to: pt(r.to), mode: r.mode, avoid: r.avoid || [] });
  }

  saveSearch(q, filters = {}) {
    if (!q) return;
    if (this.items.searches.some((s) => s.q === q && JSON.stringify(s.filters || {}) === JSON.stringify(filters))) {
      toast('החיפוש כבר שמור');
      return;
    }
    this.items.searches.unshift({ id: newId(), q, filters, name: q, added: new Date().toISOString() });
    toast('החיפוש נשמר ★');
    this._changed('searches');
  }

  isSearchSaved(q) {
    return this.items.searches.some((s) => s.q === q);
  }

  remove(kind, id) {
    this.items[kind] = this.items[kind].filter((x) => x.id !== id);
    this._changed(kind);
  }

  rename(kind, id, label) {
    const x = this.items[kind].find((i) => i.id === id);
    if (!x) return;
    if (kind === 'favorites') x.label = label.trim();
    else x.name = label.trim() || x.name;
    this._changed(kind);
  }

  move(kind, id, delta) {
    const list = this.items[kind];
    const i = list.findIndex((x) => x.id === id);
    const j = i + delta;
    if (i < 0 || j < 0 || j >= list.length) return;
    [list[i], list[j]] = [list[j], list[i]];
    this._changed(kind);
  }

  // ------------------------------------------------------------------ export / import
  exportFile() {
    const data = { app: 'hebrew-maps', version: 1, exported: new Date().toISOString(), ...this.items };
    const blob = new Blob([JSON.stringify(data, null, 1)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `מפות-ישראל-שמורים-${new Date().toISOString().slice(0, 10)}.json`;
    document.body.append(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
  }

  async importFile(file) {
    let data;
    try {
      data = JSON.parse(await file.text());
    } catch {
      toast('הקובץ אינו קובץ שמורים תקין', { kind: 'error' });
      return;
    }
    let added = 0;
    for (const kind of KINDS) {
      const incoming = Array.isArray(data?.[kind]) ? data[kind].filter((x) => x && typeof x === 'object') : [];
      const have = new Set(this.items[kind].map((x) => (kind === 'favorites' ? x.id : JSON.stringify([x.q, x.from, x.to, x.mode]))));
      for (const x of incoming) {
        const k = kind === 'favorites' ? x.id : JSON.stringify([x.q, x.from, x.to, x.mode]);
        if (have.has(k)) continue;
        if (kind === 'favorites' && !(x.id && x.location)) continue;
        this.items[kind].push(kind === 'favorites' ? x : { ...x, id: newId() });
        have.add(k);
        added++;
      }
      if (incoming.length) this._persist(kind);
    }
    bus.emit('saved:changed');
    if (this.app.panel.current?.key === KEY) this.app.panel.update(this.view(false));
    toast(added ? `יובאו ${added} פריטים` : 'כל הפריטים שבקובץ כבר קיימים');
  }

  // ------------------------------------------------------------------ panel view
  open(tab) {
    if (tab) this.tab = tab;
    this.app.panel.show(this.view(true));
  }

  view(focus = true) {
    return { key: KEY, label: 'המועדפים והשמורים שלי', focus, render: (body) => this._render(body) };
  }

  _row(kind, x, i, n) {
    let icon, title, sub;
    if (kind === 'favorites') {
      icon = iconCircle({ type: x.color ? 'poi' : 'place', icon: x.icon || 'star', color: x.color });
      title = x.label || x.name;
      sub = x.label ? x.name : x.secondary;
    } else if (kind === 'routes') {
      icon = `<span class="r-icon neutral" aria-hidden="true">${svg({ car: 'car', foot: 'walk', bike: 'bike', transit: 'bus' }[x.mode] || 'directions', { size: 18 })}</span>`;
      title = x.name;
      sub = `${MODE_HE[x.mode] || ''}${x.avoid?.length ? ` · עם הימנעויות (${x.avoid.length})` : ''}`;
    } else {
      icon = `<span class="r-icon neutral" aria-hidden="true">${svg('search', { size: 18 })}</span>`;
      title = x.name;
      sub = [x.filters?.kosher && 'כשר', x.filters?.openNow && 'פתוח עכשיו'].filter(Boolean).join(' · ') || 'חיפוש';
    }
    return `<li class="result saved-row" data-id="${esc(x.id)}">
      <button type="button" data-act="open">${icon}<span class="r-body"><span class="r-name">${esc(title)}</span>
        ${sub ? `<span class="r-sub" style="display:block">${esc(sub)}</span>` : ''}</span></button>
      <span class="saved-tools">
        <button type="button" class="icon-btn" data-act="up" aria-label="הזזה למעלה: ${esc(title)}" ${i === 0 ? 'disabled' : ''}>${svg('arrow_up', { size: 16 })}</button>
        <button type="button" class="icon-btn" data-act="rename" aria-label="שינוי שם: ${esc(title)}">${svg('edit', { size: 16 })}</button>
        <button type="button" class="icon-btn" data-act="delete" aria-label="מחיקה: ${esc(title)}">${svg('x', { size: 16 })}</button>
      </span></li>`;
  }

  _render(body) {
    const kind = this.tab;
    const list = this.items[kind];
    const empty = {
      favorites: 'עדיין אין מקומות מועדפים. בכרטיס של כל מקום יש כפתור ★ "שמירה".',
      routes: 'עדיין אין מסלולים שמורים. אחרי תכנון מסלול לחצו "שמירת המסלול".',
      searches: 'עדיין אין חיפושים שמורים. בתוצאות חיפוש לחצו "שמירת החיפוש".',
    }[kind];
    body.innerHTML = `${head(this.app, 'המועדפים והשמורים שלי')}
      <div class="seg-tabs" role="tablist" aria-label="סוג">
        ${KINDS.map((k) => `<button type="button" role="tab" id="saved-tab-${k}" aria-controls="saved-list" aria-selected="${k === kind}"
          tabindex="${k === kind ? 0 : -1}" data-tab="${k}">${esc(TITLES[k])} <span class="tab-count">${this.items[k].length}</span></button>`).join('')}
      </div>
      <div id="saved-list" role="tabpanel" aria-labelledby="saved-tab-${kind}">
        ${list.length ? `<ul class="results saved-list">${list.map((x, i) => this._row(kind, x, i, list.length)).join('')}</ul>`
          : `<div class="p-empty">${svg('star', { size: 36 })}<p>${esc(empty)}</p></div>`}
      </div>
      <div class="p-section saved-io">
        <button type="button" class="btn btn-secondary btn-sm" data-act="export">${svg('download', { size: 16 })}ייצוא לקובץ</button>
        <button type="button" class="btn btn-secondary btn-sm" data-act="import">ייבוא מקובץ…</button>
        <input type="file" accept=".json,application/json" hidden data-act="file" tabindex="-1" aria-label="קובץ שמורים">
        <p class="r-sub">${this.server ? 'נשמר רק במחשב הזה, בתיקיית הנתונים של התוכנה.' : 'נשמר בדפדפן הזה בלבד.'}</p>
      </div>`;
    wireHead(body, this.app);
    const tabs = [...body.querySelectorAll('[data-tab]')];
    tabs.forEach((b, i) => {
      b.addEventListener('click', () => { this.tab = b.dataset.tab; this.app.panel.update(this.view(false)); this._focusTab(); });
      b.addEventListener('keydown', (e) => {
        const dir = e.key === 'ArrowLeft' ? 1 : e.key === 'ArrowRight' ? -1 : 0;
        if (!dir) return;
        e.preventDefault();
        this.tab = tabs[(i + dir + tabs.length) % tabs.length].dataset.tab;
        this.app.panel.update(this.view(false));
        this._focusTab();
      });
    });
    body.querySelectorAll('.saved-row').forEach((li) => {
      const x = list.find((i) => i.id === li.dataset.id);
      li.querySelector('[data-act="open"]').addEventListener('click', () => this._open(kind, x));
      li.querySelector('[data-act="delete"]').addEventListener('click', () => {
        this.remove(kind, x.id);
        announce('נמחק');
      });
      li.querySelector('[data-act="up"]').addEventListener('click', () => this.move(kind, x.id, -1));
      li.querySelector('[data-act="rename"]').addEventListener('click', () => this._renameInline(li, kind, x));
    });
    const file = body.querySelector('[data-act="file"]');
    body.querySelector('[data-act="export"]').addEventListener('click', () => this.exportFile());
    body.querySelector('[data-act="import"]').addEventListener('click', () => file.click());
    file.addEventListener('change', () => {
      if (file.files?.[0]) this.importFile(file.files[0]);
      file.value = '';
    });
  }

  _focusTab() {
    this.app.panel.body.querySelector(`[data-tab="${this.tab}"]`)?.focus({ preventScroll: true });
  }

  _renameInline(li, kind, x) {
    const cur = kind === 'favorites' ? (x.label || x.name) : x.name;
    const form = document.createElement('form');
    form.className = 'saved-rename';
    form.innerHTML = `<label class="sr-only" for="saved-rn">שם חדש</label>
      <input id="saved-rn" type="text" maxlength="80" value="${esc(cur)}" placeholder="${kind === 'favorites' ? 'למשל: הבית, העבודה, סבתא' : ''}">
      <button type="submit" class="btn btn-primary btn-sm">שמירה</button>
      <button type="button" class="btn btn-secondary btn-sm" data-act="cancel">ביטול</button>`;
    li.append(form);
    const input = form.querySelector('input');
    input.focus();
    input.select();
    form.addEventListener('submit', (e) => {
      e.preventDefault();
      this.rename(kind, x.id, input.value);
      announce('השם עודכן');
    });
    form.querySelector('[data-act="cancel"]').addEventListener('click', () => form.remove());
  }

  _open(kind, x) {
    if (kind === 'favorites') this.app.openItem({ ...x, name: x.name });
    else if (kind === 'routes') this.openRoute(x);
    else this.app.search(x.q, x.filters || {});
  }
}

function same(a, b) {
  return Math.abs(a.lat - b.lat) < 1e-5 && Math.abs(a.lon - b.lon) < 1e-5 && !!a.mine === !!b.mine;
}

export function favoriteButton(app, item) {
  const on = app.saved?.isFavorite(item.id);
  return `<button type="button" class="action" data-act="fav" aria-pressed="${on}">
    <span class="a-circle">${svg('star', { cls: on ? 'icon-filled' : '' })}</span>${on ? 'שמור' : 'שמירה'}</button>`;
}

export function wireFavorite(body, app, item) {
  const b = body.querySelector('[data-act="fav"]');
  if (!b) return;
  b.addEventListener('click', () => {
    const on = app.saved.toggleFavorite(item);
    b.setAttribute('aria-pressed', String(on));
    b.querySelector('svg').classList.toggle('icon-filled', on);
    b.lastChild.textContent = on ? 'שמור' : 'שמירה';
  });
}


