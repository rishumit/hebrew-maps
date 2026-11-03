// "הגדרות ותיקיית נתונים": where the map data is kept (maps, tiles, aerial photos, the user's saved items).
// The folder is read when the program starts (hmaps/core/userconfig.py), so a change takes effect
// after closing and opening the program. The existing data can be copied to the new folder first.

import { svg } from './icons.js';
import { announce, esc } from './util.js';
import { head, loadingView, wireHead } from './views.js';

const KEY = 'settings';
let info = null;
let msg = null; // { kind: 'ok'|'error'|'info', text }
let timer = null;

async function call(url, body) {
  let r;
  try {
    r = await fetch(url, body === undefined ? { cache: 'no-store' } : {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'X-HMaps-User': '1' }, body: JSON.stringify(body),
    });
  } catch {
    throw new Error('אין חיבור לשרת המפות. ודאו שהתוכנה פועלת ונסו שוב.');
  }
  let data = null;
  try { data = await r.json(); } catch { /* empty */ }
  if (!r.ok) throw new Error(data?.error?.message || 'הפעולה נכשלה. נסו שוב.');
  return data;
}

function size(n) {
  if (n == null) return '';
  if (n >= 1e9) return `${(n / 1e9).toLocaleString('he-IL', { maximumFractionDigits: 1 })} GB`;
  if (n >= 1e6) return `${Math.round(n / 1e6).toLocaleString('he-IL')} MB`;
  return `${Math.max(1, Math.round(n / 1e3)).toLocaleString('he-IL')} KB`;
}

const ltr = (s) => `<bdi dir="ltr">${esc(s)}</bdi>`;

function render(body, app) {
  const i = info || {};
  const job = i.copy_job;
  const copying = job?.state === 'copying';
  const pct = copying && job.total ? Math.floor((job.done / job.total) * 100) : null;
  body.innerHTML = `${head(app, 'הגדרות ותיקיית נתונים')}
    <section class="p-section" aria-labelledby="set-dd-h">
      <h3 id="set-dd-h">תיקיית הנתונים</h3>
      <p>כאן נשמרים המפה, נקודות העניין, נתוני הניווט, התחבורה הציבורית, תצלומי האוויר שהורדו, והמועדפים שלכם.
        אפשר להעביר אותה לכונן אחר שיש בו יותר מקום (למשל <bdi dir="ltr">D:</bdi>).</p>
      <dl class="upd-meta set-meta">
        <div><dt>בשימוש עכשיו</dt><dd>${ltr(i.current || '')}</dd></div>
        ${i.size != null ? `<div><dt>גודל</dt><dd>${ltr(size(i.size))}</dd></div>` : ''}
        ${i.free != null ? `<div><dt>מקום פנוי בכונן</dt><dd>${ltr(size(i.free))}</dd></div>` : ''}
        <div><dt>ברירת המחדל</dt><dd>${ltr(i.default || '')}</dd></div>
      </dl>
      ${i.pending ? `<div class="alert alert-warn" role="status">${svg('info')}<div>נבחרה תיקייה חדשה: ${ltr(i.configured)}.
        היא תופעל אחרי סגירה ופתיחה מחדש של התוכנה.</div></div>` : ''}
      ${i.env_override ? `<div class="alert alert-warn" role="status">${svg('info')}<div>התיקייה נקבעת כרגע במשתנה הסביבה
        <code>HMAPS_DATA</code>, ולכן הבחירה כאן לא תשפיע עד שיוסר.</div></div>` : ''}
      <form class="set-form" data-role="form">
        <label for="set-path">תיקייה חדשה</label>
        <div class="set-row">
          <input id="set-path" type="text" dir="ltr" spellcheck="false" autocomplete="off" placeholder="D:\\HebrewMaps\\data"
            value="${esc(i.configured && i.pending ? i.configured : '')}" ${copying ? 'disabled' : ''}>
          ${i.can_pick ? `<button type="button" class="btn btn-secondary btn-sm" data-act="pick" ${copying ? 'disabled' : ''}>${svg('folder', { size: 16 })}עיון…</button>` : ''}
        </div>
        <label class="check"><input type="checkbox" data-role="copy" checked ${copying ? 'disabled' : ''}>
          <span>להעתיק את הנתונים הקיימים לתיקייה החדשה (מומלץ – אחרת יהיה צריך להוריד אותם שוב)</span></label>
        <div class="upd-actions">
          <button type="submit" class="btn btn-primary btn-sm" ${copying ? 'disabled' : ''}>שמירת התיקייה</button>
          ${i.configured ? `<button type="button" class="btn btn-secondary btn-sm" data-act="reset" ${copying ? 'disabled' : ''}>חזרה לברירת המחדל</button>` : ''}
        </div>
      </form>
      <div class="upd-progress" data-role="progress" ${copying ? '' : 'hidden'}>
        <progress max="100" ${pct != null ? `value="${pct}"` : ''} aria-labelledby="set-dd-h" aria-describedby="set-p"></progress>
        <span class="upd-ptext" id="set-p">${copying ? `מעתיק את הנתונים… ${pct ?? 0}%` : ''}</span>
      </div>
      <p class="set-msg ${msg ? `set-${msg.kind}` : ''}" role="status" aria-live="polite">${msg ? esc(msg.text) : ''}</p>
      <p class="r-sub">הקבצים בתיקייה הישנה אינם נמחקים. אחרי שבדקתם שהכול עובד מהתיקייה החדשה אפשר למחוק אותם ידנית.</p>
    </section>`;
  wireHead(body, app);
  const form = body.querySelector('[data-role="form"]');
  const input = body.querySelector('#set-path');
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const path = input.value.trim();
    if (!path) {
      msg = { kind: 'error', text: 'הקלידו נתיב של תיקייה או לחצו "עיון…".' };
      return rerender(app, '#set-path');
    }
    await apply(app, { path, copy: body.querySelector('[data-role="copy"]').checked });
  });
  body.querySelector('[data-act="pick"]')?.addEventListener('click', async () => {
    msg = { kind: 'info', text: 'נפתח חלון לבחירת תיקייה (ייתכן שהוא מאחורי חלון המפה)…' };
    rerender(app);
    try {
      const res = await call('/api/settings/pick-folder', {});
      msg = null;
      rerender(app);
      if (res.path) app.panel.body.querySelector('#set-path').value = res.path;
      app.panel.body.querySelector('#set-path')?.focus();
    } catch (err) {
      msg = { kind: 'error', text: err.message };
      rerender(app);
    }
  });
  body.querySelector('[data-act="reset"]')?.addEventListener('click', () => apply(app, { reset: true }));
}

