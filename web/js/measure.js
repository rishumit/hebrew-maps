/**
 * Measurement tool: distance and area measurement on the map.
 * Lets users place multiple points and see the total distance / area.
 */

import { svg } from './icons.js';
import { bus } from './util.js';

/**
 * Calculate great-circle distance between two points (Haversine formula).
 * @param {[number, number]} p1 - [lon, lat]
 * @param {[number, number]} p2 - [lon, lat]
 * @returns {number} Distance in meters
 */
function haversineDistance(p1, p2) {
  const R = 6371000; // Earth radius in meters
  const [lon1, lat1] = p1;
  const [lon2, lat2] = p2;
  const dLat = (lat2 - lat1) * Math.PI / 180;
  const dLon = (lon2 - lon1) * Math.PI / 180;
  const a = Math.sin(dLat / 2) * Math.sin(dLat / 2) +
            Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) *
            Math.sin(dLon / 2) * Math.sin(dLon / 2);
  const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  return R * c;
}

/**
 * Format distance for display.
 * @param {number} meters - Distance in meters
 * @returns {string} Formatted string (e.g., "1.2 ק״מ" or "350 מ׳")
 */
function formatDistance(meters) {
  if (meters < 1000) {
    return `${Math.round(meters)} מ׳`;
  }
  return `${(meters / 1000).toFixed(meters >= 10000 ? 1 : 2)} ק״מ`;
}

/**
 * Calculate polygon area using shoelace formula.
 * @param {Array<[number, number]>} points - Array of [lon, lat] coordinates
 * @returns {number} Area in square meters (approximation)
 */
function polygonArea(points) {
  if (points.length < 3) return 0;
  
  // Convert to meters using approximate projection
  const avgLat = points.reduce((sum, p) => sum + p[1], 0) / points.length;
  const metersPerDegLat = 111320;
  const metersPerDegLon = 111320 * Math.cos(avgLat * Math.PI / 180);
  
  const projected = points.map(([lon, lat]) => [
    lon * metersPerDegLon,
    lat * metersPerDegLat
  ]);
  
  // Shoelace formula
  let area = 0;
  for (let i = 0; i < projected.length; i++) {
    const j = (i + 1) % projected.length;
    area += projected[i][0] * projected[j][1];
    area -= projected[j][0] * projected[i][1];
  }
  
  return Math.abs(area / 2);
}

/**
 * Format area for display.
 * @param {number} sqMeters - Area in square meters
 * @returns {string} Formatted string (e.g., "1.5 קמ״ר" or "2,500 מ״ר")
 */
function formatArea(sqMeters) {
  if (sqMeters < 10000) {
    return `${Math.round(sqMeters).toLocaleString('he-IL')} מ״ר`;
  }
  return `${(sqMeters / 1000000).toFixed(2)} קמ״ר`;
}

export class MeasureTool {
  constructor(map) {
    this.map = map;
    this.active = false;
    this.points = [];
    this.closed = false;
    this._clickHandler = null;
    this._moveHandler = null;
    this._keyHandler = null;
    this._cursorPoint = null;
  }

  /**
   * Start the measurement tool.
   * @param {Object} initialPoint - Optional {lat, lon} to add as first point
   */
  start(initialPoint = null) {
    if (this.active) return;
    
    this.active = true;
    this.points = [];
    this.closed = false;
    
    if (initialPoint) {
      this.points.push([initialPoint.lon, initialPoint.lat]);
    }
    
    this._setupLayers();
    this._bindEvents();
    this._updateMeasurement();
    
    // Change cursor
    this.map.map.getCanvas().style.cursor = 'crosshair';
    
    // Show instructions
    this._showInstructions();
  }

  /**
   * Stop the measurement tool and clear all points.
   */
  stop() {
    if (!this.active) return;
    
    this.active = false;
    this.points = [];
    this.closed = false;
    this._cursorPoint = null;
    
    this._unbindEvents();
    this._clearLayers();
    
    // Reset cursor
    this.map.map.getCanvas().style.cursor = '';
    
    // Hide instructions
    this._hideInstructions();
  }

