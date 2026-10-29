// מפות ישראל – product site. Progressive enhancement only: every link and section works without this file.
//  * header shadow on scroll + the small-screen menu
//  * the screenshot tour as ARIA tabs (without JS all screenshots are shown one after another)
//  * gentle reveal-on-scroll (off with prefers-reduced-motion)
//  * the newest release (version / size / date) and installer downloads across stable versions from the GitHub API.
//    Download buttons already point at releases/latest/download/HebrewMaps-Setup.exe, so nothing breaks when the
//    API is blocked (common behind filtered networks) or rate limited.
(() => {
  'use strict';

  const REPO = 'rishumit/hebrew-maps';
  const STABLE_ASSET = 'HebrewMaps-Setup.exe';
  const CACHE_KEY = 'hm:latest-release';
  const CACHE_MS = 60 * 60 * 1000;
  const root = document.documentElement;
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // ------------------------------------------------------------ in-page anchors
  // Sections below the fold use content-visibility:auto (cheap first paint). Their real heights are unknown
  // until rendered, so lay everything out before jumping to an anchor, or the jump lands in the wrong place.
  const layoutAll = () => root.classList.add('cv-off');
  if (location.hash) layoutAll();
  document.addEventListener('click', (e) => {
    const a = e.target.closest && e.target.closest('a[href*="#"]');
    if (a && a.pathname === location.pathname && a.hash) layoutAll();
  }, true);
  window.addEventListener('hashchange', layoutAll);

  // ------------------------------------------------------------ reveal on scroll
  root.classList.add('js');
  const reveals = document.querySelectorAll('.reveal');
  if ('IntersectionObserver' in window && !reduceMotion) {
    const io = new IntersectionObserver((entries) => {
      for (const e of entries) {
        if (e.isIntersecting) {
          e.target.classList.add('in');
          io.unobserve(e.target);
        }
      }
    }, { rootMargin: '0px 0px -8% 0px', threshold: 0.08 });
    const vh = window.innerHeight;
    reveals.forEach((el) => {
      if (el.getBoundingClientRect().top < vh) el.classList.add('in'); // already on screen: no flash
      else io.observe(el);
    });
  } else {
    reveals.forEach((el) => el.classList.add('in'));
  }
  window.addEventListener('beforeprint', () => { layoutAll(); reveals.forEach((el) => el.classList.add('in')); });

  // ------------------------------------------------------------ header
  const header = document.querySelector('.site-header');
  if (header) {
    const onScroll = () => header.classList.toggle('scrolled', window.scrollY > 8);
    onScroll();
    window.addEventListener('scroll', onScroll, { passive: true });

    const btn = header.querySelector('.menu-btn');
    const links = header.querySelector('.nav-links');
    if (btn && links) {
      const setOpen = (open) => {
        header.classList.toggle('open', open);
        btn.setAttribute('aria-expanded', String(open));
        btn.setAttribute('aria-label', open ? 'סגירת התפריט' : 'תפריט');
        const use = btn.querySelector('use');
        if (use) use.setAttribute('href', `${use.getAttribute('href').split('#')[0]}#${open ? 'close' : 'menu'}`);
      };
      btn.addEventListener('click', () => setOpen(!header.classList.contains('open')));
      links.addEventListener('click', (e) => { if (e.target.closest('a')) setOpen(false); });
      document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && header.classList.contains('open')) {
          setOpen(false);
          btn.focus();
        }
      });
    }
  }

  // ------------------------------------------------------------ tour tabs
  document.querySelectorAll('[data-tour]').forEach((tour) => {
    const list = tour.querySelector('[role="tablist"]');
    if (!list) return;
    const tabs = [...list.querySelectorAll('[role="tab"]')];
    const panels = tabs.map((t) => document.getElementById(t.getAttribute('aria-controls')));
    list.hidden = false;
    const select = (i, focus) => {
      tabs.forEach((t, j) => {
        const on = i === j;
        t.setAttribute('aria-selected', String(on));
        t.tabIndex = on ? 0 : -1;
        panels[j].hidden = !on;
      });
      panels[i].tabIndex = 0;
      if (focus) {
        tabs[i].focus();
        tabs[i].scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: reduceMotion ? 'auto' : 'smooth' });
      }
    };
    tabs.forEach((t, i) => {
      t.addEventListener('click', () => select(i, false));
      t.addEventListener('keydown', (e) => {
        // RTL: the next tab is to the left
        const n = tabs.length;
        let k = null;
        if (e.key === 'ArrowLeft') k = (i + 1) % n;
        else if (e.key === 'ArrowRight') k = (i - 1 + n) % n;
        else if (e.key === 'Home') k = 0;
        else if (e.key === 'End') k = n - 1;
        if (k !== null) {
          e.preventDefault();
          select(k, true);
        }
      });
    });
    select(0, false);
  });

  // ------------------------------------------------------------ latest release
  const metas = document.querySelectorAll('[data-dl-meta]');
  const links = document.querySelectorAll('a[data-dl-link][href*="/releases/"]');
  if (!metas.length && !links.length) return;

  const fmtSize = (bytes) => `${(bytes / 1048576).toLocaleString('he-IL', { maximumFractionDigits: 0 })} MB`;
  const fmtDate = (iso) => new Date(iso).toLocaleDateString('he-IL', { day: 'numeric', month: 'long', year: 'numeric' });

  function apply(rel) {
    // only an app release (vX.Y.Z) – never a data release that GitHub happens to call "latest"
    if (!rel || !/^v\d/i.test(rel.tag)) return;
    const version = rel.tag.replace(/^v/i, '');
    metas.forEach((m) => {
      const v = m.querySelector('[data-dl-version]');
      const s = m.querySelector('[data-dl-size]');
      const d = m.querySelector('[data-dl-date]');
      if (v) v.innerHTML = `גרסה <bdi>${version.replace(/[^\w.\-]/g, '')}</bdi>`;
      if (s && rel.size) s.innerHTML = `<bdi>${fmtSize(rel.size)}</bdi>&nbsp;· <bdi>Windows 10/11</bdi> ‏(64 ביט)`;
      if (d && rel.date) d.textContent = `עודכנה ב-${fmtDate(rel.date)}`;
    });
    if (rel.url) links.forEach((a) => { a.href = rel.url; });
  }

  let cached = null;
  try {
    cached = JSON.parse(sessionStorage.getItem(CACHE_KEY) || 'null');
  } catch { /* storage blocked */ }
  if (cached && Date.now() - cached.at < CACHE_MS) {
    apply(cached.rel);
    return;
  }

  const ctrl = 'AbortController' in window ? new AbortController() : null;
  const timer = setTimeout(() => ctrl && ctrl.abort(), 6000);
  fetch(`https://api.github.com/repos/${REPO}/releases/latest`, {
    headers: { Accept: 'application/vnd.github+json' },
    signal: ctrl ? ctrl.signal : undefined,
    referrerPolicy: 'no-referrer',
    credentials: 'omit',
  })
    .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
    .then((data) => {
      const assets = Array.isArray(data.assets) ? data.assets : [];
      const exe = assets.find((a) => a.name === STABLE_ASSET) || assets.find((a) => /^HebrewMaps-Setup.*\.exe$/i.test(a.name));
      const rel = {
        tag: String(data.tag_name || ''),
        date: data.published_at || (exe && exe.updated_at) || null,
        size: exe ? exe.size : 0,
        // keep the stable "latest" link when the stable asset exists; otherwise the versioned file of that release
        url: exe && exe.name !== STABLE_ASSET && /^https:\/\/github\.com\//.test(exe.browser_download_url) ? exe.browser_download_url : null,
      };
      apply(rel);
      try {
        sessionStorage.setItem(CACHE_KEY, JSON.stringify({ at: Date.now(), rel }));
      } catch { /* storage blocked */ }
    })
    .catch(() => { /* API blocked / offline / rate limited – the static link and text stay */ })
    .finally(() => clearTimeout(timer));
})();

