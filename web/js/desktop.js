// מפות בעברית – desktop shell: tabs in a custom RTL title bar, one same-origin map iframe per tab.
//
// Each tab is <iframe src="/map.html#…">. The map reports what it shows ({type:'hmaps:state', title, kind, hash})
// with postMessage (web/js/app.js → reportState), so tab titles follow the search / selected place / route,
// and the open tabs (with their URL hash) are restored on the next start. Background tabs keep their iframe
// (instant switching); at most MAX_LIVE maps stay loaded, older background tabs are unloaded and reload
// from their hash when activated again. Tabs restored at startup load lazily, on first activation.
//
// In the Tauri desktop app (no native frame) the window buttons and the drag area call the window commands
// that src-tauri grants to this origin only: minimize, toggle_maximize, close, start_dragging, is_maximized.
// In a normal browser the window buttons are hidden and everything else works the same.

import { ICONS } from './icons.js';

const STORE_KEY = 'hmaps:desktop-tabs';
const MAX_LIVE = 8;
const MAX_CLOSED = 10;
const APP_NAME = 'מפות בעברית';
const DEFAULT_TITLE = 'מפה';
const KIND_ICON = {
  map: 'M3 6.5l6-3 6 3 6-3v14l-6 3-6-3-6 3zM9 3.5v14M15 6.5v14',
  search: ICONS.search,
  place: ICONS.pin,
  directions: ICONS.directions,
  hike: ICONS.hiker,
  panel: ICONS.info,
};

const $ = (sel, root = document) => root.querySelector(sel);
const strip = $('#tabstrip');
const views = $('#views');
const tauri = window.__TAURI_INTERNALS__?.invoke ? window.__TAURI_INTERNALS__ : null;
const motion = matchMedia('(prefers-reduced-motion: reduce)');
const ANIM_MS = 160;
/** The tabs in the strip, in visual order (tabs that are animating closed are left out). */
const liveTabEls = () => [...strip.querySelectorAll('.tab:not(.closing)')];

/** @type {Map<string, Tab>} */
const tabs = new Map();
let order = []; // tab ids, in visual order (first = rightmost in RTL)
let activeId = null;
let seq = 0;
const closed = []; // recently closed tabs (Ctrl+Shift+T)

/**
 * @typedef {{id:string, hash:string, title:string, kind:string, el:HTMLElement, view:HTMLElement,
 *   frame:HTMLIFrameElement|null, loading:boolean, used:number, focusOnLoad:boolean}} Tab
 */

// ------------------------------------------------------------------ helpers
const cleanHash = (h) => {
  const s = typeof h === 'string' ? h.trim() : '';
  if (!s || s === '#') return '';
  return (s.startsWith('#') ? s : '#' + s).slice(0, 4000);
};
const iconSvg = (kind) =>
  `<svg aria-hidden="true" focusable="false" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="${KIND_ICON[kind] || KIND_ICON.map}"/></svg>`;

function announce(text) {
  const el = $('#sr-status');
  el.textContent = '';
  setTimeout(() => (el.textContent = text), 50);
}

/** A same-origin map URL (/map.html or /, which serves the map too) → its hash; anything else → null. */
function mapHash(href) {
  let u;
  try {
    u = new URL(href, location.href);
  } catch {
    return null;
  }
  if (u.origin !== location.origin || !['/map.html', '/'].includes(u.pathname)) return null;
  return cleanHash(u.hash);
}

// ------------------------------------------------------------------ tabs
function createTab({ hash = '', title = DEFAULT_TITLE, kind = 'map' } = {}, { index = order.length } = {}) {
  const id = `t${++seq}`;
  const el = document.createElement('div');
  el.className = 'tab';
  el.dataset.id = id;
  el.innerHTML = `<span class="foot foot-l"></span><span class="foot foot-r"></span>
    <div class="tab-main" role="tab" id="tab-${id}" aria-selected="false" aria-controls="view-${id}" tabindex="-1">
      <span class="tab-icon"></span><span class="tab-title"></span></div>
    <button type="button" class="tab-close" tabindex="-1"><svg aria-hidden="true" focusable="false" viewBox="0 0 10 10"><path d="M1 1l8 8M9 1L1 9" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg></button>`;
  const view = document.createElement('div');
  view.className = 'view';
  view.id = `view-${id}`;
  view.setAttribute('role', 'tabpanel');
  view.setAttribute('aria-labelledby', `tab-${id}`);
  views.append(view);
  const tab = { id, hash: cleanHash(hash), title: title || DEFAULT_TITLE, kind, el, view, frame: null, loading: false, used: 0, focusOnLoad: false };
  tabs.set(id, tab);
  order.splice(index, 0, id);
  strip.insertBefore(el, liveTabEls()[index] || null);
  renderTab(tab);
  animateOpen(el);
  return tab;
}

