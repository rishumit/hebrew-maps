// Test double for maplibre-gl used by the browser tests (the real library is
// installed in the Docker image). It renders nothing, but implements the API
// surface the app uses and *validates every style layer* it receives, so
// expression mistakes are caught in CI. Errors go to window.__styleErrors.
(function () {
  const OPS = new Set(['literal', 'get', 'has', '!', '!=', '==', '<', '<=', '>', '>=', 'all', 'any', 'case', 'coalesce', 'match',
    'interpolate', 'step', 'zoom', 'geometry-type', 'concat', 'to-string', 'to-number', 'format', '+', '-', '*', '/', '%', '^',
    'cos', 'sin', 'tan', 'min', 'max', 'number', 'string', 'boolean', 'at', 'length', 'in', 'image', 'let', 'var', 'id',
    'properties', 'feature-state', 'upcase', 'downcase', 'number-format', 'sqrt', 'abs', 'round', 'floor', 'ceil', 'ln',
    'log10', 'log2', 'pi', 'e', 'rgb', 'rgba', 'to-color', 'to-boolean', 'typeof', 'slice', 'index-of', 'within', 'collator',
    'line-progress', 'heatmap-density', 'is-supported-script', 'resolved-locale', 'array', 'object']);
  const PLAIN_ARRAY_PROPS = new Set(['text-font', 'text-offset', 'icon-offset', 'line-dasharray', 'text-variable-anchor',
    'text-translate', 'icon-translate', 'fill-translate', 'line-translate', 'circle-translate']);
  const LAYER_TYPES = new Set(['background', 'fill', 'line', 'symbol', 'circle', 'heatmap', 'fill-extrusion', 'raster', 'hillshade']);
  const errors = (window.__styleErrors = window.__styleErrors || []);

  function hasZoom(e) {
    if (!Array.isArray(e)) return false;
    if (e[0] === 'zoom') return true;
    return e.some(hasZoom);
  }

  function checkExpr(e, where, topLevel) {
    if (!Array.isArray(e) || e.length === 0) return;
    if (typeof e[0] !== 'string') {
      e.forEach((x) => checkExpr(x, where, false));
      return;
    }
    const op = e[0];
    if (!OPS.has(op)) {
      errors.push(`${where}: unknown operator "${op}"`);
      return;
    }
    if (op === 'literal') return;
    if ((op === 'interpolate' || op === 'step') && hasZoom(op === 'interpolate' ? e[2] : e[1])) {
      if (!topLevel) errors.push(`${where}: zoom-based ${op} must be at the top level`);
      if (op === 'interpolate') {
        const kind = e[1];
        if (!Array.isArray(kind) || !['linear', 'exponential', 'cubic-bezier'].includes(kind[0])) errors.push(`${where}: bad interpolation type`);
        for (let i = 3; i < e.length; i += 2) {
          if (typeof e[i] !== 'number') errors.push(`${where}: interpolate stop input must be a number`);
          if (hasZoom(e[i + 1])) errors.push(`${where}: nested zoom expression in interpolate output`);
          checkExpr(e[i + 1], where, false);
        }
        if (e.length < 5 || (e.length - 3) % 2 !== 0) errors.push(`${where}: interpolate needs input/output pairs`);
      } else {
        for (let i = 2; i < e.length; i++) {
          if (hasZoom(e[i]) && i % 2 === 0) errors.push(`${where}: nested zoom expression in step output`);
          checkExpr(e[i], where, false);
        }
      }
      return;
    }
    if (op === 'interpolate') {
      for (let i = 2; i < e.length; i++) checkExpr(e[i], where, false);
      return;
    }
    if (op === 'match') {
      if (e.length < 5 || e.length % 2 !== 1) errors.push(`${where}: match must have input, label/output pairs and a fallback`);
      checkExpr(e[1], where, false);
      for (let i = 2; i < e.length - 1; i += 2) {
        const label = e[i];
        if (Array.isArray(label) && !label.every((x) => typeof x === 'string' || typeof x === 'number')) errors.push(`${where}: match label array must be literal`);
        checkExpr(e[i + 1], where, false);
      }
      checkExpr(e[e.length - 1], where, false);
      return;
    }
    if (op === 'format') {
      for (let i = 1; i < e.length; i += 2) checkExpr(e[i], where, false);
      return;
    }
    if (hasZoom(e) && op !== 'zoom') {
      // zoom used outside of a top-level interpolate/step
      const inner = e.slice(1).some((x) => Array.isArray(x) && (x[0] === 'interpolate' || x[0] === 'step') && hasZoom(x));
      if (inner || e.slice(1).some((x) => Array.isArray(x) && x[0] === 'zoom')) errors.push(`${where}: zoom must be the input of a top-level interpolate/step`);
    }
    e.slice(1).forEach((x) => checkExpr(x, where, false));
  }

  function validateLayer(layer, sources) {
    const w = `layer "${layer.id}"`;
    if (!layer.id) errors.push('layer without id');
    if (!LAYER_TYPES.has(layer.type)) errors.push(`${w}: bad type ${layer.type}`);
    if (layer.type !== 'background') {
      if (!sources[layer.source]) errors.push(`${w}: unknown source ${layer.source}`);
      else if (sources[layer.source].type === 'vector' && !layer['source-layer']) errors.push(`${w}: vector layer needs source-layer`);
    }
    if (layer.filter) {
      if (hasZoom(layer.filter)) errors.push(`${w}: zoom in filter`);
      checkExpr(layer.filter, `${w} filter`, true);
    }
    for (const group of ['paint', 'layout']) {
      for (const [prop, val] of Object.entries(layer[group] || {})) {
        const where = `${w} ${prop}`;
        if (!/^(fill|line|text|icon|symbol|circle|background|visibility|heatmap|raster|hillshade)/.test(prop)) errors.push(`${where}: unknown property`);
        if (Array.isArray(val)) {
          if (PLAIN_ARRAY_PROPS.has(prop) && val.every((x) => typeof x !== 'object')) continue;
          checkExpr(val, where, true);
        }
      }
    }
  }

  class Evented {
    constructor() { this._l = {}; }
    on(type, a, b) {
      const [layer, fn] = typeof a === 'function' ? [null, a] : [a, b];
      (this._l[type] = this._l[type] || []).push({ layer, fn });
      return this;
    }
    once(type, fn) {
      const wrap = (e) => { this.off(type, wrap); fn(e); };
      return this.on(type, wrap);
    }
    off(type, fn) {
      this._l[type] = (this._l[type] || []).filter((x) => x.fn !== fn);
      return this;
    }
    fire(type, data = {}) {
      (this._l[type] || []).slice().forEach(({ layer, fn }) => {
        if (layer) {
          if (data.features && (!data.layer || data.layer === layer)) fn({ ...data, type });
        } else fn({ ...data, type, target: this });
      });
    }
  }

  class Map extends Evented {
    constructor(opts) {
      super();
      this._container = typeof opts.container === 'string' ? document.getElementById(opts.container) : opts.container;
      this._canvas = document.createElement('canvas');
      this._canvas.className = 'maplibregl-canvas';
      this._canvas.tabIndex = 0;
      this._canvas.style.cssText = 'width:100%;height:100%;display:block;background:repeating-linear-gradient(45deg,#eef2f0,#eef2f0 14px,#e6ebe8 14px,#e6ebe8 28px)';
      const wrap = document.createElement('div');
      wrap.className = 'maplibregl-canvas-container';
      wrap.style.cssText = 'position:absolute;inset:0';
      wrap.appendChild(this._canvas);
      this._container.classList.add('maplibregl-map');
      this._container.appendChild(wrap);
      ['top-left', 'top-right', 'bottom-left', 'bottom-right'].forEach((p) => {
        const d = document.createElement('div');
        d.className = `maplibregl-ctrl-${p}`;
        d.style.cssText = `position:absolute;${p.includes('bottom') ? 'bottom' : 'top'}:0;${p.includes('left') ? 'left' : 'right'}:0;z-index:2`;
        this._container.appendChild(d);
      });
      this._center = { lng: opts.center?.[0] ?? 35, lat: opts.center?.[1] ?? 31.6 };
      this._zoom = opts.zoom ?? 7;
      this._bearing = 0;
      this._pitch = 0;
      this._maxPitch = opts.maxPitch ?? 60;
      this._terrain = null;
      this._sky = null;
      this._images = new Set();
      this._applyStyle(opts.style);
      window.__map = this;
      setTimeout(() => this.fire('load'), 10);
    }
    _applyStyle(style) {
      this._sources = {};
      this._layers = [];
      this.style = style;
      window.__lastStyle = style;
      for (const [id, src] of Object.entries(style.sources || {})) this._sources[id] = this._makeSource(id, src);
      (style.layers || []).forEach((l) => this.addLayer(l));
      setTimeout(() => this.fire('style.load'), 20);
    }
    _makeSource(id, def) {
      return { id, ...def, data: def.data, setData(d) { this.data = d; window.__sourceData = window.__sourceData || {}; window.__sourceData[id] = d; },
        setTiles(t) { this.tiles = t; window.__poiTiles = t; } };
    }
    setStyle(style) { this._applyStyle(style); return this; }
    addSource(id, def) { if (this._sources[id]) throw new Error(`source ${id} exists`); this._sources[id] = this._makeSource(id, def); }
    getSource(id) { return this._sources[id]; }
    removeSource(id) { delete this._sources[id]; }
    addLayer(layer) { validateLayer(layer, this._sources); this._layers.push(layer); }
    getLayer(id) { return this._layers.find((l) => l.id === id); }
    removeLayer(id) { this._layers = this._layers.filter((l) => l.id !== id); }
    getPitch() { return this._pitch; }
    setMaxPitch(p) { this._maxPitch = p; if (this._pitch > p) this._pitch = p; return this; }
    getMaxPitch() { return this._maxPitch; }
    setTerrain(t) {
      if (t && !this._sources[t.source]) errors.push(`terrain source ${t.source} missing`);
      this._terrain = t; window.__terrain = t; return this;
    }
    getTerrain() { return this._terrain; }
    setSky(s) { this._sky = s; window.__sky = s; return this; }
    cameraForBounds(b) {
      const [[w, s], [e, n]] = b;
      return { center: [(w + e) / 2, (s + n) / 2], zoom: 13 };
    }
    getStyle() { return { ...this.style, layers: this._layers, sources: this._sources }; }
    setLayoutProperty(id, p, v) { const l = this.getLayer(id); if (l) (l.layout = l.layout || {})[p] = v; }
    setPaintProperty(id, p, v) { const l = this.getLayer(id); if (l) (l.paint = l.paint || {})[p] = v; }
    setFilter(id, f) { const l = this.getLayer(id); if (l) l.filter = f; }
    hasImage(id) { return this._images.has(id); }
    addImage(id, img) {
      if (!img || !img.width) errors.push(`image ${id} invalid`);
      this._images.add(id);
    }
    addControl(ctrl, pos = 'top-right') {
      const el = ctrl.onAdd ? ctrl.onAdd(this) : document.createElement('div');
      this._container.querySelector(`.maplibregl-ctrl-${pos}`).appendChild(el);
      return this;
    }
    getCanvas() { return this._canvas; }
    getContainer() { return this._container; }
    getCenter() { return { ...this._center, toArray: () => [this._center.lng, this._center.lat] }; }
    getZoom() { return this._zoom; }
    getBearing() { return this._bearing; }
    getBounds() {
      const d = 360 / 2 ** this._zoom;
      return { getWest: () => this._center.lng - d, getEast: () => this._center.lng + d, getSouth: () => this._center.lat - d / 2,
        getNorth: () => this._center.lat + d / 2, toArray: () => [[this._center.lng - d, this._center.lat - d / 2], [this._center.lng + d, this._center.lat + d / 2]] };
    }
    _move(o) {
      if (o.center) this._center = Array.isArray(o.center) ? { lng: o.center[0], lat: o.center[1] } : { lng: o.center.lng ?? o.center.lon, lat: o.center.lat };
      if (o.zoom != null) this._zoom = o.zoom;
      if (o.bearing != null) { this._bearing = o.bearing; this.fire('rotate'); }
      if (o.pitch != null) this._pitch = Math.min(o.pitch, this._maxPitch);
      this.fire('movestart');
      setTimeout(() => this.fire('moveend'), 5);
      window.__lastCamera = { center: this._center, zoom: this._zoom, pitch: this._pitch, bearing: this._bearing };
    }
    jumpTo(o) { this._move(o); }
    flyTo(o) { this._move(o); }
    easeTo(o) { this._move(o); }
    fitBounds(b, o = {}) {
      const [[w, s], [e, n]] = b;
      this._move({ center: [(w + e) / 2, (s + n) / 2], zoom: Math.min(o.maxZoom ?? 16, 14) });
      window.__lastFit = b;
    }
    queryRenderedFeatures() { return window.__renderedFeatures || []; }
    project(ll) { return { x: 100, y: 100 }; }
    unproject(p) { return { lng: this._center.lng, lat: this._center.lat }; }
    resize() {}
    triggerRepaint() {}
    remove() {}
    // test helpers: simulate the pointer resting on / leaving a POI icon
    __hoverPoi(props, coords) {
      this.fire('mousemove', { layer: 'pois', features: [{ properties: props, geometry: { type: 'Point', coordinates: coords } }], lngLat: { lng: coords[0], lat: coords[1] }, point: { x: 100, y: 100 } });
    }
    __leavePoi() { this.fire('mouseleave', { layer: 'pois', features: [] }); }
    // test helper: simulate a click on a POI feature
    __clickPoi(props, coords) {
      this.fire('click', { layer: 'pois', features: [{ properties: props, geometry: { type: 'Point', coordinates: coords } }], lngLat: { lng: coords[0], lat: coords[1] }, point: { x: 50, y: 50 } });
    }
  }

  class Control { onAdd() { const d = document.createElement('div'); d.className = 'maplibregl-ctrl'; return d; } }
  class AttributionControl extends Control {
    constructor(o = {}) { super(); this.o = o; }
    onAdd() { const d = super.onAdd(); d.className += ' maplibregl-ctrl-attrib'; d.innerHTML = this.o.customAttribution || ''; return d; }
  }
  class ScaleControl extends Control {
    onAdd() { const d = super.onAdd(); d.className += ' maplibregl-ctrl-scale'; d.textContent = '1 ק״מ'; return d; }
  }

  let rtl = 'unavailable';
  window.maplibregl = {
    Map, AttributionControl, ScaleControl, NavigationControl: Control,
    setRTLTextPlugin: () => { rtl = 'loaded'; return Promise.resolve(); },
    getRTLTextPluginStatus: () => rtl,
  };
})();
