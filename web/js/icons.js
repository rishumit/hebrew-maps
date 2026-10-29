// Icon set: 24×24 stroke paths (original artwork for this project).
// Used both for inline SVG in the UI and to rasterise map markers via Path2D.

export const ICONS = {
  // --- POI icons ---------------------------------------------------------
  utensils: 'M7 3v8M5 3v4a2 2 0 0 0 4 0V3M7 11v10M17 3c-2 1-3 4-3 7h3v11',
  cup: 'M5 8h11v6a5 5 0 0 1-5 5h-1a5 5 0 0 1-5-5zM16 10h2a2 2 0 0 1 0 4h-2M8 2.5v3M11 2.5v3',
  burger: 'M4 11h16M4 15h16M5 11a7 5 0 0 1 14 0M5 15v1a3 3 0 0 0 3 3h8a3 3 0 0 0 3-3v-1',
  bread: 'M6 20V10a3 3 0 0 1 0-6h12a3 3 0 0 1 0 6v10z',
  icecream: 'M8 10a4 4 0 0 1 8 0zM8 10l4 11 4-11',
  bed: 'M3 18V6M3 13h18v5M21 13a3 3 0 0 0-3-3h-7v3M7 11.5a1.5 1.5 0 1 0 0-.01',
  house: 'M4 11l8-7 8 7M6 9.5V20h12V9.5M10 20v-5h4v5',
  home: 'M4 11l8-7 8 7M6 9.5V20h12V9.5M10 20v-5h4v5',
  tent: 'M3 20L12 4l9 16zM12 20l-3-6h6z',
  bag: 'M5 8h14l-1 12H6zM9 8V6a3 3 0 0 1 6 0v2',
  basket: 'M3 10h18l-2 10H5zM7 10l4-6M17 10l-4-6',
  cart: 'M3 4h2l2.5 11h11L21 7H6.5M9 20a1 1 0 1 0 0-.01M18 20a1 1 0 1 0 0-.01',
  bank: 'M3 10l9-6 9 6M5 10v8M9.5 10v8M14.5 10v8M19 10v8M3 20h18',
  cash: 'M3 7h18v10H3zM12 12m-2.5 0a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0-5 0',
  graduation: 'M2 9l10-5 10 5-10 5zM6 11v5c3 2 9 2 12 0v-5M22 9v6',
  school: 'M4 21V10l8-5 8 5v11M9 21v-6h6v6',
  blocks: 'M4 13h7v7H4zM13 13h7v7h-7zM8.5 4h7v7h-7z',
  book: 'M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2zM4 21a2 2 0 0 1 2-2h13',
  hospital: 'M4 4h16v16H4zM12 8v8M8 12h8',
  health: 'M9 3h6v6h6v6h-6v6H9v-6H3V9h6z',
  ambulance: 'M3 7h11v10H3zM14 10h4l3 3v4h-7M8.5 9.5v5M6 12h5M7 19a1.5 1.5 0 1 0 0-.01M17 19a1.5 1.5 0 1 0 0-.01',
  pill: 'M10.5 3.5a5 5 0 0 1 7 7l-7 7a5 5 0 0 1-7-7zM7 10l7 7',
  tooth: 'M7 3C4 3 3 6 4 9l2 11c1 2 2 1 3-2l1-3h4l1 3c1 3 2 4 3 2l2-11c1-3 0-6-3-6-2 0-3 1-5 1S9 3 7 3z',
  plane: 'M3 13l8-2V5a1.5 1.5 0 0 1 3 0v6l7 2v2l-7-1v4l2 2v1l-3.5-1L9 21v-1l2-2v-4l-8 1z',
  tram: 'M6 4h12v12a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2zM6 11h12M9 21l1.5-3M15 21l-1.5-3M10 2h4',
  train: 'M5 4h14v11a3 3 0 0 1-3 3H8a3 3 0 0 1-3-3zM5 10h14M8 21l2-3M16 21l-2-3M9 14.5h.01M15 14.5h.01',
  bus: 'M4 4h16v13H4zM4 11h16M7 20v-3M17 20v-3M7.5 14.5h.01M16.5 14.5h.01',
  fuel: 'M4 21V5a2 2 0 0 1 2-2h6a2 2 0 0 1 2 2v16M3 21h12M4 10h10M14 9h2a2 2 0 0 1 2 2v6a1.5 1.5 0 0 0 3 0V8l-3-3',
  bolt: 'M13 2L4 14h7l-1 8 9-12h-7z',
  parking: 'M9 18V6h4a3.5 3.5 0 0 1 0 7H9',
  taxi: 'M5 17h14v-5l-2-5H7l-2 5zM5 12h14M7.5 20v-3M16.5 20v-3M10 4h4',
  bike: 'M5.5 17.5m-3.5 0a3.5 3.5 0 1 0 7 0a3.5 3.5 0 1 0-7 0M18.5 17.5m-3.5 0a3.5 3.5 0 1 0 7 0a3.5 3.5 0 1 0-7 0M5.5 17.5L9 9h6l3.5 8.5M9 9l3 8.5H5.5M14 6h2',
  wave: 'M2 12c2-2 4-2 6 0s4 2 6 0 4-2 6 0M2 17c2-2 4-2 6 0s4 2 6 0 4-2 6 0M2 7c2-2 4-2 6 0s4 2 6 0 4-2 6 0',
  tree: 'M12 22v-6M12 3l-6 8h3l-4 5h14l-4-5h3z',
  swing: 'M4 3h16M7 3v18M17 3v18M10 3v9M14 3v9M9 12h6',
  stadium: 'M3 9c0-2 4-4 9-4s9 2 9 4v6c0 2-4 4-9 4s-9-2-9-4zM3 9c0 2 4 4 9 4s9-2 9-4',
  dumbbell: 'M6 7v10M3 9v6M18 7v10M21 9v6M6 12h12',
  swim: 'M2 18c2-1.5 4-1.5 6 0s4 1.5 6 0 4-1.5 6 0M8 14l3-6 4 3 3-2M17 5.5h.01',
  ball: 'M12 12m-9 0a9 9 0 1 0 18 0a9 9 0 1 0-18 0M12 3v18M3 12h18',
  leaf: 'M5 19c0-8 5-14 15-15-1 10-7 15-15 15zM5 19l8-8',
  eye: 'M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12zM12 12m-3 0a3 3 0 1 0 6 0a3 3 0 1 0-6 0',
  drop: 'M12 3s7 7.5 7 12a7 7 0 0 1-14 0c0-4.5 7-12 7-12z',
  mountain: 'M2 20l7-12 4 6 2-3 7 9z',
  cave: 'M2 20c0-9 5-15 10-15s10 6 10 15M8 20c0-4 2-6 4-6s4 2 4 6',
  building: 'M4 21V5l8-3v19M12 9h8v12M3 21h18M7 8h2M7 12h2M7 16h2M15 13h2M15 17h2',
  mail: 'M3 6h18v12H3zM3 7l9 6 9-6',
  shield: 'M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z',
  flame: 'M12 3c1 4 6 6 6 11a6 6 0 0 1-12 0c0-3 2-4 2-6 2 1 3 3 3 3s1-4 1-8z',
  scale: 'M12 3v18M7 21h10M4 7h16M7 7l-3 7a3 3 0 0 0 6 0zM17 7l-3 7a3 3 0 0 0 6 0z',
  people: 'M9 8m-3 0a3 3 0 1 0 6 0a3 3 0 1 0-6 0M3 20c0-3.5 2.7-6 6-6s6 2.5 6 6M16 5a3 3 0 0 1 0 6M18 14c2 .7 3 3 3 6',
  landmark: 'M3 21h18M5 21V10M19 21V10M9 21v-8M15 21v-8M2 10l10-7 10 7z',
  museum: 'M3 21h18M4 18h16M6 18v-8M10 18v-8M14 18v-8M18 18v-8M3 10l9-6 9 6z',
  columns: 'M5 21h14M6 18h12M7 18V8M12 18V8M17 18V8M5 8h14M6 5h12',
  paw: 'M12 14c-3 0-5 3-5 5 0 1.5 1.5 2 2.5 2 1.2 0 1.6-.6 2.5-.6s1.3.6 2.5.6c1 0 2.5-.5 2.5-2 0-2-2-5-5-5zM6 11h.01M18 11h.01M9.5 6h.01M14.5 6h.01',
  dot: 'M12 12m-3.2 0a3.2 3.2 0 1 0 6.4 0a3.2 3.2 0 1 0-6.4 0',
  star: 'M12 3l2.7 5.6 6.2.9-4.5 4.4 1 6.1L12 17l-5.5 3 1-6.1L3 9.5l6.2-.9z',
  frame: 'M4 4h16v16H4zM4 15l5-5 4 4 2-2 5 5',
  info: 'M12 12m-9 0a9 9 0 1 0 18 0a9 9 0 1 0-18 0M12 11v6M12 7.5v.01',
  synagogue: 'M12 2.5l8 14H4zM12 21.5l-8-14h16z',
  pin: 'M12 21s-7-6.5-7-12a7 7 0 0 1 14 0c0 5.5-7 12-7 12zM12 9m-2.5 0a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0-5 0',
  road: 'M8 3L5 21M16 3l3 18M12 4v3M12 10v3M12 16v3',
  // outdoor / hiking (trails & nature layer)
  memorial: 'M10 3h4v14h-4zM6 21h12M8 17h8v4H8z',
  briefcase: 'M3 8h18v12H3zM9 8V5h6v3M3 13h18',
  wrench: 'M14.5 4a4.5 4.5 0 0 0-4.2 6.1L4 16.4V20h3.6l6.3-6.3A4.5 4.5 0 0 0 20 9.5l-3 1-2.5-2.5 1-3z',
  stream: 'M4 4c4 2 0 6 4 8s4 4 2 8M14 4c4 2 0 6 4 8s4 4 2 8',
  cliff: 'M3 21V9l5-5h4v6l4 3v8M16 21h5',
  valley: 'M2 5l7 14h6l7-14M9 19c1-2 5-2 6 0',
  well: 'M5 10h14M7 10v11M17 10v11M5 21h14M6 10l6-6 6 6M12 10v5M10.5 15h3v2h-3z',
  tap: 'M4 9h10a3 3 0 0 1 3 3v2M4 6v6M9 6h-3M17 17c0 1.5-1 2.5-1 3.5a1 1 0 0 0 2 0c0-1-1-2-1-3.5z',
  toilets: 'M7 4.5h.01M17 4.5h.01M5 8h4l-.5 6H8v7H6v-7h-.5zM15 8h4l1.5 7h-2v6h-3v-6h-2zM12 3v18',
  shelter: 'M2 10l10-6 10 6M5 8.5V20M19 8.5V20M3 20h18',
  signpost: 'M12 3v18M12 5h7l2 2-2 2h-7M12 11H5l-2 2 2 2h7M9 21h6',
  watertower: 'M7 3h10v7a5 5 0 0 1-10 0zM9 14l-3 7M15 14l3 7M7.5 18h9',
  hiker: 'M13 3.5h.01M9 21l2.5-7 3 3v4M10 10.5l1.5-3.5 4 2 1.5 3M11.5 7l-2.5 1-1 4M18 9v12',

  // --- UI icons ------------------------------------------------------------
  search: 'M11 11m-7 0a7 7 0 1 0 14 0a7 7 0 1 0-14 0M21 21l-5-5',
  x: 'M6 6l12 12M18 6L6 18',
  directions: 'M12 2l10 10-10 10L2 12zM9 14v-2.5A1.5 1.5 0 0 1 10.5 10H15M13 8l2 2-2 2',
  print: 'M6 9V3h12v6M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2M6 14h12v7H6zM18 12h.01',
  menu: 'M4 6h16M4 12h16M4 18h16',
  layers: 'M12 3l9 5-9 5-9-5zM3 12.5l9 5 9-5M3 16.5l9 5 9-5',
  locate: 'M12 12m-3 0a3 3 0 1 0 6 0a3 3 0 1 0-6 0M12 2v3M12 19v3M2 12h3M19 12h3M12 12m-7 0a7 7 0 1 0 14 0a7 7 0 1 0-14 0',
  plus: 'M12 5v14M5 12h14',
  minus: 'M5 12h14',
  swap: 'M7 4v16M4 17l3 3 3-3M17 20V4M14 7l3-3 3 3',
  car: 'M5 17h14v-5l-2-5H7l-2 5zM5 12h14M7.5 20v-3M16.5 20v-3',
  walk: 'M13 4.5h.01M10 21l2-6 3 3v3M9 12l2-4 4 2 2 3M11 8l-2 1-1 4',
  phone: 'M5 3h4l2 5-2.5 1.5a11 11 0 0 0 6 6L16 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 5a2 2 0 0 1 2-2z',
  globe: 'M12 12m-9 0a9 9 0 1 0 18 0a9 9 0 1 0-18 0M3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18',
  clock: 'M12 12m-9 0a9 9 0 1 0 18 0a9 9 0 1 0-18 0M12 7v5l3 2',
  copy: 'M8 8h12v12H8zM16 8V4H4v12h4',
  share: 'M18 5m-2.5 0a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0-5 0M6 12m-2.5 0a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0-5 0M18 19m-2.5 0a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0-5 0M8.2 10.8l7.6-4.4M8.2 13.2l7.6 4.4',
  back: 'M5 12h14M13 6l6 6-6 6',
  chevron: 'M6 9l6 6 6-6',
  wheelchair: 'M11 4.5h.01M10 8v6h6l2 5M10 11h5M7.5 11.5a5 5 0 1 0 7 6',
  check: 'M5 12l5 5 9-10',
  download: 'M12 4v11M7 10l5 5 5-5M5 20h14',
  moon: 'M20 14A8 8 0 1 1 10 4a6 6 0 0 0 10 10z',
  external: 'M14 4h6v6M20 4l-9 9M18 14v6H4V6h6',
  list: 'M9 6h11M9 12h11M9 18h11M4.5 6h.01M4.5 12h.01M4.5 18h.01',
  keyboard: 'M3 6h18v12H3zM7 10h.01M11 10h.01M15 10h.01M7 14h10',
  alert: 'M12 3l10 18H2zM12 10v5M12 18h.01',
  history: 'M3 12a9 9 0 1 0 3-6.7L3 8M3 3v5h5M12 7v5l3 2',
  filter: 'M4 5h16l-6 8v6l-4-2v-4z',

  // --- maneuvers ----------------------------------------------------------------
  m_straight: 'M12 20V5M6 11l6-6 6 6',
  m_right: 'M6 20v-7a4 4 0 0 1 4-4h9M15 5l4 4-4 4',
  m_left: 'M18 20v-7a4 4 0 0 0-4-4H5M9 5L5 9l4 4',
  m_slight_right: 'M8 20v-6l8-8M11 6h5v5',
  m_slight_left: 'M16 20v-6L8 6M13 6H8v5',
  m_uturn: 'M8 20V9a4 4 0 0 1 8 0v5M12 11l4 4 4-4',
  m_roundabout: 'M12 10m-4 0a4 4 0 1 0 8 0a4 4 0 1 0-8 0M12 14v7M15 7l4-4M15 3h4v4',
  m_arrive: 'M5 21V4h12l-2 4 2 4H5',
  m_depart: 'M12 12m-4 0a4 4 0 1 0 8 0a4 4 0 1 0-8 0',
  start: 'M12 12m-9 0a9 9 0 1 0 18 0a9 9 0 1 0-18 0M10 8.5l5 3.5-5 3.5z',
};