/** Chrome-like open: the tab grows from nothing to its width. */
function animateOpen(el) {
  if (motion.matches || !el.isConnected || document.readyState === 'loading') return;
  const w = el.getBoundingClientRect().width;
  el.animate([{ maxWidth: '0px', minWidth: '0px', opacity: 0 }, { maxWidth: `${w}px`, minWidth: '0px', opacity: 1 }],
    { duration: ANIM_MS, easing: 'cubic-bezier(.2, 0, 0, 1)' });
}

/** Chrome-like close: the tab shrinks away, then its element is removed. */
function animateClose(el) {
  el.classList.add('closing');
  el.setAttribute('aria-hidden', 'true');
  el.querySelector('.tab-main').removeAttribute('role');
  if (motion.matches) {
    el.remove();
    return;
  }
  const w = el.getBoundingClientRect().width;
  const a = el.animate([{ maxWidth: `${w}px`, minWidth: '0px' }, { maxWidth: '0px', minWidth: '0px', opacity: 0 }],
    { duration: ANIM_MS, easing: 'cubic-bezier(.3, 0, .8, .15)', fill: 'forwards' });
  a.onfinish = a.oncancel = () => el.remove();
}

// Closing tabs with the mouse keeps the other tabs' widths (Chrome does this), so the next close button
// lands under the pointer; they take the free space once the pointer leaves the tab strip.
const tabsWrap = $('#tabs-wrap');
function freezeWidths() {
  for (const el of liveTabEls()) el.style.maxWidth = `${el.getBoundingClientRect().width}px`;
  tabsWrap.classList.add('frozen');
}
function unfreezeWidths() {
  if (!tabsWrap.classList.contains('frozen')) return;
  tabsWrap.classList.remove('frozen');
  const els = liveTabEls();
  const before = els.map((el) => el.getBoundingClientRect().width);
  els.forEach((el) => (el.style.maxWidth = ''));
  if (motion.matches) return;
  els.forEach((el, i) => {
    const w = el.getBoundingClientRect().width;
    if (Math.abs(w - before[i]) > .5) {
      el.animate([{ maxWidth: `${before[i]}px`, minWidth: `${Math.min(before[i], w)}px` }, { maxWidth: `${w}px`, minWidth: `${Math.min(before[i], w)}px` }],
        { duration: ANIM_MS, easing: 'cubic-bezier(.2, 0, 0, 1)' });
    }
  });
}
tabsWrap.addEventListener('mouseleave', unfreezeWidths);

function renderTab(tab) {
  const t = tab.title || DEFAULT_TITLE;
  tab.el.querySelector('.tab-title').textContent = t;
  tab.el.querySelector('.tab-icon').innerHTML = iconSvg(tab.kind);
  tab.el.title = t;
  tab.el.dataset.kind = KIND_ICON[tab.kind] ? tab.kind : 'map';
  tab.el.querySelector('.tab-close').setAttribute('aria-label', `סגירת הלשונית ${t}`);
  tab.el.classList.toggle('loading', tab.loading);
  tab.el.querySelector('.tab-main').setAttribute('aria-busy', String(tab.loading));
  if (tab.frame) tab.frame.title = `מפה: ${t}`;
  if (tab.id === activeId) document.title = t === DEFAULT_TITLE ? APP_NAME : `${t} – ${APP_NAME}`;
}

