// Service worker: offline app shell + cached map tiles and glyphs.
const VERSION = 'hmaps-1.1.1';
const SHELL_CACHE = `${VERSION}-shell`;
const TILE_CACHE = 'hmaps-tiles-v1';
const MAX_TILES = 4000;
const SHELL = [
  '/', '/index.html', '/css/app.css', '/manifest.webmanifest', '/icons/icon.svg', '/icons/icon-192.png',
  '/js/app.js', '/js/api.js', '/js/combobox.js', '/js/directions.js', '/js/icons.js', '/js/map.js',
  '/js/panel.js', '/js/style.js', '/js/util.js', '/js/views.js', '/js/trails.js',
  '/js/elevation.js', '/js/hovercard.js',
  '/vendor/maplibre-gl.js', '/vendor/maplibre-gl.css', '/vendor/mapbox-gl-rtl-text.js',
  '/js/transit.js', '/css/transit.css',
];

self.addEventListener('install', (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(SHELL_CACHE);
    await Promise.all(SHELL.map((u) => cache.add(u).catch(() => null)));
    await self.skipWaiting();
  })());
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    for (const key of await caches.keys()) {
      if (key.startsWith('hmaps-') && key !== SHELL_CACHE && key !== TILE_CACHE) await caches.delete(key);
    }
    await self.clients.claim();
  })());
});

async function trim(cache) {
  const keys = await cache.keys();
  if (keys.length > MAX_TILES) {
    await Promise.all(keys.slice(0, keys.length - MAX_TILES).map((k) => cache.delete(k)));
  }
}

let writes = 0;
async function tileStrategy(request) {
  const cache = await caches.open(TILE_CACHE);
  const cached = await cache.match(request);
  const network = fetch(request).then((resp) => {
    if (resp.ok && resp.status === 200) {
      cache.put(request, resp.clone());
      if (++writes % 200 === 0) trim(cache);
    }
    return resp;
  }).catch(() => cached || new Response(null, { status: 204 }));
  return cached || network;
}

async function shellStrategy(request) {
  const cache = await caches.open(SHELL_CACHE);
  const cached = await cache.match(request, { ignoreSearch: true });
  const network = fetch(request).then((resp) => {
    if (resp.ok) cache.put(request, resp.clone());
    return resp;
  }).catch(() => cached || caches.match('/index.html'));
  return cached || network;
}

async function networkFirst(request) {
  const cache = await caches.open(SHELL_CACHE);
  try {
    const resp = await fetch(request);
    if (resp.ok) cache.put(request, resp.clone());
    return resp;
  } catch {
    const cached = await cache.match(request);
    if (cached) return cached;
    return new Response(JSON.stringify({ error: { code: 'offline', message: 'אין חיבור לשרת' } }),
      { status: 503, headers: { 'Content-Type': 'application/json' } });
  }
}

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET' || url.origin !== self.location.origin) return;
  const p = url.pathname;
  if (p.startsWith('/tiles/') || p.startsWith('/fonts/')) event.respondWith(tileStrategy(event.request));
  else if (p === '/api/meta' || p === '/api/categories') event.respondWith(networkFirst(event.request));
  else if (p.startsWith('/api/') || p.startsWith('/reports') || p === '/metrics') return;
  else event.respondWith(shellStrategy(event.request));
});
