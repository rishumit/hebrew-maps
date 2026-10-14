// Prayer times (זמני תפילות) of synagogues – source: Nedarim Plus' national project (hmaps/etl/zmanim.py).
// minyanimHtml / wireMinyanim: the section of the synagogue card; nearMinyanimView: "מניינים קרובים".

import { api } from './api.js';
import { svg } from './icons.js';
import { announce, esc, fmtDistance } from './util.js';

const LETTERS = 'אבגדהוש';
const TABS = [
  { id: 'week', label: 'א׳–ה׳', days: 'אבגדה', aria: 'ראשון עד חמישי' },
  { id: 'fri', label: 'שישי', days: 'ו', aria: 'יום שישי' },
  { id: 'sat', label: 'שבת', days: 'ש', aria: 'שבת' },
];
const GROUPS = [['שחרית', 'שחרית'], ['מנחה', 'מנחה'], ['ערבית', 'ערבית'], ['שיעור', 'שיעורים']];
const DAY_HE = { א: 'א׳', ב: 'ב׳', ג: 'ג׳', ד: 'ד׳', ה: 'ה׳', ו: 'ו׳', ש: 'שבת' };
const CREDIT = 'מקור: נדרים פלוס – פרוייקט זמני תפילות הארצי';
export const SYNAGOGUE_SUBS = ['synagogue', 'yeshiva'];

/** Day letter (א..ש) and "HH:MM" now in Israel, whatever the device time zone. */
export function israelNow(date = new Date()) {
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en-US', {
    timeZone: 'Asia/Jerusalem', weekday: 'short', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
  }).formatToParts(date).map((p) => [p.type, p.value]));
  const wd = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].indexOf(parts.weekday);
  return { day: LETTERS[wd < 0 ? 0 : wd], time: `${parts.hour === '24' ? '00' : parts.hour}:${parts.minute}` };
}

const tabOf = (day) => (day === 'ו' ? 'fri' : day === 'ש' ? 'sat' : 'week');

function fmtDate(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso || '');
  return m ? `${Number(m[3])}.${Number(m[2])}.${m[1]}` : (iso || '');
}

function daysLabel(days, tab) {
  const inTab = [...tab.days].filter((d) => days.includes(d));
  if (inTab.length === tab.days.length) return '';
  return inTab.map((d) => DAY_HE[d]).join(' ');
}

function listHtml(items, tab, now) {
  const rows = items.filter((m) => [...tab.days].some((d) => m.days.includes(d)));
  if (!rows.length) return `<p class="mn-empty">אין זמני תפילות ל${esc(tab.aria)}.</p>`;
  let next = -1;
  if (tab.days.includes(now.day)) {
    next = rows.findIndex((m) => m.prayer !== 'שיעור' && m.days.includes(now.day) && m.time > now.time);
  }
  const known = new Set(GROUPS.map((g) => g[0]));
  const groups = [...GROUPS, ...[...new Set(rows.map((m) => m.prayer).filter((p) => !known.has(p)))].map((p) => [p, p])];
  return groups.map(([key, title]) => {
    const g = rows.map((m, i) => ({ m, i })).filter(({ m }) => m.prayer === key);
    if (!g.length) return '';
    return `<h4 class="mn-group">${esc(title)}</h4><ul class="mn-list" aria-label="${esc(title)}">${g.map(({ m, i }) => {
      const dl = daysLabel(m.days, tab);
      const extra = [m.hall, m.note, dl ? `רק ב${dl}` : ''].filter(Boolean);
      return `<li class="mn-item${i === next ? ' next' : ''}"${i === next ? ' aria-current="time"' : ''}>
        <span class="mn-time" dir="ltr">${esc(m.time)}</span>
        <span class="mn-body">${i === next ? '<span class="mn-badge">המניין הבא</span>' : ''}${extra.map((x) => `<span class="mn-extra">${esc(x)}</span>`).join('')}</span>
      </li>`;
    }).join('')}</ul>`;
  }).join('');
}

