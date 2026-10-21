"""WGS 84 longitude/latitude -> Israeli Transverse Mercator (ITM, "רשת ישראל החדשה", EPSG:2039).

Pure numpy (no PROJ): a 7-parameter Helmert shift WGS 84 -> Israel 1993 datum (EPSG:9676,
0.5 m accuracy, position-vector convention as in PROJ's ``+towgs84``) followed by the
Krüger-series transverse Mercator on GRS80.  Matches PROJ to well below a metre inside
Israel, which is what the govmap orthophoto reprojection (``hmaps.api.satellite``) needs.

All functions take scalars or numpy arrays.
"""

from __future__ import annotations

import math

import numpy as np

# --- EPSG:2039 projection parameters
LAT0 = math.radians(31.7343936111111)
LON0 = math.radians(35.2045169444444)
K0 = 1.0000067
X0, Y0 = 219529.584, 626907.39

# --- ellipsoids
_A = 6378137.0
_F_WGS84 = 1 / 298.257223563
_F_GRS80 = 1 / 298.257222101

# --- Israel 1993 -> WGS 84 (EPSG:9676), position vector: tx, ty, tz [m]; rx, ry, rz ["]; ds [ppm]
_T = np.array([23.772, 17.49, 17.859])
_RX, _RY, _RZ = (math.radians(v / 3600) for v in (-0.3132, -1.85274, 1.67299))
_S = -5.4262e-6
#: local -> WGS 84 rotation (position vector); WGS 84 -> local is its transpose
_R = np.array([[1.0, -_RZ, _RY], [_RZ, 1.0, -_RX], [-_RY, _RX, 1.0]])

# --- Krüger series on GRS80
_N = _F_GRS80 / (2 - _F_GRS80)
_AA = _A / (1 + _N) * (1 + _N ** 2 / 4 + _N ** 4 / 64)
_ALPHA = (_N / 2 - 2 * _N ** 2 / 3 + 5 * _N ** 3 / 16 + 41 * _N ** 4 / 180,
          13 * _N ** 2 / 48 - 3 * _N ** 3 / 5 + 557 * _N ** 4 / 1440,
          61 * _N ** 3 / 240 - 103 * _N ** 4 / 140,
          49561 * _N ** 4 / 161280)
_E2_GRS80 = _F_GRS80 * (2 - _F_GRS80)
_E_GRS80 = math.sqrt(_E2_GRS80)


def _ecef(lat, lon, f):
    e2 = f * (2 - f)
    sl = np.sin(lat)
    nu = _A / np.sqrt(1 - e2 * sl * sl)
    cl = np.cos(lat)
    return np.stack([nu * cl * np.cos(lon), nu * cl * np.sin(lon), nu * (1 - e2) * sl])


def _geodetic(xyz, f):
    x, y, z = xyz
    e2 = f * (2 - f)
    p = np.hypot(x, y)
    lon = np.arctan2(y, x)
    lat = np.arctan2(z, p * (1 - e2))
    for _ in range(4):  # converges to < 1e-12 rad in 3 iterations near the surface
        sl = np.sin(lat)
        nu = _A / np.sqrt(1 - e2 * sl * sl)
        lat = np.arctan2(z + e2 * nu * sl, p)
    return lat, lon


def _tm(lat, lon):
    """Krüger transverse Mercator (GRS80) -> (easting, northing) without the false origin."""
    s = np.sin(lat)
    c = _E_GRS80
    t = np.sinh(np.arctanh(s) - c * np.arctanh(c * s))
    dl = lon - LON0
    xi_ = np.arctan2(t, np.cos(dl))
    eta_ = np.arctanh(np.sin(dl) / np.sqrt(1 + t * t))
    xi, eta = xi_, eta_
    for j, a in enumerate(_ALPHA, start=1):
        xi = xi + a * np.sin(2 * j * xi_) * np.cosh(2 * j * eta_)
        eta = eta + a * np.cos(2 * j * xi_) * np.sinh(2 * j * eta_)
    return K0 * _AA * eta, K0 * _AA * xi


_, _NORTH0 = _tm(np.array(LAT0), np.array(LON0))


def from_wgs84(lon, lat):
    """WGS 84 degrees -> ITM metres (easting, northing); arrays in, arrays out."""
    lon = np.radians(np.asarray(lon, dtype=float))
    lat = np.radians(np.asarray(lat, dtype=float))
    xyz = _ecef(lat, lon, _F_WGS84)
    shape = xyz.shape
    v = xyz.reshape(3, -1) - _T[:, None]
    local = (_R.T @ v) / (1 + _S)
    la, lo = _geodetic(local.reshape(shape), _F_GRS80)
    e, n = _tm(la, lo)
    return X0 + e, Y0 + (n - _NORTH0)
