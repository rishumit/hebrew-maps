// Photo gallery for site and trail cards: a scrollable strip of thumbnails and a
// full-screen viewer (keyboard: ←/→, Esc). Every photo carries its credit line –
// author, licence and a link to the source page (Wikimedia Commons / Mapillary),
// as the CC licences require.

import { svg } from './icons.js';
import { esc } from './util.js';

function credit(p, { long = false } = {}) {
  const parts = [];
  if (p.author) parts.push(esc(p.author));
  if (p.license) {
    parts.push(p.license_url
      ? `<a href="${esc(p.license_url)}" target="_blank" rel="noopener license">${esc(p.license)}</a>`
      : esc(p.license));
  }
  const src = p.source_he || 'Wikimedia Commons';
  parts.push(p.page_url
    ? `<a href="${esc(p.page_url)}" target="_blank" rel="noopener">${esc(src)}<span class="sr-only"> (נפתח בלשונית חדשה)</span></a>`
    : esc(src));
  return `${long && p.title ? `<span class="g-title">${esc(p.title)}</span> · ` : ''}צילום: ${parts.join(' · ')}`;
}

/** HTML for the strip (empty string when there are no photos). */
export function galleryHtml(photos, name) {
  if (!photos?.length) return '';
  const first = photos[0];
  if (photos.length === 1) {
    return `<figure class="gallery single">
      <button type="button" class="g-open" data-g="0" aria-label="הגדלת התמונה: ${esc(first.title || name)}">
        <img class="card-hero" src="${esc(first.src)}" alt="${esc(first.title || name)}" loading="lazy" referrerpolicy="no-referrer"></button>
      <figcaption class="card-credit">${credit(first)}</figcaption></figure>`;
  }
  const items = photos.map((p, i) => `<li><button type="button" class="g-open" data-g="${i}"
      aria-label="תמונה ${i + 1} מתוך ${photos.length}${p.title ? `: ${esc(p.title)}` : ''}">
      <img src="${esc(p.src)}" alt="" loading="${i < 3 ? 'eager' : 'lazy'}" referrerpolicy="no-referrer"></button></li>`).join('');
  return `<section class="gallery" aria-label="תמונות של ${esc(name)} (${photos.length})">
    <ul class="g-strip">${items}</ul>
    <div class="card-credit g-count">${photos.length} תמונות · ${credit(first)}</div></section>`;
}

/** Wire clicks; broken images are removed from the strip. */
export function wireGallery(root, photos, name) {
  if (!photos?.length) return;
  const live = photos.map(() => true);
  root.querySelectorAll('.gallery img').forEach((img) => {
    img.addEventListener('error', () => {
      const btn = img.closest('.g-open');
      const i = Number(btn?.dataset.g);
      live[i] = false;
      const li = btn?.closest('li');
      if (li) li.remove();
      else btn?.closest('.gallery')?.remove();
      if (!root.querySelector('.gallery .g-open')) root.querySelector('.gallery')?.remove();
    }, { once: true });
  });
  root.querySelectorAll('.gallery .g-open').forEach((b) =>
    b.addEventListener('click', () => openViewer(photos, Number(b.dataset.g), name, live, b)));
}