function load(tab) {
  if (tab.frame) return;
  const f = document.createElement('iframe');
  f.src = '/map.html' + tab.hash;
  f.title = `מפה: ${tab.title}`;
  f.setAttribute('allow', 'geolocation; clipboard-read; clipboard-write; fullscreen');
  f.addEventListener('load', () => onFrameLoad(tab, f));
  tab.frame = f;
  tab.loading = true;
  tab.view.append(f);
  renderTab(tab);
  discardOld();
}

/** Unload the least recently used background maps (each holds a WebGL context and tile caches). */
function discardOld() {
  const live = [...tabs.values()].filter((t) => t.frame && t.id !== activeId).sort((a, b) => a.used - b.used);
  while (live.length + 1 > MAX_LIVE) {
    const t = live.shift();
    t.frame.remove();
    t.frame = null;
    t.loading = false;
    renderTab(t);
  }
}

function activate(id, { focusContent = false, focusTab = false } = {}) {
  const tab = tabs.get(id);
  if (!tab) return;
  const prev = tabs.get(activeId);
  if (prev && prev !== tab) {
    prev.el.classList.remove('active');
    prev.view.classList.remove('active');
    prev.view.inert = true;
    const pm = prev.el.querySelector('.tab-main');
    pm.setAttribute('aria-selected', 'false');
    pm.tabIndex = -1;
  }
  activeId = id;
  tab.used = Date.now();
  tab.el.classList.add('active');
  tab.view.classList.add('active');
  tab.view.inert = false;
  const main = tab.el.querySelector('.tab-main');
  main.setAttribute('aria-selected', 'true');
  main.tabIndex = 0;
  load(tab);
  renderTab(tab);
  tab.el.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  if (focusTab) main.focus();
  else if (focusContent) focusFrame(tab);
  save();
}

function focusFrame(tab, search = false) {
  if (!tab.frame) return;
  if (tab.loading) {
    tab.focusOnLoad = search ? 'search' : true;
    return;
  }
  try {
    tab.frame.contentWindow.focus();
    const q = search && tab.frame.contentDocument.getElementById('q');
    if (q) q.focus();
  } catch {
    tab.frame.focus();
  }
}

function newTab({ hash = '', title, kind, activate: act = true, after = null, focusSearch = true } = {}) {
  const index = after ? order.indexOf(after) + 1 : order.length;
  const tab = createTab({ hash, title, kind }, { index });
  if (act) {
    activate(tab.id);
    if (focusSearch) focusFrame(tab, true);
  } else {
    save();
  }
  announce(act ? 'נפתחה לשונית חדשה' : 'נפתחה לשונית חדשה ברקע');
  return tab;
}

function closeTab(id, { byMouse = false } = {}) {
  const tab = tabs.get(id);
  if (!tab) return;
  if (byMouse && order.length > 1) freezeWidths();
  const i = order.indexOf(id);
  closed.push({ hash: tab.hash, title: tab.title, kind: tab.kind, index: i });
  if (closed.length > MAX_CLOSED) closed.shift();
  const wasActive = id === activeId;
  const hadFocus = tab.el.contains(document.activeElement);
  tab.frame?.remove();
  animateClose(tab.el);
  tab.view.remove();
  tabs.delete(id);
  order.splice(i, 1);
  if (!order.length) {
    // closing the last tab leaves a fresh map (closing the window is the window's ✕ / Alt+F4)
    activeId = null;
    newTab({ focusSearch: true });
    return;
  }
  if (wasActive) {
    activeId = null;
    activate(order[Math.min(i, order.length - 1)], { focusTab: hadFocus, focusContent: !hadFocus });
  } else {
    if (hadFocus) tabs.get(activeId).el.querySelector('.tab-main').focus();
    save();
  }
  announce(`הלשונית ${tab.title} נסגרה`);
}

function reopenClosed() {
  const c = closed.pop();
  if (!c) return;
  const tab = createTab(c, { index: Math.min(c.index, order.length) });
  activate(tab.id, { focusContent: true });
}

function cycle(delta) {
  if (order.length < 2) return;
  const i = order.indexOf(activeId);
  activate(order[(i + delta + order.length) % order.length], { focusContent: true });
}

