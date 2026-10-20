// MapLibre styles for the OpenMapTiles schema (built by Planetiler) and for the
// fallback basemap generated from our own database. Themes: light, dark, nature, topo
// (topographic: hillshade + contour lines from the Copernicus DEM, when built).

const NAME = ['coalesce', ['get', 'name:he'], ['get', 'name'], ['get', 'name:latin']];
const REG = ['Noto Sans Regular'];
const MED = ['Noto Sans Medium'];

export const THEMES = {
  light: {
    bg: '#F4F3EF', water: '#A9D3F5', waterLine: '#8EC3EE', waterText: '#2F6390',
    farmland: '#EEF0DD', grass: '#D7EDCB', wood: '#C3E0B6', sand: '#F3EACB', wetland: '#D3E8DF', rock: '#E5E2DB',
    park: '#CDE9C6', parkText: '#2F6B35', residential: '#EFEDE8', commercial: '#F5ECE8', industrial: '#ECE7EE',
    hospital: '#F7E1E1', school: '#F3EED8', cemetery: '#DCE7D5', military: '#EDE3E3', sport: '#D8EDCF', aeroway: '#E4E1EC',
    building: '#E2DFD9', buildingLine: '#D3CFC7',
    minor: '#FFFFFF', minorCasing: '#D9D6CF', major: '#FFFFFF', majorCasing: '#C7C3BA',
    trunk: '#FCE3A1', trunkCasing: '#E2B04F', motorway: '#FBCF7E', motorwayCasing: '#D8952A',
    path: '#9C9383', rail: '#ABA59A', tunnelOpacity: 0.5,
    boundary: '#8E80A8', text: '#2B2B2B', textMinor: '#575757', roadText: '#4B4B4B', halo: '#FFFFFF',
    city: '#1F2937', suburb: '#5A6170', peak: '#6B4F2E', shieldText: '#FFFFFF', shieldHalo: '#C77A0A',
  },
  dark: {
    bg: '#1B1E23', water: '#0F2A43', waterLine: '#16395A', waterText: '#7FA9CF',
    farmland: '#1F231C', grass: '#1F3024', wood: '#1B2D20', sand: '#2B2920', wetland: '#1C2C28', rock: '#26262A',
    park: '#1D3324', parkText: '#86C98F', residential: '#202328', commercial: '#26242A', industrial: '#242329',
    hospital: '#2D2226', school: '#2A2820', cemetery: '#1F2A22', military: '#2A2225', sport: '#1F3024', aeroway: '#26252E',
    building: '#2B2F37', buildingLine: '#343944',
    minor: '#373C45', minorCasing: '#1B1E23', major: '#454B56', majorCasing: '#1B1E23',
    trunk: '#6B5427', trunkCasing: '#1B1E23', motorway: '#7F5E22', motorwayCasing: '#1B1E23',
    path: '#6E675C', rail: '#5A5650', tunnelOpacity: 0.4,
    boundary: '#8C7FB0', text: '#E5E7EB', textMinor: '#B5BAC3', roadText: '#C7CBD2', halo: '#121418',
    city: '#F3F4F6', suburb: '#A3AAB6', peak: '#D9B98F', shieldText: '#FFFFFF', shieldHalo: '#8A5F12',
  },
  nature: {
    bg: '#F1F0E6', water: '#9CCBF0', waterLine: '#6FB0E6', waterText: '#24557F',
    farmland: '#EAEED2', grass: '#CFE8BE', wood: '#AED49C', sand: '#F0E2B6', wetland: '#C6E2D3', rock: '#DDD7CB',
    park: '#BDE3B0', parkText: '#1F5C26', residential: '#ECE9E2', commercial: '#F1E8E3', industrial: '#E8E3E8',
    hospital: '#F4DEDE', school: '#F0EBD5', cemetery: '#D2E2C8', military: '#E8DEDE', sport: '#CDE7C1', aeroway: '#E1DDE8',
    building: '#E6E2DB', buildingLine: '#D6D1C7',
    minor: '#FFFFFF', minorCasing: '#D0CCC2', major: '#FFFDF5', majorCasing: '#BDB7AA',
    trunk: '#F9DFA0', trunkCasing: '#D6A74E', motorway: '#F7CB80', motorwayCasing: '#CF8E2B',
    path: '#8B5A2B', rail: '#A49E92', tunnelOpacity: 0.5,
    boundary: '#7C6E98', text: '#262626', textMinor: '#4F4F4F', roadText: '#4B4B4B', halo: '#FFFFFF',
    city: '#1F2937', suburb: '#575E6B', peak: '#5E3F1C', shieldText: '#FFFFFF', shieldHalo: '#B86F0A',
  },
};

