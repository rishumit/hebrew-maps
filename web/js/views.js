// Panel views: search results, POI / place / street / address cards, lists.

import { galleryHtml, michlolHtml, textsHtml, wireGallery, wireTexts } from './gallery.js';
import { svg } from './icons.js';
import { operatorBadge } from './logos.js';
import { minyanimHtml, wireMinyanim } from './minyanim.js';
import { poiTransitHtml, wirePoiTransit } from './transit.js';
import { announce, copyText, esc, fmtDistance, fmtNumber, html, raw, render } from './util.js';

const DAY_NAMES = ['ראשון', 'שני', 'שלישי', 'רביעי', 'חמישי', 'שישי', 'שבת'];

function head(app, title, { sub = '', id = 'panel-title' } = {}) {
  const back = app.panel.canGoBack()
    ? `<button type="button" class="icon-btn" data-act="back" aria-label="חזרה">${svg('back')}</button>` : '';
  return `<div class="p-head">${back}<h2 id="${id}">${esc(title)}${sub ? ` <span class="p-sub">${esc(sub)}</span>` : ''}</h2>
    <button type="button" class="icon-btn" data-act="close" aria-label="סגירת החלונית">${svg('x')}</button></div>`;
}

function wireHead(body, app) {
  body.querySelector('[data-act="back"]')?.addEventListener('click', () => app.panel.back());
  body.querySelector('[data-act="close"]')?.addEventListener('click', () => app.closePanel());
}

function iconCircle(item, cls = 'r-icon') {
  if (item.type === 'poi' || item.type === 'category' || item.type === 'trail') {
    return `<span class="${cls}" style="background:${esc(item.color || '#4B5563')}" aria-hidden="true">${svg(item.icon || 'pin', { size: 18 })}</span>`;
  }
  return `<span class="${cls} neutral" aria-hidden="true">${svg(item.icon || 'pin', { size: 18 })}</span>`;
}

function statusLine(item) {
  const parts = [];
  if (item.open_now === true) parts.push('<span class="open">פתוח עכשיו</span>');
  else if (item.open_now === false) parts.push('<span class="closed">סגור עכשיו</span>');
  if (item.kosher) parts.push('<span class="kosher">כשר</span>');
  if (item.distance != null) parts.push(`<span>${esc(fmtDistance(item.distance))}</span>`);
  return parts.join('<span aria-hidden="true">·</span>');
}

export function loadingView(key, label = 'טוען…') {
  return {
    key, label, focus: false,
    render(body) {
      body.innerHTML = `<div class="skeleton" aria-busy="true" aria-label="${esc(label)}">
        <div class="sk-line sk-block"></div><div class="sk-line w60"></div><div class="sk-line w80"></div><div class="sk-line w40"></div>
        <div class="sk-line w80"></div><div class="sk-line w60"></div></div>`;
    },
  };
}

export function errorView(app, key, message, retry) {
  return {
    key, label: 'שגיאה',
    render(body) {
      body.innerHTML = `${head(app, 'משהו השתבש')}<div class="alert" role="alert">${svg('alert')}<div>${esc(message)}</div></div>
        ${retry ? '<div class="p-section"><button type="button" class="btn btn-secondary" data-act="retry">ניסיון נוסף</button></div>' : ''}`;
      wireHead(body, app);
      body.querySelector('[data-act="retry"]')?.addEventListener('click', retry);
    },
  };
}