// ------------------------------------------------------------------ map → shell messages
window.addEventListener('message', (e) => {
  if (e.origin !== location.origin || !e.data || e.data.type !== 'hmaps:state') return;
  const tab = [...tabs.values()].find((t) => t.frame && t.frame.contentWindow === e.source);
  if (!tab) return;
  const { title, kind, hash } = e.data;
  if (typeof title === 'string') tab.title = title.trim().slice(0, 200) || DEFAULT_TITLE;
  if (typeof kind === 'string' && KIND_ICON[kind]) tab.kind = kind;
  if (typeof hash === 'string') tab.hash = cleanHash(hash);
  renderTab(tab);
  save();
});

function onFrameLoad(tab, frame) {
  if (tab.frame !== frame) return;
  tab.loading = false;
  renderTab(tab);
  let win, doc;
  try {
    win = frame.contentWindow;
    doc = frame.contentDocument;
  } catch {
    return;
  }
  if (!doc) return;
  // shortcuts work while the map has the focus
  win.addEventListener('keydown', onKey, true);
  win.addEventListener('focus', updateFocus);
  win.addEventListener('blur', updateFocus);
  // links to another map in a new window (target=_blank, Ctrl+click, middle click) open a tab instead
  const onLink = (e) => {
    const a = e.target.closest?.('a[href]');
    if (!a || e.defaultPrevented) return;
    const middle = e.type === 'auxclick' && e.button === 1;
    if (e.type === 'auxclick' && !middle) return;
    const newWin = a.target === '_blank' || e.ctrlKey || e.metaKey || e.shiftKey || middle;
    const hash = newWin ? mapHash(a.href) : null;
    if (hash === null) return;
    e.preventDefault();
    newTab({ hash, after: tab.id, activate: !(middle || e.ctrlKey || e.metaKey), focusSearch: false });
  };
  doc.addEventListener('click', onLink, true);
  doc.addEventListener('auxclick', onLink, true);
  if (tab.focusOnLoad) {
    const search = tab.focusOnLoad === 'search';
    tab.focusOnLoad = false;
    if (tab.id === activeId) focusFrame(tab, search);
  }
}

// ------------------------------------------------------------------ persistence
let saveTimer = null;
function save() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(saveNow, 200);
}
function saveNow() {
  clearTimeout(saveTimer);
  const list = order.map((id) => tabs.get(id)).map((t) => ({ hash: t.hash, title: t.title, kind: t.kind }));
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify({ v: 1, tabs: list, active: Math.max(0, order.indexOf(activeId)) }));
  } catch {
    /* storage unavailable – tabs are not restored */
  }
}
function restore() {
  try {
    const s = JSON.parse(localStorage.getItem(STORE_KEY) || 'null');
    if (!s || s.v !== 1 || !Array.isArray(s.tabs)) return null;
    const list = s.tabs.filter((t) => t && typeof t === 'object').slice(0, 50).map((t) => ({
      hash: cleanHash(t.hash), title: typeof t.title === 'string' ? t.title.slice(0, 200) : DEFAULT_TITLE,
      kind: KIND_ICON[t.kind] ? t.kind : 'map',
    }));
    return list.length ? { tabs: list, active: Math.min(Math.max(0, s.active | 0), list.length - 1) } : null;
  } catch {
    return null;
  }
}
window.addEventListener('pagehide', saveNow);

