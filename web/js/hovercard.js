// Hover card: a compact preview of a POI card shown while the pointer rests on its map icon.
// Desktop (fine pointer) only. Basic details from the vector tile appear at once; the full
// record from /api/poi is loaded (and cached) in the background and fills the card in.
// The pointer can move onto the card to use its quick actions (route, share).

import { api } from './api.js';
import { svg } from './icons.js';
import { bus, esc } from './util.js';

const SHOW_DELAY = 200; // ms the pointer must rest on an icon before the card appears
const HIDE_DELAY = 260; // grace period: lets the pointer travel from the icon onto the card
const GAP = 16; // px between the icon and the card
const EDGE = 8; // px kept clear of the map edges
const CACHE_MAX = 300;

const cache = new Map();
function remember(id, poi) {
  cache.delete(id);
  cache.set(id, poi);
  if (cache.size > CACHE_MAX) cache.delete(cache.keys().next().value);
}

export class PoiHoverCard {
  constructor(app, container) {
    this.app = app;
    this.container = container;
    this.current = null; // icon under the pointer
    this.shown = null; // item the card is showing
    this.el = document.createElement('div');
    this.el.id = 'poi-hover';
    this.el.className = 'poi-hover';
    this.el.setAttribute('role', 'group');
    this.el.hidden = true;
    container.appendChild(this.el);
    // keep map gestures out of the card
    for (const t of ['mousedown', 'click', 'dblclick', 'wheel', 'contextmenu', 'touchstart', 'pointerdown']) {
      this.el.addEventListener(t, (e) => e.stopPropagation());
    }
    this.el.addEventListener('mouseenter', () => clearTimeout(this._hideT));
    this.el.addEventListener('mouseleave', () => { if (!this.current) this._scheduleHide(); });
    this.el.addEventListener('click', (e) => this._onClick(e));

    const mq = window.matchMedia?.('(hover: hover) and (pointer: fine)');
    this.enabled = mq ? mq.matches : true;
    mq?.addEventListener?.('change', (e) => { this.enabled = e.matches; if (!e.matches) this.hide(); });
    bus.on('poi:hover', (h) => this.enabled && this.hoverStart(h));
    bus.on('poi:hoverend', (o) => this.hoverEnd(o));
    bus.on('select', () => this.hide());
  }

  hoverStart(h) {
    if (h.id === this.app._currentItem) return this.hoverEnd({ immediate: true }); // already open in the panel
    clearTimeout(this._hideT);
    clearTimeout(this._showT);
    this.current = h;
    const show = () => {
      if (this.current !== h) return;
      this.shown = h;
      this._render(h, cache.get(h.id));
      this._load(h);
    };
    if (this.el.hidden) this._showT = setTimeout(show, SHOW_DELAY);
    else show(); // already showing a neighbour: switch immediately
  }

  hoverEnd({ immediate = false } = {}) {
    clearTimeout(this._showT);
    this.current = null;
    if (immediate) this.hide();
    else this._scheduleHide();
  }

  _scheduleHide() {
    clearTimeout(this._hideT);
    this._hideT = setTimeout(() => { if (!this.el.matches(':hover')) this.hide(); }, HIDE_DELAY);
  }

  hide() {
    clearTimeout(this._showT);
    clearTimeout(this._hideT);
    this._ctrl?.abort();
    this.shown = null;
    this.current = null;
    this.el.classList.remove('show');
    if (!this.el.hidden) bus.emit('poi:hoverreset');
    this.el.hidden = true;
  }

  async _load(h) {
    if (cache.has(h.id)) return;
    this._ctrl?.abort();
    const ctrl = (this._ctrl = new AbortController());
    try {
      const poi = await api.poi(h.id, ctrl.signal);
      remember(h.id, poi);
      if (this.shown === h) this._render(h, poi);
    } catch {
      // offline / aborted / missing: the basic card from the tile stays as is
      if (this.shown === h) this.el.querySelector('.ph-loading')?.remove();
    }
  }

  _item(h) {
    const poi = cache.get(h.id);
    return { id: h.id, name: poi?.name || h.props?.name || '', location: poi?.location || { lon: h.coords[0], lat: h.coords[1] } };
  }