// topographic: the nature palette, lighter so hillshade and contours stand out
THEMES.topo = {
  ...THEMES.nature,
  bg: '#F6F3EA', farmland: '#EFF0DE', grass: '#DDEBCD', wood: '#C6DFB6', sand: '#F3E7C6', rock: '#E4DED2',
  park: '#D3E9C7', residential: '#EEEBE4', building: '#E3DED6',
  contour: '#B08055', contourIndex: '#8C5A2E', contourText: '#6E4520', spot: '#5E3F1C', hillshadeShadow: '#4A3A2A',
};

/**
 * Hillshade, contour lines (with elevations on index lines) and spot heights.
 * `terrain` is meta.terrain from the API: { contours: {tiles, minzoom, maxzoom, bounds}, dem: {...} }.
 * Returns the sources and two layer groups: drawn under the roads, and labels.
 */
export function terrainLayers(t, terrain) {
  const sources = {};
  const under = [];
  const labels = [];
  if (!terrain) return { sources, under, labels };
  if (terrain.dem) {
    sources['terrain-dem'] = { type: 'raster-dem', tiles: [terrain.dem.tiles], tileSize: 256, encoding: terrain.dem.encoding || 'terrarium',
      minzoom: terrain.dem.minzoom, maxzoom: terrain.dem.maxzoom, bounds: terrain.dem.bounds };
    under.push({ id: 'hillshade', type: 'hillshade', source: 'terrain-dem',
      paint: { 'hillshade-exaggeration': ['interpolate', ['linear'], ['zoom'], 6, 0.55, 11, 0.4, 15, 0.25],
        'hillshade-shadow-color': t.hillshadeShadow || '#4A3A2A', 'hillshade-highlight-color': '#FFFFFF',
        'hillshade-accent-color': t.contour || '#8C5A2E', 'hillshade-illumination-direction': 315 } });
  }
  if (terrain.contours) {
    const c = terrain.contours;
    sources.contours = { type: 'vector', tiles: [c.tiles], minzoom: c.minzoom, maxzoom: c.maxzoom, bounds: c.bounds };
    under.push(
      { id: 'contour-minor', type: 'line', source: 'contours', 'source-layer': 'contour', minzoom: 11,
        filter: ['!=', ['get', 'idx'], 1], layout: { 'line-join': 'round' },
        paint: { 'line-color': t.contour, 'line-width': ['interpolate', ['linear'], ['zoom'], 11, 0.35, 14, 0.6, 17, 1],
          'line-opacity': ['interpolate', ['linear'], ['zoom'], 11, 0.35, 13, 0.6] } },
      { id: 'contour-index', type: 'line', source: 'contours', 'source-layer': 'contour', minzoom: 10,
        filter: ['==', ['get', 'idx'], 1], layout: { 'line-join': 'round' },
        paint: { 'line-color': t.contourIndex, 'line-width': ['interpolate', ['linear'], ['zoom'], 10, 0.6, 14, 1.1, 17, 1.8],
          'line-opacity': ['interpolate', ['linear'], ['zoom'], 10, 0.45, 13, 0.8] } },
    );
    labels.push(
      { id: 'contour-label', type: 'symbol', source: 'contours', 'source-layer': 'contour', minzoom: 12,
        filter: ['==', ['get', 'idx'], 1],
        layout: { 'symbol-placement': 'line', 'text-field': ['to-string', ['get', 'ele']], 'text-font': REG,
          'text-size': ['interpolate', ['linear'], ['zoom'], 12, 9.5, 16, 11.5], 'symbol-spacing': 320, 'text-max-angle': 25,
          'text-padding': 4, 'text-rotation-alignment': 'map', 'text-pitch-alignment': 'viewport' },
        paint: { 'text-color': t.contourText, 'text-halo-color': t.bg, 'text-halo-width': 1.6 } },
      { id: 'spot-height', type: 'symbol', source: 'contours', 'source-layer': 'spot', minzoom: 12,
        layout: { 'text-field': ['concat', '• ', ['to-string', ['get', 'ele']]], 'text-font': REG, 'text-size': 10,
          'text-padding': 6, 'text-optional': true },
        paint: { 'text-color': t.spot, 'text-halo-color': t.halo, 'text-halo-width': 1.3 } },
    );
  }
  return { sources, under, labels };
}

// ------------------------------------------------------------------ satellite imagery
// Aerial orthophotos from the Israeli Survey Department via govmap.gov.il CDN.
// The CDN uses a non-standard {z}/{y}/{x} path order (row-major / TMS column convention)
// so we request the tiles directly and let the browser fetch them.
//
// `satellite` parameter accepted by omtStyle / fallbackStyle:
//   false | null  — no satellite layer (default)
//   true          — satellite on with default opacity (0.85)
//   number        — satellite on with that opacity (0–1)

