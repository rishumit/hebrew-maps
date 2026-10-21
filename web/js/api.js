// Thin client for the backend API with cancellation, timeouts and Hebrew errors.

export class ApiError extends Error {
  constructor(message, status, code) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

async function request(path, { params, method = 'GET', body, signal, timeout = 15000 } = {}) {
  const url = new URL(path, location.origin);
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v === undefined || v === null || v === '') continue;
      if (Array.isArray(v)) v.forEach((x) => url.searchParams.append(k, x));
      else url.searchParams.set(k, v);
    }
  }
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort('timeout'), timeout);
  if (signal) signal.addEventListener('abort', () => ctrl.abort(signal.reason), { once: true });
  let resp;
  try {
    resp = await fetch(url, {
      method,
      signal: ctrl.signal,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch (e) {
    clearTimeout(timer);
    if (signal?.aborted) throw e;
    throw new ApiError(ctrl.signal.reason === 'timeout' ? 'השרת לא הגיב בזמן. נסו שוב.' : 'אין חיבור לשרת. בדקו את החיבור ונסו שוב.', 0, 'network');
  }
  clearTimeout(timer);
  let data = null;
  try {
    data = await resp.json();
  } catch {
    /* empty body */
  }
  if (!resp.ok) {
    throw new ApiError(data?.error?.message || 'אירעה שגיאה. נסו שוב.', resp.status, data?.error?.code);
  }
  return data;
}

export const api = {
  meta: () => request('/api/meta'),
  categories: () => request('/api/categories'),
  autocomplete: (q, center, signal) =>
    request('/api/search/autocomplete', { params: { q, lat: center?.lat?.toFixed(4), lon: center?.lon?.toFixed(4), limit: 8 }, signal }),
  search: (query, { center, user, category, kosher, openNow, limit = 40 } = {}, signal) =>
    request('/api/search', {
      method: 'POST',
      signal,
      body: {
        query,
        location: center ? { lat: center.lat, lon: center.lon } : undefined,
        user_location: user ? { lat: user.lat, lon: user.lon } : undefined,
        filters: { category, kosher, open_now: openNow },
        limit,
      },
    }),
  poi: (id, signal) => request(`/api/poi/${encodeURIComponent(id)}`, { signal }),
  place: (id, signal) => request(`/api/place/${encodeURIComponent(id)}`, { signal }),
  street: (id, signal) => request(`/api/street/${encodeURIComponent(id)}`, { signal }),
  trail: (id, signal) => request(`/api/trails/${encodeURIComponent(id)}`, { signal }),
  nearby: (lat, lon, opts = {}, signal) => request('/api/poi', { params: { lat, lon, ...opts }, signal }),
  reverse: (lat, lon, signal) => request('/api/reverse', { params: { lat: lat.toFixed(6), lon: lon.toFixed(6) }, signal }),
  route: (from, to, mode, signal) =>
    request('/api/route', {
      params: { from: `${from.lat.toFixed(6)},${from.lon.toFixed(6)}`, to: `${to.lat.toFixed(6)},${to.lon.toFixed(6)}`, mode },
      signal,
      timeout: 25000,
    }),
  transit: (from, to, { time, mode = 'depart' } = {}, signal) =>
    request('/api/transit', {
      params: { from: `${from.lat.toFixed(6)},${from.lon.toFixed(6)}`, to: `${to.lat.toFixed(6)},${to.lon.toFixed(6)}`, time, mode },
      signal,
      timeout: 25000,
    }),
  transitStop: (id, time, signal, limit) => request(`/api/transit/stop/${encodeURIComponent(id)}`, { params: { time, limit }, signal }),
  transitStops: (lat, lon, { radius, mode, code, limit } = {}, signal) =>
    request('/api/transit/stops', { params: { lat: lat.toFixed(6), lon: lon.toFixed(6), radius, mode, code, limit }, signal }),
  transitStopRoutes: (id, signal) => request(`/api/transit/stop/${encodeURIComponent(id)}/routes`, { signal }),
  transitRoute: (id, { direction, fromStop } = {}, signal) =>
    request(`/api/transit/route/${encodeURIComponent(id)}`, { params: { direction, from_stop: fromStop }, signal }),
  transitStatus: () => request('/api/transit/status'),
  minyanimNear: (lat, lon, { radius, prayer, after, day } = {}, signal) =>
    request('/api/minyanim/near', { params: { lat: lat.toFixed(6), lon: lon.toFixed(6), radius, prayer, after, day }, signal }),
  // aerial photos kept offline (hmaps/api/satellite.py)
  satelliteStatus: () => request('/api/satellite/status'),
  satelliteEstimate: (bbox, minzoom, maxzoom) =>
    request('/api/satellite/estimate', { params: { bbox: bbox.join(','), minzoom, maxzoom } }),
  satelliteDownload: (bbox, minzoom, maxzoom) =>
    request('/api/satellite/download', { method: 'POST', body: { bbox, minzoom, maxzoom } }),
  satelliteCancel: () => request('/api/satellite/download/cancel', { method: 'POST', body: {} }),
};
