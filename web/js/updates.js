// "עדכונים" – online data updates from the weekly GitHub release (hmaps/api/updates.py).
// A panel view listing every data component (installed vs. available, size), with "עדכן" per
// item and "עדכן הכול", live progress while the server downloads + installs in the background,
// and an optional automatic weekly check that shows a small, dismissible notice.

import { svg } from './icons.js';
import { $, announce, bus, esc, store, toast } from './util.js';
import { head, loadingView, wireHead } from './views.js';

const KEY = 'updates';
const WEEK = 7 * 24 * 3600 * 1000;
const ACTIVE = ['queued', 'downloading', 'verifying', 'installing'];

let info = null; // last /api/updates answer
let pollTimer = null;
let appRef = null;
const watched = new Set(); // components installed since the panel started polling

// ------------------------------------------------------------------ data

async function call(url, { method = 'GET' } = {}) {
  let resp;
  try {
    resp = await fetch(url, { method, headers: method === 'POST' ? { 'X-HMaps-Update': '1' } : undefined, cache: 'no-store' });
  } catch {
    throw new Error('אין חיבור לשרת המפות. ודאו שהתוכנה פועלת ונסו שוב.');
  }
  let data = null;
  try {
    data = await resp.json();
  } catch {
    /* empty body */
  }
  if (!resp.ok) throw new Error(data?.error?.message || 'אירעה שגיאה. נסו שוב.');
  return data;
}

const checkUpdates = (refresh = false) => call(`/api/updates${refresh ? '?refresh=1' : ''}`);

// ------------------------------------------------------------------ formatting

function fmtSize(n) {
  if (!n) return '';
  if (n >= 1e9) return `${(n / 1e9).toLocaleString('he-IL', { maximumFractionDigits: 2 })} GB`;
  if (n >= 1e6) return `${Math.round(n / 1e6).toLocaleString('he-IL')} MB`;
  return `${Math.max(1, Math.round(n / 1e3)).toLocaleString('he-IL')} KB`;
}

// sizes are left-to-right ("120 MB") inside right-to-left text
const sizeHtml = (n) => (n ? `<bdi dir="ltr">${esc(fmtSize(n))}</bdi>` : '');

function fmtDate(s) {
  if (!s) return '';
  const d = new Date(s.length === 10 ? `${s}T12:00:00` : s);
  return Number.isNaN(d.getTime()) ? s : d.toLocaleDateString('he-IL', { day: 'numeric', month: 'long', year: 'numeric' });
}