/** Inline SVG markup for an icon (decorative by default). */
export function svg(name, { size = 20, label = null, stroke = 2, cls = '' } = {}) {
  const d = ICONS[name] || ICONS.pin;
  const a11y = label ? `role="img" aria-label="${label}"` : 'aria-hidden="true" focusable="false"';
  return `<svg class="icon ${cls}" ${a11y} width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="${stroke}" stroke-linecap="round" stroke-linejoin="round"><path d="${d}"/></svg>`;
}

/** Icon for a route step. */
export function maneuverIcon(m) {
  if (!m) return 'm_straight';
  const t = m.type, mod = m.modifier || '';
  if (t === 'arrive') return 'm_arrive';
  if (t === 'depart') return 'm_depart';
  if (t && (t.includes('roundabout') || t.includes('rotary'))) return 'm_roundabout';
  if (mod === 'uturn') return 'm_uturn';
  if (mod === 'sharp right' || mod === 'right') return 'm_right';
  if (mod === 'sharp left' || mod === 'left') return 'm_left';
  if (mod === 'slight right') return 'm_slight_right';
  if (mod === 'slight left') return 'm_slight_left';
  return 'm_straight';
}

/**
 * Rasterise a round map marker: coloured disc, white border, white glyph.
 * Returns ImageData usable with map.addImage (pixelRatio = dpr).
 */
