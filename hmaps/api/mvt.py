"""Minimal Mapbox Vector Tile (v2) encoder – points and lines.

Hand-written protobuf encoding (spec: github.com/mapbox/vector-tile-spec)
so the API needs no native dependencies. Used for the POI overlay tiles and
for the fallback basemap built from our own database.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

EXTENT = 4096


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _key(field: int, wire: int) -> bytes:
    return _varint((field << 3) | wire)


def _len_delim(field: int, payload: bytes) -> bytes:
    return _key(field, 2) + _varint(len(payload)) + payload


def _packed(field: int, values: Iterable[int]) -> bytes:
    return _len_delim(field, b"".join(_varint(v) for v in values))


def _zigzag(n: int) -> int:
    return (n << 1) ^ (n >> 31)


def _value(v: Any) -> bytes:
    import struct

    if isinstance(v, bool):
        return _key(7, 0) + _varint(1 if v else 0)
    if isinstance(v, int):
        if v >= 0:
            return _key(5, 0) + _varint(v)
        return _key(6, 0) + _varint(_zigzag(v) & 0xFFFFFFFFFFFFFFFF)
    if isinstance(v, float):
        return _key(3, 1) + struct.pack("<d", v)
    b = str(v).encode("utf-8")
    return _len_delim(1, b)


class Layer:
    def __init__(self, name: str, extent: int = EXTENT):
        self.name = name
        self.extent = extent
        self.keys: dict[str, int] = {}
        self.values: dict[tuple[type, Any], int] = {}
        self.features: list[bytes] = []

    def _tags(self, props: dict[str, Any]) -> list[int]:
        tags: list[int] = []
        for k, v in props.items():
            if v is None or v == "":
                continue
            ki = self.keys.setdefault(k, len(self.keys))
            vk = (type(v), v)
            vi = self.values.setdefault(vk, len(self.values))
            tags += [ki, vi]
        return tags

    def _feature(self, fid: int | None, props: dict, gtype: int, geometry: list[int]) -> None:
        body = b""
        if fid is not None:
            body += _key(1, 0) + _varint(fid)
        tags = self._tags(props)
        if tags:
            body += _packed(2, tags)
        body += _key(3, 0) + _varint(gtype)
        body += _packed(4, geometry)
        self.features.append(body)

    def add_point(self, x: int, y: int, props: dict, fid: int | None = None) -> None:
        self._feature(fid, props, 1, [(1 & 7) | (1 << 3), _zigzag(x), _zigzag(y)])

    def add_line(self, lines: Sequence[Sequence[tuple[int, int]]], props: dict, fid: int | None = None) -> bool:
        geom: list[int] = []
        cx = cy = 0
        for line in lines:
            pts = _dedupe(line)
            if len(pts) < 2:
                continue
            x0, y0 = pts[0]
            geom += [(1 & 7) | (1 << 3), _zigzag(x0 - cx), _zigzag(y0 - cy)]
            cx, cy = x0, y0
            geom.append((2 & 7) | ((len(pts) - 1) << 3))
            for x, y in pts[1:]:
                geom += [_zigzag(x - cx), _zigzag(y - cy)]
                cx, cy = x, y
        if not geom:
            return False
        self._feature(fid, props, 2, geom)
        return True

    def add_polygon(self, polygons: Sequence[Sequence[Sequence[tuple[int, int]]]], props: dict, fid: int | None = None) -> bool:
        """*polygons*: [[exterior, hole, …], …] in tile pixels. Winding is fixed here
        (MVT: exterior rings have positive area in screen coordinates, holes negative)."""
        geom: list[int] = []
        cx = cy = 0
        for poly in polygons:
            for ri, ring in enumerate(poly):
                pts = _dedupe(ring)
                if len(pts) > 1 and pts[0] == pts[-1]:
                    pts = pts[:-1]
                if len(pts) < 3:
                    if ri == 0:
                        break  # degenerate exterior: skip the whole polygon
                    continue
                area = sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1]
                           for i in range(len(pts)))
                if area == 0:
                    if ri == 0:
                        break
                    continue
                if (area > 0) != (ri == 0):
                    pts = pts[::-1]
                x0, y0 = pts[0]
                geom += [(1 & 7) | (1 << 3), _zigzag(x0 - cx), _zigzag(y0 - cy)]
                cx, cy = x0, y0
                geom.append((2 & 7) | ((len(pts) - 1) << 3))
                for x, y in pts[1:]:
                    geom += [_zigzag(x - cx), _zigzag(y - cy)]
                    cx, cy = x, y
                geom.append((7 & 7) | (1 << 3))
        if not geom:
            return False
        self._feature(fid, props, 3, geom)
        return True

    def encode(self) -> bytes:
        body = _key(15, 0) + _varint(2)
        body += _len_delim(1, self.name.encode("utf-8"))
        for f in self.features:
            body += _len_delim(2, f)
        for k in self.keys:
            body += _len_delim(3, k.encode("utf-8"))
        for (_, v) in self.values:
            body += _len_delim(4, _value(v))
        body += _key(5, 0) + _varint(self.extent)
        return body


def _dedupe(pts: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for p in pts:
        if not out or out[-1] != p:
            out.append(p)
    return out


def encode_tile(layers: Iterable[Layer]) -> bytes:
    return b"".join(_len_delim(3, layer.encode()) for layer in layers if layer.features)


# --- decoding (used by tests and debugging) ---------------------------------------

def _read_varint(buf: bytes, i: int) -> tuple[int, int]:
    shift = result = 0
    while True:
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, i
        shift += 7


def _fields(buf: bytes):
    import struct

    i = 0
    while i < len(buf):
        key, i = _read_varint(buf, i)
        field, wire = key >> 3, key & 7
        if wire == 0:
            v, i = _read_varint(buf, i)
        elif wire == 2:
            ln, i = _read_varint(buf, i)
            v = buf[i:i + ln]
            i += ln
        elif wire == 1:
            v = struct.unpack("<d", buf[i:i + 8])[0]
            i += 8
        elif wire == 5:
            v = struct.unpack("<f", buf[i:i + 4])[0]
            i += 4
        else:
            raise ValueError("bad wire type")
        yield field, v


def decode(buf: bytes) -> dict[str, list[dict]]:
    """Decode a tile into {layer: [{"id", "type", "props", "geometry"}]}."""
    out: dict[str, list[dict]] = {}
    for f, layer_buf in _fields(buf):
        if f != 3:
            continue
        name, keys, vals, feats = "", [], [], []
        for lf, lv in _fields(layer_buf):
            if lf == 1:
                name = lv.decode()
            elif lf == 3:
                keys.append(lv.decode())
            elif lf == 4:
                for vf, vv in _fields(lv):
                    if vf == 1:
                        vals.append(vv.decode())
                    elif vf == 6:
                        vals.append((vv >> 1) ^ -(vv & 1))
                    elif vf == 7:
                        vals.append(bool(vv))
                    else:
                        vals.append(vv)
            elif lf == 2:
                feats.append(lv)
        decoded = []
        for fb in feats:
            feat: dict = {"id": None, "props": {}, "geometry": []}
            for ff, fv in _fields(fb):
                if ff == 1:
                    feat["id"] = fv
                elif ff == 2:
                    tags, j = [], 0
                    while j < len(fv):
                        t, j = _read_varint(fv, j)
                        tags.append(t)
                    feat["props"] = {keys[tags[k]]: vals[tags[k + 1]] for k in range(0, len(tags), 2)}
                elif ff == 3:
                    feat["type"] = fv
                elif ff == 4:
                    ints, j = [], 0
                    while j < len(fv):
                        t, j = _read_varint(fv, j)
                        ints.append(t)
                    feat["geometry"] = ints
            decoded.append(feat)
        out[name] = decoded
    return out