  /**
   * Setup map layers for measurement display.
   */
  _setupLayers() {
    const map = this.map.map;
    
    // Remove existing layers if any
    this._clearLayers();
    
    // Add line layer for measurement path
    map.addSource('measure-line', {
      type: 'geojson',
      data: {
        type: 'Feature',
        geometry: {
          type: 'LineString',
          coordinates: []
        }
      }
    });
    
    map.addLayer({
      id: 'measure-line',
      type: 'line',
      source: 'measure-line',
      paint: {
        'line-color': '#2563EB',
        'line-width': 3,
        'line-dasharray': [2, 2]
      }
    });
    
    // Add polygon layer for area measurement
    map.addSource('measure-fill', {
      type: 'geojson',
      data: {
        type: 'Feature',
        geometry: {
          type: 'Polygon',
          coordinates: [[]]
        }
      }
    });
    
    map.addLayer({
      id: 'measure-fill',
      type: 'fill',
      source: 'measure-fill',
      paint: {
        'fill-color': '#2563EB',
        'fill-opacity': 0.1
      }
    });
    
    // Add points layer
    map.addSource('measure-points', {
      type: 'geojson',
      data: {
        type: 'FeatureCollection',
        features: []
      }
    });
    
    map.addLayer({
      id: 'measure-points',
      type: 'circle',
      source: 'measure-points',
      paint: {
        'circle-radius': 5,
        'circle-color': '#2563EB',
        'circle-stroke-color': '#ffffff',
        'circle-stroke-width': 2
      }
    });
    
    // Add labels layer for distance segments
    map.addSource('measure-labels', {
      type: 'geojson',
      data: {
        type: 'FeatureCollection',
        features: []
      }
    });
    
    map.addLayer({
      id: 'measure-labels',
      type: 'symbol',
      source: 'measure-labels',
      layout: {
        'text-field': ['get', 'label'],
        'text-size': 12,
        'text-offset': [0, -1.5],
        'text-anchor': 'bottom'
      },
      paint: {
        'text-color': '#1F2328',
        'text-halo-color': '#ffffff',
        'text-halo-width': 2
      }
    });
  }

  /**
   * Clear all measurement layers from the map.
   */
  _clearLayers() {
    const map = this.map.map;
    
    const layers = ['measure-labels', 'measure-points', 'measure-fill', 'measure-line'];
    const sources = ['measure-labels', 'measure-points', 'measure-fill', 'measure-line'];
    
    layers.forEach(id => {
      if (map.getLayer(id)) map.removeLayer(id);
    });
    
    sources.forEach(id => {
      if (map.getSource(id)) map.removeSource(id);
    });
  }

  /**
   * Bind map interaction events.
   */
  _bindEvents() {
    const map = this.map.map;
    
    this._clickHandler = (e) => {
      if (!this.active) return;
      
      // Left click adds a point
      this.points.push([e.lngLat.lng, e.lngLat.lat]);
      this._updateMeasurement();
    };
    
    this._moveHandler = (e) => {
      if (!this.active || this.points.length === 0) return;
      
      // Update cursor point for live preview
      this._cursorPoint = [e.lngLat.lng, e.lngLat.lat];
      this._updateMeasurement();
    };
    
    this._keyHandler = (e) => {
      if (!this.active) return;
      
      if (e.key === 'Escape') {
        // Cancel measurement
        this.stop();
      } else if (e.key === 'Enter' && this.points.length >= 3) {
        // Close polygon
        this.closed = true;
        this._cursorPoint = null;
        this._updateMeasurement();
      } else if (e.key === 'Backspace' || e.key === 'Delete') {
        // Remove last point
        if (this.points.length > 0 && !this.closed) {
          this.points.pop();
          this._updateMeasurement();
        }
      }
    };
    
    map.on('click', this._clickHandler);
    map.on('mousemove', this._moveHandler);
    document.addEventListener('keydown', this._keyHandler);
  }

  /**
   * Unbind map interaction events.
   */
  _unbindEvents() {
    const map = this.map.map;
    
    if (this._clickHandler) {
      map.off('click', this._clickHandler);
      this._clickHandler = null;
    }
    
    if (this._moveHandler) {
      map.off('mousemove', this._moveHandler);
      this._moveHandler = null;
    }
    
    if (this._keyHandler) {
      document.removeEventListener('keydown', this._keyHandler);
      this._keyHandler = null;
    }
  }

