"""Modules imported on first use – keeps the server start fast (numpy, public transport)."""

from __future__ import annotations

import importlib
import threading
from typing import Any

_lock = threading.Lock()


class LazyModule:
    """Stands in for ``import x as y``: the real module is imported on the first attribute access."""

    __slots__ = ("_name", "_mod")

    def __init__(self, name: str) -> None:
        object.__setattr__(self, "_name", name)
        object.__setattr__(self, "_mod", None)

    def load(self) -> Any:
        mod = self._mod
        if mod is None:
            with _lock:
                mod = self._mod or importlib.import_module(self._name)
                object.__setattr__(self, "_mod", mod)
        return mod

    def __getattr__(self, attr: str) -> Any:
        return getattr(self.load(), attr)

    def __repr__(self) -> str:
        return f"<lazy module {self._name!r}{' (loaded)' if self._mod is not None else ''}>"


def lazy_import(name: str) -> LazyModule:
    return LazyModule(name)