export function markerImage(iconName, color, { size = 28, dpr = 2, ring = '#ffffff', shape = 'circle' } = {}) {
  const px = Math.round(size * dpr);
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = px;
  const ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr);
  const r = size / 2;
  ctx.shadowColor = 'rgba(0,0,0,.25)';
  ctx.shadowBlur = 2;
  ctx.shadowOffsetY = 1;
  ctx.beginPath();
  if (shape === 'pin') {
    // teardrop pin
    ctx.moveTo(r, size - 1);
    ctx.bezierCurveTo(r - 2, size - 6, 2, r + 3, 2, r - 2);
    ctx.arc(r, r - 2, r - 2, Math.PI, 0);
    ctx.bezierCurveTo(size - 2, r + 3, r + 2, size - 6, r, size - 1);
  } else {
    ctx.arc(r, r, r - 1.5, 0, Math.PI * 2);
  }
  ctx.fillStyle = color;
  ctx.fill();
  ctx.shadowColor = 'transparent';
  ctx.lineWidth = 1.5;
  ctx.strokeStyle = ring;
  ctx.stroke();
  const glyph = new Path2D(ICONS[iconName] || ICONS.pin);
  const g = size * (shape === 'pin' ? 0.46 : 0.58);
  ctx.save();
  ctx.translate(r - g / 2, (shape === 'pin' ? r - 2 : r) - g / 2);
  ctx.scale(g / 24, g / 24);
  ctx.strokeStyle = '#ffffff';
  ctx.lineWidth = 2.4;
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  ctx.stroke(glyph);
  ctx.restore();
  return ctx.getImageData(0, 0, px, px);
}