  /**
   * Update measurement display on the map.
   */
  _updateMeasurement() {
    const map = this.map.map;
    
    // Build line coordinates
    let lineCoords = [...this.points];
    if (this._cursorPoint && !this.closed) {
      lineCoords.push(this._cursorPoint);
    }
    if (this.closed && this.points.length >= 3) {
      lineCoords.push(this.points[0]); // Close the loop
    }
    
    // Update line
    map.getSource('measure-line').setData({
      type: 'Feature',
      geometry: {
        type: 'LineString',
        coordinates: lineCoords
      }
    });
    
    // Update polygon fill
    if (this.closed && this.points.length >= 3) {
      map.getSource('measure-fill').setData({
        type: 'Feature',
        geometry: {
          type: 'Polygon',
          coordinates: [[...this.points, this.points[0]]]
        }
      });
    } else {
      map.getSource('measure-fill').setData({
        type: 'Feature',
        geometry: {
          type: 'Polygon',
          coordinates: [[]]
        }
      });
    }
    
    // Update points
    const pointFeatures = this.points.map(coord => ({
      type: 'Feature',
      geometry: {
        type: 'Point',
        coordinates: coord
      }
    }));
    
    map.getSource('measure-points').setData({
      type: 'FeatureCollection',
      features: pointFeatures
    });
    
    // Calculate and display distances
    const labelFeatures = [];
    let totalDistance = 0;
    
    for (let i = 0; i < lineCoords.length - 1; i++) {
      const p1 = lineCoords[i];
      const p2 = lineCoords[i + 1];
      const dist = haversineDistance(p1, p2);
      totalDistance += dist;
      
      // Add label at midpoint
      const midpoint = [
        (p1[0] + p2[0]) / 2,
        (p1[1] + p2[1]) / 2
      ];
      
      labelFeatures.push({
        type: 'Feature',
        geometry: {
          type: 'Point',
          coordinates: midpoint
        },
        properties: {
          label: formatDistance(dist)
        }
      });
    }
    
    map.getSource('measure-labels').setData({
      type: 'FeatureCollection',
      features: labelFeatures
    });
    
    // Update total display
    this._updateTotalDisplay(totalDistance);
  }

  /**
   * Show or update the total distance/area display.
   */
  _updateTotalDisplay(totalDistance) {
    let display = document.getElementById('measure-display');
    
    if (!display) {
      display = document.createElement('div');
      display.id = 'measure-display';
      display.className = 'measure-display';
      document.getElementById('map').appendChild(display);
    }
    
    let html = `<div class="measure-total">מרחק כולל: <strong>${formatDistance(totalDistance)}</strong></div>`;
    
    if (this.closed && this.points.length >= 3) {
      const area = polygonArea(this.points);
      html += `<div class="measure-area">שטח: <strong>${formatArea(area)}</strong></div>`;
    }
    
    html += `<div class="measure-hint">`;
    if (!this.closed) {
      html += `לחץ להוספת נקודה • Enter לסגירת שטח • Backspace למחיקת נקודה אחרונה • Esc לביטול`;
    } else {
      html += `מדידה הושלמה • Esc לביטול`;
    }
    html += `</div>`;
    
    display.innerHTML = html;
  }

  /**
   * Show instructions toast.
   */
  _showInstructions() {
    const toast = document.createElement('div');
    toast.id = 'measure-toast';
    toast.className = 'toast';
    toast.innerHTML = `
      ${svg('ruler', { size: 20 })}
      <span>לחץ על המפה להוספת נקודות מדידה</span>
    `;
    document.getElementById('toasts').appendChild(toast);
    
    setTimeout(() => {
      if (document.getElementById('measure-toast')) {
        document.getElementById('measure-toast').remove();
      }
    }, 3000);
  }

  /**
   * Hide instructions and total display.
   */
  _hideInstructions() {
    const display = document.getElementById('measure-display');
    if (display) display.remove();
    
    const toast = document.getElementById('measure-toast');
    if (toast) toast.remove();
  }
}
