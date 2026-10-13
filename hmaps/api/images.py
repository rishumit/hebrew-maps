"""Map stored image URLs to the local offline copies served from ``/media/``."""

from __future__ import annotations

from ..core import media
from .settings import settings


def local_image(url: str | None) -> str | None:
    """``/media/<key>`` when the image was downloaded; otherwise None (offline),
    or the original URL when HMAPS_REMOTE_IMAGES=1."""
    if not url:
        return None
    k = media.key(url)
    if (settings.media_dir / k).is_file():
        return f"/media/{k}"
    return url if settings.remote_images else None