function fmtTime(s) {
  if (!s) return '';
  const d = new Date(s);
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleString('he-IL', { day: 'numeric', month: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function jobText(job) {
  if (!job) return '';
  switch (job.state) {
    case 'queued': return 'ממתין לתורו…';
    case 'downloading': return job.percent != null ? `מוריד… ${Math.floor(job.percent)}%` : 'מוריד…';
    case 'verifying': return 'בודק את שלמות הקובץ…';
    case 'installing': return 'מתקין…';
    case 'done': return 'הותקן בהצלחה';
    case 'error': return job.error || 'העדכון נכשל';
    default: return '';
  }
}

function statusOf(c) {
  const job = c.job;
  if (job && ACTIVE.includes(job.state)) return { cls: 'busy', text: jobText(job) };
  if (job && job.state === 'error') return { cls: 'error', text: job.error || 'העדכון נכשל' };
  if (!c.available || !c.available.file) return { cls: 'na', text: 'עדיין לא זמין בגרסה המקוונת' };
  if (!c.compatible) return { cls: 'error', text: 'דורש גרסה חדשה יותר של התוכנה' };
  if (c.update_available) return { cls: 'new', text: c.installed ? 'עדכון זמין' : 'זמין להתקנה' };
  return { cls: 'ok', text: 'מעודכן' };
}

// ------------------------------------------------------------------ view

function itemHtml(c) {
  const st = statusOf(c);
  const job = c.job;
  const active = job && ACTIVE.includes(job.state);
  const inst = c.installed ? fmtDate(c.installed.date) || c.installed.version : 'לא מותקן';
  const av = c.available && c.available.file
    ? `${esc(fmtDate(c.available.date))}${c.available.size ? ` · ${sizeHtml(c.available.size)}` : ''}` : '—';
  const pct = job && job.percent != null ? Math.floor(job.percent) : null;
  return `<li class="upd-item" data-id="${esc(c.id)}">
    <div class="upd-top">
      <h3 class="upd-name" id="upd-n-${esc(c.id)}">${esc(c.name_he)}</h3>
      <span class="upd-badge upd-${st.cls}" data-role="badge">${esc(st.text)}</span>
    </div>
    <p class="upd-desc">${esc(c.description_he || '')}</p>
    <dl class="upd-meta">
      <div><dt>מותקן</dt><dd>${esc(inst)}</dd></div>
      <div><dt>זמין</dt><dd>${av}</dd></div>
    </dl>
    <div class="upd-progress" data-role="progress" ${active ? '' : 'hidden'}>
      <progress max="100" ${pct != null && job.state === 'downloading' ? `value="${pct}"` : ''}
        aria-labelledby="upd-n-${esc(c.id)}" aria-describedby="upd-p-${esc(c.id)}"></progress>
      <span class="upd-ptext" id="upd-p-${esc(c.id)}">${esc(active ? jobText(job) : '')}</span>
    </div>
    ${c.update_available || active ? `<button type="button" class="btn btn-secondary btn-sm" data-act="install"
        aria-describedby="upd-n-${esc(c.id)}" ${active || info?.busy ? 'disabled' : ''}>${svg('check', { size: 16 })}עדכן</button>` : ''}
  </li>`;
}

function render(body, app) {
  const n = info?.updates_available || 0;
  const busy = !!info?.busy;
  const err = info?.error;
  const auto = store.get('updates:auto', true);
  body.innerHTML = `${head(app, 'עדכונים')}
    <div class="p-section upd-intro">
      <p>חבילות הנתונים של המפה מתעדכנות פעם בשבוע ומורדות מ-GitHub. כל חבילה נבדקת (SHA-256) לפני ההתקנה,
        והמפה ממשיכה לעבוד ועוברת לנתונים החדשים בלי הפעלה מחדש.</p>
      ${err ? `<div class="alert upd-alert" role="alert">${svg('alert')}<div>${esc(err.message)}</div></div>` : ''}
      <p class="upd-checked" data-role="checked">${info?.checked_at ? `נבדק לאחרונה: ${esc(fmtTime(info.checked_at))}` : 'עדיין לא נבדק'}
        ${info?.release ? `<span class="upd-release">· גרסת נתונים ${esc(info.release.replace(/^data-/, ''))}</span>` : ''}</p>
      <div class="upd-actions">
        <button type="button" class="btn btn-primary btn-sm" data-act="all" ${n && !busy ? '' : 'disabled'}>
          עדכן הכול${n ? ` (${n === 1 ? 'חבילה אחת' : `${n} חבילות`}${info.total_size ? `, ${sizeHtml(info.total_size)}` : ''})` : ''}</button>
        <button type="button" class="btn btn-secondary btn-sm" data-act="refresh">בדיקה עכשיו</button>
      </div>
      <label class="switch upd-auto"><input type="checkbox" data-act="auto" ${auto ? 'checked' : ''}>
        <span>לבדוק אוטומטית פעם בשבוע ולהודיע כשיש עדכון</span></label>
    </div>
    <ul class="upd-list" aria-label="חבילות נתונים">${(info?.components || []).map(itemHtml).join('')}</ul>
    <p class="upd-foot">אפשר לעדכן גם בלי לפתוח את המפה: <code>update-online-windows.cmd</code> או <code>python -m hmaps update</code>.</p>`;
  wireHead(body, app);
  body.querySelector('[data-act="refresh"]').addEventListener('click', () => refresh(app, true));
  body.querySelector('[data-act="all"]').addEventListener('click', () => install(app, 'all'));
  body.querySelector('[data-act="auto"]').addEventListener('change', (e) => {
    store.set('updates:auto', e.target.checked);
    announce(e.target.checked ? 'בדיקה שבועית אוטומטית הופעלה' : 'בדיקה שבועית אוטומטית בוטלה');
  });
  body.querySelectorAll('[data-act="install"]').forEach((b) => b.addEventListener('click', () => {
    install(app, b.closest('.upd-item').dataset.id);
  }));
}

function view(app, focus = true) {
  return { key: KEY, label: 'עדכונים', focus, render: (body) => render(body, app) };
}

function isOpen(app) {
  return app.panel.current?.key === KEY;
}

function rerender(app) {
  if (!isOpen(app)) return;
  const active = document.activeElement;
  const id = active?.closest?.('.upd-item')?.dataset.id;
  const act = active?.dataset?.act;
  app.panel.update(view(app, false));
  // keep keyboard focus where it was (the list is rebuilt)
  const sel = id ? `.upd-item[data-id="${id}"] [data-act="install"]` : act ? `[data-act="${act}"]` : null;
  const target = sel && app.panel.body.querySelector(sel);
  if (target && !target.disabled) target.focus({ preventScroll: true });
  else if (id || act) app.panel.body.querySelector('h2')?.focus({ preventScroll: true });
}

async function refresh(app, force = false) {
  if (force) {
    const b = app.panel.body.querySelector('[data-act="refresh"]');
    if (b) {
      b.disabled = true;
      b.textContent = 'בודק…';
    }
  }
  try {
    info = await checkUpdates(force);
    store.set('updates:lastCheck', Date.now());
    if (force) {
      announce(info.error ? info.error.message
        : info.updates_available ? `נמצאו ${info.updates_available} עדכונים` : 'כל הנתונים מעודכנים');
    }
  } catch (e) {
    info = { ...(info || { components: [] }), error: { message: e.message } };
    announce(e.message, true);
  }
  rerender(app);
  if (info?.busy) startPolling(app);
}

async function install(app, id) {
  try {
    const res = await call(`/api/updates/${encodeURIComponent(id)}`, { method: 'POST' });
    if (!res.jobs?.length) {
      announce(res.message || 'כל הנתונים מעודכנים');
      return;
    }
    announce(id === 'all' ? `מתחיל לעדכן ${res.jobs.length} חבילות` : 'העדכון התחיל');
    for (const j of res.jobs) {
      watched.add(j.component);
      const c = info?.components?.find((x) => x.id === j.component);
      if (c) c.job = j;
    }
    if (info) info.busy = true;
    rerender(app);
    startPolling(app);
  } catch (e) {
    announce(e.message, true);
    info = { ...(info || { components: [] }), error: { message: e.message } };
    rerender(app);
  }
}

// progress is patched in place (no re-render) so screen readers and focus are not disturbed
function patchProgress(app, jobs) {
  const body = app.panel.body;
  for (const [id, job] of Object.entries(jobs)) {
    const li = body.querySelector(`.upd-item[data-id="${id}"]`);
    if (!li || !ACTIVE.includes(job.state)) continue;
    const box = li.querySelector('[data-role="progress"]');
    box.hidden = false;
    const bar = box.querySelector('progress');
    if (job.state === 'downloading' && job.percent != null) bar.value = Math.floor(job.percent);
    else bar.removeAttribute('value'); // indeterminate while verifying / installing
    box.querySelector('.upd-ptext').textContent = jobText(job);
    li.querySelector('[data-role="badge"]').textContent = jobText(job);
  }
}

function startPolling(app) {
  for (const c of info?.components || []) if (c.job && ACTIVE.includes(c.job.state)) watched.add(c.id);
  if (pollTimer) return;
  let prev = {};
  const told = new Set(); // components whose result was already announced
  const tick = async () => {
    pollTimer = null;
    if (!isOpen(app)) return;
    let st;
    try {
      st = await call('/api/updates/status');
    } catch {
      pollTimer = setTimeout(tick, 3000);
      return;
    }
    patchProgress(app, st.jobs);
    for (const [id, job] of Object.entries(st.jobs)) {
      const was = prev[id]?.state;
      if (was && was !== job.state && (job.state === 'done' || job.state === 'error')) {
        const name = info?.components?.find((c) => c.id === id)?.name_he || id;
        announce(job.state === 'done' ? `${name}: העדכון הותקן` : `${name}: ${job.error || 'העדכון נכשל'}`, job.state === 'error');
        told.add(id);
      }
    }
    prev = st.jobs;
    if (st.busy) {
      pollTimer = setTimeout(tick, 1000);
    } else {
      await refresh(app);
      // jobs that finished between two polls: one summary
      const fresh = [...watched].filter((id) => !told.has(id) && ['done', 'error'].includes(st.jobs[id]?.state))
        .map((id) => [id, st.jobs[id]]);
      const doneNow = [...watched].filter((id) => st.jobs[id]?.state === 'done');
      watched.clear();
      const ok = fresh.filter(([, j]) => j.state === 'done').length;
      const bad = fresh.length - ok;
      if (fresh.length) {
        announce(bad ? `העדכון הסתיים: ${ok} הותקנו, ${bad} נכשלו` : 'העדכון הסתיים בהצלחה', bad > 0);
      }
      if (doneNow.length) bus.emit('data:updated', doneNow);
      // tiles already drawn stay in the map / browser cache until the page is reloaded
      if (doneNow.includes('basemap') || doneNow.includes('terrain')) {
        toast('המפה עודכנה. רעננו את הדף (F5) כדי לראות אותה במלואה.', { ms: 8000 });
      }
    }
  };
  pollTimer = setTimeout(tick, 600);
}

// ------------------------------------------------------------------ entry points

export async function openUpdates(app) {
  hideNotice();
  app.panel.show(loadingView(KEY, 'בודק עדכונים…'));
  try {
    info = await checkUpdates(false);
    store.set('updates:lastCheck', Date.now());
  } catch (e) {
    info = { components: [], error: { message: e.message } };
  }
  if (app.panel.current?.key !== KEY) return; // the user moved on meanwhile
  app.panel.update(view(app));
  app.panel.body.querySelector('h2')?.focus({ preventScroll: true });
  if (info.busy) startPolling(app);
}

function hideNotice() {
  $('#upd-notice')?.remove();
}

function showNotice(app, data) {
  if ($('#upd-notice')) return;
  const n = data.updates_available;
  const el = document.createElement('div');
  el.id = 'upd-notice';
  el.className = 'upd-notice';
  el.setAttribute('role', 'status');
  el.innerHTML = `<span>${svg('info', { size: 18 })}${n === 1 ? 'יש עדכון נתונים חדש למפה' : `יש ${n} עדכוני נתונים חדשים למפה`}</span>
    <button type="button" class="btn btn-secondary btn-sm" data-act="open">לפרטים</button>
    <button type="button" class="icon-btn" data-act="dismiss" aria-label="סגירת ההודעה">${svg('x', { size: 18 })}</button>`;
  el.querySelector('[data-act="open"]').addEventListener('click', () => openUpdates(app));
  el.querySelector('[data-act="dismiss"]').addEventListener('click', () => {
    store.set('updates:dismissed', data.release || '');
    hideNotice();
  });
  document.body.appendChild(el);
}

/** Weekly automatic check (setting "updates:auto", on by default) – a quiet notice when something is new. */
export function initUpdates(app) {
  appRef = app;
  bus.on('panel:close', () => {
    clearTimeout(pollTimer);
    pollTimer = null;
  });
  if (!store.get('updates:auto', true)) return;
  const last = store.get('updates:lastCheck', 0);
  if (Date.now() - last < WEEK) return;
  // after the map has settled, so the check never competes with the first tiles
  setTimeout(async () => {
    try {
      const data = await checkUpdates(false);
      if (data.online) store.set('updates:lastCheck', Date.now());
      if (data.updates_available > 0 && store.get('updates:dismissed', null) !== (data.release || '')) showNotice(appRef, data);
    } catch {
      /* offline / server busy – try again next time */
    }
  }, 8000);
}