// ------------------------------------------------------------ installer downloads across all app versions
// GitHub's per-asset download_count is summed for stable vX.Y.Z releases only; data bundles are excluded.
(() => {
  const box = document.querySelector('[data-download-total]');
  if (!box) return;

  const REPO = 'rishumit/hebrew-maps';
  const KEY = 'hm:installer-downloads-all-versions';
  const CACHE_MS = 60 * 60 * 1000;
  const countNode = box.querySelector('[data-download-count]');
  const show = (count) => {
    if (!Number.isSafeInteger(count) || count < 0) return;
    countNode.textContent = count.toLocaleString('he-IL');
    box.hidden = false;
  };

  let cached = null;
  try { cached = JSON.parse(sessionStorage.getItem(KEY) || 'null'); } catch { /* storage blocked */ }
  if (cached && Number.isSafeInteger(cached.count) && Date.now() - cached.at < CACHE_MS) {
    show(cached.count);
    return;
  }

  const ctrl = 'AbortController' in window ? new AbortController() : null;
  const timer = setTimeout(() => ctrl && ctrl.abort(), 12000);
  const headers = { Accept: 'application/vnd.github+json' };
  const options = {
    headers,
    signal: ctrl ? ctrl.signal : undefined,
    referrerPolicy: 'no-referrer',
    credentials: 'omit',
  };

  async function loadAll() {
    let page = 1;
    let total = 0;
    while (true) {
      const response = await fetch(`https://api.github.com/repos/${REPO}/releases?per_page=100&page=${page}`, options);
      if (!response.ok) throw new Error(String(response.status));
      const releases = await response.json();
      for (const release of releases) {
        if (release.prerelease || !/^v\d/i.test(String(release.tag_name || ''))) continue;
        const assets = Array.isArray(release.assets) ? release.assets : [];
        for (const asset of assets) {
          if (/^HebrewMaps-Setup(?:[-_].*)?\.exe$/i.test(String(asset.name || '')) && Number.isSafeInteger(asset.download_count)) {
            total += asset.download_count;
          }
        }
      }
      if (releases.length < 100) return total;
      page += 1;
    }
  }

  loadAll()
    .then((count) => {
      show(count);
      try { sessionStorage.setItem(KEY, JSON.stringify({ at: Date.now(), count })); } catch { /* storage blocked */ }
    })
    .catch(() => { /* API blocked / offline / rate limited – omit the statistic */ })
    .finally(() => clearTimeout(timer));
})();