/**
 * Marker with an operator's official logo (an <img> already loaded): white disc with a thin
 * brand-coloured ring, or – shape 'pin' – a brand-coloured pin holding the white disc.
 * The logo keeps its aspect ratio inside the disc. Returns ImageData for map.addImage.
 */
export function logoMarkerImage(img, ring, { size = 28, dpr = 2, shape = 'circle', fit = 'contain' } = {}) {
  const px = Math.round(size * dpr);
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = px;
  const ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr);
  const r = size / 2;
  const cy = shape === 'pin' ? r - 2 : r;
  ctx.shadowColor = 'rgba(0,0,0,.28)';
  ctx.shadowBlur = 2;
  ctx.shadowOffsetY = 1;
  ctx.beginPath();
  if (shape === 'pin') {
    ctx.moveTo(r, size - 1);
    ctx.bezierCurveTo(r - 2, size - 6, 2, r + 3, 2, r - 2);
    ctx.arc(r, r - 2, r - 2, Math.PI, 0);
    ctx.bezierCurveTo(size - 2, r + 3, r + 2, size - 6, r, size - 1);
    ctx.fillStyle = ring;
    ctx.fill();
    ctx.shadowColor = 'transparent';
    ctx.beginPath();
    ctx.arc(r, cy, r - 5, 0, Math.PI * 2);
  } else {
    ctx.arc(r, r, r - 1.5, 0, Math.PI * 2);
  }
  ctx.fillStyle = '#ffffff';
  ctx.fill();
  ctx.shadowColor = 'transparent';
  const rad = shape === 'pin' ? r - 5 : r - 1.5;
  const iw = img.naturalWidth || img.width || 1;
  const ih = img.naturalHeight || img.height || 1;
  if (fit === 'cover') {
    // a square badge logo (e.g. the INPA emblem on green) fills the whole disc
    ctx.save();
    ctx.clip();
    const k = (rad * 2) / Math.min(iw, ih);
    ctx.drawImage(img, r - (iw * k) / 2, cy - (ih * k) / 2, iw * k, ih * k);
    ctx.restore();
    ctx.lineWidth = 1.6;
    ctx.strokeStyle = '#ffffff';
  } else {
    const k = (rad * 2 * 0.74) / Math.max(iw, ih);
    ctx.drawImage(img, r - (iw * k) / 2, cy - (ih * k) / 2, iw * k, ih * k);
    ctx.lineWidth = shape === 'pin' ? 1 : 1.6;
    ctx.strokeStyle = ring;
  }
  ctx.beginPath();
  ctx.arc(r, cy, rad, 0, Math.PI * 2);
  ctx.stroke();
  return ctx.getImageData(0, 0, px, px);
}