// ------------------------------------------------------------------ tab strip interaction
strip.addEventListener('click', (e) => {
  const el = e.target.closest('.tab');
  if (!el) return;
  if (e.target.closest('.tab-close')) closeTab(el.dataset.id, { byMouse: e.detail > 0 });
  else if (!el.dataset.dragged) activate(el.dataset.id);
});
strip.addEventListener('mousedown', (e) => {
  if (e.button === 1) e.preventDefault(); // no autoscroll on middle click
});
strip.addEventListener('auxclick', (e) => {
  const el = e.target.closest('.tab');
  if (el && e.button === 1) {
    e.preventDefault();
    closeTab(el.dataset.id, { byMouse: true });
  }
});
strip.addEventListener('dblclick', (e) => {
  if (e.target === strip && !tauri) newTab(); // in a browser, double-click on the empty strip = new tab
});
strip.addEventListener('wheel', (e) => {
  if (strip.scrollWidth <= strip.clientWidth) return;
  e.preventDefault();
  strip.scrollBy({ left: -(e.deltaY || e.deltaX), behavior: 'auto' });
}, { passive: false });
strip.addEventListener('keydown', (e) => {
  const el = e.target.closest('.tab');
  if (!el || !e.target.matches('.tab-main')) return;
  const i = order.indexOf(el.dataset.id);
  const rtl = getComputedStyle(strip).direction === 'rtl';
  let j = null;
  if (e.key === 'ArrowLeft') j = rtl ? i + 1 : i - 1;
  else if (e.key === 'ArrowRight') j = rtl ? i - 1 : i + 1;
  else if (e.key === 'Home') j = 0;
  else if (e.key === 'End') j = order.length - 1;
  else if (e.key === 'Delete') {
    e.preventDefault();
    closeTab(el.dataset.id);
    const t = tabs.get(activeId);
    t?.el.querySelector('.tab-main').focus();
    return;
  } else if (e.key === 'Enter' || e.key === ' ') {
    e.preventDefault();
    focusFrame(tabs.get(el.dataset.id));
    return;
  }
  if (j === null) return;
  e.preventDefault();
  j = (j + order.length) % order.length;
  activate(order[j], { focusTab: true });
});
$('#btn-new-tab').addEventListener('click', () => newTab());

// drag to reorder (live, like Chrome): the tab lifts and follows the pointer; the neighbours it passes slide
// into its old place. Positions come from offsetLeft (layout, unaffected by the transforms of the animations).
strip.addEventListener('pointerdown', (e) => {
  const el = e.target.closest('.tab');
  if (!el || el.classList.contains('closing') || e.button !== 0 || e.target.closest('.tab-close')) return;
  const startX = e.clientX;
  const grab = e.clientX - el.getBoundingClientRect().left;
  let dragging = false;
  const move = (ev) => {
    if (!dragging) {
      if (Math.abs(ev.clientX - startX) < 5) return;
      dragging = true;
      el.classList.add('dragging');
      el.setPointerCapture?.(e.pointerId);
      if (el.dataset.id !== activeId) activate(el.dataset.id);
    }
    const sr = strip.getBoundingClientRect();
    const w = el.offsetWidth;
    // where the tab should be, in the strip's layout coordinates (offsetLeft), kept inside the strip
    const origin = sr.left + strip.clientLeft - strip.scrollLeft;
    const first = liveTabEls().reduce((m, t) => Math.min(m, t.offsetLeft), Infinity);
    const last = liveTabEls().reduce((m, t) => Math.max(m, t.offsetLeft + t.offsetWidth), -Infinity);
    const x = Math.min(Math.max(ev.clientX - grab - origin, first), last - w);
    // its slot: the number of other tabs whose centre is left of its centre
    const others = liveTabEls().filter((t) => t !== el);
    const center = x + w / 2;
    const k = others.filter((t) => t.offsetLeft + t.offsetWidth / 2 < center).length;
    const byX = [...others].sort((p, q) => p.offsetLeft - q.offsetLeft);
    const rtl = getComputedStyle(strip).direction === 'rtl';
    // DOM order runs right-to-left in RTL
    const domIndex = rtl ? others.length - k : k;
    const domOthers = rtl ? [...byX].reverse() : byX;
    const ref = domOthers[domIndex] || null;
    if (el.nextElementSibling !== ref || (ref === null && el !== strip.lastElementChild)) {
      const before = new Map(others.map((t) => [t, t.offsetLeft]));
      strip.insertBefore(el, ref);
      if (!motion.matches) {
        for (const t of others) {
          const dx = before.get(t) - t.offsetLeft;
          if (dx) t.animate([{ transform: `translateX(${dx}px)` }, { transform: 'none' }], { duration: 150, easing: 'cubic-bezier(.2, 0, 0, 1)' });
        }
      }
    }
    el.style.transform = `translateX(${x - el.offsetLeft}px)`;
  };
  const up = () => {
    strip.removeEventListener('pointermove', move);
    strip.removeEventListener('pointerup', up);
    strip.removeEventListener('pointercancel', up);
    if (!dragging) return;
    el.classList.remove('dragging');
    const from = el.style.transform;
    el.style.transform = '';
    if (from && !motion.matches) el.animate([{ transform: from }, { transform: 'none' }], { duration: 150, easing: 'ease-out' });
    el.dataset.dragged = '1'; // swallow the click that follows
    setTimeout(() => delete el.dataset.dragged, 0);
    order = liveTabEls().map((t) => t.dataset.id);
    save();
  };
  strip.addEventListener('pointermove', move);
  strip.addEventListener('pointerup', up);
  strip.addEventListener('pointercancel', up);
});

