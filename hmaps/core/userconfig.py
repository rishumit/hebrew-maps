"""Per-user settings of the program that must be known before the server starts (the data folder).

Kept outside the program folder, so they survive reinstalling and updating the program:

* Windows: ``%LOCALAPPDATA%\\HebrewMaps\\config.json``
* elsewhere: ``~/.config/hebrew-maps/config.json``
* ``HMAPS_CONFIG`` overrides the path (tests, portable setups).

Importing this module must not import :mod:`hmaps.api.settings` (the launcher reads it first).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def config_path() -> Path:
    env = os.environ.get("HMAPS_CONFIG")
    if env:
        return Path(env)
    if sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "HebrewMaps" / "config.json"
    return Path.home() / ".config" / "hebrew-maps" / "config.json"


def load() -> dict:
    try:
        data = json.loads(config_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save(data: dict) -> None:
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def default_data_dir() -> Path:
    """``data`` next to the program (installed: next to hebrew-maps.exe; from source: the repository)."""
    if getattr(sys, "frozen", False):
        return Path(os.path.dirname(sys.executable)) / "data"
    return ROOT / "data"


def configured_data_dir() -> Path | None:
    """The folder the user chose in the app ("תיקיית נתונים"), if it is usable."""
    d = load().get("data_dir")
    if not d:
        return None
    p = Path(d)
    return p if p.is_absolute() else None


def set_data_dir(path: str | None) -> None:
    data = load()
    if path:
        data["data_dir"] = str(path)
    else:
        data.pop("data_dir", None)
    save(data)


def resolve_data_dir() -> Path:
    """HMAPS_DATA (set by the user) > the folder chosen in the app > the default."""
    env = os.environ.get("HMAPS_DATA")
    if env:
        return Path(env)
    return configured_data_dir() or default_data_dir()