/**
 * Israeli route-number shield, the way Google Maps draws it: a small rounded
 * rectangle in the sign's colour, a thin inner white frame, the number in bold.
 * Colours follow the Israeli signage code (freeway blue, 1-2 digit red,
 * regional green, local black/white).  Returns ImageData for map.addImage.
 */
export const SHIELD_STYLES = {
  fw: { bg: '#1D5BBF', fg: '#FFFFFF', frame: '#FFFFFF', edge: '#123C80' },
  nat: { bg: '#D6332B', fg: '#FFFFFF', frame: '#FFFFFF', edge: '#8F1D17' },
  reg: { bg: '#178246', fg: '#FFFFFF', frame: '#FFFFFF', edge: '#0D5A2F' },
  loc: { bg: '#FFFFFF', fg: '#1F2328', frame: '#1F2328', edge: '#6B7280' },
};

export function shieldImage(ref, kind = 'nat', { dpr = 2, height = 18 } = {}) {
  const st = SHIELD_STYLES[kind] || SHIELD_STYLES.nat;
  const text = String(ref).split(/[;,]/)[0].trim();
  const font = `700 ${Math.round(height * 0.64)}px "Segoe UI", "Noto Sans", Arial, sans-serif`;
  const probe = document.createElement('canvas').getContext('2d');
  probe.font = font;
  const tw = Math.ceil(probe.measureText(text).width);
  const w = Math.max(height + 2, tw + 12);
  const h = height;
  const canvas = document.createElement('canvas');
  canvas.width = Math.round((w + 2) * dpr);
  canvas.height = Math.round((h + 3) * dpr);
  const ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr);
  const rr = (x, y, ww, hh, r) => {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + ww, y, x + ww, y + hh, r);
    ctx.arcTo(x + ww, y + hh, x, y + hh, r);
    ctx.arcTo(x, y + hh, x, y, r);
    ctx.arcTo(x, y, x + ww, y, r);
    ctx.closePath();
  };
  // body with a soft drop shadow
  ctx.shadowColor = 'rgba(0,0,0,.28)';
  ctx.shadowBlur = 1.5;
  ctx.shadowOffsetY = 0.8;
  rr(1, 1, w, h, 3.5);
  ctx.fillStyle = st.bg;
  ctx.fill();
  ctx.shadowColor = 'transparent';
  ctx.lineWidth = 1;
  ctx.strokeStyle = st.edge;
  ctx.stroke();
  // inner frame, as on the real signs
  rr(2.6, 2.6, w - 3.2, h - 3.2, 2.2);
  ctx.lineWidth = 1.1;
  ctx.strokeStyle = st.frame;
  ctx.stroke();
  // number
  ctx.font = font;
  ctx.fillStyle = st.fg;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText(text, 1 + w / 2, 1 + h / 2 + 0.6);
  return ctx.getImageData(0, 0, canvas.width, canvas.height);
}