// ------------------------------------------------------------------ keyboard shortcuts
function onKey(e) {
  const ctrl = e.ctrlKey || e.metaKey;
  if (!ctrl || e.altKey) return;
  const code = e.code;
  let handled = true;
  if (code === 'KeyT' && e.shiftKey) reopenClosed();
  else if (code === 'KeyT') newTab();
  else if (code === 'KeyW' || code === 'F4') closeTab(activeId);
  else if (e.key === 'Tab' || code === 'PageDown' || code === 'PageUp') {
    cycle((e.key === 'Tab' ? e.shiftKey : code === 'PageUp') ? -1 : 1);
  } else if (/^Digit[1-9]$/.test(code) && !e.shiftKey) {
    const n = Number(code.slice(5));
    const id = n === 9 ? order[order.length - 1] : order[n - 1];
    if (id) activate(id, { focusContent: true });
  } else handled = false;
  if (handled) {
    e.preventDefault();
    e.stopPropagation();
  }
}
window.addEventListener('keydown', onKey, true);

// ------------------------------------------------------------------ window (Tauri) and theme
function updateFocus() {
  setTimeout(() => document.documentElement.classList.toggle('blurred', !document.hasFocus()), 0);
}
window.addEventListener('focus', updateFocus);
window.addEventListener('blur', updateFocus);

function applyTheme() {
  let t = 'auto';
  try {
    t = JSON.parse(localStorage.getItem('hmaps:uitheme') || '"auto"');
  } catch { /* default */ }
  document.documentElement.dataset.theme = ['light', 'dark'].includes(t) ? t : 'auto';
}
window.addEventListener('storage', (e) => {
  if (e.key === 'hmaps:uitheme') applyTheme(); // changed in a map's "מראה הממשק"
});

function initWindow() {
  if (!tauri) return;
  const call = (cmd) => tauri.invoke(`plugin:window|${cmd}`).catch(() => null);
  const ctl = $('#winctl');
  ctl.hidden = false;
  document.documentElement.classList.add('tauri');
  const maxBtn = $('#wc-max');
  const syncMax = async () => {
    const m = await call('is_maximized');
    document.documentElement.classList.toggle('maximized', !!m);
    const label = m ? 'שחזור' : 'הגדלה';
    maxBtn.setAttribute('aria-label', label);
    maxBtn.title = label;
  };
  $('#wc-min').addEventListener('click', () => call('minimize'));
  maxBtn.addEventListener('click', () => call('toggle_maximize'));
  $('#wc-close').addEventListener('click', () => {
    saveNow();
    call('close');
  });
  let t;
  window.addEventListener('resize', () => {
    clearTimeout(t);
    t = setTimeout(syncMax, 60);
  });
  syncMax();
}

// ------------------------------------------------------------------ start
/** Rust (src-tauri) calls this for links that ask for a new window, e.g. window.open('/map.html#…'). */
window.hmapsDesktop = {
  openUrl(href) {
    const hash = mapHash(href);
    if (hash === null) return false;
    newTab({ hash, focusSearch: false });
    return true;
  },
};

applyTheme();
initWindow();
updateFocus();
{
  const saved = restore();
  if (saved) {
    saved.tabs.forEach((t) => createTab(t));
  }
  // desktop.html#map=… (or a map hash passed by the app) opens that map in a tab of its own
  const initial = cleanHash(location.hash);
  if (initial) {
    history.replaceState(null, '', location.pathname + location.search);
    const dup = [...tabs.values()].find((t) => t.hash === initial);
    activate(dup ? dup.id : createTab({ hash: initial }).id);
  } else if (saved) {
    activate(order[saved.active]);
  } else {
    newTab({ focusSearch: false });
  }
  saveNow();
}
