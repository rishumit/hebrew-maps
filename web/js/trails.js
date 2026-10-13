// Marked-trail card ("שביל ישראל", "שביל מסומן כחול" …): same structure and
// accessibility conventions as the POI card (h2 title, labelled article, buttons with names).

import { svg } from './icons.js';
import { announce, esc, fmtDistance } from './util.js';
import { head, wireHead } from './views.js';

const MARK_LABEL = { red: 'אדום', blue: 'כחול', green: 'ירוק', black: 'שחור', orange: 'כתום', purple: 'סגול', yellow: 'צהוב' };

function firstPoint(trail) {
  const c = trail.geometry?.coordinates?.[0]?.[0];
  return c ? { lon: c[0], lat: c[1] } : trail.location;
}

export function trailView(app, trail) {
  const mark = MARK_LABEL[trail.mark];
  const start = firstPoint(trail);
  const length = trail.length_m != null ? fmtDistance(trail.length_m) : null;
  return {
    key: 'trail:' + trail.id, label: `פרטי שביל: ${trail.name}`,
    render(body) {
      body.innerHTML = `${head(app, trail.name, { id: 'panel-title' })}
        <article aria-labelledby="panel-title">
          <div class="card-head">
            ${trail.name_en && trail.name_en !== trail.name ? `<div class="name-en" lang="en">${esc(trail.name_en)}</div>` : ''}
            <span class="cat-badge"><span class="dot" style="background:${esc(trail.color)}" aria-hidden="true">${svg(trail.icon || 'hiker', { size: 14 })}</span>${esc(trail.route_he)}</span>
          </div>
          <div class="actions" role="group" aria-label="פעולות">
            <button type="button" class="action primary" data-act="to-start"><span class="a-circle">${svg('directions')}</span>ניווט לתחילת השביל</button>
            <button type="button" class="action" data-act="fit"><span class="a-circle">${svg('locate')}</span>הצגת כל השביל</button>
            <button type="button" class="action" data-act="share"><span class="a-circle">${svg('share')}</span>שיתוף</button>
          </div>
          ${trail.description_he ? `<div class="desc"><p style="margin:0">${esc(trail.description_he)}</p></div>` : ''}
          <ul class="info-rows">
            ${length ? `<li class="info-row">${svg('road')}<div class="ir-body">אורך: ${esc(length)}</div></li>` : ''}
            ${mark ? `<li class="info-row"><span class="dot" style="display:inline-block;width:20px;height:6px;border-radius:3px;background:${esc(trail.color)}" aria-hidden="true"></span>
              <div class="ir-body">סימון: ${esc(mark)}${trail.osmc_symbol ? ` <span class="r-sub" dir="ltr">(${esc(trail.osmc_symbol)})</span>` : ''}</div></li>` : ''}
            ${trail.network_he ? `<li class="info-row">${svg('signpost')}<div class="ir-body">${esc(trail.network_he)}</div></li>` : ''}
            ${trail.from || trail.to ? `<li class="info-row">${svg('start')}<div class="ir-body">${trail.from ? `מ${esc(trail.from)}` : ''}${trail.from && trail.to ? ' ' : ''}${trail.to ? `עד ${esc(trail.to)}` : ''}</div></li>` : ''}
            ${trail.roundtrip ? `<li class="info-row">${svg('history')}<div class="ir-body">מסלול מעגלי</div></li>` : ''}
            ${!trail.continuous ? `<li class="info-row">${svg('alert')}<div class="ir-body">השביל ממופה ב-${esc(String(trail.parts))} קטעים נפרדים ב-OpenStreetMap, ייתכנו חוסרים במפה</div></li>` : ''}
            ${trail.operator ? `<li class="info-row">${svg('building')}<div class="ir-body">מתחזק: ${esc(trail.operator)}</div></li>` : ''}
            ${trail.website ? `<li class="info-row">${svg('globe')}<div class="ir-body"><a href="${esc(trail.website)}" target="_blank" rel="noopener nofollow" dir="ltr">${esc(trail.website.replace(/^https?:\/\/(www\.)?/, '').replace(/\/$/, ''))}</a><span class="sr-only"> (נפתח בלשונית חדשה)</span></div></li>` : ''}
          </ul>
          <p class="r-sub" style="padding:0 16px">לפני יציאה לשטח כדאי לבדוק את מצב השביל ואת הסימון בפועל. המידע מגיע מ-OpenStreetMap ואינו מחליף מפת סימון שבילים רשמית.</p>
          <div class="card-foot">מקור: OpenStreetMap
            ${trail.metadata?.osm_url ? ` · <a href="${esc(trail.metadata.osm_url)}" target="_blank" rel="noopener">הצעת תיקון<span class="sr-only"> ב-OpenStreetMap (נפתח בלשונית חדשה)</span></a>` : ''}</div>
        </article>`;
      wireHead(body, app);
      body.querySelector('[data-act="to-start"]')?.addEventListener('click', () =>
        app.directions.open({ to: { ...start, name: `תחילת ${trail.name}` } }));
      body.querySelector('[data-act="fit"]')?.addEventListener('click', () => app.map.fitBounds(trail.bbox, 16));
      body.querySelector('[data-act="share"]')?.addEventListener('click', () => app.share(trail));
      announce(`${trail.name}${length ? `, אורך ${length}` : ''}${mark ? `, סימון ${mark}` : ''}`);
    },
  };
}