// ------------------------------------------------------------------- results
export function resultsView(app, query, data, filters) {
  const it = data.interpretation || {};
  const results = data.results || [];
  return {
    key: 'results:' + query, label: `תוצאות חיפוש עבור ${query}`,
    render(body) {
      const tags = [];
      if (it.corrected) tags.push(`<span class="tag info">${svg('check', { size: 14 })}מציג תוצאות עבור: ${esc(app.displayCorrected(it.corrected))}</span>`);
      if (it.converted) tags.push(`<span class="tag info">${svg('keyboard', { size: 14 })}תוקנה שפת המקלדת: ${esc(it.converted)}</span>`);
      if (it.near) tags.push(`<span class="tag">${svg('pin', { size: 14 })}${it.type === 'category' ? 'סביב' : 'ליד'}: ${esc(it.near)}</span>`);
      if (it.location_not_found) tags.push(`<span class="tag">המקום "${esc(it.location_not_found)}" לא נמצא – מוצגות תוצאות באזור המפה</span>`);
      const showFilters = it.type === 'category' || results.some((r) => r.type === 'poi');
      const list = results.map((r, i) => `
        <li class="result">
          <button type="button" data-index="${i}" aria-label="${esc(r.name)}${r.secondary ? ', ' + esc(r.secondary) : ''}${r.distance != null ? ', ' + esc(fmtDistance(r.distance)) : ''}">
            ${iconCircle(r)}
            <span class="r-body">
              <span class="r-name">${esc(r.name)}</span>
              ${r.secondary ? `<span class="r-sub" style="display:block">${esc(r.secondary)}</span>` : ''}
              <span class="r-meta">${statusLine(r)}</span>
            </span>
            ${r.thumbnail ? `<img class="r-thumb" src="${esc(r.thumbnail)}" alt="" loading="lazy" referrerpolicy="no-referrer">` : ''}
          </button>
        </li>`).join('');
      body.innerHTML = `${head(app, `תוצאות עבור "${query}"`, { sub: results.length ? `(${results.length})` : '' })}
        ${tags.length ? `<div class="interp">${tags.join('')}</div>` : ''}
        ${showFilters ? `<div class="filters" role="group" aria-label="סינון תוצאות">
          <button type="button" class="filter-btn" data-filter="openNow" aria-pressed="${!!filters.openNow}">${svg('clock', { size: 16 })}פתוח עכשיו</button>
          <button type="button" class="filter-btn" data-filter="kosher" aria-pressed="${!!filters.kosher}">${svg('check', { size: 16 })}כשר</button>
        </div>` : ''}
        ${results.length ? `<ul class="results" aria-label="תוצאות">${list}</ul>`
          : `<div class="p-empty">${svg('search', { size: 40 })}<p>לא נמצאו תוצאות עבור "${esc(query)}".</p>
             <p>נסו לבדוק את האיות, לכתוב שם של עיר או רחוב, או לחפש סוג מקום כמו "בית מרקחת".</p></div>`}`;
      wireHead(body, app);
      body.querySelectorAll('.result button').forEach((b) => {
        const r = results[Number(b.dataset.index)];
        b.addEventListener('click', () => app.openItem(r));
        b.addEventListener('mouseenter', () => app.map.setSelection(r.location));
        b.addEventListener('focus', () => app.map.setSelection(r.location));
      });
      body.querySelectorAll('[data-filter]').forEach((b) => b.addEventListener('click', () => {
        const f = { ...filters, [b.dataset.filter]: b.getAttribute('aria-pressed') !== 'true' };
        app.search(query, f, { replace: true });
      }));
      announce(results.length ? `נמצאו ${results.length} תוצאות עבור ${query}` : `לא נמצאו תוצאות עבור ${query}`);
    },
  };
}

// ------------------------------------------------------------------- POI card
function actionsBar(item, app, extra = '') {
  const loc = item.location;
  return `<div class="actions" role="group" aria-label="פעולות">
    <button type="button" class="action primary" data-act="route"><span class="a-circle">${svg('directions')}</span>מסלול</button>
    <button type="button" class="action" data-act="from"><span class="a-circle">${svg('start')}</span>מסלול מכאן</button>
    ${extra}
    <button type="button" class="action" data-act="share"><span class="a-circle">${svg('share')}</span>שיתוף</button>
    <button type="button" class="action" data-act="center"><span class="a-circle">${svg('locate')}</span>מרכוז במפה</button>
  </div>`;
}

function wireActions(body, item, app) {
  const name = item.name;
  body.querySelector('[data-act="route"]')?.addEventListener('click', () => app.directions.open({ to: { ...item.location, name, id: item.id } }));
  body.querySelector('[data-act="from"]')?.addEventListener('click', () => app.directions.open({ from: { ...item.location, name, id: item.id } }));
  body.querySelector('[data-act="share"]')?.addEventListener('click', () => app.share(item));
  body.querySelector('[data-act="center"]')?.addEventListener('click', () => app.map.flyTo(item.location, 17));
  body.querySelectorAll('[data-copy]').forEach((b) => b.addEventListener('click', () => copyText(b.dataset.copy, b.dataset.copyLabel || 'הועתק')));
}

