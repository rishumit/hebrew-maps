// "תצלומי אוויר לשימוש בלי אינטרנט" – a section of the updates panel (web/js/updates.js).
// Save the area on the map, or the whole country at a chosen level of detail; the server downloads
// in the background (hmaps/api/satellite.py) and the progress is shown here. The state lives in this
// module, so the section survives the panel being re-rendered while an update is polling.

import { api } from './api.js';
import { svg } from './icons.js';
import { announce, esc } from './util.js';

const st = {
  running: false,
  job: null,
  status: '',
  pending: null, // { bbox, minzoom, maxzoom }
  levels: null, // whole-country estimates
  country: false, // level chooser open
  level: 'basic',
};
let timer = null;
let lastBucket = -1;
let appRef = null;

const fmt = (n) => Number(n).toLocaleString('he-IL');
const mb = (b) => {
  if (b >= 1e9) return `${fmt(Math.round(b / 1e8) / 10)}GB`;
  return b >= 1e6 ? `${fmt(Math.round(b / 1e6))}MB` : `${fmt(Math.max(1, Math.round(b / 1e3)))}KB`;
};
const dur = (s) => {
  if (s == null) return '';
  if (s < 90) return 'פחות משתי דקות';
  if (s < 3600) return `כ-${Math.round(s / 60)} דקות`;
  if (s < 48 * 3600) return `כ-${fmt(Math.round(s / 360) / 10)} שעות`;
  return `כ-${fmt(Math.round(s / 8640) / 10)} ימים`;
};

const LEVELS = [
  ['basic', 'בסיסי', 'זום עד 13'],
  ['detailed', 'מפורט', 'זום עד 15'],
  ['full', 'מלא', 'זום עד 16 – גדול מאוד'],
];

function estText(id) {
  const lv = st.levels?.find((l) => l.id === id);
  if (!lv) return '';
  return lv.missing === 0 ? 'כבר שמור במחשב'
    : `${fmt(lv.missing)} אריחים, כ-${mb(lv.bytes_estimate)}, ${dur(lv.seconds_estimate)}${lv.cached ? ` (${fmt(lv.cached)} כבר שמורים)` : ''}`;
}

export function satelliteHtml() {
  const pct = st.job?.percent != null ? Math.floor(st.job.percent) : 0;
  return `<section class="p-section upd-sat" aria-labelledby="upd-sat-h">
    <h3 id="upd-sat-h">תצלומי אוויר לשימוש בלי אינטרנט</h3>
    <p class="upd-desc">תצלומי האוויר של מרכז מיפוי ישראל נשמרים במחשב לכל אזור שצפיתם בו. כאן אפשר להוריד מראש
      את האזור שבמפה או את כל הארץ, ולהשתמש בהם גם בלי חיבור.</p>
    <div class="upd-actions">
      <button type="button" class="btn btn-secondary btn-sm" data-sat="area" aria-describedby="sat-save-status">
        ${svg('download', { size: 16 })}${st.running ? 'עצירת ההורדה' : 'שמירת האזור שבמפה'}</button>
      ${st.running ? '' : `<button type="button" class="btn btn-secondary btn-sm" data-sat="country" aria-expanded="${st.country}"
        aria-controls="sat-country">${svg('download', { size: 16 })}כל הארץ…</button>`}
    </div>
    ${st.pending && !st.running ? `<div class="sat-confirm">
      <button type="button" class="btn btn-primary btn-sm" data-sat="go">הורדה</button>
      <button type="button" class="btn btn-secondary btn-sm" data-sat="cancel">ביטול</button></div>` : ''}
    ${st.country && !st.running ? `<fieldset id="sat-country" class="sat-country"><legend>רמת פירוט</legend>
      ${LEVELS.map(([id, name, z]) => `<label class="sat-level"><input type="radio" name="satlevel" value="${id}" ${st.level === id ? 'checked' : ''}>
        <span>${name} <small>(${z})</small></span><small class="sat-est" data-level="${id}">${esc(estText(id))}</small></label>`).join('')}
      <div class="sat-confirm">
        <button type="button" class="btn btn-primary btn-sm" data-sat="country-go">הורדה</button>
        <button type="button" class="btn btn-secondary btn-sm" data-sat="country-cancel">ביטול</button>
      </div></fieldset>` : ''}
    <progress class="sat-progress" max="100" value="${pct}" aria-label="התקדמות שמירת תצלומי האוויר" ${st.running ? '' : 'hidden'}></progress>
    <p id="sat-save-status" class="sat-status" role="status" aria-live="polite">${esc(st.status)}</p>
  </section>`;
}

function box() {
  return appRef?.panel.body.querySelector('.upd-sat');
}

function refresh(focusSel) {
  const el = box();
  if (!el) return;
  el.outerHTML = satelliteHtml();
  wireSatellite(appRef.panel.body, appRef);
  if (focusSel) box()?.querySelector(focusSel)?.focus({ preventScroll: true });
}

function setStatus(text) {
  st.status = text;
  const p = box()?.querySelector('#sat-save-status');
  if (p) p.textContent = text;
}

function finish(job) {
  clearInterval(timer);
  timer = null;
  st.running = false;
  st.job = null;
  const msg = {
    done: `נשמר: ${fmt(job.fetched)} אריחים חדשים (${mb(job.bytes)}), ${fmt(job.skipped)} כבר היו שמורים. התצלומים יוצגו גם בלי אינטרנט.`,
    cancelled: `ההורדה נעצרה (${fmt(job.done)} מתוך ${fmt(job.total)}). אפשר להמשיך מאותה נקודה בלחיצה נוספת.`,
    error: 'ההורדה נעצרה: אין חיבור לשרת תצלומי האוויר (או שהסינון חוסם אותו). מה שכבר ירד נשמר – נסו שוב '
      + 'כשיש אינטרנט; לבדיקה: scripts/windows/download-satellite.cmd --test',
  }[job.state] || '';
  st.status = msg;
  if (msg) announce(msg);
  refresh();
}