  _onClick(e) {
    const h = this.shown;
    if (!h) return;
    const act = e.target.closest('[data-hact]')?.dataset.hact;
    const item = this._item(h);
    this.hide();
    if (act === 'route') this.app.directions.open({ to: { ...item.location, name: item.name, id: item.id } });
    else if (act === 'share') this.app.share(item);
    else bus.emit('select', { id: item.id, location: item.location });
  }

  _render(h, poi) {
    const p = h.props || {};
    const name = poi?.name || p.name || 'ללא שם';
    const sub = poi?.sub_category_he || this.app.subs?.[p.sub]?.name_he || '';
    const md = poi?.metadata || {};
    const img = poi?.media?.images?.[0];
    const hours = poi?.opening_hours;
    const fee = poi?.nature_site?.fee_he;
    const facts = [];
    if (sub) facts.push(`<span>${esc(sub)}</span>`);
    if (poi?.kosher || md.kosher) facts.push('<span class="kosher">כשר</span>');
    if (md.wheelchair_accessible) facts.push(`<span class="ph-a11y" title="נגיש לכיסאות גלגלים">${svg('wheelchair', { size: 15, label: 'נגיש לכיסאות גלגלים' })}</span>`);
    if (md.elevation) facts.push(`<span>${esc(md.elevation)} מ׳</span>`);
    const status = hours?.status
      ? `<div class="ph-status ${hours.open_now ? 'open' : 'closed'}">${esc(hours.status)}</div>`
      : fee ? `<div class="ph-status">${esc(fee)}</div>` : '';

    this.el.setAttribute('aria-label', name);
    this.el.innerHTML = `
      ${img ? `<img class="ph-img" src="${esc(img)}" alt="" referrerpolicy="no-referrer">` : ''}
      <div class="ph-body">
        <button type="button" class="ph-main" data-hact="open" aria-label="פרטים: ${esc(name)}">
          <span class="ph-name">${esc(name)}</span>
          ${poi?.name_en && poi.name_en !== name ? `<span class="ph-en" lang="en" dir="ltr">${esc(poi.name_en)}</span>` : ''}
          ${facts.length ? `<span class="ph-facts">${facts.join('<span aria-hidden="true">·</span>')}</span>` : ''}
          ${status}
          ${poi ? '' : '<span class="ph-loading" aria-hidden="true"><span></span></span>'}
        </button>
        <div class="ph-actions">
          <button type="button" class="ph-btn" data-hact="route" aria-label="מסלול אל ${esc(name)}" title="מסלול">${svg('directions', { size: 20 })}</button>
          <button type="button" class="ph-btn" data-hact="share" aria-label="שיתוף" title="שיתוף">${svg('share', { size: 20 })}</button>
        </div>
      </div>`;
    this.el.classList.toggle('no-img', !img);
    const im = this.el.querySelector('.ph-img');
    im?.addEventListener('error', () => { im.remove(); this._place(h); });
    im?.addEventListener('load', () => this._place(h));
    this.el.hidden = false;
    this._place(h);
    requestAnimationFrame(() => this.el.classList.add('show'));
  }

  /** Above the icon when there is room, otherwise below; kept inside the visible map (not under the panel). */
  _place(h) {
    if (this.shown !== h || this.el.hidden) return;
    const box = this.container.getBoundingClientRect();
    let right = box.width;
    const panel = document.getElementById('panel');
    if (panel && !panel.hidden && window.matchMedia?.('(min-width: 768px)').matches) {
      const pr = panel.getBoundingClientRect();
      if (pr.width && pr.left > box.left) right = Math.min(right, pr.left - box.left);
    }
    const w = this.el.offsetWidth;
    const hgt = this.el.offsetHeight;
    const { x, y } = h.point;
    let top = y - GAP - hgt;
    let below = false;
    if (top < EDGE) { top = y + GAP; below = true; }
    top = Math.max(EDGE, Math.min(top, box.height - hgt - EDGE));
    const left = Math.max(EDGE, Math.min(x - w / 2, right - w - EDGE));
    this.el.style.left = `${Math.round(left)}px`;
    this.el.style.top = `${Math.round(top)}px`;
    this.el.classList.toggle('below', below);
  }
}