function hoursRow(h) {
  if (!h) return '';
  if (!h.rows) {
    return `<li class="info-row">${svg('clock')}<div class="ir-body"><div>שעות פתיחה</div><div dir="ltr" style="text-align:right">${esc(h.raw)}</div></div></li>`;
  }
  const cls = h.open_now ? 'open' : 'closed';
  const rows = h.rows.map((r, i) => `<tr class="${i === h.today ? 'today' : ''}"><th scope="row">${esc(r.day)}</th><td>${esc(r.text)}</td></tr>`).join('');
  return `<li class="info-row">${svg('clock')}<div class="ir-body">
    <button type="button" class="hours-toggle" aria-expanded="false" aria-controls="hours-table">
      <span class="${cls}">${esc(h.status || '')}</span>${svg('chevron', { size: 18 })}</button>
    <table id="hours-table" class="hours-table" hidden><caption class="sr-only">שעות פתיחה לפי ימים</caption><tbody>${rows}</tbody></table>
  </div></li>`;
}

// trails passing by / trip sites around (site cards) – also used by the trail card
export function nearbyTrailsHtml(trails, title = 'שבילים מסומנים במקום') {
  if (!trails?.length) return '';
  return `<h3 class="subtitle">${esc(title)}</h3><ul class="results nearby" aria-label="${esc(title)}">${trails.map((t, i) =>
    `<li class="result"><button type="button" data-ntrail="${i}">${iconCircle({ ...t, type: 'trail' })}<span class="r-body">
      <span class="r-name">${esc(t.name)}</span>
      <span class="r-sub" style="display:block">${t.length_m ? esc(fmtDistance(t.length_m)) : ''}${t.distance > 30 ? ` · במרחק ${esc(fmtDistance(t.distance))}` : ''}</span>
    </span></button></li>`).join('')}</ul>`;
}

export function nearbySitesHtml(sites, title = 'אתרים בסביבה') {
  if (!sites?.length) return '';
  return `<h3 class="subtitle">${esc(title)}</h3><ul class="results nearby" aria-label="${esc(title)}">${sites.map((r, i) =>
    `<li class="result"><button type="button" data-nsite="${i}">${iconCircle(r)}<span class="r-body">
      <span class="r-name">${esc(r.name)}</span>
      <span class="r-sub" style="display:block">${esc(r.secondary || '')}${r.at_m != null ? ` · ק"מ ${esc((r.at_m / 1000).toFixed(1))}` : (r.distance != null ? ` · ${esc(fmtDistance(r.distance))}` : '')}</span>
    </span>${r.thumbnail ? `<img class="r-thumb" src="${esc(r.thumbnail)}" alt="" loading="lazy" referrerpolicy="no-referrer">` : ''}</button></li>`).join('')}</ul>`;
}

export function wireNearby(body, app, trails, sites) {
  body.querySelectorAll('[data-ntrail]').forEach((b) => b.addEventListener('click', () => app.openItem(trails[Number(b.dataset.ntrail)])));
  body.querySelectorAll('[data-nsite]').forEach((b) => {
    const r = sites[Number(b.dataset.nsite)];
    b.addEventListener('click', () => app.openItem(r));
    b.addEventListener('mouseenter', () => app.map.setSelection(r.location));
  });
  body.querySelectorAll('.nearby .r-thumb').forEach((img) => img.addEventListener('error', () => img.remove(), { once: true }));
}

