"""Generate MapLibre SDF glyph ranges (PBF) from a TrueType font.

Fallback for map labels when the prebuilt Noto Sans glyphs could not be
downloaded during setup. Output layout matches the glyphs URL template:
``OUT/<Font Stack Name>/<start>-<end>.pbf``.

    python tools/glyphs.py FONT.ttf OUT_DIR "Hebrew Fallback Regular"

Format: github.com/mapbox/node-fontnik (24px em, 3px buffer, radius 8, cutoff .25).
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy.ndimage import distance_transform_edt

SIZE = 24
BUFFER = 3
RADIUS = 8
CUTOFF = 0.25
SUPER = 4  # render at 4x for sub-pixel accurate distances

RANGES = [(0, 255), (256, 511), (1280, 1535), (8192, 8447), (64256, 64511)]


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _zig(n: int) -> int:
    return (n << 1) ^ (n >> 31)


def _field(num: int, wire: int) -> bytes:
    return _varint((num << 3) | wire)


def _ld(num: int, payload: bytes) -> bytes:
    return _field(num, 2) + _varint(len(payload)) + payload


def render_glyph(font_big: ImageFont.FreeTypeFont, font: ImageFont.FreeTypeFont, ch: str, ascender: int) -> bytes | None:
    try:
        if not font.getmask(ch).getbbox() and not ch.isspace():
            return None
    except Exception:
        return None
    advance = int(round(font.getlength(ch)))
    x0, y0, x1, y1 = font.getbbox(ch, anchor="ls")
    w, h = max(0, x1 - x0), max(0, y1 - y0)
    if w == 0 or h == 0:
        body = _field(1, 0) + _varint(ord(ch)) + _field(3, 0) + _varint(0) + _field(4, 0) + _varint(0)
        body += _field(5, 0) + _varint(_zig(0)) + _field(6, 0) + _varint(_zig(0)) + _field(7, 0) + _varint(advance)
        return body
    bw, bh = w + 2 * BUFFER, h + 2 * BUFFER
    big = Image.new("L", (bw * SUPER, bh * SUPER), 0)
    d = ImageDraw.Draw(big)
    d.text(((BUFFER - x0) * SUPER, (BUFFER - y0) * SUPER), ch, font=font_big, fill=255, anchor="ls")
    a = np.asarray(big) > 127
    outside = distance_transform_edt(~a) / SUPER
    inside = distance_transform_edt(a) / SUPER
    dist = outside - inside  # positive outside
    # sample at the centre of each 1x pixel
    c = SUPER // 2
    dist = dist[c::SUPER, c::SUPER][:bh, :bw]
    vals = np.clip(255 - 255 * (dist / RADIUS + CUTOFF), 0, 255).astype(np.uint8)
    top = int(round(-y0)) - ascender
    body = _field(1, 0) + _varint(ord(ch))
    body += _ld(2, vals.tobytes())
    body += _field(3, 0) + _varint(w) + _field(4, 0) + _varint(h)
    body += _field(5, 0) + _varint(_zig(int(x0))) + _field(6, 0) + _varint(_zig(top) & 0xFFFFFFFF)
    body += _field(7, 0) + _varint(advance)
    return body


def build(font_path: str, out_dir: str, name: str, ranges=RANGES) -> int:
    font = ImageFont.truetype(font_path, SIZE)
    font_big = ImageFont.truetype(font_path, SIZE * SUPER)
    ascender, _ = font.getmetrics()
    out = Path(out_dir) / name
    out.mkdir(parents=True, exist_ok=True)
    count = 0
    for start, end in ranges:
        glyphs = []
        for cp in range(start, end + 1):
            g = render_glyph(font_big, font, chr(cp), ascender)
            if g:
                glyphs.append(_ld(3, g))
                count += 1
        stack = _ld(1, name.encode()) + _ld(2, f"{start}-{end}".encode()) + b"".join(glyphs)
        (out / f"{start}-{end}.pbf").write_bytes(_ld(1, stack))
    return count


if __name__ == "__main__":
    if len(sys.argv) < 4:
        sys.exit(__doc__)
    n = build(sys.argv[1], sys.argv[2], sys.argv[3])
    print(f"{n} glyphs written")
