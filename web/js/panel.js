// Side panel (desktop) / draggable bottom sheet (mobile) with a back stack.

import { bus, isMobile } from './util.js';

const SNAPS = ['peek', 'half', 'full'];

export class Panel {
  constructor(el) {
    this.el = el;
    this.body = el.querySelector('#panel-body');
    this.toggle = el.querySelector('#sheet-toggle');
    this.stack = [];
    this.current = null;
    this.toggle.addEventListener('click', () => this.cycleSnap());
    this._bindDrag();
    el.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && !e.defaultPrevented) {
        e.preventDefault();
        this.back();
      }
    });
  }

  get isOpen() {
    return !this.el.hidden;
  }

  /**
   * @param {{key:string, render:(body:HTMLElement)=>void, label?:string, snap?:string, focus?:boolean, replace?:boolean}} view
   */
  show(view) {
    if (this.current && !view.replace && this.current.key !== view.key) this.stack.push(this.current);
    if (this.stack.length > 12) this.stack.shift();
    this.current = view;
    this._render(view);
  }

  /** Re-render the current view in place (e.g. after data arrives). */
  update(view) {
    if (this.current && this.current.key === view.key) {
      this.current = view;
      this._render(view, false);
    }
  }

  _render(view, focus = view.focus !== false) {
    const wasHidden = this.el.hidden;
    this.el.hidden = false;
    this.el.setAttribute('aria-label', view.label || 'חלונית מידע');
    this.body.scrollTop = 0;
    view.render(this.body);
    bus.emit('panel:view', view);
    if (isMobile()) this.setSnap(view.snap || this.el.dataset.snap || 'half');
    if (focus) {
      const target = this.body.querySelector('[data-autofocus]') || this.body.querySelector('h2') || this.el;
      if (target.tagName === 'H2' && !target.hasAttribute('tabindex')) target.setAttribute('tabindex', '-1');
      target.focus({ preventScroll: true });
    }
    if (wasHidden) bus.emit('panel:open');
    this._updateControls();
  }

  canGoBack() {
    return this.stack.length > 0;
  }

  back() {
    const prev = this.stack.pop();
    if (prev) {
      this.current = prev;
      this._render(prev);
      bus.emit('panel:back', prev);
    } else {
      this.close();
    }
  }

  close() {
    if (this.el.hidden) return;
    this.el.hidden = true;
    this.stack = [];
    const closed = this.current;
    this.current = null;
    this.body.innerHTML = '';
    this._updateControls();
    bus.emit('panel:close', closed);
  }

  // ------------------------------------------------------------ mobile sheet
  setSnap(snap) {
    this.el.dataset.snap = snap;
    this.toggle.setAttribute('aria-expanded', String(snap === 'full'));
    this.toggle.setAttribute('aria-label', snap === 'full' ? 'הקטנת החלונית' : 'הרחבת החלונית');
    this._updateControls();
  }

  cycleSnap() {
    const i = SNAPS.indexOf(this.el.dataset.snap || 'half');
    this.setSnap(SNAPS[(i + 1) % SNAPS.length]);
  }

  _updateControls() {
    // map controls float just above the bottom sheet (computed from the snap, not the animating height)
    const frac = { peek: 0.3, half: 0.55, full: 1 }[this.el.dataset.snap || 'half'];
    const off = this.el.hidden || !isMobile() ? 0 : window.innerHeight * frac;
    document.documentElement.style.setProperty('--controls-offset', `${Math.round(off)}px`);
    document.documentElement.classList.toggle('sheet-full', !this.el.hidden && isMobile() && frac === 1);
  }

  _bindDrag() {
    const handle = this.el.querySelector('.sheet-handle');
    let startY = 0, startH = 0, dragging = false, pressed = false;
    handle.addEventListener('pointerdown', (e) => {
      if (!isMobile()) return;
      pressed = true;
      startY = e.clientY;
      startH = this.el.getBoundingClientRect().height;
    });
    handle.addEventListener('pointermove', (e) => {
      if (!pressed) return;
      if (!dragging) {
        if (Math.abs(e.clientY - startY) < 6) return; // still a tap -> let the button get its click
        dragging = true;
        this.el.classList.add('dragging');
        handle.setPointerCapture(e.pointerId);
      }
      const h = Math.max(80, Math.min(window.innerHeight - 64, startH + (startY - e.clientY)));
      this.el.style.setProperty('--sheet-h', `${h}px`);
    });
    const end = (e) => {
      pressed = false;
      if (!dragging) return;
      dragging = false;
      this.el.classList.remove('dragging');
      const h = this.el.getBoundingClientRect().height;
      this.el.style.removeProperty('--sheet-h');
      const moved = Math.abs(startY - e.clientY);
      if (moved < 6) return; // a tap: handled by the button
      const ratio = h / window.innerHeight;
      if (ratio < 0.18) this.close();
      else this.setSnap(ratio < 0.42 ? 'peek' : ratio < 0.75 ? 'half' : 'full');
    };
    handle.addEventListener('pointerup', end);
    handle.addEventListener('pointercancel', end);
    window.addEventListener('resize', () => this._updateControls());
  }
}