export function poiView(app, poi) {
  const addr = poi.address?.full;
  const c = poi.contact || {};
  const md = poi.metadata || {};
  const photos = poi.media?.gallery?.length ? poi.media.gallery
    : (poi.media?.images || []).map((src) => ({ src, full: src, source_he: 'Wikimedia Commons' }));
  const loc = poi.location;
  const site = poi.website || c.website || poi.official_website;
  const coords = `${loc.lat.toFixed(6)}, ${loc.lon.toFixed(6)}`;
  return {
    key: 'poi:' + poi.id, label: `פרטים: ${poi.name}`,
    render(body) {
      body.innerHTML = `${head(app, poi.name, { id: 'panel-title' })}
        <article aria-labelledby="panel-title">
          ${galleryHtml(photos, poi.name)}
          <div class="card-head">
            ${poi.name_en && poi.name_en !== poi.name ? `<div class="name-en" lang="en">${esc(poi.name_en)}</div>` : ''}
            <div class="card-badges"><span class="cat-badge"><span class="dot" style="background:${esc(poi.color)}" aria-hidden="true">${svg(poi.icon, { size: 14 })}</span>${esc(poi.sub_category_he)}</span>
              ${operatorBadge(poi.operator_brand, { alt: poi.operator_brand ? `מופעל על ידי ${poi.operator_brand.name}` : '' })}</div>
            ${poi.opening_hours?.status ? `<div class="card-status ${poi.opening_hours.open_now ? 'open' : 'closed'}">${esc(poi.opening_hours.status)}</div>` : ''}
          </div>
          ${actionsBar(poi, app, (c.phone ? `<a class="action" href="${esc(c.phone_href)}" aria-label="התקשרות אל ${esc(poi.name)}: ${esc(c.phone)}"><span class="a-circle">${svg('phone')}</span>התקשרות</a>` : '')
            + (poi.nearby_sites !== undefined ? `<button type="button" class="action" data-act="view3d"><span class="a-circle a-3d" aria-hidden="true">3D</span>תלת-ממד</button>` : ''))}
          ${poiTransitHtml(poi)}
          ${md.tags?.length ? `<div class="tags" aria-label="מאפיינים" style="padding-top:12px">${md.tags.map((t) => `<span class="tag">${esc(t)}</span>`).join('')}</div>` : ''}
          ${textsHtml(poi.texts, poi.description_he ? { text: poi.description_he, url: md.wikipedia_url } : null)}
          ${poi.nearby_sites !== undefined ? michlolHtml(poi.name_he || poi.name) : ''}
          <ul class="info-rows">
            ${addr ? `<li class="info-row">${svg('pin')}<div class="ir-body"><address style="font-style:normal">${esc(addr)}</address></div>
              <button type="button" class="ir-btn" data-copy="${esc(addr)}" data-copy-label="הכתובת הועתקה" aria-label="העתקת הכתובת">${svg('copy', { size: 18 })}</button></li>` : ''}
            ${hoursRow(poi.opening_hours)}
            ${c.phone ? `<li class="info-row">${svg('phone')}<div class="ir-body"><a href="${esc(c.phone_href)}" dir="ltr">${esc(c.phone)}</a></div></li>` : ''}
            ${site ? `<li class="info-row">${svg('globe')}<div class="ir-body"><a href="${esc(site)}" target="_blank" rel="noopener nofollow" dir="ltr">${esc(site.replace(/^https?:\/\/(www\.)?/, '').replace(/\/$/, ''))}</a><span class="sr-only"> (נפתח בלשונית חדשה)</span></div></li>` : ''}
            ${c.email ? `<li class="info-row">${svg('mail')}<div class="ir-body"><a href="mailto:${esc(c.email)}" dir="ltr">${esc(c.email)}</a></div></li>` : ''}
            ${md.wheelchair_he ? `<li class="info-row">${svg('wheelchair')}<div class="ir-body">${esc(md.wheelchair_he)}</div></li>` : ''}
            ${md.operator ? `<li class="info-row">${svg('building')}<div class="ir-body">מפעיל: ${esc(md.operator)}</div></li>` : ''}
            ${poi.nature_site?.inpa_he && !md.operator ? `<li class="info-row">${svg('leaf')}<div class="ir-body">${esc(poi.nature_site.inpa_he)}</div></li>` : ''}
            ${poi.nature_site?.fee_he ? `<li class="info-row">${svg('cash')}<div class="ir-body">${esc(poi.nature_site.fee_he)}</div></li>` : ''}
            ${poi.length_m ? `<li class="info-row">${svg('road')}<div class="ir-body">אורך: ${esc(fmtDistance(poi.length_m))}</div></li>` : ''}
            ${md.elevation ? `<li class="info-row">${svg('mountain')}<div class="ir-body">גובה: ${esc(md.elevation)} מ׳</div></li>` : ''}
            ${(poi.facts || []).map((f) => `<li class="info-row">${svg(f.icon || 'info')}<div class="ir-body">${esc(f.label)}: ${esc(f.value)}</div></li>`).join('')}
            <li class="info-row">${svg('locate')}<div class="ir-body" dir="ltr" style="text-align:right">${esc(coords)}</div>
              <button type="button" class="ir-btn" data-copy="${esc(coords)}" data-copy-label="הקואורדינטות הועתקו" aria-label="העתקת הקואורדינטות">${svg('copy', { size: 18 })}</button></li>
          </ul>
          ${minyanimHtml(poi)}
          ${nearbyTrailsHtml(poi.nearby_trails)}
          ${nearbySitesHtml(poi.nearby_sites)}
          ${poi.commons_category_url ? `<p class="r-sub more-photos"><a href="${esc(poi.commons_category_url)}" target="_blank" rel="noopener">${svg('external', { size: 14 })} עוד תמונות בוויקישיתוף<span class="sr-only"> (נפתח בלשונית חדשה)</span></a></p>` : ''}
          <div class="card-foot">מקור: OpenStreetMap${poi.texts?.length ? ' · ויקיפדיה' : ''}${photos.length ? ' · Wikimedia Commons' : ''}${md.last_updated ? ` · עודכן ${esc(md.last_updated)}` : ''}
            ${md.osm_url ? ` · <a href="${esc(md.osm_url)}" target="_blank" rel="noopener">הצעת תיקון<span class="sr-only"> ב-OpenStreetMap (נפתח בלשונית חדשה)</span></a>` : ''}</div>
        </article>`;
      wireHead(body, app);
      wireActions(body, poi, app);
      wireGallery(body, photos, poi.name);
      body.querySelector('[data-act="view3d"]')?.addEventListener('click', () =>
        app.view3D(poi.bbox ? { bbox: poi.bbox } : { point: poi.location }));
      wireTexts(body);
      wireNearby(body, app, poi.nearby_trails, poi.nearby_sites);
      wireMinyanim(body, app, poi);
      wirePoiTransit(body, app, poi);
      const tg = body.querySelector('.hours-toggle');
      tg?.addEventListener('click', () => {
        const open = tg.getAttribute('aria-expanded') === 'true';
        tg.setAttribute('aria-expanded', String(!open));
        body.querySelector('#hours-table').hidden = open;
      });
    },
  };
}

