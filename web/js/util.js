// Small DOM, storage and formatting helpers.

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
export const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ESC[c]);

/** Tagged template that escapes interpolated values (use raw() to opt out). */
export function html(strings, ...values) {
  let out = '';
  strings.forEach((s, i) => {
    out += s;
    if (i < values.length) {
      const v = values[i];
      if (v && v.__raw) out += v.html;
      else if (Array.isArray(v)) out += v.map((x) => (x && x.__raw ? x.html : esc(x))).join('');
      else out += esc(v ?? '');
    }
  });
  return { __raw: true, html: out };
}
export const raw = (h) => ({ __raw: true, html: h });

export function render(el, tpl) {
  el.innerHTML = tpl.__raw ? tpl.html : esc(tpl);
}

export function debounce(fn, ms) {
  let t;
  const d = (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
  d.cancel = () => clearTimeout(t);
  return d;
}

export const store = {
  get(key, fallback = null) {
    try {
      const v = localStorage.getItem('hmaps:' + key);
      return v == null ? fallback : JSON.parse(v);
    } catch {
      return fallback;
    }
  },
  set(key, value) {
    try {
      localStorage.setItem('hmaps:' + key, JSON.stringify(value));
    } catch {
      /* storage unavailable (private mode) – ignore */
    }
  },
};

// --- screen reader announcements ------------------------------------------------
let liveTimer;
export function announce(text, assertive = false) {
  const el = document.getElementById(assertive ? 'sr-alert' : 'sr-status');
  if (!el) return;
  el.textContent = '';
  clearTimeout(liveTimer);
  liveTimer = setTimeout(() => (el.textContent = text), 60);
}

// --- toast ---------------------------------------------------------------------------
export function toast(text, { kind = 'info', ms = 3500 } = {}) {
  const host = document.getElementById('toasts');
  const t = document.createElement('div');
  t.className = `toast toast-${kind}`;
  t.setAttribute('role', kind === 'error' ? 'alert' : 'status');
  t.textContent = text;
  host.appendChild(t);
  requestAnimationFrame(() => t.classList.add('show'));
  setTimeout(() => {
    t.classList.remove('show');
    setTimeout(() => t.remove(), 300);
  }, ms);
}

export async function copyText(text, label = 'הועתק') {
  try {
    await navigator.clipboard.writeText(text);
    toast(`${label} ✓`);
  } catch {
    const ta = document.createElement('textarea');
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    try {
      document.execCommand('copy');
      toast(`${label} ✓`);
    } catch {
      toast('לא ניתן להעתיק', { kind: 'error' });
    }
    ta.remove();
  }
}

// --- formatting -------------------------------------------------------------------------
export function fmtDistance(m) {
  if (m == null) return '';
  if (m < 1000) return `${Math.round(m / 10) * 10} מ׳`;
  const km = m / 1000;
  return km < 10 ? `${km.toFixed(1)} ק״מ` : `${Math.round(km)} ק״מ`;
}

export const fmtNumber = (n) => (n == null ? '' : new Intl.NumberFormat('he-IL').format(n));

export function haversine(a, b) {
  const R = 6371008.8, toR = Math.PI / 180;
  const dLat = (b.lat - a.lat) * toR, dLon = (b.lon - a.lon) * toR;
  const x = Math.sin(dLat / 2) ** 2 + Math.cos(a.lat * toR) * Math.cos(b.lat * toR) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(x)));
}

export const isMobile = () => window.matchMedia('(max-width: 767px)').matches;
export const prefersReducedMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;

// --- simple event bus -------------------------------------------------------------------
const listeners = new Map();
export const bus = {
  on(evt, fn) {
    if (!listeners.has(evt)) listeners.set(evt, new Set());
    listeners.get(evt).add(fn);
    return () => listeners.get(evt).delete(fn);
  },
  emit(evt, data) {
    (listeners.get(evt) || []).forEach((fn) => {
      try {
        fn(data);
      } catch (e) {
        console.error(e);
      }
    });
  },
};

/** Trap Tab focus inside a dialog element; returns a release function. */
export function trapFocus(container) {
  const sel = 'a[href],button:not([disabled]),input:not([disabled]),select,textarea,[tabindex]:not([tabindex="-1"])';
  function onKey(e) {
    if (e.key !== 'Tab') return;
    const items = $$(sel, container).filter((el) => el.offsetParent !== null);
    if (!items.length) return;
    const first = items[0], last = items[items.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      last.focus();
      e.preventDefault();
    } else if (!e.shiftKey && document.activeElement === last) {
      first.focus();
      e.preventDefault();
    }
  }
  container.addEventListener('keydown', onKey);
  return () => container.removeEventListener('keydown', onKey);
}
