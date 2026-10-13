// Map view: MapLibre setup, basemap themes, overlay layers and map interactions.
/* global maplibregl */

import { markerImage } from './icons.js';
import { fallbackStyle, omtStyle, outdoorLayers, THEMES } from './style.js';
import { bus, prefersReducedMotion } from './util.js';

const EMPTY = { type: 'FeatureCollection', features: [] };
const OUTDOOR_LAYER_IDS = ['parks-fill', 'parks-outline', 'trails-casing', 'trails-line', 'trails-bike', 'trails-label'];
const TRAIL_CLICK_LAYERS = ['trails-line', 'trails-bike'];
const ISRAEL_CENTER = [35.0, 31.6];

export class MapView {
  constructor(container, meta, categories, initial) {
    this.meta = meta;
    this.categories = categories;
    this.catColor = Object.fromEntries(categories.map((c) => [c.id, c.color]));
    this.theme = initial.theme || 'light';
    this.poiFilter = { cats: [], kosher: false };
    this.showPois = true;
    this.showOutdoor = true; // marked trails + nature reserves
    this.data = { results: EMPTY, selection: EMPTY, route: EMPTY, location: EMPTY, highlight: EMPTY, endpoints: EMPTY };

    try {
      if (maplibregl.getRTLTextPluginStatus && maplibregl.getRTLTextPluginStatus() === 'unavailable') {
        const p = maplibregl.setRTLTextPlugin('/vendor/mapbox-gl-rtl-text.js', true);
        if (p && p.catch) p.catch((e) => console.warn('RTL plugin failed to load', e));
      }
    } catch (e) {
      console.warn('RTL plugin', e);
    }

    const maxBounds = [[meta.bounds[0] - 1.5, meta.bounds[1] - 1.5], [meta.bounds[2] + 1.5, meta.bounds[3] + 1.5]];
    this.map = new maplibregl.Map({
      container,
      style: this._baseStyle(),
      center: initial.center ? [initial.center.lon, initial.center.lat] : ISRAEL_CENTER,
      zoom: initial.zoom ?? 7.3,
      minZoom: 5,
      maxZoom: 19,
      maxBounds,
      attributionControl: false,
      hash: false,
      dragRotate: true,
      pitchWithRotate: false,
      cooperativeGestures: false,
      locale: {
        'AttributionControl.ToggleAttribution': 'הצג/הסתר ייחוס',
        'AttributionControl.MapFeedback': 'משוב על המפה',
        'FullscreenControl.Enter': 'מסך מלא',
        'FullscreenControl.Exit': 'יציאה ממסך מלא',
        'GeolocateControl.FindMyLocation': 'המיקום שלי',
        'GeolocateControl.LocationNotAvailable': 'המיקום אינו זמין',
        'LogoControl.Title': 'MapLibre',
        'NavigationControl.ResetBearing': 'איפוס כיוון לצפון',
        'NavigationControl.ZoomIn': 'הגדלה',
        'NavigationControl.ZoomOut': 'הקטנה',
        'ScaleControl.Feet': 'רגל',
        'ScaleControl.Meters': 'מ׳',
        'ScaleControl.Kilometers': 'ק״מ',
        'ScaleControl.Miles': 'מייל',
        'ScaleControl.NauticalMiles': 'מייל ימי',
        'CooperativeGesturesHandler.WindowsHelpText': 'השתמשו ב-Ctrl + גלילה כדי לשנות את גודל המפה',
        'CooperativeGesturesHandler.MacHelpText': 'השתמשו ב-⌘ + גלילה כדי לשנות את גודל המפה',
        'CooperativeGesturesHandler.MobileHelpText': 'השתמשו בשתי אצבעות כדי להזיז את המפה',
      },
    });
    this.map.addControl(new maplibregl.AttributionControl({ compact: true, customAttribution: meta.attribution }), 'bottom-left');
    this.map.addControl(new maplibregl.ScaleControl({ unit: 'metric', maxWidth: 90 }), 'bottom-left');

    this.map.on('style.load', () => this._installOverlays());
    this.map.on('styleimagemissing', (e) => this._missingImage(e.id));
    this.map.on('error', (e) => {
      const msg = e?.error?.message || '';
      if (!/Failed to fetch|AbortError|404|204/.test(msg)) console.warn('map error:', msg);
    });
    this.map.on('moveend', () => bus.emit('map:moved', this.view()));
    this.map.on('rotate', () => bus.emit('map:rotate', this.map.getBearing()));
    this._bindInteractions();
    const canvas = this.map.getCanvas();
    canvas.setAttribute('aria-label', 'מפה אינטראקטיבית של ישראל. חיצים להזזה, פלוס ומינוס לשינוי גודל');
    canvas.setAttribute('role', 'application');
  }