async function apply(app, body) {
  try {
    const res = await call('/api/settings/data-dir', body);
    info = res;
    if (res.copying) {
      msg = { kind: 'info', text: 'מעתיק את הנתונים לתיקייה החדשה… אפשר להמשיך להשתמש במפה בינתיים.' };
      poll(app);
    } else {
      msg = res.restart ? { kind: 'ok', text: 'נשמר. סגרו ופתחו מחדש את התוכנה כדי לעבור לתיקייה החדשה.' }
        : { kind: 'ok', text: 'נשמר.' };
    }
  } catch (e) {
    msg = { kind: 'error', text: e.message };
  }
  announce(msg.text, msg.kind === 'error');
  rerender(app);
}

function poll(app) {
  clearTimeout(timer);
  const tick = async () => {
    try {
      info = await call('/api/settings/data-dir');
    } catch {
      timer = setTimeout(tick, 2000);
      return;
    }
    const job = info.copy_job;
    if (job?.state === 'copying') {
      const box = app.panel.current?.key === KEY && app.panel.body.querySelector('[data-role="progress"]');
      if (box) {
        const pct = job.total ? Math.floor((job.done / job.total) * 100) : 0;
        box.hidden = false;
        box.querySelector('progress').value = pct;
        box.querySelector('.upd-ptext').textContent = `מעתיק את הנתונים… ${pct}%`;
      }
      timer = setTimeout(tick, 1000);
      return;
    }
    msg = job?.state === 'error' ? { kind: 'error', text: `ההעתקה נכשלה: ${job.error}` }
      : { kind: 'ok', text: 'הנתונים הועתקו. סגרו ופתחו מחדש את התוכנה כדי לעבור לתיקייה החדשה.' };
    announce(msg.text, job?.state === 'error');
    rerender(app);
  };
  timer = setTimeout(tick, 800);
}

function view(app, focus = true) {
  return { key: KEY, label: 'הגדרות', focus, render: (body) => render(body, app) };
}

function rerender(app, focusSel) {
  if (app.panel.current?.key !== KEY) return;
  app.panel.update(view(app, false));
  if (focusSel) app.panel.body.querySelector(focusSel)?.focus();
}

export async function openSettings(app) {
  msg = null;
  app.panel.show(loadingView(KEY, 'טוען הגדרות…'));
  try {
    info = await call('/api/settings/data-dir');
  } catch (e) {
    info = null;
    msg = { kind: 'error', text: e.message };
  }
  if (app.panel.current?.key !== KEY) return;
  app.panel.update(view(app));
  app.panel.body.querySelector('h2')?.focus({ preventScroll: true });
  if (info?.copy_job?.state === 'copying') poll(app);
}
