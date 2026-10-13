"""Local (offline) copies of POI images.

The database keeps the original image URL (usually Wikimedia Commons). The
``tools/media.py`` downloader stores a small copy of each image under
``data/media/<key>`` and the API serves it from ``/media/<key>``, so the map
needs no internet connection to show photos.
"""

from __future__ import annotations

import hashlib
import re
import urllib.parse

COMMONS_FILEPATH = "https://commons.wikimedia.org/wiki/Special:FilePath/"
KEY_RE = re.compile(r"^[0-9a-f]{24}$")

_COMMONS_PAGE = re.compile(r"^https?://commons\.(?:m\.)?wikimedia\.org/wiki/(?:File|Special:FilePath):?/?(.+)$", re.I)
_COMMONS_UPLOAD = re.compile(
    r"^https?://upload\.wikimedia\.org/wikipedia/commons/(?:thumb/)?[0-9a-f]/[0-9a-f]{2}/([^/?#]+)", re.I)


def key(url: str) -> str:
    """Stable file name for an image URL (as stored in the database)."""
    return hashlib.sha1(url.strip().encode("utf-8")).hexdigest()[:24]


def commons_file(url: str) -> str | None:
    """The Commons file name behind a Commons URL, or None for other hosts."""
    u = url.strip().split("#", 1)[0]
    m = _COMMONS_PAGE.match(u) or _COMMONS_UPLOAD.match(u)
    if not m:
        return None
    name = m.group(1).split("?", 1)[0]
    return urllib.parse.unquote(name).replace(" ", "_") or None


def download_url(url: str, width: int = 500) -> str:
    """URL to fetch: a resized Commons thumbnail when possible, otherwise the original."""
    f = commons_file(url)
    if f:
        return COMMONS_FILEPATH + urllib.parse.quote(f) + f"?width={width}"
    return url.strip()


def sniff(data: bytes) -> str | None:
    """Image media type from the first bytes; None when the payload is not an image."""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def is_filter_placeholder(data: bytes) -> bool:
    """True for the stand-in image a content filter returns while a photo awaits review.

    NetFree answers with a heavily pixelated copy of the photo (a coarse grid of flat
    colour blocks) with a "sent for review" notice in the middle. A real photo never has
    all of its border cells perfectly flat, so: for grid sizes 3–10, check that every
    border cell (sampled away from the cell edges) has ~zero colour variation.
    Needs Pillow; without it the check is skipped (returns False).
    """
    try:
        import io

        from PIL import Image, ImageStat
    except ImportError:
        return False
    if data[:3] != b"\xff\xd8\xff":
        return False  # the filter's stand-in is a JPEG; flat PNG logos are left alone
    try:
        im = Image.open(io.BytesIO(data)).convert("RGB")
    except Exception:  # noqa: BLE001
        return False
    w, h = im.size
    if w < 60 or h < 60:
        return False
    for n in range(3, 11):
        cw, ch = w / n, h / n
        flat = True
        for cx in range(n):
            for cy in range(n):
                if 0 < cx < n - 1 and 0 < cy < n - 1:
                    continue  # inner cells hold the notice text
                box = (int(cx * cw + cw * 0.2), int(cy * ch + ch * 0.2),
                       int(cx * cw + cw * 0.8), int(cy * ch + ch * 0.8))
                if box[2] - box[0] < 2 or box[3] - box[1] < 2 or max(ImageStat.Stat(im.crop(box)).stddev) > 2.0:
                    flat = False
                    break
            if not flat:
                break
        if flat:
            return True
    return False