// govmap.gov.il CDN — 2023 orthophotos, zoom 7–20, covers all of Israel.
// Tiles are proxied through our own server (/tiles/satellite/{z}/{x}/{y}.jpg)
// because the CDN blocks direct browser requests (CORS + Referer check).
const GOVMAP_ATTRIBUTION = '© <a href="https://www.govmap.gov.il" target="_blank" rel="noopener">מרכז מיפוי ישראל</a>';

/**
 * Returns a MapLibre raster source definition for the govmap orthophotos.
 * Pass to `omtStyle` / `fallbackStyle` via the `satellite` option.
 */
export function satelliteSource() {
  return {
    type: 'raster',
    tiles: ['/tiles/satellite/{z}/{x}/{y}.jpg'],
    tileSize: 256,
    minzoom: 7,
    maxzoom: 20,
    bounds: [34.2, 29.4, 35.9, 33.4], // Israel bounding box
    attribution: GOVMAP_ATTRIBUTION,
  };
}

/**
 * Inject the satellite raster layer at the very bottom of an existing style
 * (before `background`), so vector layers render on top.
 * `opacity` (0–1, default 0.85) controls blending.
 * Idempotent: safe to call even if the source is already present.
 */
export function addSatellite(style, opacity = 0.85) {
  if (!style.sources.satellite) {
    style.sources.satellite = satelliteSource();
  }
  if (!style.layers.find((l) => l.id === 'satellite')) {
    style.layers.unshift({
      id: 'satellite',
      type: 'raster',
      source: 'satellite',
      paint: { 'raster-opacity': opacity, 'raster-saturation': 0.05 },
    });
  }
  return style;
}

/** Insert the terrain groups into a style: `under` before `underId`, labels before `labelId`. */
function addTerrain(style, t, terrain, underId, labelId) {
  const { sources, under, labels } = terrainLayers(t, terrain);
  Object.assign(style.sources, sources);
  const at = (id) => {
    const i = style.layers.findIndex((l) => l.id === id);
    return i < 0 ? style.layers.length : i;
  };
  style.layers.splice(at(underId), 0, ...under);
  style.layers.splice(at(labelId), 0, ...labels);
  return style;
}

const zw = (pairs) => ['interpolate', ['exponential', 1.5], ['zoom'], ...pairs.flat()];
const cls = (...v) => ['match', ['get', 'class'], v, true, false];
const notTunnel = ['!=', ['get', 'brunnel'], 'tunnel'];
const isTunnel = ['==', ['get', 'brunnel'], 'tunnel'];

const ROAD_WIDTH = {
  motorway: [[5, 0.6], [9, 1.6], [12, 3.2], [15, 9], [18, 26]],
  trunk: [[6, 0.5], [9, 1.4], [12, 2.8], [15, 8], [18, 24]],
  primary: [[7, 0.4], [10, 1.4], [12, 2.4], [15, 7], [18, 22]],
  secondary: [[9, 0.4], [11, 1.1], [13, 2.2], [15, 6], [18, 20]],
  tertiary: [[10, 0.4], [12, 1], [14, 2.4], [16, 6], [18, 16]],
  minor: [[12, 0.4], [14, 1.6], [16, 4.5], [18, 13]],
  service: [[14, 0.3], [16, 2.2], [18, 6]],
};

function road(id, theme, classes, widthKey, fill, casing, minzoom, extraFilter) {
  const filter = ['all', cls(...classes), ...(extraFilter ? [extraFilter] : [])];
  const w = ROAD_WIDTH[widthKey];
  const layers = [];
  if (casing) {
    layers.push({
      id: `${id}-casing`, type: 'line', source: 'openmaptiles', 'source-layer': 'transportation', minzoom,
      filter, layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: { 'line-color': casing, 'line-width': zw(w.map(([z, v]) => [z, v * 1.35 + (z >= 12 ? 1 : 0.2)])) },
    });
  }
  layers.push({
    id, type: 'line', source: 'openmaptiles', 'source-layer': 'transportation', minzoom,
    filter, layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: { 'line-color': fill, 'line-width': zw(w) },
  });
  return layers;
}

