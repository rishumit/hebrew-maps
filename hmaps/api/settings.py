"""Runtime configuration from environment variables (12-factor)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(_env("HMAPS_DATA", str(ROOT / "data"))))
    sample_db: Path = field(default_factory=lambda: Path(_env("HMAPS_SAMPLE_DB", str(ROOT / "data" / "sample" / "maps.db"))))
    web_dir: Path = field(default_factory=lambda: Path(_env("HMAPS_WEB", str(ROOT / "web"))))
    osrm: dict = field(default_factory=lambda: {
        "car": _env("HMAPS_OSRM_CAR", "http://routing:5001"),
        "foot": _env("HMAPS_OSRM_FOOT", "http://routing:5002"),
        "bike": _env("HMAPS_OSRM_BIKE", "http://routing:5003"),
    })
    router: str = _env("HMAPS_ROUTER", "auto")  # auto | osrm | local
    rate_per_sec: float = float(_env("HMAPS_RATE_PER_SEC", "20"))
    rate_burst: int = int(_env("HMAPS_RATE_BURST", "80"))
    cors_origins: tuple[str, ...] = tuple(o for o in _env("HMAPS_CORS_ORIGINS", "").split(",") if o)
    log_level: str = _env("HMAPS_LOG_LEVEL", "INFO")
    # "1" (default) = show images that were not downloaded yet straight from Wikimedia (needs internet);
    # "0" = fully offline: only the copies downloaded by tools/media.py
    remote_images: bool = _env("HMAPS_REMOTE_IMAGES", "1") == "1"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "maps.db"

    @property
    def basemap_path(self) -> Path:
        return self.data_dir / "tiles" / "israel.mbtiles"

    @property
    def satellite_path(self) -> Path:
        return self.data_dir / "tiles" / "satellite.mbtiles"

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"

    @property
    def graph_path(self) -> Path:
        return self.data_dir / "routing" / "graph.npz"

    @property
    def fonts_dirs(self) -> list[Path]:
        return [self.data_dir / "fonts", self.web_dir / "fonts"]


settings = Settings()
