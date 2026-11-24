// שורות גלילה אופקיות עם חץ בסוף השורה וטשטוש (ראו hscroll.css).
// שימוש: initHScroll('.actions, .g-strip')  – פעם אחת; חל גם על אלמנטים שנוספים אחר כך (כרטיסים נבנים מחדש).

const SELECTORS_DEFAULT = '[data-hscroll]';

export function enhanceHScroll(track, label = 'הצג עוד') {
  if (!track || track.closest('.hs') || track.dataset.hsDone) return;
  track.dataset.hsDone = '1';
  const wrap = document.createElement('div');
  wrap.className = 'hs';
  track.parentNode.insertBefore(wrap, track);
  wrap.appendChild(track);
  track.classList.add('hs-track');

  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'hs-next';
  btn.setAttribute('aria-label', label);
  btn.tabIndex = -1;                       // המקלדת גוללת את השורה עצמה; החץ הוא עזר לעכבר/מגע
  btn.innerHTML = '<span aria-hidden="true"></span>';
  wrap.appendChild(btn);

  const rtl = () => getComputedStyle(track).direction === 'rtl';
  const update = () => {
    // ב-RTL scrollLeft שלילי; Math.abs מנרמל את שני הכיוונים
    const left = track.scrollWidth - track.clientWidth - Math.abs(track.scrollLeft);
    const more = left > 2;
    wrap.classList.toggle('hs-more', more);
  };
  btn.addEventListener('click', () => {
    track.scrollBy({ left: (rtl() ? -1 : 1) * Math.max(120, track.clientWidth * 0.8) });
  });
  track.addEventListener('scroll', update, { passive: true });
  new ResizeObserver(update).observe(track);
  new MutationObserver(update).observe(track, { childList: true, subtree: true });
  track.addEventListener('load', update, true);   // תמונות שנטענות מאוחר משנות את הרוחב
  update();
}

export function initHScroll(selector = SELECTORS_DEFAULT, root = document) {
  const run = (scope) => {
    if (scope.matches?.(selector)) enhanceHScroll(scope);
    scope.querySelectorAll?.(selector).forEach((el) => enhanceHScroll(el));
  };
  run(root);
  new MutationObserver((muts) => {
    for (const m of muts) m.addedNodes.forEach((n) => n.nodeType === 1 && run(n));
  }).observe(root === document ? document.body : root, { childList: true, subtree: true });
}