export function omtStyle(themeName, { tilejson, glyphs, terrain, satellite = false }) {
  const t = THEMES[themeName] || THEMES.light;
  const nature = themeName === 'nature' || themeName === 'topo';
  const layers = [
    { id: 'background', type: 'background', paint: { 'background-color': t.bg } },
    {
      id: 'landcover', type: 'fill', source: 'openmaptiles', 'source-layer': 'landcover',
      paint: {
        'fill-color': ['match', ['get', 'class'], 'farmland', t.farmland, 'grass', t.grass, 'wood', t.wood, 'sand', t.sand,
          'wetland', t.wetland, 'rock', t.rock, 'ice', '#FFFFFF', t.grass],
        'fill-opacity': ['interpolate', ['linear'], ['zoom'], 5, nature ? 0.9 : 0.6, 12, nature ? 1 : 0.85],
      },
    },
    {
      id: 'landuse', type: 'fill', source: 'openmaptiles', 'source-layer': 'landuse', minzoom: 9,
      paint: {
        'fill-color': ['match', ['get', 'class'],
          'residential', t.residential, ['commercial', 'retail'], t.commercial, ['industrial', 'garages', 'quarry'], t.industrial,
          'hospital', t.hospital, ['school', 'university', 'college', 'kindergarten', 'library'], t.school,
          'cemetery', t.cemetery, 'military', t.military, ['stadium', 'pitch', 'playground', 'track', 'zoo', 'theme_park'], t.sport,
          t.residential],
        'fill-opacity': ['interpolate', ['linear'], ['zoom'], 9, ['match', ['get', 'class'], 'residential', 0.5, 0.9], 13, 0.9],
      },
    },
    {
      id: 'park', type: 'fill', source: 'openmaptiles', 'source-layer': 'park',
      paint: { 'fill-color': t.park, 'fill-opacity': ['interpolate', ['linear'], ['zoom'], 5, 0.5, 10, nature ? 0.95 : 0.8] },
    },
    ...(nature ? [{
      id: 'park-outline', type: 'line', source: 'openmaptiles', 'source-layer': 'park', minzoom: 8,
      paint: { 'line-color': t.parkText, 'line-opacity': 0.5, 'line-width': 1, 'line-dasharray': [3, 2] },
    }] : []),
    {
      id: 'water', type: 'fill', source: 'openmaptiles', 'source-layer': 'water',
      filter: ['!=', ['get', 'brunnel'], 'tunnel'],
      paint: { 'fill-color': t.water },
    },
    {
      id: 'waterway-river', type: 'line', source: 'openmaptiles', 'source-layer': 'waterway', minzoom: 8,
      filter: ['all', notTunnel, cls('river', 'canal')],
      paint: { 'line-color': t.waterLine, 'line-width': zw([[8, 0.8], [14, 3], [18, 10]]) },
    },
    {
      id: 'waterway-stream', type: 'line', source: 'openmaptiles', 'source-layer': 'waterway', minzoom: 11,
      filter: ['all', notTunnel, ['!', cls('river', 'canal')]],
      paint: { 'line-color': t.waterLine, 'line-width': zw([[11, 0.4], [15, 1.4], [18, 3]]), 'line-dasharray': ['literal', [3, 2]] },
    },
    {
      id: 'aeroway-area', type: 'fill', source: 'openmaptiles', 'source-layer': 'aeroway', minzoom: 10,
      filter: ['==', ['geometry-type'], 'Polygon'], paint: { 'fill-color': t.aeroway },
    },
    {
      id: 'aeroway-runway', type: 'line', source: 'openmaptiles', 'source-layer': 'aeroway', minzoom: 10,
      filter: ['all', ['==', ['geometry-type'], 'LineString'], cls('runway')],
      paint: { 'line-color': themeName === 'dark' ? '#3A3A44' : '#D2CEDB', 'line-width': zw([[10, 1], [14, 12], [18, 60]]) },
    },
    {
      id: 'aeroway-taxiway', type: 'line', source: 'openmaptiles', 'source-layer': 'aeroway', minzoom: 12,
      filter: ['all', ['==', ['geometry-type'], 'LineString'], cls('taxiway')],
      paint: { 'line-color': themeName === 'dark' ? '#3A3A44' : '#D2CEDB', 'line-width': zw([[12, 0.5], [18, 12]]) },
    },
    {
      id: 'building', type: 'fill', source: 'openmaptiles', 'source-layer': 'building', minzoom: 14,
      paint: { 'fill-color': t.building, 'fill-outline-color': t.buildingLine,
        'fill-opacity': ['interpolate', ['linear'], ['zoom'], 14, 0, 15, nature ? 0.6 : 0.9] },
    },
    // tunnels (faded)
    {
      id: 'road-tunnel', type: 'line', source: 'openmaptiles', 'source-layer': 'transportation', minzoom: 12,
      filter: ['all', isTunnel, cls('motorway', 'trunk', 'primary', 'secondary', 'tertiary', 'minor')],
      layout: { 'line-join': 'round' },
      paint: { 'line-color': t.majorCasing, 'line-opacity': t.tunnelOpacity, 'line-width': zw([[12, 1], [16, 6], [18, 14]]), 'line-dasharray': [2, 1] },
    },
    // paths, tracks, steps
    {
      id: 'path', type: 'line', source: 'openmaptiles', 'source-layer': 'transportation', minzoom: nature ? 11 : 14,
      filter: ['all', notTunnel, cls('path', 'track')],
      layout: { 'line-join': 'round' },
      paint: {
        'line-color': t.path,
        'line-width': zw([[13, nature ? 1 : 0.6], [16, nature ? 2 : 1.4], [18, 3]]),
        'line-dasharray': ['literal', [2, 1.5]],
        'line-opacity': nature ? 0.95 : 0.8,
      },
    },
    {
      id: 'rail', type: 'line', source: 'openmaptiles', 'source-layer': 'transportation', minzoom: 9,
      filter: ['all', notTunnel, cls('rail', 'transit')],
      paint: { 'line-color': t.rail, 'line-width': zw([[9, 0.6], [14, 1.6], [18, 3]]) },
    },
    {
      id: 'rail-hatch', type: 'line', source: 'openmaptiles', 'source-layer': 'transportation', minzoom: 13,
      filter: ['all', notTunnel, cls('rail', 'transit')],
      paint: { 'line-color': t.rail, 'line-width': zw([[13, 3], [18, 7]]), 'line-dasharray': ['literal', [0.2, 4]] },
    },
    ...road('road-service', t, ['service'], 'service', t.minor, t.minorCasing, 14, notTunnel),
    ...road('road-minor', t, ['minor'], 'minor', t.minor, t.minorCasing, 12, notTunnel),
    ...road('road-tertiary', t, ['tertiary'], 'tertiary', t.major, t.majorCasing, 10, notTunnel),
    ...road('road-secondary', t, ['secondary'], 'secondary', t.major, t.majorCasing, 9, notTunnel),
    ...road('road-primary', t, ['primary'], 'primary', t.major, t.majorCasing, 7, notTunnel),
    ...road('road-trunk', t, ['trunk'], 'trunk', t.trunk, t.trunkCasing, 5, notTunnel),
    ...road('road-motorway', t, ['motorway'], 'motorway', t.motorway, t.motorwayCasing, 5, notTunnel),
    {
      id: 'boundary-country', type: 'line', source: 'openmaptiles', 'source-layer': 'boundary',
      filter: ['all', ['<=', ['get', 'admin_level'], 2], ['!=', ['get', 'maritime'], 1]],
      layout: { 'line-join': 'round' },
      paint: { 'line-color': t.boundary, 'line-width': zw([[3, 0.6], [8, 1.6], [12, 2.5]]), 'line-dasharray': ['literal', [4, 2]], 'line-opacity': 0.7 },
    },
    {
      id: 'boundary-district', type: 'line', source: 'openmaptiles', 'source-layer': 'boundary', minzoom: 8,
      filter: ['all', ['>=', ['get', 'admin_level'], 3], ['<=', ['get', 'admin_level'], 6], ['!=', ['get', 'maritime'], 1]],
      paint: { 'line-color': t.boundary, 'line-width': 0.8, 'line-dasharray': ['literal', [3, 2]], 'line-opacity': 0.45 },
    },
    // ---- labels ----
    {
      id: 'water-name', type: 'symbol', source: 'openmaptiles', 'source-layer': 'water_name',
      layout: { 'text-field': NAME, 'text-font': REG, 'text-size': ['match', ['get', 'class'], ['ocean', 'sea'], 15, 12], 'text-max-width': 8 },
      paint: { 'text-color': t.waterText, 'text-halo-color': t.halo, 'text-halo-width': 1.2 },
    },
    {
      id: 'waterway-name', type: 'symbol', source: 'openmaptiles', 'source-layer': 'waterway', minzoom: 12,
      layout: { 'text-field': NAME, 'text-font': REG, 'text-size': 11, 'symbol-placement': 'line', 'symbol-spacing': 400 },
      paint: { 'text-color': t.waterText, 'text-halo-color': t.halo, 'text-halo-width': 1.2 },
    },
    {
      id: 'road-name-minor', type: 'symbol', source: 'openmaptiles', 'source-layer': 'transportation_name', minzoom: 15,
      filter: cls('minor', 'service', 'path', 'track'),
      layout: { 'text-field': NAME, 'text-font': REG, 'text-size': ['interpolate', ['linear'], ['zoom'], 15, 10.5, 18, 13],
        'symbol-placement': 'line', 'text-rotation-alignment': 'map', 'symbol-spacing': 300, 'text-max-angle': 30 },
      paint: { 'text-color': t.roadText, 'text-halo-color': t.halo, 'text-halo-width': 1.4 },
    },
    {
      id: 'road-name-major', type: 'symbol', source: 'openmaptiles', 'source-layer': 'transportation_name', minzoom: 12,
      filter: cls('primary', 'secondary', 'tertiary', 'trunk', 'motorway'),
      layout: { 'text-field': NAME, 'text-font': REG, 'text-size': ['interpolate', ['linear'], ['zoom'], 12, 10.5, 18, 14],
        'symbol-placement': 'line', 'text-rotation-alignment': 'map', 'symbol-spacing': 350, 'text-max-angle': 30 },
      paint: { 'text-color': t.roadText, 'text-halo-color': t.halo, 'text-halo-width': 1.5 },
    },
    // route-number shields, Google style: a coloured badge per Israeli sign class
    // (freeway blue, 1-2 digit red, 3 digit green, 4 digit black) drawn by map.js
    ...[
      { id: 'road-shield', classes: ['motorway', 'trunk', 'primary'], minzoom: 7, spacing: 420 },
      { id: 'road-shield-minor', classes: ['secondary', 'tertiary'], minzoom: 11, spacing: 380 },
    ].map(({ id, classes, minzoom, spacing }) => ({
      id, type: 'symbol', source: 'openmaptiles', 'source-layer': 'transportation_name', minzoom,
      filter: ['all', ['has', 'ref'], ['<=', ['get', 'ref_length'], 4], cls(...classes)],
      layout: {
        'icon-image': ['concat', 'shield:',
          ['case',
            ['==', ['get', 'class'], 'motorway'], 'fw',
            ['<=', ['get', 'ref_length'], 2], 'nat',
            ['==', ['get', 'ref_length'], 3], 'reg',
            'loc'],
          ':', ['get', 'ref']],
        'icon-size': ['interpolate', ['linear'], ['zoom'], 7, 0.85, 12, 1, 16, 1.1],
        'icon-rotation-alignment': 'viewport',
        'icon-padding': 6,
        'symbol-placement': 'line',
        'symbol-spacing': ['interpolate', ['linear'], ['zoom'], 8, spacing, 14, spacing * 1.3],
        // the most important roads win when shields compete for space
        'symbol-sort-key': ['match', ['get', 'class'], 'motorway', 0, 'trunk', 1, 'primary', 2, 'secondary', 3, 4],
      },
    })),
    {
      id: 'housenumber', type: 'symbol', source: 'openmaptiles', 'source-layer': 'housenumber', minzoom: 17,
      layout: { 'text-field': ['get', 'housenumber'], 'text-font': REG, 'text-size': 10 },
      paint: { 'text-color': t.textMinor, 'text-halo-color': t.halo, 'text-halo-width': 1 },
    },
    {
      id: 'peak', type: 'symbol', source: 'openmaptiles', 'source-layer': 'mountain_peak', minzoom: nature ? 9 : 12,
      filter: ['has', 'name'],
      layout: {
        'text-field': ['format', '▲ ', {}, NAME, {}, ['case', ['has', 'ele'], ['concat', '\n', ['to-string', ['get', 'ele']], ' מ׳'], ''], { 'font-scale': 0.85 }],
        'text-font': REG, 'text-size': 11, 'text-max-width': 8,
      },
      paint: { 'text-color': t.peak, 'text-halo-color': t.halo, 'text-halo-width': 1.3 },
    },
    {
      id: 'park-name', type: 'symbol', source: 'openmaptiles', 'source-layer': 'park', minzoom: nature ? 9 : 12,
      filter: ['has', 'name'],
      layout: { 'text-field': NAME, 'text-font': REG, 'text-size': 11.5, 'text-max-width': 8, 'symbol-sort-key': ['coalesce', ['get', 'rank'], 99] },
      paint: { 'text-color': t.parkText, 'text-halo-color': t.halo, 'text-halo-width': 1.3 },
    },
    {
      id: 'airport', type: 'symbol', source: 'openmaptiles', 'source-layer': 'aerodrome_label', minzoom: 9,
      layout: { 'text-field': ['format', '✈ ', {}, NAME, {}], 'text-font': MED, 'text-size': 12, 'text-max-width': 8 },
      paint: { 'text-color': t.text, 'text-halo-color': t.halo, 'text-halo-width': 1.4 },
    },
    {
      id: 'place-neighbourhood', type: 'symbol', source: 'openmaptiles', 'source-layer': 'place', minzoom: 13,
      filter: cls('suburb', 'quarter', 'neighbourhood'),
      layout: { 'text-field': NAME, 'text-font': MED, 'text-size': ['interpolate', ['linear'], ['zoom'], 13, 11, 16, 14],
        'text-max-width': 7, 'text-transform': 'none' },
      paint: { 'text-color': t.suburb, 'text-halo-color': t.halo, 'text-halo-width': 1.4 },
    },
    {
      id: 'place-village', type: 'symbol', source: 'openmaptiles', 'source-layer': 'place', minzoom: 10,
      filter: cls('village', 'hamlet', 'isolated_dwelling'),
      layout: { 'text-field': NAME, 'text-font': REG, 'text-size': ['interpolate', ['linear'], ['zoom'], 10, 11, 15, 15], 'text-max-width': 8 },
      paint: { 'text-color': t.text, 'text-halo-color': t.halo, 'text-halo-width': 1.4 },
    },
    {
      id: 'place-town', type: 'symbol', source: 'openmaptiles', 'source-layer': 'place', minzoom: 8,
      filter: cls('town'),
      layout: { 'text-field': NAME, 'text-font': MED, 'text-size': ['interpolate', ['linear'], ['zoom'], 8, 11.5, 14, 17], 'text-max-width': 8 },
      paint: { 'text-color': t.city, 'text-halo-color': t.halo, 'text-halo-width': 1.5 },
    },
    {
      id: 'place-city', type: 'symbol', source: 'openmaptiles', 'source-layer': 'place', minzoom: 5,
      filter: cls('city'),
      layout: { 'text-field': NAME, 'text-font': MED,
        'text-size': ['interpolate', ['linear'], ['zoom'], 5, ['case', ['<=', ['coalesce', ['get', 'rank'], 9], 4], 13, 11], 12, 20],
        'text-max-width': 8, 'symbol-sort-key': ['coalesce', ['get', 'rank'], 9] },
      paint: { 'text-color': t.city, 'text-halo-color': t.halo, 'text-halo-width': 1.8 },
    },
    {
      id: 'place-country', type: 'symbol', source: 'openmaptiles', 'source-layer': 'place', maxzoom: 7,
      filter: cls('country'),
      layout: { 'text-field': NAME, 'text-font': MED, 'text-size': 14, 'text-max-width': 6 },
      paint: { 'text-color': t.textMinor, 'text-halo-color': t.halo, 'text-halo-width': 1.6 },
    },
  ];
  const style = {
    version: 8,
    name: `hmaps-${themeName}`,
    glyphs,
    sources: { openmaptiles: { type: 'vector', url: tilejson } },
    layers,
  };
  const result = themeName === 'topo' ? addTerrain(style, t, terrain, 'aeroway-area', 'water-name') : style;
  if (satellite !== false && satellite != null) {
    addSatellite(result, typeof satellite === 'number' ? satellite : 0.85);
  }
  return result;
}

