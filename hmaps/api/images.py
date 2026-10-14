"""Map stored image URLs to the local offline copies served from ``/media/``."""

from __future__ import annotations

from ..core import media
from .settings import settings


def local_image(url: str | None, width: int | None = None) -> str | None:
    """``/media/<key>`` when the image was downloaded; otherwise the original URL
    (a resized Commons thumbnail when *width* is given), or None with HMAPS_REMOTE_IMAGES=0."""
    if not url:
        return None
    k = media.key(url)
    if (settings.media_dir / k).is_file():
        return f"/media/{k}"
    if not settings.remote_images:
        return None
    return media.download_url(url, width) if width else url