// ------------------------------------------------------------------- place
export function placeView(app, place) {
  const quick = [['restaurant', 'מסעדות'], ['cafe', 'בתי קפה'], ['synagogue', 'בתי כנסת'], ['supermarket', 'סופרמרקטים'],
    ['pharmacy', 'בתי מרקחת'], ['attraction', 'אטרקציות'], ['park', 'פארקים'], ['hotel', 'מלונות']];
  return {
    key: 'place:' + place.id, label: `פרטים: ${place.name}`,
    render(body) {
      body.innerHTML = `${head(app, place.name)}
        <div class="card-head">
          ${place.name_en ? `<div class="name-en" lang="en">${esc(place.name_en)}</div>` : ''}
          <span class="cat-badge"><span class="dot" style="background:#4B5563" aria-hidden="true">${svg('pin', { size: 14 })}</span>${esc(place.kind_he)}</span>
        </div>
        ${actionsBar(place, app)}
        <ul class="info-rows">
          ${place.population ? `<li class="info-row">${svg('people')}<div class="ir-body">אוכלוסייה: ${esc(fmtNumber(place.population))}</div></li>` : ''}
          ${place.parent ? `<li class="info-row">${svg('building')}<div class="ir-body">${esc(place.parent)}</div></li>` : ''}
          ${place.district ? `<li class="info-row">${svg('landmark')}<div class="ir-body">נפה: ${esc(place.district)}</div></li>` : ''}
          ${place.settlement_code ? `<li class="info-row">${svg('info')}<div class="ir-body">סמל יישוב (למ״ס): ${esc(place.settlement_code)}</div></li>` : ''}
        </ul>
        <h3 class="subtitle">מה יש ב${esc(place.name)}?</h3>
        <div class="mini-chips">${quick.map(([id, label]) => `<button type="button" class="chip" data-sub="${id}">${esc(label)}</button>`).join('')}</div>`;
      wireHead(body, app);
      wireActions(body, place, app);
      body.querySelectorAll('[data-sub]').forEach((b) => b.addEventListener('click', () =>
        app.search(`${b.textContent} ב${place.name}`, {})));
    },
  };
}

// ------------------------------------------------------------------- street
export function streetView(app, street) {
  const nums = street.house_numbers || [];
  return {
    key: 'street:' + street.id, label: `פרטים: ${street.name}`,
    render(body) {
      body.innerHTML = `${head(app, street.name, { sub: street.city ? `· ${street.city}` : '' })}
        <div class="card-head"><span class="cat-badge"><span class="dot" style="background:#4B5563" aria-hidden="true">${svg('road', { size: 14 })}</span>רחוב</span></div>
        ${actionsBar(street, app)}
        <ul class="info-rows">
          ${street.city ? `<li class="info-row">${svg('building')}<div class="ir-body">${esc(street.city)}</div></li>` : ''}
          ${street.length_m ? `<li class="info-row">${svg('road')}<div class="ir-body">אורך: ${esc(fmtDistance(street.length_m))}</div></li>` : ''}
        </ul>
        ${nums.length ? `<h3 class="subtitle">מספרי בתים במאגר</h3><div class="mini-chips">${nums.slice(0, 80).map((n) =>
          `<button type="button" class="chip" data-num="${esc(n)}" aria-label="${esc(street.name)} ${esc(n)}">${esc(n)}</button>`).join('')}</div>` : ''}`;
      wireHead(body, app);
      wireActions(body, street, app);
      body.querySelectorAll('[data-num]').forEach((b) => b.addEventListener('click', () =>
        app.search(`${street.name} ${b.dataset.num}${street.city ? ', ' + street.city : ''}`, {})));
    },
  };
}