/** Basemap drawn from our own tiles while the full basemap is not built. */
export function fallbackStyle(themeName, { tiles, glyphs, terrain, satellite = false }) {
  const t = THEMES[themeName] || THEMES.light;
  const W = {
    motorway: [[6, 1], [12, 4], [16, 12], [18, 26]], trunk: [[7, 1], [12, 3.5], [16, 11], [18, 24]],
    primary: [[9, 0.8], [13, 3], [16, 9], [18, 20]], secondary: [[11, 0.8], [14, 3], [16, 8], [18, 18]],
    tertiary: [[12, 0.8], [14, 2.5], [16, 7], [18, 16]], minor: [[13, 0.6], [15, 2.5], [18, 12]],
  };
  const street = (id, classes, stops, fill, minzoom) => {
    const filter = classes ? ['match', ['get', 'class'], classes, true, false] : ['!', ['match', ['get', 'class'], ['motorway', 'trunk', 'primary', 'secondary', 'tertiary'], true, false]];
    return [
      { id: `${id}-casing`, type: 'line', source: 'fallback', 'source-layer': 'streets', minzoom, filter,
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: { 'line-color': t.majorCasing, 'line-width': zw(stops.map(([z, v]) => [z, v + 1.5])) } },
      { id, type: 'line', source: 'fallback', 'source-layer': 'streets', minzoom, filter,
        layout: { 'line-cap': 'round', 'line-join': 'round' }, paint: { 'line-color': fill, 'line-width': zw(stops) } },
    ];
  };
  const style = {
    version: 8, name: `hmaps-fallback-${themeName}`, glyphs,
    sources: { fallback: { type: 'vector', tiles: [tiles], minzoom: 6, maxzoom: 15 } },
    layers: [
      { id: 'background', type: 'background', paint: { 'background-color': t.bg } },
      { id: 'boundaries', type: 'line', source: 'fallback', 'source-layer': 'boundaries',
        paint: { 'line-color': t.boundary, 'line-width': 1, 'line-dasharray': ['literal', [3, 2]], 'line-opacity': 0.6 } },
      ...street('streets-minor', null, W.minor, t.minor, 13),
      ...street('streets-tertiary', ['tertiary', 'secondary'], W.secondary, t.major, 11),
      ...street('streets-primary', ['primary'], W.primary, t.major, 9),
      ...street('streets-trunk', ['trunk'], W.trunk, t.trunk, 7),
      ...street('streets-motorway', ['motorway'], W.motorway, t.motorway, 6),
      { id: 'street-names', type: 'symbol', source: 'fallback', 'source-layer': 'streets', minzoom: 14,
        layout: { 'text-field': ['get', 'name'], 'text-font': REG, 'text-size': 11, 'symbol-placement': 'line' },
        paint: { 'text-color': t.roadText, 'text-halo-color': t.halo, 'text-halo-width': 1.4 } },
      { id: 'places', type: 'symbol', source: 'fallback', 'source-layer': 'places',
        layout: { 'text-field': ['get', 'name'], 'text-font': MED, 'text-max-width': 8,
          'text-size': ['match', ['get', 'kind'], 'city', 16, 'town', 14, ['suburb', 'quarter', 'neighbourhood'], 12, 12],
          'symbol-sort-key': ['-', 0, ['get', 'rank']] },
        paint: { 'text-color': ['match', ['get', 'kind'], ['suburb', 'quarter', 'neighbourhood'], t.suburb, t.city],
          'text-halo-color': t.halo, 'text-halo-width': 1.6 } },
    ],
  };
  const result = themeName === 'topo' ? addTerrain(style, t, terrain, 'boundaries', 'street-names') : style;
  if (satellite !== false && satellite != null) {
    addSatellite(result, typeof satellite === 'number' ? satellite : 0.85);
  }
  return result;
}