  // ----------------------------------------------------------------- styles
  _resolvedTheme() {
    return this.theme === 'auto' ? (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light') : this.theme;
  }

  _baseStyle() {
    const theme = this._resolvedTheme();
    const b = this.meta.basemap;
    const terrain = this.meta.terrain;
    if (b.type === 'openmaptiles' && b.tilejson) return omtStyle(theme, { tilejson: b.tilejson, glyphs: b.glyphs, terrain });
    return fallbackStyle(theme, { tiles: b.fallback_tiles, glyphs: b.glyphs, terrain });
  }

  setTheme(theme) {
    this.theme = theme;
    this.map.setStyle(this._baseStyle(), { diff: false });
  }

  // ----------------------------------------------------------------- overlays
  _addLayerSafe(layer, before) {
    try {
      if (!this.map.getLayer(layer.id)) this.map.addLayer(layer, before);
    } catch (e) {
      console.warn('layer failed', layer.id, e);
    }
  }

  _poiTilesUrl() {
    const u = new URL(this.meta.poi_tiles.replace('{z}/{x}/{y}', '__Z__/__X__/__Y__'));
    if (this.poiFilter.cats.length) u.searchParams.set('cats', this.poiFilter.cats.join(','));
    if (this.poiFilter.kosher) u.searchParams.set('kosher', '1');
    return u.toString().replace('__Z__/__X__/__Y__', '{z}/{x}/{y}');
  }

  _installOverlays() {
    const m = this.map;
    const t = THEMES[this._resolvedTheme()] || THEMES.light;
    const dark = this._resolvedTheme() === 'dark';
    for (const [id, data] of Object.entries(this.data)) {
      if (!m.getSource(id)) m.addSource(id, { type: 'geojson', data });
    }
    if (!m.getSource('pois')) m.addSource('pois', { type: 'vector', tiles: [this._poiTilesUrl()], minzoom: 6, maxzoom: 17 });
    this._installOutdoor();

    this._addLayerSafe({ id: 'highlight-fill', type: 'fill', source: 'highlight',
      filter: ['match', ['geometry-type'], ['Polygon', 'MultiPolygon'], true, false],
      paint: { 'fill-color': '#2563EB', 'fill-opacity': 0.06 } });
    this._addLayerSafe({ id: 'highlight-line', type: 'line', source: 'highlight',
      layout: { 'line-join': 'round', 'line-cap': 'round' },
      paint: { 'line-color': '#DC2626', 'line-width': ['match', ['geometry-type'], ['LineString', 'MultiLineString'], 6, 2.5],
        'line-opacity': 0.8 } });

    // routes: alternatives under the selected one
    this._addLayerSafe({ id: 'route-alt', type: 'line', source: 'route', filter: ['!=', ['get', 'selected'], true],
      layout: { 'line-join': 'round', 'line-cap': 'round' },
      paint: { 'line-color': dark ? '#6B7280' : '#9CA3AF', 'line-width': ['interpolate', ['linear'], ['zoom'], 8, 4, 16, 9] } });
    this._addLayerSafe({ id: 'route-casing', type: 'line', source: 'route', filter: ['==', ['get', 'selected'], true],
      layout: { 'line-join': 'round', 'line-cap': 'round' },
      paint: { 'line-color': dark ? '#0B1F4D' : '#1E3A8A', 'line-width': ['interpolate', ['linear'], ['zoom'], 8, 7, 16, 13] } });
    this._addLayerSafe({ id: 'route-line', type: 'line', source: 'route', filter: ['==', ['get', 'selected'], true],
      layout: { 'line-join': 'round', 'line-cap': 'round' },
      paint: { 'line-color': ['match', ['get', 'mode'], 'foot', '#0EA5E9', 'bike', '#10B981', '#2563EB'],
        'line-width': ['interpolate', ['linear'], ['zoom'], 8, 4.5, 16, 9] } });

    // POIs
    this._addLayerSafe({ id: 'pois-cluster', type: 'circle', source: 'pois', 'source-layer': 'pois', filter: ['has', 'count'],
      paint: {
        'circle-color': ['match', ['get', 'cat'], ...Object.entries(this.catColor).flat(), '#4B5563'],
        'circle-radius': ['interpolate', ['linear'], ['get', 'count'], 2, 13, 50, 20, 500, 28],
        'circle-stroke-color': '#FFFFFF', 'circle-stroke-width': 2, 'circle-opacity': 0.9,
      } });
    this._addLayerSafe({ id: 'pois-cluster-count', type: 'symbol', source: 'pois', 'source-layer': 'pois', filter: ['has', 'count'],
      layout: { 'text-field': ['to-string', ['get', 'count']], 'text-font': ['Noto Sans Medium'], 'text-size': 12, 'text-allow-overlap': true },
      paint: { 'text-color': '#FFFFFF' } });
    this._addLayerSafe({ id: 'pois', type: 'symbol', source: 'pois', 'source-layer': 'pois', filter: ['!', ['has', 'count']],
      layout: {
        'icon-image': ['concat', 'poi:', ['get', 'icon'], ':', ['get', 'cat']],
        'icon-size': ['interpolate', ['linear'], ['zoom'], 10, 0.75, 16, 1],
        'icon-allow-overlap': false,
        'symbol-sort-key': ['-', 0, ['get', 'rank']],
        'text-field': ['step', ['zoom'], '', 14, ['get', 'name']],
        'text-font': ['Noto Sans Medium'],
        'text-size': 11.5,
        'text-anchor': 'top',
        'text-offset': [0, 1.05],
        'text-max-width': 8,
        'text-optional': true,
        visibility: this.showPois ? 'visible' : 'none',
      },
      paint: {
        'text-color': ['match', ['get', 'cat'], ...Object.entries(this.catColor).flatMap(([k, v]) => [k, dark ? '#E5E7EB' : v]), t.text],
        'text-halo-color': t.halo, 'text-halo-width': 1.5,
      } });

    // search results, endpoints, selection, my location
    this._addLayerSafe({ id: 'results', type: 'symbol', source: 'results',
      layout: { 'icon-image': 'pin:result', 'icon-anchor': 'bottom', 'icon-allow-overlap': true,
        'symbol-sort-key': ['get', 'order'],
        'text-field': ['get', 'label'], 'text-font': ['Noto Sans Medium'], 'text-size': 12, 'text-anchor': 'top', 'text-offset': [0, 0.2],
        'text-optional': true, 'text-max-width': 9 },
      paint: { 'text-color': dark ? '#FCA5A5' : '#B91C1C', 'text-halo-color': t.halo, 'text-halo-width': 1.5 } });
    this._addLayerSafe({ id: 'endpoints', type: 'symbol', source: 'endpoints',
      layout: { 'icon-image': ['concat', 'pin:', ['get', 'role']], 'icon-anchor': 'bottom', 'icon-allow-overlap': true } });
    this._addLayerSafe({ id: 'selection', type: 'symbol', source: 'selection',
      layout: { 'icon-image': 'pin:selected', 'icon-anchor': 'bottom', 'icon-allow-overlap': true, 'icon-size': 1.2 } });
    this._addLayerSafe({ id: 'location-accuracy', type: 'circle', source: 'location',
      paint: { 'circle-color': '#2563EB', 'circle-opacity': 0.12, 'circle-stroke-color': '#2563EB', 'circle-stroke-opacity': 0.3,
        'circle-stroke-width': 1,
        'circle-radius': ['interpolate', ['exponential', 2], ['zoom'], 0, 0, 22,
          ['/', ['*', ['get', 'accuracy'], 2147483648], ['*', 40075016.686, ['cos', ['/', ['*', ['get', 'lat'], 3.14159265], 180]]]]] } });
    this._addLayerSafe({ id: 'location-dot', type: 'circle', source: 'location',
      paint: { 'circle-color': '#2563EB', 'circle-radius': 7, 'circle-stroke-color': '#FFFFFF', 'circle-stroke-width': 3 } });
    bus.emit('map:ready');
  }

  /** Marked trails and nature reserves (under routes, selection and POIs). */
  _installOutdoor() {
    const m = this.map;
    const v = encodeURIComponent(this.meta.dataset?.version || '0');
    const url = (layer) => `${location.origin}/tiles/${layer}/{z}/{x}/{y}.pbf?v=${v}`;
    if (!m.getSource('parks')) m.addSource('parks', { type: 'vector', tiles: [url('parks')], minzoom: 8, maxzoom: 14 });
    if (!m.getSource('trails')) m.addSource('trails', { type: 'vector', tiles: [url('trails')], minzoom: 7, maxzoom: 15 });
    // lines and areas go under the basemap labels; trail names stay on top
    const firstLabel = (m.getStyle().layers || []).find((l) => l.type === 'symbol')?.id;
    for (const layer of outdoorLayers(this._resolvedTheme())) {
      layer.layout = { ...(layer.layout || {}), visibility: this.showOutdoor ? 'visible' : 'none' };
      this._addLayerSafe(layer, layer.type === 'symbol' ? undefined : firstLabel);
    }
  }

  setOutdoorVisible(visible) {
    this.showOutdoor = visible;
    for (const id of OUTDOOR_LAYER_IDS) {
      if (this.map.getLayer(id)) this.map.setLayoutProperty(id, 'visibility', visible ? 'visible' : 'none');
    }
  }

  /** Marked trails currently drawn (for the accessible list of what is on the map). */
  visibleTrails(limit = 30) {
    let feats = [];
    try {
      feats = this.map.queryRenderedFeatures({ layers: ['trails-line', 'trails-bike'].filter((l) => this.map.getLayer(l)) });
    } catch {
      return [];
    }
    const seen = new Map();
    for (const f of feats) {
      const p = f.properties;
      if (!p.name || seen.has(p.id)) continue;
      seen.set(p.id, { id: `trail-${p.id}`, type: 'trail', name: p.name, mark: p.mark, rank: p.rank || 0 });
    }
    return [...seen.values()].sort((a, b) => b.rank - a.rank).slice(0, limit);
  }

  _missingImage(id) {
    if (this.map.hasImage(id)) return;
    let img;
    if (id.startsWith('poi:')) {
      const [, icon, cat] = id.split(':');
      img = markerImage(icon, this.catColor[cat] || '#4B5563', { size: 26 });
    } else if (id === 'pin:result') {
      img = markerImage('pin', '#DC2626', { size: 30, shape: 'pin' });
    } else if (id === 'pin:selected') {
      img = markerImage('star', '#B91C1C', { size: 34, shape: 'pin' });
    } else if (id === 'pin:from') {
      img = markerImage('m_depart', '#059669', { size: 30, shape: 'pin' });
    } else if (id === 'pin:to') {
      img = markerImage('m_arrive', '#DC2626', { size: 30, shape: 'pin' });
    } else {
      return;
    }
    this.map.addImage(id, img, { pixelRatio: 2 });
  }

  // ----------------------------------------------------------------- data setters
  _set(id, data) {
    this.data[id] = data;
    const src = this.map.getSource(id);
    if (src) src.setData(data);
  }

  setResults(items) {
    this._set('results', {
      type: 'FeatureCollection',
      features: items.filter((r) => r.location).map((r, i) => ({
        type: 'Feature', id: i,
        properties: { id: r.id, label: r.name, order: i },
        geometry: { type: 'Point', coordinates: [r.location.lon, r.location.lat] },
      })),
    });
  }

  setSelection(loc) {
    this._set('selection', loc ? { type: 'FeatureCollection', features: [{ type: 'Feature', properties: {}, geometry: { type: 'Point', coordinates: [loc.lon, loc.lat] } }] } : EMPTY);
  }

  setHighlight(geometry) {
    this._set('highlight', geometry ? { type: 'FeatureCollection', features: [{ type: 'Feature', properties: {}, geometry }] } : EMPTY);
  }

  setRoutes(routes, selected, mode) {
    this._set('route', {
      type: 'FeatureCollection',
      features: routes.map((r, i) => ({ type: 'Feature', properties: { selected: i === selected, idx: i, mode }, geometry: r.geometry }))
        .sort((a, b) => Number(a.properties.selected) - Number(b.properties.selected)),
    });
  }

  setEndpoints(from, to) {
    const f = [];
    if (from) f.push({ type: 'Feature', properties: { role: 'from' }, geometry: { type: 'Point', coordinates: [from.lon, from.lat] } });
    if (to) f.push({ type: 'Feature', properties: { role: 'to' }, geometry: { type: 'Point', coordinates: [to.lon, to.lat] } });
    this._set('endpoints', { type: 'FeatureCollection', features: f });
  }

  setLocation(pos) {
    this._set('location', pos ? { type: 'FeatureCollection', features: [{ type: 'Feature',
      properties: { accuracy: pos.accuracy || 0, lat: pos.lat },
      geometry: { type: 'Point', coordinates: [pos.lon, pos.lat] } }] } : EMPTY);
  }

  setPoiFilter({ cats = [], kosher = false } = {}) {
    this.poiFilter = { cats, kosher };
    const src = this.map.getSource('pois');
    if (src && src.setTiles) src.setTiles([this._poiTilesUrl()]);
  }

  setPoisVisible(visible) {
    this.showPois = visible;
    for (const id of ['pois', 'pois-cluster', 'pois-cluster-count']) {
      if (this.map.getLayer(id)) this.map.setLayoutProperty(id, 'visibility', visible ? 'visible' : 'none');
    }
  }

  // ----------------------------------------------------------------- camera
  view() {
    const c = this.map.getCenter();
    return { center: { lat: c.lat, lon: c.lng }, zoom: this.map.getZoom(), bounds: this.map.getBounds() };
  }

  center() {
    const c = this.map.getCenter();
    return { lat: c.lat, lon: c.lng };
  }

  _padding() {
    const panel = document.getElementById('panel');
    const mobile = window.matchMedia('(max-width: 767px)').matches;
    const open = panel && !panel.hidden;
    if (mobile) return { top: 120, bottom: open ? Math.min(window.innerHeight * 0.5, 420) : 60, left: 40, right: 40 };
    return { top: 90, bottom: 60, left: 60, right: open ? 440 : 60 };
  }

  flyTo(loc, zoom = 16) {
    const opts = { center: [loc.lon, loc.lat], zoom, padding: this._padding() };
    if (prefersReducedMotion()) this.map.jumpTo(opts);
    else this.map.flyTo({ ...opts, speed: 1.6, curve: 1.3, essential: true });
  }

  fitBounds(bbox, maxZoom = 16) {
    const [minx, miny, maxx, maxy] = bbox;
    if (!(isFinite(minx) && isFinite(maxy))) return;
    this.map.fitBounds([[minx, miny], [maxx, maxy]], { padding: this._padding(), maxZoom, duration: prefersReducedMotion() ? 0 : 900 });
  }

  fitPoints(points, maxZoom = 16) {
    if (!points.length) return;
    if (points.length === 1) return this.flyTo(points[0], maxZoom);
    let minx = 180, miny = 90, maxx = -180, maxy = -90;
    for (const p of points) {
      minx = Math.min(minx, p.lon); maxx = Math.max(maxx, p.lon);
      miny = Math.min(miny, p.lat); maxy = Math.max(maxy, p.lat);
    }
    this.fitBounds([minx, miny, maxx, maxy], maxZoom);
  }

  zoomBy(delta) {
    this.map.easeTo({ zoom: this.map.getZoom() + delta, duration: prefersReducedMotion() ? 0 : 250 });
  }

  resetNorth() {
    this.map.easeTo({ bearing: 0, pitch: 0, duration: prefersReducedMotion() ? 0 : 300 });
  }

  /** POIs currently rendered (for the accessible list of places in view). */
  visiblePois(limit = 60) {
    let feats = [];
    try {
      feats = this.map.queryRenderedFeatures({ layers: ['pois'] });
    } catch {
      return [];
    }
    const seen = new Set();
    const out = [];
    for (const f of feats.sort((a, b) => (b.properties.rank || 0) - (a.properties.rank || 0))) {
      const id = f.properties.id;
      if (seen.has(id)) continue;
      seen.add(id);
      out.push({ id: `poi-${id}`, name: f.properties.name, cat: f.properties.cat, sub: f.properties.sub, icon: f.properties.icon,
        location: { lon: f.geometry.coordinates[0], lat: f.geometry.coordinates[1] } });
      if (out.length >= limit) break;
    }
    return out;
  }

  // ----------------------------------------------------------------- interactions
  _bindInteractions() {
    const m = this.map;
    const pointer = (layer) => {
      m.on('mouseenter', layer, () => (m.getCanvas().style.cursor = 'pointer'));
      m.on('mouseleave', layer, () => (m.getCanvas().style.cursor = ''));
    };
    ['pois', 'pois-cluster', 'results'].forEach(pointer);
    // hover preview of a POI card (rendered by hovercard.js)
    let hovered = null;
    const hoverEnd = (immediate = false) => {
      if (hovered === null) return;
      hovered = null;
      bus.emit('poi:hoverend', { immediate });
    };
    m.on('mousemove', 'pois', (e) => {
      const f = e.features?.[0];
      if (!f || (m.isMoving && m.isMoving())) return;
      if (hovered === f.properties.id) return;
      hovered = f.properties.id;
      const coords = f.geometry.coordinates;
      bus.emit('poi:hover', { id: `poi-${f.properties.id}`, props: f.properties, coords, point: m.project(coords) });
    });
    m.on('mouseleave', 'pois', () => hoverEnd());
    bus.on('poi:hoverreset', () => { hovered = null; }); // card closed by an action
    m.on('mousedown', () => hoverEnd(true));
    m.on('movestart', () => hoverEnd(true));
    m.getContainer().addEventListener('mouseleave', () => hoverEnd());
    m.on('click', 'pois', (e) => {
      const f = e.features?.[0];
      if (f) bus.emit('select', { id: `poi-${f.properties.id}`, location: { lon: f.geometry.coordinates[0], lat: f.geometry.coordinates[1] } });
    });
    m.on('click', 'results', (e) => {
      const f = e.features?.[0];
      if (f) bus.emit('select', { id: f.properties.id, location: { lon: f.geometry.coordinates[0], lat: f.geometry.coordinates[1] } });
    });
    m.on('click', 'pois-cluster', (e) => {
      const f = e.features?.[0];
      if (f) m.easeTo({ center: f.geometry.coordinates, zoom: Math.min(m.getZoom() + 2.2, 17), duration: prefersReducedMotion() ? 0 : 500 });
    });
    m.on('click', (e) => {
      const hits = m.queryRenderedFeatures(e.point, { layers: ['pois', 'results', 'pois-cluster'].filter((l) => m.getLayer(l)) });
      if (hits.length) return;
      // a marked trail under the pointer (a few pixels of tolerance: lines are thin)
      const box = [[e.point.x - 4, e.point.y - 4], [e.point.x + 4, e.point.y + 4]];
      const trail = this.showOutdoor
        ? m.queryRenderedFeatures(box, { layers: TRAIL_CLICK_LAYERS.filter((l) => m.getLayer(l)) })[0] : null;
      if (trail) {
        bus.emit('select', { id: `trail-${trail.properties.id}`, type: 'trail', name: trail.properties.name || undefined });
        return;
      }
      bus.emit('map:click', { lat: e.lngLat.lat, lon: e.lngLat.lng, point: e.point });
    });
    TRAIL_CLICK_LAYERS.forEach(pointer);
    m.on('contextmenu', (e) => {
      e.preventDefault?.();
      bus.emit('map:context', { lat: e.lngLat.lat, lon: e.lngLat.lng, point: e.point });
    });
    // long press on touch devices
    let timer = null, start = null;
    m.on('touchstart', (e) => {
      if (e.originalEvent.touches.length !== 1) return;
      start = e.point;
      timer = setTimeout(() => bus.emit('map:context', { lat: e.lngLat.lat, lon: e.lngLat.lng, point: e.point }), 600);
    });
    const cancel = (e) => {
      if (timer && (!e?.point || !start || Math.hypot(e.point.x - start.x, e.point.y - start.y) > 8)) {
        clearTimeout(timer);
        timer = null;
      }
    };
    m.on('touchmove', cancel);
    m.on('touchend', () => { clearTimeout(timer); timer = null; });
    m.on('movestart', () => { clearTimeout(timer); timer = null; });
  }
}