function openViewer(photos, start, name, live, opener) {
  const dlg = document.createElement('dialog');
  dlg.className = 'g-viewer';
  dlg.setAttribute('aria-label', `תמונות: ${name}`);
  dlg.innerHTML = `
    <div class="gv-top">
      <span class="gv-pos" aria-live="polite"></span>
      <button type="button" class="icon-btn" data-gv="close" aria-label="סגירת התמונות">${svg('x')}</button>
    </div>
    <div class="gv-stage">
      <button type="button" class="gv-nav gv-prev" data-gv="prev" aria-label="התמונה הקודמת">${svg('chevron', { size: 28 })}</button>
      <img class="gv-img" alt="" referrerpolicy="no-referrer">
      <button type="button" class="gv-nav gv-next" data-gv="next" aria-label="התמונה הבאה">${svg('chevron', { size: 28 })}</button>
    </div>
    <p class="gv-credit"></p>`;
  document.body.appendChild(dlg);
  const idx = photos.map((_, i) => i).filter((i) => live[i]);
  let k = Math.max(0, idx.indexOf(start));
  const img = dlg.querySelector('.gv-img');
  const show = () => {
    const p = photos[idx[k]];
    img.src = p.full || p.src;
    img.alt = p.title || name;
    dlg.querySelector('.gv-pos').textContent = `${k + 1} / ${idx.length}`;
    dlg.querySelector('.gv-credit').innerHTML = credit(p, { long: true });
    dlg.querySelector('.gv-prev').hidden = dlg.querySelector('.gv-next').hidden = idx.length < 2;
  };
  img.addEventListener('error', () => {
    const p = photos[idx[k]];
    if (p.full && img.src.indexOf(p.src) === -1) img.src = p.src; // full size blocked → thumbnail
  });
  const step = (d) => { k = (k + d + idx.length) % idx.length; show(); };
  // RTL: "next" sits on the left
  dlg.querySelector('[data-gv="next"]').addEventListener('click', () => step(1));
  dlg.querySelector('[data-gv="prev"]').addEventListener('click', () => step(-1));
  dlg.querySelector('[data-gv="close"]').addEventListener('click', () => dlg.close());
  dlg.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowLeft') { step(1); e.preventDefault(); }
    if (e.key === 'ArrowRight') { step(-1); e.preventDefault(); }
  });
  dlg.addEventListener('click', (e) => { if (e.target === dlg) dlg.close(); });
  dlg.addEventListener('close', () => { dlg.remove(); opener?.focus(); });
  show();
  dlg.showModal();
  dlg.querySelector('[data-gv="close"]').focus();
}

/** Long texts (Wikipedia / Wikivoyage): first paragraph, "read more" for the rest. */
export function textsHtml(texts, fallback, firstTitle = 'על המקום') {
  if (!texts?.length) {
    return fallback ? `<div class="desc"><p style="margin:0">${esc(fallback.text)}</p>${fallback.url
      ? `<a class="src" href="${esc(fallback.url)}" target="_blank" rel="noopener">מתוך ויקיפדיה<span class="sr-only"> (נפתח בלשונית חדשה)</span></a>` : ''}</div>` : '';
  }
  return texts.map((t, n) => {
    const paras = t.paragraphs?.length ? t.paragraphs : [t.text];
    const more = paras.length > 1 || paras[0].length > 420;
    const id = `txt-${n}`;
    return `<section class="desc rich-text" aria-labelledby="${id}-h">
      <h3 class="subtitle" id="${id}-h">${n === 0 ? esc(firstTitle) : `עוד מ${esc(t.source_he)}`}</h3>
      <div class="rt-body${more ? ' clamp' : ''}" id="${id}">${paras.map((p) => `<p>${esc(p)}</p>`).join('')}</div>
      ${more ? `<button type="button" class="link-btn" data-more="${id}" aria-expanded="false" aria-controls="${id}">קריאת עוד</button>` : ''}
      <div class="src">מתוך <a href="${esc(t.url)}" target="_blank" rel="noopener">${esc(t.source_he)}: ${esc(t.title)}<span class="sr-only"> (נפתח בלשונית חדשה)</span></a>
        ${t.license ? ` · ${esc(t.license)}` : ''}</div>
    </section>`;
  }).join('');
}

export function wireTexts(root) {
  root.querySelectorAll('[data-more]').forEach((b) => b.addEventListener('click', () => {
    const body = root.querySelector('#' + b.dataset.more);
    const open = b.getAttribute('aria-expanded') === 'true';
    body.classList.toggle('clamp', open);
    b.setAttribute('aria-expanded', String(!open));
    b.textContent = open ? 'קריאת עוד' : 'הצגת פחות';
  }));
}

/** A link that opens a search for the place in Hamichlol (in the viewer's own browser – no automated access). */
export function michlolHtml(name) {
  if (!name) return '';
  const url = `https://www.hamichlol.org.il/w/index.php?search=${encodeURIComponent(name)}`;
  return `<p class="r-sub more-photos"><a href="${esc(url)}" target="_blank" rel="noopener">${svg('book', { size: 14 })} מידע על ${esc(name)} במכלול<span class="sr-only"> (נפתח בלשונית חדשה)</span></a></p>`;
}