/** "זמני תפילות" section of a synagogue card ('' when the POI has no data and is no synagogue). */
export function minyanimHtml(poi) {
  const mn = poi.minyanim;
  const isSyn = SYNAGOGUE_SUBS.includes(poi.sub_category);
  if (!mn && !isSyn) return '';
  const nearBtn = `<button type="button" class="btn btn-secondary btn-sm mn-near" data-act="minyan-near">${svg('clock', { size: 16 })}מניינים קרובים</button>`;
  if (!mn) return `<section class="minyanim" aria-label="זמני תפילות"><div class="mn-head"><h3 class="subtitle">זמני תפילות</h3>${nearBtn}</div>
    <p class="mn-empty">אין במאגר זמני תפילות לבית כנסת זה.</p></section>`;
  const now = israelNow();
  const cur = tabOf(now.day);
  const sun = mn.sun ? `<p class="mn-sun">${svg('clock', { size: 14 })} היום: זריחה <span dir="ltr">${esc(mn.sun.sunrise)}</span> · שקיעה <span dir="ltr">${esc(mn.sun.sunset)}</span>
    <span class="mn-approx" title="${esc(mn.sun_note || '')}">(בקירוב)</span></p>` : '';
  return `<section class="minyanim" aria-labelledby="mn-title">
    <div class="mn-head"><h3 class="subtitle" id="mn-title">זמני תפילות</h3>${nearBtn}</div>
    ${mn.items?.length ? `<div class="mn-tabs" role="tablist" aria-label="ימים">${TABS.map((t) => `
      <button type="button" role="tab" class="filter-btn" id="mn-tab-${t.id}" data-tab="${t.id}" aria-controls="mn-panel"
        aria-selected="${t.id === cur}" tabindex="${t.id === cur ? 0 : -1}" aria-label="${esc(t.aria)}">${esc(t.label)}</button>`).join('')}</div>
    <div class="mn-panel" id="mn-panel" role="tabpanel" aria-labelledby="mn-tab-${cur}" tabindex="0">${listHtml(mn.items, TABS.find((t) => t.id === cur), now)}</div>`
      : '<p class="mn-empty">אין כרגע זמני תפילות במאגר. לפרטים פנו לגבאי.</p>'}
    ${mn.address && !poi.address?.full ? `<p class="mn-sun">${svg('pin', { size: 14 })} ${esc(mn.address)}</p>` : ''}
    ${sun}
    ${mn.phone ? `<p class="mn-phone">${svg('phone', { size: 14 })} לשמיעת זמני התפילות בטלפון: <a href="tel:${esc(mn.phone.split(' ')[0].replace(/[^\d+]/g, ''))}" dir="ltr">${esc(mn.phone.split(' ')[0])}</a> ${esc(mn.phone.split(' ').slice(1).join(' '))}</p>` : ''}
    <p class="mn-credit"><a href="${esc(mn.source_url)}" target="_blank" rel="noopener">${esc(CREDIT)}<span class="sr-only"> (נפתח בלשונית חדשה)</span></a>${mn.updated ? ` · עודכן ב־${esc(fmtDate(mn.updated))}` : ''}</p>
  </section>`;
}

export function wireMinyanim(body, app, poi) {
  body.querySelector('[data-act="minyan-near"]')?.addEventListener('click', () =>
    app.panel.show(nearMinyanimView(app, poi.location, { from: poi.name })));
  const mn = poi.minyanim;
  const tabs = [...body.querySelectorAll('.mn-tabs [role="tab"]')];
  if (!mn || !tabs.length) return;
  const panel = body.querySelector('#mn-panel');
  const select = (btn, focus = false) => {
    tabs.forEach((b) => {
      const on = b === btn;
      b.setAttribute('aria-selected', String(on));
      b.tabIndex = on ? 0 : -1;
    });
    const tab = TABS.find((t) => t.id === btn.dataset.tab);
    panel.setAttribute('aria-labelledby', btn.id);
    panel.innerHTML = listHtml(mn.items, tab, israelNow());
    if (focus) btn.focus();
  };
  tabs.forEach((b, i) => {
    b.addEventListener('click', () => select(b));
    b.addEventListener('keydown', (e) => {
      // RTL: the left arrow moves to the next tab
      const step = { ArrowLeft: 1, ArrowRight: -1, Home: -99, End: 99 }[e.key];
      if (step === undefined) return;
      e.preventDefault();
      const j = Math.max(0, Math.min(tabs.length - 1, step === -99 ? 0 : step === 99 ? tabs.length - 1 : (i + step + tabs.length) % tabs.length));
      select(tabs[j], true);
    });
  });
}

// ------------------------------------------------------------------- nearby minyanim
const PRAYER_FILTERS = [['', 'הכל'], ['שחרית', 'שחרית'], ['מנחה', 'מנחה'], ['ערבית', 'ערבית']];

