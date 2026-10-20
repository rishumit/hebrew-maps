// Operator logos (INPA, KKL, bus / rail companies). The server attaches a "brand" object
// ({key, name, short, color, logo}) to POIs and transit legs / departures – see hmaps/core/logos.py.
// Logo files are bundled under /logos/ (never loaded from the internet); an operator without a
// logo file is shown as a small text badge.

import { esc } from './util.js';

/**
 * HTML of an operator logo chip, or '' when there is no brand.
 * @param {{name?: string, short?: string, color?: string, logo?: string|null}|null|undefined} brand
 * @param {{size?: 'sm'|'md', label?: boolean, alt?: string}} [opts]
 *   label: also print the operator name after the chip; alt: overrides the img alt text
 *   (pass '' when the name is already printed next to it).
 */
export function operatorBadge(brand, { size = 'md', label = false, alt } = {}) {
  if (!brand) return '';
  const name = brand.name || brand.short || '';
  const cls = `op-chip${size === 'sm' ? ' sm' : ''}`;
  const altText = alt ?? name;
  const chip = brand.logo
    ? `<span class="${cls}" title="${esc(name)}"><img class="op-logo" src="${esc(brand.logo)}" alt="${esc(altText)}" loading="lazy" decoding="async"></span>`
    : `<span class="${cls} op-text" style="--op-color:${esc(brand.color || '#475569')}" title="${esc(name)}"${altText ? '' : ' aria-hidden="true"'}>${esc(brand.short || name)}</span>`;
  return label ? `<span class="op-with-name">${chip}<span class="op-name">${esc(name)}</span></span>` : chip;
}

/** Same as operatorBadge for transit rows, which carry agency + agency_brand (older servers: agency only). */
export function agencyBadge(row, opts = {}) {
  if (row?.agency_brand) return operatorBadge({ ...row.agency_brand, name: row.agency || row.agency_brand.name }, opts);
  return row?.agency && opts.label ? `<span class="op-name">${esc(row.agency)}</span>` : '';
}
