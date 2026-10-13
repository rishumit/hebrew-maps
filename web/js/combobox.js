// Accessible autocomplete (WAI-ARIA 1.2 combobox pattern), reused for the main
// search box and the route planner fields.

import { svg } from './icons.js';
import { announce, debounce, esc, fmtDistance } from './util.js';

let uid = 0;

function highlight(name, query) {
  const n = esc(name);
  const q = (query || '').trim();
  if (q.length < 1) return n;
  const idx = name.indexOf(q);
  if (idx < 0) return n;
  return esc(name.slice(0, idx)) + '<mark>' + esc(name.slice(idx, idx + q.length)) + '</mark>' + esc(name.slice(idx + q.length));
}

export function suggestionIcon(s) {
  if (s.type === 'history') return `<span class="s-icon neutral">${svg('history', { size: 18 })}</span>`;
  if (s.type === 'mylocation') return `<span class="s-icon" style="background:#2563EB">${svg('locate', { size: 18 })}</span>`;
  if (s.type === 'poi' || s.type === 'category') {
    return `<span class="s-icon" style="background:${esc(s.color || '#4B5563')}">${svg(s.icon || 'pin', { size: 18 })}</span>`;
  }
  return `<span class="s-icon neutral">${svg(s.icon || 'pin', { size: 18 })}</span>`;
}

export class Combobox {
  /**
   * @param {HTMLInputElement} input
   * @param {HTMLUListElement} list
   * @param {{fetch: (q:string, signal:AbortSignal)=>Promise<object[]>, onSelect: (item:object)=>void,
   *          onSubmit?: (text:string)=>void, empty?: ()=>object[], minChars?: number}} opts
   */
  constructor(input, list, opts) {
    this.input = input;
    this.list = list;
    this.opts = { minChars: 1, ...opts };
    this.items = [];
    this.active = -1;
    this.ctrl = null;
    this.id = list.id || `cb-${++uid}`;
    list.id = this.id;
    list.setAttribute('role', 'listbox');
    input.setAttribute('role', 'combobox');
    input.setAttribute('aria-autocomplete', 'list');
    input.setAttribute('aria-controls', this.id);
    input.setAttribute('aria-expanded', 'false');

    this.query = debounce((q) => this._load(q), 110);
    input.addEventListener('input', () => this._onInput());
    input.addEventListener('focus', () => {
      if (!input.value.trim() && this.opts.empty) this._show(this.opts.empty(), '');
    });
    input.addEventListener('keydown', (e) => this._onKey(e));
    input.addEventListener('blur', () => setTimeout(() => {
      if (!this.list.contains(document.activeElement)) this.close();
    }, 150));
    list.addEventListener('mousedown', (e) => e.preventDefault()); // keep focus in the input
    list.addEventListener('click', (e) => {
      const li = e.target.closest('[role="option"]');
      if (li) this._choose(Number(li.dataset.index));
    });
  }

  _onInput() {
    const q = this.input.value;
    if (q.trim().length < this.opts.minChars) {
      this.query.cancel();
      if (this.ctrl) this.ctrl.abort();
      if (!q.trim() && this.opts.empty) this._show(this.opts.empty(), '');
      else this.close();
      return;
    }
    this.query(q);
  }

  async _load(q) {
    if (this.ctrl) this.ctrl.abort();
    this.ctrl = new AbortController();
    const gen = (this._gen = (this._gen || 0) + 1);
    try {
      const items = await this.opts.fetch(q, this.ctrl.signal);
      if (this.input.value !== q || gen !== this._gen) return; // stale
      this._show(items, q);
    } catch (e) {
      if (e.name !== 'AbortError') this.close();
    }
  }

  _show(items, q) {
    this.items = items || [];
    this.active = -1;
    this.input.removeAttribute('aria-activedescendant');
    if (!this.items.length) {
      this.close();
      return;
    }
    this.list.innerHTML = this.items.map((s, i) => `
      <li id="${this.id}-o${i}" role="option" class="suggestion" aria-selected="false" data-index="${i}">
        ${suggestionIcon(s)}
        <span class="s-text"><span class="s-name">${highlight(s.name, q)}</span>${s.secondary ? `<span class="s-sub">${esc(s.secondary)}</span>` : ''}</span>
        ${s.distance != null ? `<span class="s-dist">${esc(fmtDistance(s.distance))}</span>` : ''}
      </li>`).join('');
    this.list.hidden = false;
    this.input.setAttribute('aria-expanded', 'true');
    announce(this.items.length === 1 ? 'הצעה אחת זמינה' : `${this.items.length} הצעות זמינות`);
  }

  /** Stop pending lookups (e.g. the user pressed Enter) and close. */
  cancel() {
    this._gen = (this._gen || 0) + 1;
    this.query.cancel();
    if (this.ctrl) this.ctrl.abort();
    this.close();
  }

  close() {
    this.list.hidden = true;
    this.input.setAttribute('aria-expanded', 'false');
    this.input.removeAttribute('aria-activedescendant');
    this.active = -1;
  }

  _move(delta) {
    if (this.list.hidden) {
      if (this.items.length) this._show(this.items, this.input.value);
      return;
    }
    const n = this.items.length;
    let next = this.active + delta;
    if (next >= n) next = -1; // back to the text field
    if (next < -1) next = n - 1;
    this.active = next;
    this.list.querySelectorAll('[role="option"]').forEach((li, i) => li.setAttribute('aria-selected', String(i === this.active)));
    if (this.active >= 0) {
      const li = document.getElementById(`${this.id}-o${this.active}`);
      this.input.setAttribute('aria-activedescendant', li.id);
      li.scrollIntoView({ block: 'nearest' });
    } else {
      this.input.removeAttribute('aria-activedescendant');
    }
  }

  _onKey(e) {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      this._move(1);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      this._move(-1);
    } else if (e.key === 'Enter') {
      if (!this.list.hidden && this.active >= 0) {
        e.preventDefault();
        this._choose(this.active);
      } else if (this.opts.onSubmit) {
        e.preventDefault();
        this.cancel();
        this.opts.onSubmit(this.input.value.trim());
      }
    } else if (e.key === 'Escape') {
      if (!this.list.hidden) {
        e.preventDefault();
        e.stopPropagation();
        this.close();
      }
    } else if (e.key === 'Tab') {
      this.close();
    }
  }

  _choose(i) {
    const item = this.items[i];
    if (!item) return;
    this.cancel();
    this.opts.onSelect(item);
  }
}