// ------------------------------------------------------------ offline update file (download.html#offline)
// The weekly data workflow publishes a full offline update and a smaller ZIP for each component.
(() => {
  const box = document.querySelector('[data-off]');
  if (!box) return;
  const REPO = 'rishumit/hebrew-maps';
  const FULL = /^HebrewMaps-Update(?:-part(\d+))?\.zip$/;
  const SINGLE = /^HebrewMaps-Update-(maps|basemap|terrain|transit|media|zmanim)\.zip$/;
  const COMPONENTS = { maps: 'מפה ונקודות עניין', basemap: 'מפת בסיס', terrain: 'טופוגרפיה',
    transit: 'תחבורה ציבורית', media: 'תמונות ותוכן', zmanim: 'זמני תפילות' };
  const fmtSize = (b) => (b >= 1e9
    ? `${(b / 1073741824).toLocaleString('he-IL', { maximumFractionDigits: 1 })} GB`
    : `${(b / 1048576).toLocaleString('he-IL', { maximumFractionDigits: 0 })} MB`);
  const fmtDate = (iso) => new Date(iso).toLocaleDateString('he-IL', { day: 'numeric', month: 'long', year: 'numeric' });
  const ctrl = 'AbortController' in window ? new AbortController() : null;
  const timer = setTimeout(() => ctrl && ctrl.abort(), 6000);
  fetch(`https://api.github.com/repos/${REPO}/releases/tags/data-latest`, {
    headers: { Accept: 'application/vnd.github+json' },
    signal: ctrl ? ctrl.signal : undefined,
    referrerPolicy: 'no-referrer',
    credentials: 'omit',
  })
    .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
    .then((rel) => {
      const assets = (Array.isArray(rel.assets) ? rel.assets : [])
        .filter((a) => /^https:\/\/github\.com\//.test(a.browser_download_url));
      const full = assets.filter((a) => FULL.test(a.name))
        .map((a) => ({ ...a, part: Number((a.name.match(FULL) || [])[1] || 0) }))
        .sort((a, b) => a.part - b.part);
      const singles = assets.filter((a) => SINGLE.test(a.name))
        .sort((a, b) => Object.keys(COMPONENTS).indexOf((a.name.match(SINGLE) || [])[1]) -
          Object.keys(COMPONENTS).indexOf((b.name.match(SINGLE) || [])[1]));
      if (!full.length && !singles.length) return;
      const d = box.querySelector('[data-off-date]');
      const s = box.querySelector('[data-off-size]');
      const when = full[0]?.updated_at || singles[0]?.updated_at || rel.published_at;
      if (d && when) d.textContent = `נתונים מ-${fmtDate(when)}`;
      const link = box.querySelector('[data-off-link]');
      if (full.length) {
        const total = full.reduce((n, a) => n + (a.size || 0), 0);
        if (s && total) s.innerHTML = `<bdi>${fmtSize(total)}</bdi>${full.length > 1 ? ` ב-${full.length} קבצים` : ''}`;
        if (full.length === 1) link.href = full[0].browser_download_url;
        else {
          link.href = full[0].browser_download_url;
          const ul = box.querySelector('[data-off-parts]');
          ul.innerHTML = full.map((a, i) => `<li><a class="btn btn-sm" href="${a.browser_download_url.replace(/"/g, '%22')}">`
            + `קובץ ${i + 1} מתוך ${full.length} · <bdi>${fmtSize(a.size)}</bdi></a></li>`).join('');
          ul.hidden = false;
          link.hidden = true;
        }
      }
      if (singles.length) {
        const ul = box.querySelector('[data-off-components]');
        ul.innerHTML = singles.map((a) => {
          const component = (a.name.match(SINGLE) || [])[1];
          return `<li><a class="btn btn-sm" href="${a.browser_download_url.replace(/"/g, '%22')}">`
            + `${COMPONENTS[component]} · <bdi>${fmtSize(a.size)}</bdi></a></li>`;
        }).join('');
        ul.hidden = false;
        box.querySelector('[data-off-specific-label]').hidden = false;
      }
    })
    .catch(() => { /* API blocked / offline / rate limited – the static link stays */ })
    .finally(() => clearTimeout(timer));
})();