// ------------------------------------------------------------------- address / point
export function pointView(app, item, nearbyItems = null) {
  const loc = item.location;
  const coords = `${loc.lat.toFixed(6)}, ${loc.lon.toFixed(6)}`;
  return {
    key: 'point:' + item.id, label: `פרטים: ${item.name}`,
    render(body) {
      body.innerHTML = `${head(app, item.name)}
        <div class="card-head">
          ${item.secondary ? `<div class="r-sub">${esc(item.secondary)}</div>` : ''}
          ${item.accuracy === 'interpolated' || item.accuracy === 'street'
            ? `<div class="card-status" style="color:var(--warning)">${svg('alert', { size: 16 })} המיקום משוער</div>` : ''}
        </div>
        ${actionsBar(item, app)}
        <ul class="info-rows">
          <li class="info-row">${svg('locate')}<div class="ir-body" dir="ltr" style="text-align:right">${esc(coords)}</div>
            <button type="button" class="ir-btn" data-copy="${esc(coords)}" data-copy-label="הקואורדינטות הועתקו" aria-label="העתקת הקואורדינטות">${svg('copy', { size: 18 })}</button></li>
        </ul>
        <h3 class="subtitle">בסביבה</h3>
        <div id="nearby-list">${nearbyItems == null ? '<div class="skeleton"><div class="sk-line w80"></div><div class="sk-line w60"></div></div>'
          : nearbyItems.length ? `<ul class="results">${nearbyItems.map((r, i) => `<li class="result"><button type="button" data-index="${i}">
              ${iconCircle(r)}<span class="r-body"><span class="r-name">${esc(r.name)}</span><span class="r-sub" style="display:block">${esc(r.secondary || '')}</span>
              <span class="r-meta">${statusLine(r)}</span></span></button></li>`).join('')}</ul>`
          : '<p class="p-empty">לא נמצאו מקומות בקרבת הנקודה.</p>'}</div>`;
      wireHead(body, app);
      wireActions(body, item, app);
      body.querySelectorAll('#nearby-list .result button').forEach((b) =>
        b.addEventListener('click', () => app.openItem(nearbyItems[Number(b.dataset.index)])));
    },
  };
}

// ------------------------------------------------------------------- accessible list of visible places
export function visibleListView(app, items, trails = []) {
  return {
    key: 'visible', label: 'רשימת המקומות המוצגים במפה',
    render(body) {
      body.innerHTML = `${head(app, 'מקומות המוצגים במפה', { sub: `(${items.length})` })}
        <p class="r-sub" style="padding:12px 16px 0;margin:0">רשימה זו מציגה את נקודות העניין שבתחום המפה הנוכחי, ממוינות לפי חשיבות.</p>
        ${items.length ? `<ul class="results">${items.map((r, i) => `<li class="result"><button type="button" data-index="${i}">
          ${iconCircle({ ...r, type: 'poi', color: app.catColor[r.cat] })}<span class="r-body"><span class="r-name">${esc(r.name || app.subName(r.sub))}</span>
          <span class="r-sub" style="display:block">${esc(app.subName(r.sub))}</span></span></button></li>`).join('')}</ul>`
          : '<div class="p-empty">אין נקודות עניין בתחום המפה. נסו להגדיל את המפה או להזיז אותה.</div>'}
        ${trails.length ? `<h3 class="subtitle">שבילים מסומנים בתחום המפה</h3><ul class="results" aria-label="שבילים מסומנים">${trails.map((r, i) =>
          `<li class="result"><button type="button" data-trail="${i}">${iconCircle({ icon: 'hiker' })}<span class="r-body"><span class="r-name">${esc(r.name)}</span></span></button></li>`).join('')}</ul>` : ''}`;
      wireHead(body, app);
      body.querySelectorAll('.result button[data-index]').forEach((b) => b.addEventListener('click', () => app.openItem(items[Number(b.dataset.index)])));
      body.querySelectorAll('.result button[data-trail]').forEach((b) => b.addEventListener('click', () => app.openItem(trails[Number(b.dataset.trail)])));
    },
  };
}

export { head, wireHead, DAY_NAMES, iconCircle, statusLine, raw, html, render };