function show(job) {
  st.job = job;
  const bar = box()?.querySelector('progress');
  if (bar) {
    bar.value = job.percent;
    bar.setAttribute('aria-valuetext', `${Math.floor(job.percent)}%`);
  }
  const bucket = Math.floor(job.percent / 10);
  if (bucket !== lastBucket) { // the live region speaks every 10%, not every second
    lastBucket = bucket;
    const eta = job.eta_seconds ? `, נותרו ${dur(job.eta_seconds)}` : '';
    setStatus(`מוריד תצלומי אוויר${job.area === 'israel' ? ' לכל הארץ' : ''}: `
      + `${Math.floor(job.percent)}% (${fmt(job.done)} מתוך ${fmt(job.total)}${eta})`);
  }
}

async function poll() {
  try {
    const { job } = await api.satelliteStatus();
    if (!job) return finish({ state: '' });
    if (job.state === 'running' || job.state === 'pending') show(job);
    else finish(job);
  } catch {
    /* server busy – try again on the next tick */
  }
}

function watch() {
  st.running = true;
  st.pending = null;
  st.country = false;
  lastBucket = -1;
  refresh('[data-sat="area"]');
  clearInterval(timer);
  timer = setInterval(poll, 1000);
  poll();
}

function enableSatellite() {
  const type = document.querySelector('input[name="maptype"][value="satellite"]');
  if (type && !type.checked) {
    type.checked = true;
    type.dispatchEvent(new Event('change', { bubbles: true }));
  }
}

async function estimateArea(app) {
  const m = app.map.map;
  const b = m.getBounds();
  const bbox = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()].map((v) => +v.toFixed(5));
  const minzoom = Math.max(7, Math.floor(m.getZoom()));
  st.pending = null;
  st.country = false;
  setStatus('מחשב את גודל ההורדה…');
  try {
    // the deepest zoom (up to +3, max 18) that still fits the per-download limit
    let est = null;
    let maxzoom = Math.min(minzoom + 3, 18);
    for (; maxzoom >= minzoom; maxzoom--) {
      est = await api.satelliteEstimate(bbox, minzoom, maxzoom);
      if (!est.too_many) break;
    }
    if (!est || est.too_many || maxzoom < minzoom) st.status = 'האזור גדול מדי לשמירה בבת אחת. התקרבו במפה ונסו שוב.';
    else if (est.tiles === 0) st.status = 'האזור שבמפה מחוץ לכיסוי תצלומי האוויר (ישראל).';
    else if (est.missing === 0) st.status = `כל ${fmt(est.tiles)} האריחים של האזור (זום ${minzoom}–${maxzoom}) כבר שמורים במחשב.`;
    else {
      st.pending = { bbox, minzoom, maxzoom };
      st.status = `להוריד את האזור שבמפה: ${fmt(est.missing)} אריחים (כ-${mb(est.bytes_estimate)}), זום ${minzoom}–${maxzoom}?`
        + (est.cached ? ` ${fmt(est.cached)} כבר שמורים.` : '');
    }
  } catch (e) {
    st.status = e.message;
  }
  refresh(st.pending ? '[data-sat="go"]' : '[data-sat="area"]');
}

export function wireSatellite(body, app) {
  appRef = app;
  const el = body.querySelector('.upd-sat');
  if (!el) return;
  const on = (sel, fn) => el.querySelector(`[data-sat="${sel}"]`)?.addEventListener('click', fn);
  on('area', async () => {
    if (st.running) {
      try { await api.satelliteCancel(); } catch { /* the next poll shows the state */ }
      return;
    }
    estimateArea(app);
  });
  on('cancel', () => { st.pending = null; st.status = ''; refresh('[data-sat="area"]'); });
  on('go', async () => {
    const p = st.pending;
    if (!p) return;
    try {
      await api.satelliteDownload(p.bbox, p.minzoom, p.maxzoom);
      enableSatellite();
      watch();
    } catch (e) {
      st.pending = null;
      st.status = e.message;
      refresh();
    }
  });
  on('country', async () => {
    if (st.country) { st.country = false; refresh('[data-sat="country"]'); return; }
    st.pending = null;
    setStatus('מחשב את גודל ההורדה…');
    try {
      st.levels = (await api.satelliteCountry()).levels;
      st.country = true;
      st.status = 'בחרו רמת פירוט. אפשר לעצור בכל רגע ולהמשיך אחר כך מאותה נקודה.';
    } catch (e) {
      st.status = e.message;
    }
    refresh('input[name="satlevel"]:checked');
  });
  el.querySelectorAll('input[name="satlevel"]').forEach((r) => r.addEventListener('change', () => { st.level = r.value; }));
  on('country-cancel', () => { st.country = false; st.status = ''; refresh('[data-sat="country"]'); });
  on('country-go', async () => {
    try {
      await api.satelliteDownloadCountry(st.level);
      enableSatellite();
      watch();
    } catch (e) {
      st.status = e.message;
      refresh();
    }
  });
}

/** A download started earlier (or from another tab) keeps showing its progress. */
export function resumeSatellite(app) {
  appRef = app;
  if (st.running) return;
  api.satelliteStatus().then(({ job }) => {
    if (job && (job.state === 'running' || job.state === 'pending')) watch();
  }).catch(() => {});
}