// ------------------------------------------------------------------ outdoor overlay
// Marked trails (source "trails", /tiles/trails) in the Israeli marking colours, and
// nature reserves / national parks (source "parks", /tiles/parks). Drawn on every basemap.

/** Trail mark colours per theme (black trails become light on the night map). */
export const TRAIL_MARKS = {
  light: { red: '#D7261E', blue: '#1D5FD6', green: '#15803D', black: '#1F2937', orange: '#E46F0A', purple: '#7E3AC9', yellow: '#C99A06', other: '#6B7280' },
  dark: { red: '#F87171', blue: '#60A5FA', green: '#4ADE80', black: '#D1D5DB', orange: '#FB923C', purple: '#C084FC', yellow: '#FACC15', other: '#9CA3AF' },
};

export function outdoorLayers(themeName) {
  const dark = themeName === 'dark';
  const nature = themeName === 'nature';
  const m = TRAIL_MARKS[dark ? 'dark' : 'light'];
  const markColor = ['match', ['get', 'mark'], ...Object.entries(m).flat(), m.other];
  const inpaColor = dark ? '#4ADE80' : '#1B6E2A';
  const parkColor = dark ? '#86C98F' : '#4D7C0F';
  const width = zw([[8, ['case', ['==', ['get', 'network'], 'nwn'], 2.2, 1.4]], [12, 2.2], [15, 3.2], [18, 5]]);
  return [
    { id: 'parks-fill', type: 'fill', source: 'parks', 'source-layer': 'parks', minzoom: 8,
      paint: { 'fill-color': ['case', ['get', 'inpa'], inpaColor, parkColor], 'fill-opacity': nature ? 0.12 : 0.07 } },
    { id: 'parks-outline', type: 'line', source: 'parks', 'source-layer': 'parks', minzoom: 9,
      layout: { 'line-join': 'round' },
      paint: { 'line-color': ['case', ['get', 'inpa'], inpaColor, parkColor], 'line-width': ['interpolate', ['linear'], ['zoom'], 9, 0.8, 14, 1.8],
        'line-opacity': 0.7, 'line-dasharray': ['literal', [3, 2]] } },
    { id: 'trails-casing', type: 'line', source: 'trails', 'source-layer': 'trails', minzoom: 8,
      layout: { 'line-join': 'round', 'line-cap': 'round' },
      paint: { 'line-color': dark ? '#111418' : '#FFFFFF', 'line-opacity': nature ? 0.9 : 0.7,
        'line-width': zw([[8, 3], [12, 4.2], [15, 6], [18, 8]]) } },
    { id: 'trails-line', type: 'line', source: 'trails', 'source-layer': 'trails', minzoom: 8,
      filter: ['!', ['get', 'bike']],
      layout: { 'line-join': 'round', 'line-cap': 'round' },
      paint: { 'line-color': markColor, 'line-width': width, 'line-opacity': nature ? 1 : 0.85 } },
    { id: 'trails-bike', type: 'line', source: 'trails', 'source-layer': 'trails', minzoom: 8,
      filter: ['get', 'bike'],
      layout: { 'line-join': 'round' },
      paint: { 'line-color': markColor, 'line-width': width, 'line-opacity': nature ? 1 : 0.85, 'line-dasharray': ['literal', [2.5, 1.2]] } },
    { id: 'trails-label', type: 'symbol', source: 'trails', 'source-layer': 'trails', minzoom: 13,
      filter: ['!=', ['get', 'name'], ''],
      layout: { 'text-field': ['get', 'name'], 'text-font': REG, 'text-size': 11, 'symbol-placement': 'line',
        'symbol-spacing': 450, 'text-max-angle': 30, 'text-offset': [0, 0.9] },
      paint: { 'text-color': markColor, 'text-halo-color': dark ? '#121418' : '#FFFFFF', 'text-halo-width': 1.5 } },
  ];
}