export function nearMinyanimView(app, center, { from = '', radius = 1500 } = {}) {
  let prayer = '';
  let data = null;
  let error = null;
  const key = `minyan-near:${center.lat.toFixed(4)},${center.lon.toFixed(4)}`;
  const view = {
    key, label: 'מניינים קרובים',
    render(body) {
      const head = `<div class="p-head">${app.panel.canGoBack() ? `<button type="button" class="icon-btn" data-act="back" aria-label="חזרה">${svg('back')}</button>` : ''}
        <h2 id="panel-title">מניינים קרובים${from ? ` <span class="p-sub">ליד ${esc(from)}</span>` : ''}</h2>
        <button type="button" class="icon-btn" data-act="close" aria-label="סגירת החלונית">${svg('x')}</button></div>`;
      const filters = `<div class="filters" role="group" aria-label="סוג תפילה">${PRAYER_FILTERS.map(([v, l]) =>
        `<button type="button" class="filter-btn" data-prayer="${esc(v)}" aria-pressed="${v === prayer}">${esc(l)}</button>`).join('')}</div>`;
      let list;
      if (error) list = `<div class="alert" role="alert">${svg('alert')}<div>${esc(error)}</div></div>`;
      else if (!data) list = '<div class="skeleton" aria-busy="true" aria-label="טוען מניינים"><div class="sk-line w80"></div><div class="sk-line w60"></div><div class="sk-line w80"></div></div>';
      else if (!data.results.length) list = `<p class="p-empty">לא נמצאו מניינים ב-24 השעות הקרובות ברדיוס ${esc(fmtDistance(radius))}.</p>`;
      else {
        list = `<ul class="results mn-near-list" aria-label="מניינים קרובים">${data.results.map((r, i) => `<li class="result"><button type="button" data-i="${i}">
          <span class="mn-time big" dir="ltr">${esc(r.time)}</span>
          <span class="r-body"><span class="r-name">${esc(r.prayer)} · ${esc(r.name)}</span>
            <span class="r-sub" style="display:block">${r.tomorrow ? '<span class="tag">מחר</span> ' : ''}${esc(fmtDistance(r.distance_m))}${r.address ? ` · ${esc(r.address)}` : ''}</span>
            ${r.hall || r.note ? `<span class="r-sub" style="display:block">${esc([r.hall, r.note].filter(Boolean).join(' · '))}</span>` : ''}
          </span></button></li>`).join('')}</ul>`;
      }
      body.innerHTML = `${head}${filters}<div id="mn-near-body">${list}</div>
        <div class="card-foot"><a href="https://www.matara.pro/nedarimplus/zmanim/" target="_blank" rel="noopener">${esc(CREDIT)}<span class="sr-only"> (נפתח בלשונית חדשה)</span></a></div>`;
      body.querySelector('[data-act="back"]')?.addEventListener('click', () => app.panel.back());
      body.querySelector('[data-act="close"]')?.addEventListener('click', () => app.closePanel());
      body.querySelectorAll('[data-prayer]').forEach((b) => b.addEventListener('click', () => {
        prayer = b.dataset.prayer;
        load();
      }));
      body.querySelectorAll('.mn-near-list [data-i]').forEach((b) => {
        const r = data.results[Number(b.dataset.i)];
        const loc = { lat: r.lat, lon: r.lon };
        b.addEventListener('mouseenter', () => app.map.setSelection(loc));
        b.addEventListener('focus', () => app.map.setSelection(loc));
        b.addEventListener('click', () => {
          if (r.poi_id) app.openItem({ id: r.poi_id, name: r.name, location: loc });
          else app.map.flyTo(loc, 17);
        });
      });
      if (!data && !error && !view._loading) load();
    },
  };
  async function load() {
    view._loading = true;
    data = null;
    error = null;
    app.panel.update(view);
    try {
      data = await api.minyanimNear(center.lat, center.lon, { radius, prayer });
      const seen = new Set();
      app.map.setResults(data.results.filter((r) => r.poi_id && !seen.has(r.mosad_id) && seen.add(r.mosad_id))
        .map((r) => ({ id: r.poi_id, name: `${r.time} ${r.name}`, location: { lat: r.lat, lon: r.lon } })));
      announce(data.results.length ? `נמצאו ${data.results.length} מניינים קרובים` : 'לא נמצאו מניינים קרובים');
    } catch (e) {
      error = e.message;
    }
    view._loading = false;
    app.panel.update(view);
  }
  return view;
}
