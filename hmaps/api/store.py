"""Access to the read-only dataset with hot reload.

Each worker thread keeps its own SQLite connection. When the ETL swaps in a
new ``maps.db`` (atomic rename), the file's identity changes and threads
transparently reopen it – no API restart needed after a data refresh.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from pathlib import Path

from ..core import db

log = logging.getLogger(__name__)


class Dataset:
    def __init__(self, primary: Path, fallback: Path | None = None, check_every: float = 10.0):
        self.primary = primary
        self.fallback = fallback
        self.check_every = check_every
        self._local = threading.local()
        self._lock = threading.Lock()
        self._version: tuple | None = None
        self._path: Path | None = None
        self._last_check = 0.0
        self.meta: dict[str, str] = {}
        self._refresh(force=True)

    @property
    def available(self) -> bool:
        return self._path is not None

    @property
    def version(self) -> str:
        return str(abs(hash(self._version)) % 10**10) if self._version else "0"

    def _pick(self) -> Path | None:
        primary = db.resolve(self.primary)
        if primary.exists():
            return primary
        if self.fallback:
            fb = db.resolve(self.fallback)
            if fb.exists():
                return fb
        return None

    @property
    def demo(self) -> bool:
        return self._path is not None and not db.resolve(self.primary).exists()

    def _refresh(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_check < self.check_every:
            return
        with self._lock:
            self._last_check = now
            path = self._pick()
            if path is None:
                if self._path is not None:
                    log.warning("dataset disappeared")
                self._path, self._version = None, None
                return
            st = os.stat(path)
            version = (str(path), st.st_ino, st.st_size, st.st_mtime_ns)
            if version != self._version:
                try:
                    con = db.open_readonly(path)
                    self.meta = dict(con.execute("SELECT key, value FROM meta").fetchall())
                    con.close()
                except sqlite3.DatabaseError as e:
                    log.error("cannot open dataset %s: %s", path, e)
                    return
                log.info("dataset loaded: %s (built %s)", path, self.meta.get("built"))
                self._path, self._version = path, version

    def conn(self) -> sqlite3.Connection:
        self._refresh()
        if self._path is None:
            raise RuntimeError("dataset not available")
        local = self._local
        if getattr(local, "version", None) != self._version:
            old = getattr(local, "con", None)
            if old is not None:
                old.close()
            local.con = db.open_readonly(self._path)
            local.version = self._version
        return local.con


class Basemap:
    """Reader for the OpenMapTiles MBTiles file produced by Planetiler."""

    def __init__(self, path: Path):
        self.logical = path
        self.path = path
        self._local = threading.local()
        self._mtime: int | None = None
        self._last_scan = 0.0
        self.metadata: dict[str, str] = {}
        self._check()

    def _scan(self) -> None:
        """Pick the newest ``israel*.mbtiles`` (new builds get timestamped names)."""
        now = time.monotonic()
        if now - self._last_scan < 10 and self._mtime is not None:
            return
        self._last_scan = now
        cands = [p for p in self.logical.parent.glob(self.logical.stem + "*" + self.logical.suffix) if p.is_file()]
        if cands:
            self.path = max(cands, key=lambda p: p.stat().st_mtime_ns)

    def _check(self) -> bool:
        self._scan()
        if not self.path.exists():
            self._mtime = None
            return False
        m = self.path.stat().st_mtime_ns
        if m != self._mtime:
            try:
                con = sqlite3.connect(f"file:{self.path.as_posix()}?mode=ro", uri=True)
                self.metadata = dict(con.execute("SELECT name, value FROM metadata").fetchall())
                con.close()
            except sqlite3.DatabaseError:
                return False
            self._mtime = m
        return True

    @property
    def available(self) -> bool:
        return self._check()

    def tile(self, z: int, x: int, y: int) -> bytes | None:
        if (self._mtime is None or time.monotonic() - self._last_scan > 10) and not self._check():
            return None
        local = self._local
        if getattr(local, "mtime", None) != self._mtime:
            if getattr(local, "con", None) is not None:
                local.con.close()
            local.con = sqlite3.connect(f"file:{self.path.as_posix()}?mode=ro", uri=True, check_same_thread=False)
            local.mtime = self._mtime
        tms_y = (1 << z) - 1 - y
        row = local.con.execute(
            "SELECT tile_data FROM tiles WHERE zoom_level = ? AND tile_column = ? AND tile_row = ?", (z, x, tms_y)
        ).fetchone()
        return row[0] if row else None
