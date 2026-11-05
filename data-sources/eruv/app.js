/* גבולות עירוב — תוסף לאוצריא.
   נתונים: window.ERUV_DATA (data.js), נבנה ע"י eruv-build/build_data.py מאתר מוקד העירוב, באישורם.
   מבנה: שלוש רמות ניווט — כל הארץ ← עיר ← מפה (מציג עם הגדלה, סיבוב וייצוא). */
(function () {
  'use strict';
  const D = window.ERUV_DATA;
  // אחראי נוסף שנמסר על ידי מפתח התוסף (לא מהמוקד הארצי): ועד העירובים השכונתיים בבני ברק
  // קישורים מהאתר של העירוב עצמו, לפי דיווח משתמש (#10 — פסגת זאב מרכז). מוצגים רק כשאוצריא מחוברת.
  const AREA_LINKS = { 'ירושלים|פסגת זאב מרכז': [['אתר העירוב', 'https://eruvpzm.yisroelk.link/'], ['מפות העירוב (Google Drive)', 'https://drive.google.com/drive/folders/1BAn0sp5qWmEUx2STEwHPhApw-qT0RhjQ']] };
  if (!D.areas.some(a => a.phones && a.phones.includes('050-4154005'))) D.areas.push({ city: 'בני ברק', n: '', area: 'ועד העירובים השכונתיים (מסר מפתח התוסף)', names: ['רפאל סטפנסקי (אחראי בפ"כ)'], phones: ['050-4154005'], emails: [['ועד העירובים השכונתיים', 'eruv.bbc@gmail.com']], maps: [] });
  const $ = (s, r = document) => r.querySelector(s);
  const hasOtz = typeof window.Otzaria !== 'undefined';
  const call = async (method, params) => {
    if (!hasOtz) return { success: false };
    try { return await Otzaria.call(method, params || {}); } catch (e) { return { success: false, error: e }; }
  };

  // ── אינדקסים ────────────────────────────────────────────────
  const mapById = new Map(D.maps.map(m => [m.id, m]));
  const byCity = new Map(D.cities.map(c => [c, { maps: [], areas: [] }]));
  D.maps.forEach(m => byCity.get(m.city).maps.push(m));
  D.areas.forEach(a => byCity.get(a.city).areas.push(a));
  const areasOfMap = id => D.areas.filter(a => a.maps.includes(id));
  // ── 1.9.0 (ו׳/ז׳): קווי עירוב בקירוב, משורטטים מתוך מפות המוקד (gvulot-eruv-build/eruv_lines.py) ──
  const LINES = window.ERUV_LINES || {};
  const LINE_LAYER = 'קווי עירוב (בקירוב — לפי מפת המוקד)';
  const lineResp = id => { const ar = areasOfMap(id); return ar.length ? ar.map(a => (a.names.join(', ') || 'אחראי') + (a.phones[0] ? ' ' + a.phones[0] : '')).join(' · ') : (LINES[id].resp || ''); };
  const lineCenter = L => { const c = L.coords; return [c.reduce((s, p) => s + p[0], 0) / c.length, c.reduce((s, p) => s + p[1], 0) / c.length]; };
  function insideMsg(lat, lon) {
    const MC = window.MapCore; if (!MC || !MC.inPoly) return '';
    let near = null;
    for (const [id, L] of Object.entries(LINES)) {
      if (!L.closed) continue;
      const d = MC.distToRing([lat, lon], L.coords), inn = MC.inPoly([lat, lon], L.coords);
      if (inn && d > 40) return `בתוך קו העירוב בקירוב (${L.city} · ${L.name}). לוודא במפת המוקד ומול האחראי: ${lineResp(id)}`;
      if (d <= 40) return `על גבול העירוב (כ-${Math.round(d)} מ' מהקו בקירוב, ${L.city}) — רק המפה המקורית והאחראי קובעים: ${lineResp(id)}`;
      if (d < 3000 && (!near || d < near.d)) near = { d, L };
    }
    return near ? `מחוץ לקו העירוב בקירוב של ${near.L.name} (${near.L.city}), כ-${Math.round(near.d)} מ' ממנו` : '';
  }
  function openLine(id) { const L = LINES[id]; if (!L) return; closeMap(); state.mapSel = null; state.mapCenter = lineCenter(L); state.mapZoom = 0.6; state.tab = 'map'; mapKey = ''; showTab(); saveView(); }
  const srcOf = (m, thumb) => m.src ? (thumb ? m.thumb : m.src) : m.user ? m.url : `maps/${m.id}${thumb ? '.t' : ''}.webp`;
  // חוברות ומאמרי תחום שבת (docs.js), ספרייה וחו"ל (extra.js)
  const DOCS = window.ERUV_DOCS || [];
  const X = window.ERUV_EXTRA || { library: [], abroad: [] };
  const docById = new Map(DOCS.map(d => [d.id, d]));
  const docsOfCity = c => DOCS.filter(d => d.cities.includes(c));
  const pageSrc = (d, p, thumb) => `docs/${d.id}/${String(p).padStart(3, '0')}${thumb ? '.t' : ''}.webp`;
  // מפה כללית של העיר — לשכונה שאין לה מפה משלה (למשל פרדס כץ במפת בני ברק)
  function generalMap(city) {
    const ms = (byCity.get(city) || { maps: [] }).maps.filter(m => !m.user);
    return ms.find(m => /כלל|השכונתיים|כל העיר|מפה כללית/.test(m.title)) || (ms.length === 1 ? ms[0] : null);
  }

  // ── מצב ושמירה ──────────────────────────────────────────────
  const state = { sec: 'il', view: 'home', city: null, q: '', favs: [], recent: [], docPages: {}, abroadC: '', settings: { toolbar: true }, tab: 'list', mapSel: null, offline: false, linkMode: 'open' };
  async function loadPrefs() {
    for (const k of ['favs', 'recent']) {
      const r = await call('storage.get', { key: k });
      if (r && r.success && Array.isArray(r.data)) state[k] = r.data.filter(c => byCity.has(c));
    }
    const dp = await call('storage.get', { key: 'docPages' });
    if (dp && dp.success && dp.data && typeof dp.data === 'object') state.docPages = dp.data;
    const tb = await call('storage.get', { key: 'hideToolbar' });
    state.settings.toolbar = !(tb && tb.success && tb.data === true);
    const lm = await call('storage.get', { key: 'linkMode' });
    if (lm && lm.success && (lm.data === 'copy' || lm.data === 'open')) state.linkMode = lm.data;
  }
  const savePref = k => call('storage.set', { key: k, value: state[k] });

  // ── ערכת צבעים מאוצריא ──────────────────────────────────────
  const kebab = s => s.replace(/[A-Z]/g, m => '-' + m.toLowerCase());
  function applyTheme(t) {
    if (!t) return;
    const root = document.documentElement.style;
    const cs = t.colorScheme || {};
    Object.keys(cs).forEach(k => root.setProperty('--color-' + kebab(k), cs[k]));
    if (cs.primary) root.setProperty('--color-primary-subtle', hexA(cs.primary, .12));
    const ty = t.typography || {};
    if (ty.uiFontFamily) {
      root.setProperty('--font-ui', `'${ty.uiFontFamily}', system-ui, sans-serif`);
      root.setProperty('--font-main', `'${ty.uiFontFamily}', system-ui, sans-serif`);
    }
    document.documentElement.dataset.theme = t.mode === 'dark' ? 'dark' : 'light';
  }
  function hexA(hex, a) {
    const m = /^#?([0-9a-f]{6}|[0-9a-f]{8})$/i.exec(hex || '');
    if (!m) return hex;
    const v = m[1].slice(-6); // ‎#AARRGGBB או ‎#RRGGBB
    return `rgba(${parseInt(v.slice(0, 2), 16)}, ${parseInt(v.slice(2, 4), 16)}, ${parseInt(v.slice(4, 6), 16)}, ${a})`;
  }

  // ── עזרים ───────────────────────────────────────────────────
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const norm = s => String(s || '').replace(/[֑-ׇ"'׳״`’\-–,.()]/g, ' ').replace(/יי/g, 'י').replace(/וו/g, 'ו').replace(/\s+/g, ' ').trim();
  function hl(text, q) {
    const t = esc(text);
    if (!q) return t;
    const words = q.split(' ').filter(w => w.length > 1).map(w => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
    return words.length ? t.replace(new RegExp('(' + words.join('|') + ')', 'g'), '<mark>$1</mark>') : t;
  }
  const fmtPhone = p => {
    const d = p.replace(/\D/g, '');
    if (/[a-z@()]/i.test(p) || d[0] !== '0') return p;
    if (d.length === 10) return d.slice(0, 3) + '-' + d.slice(3);
    if (d.length === 9) return d.slice(0, 2) + '-' + d.slice(2);
    return p;
  };
  let toastT;
  function toast(msg) {
    const t = $('#toast'); t.textContent = msg; t.classList.add('show');
    clearTimeout(toastT); toastT = setTimeout(() => t.classList.remove('show'), 2200);
  }
  async function copyPhone(p) {
    const v = fmtPhone(p);
    try { await navigator.clipboard.writeText(v); toast('המספר ' + v + ' הועתק'); }
    catch (e) { toast('טלפון: ' + v); }
  }
  const ICON = {
    phone: '<svg viewBox="0 0 24 24"><path d="M6.6 3.5l2.6.4 1.3 4-1.9 1.5a11 11 0 0 0 6 6l1.5-1.9 4 1.3.4 2.6c-.2 1.5-1.6 2.6-3.1 2.6A15.6 15.6 0 0 1 4 6.6C4 5.1 5.1 3.7 6.6 3.5z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg>',
    map: '<svg viewBox="0 0 24 24"><path d="M9 4L3 6.5v13L9 17l6 2.5 6-2.5v-13L15 6.5 9 4zM9 4v13M15 6.5v13" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg>',
    search: '<svg viewBox="0 0 24 24"><circle cx="10.5" cy="10.5" r="6" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M15 15l5 5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>',
    star: '<svg viewBox="0 0 24 24"><path d="M12 4l2.4 5 5.4.6-4 3.7 1.1 5.3L12 16l-4.9 2.6 1.1-5.3-4-3.7L9.6 9z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    starFill: '<svg viewBox="0 0 24 24"><path d="M12 4l2.4 5 5.4.6-4 3.7 1.1 5.3L12 16l-4.9 2.6 1.1-5.3-4-3.7L9.6 9z" fill="currentColor" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/></svg>',
    mail: '<svg viewBox="0 0 24 24"><path d="M4 6.5h16v11H4zM4.5 7l7.5 6 7.5-6" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    ext: '<svg viewBox="0 0 24 24"><path d="M14 4h6v6M20 4l-8.5 8.5M18 14v5H5V6h5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    pin: '<svg viewBox="0 0 24 24"><path d="M12 21s-6.5-6.2-6.5-11a6.5 6.5 0 0 1 13 0c0 4.8-6.5 11-6.5 11zM12 12.3a2.3 2.3 0 1 0 0-4.6 2.3 2.3 0 0 0 0 4.6z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    mapAdd: '<svg viewBox="0 0 24 24"><path d="M9 4L3 6.5v13L9 17l3 1.2M9 4l6 2.5 6-2.5v7M9 4v13M15 6.5V12M18 15v6M15 18h6" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    hospital: '<svg viewBox="0 0 24 24"><path d="M4 20V8l8-4 8 4v12M3 20h18M12 9v6M9 12h6M10 20v-3h4v3" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    line: '<svg viewBox="0 0 24 24"><path d="M3 14c2.5-4 4.5-4 6 0s3.5 4 6 0 4.5-4 6 0" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    book: '<svg viewBox="0 0 24 24"><path d="M5 4.5h5.5a2 2 0 0 1 2 2V20a1.6 1.6 0 0 0-1.6-1.6H5zM19 4.5h-5.5a1 1 0 0 0-1 1V20a1.6 1.6 0 0 1 1.6-1.6H19z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg>',
    globe: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8.5" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M3.5 12h17M12 3.5c2.6 2.4 3.8 5.2 3.8 8.5s-1.2 6.1-3.8 8.5c-2.6-2.4-3.8-5.2-3.8-8.5S9.4 5.9 12 3.5z" fill="none" stroke="currentColor" stroke-width="1.5"/></svg>',
    link: '<svg viewBox="0 0 24 24"><path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>'
  };

  // ── כרטיס אחראי ─────────────────────────────────────────────
  function areaHTML(a, q, opts = {}) {
    let mapsChips = opts.noMaps ? '' : a.maps.map(id => {
      const m = mapById.get(id);
      return `<button class="chip map" data-open-map="${id}" title="פתיחת המפה">${ICON.map}${esc(m.title)}</button>`;
    }).join('');
    const own = (byCity.get(a.city) || { maps: [] }).maps.filter(m => m.user && m.area && (m.area === a.area || norm(a.area).includes(norm(m.area))));
    if (!opts.noMaps) mapsChips += own.map(m => `<button class="chip map" data-open-map="${m.id}">${ICON.map}${esc(m.title)} (שלי)</button>`).join('');
    const gm = !opts.noMaps && !a.maps.length && !own.length && generalMap(a.city);
    if (gm) mapsChips += `<button class="chip map" data-open-map="${gm.id}" title="השכונה מסומנת במפה הכללית של העיר">${ICON.map}מפה כללית</button>`;
    const phones = a.phones.map(p => `<button class="chip" data-phone="${esc(p)}" title="העתקת המספר">${ICON.phone}${esc(fmtPhone(p))}</button>`).join('');
    const links = (AREA_LINKS[a.city + '|' + a.area] || []).map(([t, u]) => `<button class="chip ext-only" data-url="${esc(u)}" title="קישור מאתר העירוב (לפי דיווח משתמש)">${ICON.ext}${esc(t)}</button>`).join('');
    const mails = (a.emails || []).map(([t, m]) => `<button class="chip" data-url="mailto:${esc(m)}" title="${esc(m)} · הכתובת גם מועתקת">${ICON.mail}${esc(t)}</button>`).join('');
    return `<div class="area${opts.hl ? ' hl' : ''}">
      <span class="area-num">${esc(a.n || '•')}</span>
      <div class="area-body">
        <div class="area-title">${opts.withCity ? esc(a.city) + ' · ' : ''}${hl(a.area || 'כל השכונה', q)}</div>
        <div class="area-names">${a.names.length ? hl(a.names.join(' · '), q) : 'שם האחראי לא צוין'}</div>
        <div class="area-actions">${phones}${mails}${mapsChips}${links}</div>
      </div></div>`;
  }
  function mapCardHTML(m) {
    return `<button class="map-card" data-open-map="${m.id}">
      <img src="${esc(srcOf(m, true))}" alt="" loading="lazy">
      <div class="meta"><strong>${esc(m.title)}</strong>${m.user ? '<span class="own">שלי</span>' : ''}<span>${areasOfMap(m.id).length || ''}${areasOfMap(m.id).length ? ' אחראים' : ''}</span>${LINES[m.id] ? '<span class="own" title="יש קו עירוב בקירוב בלשונית המפה">〰 קו במפה</span>' : ''}</div>
    </button>`;
  }

  // ── סרגל הערים ──────────────────────────────────────────────
  function secOf(v) { return v === 'abroad' ? 'abroad' : (v === 'hosp' || v === 'hosps') ? 'hosp' : 'il'; }
  function renderRail() {
    const sec = state.q.trim() ? state.sec : secOf(state.view); state.sec = sec;
    document.querySelectorAll('.sec').forEach(b => { const on = b.dataset.sec === sec; b.classList.toggle('on', on); b.setAttribute('aria-selected', on); });
    const item = c => {
      const n = byCity.get(c);
      const on = !state.q.trim() && state.view === 'city' && state.city === c;
      return `<button class="rail-item${on ? ' on' : ''}" data-city="${esc(c)}">
        ${state.favs.includes(c) ? `<span class="star">${ICON.starFill}</span>` : ''}
        <span class="name">${esc(c)}</span>${docsOfCity(c).length ? '<span class="tag" title="יש מאמר תחום שבת">תחום</span>' : ''}<span class="badge">${n.maps.length || ''}</span></button>`;
    };
    const cs = [...new Map(X.abroad.map(a => [a.c, a.ch])).entries()].sort((a, b) => a[1].localeCompare(b[1], 'he'));
    let title, count, html;
    if (sec === 'hosp') {
      title = 'בתי חולים'; count = HOSPITALS.length;
      const hb = h => `<button class="rail-item${state.view === 'hosp' && state.hosp === h.id ? ' on' : ''}" data-hosp="${h.id}"><span class="name">${esc(h.name)}</span><span class="badge">${esc(h.city)}</span></button>`;
      html = HOSPITALS.filter(h => !h.abroad).map(hb).join('') + `<div class="rail-head"><span>חו"ל</span><span class="count">${HOSPITALS.filter(h => h.abroad).length}</span></div>` + HOSPITALS.filter(h => h.abroad).map(hb).join('');
    } else if (sec === 'abroad') {
      title = 'ארצות'; count = cs.length;
      html = cs.map(c => `<button class="rail-item${state.view === 'abroad' && state.abroadC === c[0] ? ' on' : ''}" data-abroad="${esc(c[0])}"><span class="name">${esc(c[1])}</span><span class="badge">${X.abroad.filter(a => a.c === c[0]).length}</span></button>`).join('');
    } else {
      title = 'ערים'; count = D.cities.length;
      const favs = state.favs.filter(c => byCity.has(c));
      html = (favs.length ? favs.map(item).join('') + '<div class="rail-sep"></div>' : '') + D.cities.map(item).join('');
    }
    $('#railTitle').textContent = title; $('#cityCount').textContent = count; $('#cityList').innerHTML = html;
  }
  function goSec(sec) {
    if (sec === 'abroad') goView('abroad');
    else if (sec === 'hosp') goView('hosps');
    else goHome();
  }

  // ── מסכים ───────────────────────────────────────────────────
  function render() {
    renderRail();
    const main = $('#main');
    if (state.q.trim()) { $('#crumb').textContent = 'חיפוש'; main.innerHTML = searchView(); }
    else if (state.view === 'city') { $('#crumb').textContent = state.city; main.innerHTML = cityView(state.city); }
    else if (state.view === 'docs') { $('#crumb').textContent = 'תחום שבת וחוברות'; main.innerHTML = docsView(); }
    else if (state.view === 'hosp') { const h = HOSPITALS.find(x => x.id === state.hosp); $('#crumb').textContent = h ? h.name : 'בית חולים'; main.innerHTML = hospView(h); }
    else if (state.view === 'hosps') { $('#crumb').textContent = 'בתי חולים'; main.innerHTML = hospsView(); }
    else if (state.view === 'abroad') { $('#crumb').textContent = 'עירובין בחו"ל'; main.innerHTML = abroadView(); }
    else { $('#crumb').textContent = 'כל הארץ'; main.innerHTML = homeView(); }
    main.scrollTop = 0;
    showTab();
    $('#btnDocs').classList.toggle('on', state.view === 'docs' && !state.q.trim());
    saveView();
  }
  // מצב הכרטיסייה לשחזור אחרי פינוי (מ-0.9.99; בגרסאות קודמות הקריאה פשוט נכשלת)
  let saveT;
  function saveView() {
    clearTimeout(saveT);
    saveT = setTimeout(() => call('plugin.setRestoreState', { state: { view: state.view, city: state.city, abroadC: state.abroadC, tab: state.tab } }), 400);
  }

  function homeView() {
    const card = c => {
      const n = byCity.get(c); const m = n.maps[0];
      const thumb = m ? `<div class="city-thumb" style="background-image:url('${esc(srcOf(m, true))}')"></div>`
        : `<div class="city-thumb empty">${ICON.phone}</div>`;
      const fav = state.favs.includes(c) ? `<span class="fav-pin">${ICON.starFill}</span>` : '';
      return `<button class="city-card" data-city="${esc(c)}">${fav}${thumb}
        <div class="city-meta"><strong>${esc(c)}</strong>
        <span>${n.maps.length ? n.maps.length + ' מפות' : 'אחראים בלבד'}${n.areas.length ? ' · ' + n.areas.length + ' שכונות' : ''}</span></div></button>`;
    };
    const recent = state.recent.filter(c => byCity.has(c)).slice(0, 4);
    return `<div class="wrap">
      <div class="hero"><div class="grow"><h2>גבולות העירובים השכונתיים</h2>
      <p>${D.maps.length} מפות ו־${D.areas.length} אחראים ב־${D.cities.length} ערים. בחרו עיר או חפשו שכונה, רחוב או שם רב.</p></div>
      <button class="btn tonal" data-add-map="">${ICON.mapAdd} הוספת מפה משלי</button></div>
      ${recent.length ? `<div class="section-title">נצפו לאחרונה</div><div class="city-grid">${recent.map(card).join('')}</div>` : ''}
      <div class="section-title">כל הערים</div>
      <div class="city-grid">${D.cities.map(card).join('')}</div>
      ${DOCS.length ? `<div class="section-title">תחום שבת וחוברות</div><div class="doc-grid">${DOCS.filter(d => d.kind === 'booklet').map(docCardHTML).join('')}</div>
        <div class="row-links"><button class="btn text" data-view="docs">${ICON.book} כל מאמרי התחום והספרייה</button>
        <button class="btn text" data-view="abroad">${ICON.globe} עירובין בחו"ל</button>
        <button class="btn text" data-view="hosps">${ICON.hospital} בתי חולים</button></div>` : ''}
      ${sourceCard()}</div>`;
  }

  function cityView(c) {
    const n = byCity.get(c);
    const fav = state.favs.includes(c);
    return `<div class="wrap">
      <div class="hero"><div class="grow"><h2>${esc(c)}</h2>
        <p>${n.maps.length ? n.maps.length + ' מפות עירוב' : 'אין עדיין מפה לעיר זו, אך יש אחראים מקומיים'}${n.areas.length ? ' · ' + n.areas.length + ' שכונות עם אחראי' : ''}</p></div>
        ${CITY_POS[c] ? `<button class="btn text" data-mapdev="${esc(c)}">${ICON.pin} במפה</button>` : ''}
        <button class="btn text" data-add-map="${esc(c)}">${ICON.mapAdd} הוספת מפה משלי</button>
        <button class="btn ${fav ? 'tonal' : 'text'}" data-fav="${esc(c)}">${fav ? ICON.starFill : ICON.star} ${fav ? 'במועדפים' : 'הוספה למועדפים'}</button>
      </div>
      ${n.maps.length ? `<div class="section-title">מפות</div><div class="map-grid">${n.maps.map(mapCardHTML).join('')}</div>` : ''}
      ${techumHTML(c)}
      ${n.areas.length ? `<div class="section-title">אחראים לפי שכונה</div><div class="area-list">${n.areas.map(a => areaHTML(a, '')).join('')}</div>` : ''}
      ${sourceCard()}</div>`;
  }

  function searchView() {
    const q = norm(state.q);
    const words = q.split(' ').filter(Boolean);
    const match = s => { const t = norm(s); return words.every(w => t.includes(w)); };
    const cities = D.cities.filter(match);
    const maps = D.maps.filter(m => match(m.city + ' ' + m.title));
    const areas = D.areas.filter(a => match([a.city, a.area, a.names.join(' ')].join(' ')));
    const docs = DOCS.filter(d => match(d.title + ' ' + d.cities.join(' ') + ' ' + Object.values(d.summary || {}).join(' ')));
    const books = X.library.filter(b => match(b.t));
    const abroad = X.abroad.filter(a => match([a.p, a.in, a.r, a.c, a.ch, heOf(a.p), heOf(a.r), metroOf(a)].join(' ')));
    if (!cities.length && !maps.length && !areas.length && !docs.length && !books.length && !abroad.length) {
      return `<div class="empty-state">${ICON.search}<strong>לא נמצא "${esc(state.q)}"</strong>
        אפשר לחפש שם עיר, שכונה, רחוב או שם האחראי. למקום שאינו ברשימה: מוקד העירוב, 054-8483320.</div>`;
    }
    const qh = state.q.trim();
    return `<div class="wrap">
      <div class="hero"><div class="grow"><h2>תוצאות עבור "${esc(qh)}"</h2>
      <p>${cities.length} ערים · ${maps.length} מפות · ${areas.length} אחראים${docs.length ? ' · ' + docs.length + ' חוברות' : ''}${abroad.length ? ' · ' + abroad.length + ' בחו"ל' : ''}</p></div></div>
      ${docs.length ? `<div class="section-title">תחום שבת וחוברות</div><div class="doc-grid">${docs.map(docCardHTML).join('')}</div>` : ''}
      ${cities.length ? `<div class="section-title">ערים</div><div class="city-grid">${cities.map(c => {
        const n = byCity.get(c);
        return `<button class="city-card" data-city="${esc(c)}">${n.maps[0] ? `<div class="city-thumb" style="background-image:url('${esc(srcOf(n.maps[0], true))}')"></div>` : ''}
          <div class="city-meta"><strong>${hl(c, qh)}</strong><span>${n.maps.length} מפות · ${n.areas.length} שכונות</span></div></button>`;
      }).join('')}</div>` : ''}
      ${maps.length ? `<div class="section-title">מפות</div><div class="map-grid">${maps.map(m => mapCardHTML(m).replace(`<strong>${esc(m.title)}</strong>`, `<strong>${hl(m.city + ' · ' + m.title, qh)}</strong>`)).join('')}</div>` : ''}
      ${areas.length ? `<div class="section-title">אחראים</div><div class="area-list">${areas.slice(0, 120).map(a => areaHTML(a, qh, { withCity: true })).join('')}</div>` : ''}
      ${books.length ? `<div class="section-title">ספריית העירובין</div><div class="lib">${books.map(b => `<button class="chip" data-url="${esc(b.u)}">${ICON.link}${hl(b.t, qh)}</button>`).join('')}</div>` : ''}
      ${abroad.length ? `<div class="section-title">עירובין בחו"ל</div><div class="ab-list">${abroad.slice(0, 60).map(a => `<div class="ab-item"><span dir="auto">${hl(a.p, qh)}<small> · ${esc([a.in, a.r, a.ch].filter(Boolean).join(', '))}</small></span>
        ${a.u ? `<button class="chip" data-url="${esc(a.u)}">${ICON.link}אתר העירוב</button>` : ''}</div>`).join('')}</div>` : ''}
    </div>`;
  }

  // מדף "יצירת קשר" באתר המוקד (נבדק 22.10.2026)
  const MOKED_EMAIL = 'a83320@gmail.com';
  const MOKED_CONTACT = 'http://hiruv.com/%D7%99%D7%A6%D7%99%D7%A8%D7%AA-%D7%A7%D7%A9%D7%A8/';
  function sourceCard() {
    return `<div class="source-card"><div class="grow"><b>מקור:</b> ${esc(D.source.name)}, באישורם. ${esc(D.source.note)}
      עודכן ${esc(D.built)}.</div>
      <button class="btn text" data-url="${esc(D.source.site)}">לאתר המוקד</button>
      <button class="btn text" data-url="${MOKED_CONTACT}">יצירת קשר עם המוקד</button>
      <button class="btn text" data-phone="${MOKED_EMAIL}" title="העתקת האימייל">${ICON.mail} ${MOKED_EMAIL}</button>
      <button class="btn tonal" data-phone="${esc(D.source.phone)}">${ICON.phone} מענה הלכתי ${esc(D.source.phone)}</button></div>`;
  }


  // ── תחום שבת, חוברות וספרייה ───────────────────────────────
  // חוברת עם חלקים (parts): בדף עיר נפתח רק החלק של אותה עיר
  const partOf = (d, c) => c && d.parts ? d.parts.find(p => p.cities.includes(c)) : null;
  function docCardHTML(d, c) {
    const last = state.docPages[d.id], pt = partOf(d, typeof c === 'string' ? c : null);
    return `<button class="doc-card" data-doc="${d.id}"${pt ? ` data-from="${pt.from}" data-to="${pt.to}" data-part="${esc(pt.title)}"` : ''}>
      <img src="${esc(pageSrc(d, 1, true))}" alt="" loading="lazy">
      <div class="meta"><strong>${esc(d.title)}</strong>
      <span>${pt ? esc(pt.title) + ' · עמ\' ' + pt.from + '–' + pt.to : d.pages + ' עמודים'}${last > 1 ? ' · המשך מעמוד ' + last : ''}</span></div></button>`;
  }
  const SUM_ROWS = [['near', 'מותר ללכת בלי חשש'], ['eruv', 'רק ע"י עירוב תחומין'], ['far', 'אסור גם ע"י עירוב תחומין']];
  function summaryHTML(sm, title) {
    if (!sm) return '';
    return `<div class="techum-sum">${title ? `<strong>${esc(title)}</strong>` : ''}${sm.intro ? `<p>${esc(sm.intro)}</p>` : ''}
      ${SUM_ROWS.filter(r => sm[r[0]]).map(r => `<div class="tz tz-${r[0]}"><b>${esc(r[1])}</b><span>${esc(sm[r[0]])}</span></div>`).join('')}</div>`;
  }
  function techumHTML(c) {
    const ds = docsOfCity(c);
    if (!ds.length) return `<div class="techum-empty">${ICON.book}<div><b>תחום שבת</b> — אין עדיין מאמר תחומין לעיר זו במאגר.
      לבירור התחום ועירוב תחומין: מוקד העירוב <button class="chip" data-phone="${esc(D.source.phone)}">${ICON.phone}${esc(D.source.phone)}</button></div></div>`;
    return `<div class="section-title">תחום שבת</div>
      ${ds.map(d => summaryHTML(d.summary)).join('')}
      <div class="doc-grid">${ds.map(d => docCardHTML(d, c)).join('')}</div>
      <p class="fine">מאמרי התחומין של מוקד העירוב. החישוב תלוי בשיטות שונות — לשאלה למעשה יש לפנות לרב או למוקד.</p>`;
  }
  // בתי חולים. כל טלפון ושם כאן מועתק מעמוד רשמי של בית החולים (src), ונבדק ב-22.10.2026.
  // אין להוסיף מספר שלא הופיע במקור רשמי. eruv/techum — רק מה שכתוב במקור; בלי מקור לתחום מוצגת הפנייה למוקד.
  const HOSPITALS = [
    { id: 'shaare-zedek', name: 'שערי צדק', city: 'ירושלים',
      src: 'https://www.szmc.org.il/media/bnlhwz3j/%D7%A9%D7%99%D7%A8%D7%95%D7%AA%D7%99-%D7%93%D7%AA.pdf', srcName: 'עלון המחלקה לשירותי דת, אתר שערי צדק',
      contacts: [
        { role: 'רב בית החולים', name: 'הרה"ג משה פלג', phone: '02-6666483' },
        { role: 'ראש שירותי הדת', name: 'הרב אברהם גולדשמידט', phone: '052-7680550' },
        { role: 'המחלקה לשירותי דת (אפשר להשאיר הודעה)', phone: '02-6555015', email: 'yosefsp@szmc.org.il' }],
      eruv: 'לפי עלון שירותי הדת: בית החולים בתוך מפת העירוב של ירושלים שבאישור הבד"ץ, ומחובר גם לעירוב השכונתי של בית וגן. יש עירוב חצרות.' },
    { id: 'hadassah', name: 'הדסה עין כרם', city: 'ירושלים',
      src: 'https://he.hadassah.org.il/patient-information/religious-services/', srcName: 'עמוד שירותי דת, אתר הדסה',
      contacts: [
        { role: 'רב המרכזים הרפואיים', name: 'הרב משה קליין', phone: '02-6778597', phone2: '054-5203908' },
        { role: 'עוזר הרב', name: 'הרב יעקב רוזנשטיין', phone: '050-4116058' },
        { role: 'קו מידע לשירותי דת (בכל שעה)', phone: '02-6777699' }],
      eruv: 'לפי אתר הדסה: סביב קמפוס עין כרם שלושה עירובין — של רבנות ירושלים, של בד"ץ העדה החרדית, ועירוב "מהודר" של ועד העירוב השכונתי לפי שיטת הגרי"ש אלישיב. העירובין נבדקים מדי שבוע. על הר הצופים לא נמצא פירוט בעמוד — לברר בקו המידע.' },
    { id: 'mayanei', name: 'מעיני הישועה', city: 'בני ברק',
      src: 'https://woman.mymc.co.il/the-rav-office-a-response-to-your-medical-halachic-questions/', srcName: 'עמוד לשכת הרב, אתר מעיני הישועה',
      contacts: [
        { role: 'רב המרכז הרפואי', name: 'הרה"ג יוסף ברוך הופנר', note: 'לא פורסם טלפון ישיר' },
        { role: 'מוקד המידע של בית החולים', phone: '03-5771111' }],
      eruv: 'לא נמצא באתר בית החולים פירוט על העירוב. לברר בלשכת הרב או מול האחראי על עירוב בני ברק (בדף העיר).' },
    { id: 'laniado', name: 'לניאדו', city: 'נתניה',
      src: 'https://www.laniado.org.il/en/religious-services-at-laniado-hospital/', srcName: 'עמוד שירותי דת, אתר לניאדו',
      contacts: [
        { role: 'רב', name: 'הרב מרדכי לוינשטיין', phone: '053-3104918' },
        { role: 'רב', name: 'הרב הלל דויטש', phone: '054-8421490' },
        { role: 'ראש הקשר הדתי', name: 'הרב הלל קסמן', phone: '058-7624668', email: 'rabanim@laniado.org.il' }],
      eruv: 'לפי אתר בית החולים: בית החולים בתוך עירוב קהילה קדושה צאנז.' },
    { id: 'beilinson', name: 'בילינסון (רבין)', city: 'פתח תקוה',
      src: 'https://hospitals.clalit.co.il/rabin/he/departments-and-clinics/administrative-units/Documents/13007_%D7%91%D7%9C%D7%99%D7%A0%D7%A1%D7%95%D7%9F.pdf', srcName: 'עלון דת וכשרות, אתר כללית',
      contacts: [
        { role: 'רב בית החולים', name: 'הרב ישראל בבצ\'יק', phone: '050-4193544', phone2: '03-9376043' },
        { role: 'משגיח שבת', name: 'ר\' שמואל חרמץ', phone: '054-6367703' },
        { role: 'מחלקת דת וכשרות, הנהלת כללית', phone: '03-6923275' },
        { role: 'בירורים על העירוב הפנימי', phone: '050-4142100', note: 'בערבים 22:45–23:15, ובערב שבת 13:30 עד כניסת השבת' }],
      eruv: 'לפי העלון: עירוב חצרות סביב בית החולים, באישור הרב הראשי של כללית, רבני קהילות ועירובים שכונתיים. מפת העירוב הפנימי בתוקף רק עם תעודת כשרות עדכנית מוועד העירובין השכונתיים בני ברק.',
      techum: 'לפי העלון: בית החולים בתוך תחום שבת, ואפשר להגיע אליו מבני ברק ומגבעת שמואל.' },
    { id: 'schneider', name: 'שניידר', city: 'פתח תקוה',
      src: 'https://www.schneider.org.il/?CategoryID=839&ArticleID=2457', srcName: 'עמוד שירותי הדת היהודית וכשרות, אתר שניידר',
      contacts: [
        { role: 'רב בית החולים ומשגיח הכשרות', name: 'הרב אשר לוי', phone: '03-9253971', phone2: '050-4205574' },
        { role: 'משגיח שבתות וחגים (שאלות בערב שבת וערב חג)', name: 'הרב דניאל שהרבני', phone: '054-8473654' },
        { role: 'מחלקת דת וכשרות, הנהלת כללית', phone: '03-6923275' }],
      eruv: 'לפי אתר בית החולים: עירוב חצרות מהודר סביב בית החולים, בפיקוח הרב הראשי של כללית ורבני הקהילות והעירובים השכונתיים.',
      techum: 'לפי אתר בית החולים: המרכז בתוך תחום שבת, ואפשר להגיע אליו מפתח תקוה והסביבה, מבני ברק ומגבעת שמואל.' },
    { id: 'ichilov', name: 'איכילוב', city: 'תל אביב', doc: 'derech-ab',
      src: 'https://www.tasmc.org.il/service-info/service-info/religion-services/', srcName: 'עמוד שירותי דת, אתר איכילוב',
      contacts: [{ role: 'רב בית החולים', name: 'הרב אברהם רזניקוב', note: 'לא פורסם טלפון ישיר' }],
      eruv: 'לפי אתר בית החולים: עירוב חצרות שהוקם עם בית הדין של הרב ניסים קרליץ, בנפרד מהעירוב של רבנות תל אביב.',
      techum: 'ההליכה בשבת מבני ברק לאיכילוב, ומתי צריך עירוב תחומין, מבוארות בחוברת "דרך התחומין א–ב" (פרק "בענין ההליכה מבני ברק לתל אביב ולבית החולים איכילוב").' },
    { id: 'sheba', name: 'תל השומר (שיבא)', city: 'רמת גן', doc: 'derech-ab', contacts: [],
      techum: 'דין התחום לבית החולים תל השומר מבואר בחוברת "דרך התחומין א–ב" (חלק א, "בירור דיני התחום לערי גוש דן ובתי החולים").' },
    { id: 'wolfson', name: 'וולפסון', city: 'חולון', doc: 'derech-ab', contacts: [],
      techum: 'דין התחום לעיר חולון ולבית החולים וולפסון, והתקנה ע"י עירוב תחומין במקום המשותף לשתי הערים, מבוארים בחוברת "דרך התחומין א–ב".' },
    // חו"ל — רק מעמוד רשמי של בית החולים
    { id: 'mskcc', name: 'Memorial Sloan Kettering', city: 'ניו יורק', abroad: true,
      src: 'https://www.mskcc.org/sites/default/files/node/19442/documents/jewish.pdf', srcName: 'עלון למטופלים יהודים, אתר MSK',
      contacts: [
        { role: 'בקשת ביקור רב / ביקור חולים של קהילת קהילת ישורון (שלוחה פנימית)', phone: 'ext. 5982' },
        { role: 'מצב עירוב מנהטן (הקו שמופיע בעלון)', phone: '(212) 874-6100 ext 3' }],
      eruv: 'לפי העלון: מעלית שבת בגוש המעליות M, כניסה ברחוב 67 עם דלתות מכניות פתוחה תמיד, וחדר אירוח לשבת (C-172). את מצב העירוב יש לבדוק בקו של עירוב מנהטן.' },
    { id: 'maimonides', name: 'Maimonides Medical Center', city: 'ברוקלין', abroad: true,
      src: 'https://maimo.org/?p=5550', srcName: 'עמוד הרבנות, אתר Maimonides',
      contacts: [{ role: 'מנהל הרבנות', name: 'Rabbi Benzion Leser', email: 'chaplain@maimo.org', note: 'הטלפון בעמוד מופיע בשתי צורות שונות, ולכן לא הוכנס' }],
      eruv: 'בעמוד הרשמי לא מוזכר עירוב. יש בית כנסת בבית החולים עם תפילות שבת.' },
    { id: 'sinai-baltimore', name: 'Sinai Hospital', city: 'בולטימור', abroad: true,
      src: 'https://www.lifebridgehealth.org/news/sinai-hospital-expands-resources-sabbath-observant-jewish-patients-and-families', srcName: 'הודעה באתר LifeBridge Health',
      contacts: [{ role: 'רב בית החולים', name: 'Rabbi Jeffrey Orkin', note: 'לא פורסם טלפון ישיר' }, { role: 'מוקד LifeBridge Health', phone: '410-601-9355' }],
      eruv: 'לפי ההודעה: שלוש מעליות שבת, חדר ביקור חולים עם אוכל כשר, וכניסת שבת לבניין Blaustein עם ליווי לחדר המיון לפי בקשה. בעמוד לא מפורט עירוב.' },
    { id: 'evanston', name: 'Endeavor Evanston Hospital', city: 'שיקגו (אוונסטון)', abroad: true,
      src: 'https://chicagomitzvahcampaign.org/wp-content/uploads/2024/06/155.-What-to-do-now_Map-Brochure_8-Panel_6.5.2024.pdf', srcName: 'עלון Chicago Mitzvah Campaign',
      contacts: [{ role: 'Chicago Mitzvah Campaign — ליווי רבני בבתי החולים', phone: '1-866-697-2224' }],
      eruv: 'לפי העלון: יש עירוב של בית החולים (מפה בעלון), וחדר אירוח לשבת ע"ש סולומון ודורה איינהורן, Central Street 1022. עירוב אוונסטון הכללי בבנייה (צפי 2028) ויחבר את בית החולים לווסט רוג\'רס פארק ולסקוקי.' },
    { id: 'skokie-hosp', name: 'Endeavor Skokie Hospital', city: 'שיקגו (סקוקי)', abroad: true,
      src: 'https://chicagomitzvahcampaign.org/wp-content/uploads/2024/06/155.-What-to-do-now_Map-Brochure_8-Panel_6.5.2024.pdf', srcName: 'עלון Chicago Mitzvah Campaign',
      contacts: [{ role: 'Chicago Mitzvah Campaign — ליווי רבני בבתי החולים', phone: '1-866-697-2224' }, { role: 'קו סטטוס עירוב סקוקי (cRc)', phone: '847-679-3788' }],
      eruv: 'לפי העלון: עירוב סקוקי מתיר לטלטל בין בית החולים לחדר האירוח לשבת (Gross Point Road 9631, בקומה התחתונה מכניסת החניון). לפני שבת יש לבדוק את קו הסטטוס.' }
  ];
  // ארגונים — רק פרטים מהאתר הרשמי של הארגון
  const ORGS = [
    { name: 'מוקד העירוב', what: 'מענה הלכתי בענייני עירוב, מפות ואחראים · a83320@gmail.com', phone: '054-8483320', url: 'http://hiruv.com/%D7%99%D7%A6%D7%99%D7%A8%D7%AA-%D7%A7%D7%A9%D7%A8/', src: 'דף יצירת קשר באתר מוקד העירוב' },
    { name: 'Chicago Mitzvah Campaign (שיקגו)', what: 'ליווי רבני וחדרי אירוח לשבת בבתי החולים באזור שיקגו', phone: '1-866-697-2224', url: 'https://chicagomitzvahcampaign.org/in-hospital-patient-services/', src: 'אתר הארגון' },
    { name: 'מועצת הרבנים של שיקגו (cRc) — עירובין', what: 'רשימת העירובין באזור שיקגו וקווי הסטטוס', phone: '847-677-3788', url: 'https://crcweb.org/guide_chicago.php', src: 'אתר cRc (הקו של לינקולנווד–פיטרסון פארק)' },
    { name: 'עזר מציון — סניף ראשי', what: 'בחוברת "דרך התחומין" מוזכר שהאגודה מניחה עירוב תחומין ציבורי', phone: '03-6144444', url: 'https://www.ami.org.il/', src: 'אתר עזר מציון' }
  ];
  // מקורות רשמיים נוספים: עמודי שירותי דת של בתי חולים שעוד לא פורטו כאן (קישור בלבד)
  const MORE_SOURCES = [
    ['סורוקה — מחלקת דת וכשרות', 'https://hospitals.clalit.co.il/soroka/he/patients-info/Pages/religious-service.aspx'],
    ['העמק — שירותי דת וכשרות', 'https://hospitals.clalit.co.il/emek/he/aboutus/Pages/religious_and_kosher_services.aspx'],
    ['גהה — שירותי דת וכשרות', 'https://hospitals.clalit.co.il/geha/he/patient/Pages/religion.aspx'],
    ['קפלן — שירותי דת', 'https://hospitals.clalit.co.il/kaplan/he/gen_info/Pages/rav.aspx'],
    ['לוינשטיין — דת וכשרות', 'https://hospitals.clalit.co.il/loewenstein/he/customer_info/Pages/religion_and_kashrut.aspx'],
    ['רמב"ם — שירותי דת', 'https://www.rambam.org.il/?catid=%7B767f81cc-1bcc-4f10-95b8-0e667d0bd203%7D'],
    ['הלל יפה — שירותי דת', 'https://hymc.org.il/?CategoryID=276&ArticleID=3799'],
    ['רעות — שירותי דת', 'https://www.reuth-mc.org.il/%D7%A9%D7%99%D7%A8%D7%95%D7%AA%D7%99-%D7%93%D7%AA/']
  ];
  const HOSP_EZER = 'בחוברת "דרך התחומין" מוזכר שאגודת עזר מציון מניחה עירוב תחומין ציבורי (ומבואר שם איזה מזון מניחים). לפני שבת יש לברר אם העירוב הונח ולמי הוא מועיל.';
  const telChip = p => `<button class="chip" data-phone="${esc(p)}">${ICON.phone}${esc(p)}</button>`;
  function contactHTML(c) {
    return `<div class="contact"><div class="who"><b>${esc(c.name || c.role)}</b>${c.name ? `<small>${esc(c.role)}</small>` : ''}${c.note ? `<small>${esc(c.note)}</small>` : ''}</div>
      ${c.phone ? telChip(c.phone) : ''}${c.phone2 ? telChip(c.phone2) : ''}${c.email ? `<button class="chip" data-phone="${esc(c.email)}" title="העתקה">${ICON.mail}${esc(c.email)}</button>` : ''}</div>`;
  }
  function hospView(h) {
    if (!h) return hospsView();
    const doc = h.doc && docById.get(h.doc);
    return `<div class="wrap">
      <div class="hero"><div class="grow"><h2>${esc(h.name)}</h2><p>${esc(h.city)} · מידע לשבת</p></div></div>
      <div class="section-title">רב בית החולים ושירותי הדת</div>
      ${h.contacts.length ? `<div class="contacts">${h.contacts.map(contactHTML).join('')}</div>`
        : `<div class="techum-empty">${ICON.phone}<div>עדיין לא נמצאו טלפונים ממקור רשמי של בית החולים. לבירור: מוקד העירוב ${telChip(D.source.phone)}</div></div>`}
      <div class="section-title">עירוב בתוך המתחם</div>
      <div class="techum-empty">${ICON.map}<div>${esc(h.eruv || 'בבתי החולים יש יחידת שירותי דת שאחראית על העירוב במתחם. לפני שבת יש לפנות אליה ולוודא שהעירוב תקין.')}</div></div>
      <div class="section-title">תחום שבת ועירוב תחומין</div>
      <div class="techum-empty">${ICON.book}<div>${esc(h.techum || 'לא נמצא מקור מוסמך לתחום השבת של בית חולים זה. לשאלה: מוקד העירוב או רב בית החולים.')}${doc ? '<br>' + esc(HOSP_EZER) : ''}</div></div>
      ${doc ? `<div class="doc-grid">${docCardHTML(doc)}</div>` : ''}
      ${h.src ? `<p class="fine">מקור: <button class="btn text" data-url="${esc(h.src)}">${esc(h.srcName)}</button> · נבדק בחשוון תשפ"ז. מספרים עשויים להשתנות.</p>` : ''}
      <p class="fine">התוסף אינו פוסק הלכה. לשאלה למעשה יש לפנות לרב בית החולים או למוקד העירוב.</p></div>`;
  }
  function hospsView() {
    return `<div class="wrap">
      <div class="hero"><div class="grow"><h2>בתי חולים בשבת</h2><p>רב בית החולים, שירותי הדת, העירוב במתחם ותחום שבת — רק ממקורות רשמיים.</p></div></div>
      <div class="row-links">${HOSPITALS.filter(h => !h.abroad).map(h => `<button class="btn tonal" data-hosp="${h.id}">${ICON.hospital} ${esc(h.name)} · ${esc(h.city)}</button>`).join('')}</div>
      <div class="section-title">בתי חולים בחו"ל</div>
      <div class="row-links">${HOSPITALS.filter(h => h.abroad).map(h => `<button class="btn tonal" data-hosp="${h.id}">${ICON.hospital} ${esc(h.name)} · ${esc(h.city)}</button>`).join('')}</div>
      <div class="section-title">ארגונים</div>
      <div class="contacts">${ORGS.map(o => `<div class="contact"><div class="who"><b>${esc(o.name)}</b><small>${esc(o.what)}</small><small>מקור: ${esc(o.src)}</small></div>
        ${telChip(o.phone)}<button class="chip" data-url="${esc(o.url)}">${ICON.link}אתר</button></div>`).join('')}</div>
      <div class="section-title">בתי חולים נוספים — עמודי שירותי הדת הרשמיים</div>
      <div class="lib">${MORE_SOURCES.map(x => `<button class="chip" data-url="${esc(x[1])}">${ICON.link}${esc(x[0])}</button>`).join('')}</div>
      <p class="fine">${esc(HOSP_EZER)}</p></div>`;
  }
  function docsView() {
    const tech = DOCS.filter(d => d.kind === 'techum'), books = DOCS.filter(d => d.kind === 'booklet');
    const groups = [...new Set(X.library.map(b => b.g))];
    return `<div class="wrap">
      <div class="hero"><div class="grow"><h2>תחום שבת וחוברות</h2>
      <p>מאמרי התחומין והחוברות של מוקד העירוב, בתוך התוסף ובלי אינטרנט: דפדוף, הגדלה, הדפסה ושמירה של עמוד.</p></div></div>
      <div class="section-title">חוברות</div><div class="doc-grid">${books.map(docCardHTML).join('')}</div>
      <div class="section-title">תחום שבת לפי עיר</div>
      ${tech.map(d => summaryHTML(d.summary, d.title)).join('')}
      <div class="doc-grid">${tech.map(docCardHTML).join('')}</div>
      <div class="section-title">בתי חולים</div>
      <div class="row-links">${HOSPITALS.map(h => `<button class="btn tonal" data-hosp="${h.id}">${ICON.hospital} ${esc(h.name)} · ${esc(h.city)}</button>`).join('')}</div>
      <p class="fine">${esc(HOSP_EZER)}</p>
      <div class="section-title">ספריית העירובין של המוקד</div>
      <p class="fine">${X.library.length} ספרים וחוברות להורדה מאתר מוקד העירוב (נפתחים בדפדפן).</p>
      ${groups.map(g => `<h3 class="info-h">${esc(g)}</h3><div class="lib">${X.library.filter(b => b.g === g).map(b =>
        `<button class="chip" data-url="${esc(b.u)}" title="פתיחה בדפדפן">${ICON.link}${esc(b.t)}</button>`).join('')}</div>`).join('')}
      ${sourceCard()}</div>`;
  }
  const HEP = {"Alabama": "אלבמה", "Arizona": "אריזונה", "Arkansas": "ארקנסו", "California": "קליפורניה", "Colorado": "קולורדו", "Connecticut": "קונטיקט", "Delaware": "דלאוור", "District of Columbia": "וושינגטון הבירה", "Florida": "פלורידה", "Georgia": "ג'ורג'יה", "Hawaii": "הוואי", "Illinois": "אילינוי", "Indiana": "אינדיאנה", "Iowa": "איווה", "Kansas": "קנזס", "Kentucky": "קנטקי", "Louisiana": "לואיזיאנה", "Maine": "מיין", "Maryland": "מרילנד", "Massachusetts": "מסצ'וסטס", "Michigan": "מישיגן", "Minnesota": "מינסוטה", "Missouri": "מיזורי", "Nebraska": "נברסקה", "Nevada": "נבדה", "New Hampshire": "ניו המפשייר", "New Jersey": "ניו ג'רזי", "New Mexico": "ניו מקסיקו", "New York": "ניו יורק", "North Carolina": "צפון קרוליינה", "Ohio": "אוהיו", "Oklahoma": "אוקלהומה", "Oregon": "אורגון", "Pennsylvania": "פנסילבניה", "Rhode Island": "רוד איילנד", "South Carolina": "דרום קרוליינה", "Tennessee": "טנסי", "Texas": "טקסס", "Utah": "יוטה", "Vermont": "ורמונט", "Virginia": "וירג'יניה", "Washington": "וושינגטון", "Wisconsin": "ויסקונסין", "Ontario": "אונטריו", "Quebec": "קוויבק", "Québec": "קוויבק", "British Columbia": "קולומביה הבריטית", "Manitoba": "מניטובה", "Alberta": "אלברטה", "England": "אנגליה", "London": "לונדון", "Manchester": "מנצ'סטר", "Gateshead": "גייטסהד", "Chicago": "שיקגו", "Brooklyn": "ברוקלין", "Manhattan": "מנהטן", "Queens": "קווינס", "Monsey": "מונסי", "Lakewood": "לייקווד", "Baltimore": "בולטימור", "Los Angeles": "לוס אנג'לס", "Miami": "מיאמי", "Miami Beach": "מיאמי ביץ'", "Boston": "בוסטון", "Philadelphia": "פילדלפיה", "Cleveland": "קליבלנד", "Detroit": "דטרויט", "Toronto": "טורונטו", "Montreal": "מונטריאול", "Antwerp": "אנטוורפן", "Paris": "פריז", "Zurich": "ציריך", "Vienna": "וינה", "Johannesburg": "יוהנסבורג", "Melbourne": "מלבורן", "Sydney": "סידני", "Passaic": "פסאיק", "Teaneck": "טינק", "Skokie": "סקוקי", "Lincolnwood": "לינקולנווד", "West Rogers Park": "ווסט רוג'רס פארק", "Evanston": "אוונסטון", "Rockland County": "מחוז רוקלנד", "Staten Island": "סטטן איילנד", "Far Rockaway": "פאר רוקאווי", "Five Towns": "פייב טאונס", "Atlanta": "אטלנטה", "Denver": "דנבר", "Dallas": "דאלאס", "Houston": "יוסטון", "St. Louis": "סנט לואיס", "Pittsburgh": "פיטסבורג", "Cincinnati": "סינסינטי", "Phoenix": "פיניקס", "San Diego": "סן דייגו", "San Francisco": "סן פרנסיסקו", "Seattle": "סיאטל", "Buenos Aires": "בואנוס איירס", "Mexico City": "מקסיקו סיטי", "Highland Park": "היילנד פארק", "Buffalo Grove": "באפלו גרוב", "Lakeview": "לייקוויו", "Hyde Park": "הייד פארק"};
  // שם בעברית למדינה/עיר בחו"ל: התאמה מלאה, ואחרת מילה מוכרת בתוך השם (למשל West Rogers Park (Chicago) → שיקגו)
  const heOf = s => { if (!s) return ''; if (HEP[s]) return HEP[s]; const k = Object.keys(HEP).find(k => k.length > 4 && s.includes(k)); return k ? HEP[k] : ''; };
  // פרברים שנחשבים לאזור עיר גדולה (לחיפוש "שיקגו" וכו')
  const METRO = { 'שיקגו Chicago': /Skokie|Lincolnwood|Rogers Park|Lakeview|Lakefront|Glenbrook|Glenview|Northbrook|Buffalo Grove|Highland Park|Hyde Park|South Side|Evanston|Peterson Park|Chicago/i,
    'ניו יורק New York': /Brooklyn|Manhattan|Queens|Bronx|Staten Island|Far Rockaway|Five Towns|Riverdale|Kew Gardens/i, 'לונדון London': /London|Edgware|Stanmore|Golders|Hendon|Barnet|Bushey|Borehamwood|Mill Hill|Pinner|Chigwell|Hampstead|Woodside Park/i };
  const metroOf = a => Object.keys(METRO).filter(k => METRO[k].test(a.p + ' ' + (a.in || ''))).join(' ');
  function abroadView() {
    const cs = [...new Map(X.abroad.map(a => [a.c, a.ch])).entries()].sort((a, b) => a[1].localeCompare(b[1], 'he'));
    const cur = state.abroadC || (cs.find(c => c[0] === 'United States') || cs[0] || [''])[0];
    const fq = (state.abq || '').trim().toLowerCase();
    const list = X.abroad.filter(a => a.c === cur && (!fq || [a.p, a.in, a.r, heOf(a.p), heOf(a.r), heOf(a.in), metroOf(a)].join(' ').toLowerCase().includes(fq)));
    const regions = [...new Set(list.map(a => a.r))];
    const item = a => `<div class="ab-item${a.in ? ' sub' : ''}"><span dir="auto">${heOf(a.p) ? `<b>${esc(heOf(a.p))}</b> · ` : ''}${esc(a.p)}${a.in ? `<small> · ${esc(a.in)}</small>` : ''}</span><span class="ab-acts">
      ${a.em ? `<button class="chip" data-phone="${esc(a.em)}" title="העתקת האימייל">${ICON.mail}${esc(a.em)}</button>` : ''}${a.m ? `<button class="chip" data-url="${esc(a.m)}" title="${esc(a.m)}">${ICON.map}מפה</button>` : ''}${a.ph ? `<button class="chip" data-phone="${esc(a.ph)}" title="קו סטטוס העירוב">${ICON.phone}${esc(a.ph)}</button>` : ''}${a.u ? `<button class="chip" data-url="${esc(a.u)}" title="${esc(a.u)}">${ICON.link}אתר העירוב</button>` : ''}</span></div>`;
    return `<div class="wrap">
      <div class="hero"><div class="grow"><h2>עירובין בחו"ל</h2>
      <p>${X.abroad.length} מקומות ב־${cs.length} ארצות. רשימת מקומות וקישורים לאתר העירוב המקומי — בלי מפות. הסטטוס משתנה מדי שבוע, ויש לבדוק באתר המקומי.</p></div></div>
      <div class="tabs">${cs.map(c => `<button class="tab${c[0] === cur ? ' on' : ''}" data-abroad="${esc(c[0])}">${esc(c[1])}
        <span class="badge">${X.abroad.filter(a => a.c === c[0]).length}</span></button>`).join('')}</div>
      <input id="abQ" class="ab-filter" type="search" placeholder="סינון: עיר או מדינה, בעברית או באנגלית" value="${esc(state.abq || '')}">
      ${!list.length ? '<p class="fine">לא נמצא. נסו שם אחר, או את החיפוש הכללי למעלה.</p>' : ''}
      ${regions.map(r => `${r ? `<div class="section-title">${heOf(r) ? esc(heOf(r)) + ' · ' : ''}${esc(r)}</div>` : ''}<div class="ab-list">${list.filter(a => a.r === r).map(item).join('')}</div>`).join('')}
      <p class="fine">מקור הרשימה: ויקיפדיה, "List of places with eruvin" (CC BY-SA). מדריך עולמי נוסף: eruv.org.
        <button class="btn text" data-url="https://en.wikipedia.org/wiki/List_of_places_with_eruvin">לרשימה המלאה</button>
        <button class="btn text" data-url="https://www.eruv.org/">eruv.org</button></p></div>`;
  }

  // ── ניווט ───────────────────────────────────────────────────
  function goCity(c) {
    state.view = 'city'; state.city = c; state.q = ''; $('#q').value = '';
    state.mapSel = null;
    state.recent = [c, ...state.recent.filter(x => x !== c)].slice(0, 8); savePref('recent');
    render();
  }
  function goHome() { state.view = 'home'; state.city = null; state.q = ''; $('#q').value = ''; render(); }
  function goView(v) { state.view = v; state.q = ''; $('#q').value = ''; render(); }
  function toggleFav(c) {
    state.favs = state.favs.includes(c) ? state.favs.filter(x => x !== c) : [...state.favs, c];
    savePref('favs'); render();
    toast(state.favs.includes(c) ? c + ' נוסף למועדפים' : c + ' הוסר מהמועדפים');
  }

  // ── מציג המפה: הזזה, הגדלה, סיבוב ──────────────────────────
  const V = { m: null, x: 0, y: 0, s: 1, r: 0, w: 0, h: 0, min: .05, max: 8 };
  const stage = $('#stage'), img = $('#vImg');
  function apply(anim) {
    stage.classList.toggle('anim', !!anim);
    img.style.transform = `translate(${V.x}px, ${V.y}px) scale(${V.s}) translate(${V.w / 2}px, ${V.h / 2}px) rotate(${V.r}deg) translate(${-V.w / 2}px, ${-V.h / 2}px)`;
    $('#vZoom').textContent = Math.round(V.s * 100) + '%';
  }
  function fit(anim) {
    const b = stage.getBoundingClientRect();
    const rw = V.r % 180 ? V.h : V.w, rh = V.r % 180 ? V.w : V.h;
    V.s = Math.min((b.width - 32) / rw, (b.height - 32) / rh);
    V.min = Math.min(V.s, 1) * .5;
    V.x = b.width / 2 - V.s * V.w / 2; V.y = b.height / 2 - V.s * V.h / 2;
    apply(anim);
  }
  function zoomAt(k, px, py, anim) {
    const ns = Math.max(V.min, Math.min(V.max, V.s * k)); k = ns / V.s;
    V.x = px - (px - V.x) * k; V.y = py - (py - V.y) * k; V.s = ns; apply(anim);
  }
  const center = () => { const b = stage.getBoundingClientRect(); return [b.width / 2, b.height / 2]; };

  function openMap(id, pm) {
    const m = pm || mapById.get(id); if (!m) return;
    if (!pm) V.doc = null;
    $('#vPager').hidden = !V.doc;
    V.m = m; V.r = 0; V.w = m.w; V.h = m.h;
    $('#vTitle').textContent = m.city + ' · ' + m.title;
    $('#vSub').textContent = m.sub || (m.user ? 'מפה שהוספת · להתמצאות בלבד' : 'מוקד העירוב · להתמצאות בלבד');
    const ar = pm ? [] : areasOfMap(id);
    const side = $('#vSidePanel');
    if (pm) side.innerHTML = m.side; else side.innerHTML = `<h3>אחראים במפה זו</h3>` +
      (ar.length ? ar.map(a => areaHTML(a, '', { noMaps: true })).join('') : `<p class="area-names">לא שויכו אחראים למפה זו. בדף העיר ${esc(m.city)} יש רשימה מלאה.</p>`) +
      (LINES[m.id] ? `<button class="btn" data-line="${m.id}">${ICON.line} קו העירוב בקירוב במפה</button><p class="fine">הקו שורטט מתוך מפה זו ומוצג בקירוב בלבד. הקובע: המפה הזו והאחראי.</p>` : '') +
      (!ar.length && LINES[m.id] && LINES[m.id].resp ? `<p class="area-names">לבירורים לפי המפה: ${esc(LINES[m.id].resp)}</p>` : '') +
      (m.user ? `<button class="btn text" data-del-map="${m.id}">הסרת המפה שהוספתי</button>` : `<button class="btn text" data-url="${esc(m.pageUrl)}">פתיחת הדף באתר המוקד</button>`);
    $('#viewer').classList.toggle('side-off', pm ? (V.sideOff || innerWidth < 760) : (!ar.length && !m.user && !LINES[m.id]) || innerWidth < 760);
    $('#vSide').classList.toggle('on', !$('#viewer').classList.contains('side-off'));
    $('#viewer').hidden = false;
    img.style.transform = ''; img.onload = () => { if (!V.w || m.user) { V.w = m.w = img.naturalWidth; V.h = m.h = img.naturalHeight; } fit(false); };
    img.removeAttribute('width'); img.removeAttribute('height');
    img.src = srcOf(m); if (m.w) { img.width = m.w; img.height = m.h; requestAnimationFrame(() => fit(false)); }
    const hint = $('#vHint'); hint.classList.remove('gone'); setTimeout(() => hint.classList.add('gone'), 3500);
    if (!pm || !V.keepFocus) $('#vClose').focus();
  }
  function closeMap() { $('#viewer').hidden = true; $('#expMenu').hidden = true; img.removeAttribute('src'); if (V.doc) { V.doc = null; render(); } }

  // דפי חוברת במציג: דפדוף, תמונות ממוזערות בצד, זכירת העמוד האחרון
  function openDoc(id, page, range) {
    const d = docById.get(id); if (!d) return;
    const wasOpen = V.doc && V.doc.id === id && !$('#viewer').hidden;
    if (range !== undefined) V.range = range; else if (!wasOpen) V.range = null;
    const R0 = V.range ? V.range.from : 1, R1 = V.range ? V.range.to : d.pages;
    if (wasOpen) V.sideOff = $('#viewer').classList.contains('side-off');
    else V.sideOff = d.pages < 3;
    page = Math.max(R0, Math.min(R1, page || (V.range ? R0 : state.docPages[id]) || 1));
    V.doc = d; V.page = page; V.keepFocus = wasOpen;
    const PW = 2200, w = PW, h = Math.round(PW * d.dims[page - 1][1] / d.dims[page - 1][0]);
    const side = `<h3>עמודים${V.range ? ' · ' + esc(V.range.title) : ''}</h3>
      <div class="row-links"><button class="btn" data-booklet="1">${ICON.book} חוברת רציפה</button>${V.range ? '<button class="btn text" data-doc-all="1">כל החוברת</button>' : ''}</div>
      <div class="thumbs">${Array.from({ length: R1 - R0 + 1 }, (_, i) => i + R0 - 1).map(i =>
      `<button class="thumb${i + 1 === page ? ' on' : ''}" data-doc-page="${i + 1}" title="עמוד ${i + 1}"><img src="${pageSrc(d, i + 1, true)}" alt="" loading="lazy"><span>${i + 1}</span></button>`).join('')}</div>
      <button class="btn text" data-url="${esc(d.url)}">המקור באתר מוקד העירוב</button>`;
    const cached = pageCache.get(id + ':' + page);
    openMap(null, { id: 'doc-' + id + '-' + page, city: 'עמוד ' + page, title: d.title, w, h, src: cached || BLANK, thumb: pageSrc(d, page, true),
      sub: 'מוקד העירוב · עמוד ' + page + ' מתוך ' + d.pages, side });
    $('#vTitle').textContent = d.title;
    $('#vPage').value = page; $('#vPage').max = d.pages; $('#vPages').textContent = '/ ' + d.pages;
    $('#vPrev').disabled = page <= R0; $('#vNext').disabled = page >= R1; $('#vPage').min = R0; $('#vPage').max = R1;
    const on = $('#vSidePanel .thumb.on'); if (on) on.scrollIntoView({ block: 'nearest' });
    state.docPages[id] = page; clearTimeout(V.dpT); V.dpT = setTimeout(() => savePref('docPages'), 800);
    $('#viewer').classList.toggle('loading', !cached);
    if (!cached) renderPage(d, page, PW).then(u => {
      if (u && V.doc && V.doc.id === id && V.page === page && !$('#viewer').hidden) { img.onload = null; img.src = u; $('#viewer').classList.remove('loading'); }
    }).catch(e => { $('#viewer').classList.remove('loading'); toast('לא ניתן להציג את ה-PDF: ' + ((e && e.message) || e)); });
    if (page < R1) setTimeout(() => renderPage(d, page + 1, PW).catch(() => {}), 600); // העמוד הבא מראש
  }
  // הצגת ה-PDF המקורי (pdf.js, ארוז בתוסף) — עמוד מצויר ל-canvas ונשמר כתמונה; שומרים בזיכרון עד 12 עמודים
  let pdfLib = null;
  const BLANK = 'data:image/gif;base64,R0lGODlhAQABAIAAAP///wAAACH5BAEAAAAALAAAAAABAAEAAAICRAEAOw==';
  const pdfDocs = new Map(), pageCache = new Map(), pending = new Map();
  async function getPdf(d) {
    // כמו בצופה PDF+OCR: הבנייה הרגילה (לא מודול) של pdf.js ו-worker בנתיב יחסי
    if (!pdfLib) {
      pdfLib = window.pdfjsLib; if (!pdfLib) throw new Error('pdf.js missing');
      pdfLib.GlobalWorkerOptions.workerSrc = './pdfjs/pdf.worker.min.js';
    }
    if (!pdfDocs.has(d.id)) pdfDocs.set(d.id, loadPdfData(d.id).then(data => pdfLib.getDocument({ data, isEvalSupported: false, enableXfa: false, disableFontFace: true, // #9: בלי זה גופני Word העבריים מצוירים משובשים בחוברת שיורדת (כמו בשיח רינה)
      
      standardFontDataUrl: new URL('pdfjs/standard_fonts/', document.baseURI).href }).promise));
    return pdfDocs.get(d.id);
  }
  // ה-PDF ארוז כ-base64 ב-docs/<id>.pdf.js ונטען בתג script (fetch של file:// חסום באוצריא)
  function loadPdfData(id) {
    return new Promise((res, rej) => {
      const done = () => { const b = (window.ERUV_PDF || {})[id]; if (!b) return rej(new Error('הקובץ חסר')); const bin = atob(b); const u = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) u[i] = bin.charCodeAt(i); delete window.ERUV_PDF[id]; res(u); };
      if ((window.ERUV_PDF || {})[id]) return done();
      const sc = document.createElement('script'); sc.src = 'docs/' + id + '.pdf.js'; sc.onload = done; sc.onerror = () => rej(new Error('הקובץ חסר')); document.head.appendChild(sc);
    });
  }
  function renderPage(d, p, PW) {
    const key = d.id + ':' + p;
    if (pageCache.has(key)) return Promise.resolve(pageCache.get(key));
    if (pending.has(key)) return pending.get(key);
    const job = (async () => {
      const pg = await (await getPdf(d)).getPage(p);
      const vp = pg.getViewport({ scale: PW / pg.getViewport({ scale: 1 }).width });
      const cv = document.createElement('canvas'); cv.width = Math.round(vp.width); cv.height = Math.round(vp.height);
      const g = cv.getContext('2d'); g.fillStyle = 'white'; g.fillRect(0, 0, cv.width, cv.height);
      await pg.render({ canvasContext: g, viewport: vp }).promise;
      const blob = await new Promise(r => cv.toBlob(r, 'image/jpeg', .9));
      const u = URL.createObjectURL(blob);
      pageCache.set(key, u);
      if (pageCache.size > 12) { const [k0, u0] = pageCache.entries().next().value; if (img.src !== u0) { URL.revokeObjectURL(u0); pageCache.delete(k0); } }
      return u;
    })();
    pending.set(key, job); job.finally(() => pending.delete(key));
    return job;
  }
  const turn = k => { if (V.doc) openDoc(V.doc.id, V.page + k); };
  $('#vPrev').onclick = () => turn(-1);
  $('#vNext').onclick = () => turn(1);
  $('#vPage').addEventListener('change', e => { if (V.doc) openDoc(V.doc.id, +e.target.value || 1); });
  $('#vSidePanel').addEventListener('click', e => {
    if (!V.doc) return;
    const t = e.target.closest('[data-doc-page]'); if (t) return openDoc(V.doc.id, +t.dataset.docPage);
    if (e.target.closest('[data-doc-all]')) return openDoc(V.doc.id, V.page, null);
    if (e.target.closest('[data-booklet]')) openBooklet(V.doc, V.range, V.page);
  });
  // חוברת רציפה: כל העמודים (או החלק של העיר) זה תחת זה בגלילה, ציור עצל לפי הגלילה
  function openBooklet(d, range, at) {
    const R0 = range ? range.from : 1, R1 = range ? range.to : d.pages;
    let bk = $('#booklet');
    if (!bk) { bk = document.createElement('div'); bk.id = 'booklet'; bk.className = 'booklet'; document.body.appendChild(bk); }
    bk.innerHTML = `<div class="bk-bar"><strong>${esc(d.title)}${range ? ' · ' + esc(range.title) : ''}</strong><span class="bk-pos"></span>
      <button class="btn text" data-bk-close="1" aria-label="סגירה">✕ חזרה</button></div><div class="bk-pages">${Array.from({ length: R1 - R0 + 1 }, (_, i) => {
        const p = i + R0, dm = d.dims[p - 1] || [1000, 1410];
        return `<figure class="bk-page" data-p="${p}" style="aspect-ratio:${dm[0]}/${dm[1]}"><img alt="עמוד ${p}" src="${pageSrc(d, p, true)}"><figcaption>${p}</figcaption></figure>`; }).join('')}</div>`;
    bk.hidden = false;
    const pos = bk.querySelector('.bk-pos');
    const io = new IntersectionObserver(es => es.forEach(en => {
      if (!en.isIntersecting) return; const f = en.target, p = +f.dataset.p; pos.textContent = 'עמוד ' + p + ' / ' + R1;
      if (f.dataset.done) return; f.dataset.done = 1;
      renderPage(d, p, 1400).then(u => { f.querySelector('img').src = u; }).catch(() => { delete f.dataset.done; });
    }), { root: bk.querySelector('.bk-pages'), rootMargin: '600px 0px' });
    bk.querySelectorAll('.bk-page').forEach(f => io.observe(f));
    bk.onclick = e => {
      if (e.target.closest('[data-bk-close]')) { io.disconnect(); bk.hidden = true; bk.innerHTML = ''; return; }
      const f = e.target.closest('.bk-page'); if (f) { io.disconnect(); bk.hidden = true; bk.innerHTML = ''; openDoc(d.id, +f.dataset.p); }
    };
    const t = bk.querySelector(`.bk-page[data-p="${at}"]`); if (t) requestAnimationFrame(() => t.scrollIntoView({ block: 'start' }));
  }

  stage.addEventListener('wheel', e => {
    e.preventDefault();
    const b = stage.getBoundingClientRect();
    zoomAt(Math.exp(-e.deltaY * (e.ctrlKey ? .01 : .0018)), e.clientX - b.left, e.clientY - b.top);
  }, { passive: false });
  const ptrs = new Map(); let last = null, pinch = null;
  stage.addEventListener('pointerdown', e => {
    stage.setPointerCapture(e.pointerId); ptrs.set(e.pointerId, [e.clientX, e.clientY]);
    stage.classList.add('drag'); last = [e.clientX, e.clientY];
    if (ptrs.size === 2) { const [a, b] = [...ptrs.values()]; pinch = Math.hypot(a[0] - b[0], a[1] - b[1]); }
  });
  stage.addEventListener('pointermove', e => {
    if (!ptrs.has(e.pointerId)) return;
    ptrs.set(e.pointerId, [e.clientX, e.clientY]);
    if (ptrs.size === 2 && pinch) {
      const [a, b] = [...ptrs.values()]; const d = Math.hypot(a[0] - b[0], a[1] - b[1]);
      const r = stage.getBoundingClientRect();
      zoomAt(d / pinch, (a[0] + b[0]) / 2 - r.left, (a[1] + b[1]) / 2 - r.top); pinch = d; return;
    }
    if (ptrs.size === 1 && last) { V.x += e.clientX - last[0]; V.y += e.clientY - last[1]; last = [e.clientX, e.clientY]; apply(); }
  });
  const up = e => { ptrs.delete(e.pointerId); if (ptrs.size < 2) pinch = null; if (!ptrs.size) { stage.classList.remove('drag'); last = null; } };
  stage.addEventListener('pointerup', up); stage.addEventListener('pointercancel', up);
  stage.addEventListener('dblclick', e => { const b = stage.getBoundingClientRect(); zoomAt(2.2, e.clientX - b.left, e.clientY - b.top, true); });
  new ResizeObserver(() => { if (!$('#viewer').hidden) fit(false); }).observe(stage);

  $('#vClose').onclick = closeMap;
  $('#vIn').onclick = () => zoomAt(1.4, ...center(), true);
  $('#vOut').onclick = () => zoomAt(1 / 1.4, ...center(), true);
  $('#vFit').onclick = () => fit(true);
  $('#vRot').onclick = () => { V.r = (V.r + 90) % 360; fit(true); };
  $('#vSide').onclick = () => { const v = $('#viewer'); v.classList.toggle('side-off'); $('#vSide').classList.toggle('on', !v.classList.contains('side-off')); };

  // ── ייצוא: PNG / JPG / הדפסה ─────────────────────────────────
  $('#vExp').onclick = e => {
    e.stopPropagation();
    document.querySelectorAll('#expMenu .doc-only').forEach(b => { b.hidden = !V.doc; });
    document.querySelectorAll('#expMenu .part-only').forEach(b => { b.hidden = !(V.doc && V.range); });
    $('#expMenu').hidden = !$('#expMenu').hidden;
  };
  document.addEventListener('click', () => { $('#expMenu').hidden = true; });
  $('#expMenu').addEventListener('click', e => {
    const b = e.target.closest('[data-exp]'); if (!b) return;
    $('#expMenu').hidden = true;
    const x = b.dataset.exp;
    if (x === 'print') printMap();
    else if (x === 'book-pdf' || x === 'book-zip' || x === 'part-pdf') exportBooklet(x === 'book-zip' ? 'zip' : 'pdf', x === 'part-pdf');
    else exportImage(x);
  });

  const cssVar = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  function composeCanvas() {
    const m = V.m, W = img.naturalWidth || m.w, H = img.naturalHeight || m.h;
    const band = Math.round(W * .07), pad = Math.round(band * .3);
    const cv = document.createElement('canvas'); cv.width = W; cv.height = H + band;
    const g = cv.getContext('2d');
    g.fillStyle = cssVar('--color-surface') || 'white'; g.fillRect(0, 0, cv.width, cv.height);
    g.fillStyle = cssVar('--color-surface-container-high') || 'white'; g.fillRect(0, 0, W, band);
    const font = cssVar('--font-ui') || 'sans-serif';
    g.direction = 'rtl'; g.textAlign = 'right'; g.textBaseline = 'middle';
    g.fillStyle = cssVar('--color-on-surface') || 'black';
    g.font = `600 ${Math.round(band * .28)}px ${font}`;
    g.fillText(V.doc ? `${m.title} · עמוד ${V.page}` : `גבולות עירוב · ${m.city} · ${m.title}`, W - pad, band * .24);
    g.fillStyle = cssVar('--color-on-surface-variant') || 'black';
    g.font = `400 ${Math.round(band * .17)}px ${font}`;
    g.fillText(`מקור: מוקד העירוב (${D.source.phone}). להתמצאות בלבד — יש לוודא מול האחראי המקומי לפני שבת.`, W - pad, band * .54);
    g.fillText(exportLinks(), W - pad, band * .8);
    g.drawImage(img, 0, band, W, H);
    return cv;
  }
  // שמירת קובץ בינארי דרך אוצריא (חלון "שמירה בשם" אחד); מחוץ לאוצריא — הורדה בדפדפן
  async function saveBlob(blob, name, ext, mime, title) {
    if (!hasOtz) {
      const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = name + '.' + ext; a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 8000); return true;
    }
    const begin = await call('fs.beginBinaryWrite', { purpose: 'user-file', expectedSize: blob.size });
    if (!begin.success) { toast('לא ניתן לשמור: אין הרשאת שמירת קבצים'); return false; }
    try { const put = await fetch(begin.data.uploadUrl, { method: 'PUT', headers: { 'Content-Type': mime }, body: blob }); if (!put.ok) throw new Error(put.status); }
    catch (e) { toast('השמירה נכשלה'); return false; }
    const res = await call('fs.commitUserFileWrite', { writeToken: begin.data.writeToken, suggestedName: name, extension: ext, title });
    if (res.success && res.data && !res.data.cancelled) { toast('נשמר: ' + (res.data.name || name + '.' + ext)); return true; }
    if (!res.success) toast('השמירה נכשלה');
    return false;
  }

  // ── 1.3.0 (#3): הורדת חוברת שלמה בלחיצה אחת — PDF אחד או ZIP של תמונות ──
  // כל עמוד מצויר ב-pdf.js, ומעליו פס הייצוא (מקור, אזהרה, קישורי GitHub וחנות אוצריא). בלי ספריות חיצוניות.
  let bookBusy = false;
  async function exportBooklet(kind, partOnly) {
    if (bookBusy) { toast('הורדת חוברת כבר בעבודה'); return; }
    const d = V.doc; if (!d) return;
    const R0 = partOnly && V.range ? V.range.from : 1, R1 = partOnly && V.range ? V.range.to : d.pages;
    bookBusy = true;
    try {
      const pdf = await getPdf(d), pages = [], n = R1 - R0 + 1, PW = 1400;
      for (let p = R0; p <= R1; p++) {
        toast('מכין עמוד ' + (p - R0 + 1) + ' מתוך ' + n + '…');
        const pg = await pdf.getPage(p);
        const vp = pg.getViewport({ scale: PW / pg.getViewport({ scale: 1 }).width });
        const W = Math.round(vp.width), H = Math.round(vp.height), band = Math.round(W * .07), pad = Math.round(band * .3);
        const cv = document.createElement('canvas'); cv.width = W; cv.height = H + band;
        const g = cv.getContext('2d');
        g.fillStyle = 'white'; g.fillRect(0, 0, W, H + band);
        g.fillStyle = '#eeeeee'; g.fillRect(0, 0, W, band);
        const font = cssVar('--font-ui') || 'sans-serif';
        g.direction = 'rtl'; g.textAlign = 'right'; g.textBaseline = 'middle'; g.fillStyle = 'black';
        g.font = `600 ${Math.round(band * .28)}px ${font}`;
        g.fillText(`${d.title} · עמוד ${p}`, W - pad, band * .24);
        g.font = `400 ${Math.round(band * .17)}px ${font}`;
        g.fillText(`מקור: מוקד העירוב (${D.source.phone}). להתמצאות בלבד — יש לוודא מול האחראי המקומי לפני שבת.`, W - pad, band * .54);
        g.fillText(exportLinks(), W - pad, band * .8);
        g.save(); g.translate(0, band);
        await pg.render({ canvasContext: g, viewport: vp }).promise;
        g.restore();
        const blob = await new Promise(r => cv.toBlob(r, 'image/jpeg', .85));
        pages.push({ w: W, h: H + band, bytes: new Uint8Array(await blob.arrayBuffer()), p });
        cv.width = cv.height = 0; pg.cleanup && pg.cleanup();
      }
      toast('אורז את הקובץ…');
      const base = (d.title.split(' — ').join(' - ') + (partOnly && V.range ? ' - ' + V.range.title : '')).replace(/[\\/:*?"<>|]/g, '');
      const blob = kind === 'zip' ? makeZip(pages, readmeText(d, R0, R1)) : makePdf(pages, d.title);
      await saveBlob(blob, base, kind, kind === 'zip' ? 'application/zip' : 'application/pdf', 'שמירת החוברת');
    } catch (e) { toast('לא ניתן ליצור את הקובץ: ' + ((e && e.message) || e)); }
    finally { bookBusy = false; }
  }
  const readmeText = (d, a, b) => `${d.title}\r\nעמודים ${a}–${b}\r\n\r\nמקור: מוקד העירוב (${D.source.phone}), ${d.url}\r\nלהתמצאות בלבד — יש לוודא מול האחראי המקומי לפני שבת.\r\n\r\n` +
    `הורד מתוך תוסף "גבולות עירוב" לאוצריא.\r\nהורדת התוסף מ-GitHub: ${REPO}/releases\r\nבחנות התוספים של אוצריא: כלים ← תוספים ← חנות, חיפוש "גבולות עירוב"\r\n`;
  // PDF 1.4 מינימלי: עמוד לכל תמונה (JPEG כ-DCTDecode), גודל העמוד לפי התמונה ב-72dpi מוקטן לרוחב A4 בערך
  function makePdf(pages, title) {
    const enc = new TextEncoder(), parts = [], offs = []; let len = 0;
    const push = x => { const b = typeof x === 'string' ? enc.encode(x) : x; parts.push(b); len += b.length; };
    const obj = (i, body) => { offs[i] = len; push(i + ' 0 obj\n'); body(); push('\nendobj\n'); };
    const N = pages.length, kids = pages.map((_, i) => (3 + i * 3) + ' 0 R').join(' ');
    push('%PDF-1.4\n%\xE2\xE3\xCF\xD3\n');
    obj(1, () => push('<< /Type /Catalog /Pages 2 0 R >>'));
    obj(2, () => push(`<< /Type /Pages /Count ${N} /Kids [${kids}] >>`));
    pages.forEach((pg, i) => {
      const o = 3 + i * 3, sc = 595 / pg.w, w = 595, h = +(pg.h * sc).toFixed(2);
      obj(o, () => push(`<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${w} ${h}] /Resources << /XObject << /Im${i} ${o + 1} 0 R >> >> /Contents ${o + 2} 0 R >>`));
      obj(o + 1, () => { push(`<< /Type /XObject /Subtype /Image /Width ${pg.w} /Height ${pg.h} /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length ${pg.bytes.length} >>\nstream\n`); push(pg.bytes); push('\nendstream'); });
      const cs = `q ${w} 0 0 ${h} 0 0 cm /Im${i} Do Q`;
      obj(o + 2, () => push(`<< /Length ${cs.length} >>\nstream\n${cs}\nendstream`));
    });
    const info = 3 + N * 3, hex = s => '<FEFF' + [...s].map(c => c.charCodeAt(0).toString(16).padStart(4, '0')).join('') + '>';
    obj(info, () => push(`<< /Title ${hex(title)} /Creator ${hex('גבולות עירוב לאוצריא — ' + REPO)} >>`));
    const xref = len, total = info + 1;
    push(`xref\n0 ${total}\n0000000000 65535 f \n` + offs.slice(1).map(o => String(o).padStart(10, '0') + ' 00000 n \n').join(''));
    push(`trailer\n<< /Size ${total} /Root 1 0 R /Info ${info} 0 R >>\nstartxref\n${xref}\n%%EOF\n`);
    return new Blob(parts, { type: 'application/pdf' });
  }
  // ZIP בשיטת STORE (JPEG כבר דחוס), שמות UTF-8
  const CRC_T = (() => { const t = new Uint32Array(256); for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1; t[n] = c >>> 0; } return t; })();
  const crc32 = b => { let c = 0xFFFFFFFF; for (let i = 0; i < b.length; i++) c = CRC_T[(c ^ b[i]) & 255] ^ (c >>> 8); return (c ^ 0xFFFFFFFF) >>> 0; };
  function makeZip(pages, readme) {
    const enc = new TextEncoder();
    const files = [{ name: 'קרא-אותי.txt', data: enc.encode(readme) }].concat(pages.map(pg => ({ name: 'עמוד-' + String(pg.p).padStart(3, '0') + '.jpg', data: pg.bytes })));
    const parts = [], central = []; let off = 0;
    const hdr = (sz) => { const b = new DataView(new ArrayBuffer(sz)); return b; };
    for (const f of files) {
      const nm = enc.encode(f.name), crc = crc32(f.data), h = hdr(30);
      h.setUint32(0, 0x04034b50, true); h.setUint16(4, 20, true); h.setUint16(6, 0x0800, true); h.setUint16(8, 0, true);
      h.setUint16(10, 0, true); h.setUint16(12, 0x21, true); h.setUint32(14, crc, true); h.setUint32(18, f.data.length, true); h.setUint32(22, f.data.length, true);
      h.setUint16(26, nm.length, true); h.setUint16(28, 0, true);
      parts.push(h.buffer, nm, f.data);
      const c = hdr(46);
      c.setUint32(0, 0x02014b50, true); c.setUint16(4, 20, true); c.setUint16(6, 20, true); c.setUint16(8, 0x0800, true); c.setUint16(10, 0, true);
      c.setUint16(12, 0, true); c.setUint16(14, 0x21, true); c.setUint32(16, crc, true); c.setUint32(20, f.data.length, true); c.setUint32(24, f.data.length, true);
      c.setUint16(28, nm.length, true); c.setUint32(42, off, true);
      central.push(c.buffer, nm);
      off += 30 + nm.length + f.data.length;
    }
    const csize = central.reduce((a, b) => a + b.byteLength, 0), e = hdr(22);
    e.setUint32(0, 0x06054b50, true); e.setUint16(8, files.length, true); e.setUint16(10, files.length, true); e.setUint32(12, csize, true); e.setUint32(16, off, true);
    return new Blob(parts.concat(central, [e.buffer]), { type: 'application/zip' });
  }
  async function exportImage(kind) {
    if (!img.complete) { toast('המפה עדיין נטענת'); return; }
    const type = kind === 'jpg' ? 'image/jpeg' : 'image/png';
    let blob = null;
    try { blob = await new Promise(r => composeCanvas().toBlob(r, type, .92)); } catch (e) { blob = null; }
    if (!blob) { toast('לא ניתן ליצור את קובץ התמונה'); return; }
    const name = (V.doc ? `${V.doc.title.split(' — ')[0]} - עמוד ${V.page}` : `עירוב ${V.m.city} - ${V.m.title}`).replace(/[\\/:*?"<>|]/g, '');
    if (!hasOtz) { // מחוץ לאוצריא (בדיקה בדפדפן)
      const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = name + '.' + kind; a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 4000); return;
    }
    const begin = await call('fs.beginBinaryWrite', { purpose: 'user-file', expectedSize: blob.size });
    if (!begin.success) { toast('לא ניתן לשמור: אין הרשאת שמירת קבצים'); return; }
    try {
      const put = await fetch(begin.data.uploadUrl, { method: 'PUT', headers: { 'Content-Type': type }, body: blob });
      if (!put.ok) throw new Error(put.status);
    } catch (e) { toast('השמירה נכשלה'); return; }
    const res = await call('fs.commitUserFileWrite', { writeToken: begin.data.writeToken, suggestedName: name, extension: kind, title: 'שמירת מפת העירוב' });
    if (res.success && res.data && !res.data.cancelled) toast('המפה נשמרה: ' + (res.data.name || name));
    else if (!res.success) toast('השמירה נכשלה');
  }
  function printMap() {
    const m = V.m, ps = $('#printSheet');
    ps.innerHTML = `<h1>${V.doc ? esc(m.title) + ' · עמוד ' + V.page : 'גבולות עירוב · ' + esc(m.city) + ' · ' + esc(m.title)}</h1>
      <p>מקור: מוקד העירוב (${esc(D.source.phone)}). להתמצאות בלבד — יש לוודא מול האחראי המקומי לפני שבת.</p>
      <p class="links">${exportLinksHTML()}</p>
      <img src="${esc(srcOf(m))}" alt="">${V.doc || m.user ? '' : respHTML(m)}`;
    const go = () => {
      document.body.classList.add('printing');
      const done = () => { document.body.classList.remove('printing'); ps.innerHTML = ''; removeEventListener('afterprint', done); };
      addEventListener('afterprint', done); window.print(); setTimeout(done, 60000);
    };
    const pi = ps.querySelector('img'); pi.complete ? go() : (pi.onload = go);
  }

  // ── מפות שהמשתמש מוסיף (למשל מפה מקומית של פרדס כץ) ─────────
  // נשמרים רק ה-token של הקובץ ופרטי התיאור, ב-storage 'userMaps'. ה-URL נבנה מחדש בכל הפעלה.
  async function loadOwnMaps() {
    const r = await call('storage.get', { key: 'userMaps' });
    const list = r && r.success && Array.isArray(r.data) ? r.data : [];
    for (const u of list) {
      const f = await call('fs.resolveFileUrl', { token: u.token });
      if (!f.success) continue;
      attachOwn({ ...u, url: f.data.url });
    }
  }
  function attachOwn(u) {
    const m = { id: u.id, city: u.city, title: u.title, area: u.area || '', user: true, url: u.url, token: u.token, w: 0, h: 0 };
    if (!byCity.has(m.city)) { byCity.set(m.city, { maps: [], areas: [] }); D.cities.push(m.city); D.cities.sort((a, b) => a.localeCompare(b, 'he')); }
    byCity.get(m.city).maps.push(m); mapById.set(m.id, m); D.maps.push(m);
  }
  const ownList = () => D.maps.filter(m => m.user).map(({ id, city, title, area, token }) => ({ id, city, title, area, token }));
  async function addOwnMap(city) {
    if (!hasOtz) { toast('הוספת מפה זמינה בתוך אוצריא'); return; }
    const f = await call('fs.pickUserFile', { title: 'בחירת תמונת מפת עירוב', extensions: ['png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp'] });
    if (!f.success) { toast('אין הרשאה לבחירת קובץ'); return; }
    if (f.data.cancelled) return;
    const base = (f.data.name || 'מפה').replace(/\.[^.]+$/, '');
    const ans = await ownMapDialog(city, base);
    if (!ans) return;
    const { title, area } = ans; city = ans.city;
    attachOwn({ id: 'u-' + Date.now().toString(36), city, title, area, token: f.data.token, url: f.data.url });
    await call('storage.set', { key: 'userMaps', value: ownList() });
    render(); toast('המפה נוספה ל' + city);
  }
  // דיאלוג בתוך הדף (prompt/confirm חוסמים באוצריא)
  function ownMapDialog(city, title) {
    return new Promise(res => {
      const sc = document.createElement('div'); sc.className = 'sheet-scrim';
      sc.innerHTML = `<form class="sheet" role="dialog"><h2>הוספת מפה משלי</h2>
        <p>המפה תישמר אצלך בלבד, ותופיע בעיר ובשכונה שתבחרי.</p>
        <label class="field"><span>עיר</span><input name="city" list="dlCities" required value="${esc(city || '')}"></label>
        <datalist id="dlCities">${D.cities.map(c => `<option value="${esc(c)}">`).join('')}</datalist>
        <label class="field"><span>שם המפה</span><input name="title" required value="${esc(title)}"></label>
        <label class="field"><span>שכונה (המפה תופיע באריח של האחראי)</span><select name="area"></select></label>
        <p class="fine own-link">רוצים לראות היכן העיר על המפה? <button type="button" class="btn text" data-own-mapdev>המפה המצוירת (בפיתוח)</button></p>
        <div class="sheet-actions"><button type="button" class="btn text" data-x>ביטול</button><button class="btn filled">הוספה</button></div></form>`;
      document.body.appendChild(sc);
      const f = sc.querySelector('form');
      // השכונות של העיר מתוך רשימת האחראים, כך שהמפה מתחברת לאריח הנכון בלי הקלדה
      const fillAreas = () => { const n = byCity.get(f.city.value.trim()); const list = n ? [...new Set(n.areas.map(a => a.area).filter(Boolean))] : [];
        f.area.innerHTML = '<option value="">כל העיר</option>' + list.map(x => `<option value="${esc(x)}">${esc(x)}</option>`).join(''); };
      fillAreas(); f.city.addEventListener('change', fillAreas); f.city.addEventListener('input', fillAreas);
      sc.querySelector('[data-own-mapdev]').onclick = () => { const c = f.city.value.trim(); done(null); openMapDev(byCity.has(c) ? c : null); };
      (city ? f.title : f.city).focus();
      const done = v => { sc.remove(); res(v); };
      sc.querySelector('[data-x]').onclick = () => done(null);
      sc.addEventListener('keydown', e => { if (e.key === 'Escape') { e.stopPropagation(); done(null); } });
      f.onsubmit = e => { e.preventDefault(); done({ city: f.city.value.trim(), title: f.title.value.trim(), area: f.area.value.trim() }); };
    });
  }
  async function delOwnMap(id) {
    const m = mapById.get(id); if (!m) return;
    const ok = await call('ui.showConfirm', { title: 'הסרת מפה', content: 'להסיר את "' + m.title + '" מהתוסף? הקובץ עצמו לא יימחק.' });
    if (ok.success && ok.data && !ok.data.confirmed) return;
    D.maps.splice(D.maps.indexOf(m), 1); mapById.delete(id);
    const c = byCity.get(m.city); c.maps.splice(c.maps.indexOf(m), 1);
    await call('storage.set', { key: 'userMaps', value: ownList() });
    closeMap(); render(); toast('המפה הוסרה');
  }

  async function confirmExternal(url) {
    if (!/^https?:/i.test(url)) return true;
    let host = url; try { host = new URL(url).hostname || url; } catch (e) {}
    const c = await call('ui.showConfirm', { title: 'קישור לאתר חיצוני', content: 'הקישור מוביל אל "' + host + '", מחוץ לתוסף. לפתוח אותו בדפדפן המערכת?' });
    if (!c || !c.success || !c.data) return true;   // אין חלון אישור זמין: ממשיכים לפתוח
    return c.data.confirmed === true;
  }
  // ── קישורים: באוצריא קישור רגיל לא נפתח — פותחים בדפדפן החיצוני וגם מעתיקים ללוח ──
  async function openLink(u) {
    if (state.offline && /^https?:/i.test(u)) { toast('אוצריא במצב מנותק: קישורים חיצוניים כבויים'); return; }
    let copied = false;
    // 1.3.0: הגדרה "העתקה" — רק העתקה ללוח, בלי אישור ובלי פתיחה
    if (state.linkMode === 'copy' && /^https?:/i.test(u)) {
      try { await navigator.clipboard.writeText(u); copied = true; } catch (e) {}
      toast(copied ? 'הקישור הועתק ללוח' : u); return;
    }
    if (!(await confirmExternal(u))) return;
    try { await navigator.clipboard.writeText(u); copied = true; } catch (e) {}
    let w = null;
    if (hasOtz) { const r = await call('app.openUrl', { url: u }); if (r.success) { toast('נפתח בדפדפן' + (copied ? ' · הקישור גם הועתק' : '')); return; } }
    try { w = window.open(u, '_blank', 'noopener'); } catch (e) {}
    toast(w ? 'נפתח בחלון חדש' : copied ? 'הקישור הועתק ללוח — הדביקו בדפדפן' : u);
  }
  // ── מקורות בספריית אוצריא (כמו בכיוון תפילה): הספר עצמו, הכותרת הקצרה ביותר ──
  const srcNorm = t => (t || '').replace(/[^\u05d0-\u05eaA-Za-z0-9 ]/g, ' ').replace(/\s+/g, ' ').trim();
  async function openSrc(q, ref) {
    if (!hasOtz) { toast(q + ' ' + ref); return; }
    for (const qq of q.split('|')) {
      const l = await call('library.findBooks', { query: qq, limit: 30 });
      const w = srcNorm(qq).split(' ');
      const m = (l.success ? l.data || [] : []).filter(x => { const t = ' ' + srcNorm(x.title) + ' '; return w.every(y => t.includes(' ' + y + ' ')); })
        .sort((a, b) => srcNorm(a.title).length - srcNorm(b.title).length)[0];
      if (m) { const r = await call('reader.openBookAtRef', { bookId: m.bookId, ref, highlight: true }); if (r.success && r.data !== false) return; }
    }
    const r = await call('reader.openSearchTab', { query: q.split('|')[0] + ' ' + ref });
    if (!r.success) toast('לא נמצא בספרייה: ' + q + ' ' + ref);
  }
  const SOURCES = [
    ['שולחן ערוך — הלכות עירובין', 'שולחן ערוך אורח חיים', 'שסו'],
    ['משנה ברורה — הלכות עירובין', 'משנה ברורה', 'שסו'],
    ['שולחן ערוך — הלכות רשויות', 'שולחן ערוך אורח חיים', 'שמה'],
    ['רמב"ם — הלכות עירובין', 'הלכות עירובין|משנה תורה עירובין', 'א'],
    ['גמרא — מסכת עירובין', 'עירובין', 'ב א']
  ];
  const OPEN_TASKS = [
    'קווי עירוב בקירוב לעוד ערים (כעת קרית אתא וחדרה) — כל עיר נבדקת מול מפת המוקד לפני שהיא נכנסת',
    'מפת רחובות בלי אינטרנט, בסיס משותף עם מקוואות וכיוון תפילה (בפיתוח, מחכה לעזרה: ראו "מפה (בפיתוח)")',
    'עדכון מתוך התוסף בזמן ריצה — אחרי שאוצריא תפתח את הכתובות (בקשה הוכנה)',
    'סיווג רמות עירוב (חצר / שכונתי / עירוני) — רק עם מקור מהמוקד',
    'קווי עירוב חדים (וקטור/SVG) מעל מפת רחובות, משכבת העירוב של GovMap — מחייב הרצה מישראל',
    'עירובין נוספים בחו"ל, ועוד בתי חולים בחו"ל — רק ממקור רשמי',
    'תחום שבת לבתי חולים נוספים (שערי צדק, הדסה, לניאדו ועוד) — רק ממקור מוסמך',
    'תרגום מלא לאנגלית (כעת רק ממשק, ערים ובתי חולים)',
    'בדיקה אם המיקום שלי בתוך העירוב (כעת: בדיקת כתובת שמחפשים, בערים שיש להן קו)',
    'מפות לשכונות חסרות (פרדס כץ, קרית הרצוג ועוד) — פנייה למוקד העירוב',
    'מראי מקום לפי עיר בתוך חוברות "דרך התחומין" (קפיצה לעמוד העיר)'
  ];

  // ── זיהוי מיקום: גשר "אחת שאלתי" (כמו בכיוון תפילה), ואם אין — מיקום הדפדפן; העיר הקרובה נפתחת ──
  const CITY_POS = {
    'אופקים': [31.314, 34.620], 'אלעד': [32.052, 34.951], 'אשדוד': [31.801, 34.650], 'באר שבע': [31.252, 34.791],
    'בית שמש': [31.730, 34.990], 'ביתר': [31.700, 35.120], 'בני ברק': [32.084, 34.834], 'בת ים': [32.023, 34.751],
    'גבעת זאב': [31.860, 35.170], 'גני תקוה': [32.060, 34.873], 'זכרון יעקב': [32.570, 34.950], 'חדרה': [32.434, 34.919],
    'חיפה': [32.800, 34.990], 'חצור': [32.980, 35.540], 'טבריה': [32.790, 35.530], 'יסודות': [31.810, 34.860],
    'ירוחם': [30.990, 34.930], 'ירושלים': [31.780, 35.210], 'כרמיאל': [32.920, 35.300], 'לוד (אחיסמך)': [31.950, 34.890],
    'מודיעין עילית': [31.930, 35.040], 'מיצד': [31.590, 35.190], 'מירון': [32.980, 35.440], 'נוף הגליל': [32.710, 35.320],
    'נתיבות': [31.420, 34.590], 'נתניה': [32.330, 34.860], 'עמנואל': [32.160, 35.140], 'עפולה': [32.610, 35.290],
    'פתח תקוה': [32.090, 34.880], 'צפת': [32.960, 35.500], 'קרית אתא': [32.810, 35.110], 'קרית יערים (טלזסטון)': [31.800, 35.100],
    'קרית מלאכי': [31.730, 34.750], 'רחובות': [31.890, 34.810], 'רכסים': [32.750, 35.100], 'תל ציון': [31.880, 35.250], 'תפרח': [31.330, 34.670]
  };
  const km = (a, b) => { const r = Math.PI / 180, x = (b[1] - a[1]) * r * Math.cos((a[0] + b[0]) * r / 2), y = (b[0] - a[0]) * r; return Math.hypot(x, y) * 6371; };
  async function locateMe() {
    toast('מאתר מיקום…');
    let p = null, how = '';
    try { if (window.ERUV_BRIDGE && await ERUV_BRIDGE.available()) { const d = await ERUV_BRIDGE.location(); p = [+d.lat, +d.lon]; how = 'מ-Windows'; } } catch (e) {}
    if (!p && navigator.geolocation) {
      p = await new Promise(res => navigator.geolocation.getCurrentPosition(g => res([g.coords.latitude, g.coords.longitude]), () => res(null), { timeout: 12000, maximumAge: 300000 }));
      how = 'מהמכשיר';
    }
    if (!p) { toast('לא ניתן לזהות מיקום. התקינו את הגשר "אחת שאלתי" או בחרו עיר מהרשימה'); return; }
    const best = Object.keys(CITY_POS).filter(c => byCity.has(c)).map(c => [c, km(p, CITY_POS[c])]).sort((a, b) => a[1] - b[1])[0];
    if (!best || best[1] > 25) { toast('אין עירוב במאגר בקרבת המיקום (העיר הקרובה: ' + (best ? best[0] + ', ' + Math.round(best[1]) + ' ק"מ' : '—') + ')'); return; }
    goCity(best[0]); toast('מיקום ' + how + ': ' + best[0] + (best[1] > 3 ? ' (כ-' + Math.round(best[1]) + ' ק"מ)' : ''));
  }

  // ── יומן שינויים ותגובות ─────────────────────────────────────
  const REPO = 'https://github.com/Hadabadra/gvulot-eruv';
  // בכל ייצוא (תמונה, הדפסה/PDF): קישור לגיטהאב והפניה לחנות התוספים של אוצריא
  const STORE = 'https://otzaria.org/plugins/6ad93b5d2917198d73992a9d';
  const exportLinks = () => `מתוך תוסף "גבולות עירוב" לאוצריא · חנות התוספים: ${STORE.replace('https://', '')} · GitHub: ${REPO.replace('https://', '')}/releases`;
  // 1.9.0: בהדפסה / PDF הקישורים לחיצים (תגית a), וגם קישור לחנות התוספים
  const exportLinksHTML = () => `מתוך תוסף "גבולות עירוב" לאוצריא · <a href="${STORE}">חנות התוספים של אוצריא</a> (${STORE.replace('https://', '')}) · <a href="${REPO}/releases/latest">הורדה מ-GitHub</a> (${REPO.replace('https://', '')}/releases)`;
  const respHTML = m => {   // האחראים של המפה המודפסת
    const ar = areasOfMap(m.id), L = LINES[m.id];
    if (ar.length) return `<h2>אחראים במפה זו</h2><ul>${ar.map(a => `<li><b>${esc(a.area || a.city)}</b> — ${esc(a.names.join(', ') || 'שם לא צוין')}${a.phones.length ? ' · ' + a.phones.map(fmtPhone).map(esc).join(', ') : ''}</li>`).join('')}</ul>`;
    return L && L.resp ? `<h2>לבירורים (לפי המפה)</h2><p>${esc(L.resp)}</p>` : `<p>האחראים המלאים של ${esc(m.city)} — בתוסף, בדף העיר.</p>`;
  };
  const CHANGELOG = [
    ['1.9.0', ["קווי עירוב על המפה (פיילוט): קרית אתא — שכונת שביט, וחדרה — שכונות שלמה, קלרין ופאר. השכבה \"קווי עירוב (בקירוב — לפי מפת המוקד)\" משורטטת מתוך מפות המוקד, עם המפה המקורית והאחראי. קירוב בלבד — הקובע הוא המפה והאחראי.", "בדיקת כתובת: אחרי חיפוש כתובת במפה מוצג אם היא בתוך קו העירוב, על הגבול או מחוצה לו (בערים שיש להן קו).", "במציג המפה: כפתור \"קו העירוב בקירוב במפה\" ושורת \"לבירורים לפי המפה\" כשאין אחראים משויכים.", "עט לשרטוט ידני: סרגל קטן — קו או שטח, ביטול נקודה (Ctrl+Z), ניקוי, 5 צבעים ושמירה (Enter). נשמר כשכבה שנטענת לבד וכקובץ GeoJSON.", "אודות: רמות 2–4 בוצעו חלקית; קו שהמפה שלו התחלפה אצל המוקד יורד אוטומטית בעדכון השבועי.", "הדפסה: קישורים לחיצים לחנות התוספים ול-GitHub, והאחראים של המפה מודפסים יחד איתה.", "סמלים מונוכרום שתואמים לתוכן (מועדף, אימייל, קישור חיצוני, בית חולים, הוספת מפה, מקורות).", "כפתור \"חיפוש עירוב\" בתוך שדות החיפוש של אוצריא (מ-0.9.98.3), מופיע כשיש טקסט.", "תיקון: מפת \"מעלות דפנה והסביבה\" לא נפתחה במציג (#12). השכבה ששורטטה נטענת לבד (#11).", "החוברות \"דרך התחומין\" נדחסו מעט בלי שינוי באיכות. דורש אוצריא 0.9.98.3 ומעלה."]],
    ['1.8.0', ["מפה משותפת 3.0: מצבי תצוגה מפה · סביבה קרובה · רחובות · כדור, ו\"סגנון המפה\" בתפריט השכבות (צבעוני כמו עינים למקרא, ארצות בצבעים, חום-לבן).", "באודות: קישור לשרשור הדיווחים בפורום אוצריא.", "המלצה על \"אחת שאלתי\" 1.2.0."]],
    ['1.7.0', ["מפה משותפת 2.2 — לחיצה על רחוב בוחרת אותו (כמו בכיוון תפילה): שם, אורך, \"נקודת מוצא מכאן\" ו\"מסלול הליכה לכאן\".", "כפתור + בסרגל המפה: טעינת קובץ רחובות, שכבה מקובץ GeoJSON, ושרטוט קו או שטח על המפה (נשמר כשכבה וכקובץ).", "מפה צבעונית (ים כחול וכל ארץ בצבע משלה) — מתג בתפריט השכבות.", "\"המיקום שלי\" במפה דרך הגשר \"אחת שאלתי\", ואם אין גשר — מיקום המחשב.", "דיווח תקלות אוטומטי: כשמשהו נתקע, הודעה קטנה מציעה לשלוח דיווח קריסה דרך מערכת הדיווחים של אוצריא (באישור שלכם; בלי אינטרנט נשמר ונשלח אחר כך).", "המלצה על \"אחת שאלתי\" מהמקור המשותף (גרסה 1.1.0): בלי אינטרנט נשמר דף הסבר עם קישורים לחיצים.", "ההדרכה שיורדת כוללת גם את מפות העירוב של העיר שנבחרה.", "תיקון: חוברת תחומין שלמה שירדה כ-PDF או ZIP יצאה עם עברית משובשת (#9).", "פסגת זאב מרכז (ירושלים): קישורים לאתר העירוב ולתיקיית המפות שלו (#10).", "תיקון: שליחת תגובה מהתוסף נשלחה עם סוג דיווח לא תקין."]],
    ['1.6.0', ["המפה ממלאת את גובה החלון — בלשונית ובמסך מלא — בלי גלילה (מפה משותפת 2.1).", "במפה: כרטיס העיר ורשימת \"ערים קרובות\" תופסים פחות מקום, כך שרואים יותר מפה. הוסר כפתור \"מסלול הליכה\" — הנקודות כאן הן ערים, לא יעד הליכה; הניווט ב-Waze וב-Google Maps נשאר.", "הדרכה שלב אחר שלב: כפתור ? על המפה, עם \"הקודם\" ו\"הבא\". \"הורדת ההדרכה\" שומרת קובץ עם כל השלבים, צילום של המפה וקישורי ההורדה של התוסף (GitHub וחנות אוצריא).", "המלצה על \"אחת שאלתי\" — הופך את המחשב לאוצריא ו-Word בלבד: הודעה קטנה לכל היותר פעם בשבוע (עם \"אל תציג שוב\"), ושורה ומתג \"הצג המלצות\" באודות. במצב \"ללא גישה לאינטרנט\" — שמירת קובץ הסבר עם קישורי ההורדה, להעברה למחשב מחובר.", "בתכנון (עוד לא במימוש): קווי עירוב על המפה, הורדה מ-GovMap ופיילוט ממפות מוקד העירוב — [PLAN](https://github.com/Hadabadra/gvulot-eruv/blob/main/gvulot-eruv-docs/PLAN.md)."]],
    ['1.5.0', ["מפה משותפת 2.0, במראה של כיוון תפילה: כפתורים צפים על המפה — זום, סיבוב, מצפן שמחזיר צפון למעלה, מרכוז ומסך מלא. גרירה, צביטה, גלגלת, לחיצה כפולה ומקלדת (חיצים, + ו-−, [ ו-] לסיבוב, N לצפון).", "חיפוש מקום בתוך המפה (#7): עיר, שכונה, רחוב, פינה או כתובת עם מספר בית — למשל \"נחל לכיש 30, בית שמש\". הצעות בזמן ההקלדה; כתובת מדויקת נמצאת ב-OpenStreetMap כשאוצריא מחוברת, ובלי רשת מוצג הרחוב. רחובות העיר נטענים לבד.", "כפתור \"שכבות\": ערי העירוב, רחובות, בתי כנסת ומפת העולם, כל אחת עם מתג; השכבות של כיוון תפילה, מקוואות ועינים למקרא — לחיצה שואלת ופותחת את התוסף ההוא באותו מקום.", "מפת העולם בלי אינטרנט: יבשות, ארצות, ימים, נהרות וערים בעברית, כשמתרחקים בזום — שימושי לעירובין בחו\"ל.", "טעינת שכבה מקובץ GeoJSON (למשל קו עירוב שציירתם), לזמן הפתיחה של המסך.", "באודות: פרק \"המפה המשותפת\" עם [הסבר](https://github.com/Hadabadra/Mikvaot/blob/main/shared/map-core/SHARED-MAP.md), [הקוד של המפה](https://github.com/Hadabadra/Mikvaot/tree/main/shared/map-core) וקישורים ל[כיוון תפילה](https://otzaria.org/plugins/6ac80057bd083fcc18562fa0), [מקוואות](https://otzaria.org/plugins/6ad9c9c92917198d739939b7) ו[עינים למקרא](https://otzaria.org/plugins/6a671db1dd175558ae6f20ac). קישורים ביומן השינויים נפתחים בלחיצה.", "הרשאה חדשה: network.access, רק לחיפוש כתובת עם מספר בית (nominatim.openstreetmap.org). במצב \"ללא גישה לאינטרנט\" לא נשלח כלום."]],
    ['1.4.1', ["כפתורי השכבות במפה מקשרים לדפי החנות הנכונים של מקוואות וגבולות עירוב."]],
    ['1.4.0', ["מפה משותפת: במסך המפה, בשורת השכבות, כפתורים \"↗ כיוון תפילה\" ו\"↗ מקוואות\" פותחים את התוסף ההוא באותו מקום במפה (ואם אינו מותקן — קישור לחנות). קוד המפה מגיע עכשיו ממקור אחד המשותף לשלושת התוספים, וכולל מסלול הליכה."]],
    ['1.3.1', ["נוספה לבקשה למוקד העירוב מפת העירוב השכונתי החדש ברמת בית שמש ד'3, שלא נמצאה במקור רשמי מקוון. עד שתתקבל — פונים לאחראי המקומי ולמוקד."]],
    ['1.3.0', ["מצב מנותק נקבע לפי ההגדרה \"ללא גישה לאינטרנט\" באוצריא ולא לפי זיהוי רשת: מי שכרגע בלי רשת עדיין יכול לפתוח או להעתיק קישור. ההסתרה מתעדכנת מיד כשמשנים את ההגדרה.", "הגדרה חדשה באודות: קישור חיצוני — פתיחה בדפדפן (אחרי אישור, כמו קודם) או העתקה ללוח בלבד.", "הורדת חוברת שלמה בלחיצה אחת: בתפריט הייצוא של המציג — החוברת כולה כ-PDF או כ-ZIP של תמונות, או רק החלק הפתוח. בכל עמוד המקור וקישורי ההורדה של התוסף."]],
    ['1.2.1', ["תיקון: קווי הרחובות במפה לא נראו (משתנה צבע שאינו קיים באוצריא). עכשיו הרחובות ברורים."]],
    ['1.2.0', ["רחובות ובתי כנסת של 76 ערים מובנים בתוסף ונטענים לבד בלשונית המפה כשמתקרבים לעיר — בלי אינטרנט ובלי הורדת קובץ."]],
    ['1.1.0', ["המפה היא עכשיו לשונית בתוך התוסף (\"רשימה | מפה\" מעל התוכן) במקום חלון מעל המסך. בדף כל עיר כפתור \"במפה\" שעובר ללשונית וממקד על העיר.", "מצב מנותק באוצריא: כל הקישורים החוצה (אתרים, GitHub, הורדת רחובות) מוסתרים. טלפונים ואימיילים נשארים.", "מפה אישית: השכונה נבחרת מרשימת השכונות של העיר, והמפה מופיעה אוטומטית באריח של האחראי לשכונה. בחלון ההוספה קישור למפה המצוירת (בפיתוח).", "הוסרה מהמפה השכבה \"כיוון תפילה (בקרוב)\"."]],
    ['1.0.2', ['תיקון צבע צל בחלון המפה הצדדי כדי שהעדכון יעלה לחנות']],
    ['1.0.1', ['המפה נפתחת בצד ליד סרגל הערים ולא מכסה את המסך; לחיצה נוספת על כפתור המפה סוגרת אותה; בחירת עיר ברשימה ממקדת את המפה']],
    ['1.0.0', ['גרסה סופית ראשונה', 'קישור חיצוני נפתח בדפדפן המערכת אחרי אישור', 'המפה במסך מלא מתחת לסרגל העליון', 'הורדת קובץ רחובות לעיר בלחיצה אחת, ואז טעינה במסך המפה']],
    ['0.6.4', ['קו מתאר מקורב של הארץ כרקע במפה', 'הורדת רחובות משני שרתים לבחירה (פתרון לשגיאה 406), עם הסבר']],
    ['0.6.3', ['ועד העירובים השכונתיים בבני ברק: כפתור שליחת מייל לוועד']],
    ['0.6.2', ['המפה נפתחת במסך מלא', 'ועד העירובים השכונתיים בבני ברק נוסף לרשימת האחראים (רפאל סטפנסקי)']],
    ['0.6.1', ['תיקון שם האייקון לפי דרישות חנות אוצריא']],
    ['0.6.0', ['מסך מפה חדש: זום, גרירה, חיפוש, טעינת רחובות מקובץ, בתי כנסת, ופתיחת העיר מהמפה', 'כפתור מפה בסרגל העליון', 'אפשרויות עתידיות מסומנות "בקרוב": קווי עירוב, בדיקת כתובת, כיוון תפילה', 'חיפוש רחוב וכתובת תומך בקיצורי ערים']],
    ['0.5.3', ['מסך חדש "מפה (בפיתוח)" באודות: ערי העירוב על מפה, ולחיצה על עיר פותחת אותה', 'בכל ייצוא (תמונה והדפסה/PDF): קישור לגיטהאב והפניה לחנות התוספים של אוצריא', 'בסיס משותף למפה ולחיפוש (map-core) עם מקוואות וכיוון תפילה']],
    ['0.5.2', ['תיקון: כרטיסי חו"ל — הטקסט והכפתורים כבר לא עולים זה על זה', 'הדף מותאם לרוחב החלון, בלי גלילה אופקית']],
    ['0.5.1', ['חו"ל: נתוני המדריך eruv.org (באישורם) — 411 עירובין, קווי סטטוס וטלפונים, אימיילים וקישורי מפה', 'שמות בעברית למדינות ולערים מרכזיות בחו"ל, וחיפוש בעברית ובאנגלית', 'תיבת סינון בתוך דף הארץ (למשל "שיקגו")', 'יצירת קשר עם מוקד העירוב: דף יצירת הקשר, טלפון ואימייל', 'שיקגו: תוקנו שני קישורים מתים (ווסט רוג\'רס פארק, היילנד פארק)', 'אנגלית: גם התוכן של שיקגו']],
    ['0.5.0', ['ניווט ראשי בסרגל הצד: ישראל / חו"ל / בתי חולים', 'בתי חולים: נוספו שערי צדק, הדסה עין כרם, מעיני הישועה, לניאדו, בילינסון ושניידר — רב בית החולים, העוזר ושירותי הדת, רק מהאתר הרשמי של בית החולים ועם קישור למקור', 'עירוב במתחם ותחום שבת לפי מה שכתוב במקור הרשמי (בילינסון ושניידר: בתוך התחום מבני ברק ומגבעת שמואל)', 'ארגונים: מוקד העירוב ועזר מציון', 'קישורים לעמודי שירותי הדת של בתי חולים נוספים', 'עדכון אוטומטי שבועי מהמקורות במאגר, עם גרסה חדשה כשמשהו משתנה', 'בתי חולים בחו"ל: Memorial Sloan Kettering, Maimonides, Sinai (בולטימור) — מהאתר הרשמי', 'שיקגו: רשימת העירובין וקווי הסטטוס לפי מועצת הרבנים (cRc), כולל לינקולנווד–פיטרסון פארק; בתי החולים אוונסטון וסקוקי', 'ממשק בסיסי באנגלית (כפתור EN בסרגל העליון): כפתורים, כותרות, ערים ובתי חולים']],
    ['0.4.0', ['חוברת רציפה: כל העמודים בגלילה אחת, ולא רק עמוד אחר עמוד', 'בדף עיר נפתח רק החלק שלה בחוברת (למשל מודיעין עילית/קרית ספר — חלק ב\' של דרך התחומין)', 'חו"ל: נוספו מונסי, רוקלנד, שני מקורות ללייקווד ו-Eruv Finder', 'חו"ל: הוסר הקישור של מיניאפוליס (darcheinoammn.org) לבקשת משתמש מחו"ל, כי אינו מקור חרדי']],
    ['0.3.5', ['תיקון: שגיאת Stack Overflow בהתקנה']],
    ['0.3.4', ['תיקון: החוברות לא נפתחו באוצריא (Missing PDF)']],
    ['0.3.3', ['החוברות כ-PDF בקורא של צופה PDF+OCR — חדות בכל הגדלה', 'סרגל הצד: ערים (עם סימון תחום), בתי חולים וחו"ל', 'בתי חולים: עירוב במתחם ותחום שבת, עזר מציון', 'מפות 2000 פיקסלים כדי לעמוד ב-50MB']],
    ['0.3.2', ['תיאור התוסף קוצר ל-150 תווים כנדרש בחנות']],
    ['0.3.1', ['החוברות כקובצי PDF מקוריים בתוך התוסף, חדות בכל הגדלה', 'גודל התוסף ירד לכ-41MB (מגבלת החנות 50MB)']],
    ['0.3.0', ['תחום שבת: מאמרי התחומין של מוקד העירוב לשש ערים, ותקציר "מותר / ע"י עירוב תחומין / אסור"', 'החוברות "דרך התחומין" (גוש דן ובתי החולים איכילוב ותל השומר, ירושלים) ו"המדריך לנופש" בתוך התוסף, עם דפדוף ושמירת העמוד האחרון', 'ספריית העירובין של המוקד — 42 ספרים וחוברות', 'עירובין בחו"ל — 339 מקומות ב־27 ארצות', 'מפות חדות יותר (2400 פיקסלים)', 'כפתור בסרגל העיון וקיצור מקשים Ctrl+Alt+E', 'חיפוש גם בחוברות, בספרייה ובחו"ל']],
    ['0.2.5', ['זיהוי מיקום (גשר אחת שאלתי או המכשיר) ופתיחת העיר הקרובה', 'כפתור נפרד למידע הלכתי ומקורות', 'קישורים נפתחים בדפדפן ומועתקים ללוח', 'מקורות הלכות עירובין בספריית אוצריא', 'התכנון והמשימות הפתוחות בתוך התוסף', 'יומן שינויים מקוצר']],
    ['0.2.2', ['סמל חדש לתוסף: קו עירוב בין עמודים סביב סמן מיקום']],
    ['0.2.1', ['שליחת תגובה מתוך התוסף דרך אוצריא', 'יומן שינויים וקישור לתכנון המתעדכן בגיטהאב']],
    ['0.2.0', ['כפתור "מפה כללית" לשכונות בלי מפה משלהן (פרדס כץ, קרית הרצוג ועוד)', 'הוספת מפה משלי מהמחשב, עם קישור לשכונה ולאחראים']],
    ['0.1.0', ['66 מפות ו־206 אחראים מאת מוקד העירוב', 'חיפוש, מציג עם הגדלה וסיבוב, ייצוא לתמונה ולהדפסה, מועדפים']]
  ];
  async function sendFeedback() {
    const t = ($('#fbText').value || '').trim();
    if (!t) { $('#fbText').focus(); return; }
    const r = await call('feedback.report', { details: '[גבולות עירוב ' + VERSION + '] ' + t, reportType: 'other' });
    if (r.success && r.data === 'cancelled') return;
    if (r.success) { $('#fbText').value = ''; toast(r.data === 'queued' ? 'התגובה תישלח כשיהיה חיבור' : 'התגובה נשלחה, תודה!'); }
    else toast('השליחה זמינה בתוך אוצריא');
  }
  const VERSION = '1.9.0';

  // ── אודות ───────────────────────────────────────────────────
  function openHalacha() {
    $('#infoTitle').textContent = 'מידע הלכתי';
    $('#infoBody').innerHTML = `
      <p>המפות מסמנות את גבולות העירובים השכונתיים כפי שפורסמו על ידי <b>מוקד העירוב</b>.
      יש הבדלים בדרגות הכשרות בין העירובים, וכל עירוב עלול להיפסל במהלך השבוע. ${esc(D.source.note)}</p>
      <p>לפני שבת יש לוודא מול האחראי המקומי שהעירוב תקין. לשאלות למעשה יש לפנות לרב המקומי או למוקד העירוב,
      שנותן מענה הלכתי בטלפון <button class="chip" data-phone="${esc(D.source.phone)}">${ICON.phone}${esc(D.source.phone)}</button></p>
      <p>התוסף אינו פוסק הלכה ואינו קובע אם עירוב כשר. הוא מרכז את המידע כדי להקל על הבירור.</p>
      <h3 class="info-h">ללימוד: מקורות בספריית אוצריא</h3>
      <div class="area-actions">${SOURCES.map(x => `<button class="chip map" data-src="${esc(x[1])}" data-ref="${esc(x[2])}">${ICON.book}${esc(x[0])}</button>`).join('')}</div>`;
    $('#infoScrim').hidden = false; $('#infoClose').focus();
  }
  // מפה (בפיתוח): בסיס משותף עם מקוואות וכיוון תפילה (map-core.js). כרגע ערי העירוב כנקודות, בלי שכבת רחובות וקווי עירוב.
  // לשונית מפה (1.1.0): חלק מהתוסף, לא חלון מעליו. ערי העירוב כנקודות (מיקום בקירוב), בלי קווי עירוב עדיין.
  let mapKey = '';
  function showTab() {
    const map = state.tab === 'map';
    document.querySelectorAll('#viewTabs [data-tab]').forEach(b => { b.classList.toggle('on', b.dataset.tab === state.tab); b.setAttribute('aria-selected', String(b.dataset.tab === state.tab)); });
    $('#main').hidden = map; $('#mapTab').hidden = !map;
    const sel = state.mapSel || (state.view === 'city' && CITY_POS[state.city] ? state.city : null);
    $('#viewScope').textContent = map ? (sel || 'כל הארץ') : '';
    if (!map) return;
    const key = [sel, state.offline].join('|');
    if (key === mapKey && $('#mapTab').firstChild) return; mapKey = key;
    const MC = window.MapCore, UI = window.MapUI;
    if (!MC || !UI) { $('#mapTab').textContent = 'רכיב המפה לא נטען.'; return; }
    const cities = Object.keys(CITY_POS).filter(c => byCity.has(c));
    const pts = cities.map(c => ({ id: c, name: c + ' · ' + byCity.get(c).maps.length + ' מפות', sub: byCity.get(c).maps.length + ' מפות עירוב · ' + byCity.get(c).areas.length + ' שכונות עם אחראי', lat: CITY_POS[c][0], lon: CITY_POS[c][1] }));
    // מסלול הליכה כבוי: הנקודות כאן הן ערים (מיקום בקירוב), לא יעד הליכה. ניווט ב-Waze / Google Maps נשאר.
    const lineIds = Object.keys(LINES), lineCities = [...new Set(lineIds.map(i => LINES[i].city))];
    const lineLayer = lineIds.length ? [{ id: 'eruv-lines', name: LINE_LAYER, glyph: '〰', sub: lineIds.length + ' עירובים · ' + lineCities.join(', '), points: lineIds.map(i => { const c = lineCenter(LINES[i]); return { lat: c[0], lon: c[1], id: '__line-' + i, label: LINES[i].name }; }),
      lines: lineIds.map(i => ({ coords: LINES[i].coords, closed: LINES[i].closed, solid: true, color: 'var(--eruv-line, #d84315)', label: `קו עירוב בקירוב — ${LINES[i].city} · ${LINES[i].name} · המקור: מפת מוקד העירוב · בירורים: ${lineResp(i)}` })) }] : [];
    UI.mount($("#mapTab"), { tab: true, route: false, draw: true, zoom: state.mapZoom || null, inside: insideMsg, layers: lineLayer, self: 'com.hadabadra.gvulot-eruv', center: state.mapCenter || null, builtinStreets: true, online: !state.offline, points: pts, layerId: 'eruv', glyph: 'ע', layerName: 'ערי עירוב', selectedId: sel, call, openLink, repo: REPO, forumUrl: 'https://otzaria.org/forum/post/45390', issueUrl: REPO + '/issues/1',
      // 2.2: בהדרכה שיורדת — גם מפות העירוב של העיר שנבחרה (תמונות מוקד העירוב, מוטמעות בקובץ)
      guideExtra: async id => {
        const c = byCity.get(id || state.city); if (!c || !c.maps.length) return '';
        const toData = async m => { try { const b = await (await fetch(srcOf(m))).blob(); return await new Promise(r => { const f = new FileReader(); f.onload = () => r(f.result); f.onerror = () => r(''); f.readAsDataURL(b); }); } catch (e) { return ''; } };
        let h = `<h2>מפות העירוב — ${esc(id || state.city)}</h2><p>מקור: מוקד העירוב (${esc(D.source.phone)}). להתמצאות בלבד — יש לוודא מול האחראי המקומי לפני שבת.</p>`;
        for (const m of c.maps.slice(0, 6)) { const u = await toData(m); if (u) h += `<figure><img src="${u}" alt="${esc(m.title)}" style="width:100%;height:auto;display:block"><figcaption>${esc(m.title)}</figcaption></figure>`; }
        return h;
      },
      guide: [{ t: 'ערי העירוב במפה', d: 'כל נקודה היא עיר עם מפות עירוב (מיקום בקירוב). "פתיחת העיר" מציגה את מפות מוקד העירוב והאחראים.' },
        { t: LINE_LAYER, d: `בחלק מהערים (${lineCities.join(', ') || 'בקרוב'}) קו העירוב משורטט על המפה בקירוב, מתוך מפת המוקד. חיפוש כתובת מראה אם היא בתוך הקו, סמוכה לו או מחוצה לו. הקובע תמיד: המפה המקורית של המוקד והאחראי המקומי.` },
        { t: 'עט — שרטוט ידני', d: 'כפתור העט פותח סרגל קטן: קו או שטח, ביטול נקודה (Ctrl+Z), ניקוי, צבע ושמירה (Enter). השרטוט נשמר כשכבה שנטענת לבד וכקובץ GeoJSON.' }],
      pickLabel: 'פתיחת העיר', nearbyLabel: 'ערים קרובות', future: ['קווי עירוב לעוד ערים (לפי מפות המוקד)', 'עדכון אוטומטי של הקווים כשהמוקד מעדכן מפה'],
      onPick: id => { state.tab = 'list'; goCity(id); } });
    $('#mapTab').insertAdjacentHTML('beforeend', `<p class="fine">מיקומי הערים בקירוב. ${lineIds.length ? 'קווי העירוב המשורטטים (' + lineCities.join(', ') + ') הם קירוב בלבד, מתוך מפות המוקד — לא לסמוך עליהם להלכה. ' : ''}המפות עצמן הן התמונות של מוקד העירוב (בלשונית "רשימה").</p>`);
  }
  // תוסף אחר (כיוון תפילה / מקוואות) מבקש לפתוח את המפה באותו מקום (מפה משותפת, Prayer-pointA#11)
  if (window.MapUI && window.MapUI.onOpenMap) window.MapUI.onOpenMap(p => { state.mapSel = null; state.mapCenter = p.lat != null ? [p.lat, p.lon] : null; state.tab = 'map'; mapKey = ''; showTab(); });
  function openMapDev(sel) { state.mapSel = typeof sel === 'string' && sel ? sel : null; state.tab = 'map'; showTab(); saveView(); }
  // ── מצב מנותק באוצריא: מסתירים כל קישור החוצה (טלפונים ואימיילים נשארים) ──
  // 1.3.0 (#2): רק לפי ההגדרה "ללא גישה לאינטרנט" (isOfflineMode), לא לפי זיהוי רשת בפועל —
  // מי שכרגע בלי רשת עדיין יכול להעתיק קישור.
  function setOffline(v) { const was = state.offline; state.offline = v === true; document.body.classList.toggle('offline', state.offline); if (was !== state.offline) { mapKey = ''; showTab(); } }
  async function recheckOnline(payload) {
    if (payload && payload.connectivity && payload.connectivity.isOfflineMode != null) return setOffline(payload.connectivity.isOfflineMode);
    if (!hasOtz) return;
    const c = await call('app.getConnectivity'); if (c && c.success && c.data && c.data.isOfflineMode != null) setOffline(c.data.isOfflineMode);
  }
  // קישורים בסגנון [טקסט](כתובת) ביומן השינויים (מוסתרים במצב מנותק)
  const mdLinks = t => esc(t).replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, (m, a, u) => state.offline ? a : `<button class="btn text" data-url="${u}">${a}</button>`);
  function openInfo() {
    $('#infoScrim').classList.remove('full'); $('#infoScrim').style.top = '';
    $('#infoTitle').textContent = 'אודות התוסף';
    $('#infoBody').innerHTML = `
      <p>גבולות עירוב ${VERSION} · מאת לזש"ק לרמב"ר ומבק"י. המפות ורשימת האחראים מאתר <b>מוקד העירוב</b>, באישורם.
      מידע על כשרות ובירור לפני שבת — בכפתור "מידע הלכתי".</p>
      <div class="levels">
        <div class="level done"><span class="dot">1</span><div><b>מפות ואחראים</b>${D.maps.length} מפות ו־${D.areas.length} אחראים, בלי צורך באינטרנט.</div></div>
        <div class="level part"><span class="dot">2</span><div><b>קווי עירוב על מפת רחובות</b>בוצע חלקית (1.9.0): ${Object.keys(LINES).length} עירובים משורטטים בקירוב מתוך מפות המוקד — ${esc([...new Set(Object.values(LINES).map(l => l.city))].join(', '))}. השכבה "${LINE_LAYER}" בלשונית המפה, עם קישור למפה המקורית ולאחראי. ערים נוספות יתווספו בהדרגה.</div></div>
        <div class="level part"><span class="dot">3</span><div><b>בדיקת כתובת</b>בוצע חלקית (1.9.0): חיפוש כתובת עם מספר בית במפה מראה אם היא בתוך קו העירוב, סמוכה לגבול או מחוצה לו — בערים שיש להן קו. הקובע תמיד: המפה המקורית והאחראי.</div></div>
        <div class="level part"><span class="dot">4</span><div><b>עדכונים מהמוקד</b>בוצע חלקית: גרסה חדשה בכל שבוע שבו המקורות השתנו, ומ-1.9.0 — אם המוקד מחליף מפה שיש לה קו, הקו יורד אוטומטית עד שישורטט מחדש (שלא יוצג קו ישן). בהמשך: משיכה מתוך התוסף, כשאוצריא תאפשר.</div></div>
      </div>
      <p>קיצורי מקשים: <b>/</b> חיפוש · במציג: <b>+ / -</b> הגדלה, <b>0</b> התאמה, <b>R</b> סיבוב, <b>PageUp/PageDown</b> דפדוף בחוברת, <b>Esc</b> סגירה.
      בספר פתוח: <b>Ctrl+Alt+E</b> פותח את התוסף (אפשר לשנות בהגדרות → קיצורי מקשים).</p>
      <p><label class="switch"><input type="checkbox" data-toggle-toolbar ${state.settings.toolbar ? 'checked' : ''}> כפתור "גבולות עירוב" בסרגל העיון</label></p>
      <p class="link-mode">קישור חיצוני:
        <label><input type="radio" name="linkMode" value="open" ${state.linkMode === 'open' ? 'checked' : ''}> פתיחה בדפדפן</label>
        <label><input type="radio" name="linkMode" value="copy" ${state.linkMode === 'copy' ? 'checked' : ''}> העתקה</label></p>
      <p>הנתונים עודכנו ב־${esc(D.built)}.</p>
      ${window.AchatPromo ? '<h3 class="info-h">המלצה</h3>' + AchatPromo.aboutHtml() + AchatPromo.settingsHtml() : ''}
      <h3 class="info-h">שליחת תגובה</h3>
      <textarea id="fbText" rows="3" placeholder="הערה, תיקון או שכונה חסרה — נשלח דרך אוצריא למפתחת"></textarea>
      <div class="sheet-actions"><button class="btn filled" id="fbSend">שליחה</button></div>
      <h3 class="info-h">בתכנון</h3>
      <ul class="tasks">${OPEN_TASKS.map(t => `<li>${esc(t)}</li>`).join('')}</ul>
      <h3 class="info-h">המפה המשותפת</h3>
      <p>לשונית המפה משותפת לארבעה תוספים: כיוון תפילה, מקוואות, גבולות עירוב ועינים למקרא. הקוד במקום אחד (Hadabadra/Mikvaot, תיקייה shared), וכל תיקון מגיע לכולם. בכפתור השכבות שעל המפה פותחים את השכבות של התוספים האחרים באותו מקום.</p>
      ${state.offline ? '' : `<p>${[['הסבר על המפה המשותפת', 'https://github.com/Hadabadra/Mikvaot/blob/main/shared/map-core/SHARED-MAP.md'], ['הקוד של המפה', 'https://github.com/Hadabadra/Mikvaot/tree/main/shared/map-core'], ['כיוון תפילה בחנות', 'https://otzaria.org/plugins/6ac80057bd083fcc18562fa0'], ['מקוואות בחנות', 'https://otzaria.org/plugins/6ad9c9c92917198d739939b7'], ['עינים למקרא בחנות', 'https://otzaria.org/plugins/6a671db1dd175558ae6f20ac']].map(([a, u]) => `<button class="btn text" data-url="${u}">${a}</button>`).join(' ')}</p>`}
      <details class="cl" open><summary><b>יומן שינויים</b> · גרסה ${VERSION}</summary>
        ${CHANGELOG.map((c, i) => `<p><b>${esc(c[0])}</b></p><ul>${c[1].map(t => `<li>${mdLinks(t)}</li>`).join('')}</ul>`).join('')}</details>
      <p><button class="btn tonal" data-mapdev>מפה</button></p>
      <p><button class="btn text" data-url="${REPO}/blob/main/gvulot-eruv-docs/PLAN.md">התכנון המלא בגיטהאב</button>
      <button class="btn text" data-url="${REPO}/releases">כל הגרסאות</button>
      <button class="btn text" data-url="https://otzaria.org/forum/post/45390">שרשור הדיווחים בפורום אוצריא</button></p>`;
    $('#fbSend').onclick = sendFeedback;
    if (window.AchatPromo) AchatPromo.bind($('#infoBody'));
    $('#infoScrim').hidden = false; $('#infoClose').focus();
  }

  // ── אירועים כלליים ─────────────────────────────────────────
  document.addEventListener('input', e => {
    if (e.target.id !== 'abQ') return;
    state.abq = e.target.value; const pos = e.target.selectionStart;
    $('#main').innerHTML = abroadView(); const n = $('#abQ'); n.focus(); try { n.setSelectionRange(pos, pos); } catch (er) {}
  });
  document.addEventListener('change', e => {
    if (e.target.name !== 'linkMode') return;
    state.linkMode = e.target.value === 'copy' ? 'copy' : 'open';
    call('storage.set', { key: 'linkMode', value: state.linkMode });
    toast(state.linkMode === 'copy' ? 'קישורים חיצוניים יועתקו ללוח' : 'קישורים חיצוניים ייפתחו בדפדפן אחרי אישור');
  });
  // קישור <a> רגיל לא ינווט בתוך ה-iframe ולא ייפתח ב-_blank: הכל עובר דרך openLink (אישור + app.openUrl / העתקה)
  document.addEventListener('click', e => {
    const a = e.target.closest && e.target.closest('a[href]'); if (!a) return;
    const h = a.getAttribute('href'); if (!/^https?:/i.test(h)) return;
    e.preventDefault(); e.stopPropagation(); openLink(h);
  }, true);
  document.addEventListener('click', e => {
    const t = e.target.closest('[data-open-map],[data-city],[data-phone],[data-fav],[data-url],[data-add-map],[data-del-map],[data-src],[data-doc],[data-view],[data-abroad],[data-hosp],[data-sec],[data-toggle-toolbar],[data-mapdev],[data-line]');
    if (!t) return;
    if (t.dataset.line) return openLine(t.dataset.line);
    if (t.dataset.doc) { $('#infoScrim').hidden = true; openDoc(t.dataset.doc, 0, t.dataset.from ? { from: +t.dataset.from, to: +t.dataset.to, title: t.dataset.part } : null); }
    else if (t.dataset.sec) goSec(t.dataset.sec);
    else if (t.dataset.view) goView(t.dataset.view);
    else if (t.dataset.abroad) { state.abroadC = t.dataset.abroad; state.abq = ''; state.view = 'abroad'; state.q = ''; $('#q').value = ''; render(); }
    else if (t.dataset.hosp) { state.hosp = t.dataset.hosp; state.view = 'hosp'; state.q = ''; $('#q').value = ''; render(); }
    else if (t.dataset.mapdev !== undefined) { $('#infoScrim').hidden = true; openMapDev(t.dataset.mapdev || null); }
    else if (t.dataset.toggleToolbar !== undefined) toggleToolbar();
    else if (t.dataset.addMap !== undefined) addOwnMap(t.dataset.addMap);
    else if (t.dataset.delMap) delOwnMap(t.dataset.delMap);
    else if (t.dataset.openMap) openMap(t.dataset.openMap);
    else if (t.dataset.fav) toggleFav(t.dataset.fav);
    else if (t.dataset.city) goCity(t.dataset.city);
    else if (t.dataset.phone) copyPhone(t.dataset.phone);
    else if (t.dataset.url) openLink(t.dataset.url);
    else if (t.dataset.src) openSrc(t.dataset.src, t.dataset.ref);
  });
  let qT;
  $('#q').addEventListener('input', e => { clearTimeout(qT); qT = setTimeout(() => { state.q = e.target.value; render(); }, 120); });
  $('#q').addEventListener('keydown', e => {
    if (e.key === 'Enter') { const f = $('#main [data-city], #main [data-open-map]'); if (f) f.click(); }
    if (e.key === 'Escape') { e.target.value = ''; state.q = ''; render(); }
  });
  $('#btnHome').onclick = goHome;
  $('#btnDocs').onclick = () => goView('docs');
  $('#btnInfo').onclick = openInfo;
  $('#viewTabs').onclick = e => { const b = e.target.closest('[data-tab]'); if (!b) return; state.tab = b.dataset.tab; if (state.tab === 'list') state.mapSel = null; showTab(); saveView(); };
  $('#btnHal').onclick = openHalacha;
  $('#btnLoc').onclick = locateMe;
  $('#infoClose').onclick = () => { $('#infoScrim').hidden = true; };
  $('#infoScrim').addEventListener('click', e => { if (e.target.id === 'infoScrim') e.target.hidden = true; });
  document.addEventListener('keydown', e => {
    const inField = /INPUT|TEXTAREA/.test(document.activeElement.tagName);
    if (!$('#viewer').hidden) {
      if (e.key === 'Escape') closeMap();
      else if (V.doc && (e.key === 'PageDown' || (e.key === 'ArrowLeft' && !inField))) { e.preventDefault(); turn(1); }
      else if (V.doc && (e.key === 'PageUp' || (e.key === 'ArrowRight' && !inField))) { e.preventDefault(); turn(-1); }
      else if (inField) return;
      else if (e.key === '+' || e.key === '=') zoomAt(1.4, ...center(), true);
      else if (e.key === '-') zoomAt(1 / 1.4, ...center(), true);
      else if (e.key === '0') fit(true);
      else if (e.key === 'r' || e.key === 'R' || e.key === 'ר') { V.r = (V.r + 90) % 360; fit(true); }
      return;
    }
    if (e.key === 'Escape' && !$('#infoScrim').hidden) { $('#infoScrim').hidden = true; return; }
    if (e.key === '/' && !inField) { e.preventDefault(); $('#q').focus(); }
  });

  // ── שילוב באוצריא: כפתור בסרגל העיון, קיצור מקשים, השהיה ──────
  async function toggleToolbar() {
    state.settings.toolbar = !state.settings.toolbar;
    await call('storage.set', { key: 'hideToolbar', value: !state.settings.toolbar });
    toast(state.settings.toolbar ? 'הכפתור יוצג בסרגל העיון' : 'הכפתור הוסתר מסרגל העיון');
  }
  // פתיחה מתוך ספר: במסכת עירובין ובהלכות עירובין/תחומין — ישר למאמרי התחום והחוברות
  function fromBook(d) {
    const b = (d && (d.currentBook || d.currentRef)) || '';
    if (/עירובין|תחומין|תחום שבת|שצו|שצז|שצח|שצט|ת"ו/.test(b)) goView('docs');
    else if (state.view === 'home' && state.recent[0]) goCity(state.recent[0]);
  }
  let suspendedSrc = null;
  function onSuspend() { if (!$('#viewer').hidden && img.src) { suspendedSrc = img.src; img.removeAttribute('src'); } }
  function onResume() { if (suspendedSrc && !$('#viewer').hidden) { img.src = suspendedSrc; } suspendedSrc = null; }

  // ── אתחול ───────────────────────────────────────────────────
  async function start(payload) {
    if (payload && payload.theme) applyTheme(payload.theme);
    else { const r = await call('app.getTheme'); if (r.success) applyTheme(r.data); }
    await loadPrefs();
    await loadOwnMaps();
    const rs = payload && payload.restore && payload.restore.state;
    if (rs && rs.tab) state.tab = rs.tab;
    await recheckOnline(payload);
    addEventListener('focus', () => recheckOnline());
    if (hasOtz) Otzaria.on('plugin.resumed', () => recheckOnline());
    if (hasOtz) Otzaria.on('settings.changed', () => recheckOnline()); // אין אירוע קישוריות ייעודי; isOfflineMode נקרא מחדש בכל קריאה
    if (rs && rs.view) { state.view = rs.view; state.city = byCity.has(rs.city) ? rs.city : null; state.abroadC = rs.abroadC || ''; if (rs.view === 'city' && !state.city) state.view = 'home'; }
    render();
    $('#q').focus();
    // דיווח תקלות אוטומטי (רכיב משותף shared/report): תקלה שלא טופלה ← הצעה לשלוח דיווח קריסה דרך מערכת הדיווחים של אוצריא
    if (window.CrashReport) CrashReport.init({ call, plugin: 'גבולות עירוב', version: VERSION });
    // המלצה על "אחת שאלתי" (רכיב משותף מ-KLI-MULON shared/promo): פופאפ בפתיחה מתפריט הכלים, לכל היותר פעם בשבוע
    if (window.AchatPromo) { try { AchatPromo.init({ call, plugin: 'גבולות עירוב', links: { gh: REPO + '/releases/latest', store: 'https://otzaria.org/plugins/6ad93b5d2917198d73992a9d' } }); AchatPromo.maybeShow(); } catch (e) {} }
  }
  if (hasOtz) {
    Otzaria.on('plugin.boot', start);
    Otzaria.on('theme.changed', applyTheme);
    // 1.9.0 (אוצריא 0.9.98.3): כפתור בשדות החיפוש של אוצריא (מופיע רק כשיש טקסט) — מעביר את הטקסט לחיפוש בתוסף
    Otzaria.on('search.fieldAction.invoked', p => {
      if (!p || p.actionId !== 'find-eruv') return;
      const t = String(p.selectionEnd > p.selectionStart ? p.text.slice(p.selectionStart, p.selectionEnd) : p.text || '').trim();
      try { Otzaria.call('search.endFieldSession', { sessionId: p.sessionId }); } catch (e) {}
      if (!t) return; closeMap(); state.tab = 'list'; $('#q').value = t; state.q = t; showTab(); render(); call('plugin.openSelf');
    });
    Otzaria.on('reader.toolbar_item_clicked', fromBook);
    Otzaria.on('app.command', p => { if (p && p.command === 'open') call('plugin.openSelf'); });
    Otzaria.on('plugin.suspended', onSuspend);
    Otzaria.on('plugin.resumed', onResume);
  } else {
    start(null);
  }
})();
