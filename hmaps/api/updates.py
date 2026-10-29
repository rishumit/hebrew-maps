"""Online data updates from the weekly GitHub release ("עדכונים").

The weekly workflow (``.github/workflows/weekly-data.yml``) publishes one
dated release ``data-YYYY-MM-DD`` with a ZIP per component and a
``manifest.json``; the rolling release ``data-latest`` always carries the
newest manifest (see :mod:`hmaps.core.components` for the layout).

    GET  /api/updates[?refresh=1]     installed vs. available version of every component
    POST /api/updates/{component}     download + verify + install in the background
                                      ({component} = maps | basemap | … | all)
    GET  /api/updates/status          progress of the background jobs (+ "app": the program update)
    GET  /api/updates/app             installed vs. newest program version (appupdate.py)
    POST /api/updates/app             download + verify + start the program installer

and the command line (also for a weekly Windows scheduled task, see
``update-online-windows.cmd``)::

    python -m hmaps update --check               # what is new?
    python -m hmaps update                       # install every available update
    python -m hmaps update --component transit   # only one component (repeatable)
    python -m hmaps update --bundle E:\\HebrewMaps-Update-2026-10-22.zip   # offline update file
    python -m hmaps update --app [--check]       # the program itself (see appupdate.py)

Offline update file (for computers without internet): every weekly release also
carries ``HebrewMaps-Update-<date>.zip`` – manifest.json plus the component ZIPs
(see ``tools/release.py bundle``). It is copied with a USB stick and installed
from the "עדכונים" page ("עדכון מקובץ", ``POST /api/updates/offline`` with the
file as the request body) or with ``--bundle``; every component inside is
verified against the SHA-256 of that manifest exactly like an online download.
The offline file always holds full packages (no deltas).

Delta packages: a manifest entry may list ``deltas`` (``{from, to, file, size, sha256,
kind}``) – small packages that turn an installed version into a newer one (changed images
only, or a zstd binary patch of maps.db / the tile sets, see :mod:`hmaps.core.delta`). The
cheapest chain from the installed version is downloaded when it is under 60 % of the full
package; the result is verified file by file and swapped exactly like a full package. Any
problem (another base, a corrupt or missing delta) falls back to the full package.
``HMAPS_UPDATES_DELTA=0`` / ``--no-delta`` turns deltas off.

Robustness: the manifest is read from a plain release download URL (no GitHub
API rate limit), with the REST API as a fallback; downloads resume with HTTP
``Range`` and are retried; every file is checked against the SHA-256 of the
manifest before anything is touched. Behind content filters that re-sign TLS
with their own root certificate (NetFree, Rimon, …) Python may reject the
certificate – like ``tools/terrain.py`` we then retry without certificate
verification, which is safe here because the content itself is verified.

Installation never replaces a file the server has open: databases go through
the versioned-file + pointer mechanism of :func:`hmaps.core.db.publish`, tile
sets get a new timestamped name (the server serves the newest), and the
running server is told to reload right away.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import shutil
import sqlite3
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from .. import __version__
from ..core import components as catalog
from ..core import db, delta

log = logging.getLogger("hmaps.updates")

REPO = os.environ.get("HMAPS_UPDATES_REPO", "rishumit/hebrew-maps")
#: the release that always carries the newest manifest
POINTER_TAG = "data-latest"
UA = {"User-Agent": f"HebrewMaps-updater/{__version__}", "Accept": "application/octet-stream, application/json"}
MANIFEST_TTL = 15 * 60  # seconds an in-memory manifest stays fresh
CHUNK = 1 << 20
RETRIES = 4


def manifest_url() -> str:
    """Where the manifest is read from (``HMAPS_UPDATES_MANIFEST`` may be a URL or a local path)."""
    return os.environ.get("HMAPS_UPDATES_MANIFEST") or f"https://github.com/{REPO}/releases/download/{POINTER_TAG}/manifest.json"


def releases_api_url() -> str:
    return os.environ.get("HMAPS_UPDATES_API") or f"https://api.github.com/repos/{REPO}/releases?per_page=30"


def data_dir() -> Path:
    from .settings import settings
    return settings.data_dir


class UpdateError(Exception):
    """An error with a Hebrew message for the user."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# =========================================================================== HTTP

_insecure = False  # set once a filtering proxy broke certificate verification


def _insecure_ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _is_cert_error(e: BaseException) -> bool:
    reason = getattr(e, "reason", e)
    return isinstance(reason, ssl.SSLError) or isinstance(e, ssl.SSLError)


def _local_path(url: str) -> Path | None:
    if url.startswith("file://"):
        return Path(urllib.request.url2pathname(urllib.parse.urlparse(url).path))
    if "://" not in url:
        return Path(url)
    return None


class _CheckedRedirects(urllib.request.HTTPRedirectHandler):
    """Follows redirects only to URLs that *allow* accepts (the app installer: GitHub hosts only)."""

    def __init__(self, allow):
        self.allow = allow

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not self.allow(newurl):
            raise UpdateError("bad_url", "שרת העדכונים הפנה לכתובת לא מורשית – ההורדה בוטלה.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _urlopen(req, timeout: float, context: ssl.SSLContext | None, allow):
    if allow is None:
        return urllib.request.urlopen(req, timeout=timeout, context=context) if context else \
            urllib.request.urlopen(req, timeout=timeout)
    handlers = [_CheckedRedirects(allow)]
    if context is not None:
        handlers.append(urllib.request.HTTPSHandler(context=context))
    return urllib.request.build_opener(*handlers).open(req, timeout=timeout)


def _open(url: str, headers: dict | None = None, timeout: float = 30, allow=None):
    """urlopen with a User-Agent, falling back to an unverified TLS context behind content filters.

    *allow*: optional ``url -> bool`` checked for the URL and every redirect."""
    global _insecure
    if allow is not None and not allow(url):
        raise UpdateError("bad_url", "כתובת ההורדה אינה מורשית – ההורדה בוטלה.")
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    if _insecure and url.startswith("https:"):
        return _urlopen(req, timeout, _insecure_ctx(), allow)
    try:
        return _urlopen(req, timeout, None, allow)
    except urllib.error.URLError as e:
        if not (url.startswith("https:") and _is_cert_error(e)):
            raise
        log.warning("TLS certificate rejected (%s) – probably a filtering proxy; continuing without "
                    "certificate verification, downloads are verified by SHA-256", getattr(e, "reason", e))
        _insecure = True
        return _urlopen(req, timeout, _insecure_ctx(), allow)


def _read_json(url: str, timeout: float = 20, allow=None, headers: dict | None = None) -> dict | list:
    local = _local_path(url)
    if local is not None:
        return json.loads(local.read_text(encoding="utf-8"))
    with _open(url, {"Accept": "application/json", **(headers or {})}, timeout=timeout, allow=allow) as r:
        return json.loads(r.read(4 * 1024 * 1024).decode("utf-8"))


def _http_error(e: Exception) -> UpdateError:
    if isinstance(e, UpdateError):
        return e
    if isinstance(e, urllib.error.HTTPError):
        if e.code in (403, 429) and (e.headers.get("X-RateLimit-Remaining") == "0" or e.code == 429):
            reset = e.headers.get("X-RateLimit-Reset")
            when = ""
            if reset and reset.isdigit():
                when = " אחרי " + datetime.fromtimestamp(int(reset)).strftime("%H:%M")
            return UpdateError("rate_limited", f"GitHub הגביל זמנית את מספר הבקשות מהרשת שלכם. נסו שוב{when}.")
        if e.code == 404:
            return UpdateError("not_found", "לא נמצאה עדיין גרסת נתונים מקוונת לעדכון.")
        if e.code in (401, 403):
            return UpdateError("blocked", "הגישה לשרת העדכונים נחסמה (ייתכן שסינון האינטרנט חוסם את GitHub).")
        return UpdateError("http_error", f"שרת העדכונים החזיר שגיאה ({e.code}). נסו שוב מאוחר יותר.")
    if isinstance(e, TimeoutError) or "timed out" in str(e):
        return UpdateError("timeout", "שרת העדכונים לא הגיב בזמן. בדקו את החיבור לאינטרנט ונסו שוב.")
    if isinstance(e, (urllib.error.URLError, OSError)):
        return UpdateError("offline", "אין חיבור לשרת העדכונים. בדקו את החיבור לאינטרנט ונסו שוב.")
    if isinstance(e, (ValueError, KeyError)):
        return UpdateError("bad_manifest", "קובץ העדכונים שהתקבל אינו תקין (ייתכן שסינון האינטרנט שינה אותו).")
    return UpdateError("error", "בדיקת העדכונים נכשלה. נסו שוב מאוחר יותר.")


# =========================================================================== manifest

_manifest_cache: tuple[float, dict] | None = None
_manifest_lock = threading.Lock()


def _validate_manifest(m: dict, source: str) -> dict:
    if not isinstance(m, dict) or not isinstance(m.get("components"), dict):
        raise ValueError("manifest without components")
    m.setdefault("_source", source)
    return m


def _manifest_from_api() -> dict:
    """Fallback: newest dated ``data-*`` release that has a manifest.json asset (REST API, rate limited)."""
    releases = _read_json(releases_api_url())
    for rel in releases if isinstance(releases, list) else []:
        tag = rel.get("tag_name", "")
        if not tag.startswith("data-") or tag == POINTER_TAG or rel.get("draft"):
            continue
        for asset in rel.get("assets") or []:
            if asset.get("name") == "manifest.json":
                return _validate_manifest(_read_json(asset["browser_download_url"]), asset["browser_download_url"])
    raise UpdateError("not_found", "לא נמצאה עדיין גרסת נתונים מקוונת לעדכון.")


def fetch_manifest(force: bool = False) -> dict:
    """The newest manifest (cached for a few minutes)."""
    global _manifest_cache
    with _manifest_lock:
        if not force and _manifest_cache and time.monotonic() - _manifest_cache[0] < MANIFEST_TTL:
            return _manifest_cache[1]
        url = manifest_url()
        try:
            m = _validate_manifest(_read_json(url), url)
        except Exception as first:  # noqa: BLE001
            if _local_path(url) is not None or os.environ.get("HMAPS_UPDATES_MANIFEST"):
                raise _http_error(first) from first
            log.warning("manifest download failed (%s) – trying the GitHub API", first)
            try:
                m = _manifest_from_api()
            except Exception as second:  # noqa: BLE001
                # online (the pointer answered with an HTTP error): the API's error is more telling
                # (rate limit, no release yet); otherwise we are simply offline
                raise _http_error(second if isinstance(first, urllib.error.HTTPError) else first) from second
        _manifest_cache = (time.monotonic(), m)
        return m


def _asset_url(manifest: dict, entry: dict) -> str:
    url = entry.get("url")
    if url:
        return url
    src = manifest.get("_source", "")
    local = _local_path(src) if src else None
    if local is not None:
        return str(local.parent / entry["file"])
    if src and "://" in src and not entry.get("release"):
        return urllib.parse.urljoin(src, entry["file"])
    repo = manifest.get("repo") or REPO
    return f"https://github.com/{repo}/releases/download/{entry.get('release') or manifest.get('release')}/{entry['file']}"


# =========================================================================== local state

_state_lock = threading.RLock()


def _state_path(data: Path) -> Path:
    return data / "components.json"


def load_state(data: Path) -> dict:
    try:
        st = json.loads(_state_path(data).read_text(encoding="utf-8"))
        if isinstance(st, dict) and isinstance(st.get("components"), dict):
            return st
    except (OSError, ValueError):
        pass
    return {"schema": 1, "components": {}}


def save_state(data: Path, st: dict) -> None:
    with _state_lock:
        data.mkdir(parents=True, exist_ok=True)
        p = _state_path(data)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, p)


def _stamp(ts: float) -> dict:
    d = datetime.fromtimestamp(ts, timezone.utc)
    return {"version": d.strftime("%Y%m%d"), "date": d.strftime("%Y-%m-%d"), "source": "local"}


def _newest(folder: Path, stem: str) -> Path | None:
    cands = [p for p in folder.glob(f"{stem}*.mbtiles") if p.is_file()]
    return max(cands, key=lambda p: p.stat().st_mtime_ns) if cands else None


def detect_installed(data: Path, component_id: str) -> dict | None:
    """Version of data that was built or copied locally (no components.json entry)."""
    try:
        if component_id == "maps":
            live = db.resolve(data / "maps.db")
            if not live.exists():
                return None
            try:
                con = sqlite3.connect(f"file:{live.as_posix()}?mode=ro", uri=True)
                built = con.execute("SELECT value FROM meta WHERE key = 'built'").fetchone()
                con.close()
                if built and built[0] and built[0][:4].isdigit():
                    d = datetime.fromisoformat(built[0].replace("Z", "+00:00"))
                    return {"version": d.strftime("%Y%m%d"), "date": d.strftime("%Y-%m-%d"), "source": "local"}
            except (sqlite3.Error, ValueError):
                pass
            return _stamp(live.stat().st_mtime)
        if component_id == "basemap":
            p = _newest(data / "tiles", "israel")
            return _stamp(p.stat().st_mtime) if p else None
        if component_id == "terrain":
            p = _newest(data / "tiles", "contours") or _newest(data / "tiles", "terrain")
            return _stamp(p.stat().st_mtime) if p else None
        if component_id == "transit":
            p = db.resolve(data / "transit" / "transit.npz")
            return _stamp(p.stat().st_mtime) if p.exists() else None
        if component_id == "media":
            d = data / "media"
            if d.is_dir() and any(d.iterdir()):
                return _stamp(d.stat().st_mtime)
            return None
        if component_id == "zmanim":
            from ..etl import zmanim
            p = zmanim.snapshot_path(data)
            return _stamp(p.stat().st_mtime) if p.exists() else None
    except (OSError, ImportError):
        return None
    return None


def installed(data: Path) -> dict[str, dict | None]:
    st = load_state(data)["components"]
    return {cid: st.get(cid) or detect_installed(data, cid) for cid in catalog.IDS}


def _vkey(v) -> str:
    return "".join(ch for ch in str(v or "") if ch.isdigit()).ljust(14, "0")


def needs_update(inst: dict | None, avail: dict | None) -> bool:
    if not avail or avail.get("status") == "unavailable" or not avail.get("file"):
        return False
    if not inst:
        return True
    if inst.get("sha256") and inst.get("sha256") == avail.get("sha256"):
        return False
    return _vkey(avail.get("version")) > _vkey(inst.get("version"))


def compatible(avail: dict | None) -> bool:
    return catalog.parse_version(__version__) >= catalog.parse_version((avail or {}).get("min_app_version"))


# =========================================================================== check

def check(force: bool = False, data: Path | None = None) -> dict:
    """Installed vs. available versions; never raises (errors are reported in ``error``)."""
    data = data or data_dir()
    manifest, error = None, None
    try:
        manifest = fetch_manifest(force=force)
    except UpdateError as e:
        error = {"code": e.code, "message": e.message}
    except Exception as e:  # noqa: BLE001
        err = _http_error(e)
        error = {"code": err.code, "message": err.message}
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    st = load_state(data)
    if manifest is not None:
        with _state_lock:
            st = load_state(data)
            st["last_check"] = now
            st["release"] = manifest.get("release")
            try:
                save_state(data, st)
            except OSError:
                pass
    inst = installed(data)
    jobs = status()["jobs"]
    items, n, total = [], 0, 0
    for comp in catalog.COMPONENTS:
        avail = (manifest or {}).get("components", {}).get(comp.id)
        if avail is not None:
            avail = {k: avail.get(k) for k in ("version", "date", "file", "size", "sha256", "status", "min_app_version",
                                               "release", "description_he")}
        upd = needs_update(inst[comp.id], avail)
        ok = compatible(avail) if avail else True
        chain = delta_plan(comp.id, inst[comp.id], (manifest or {}).get("components", {}).get(comp.id)) \
            if upd and ok else None
        if avail is not None:
            avail["delta"] = {"size": sum(int(d["size"]) for d in chain), "steps": len(chain)} if chain else None
            avail["download_size"] = avail["delta"]["size"] if chain else avail.get("size")
        if upd and ok:
            n += 1
            total += int((avail or {}).get("download_size") or 0)
        items.append({
            "id": comp.id, "name_he": comp.name_he,
            "description_he": (avail or {}).get("description_he") or comp.description_he,
            "installed": inst[comp.id], "available": avail,
            "update_available": upd and ok, "compatible": ok, "job": jobs.get(comp.id),
        })
    from . import appupdate
    return {
        "app_version": __version__, "app": appupdate.check_app(force=force),
        "online": manifest is not None, "error": error,
        "release": (manifest or {}).get("release") or st.get("release"),
        "generated": (manifest or {}).get("generated"),
        "checked_at": now if manifest is not None else st.get("last_check"),
        "updates_available": n, "total_size": total, "components": items,
        "busy": any(j["state"] in ACTIVE for j in jobs.values()),
    }


# =========================================================================== download + install

ACTIVE = ("queued", "downloading", "verifying", "installing")


@dataclass
class Job:
    component: str
    state: str = "queued"  # queued | downloading | verifying | installing | done | error
    downloaded: int = 0
    total: int = 0
    version: str | None = None
    error: str | None = None
    code: str | None = None
    started: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    finished: str | None = None
    source: str = "online"  # online | file (an offline update file)
    #: a delta (chain) is downloaded instead of the full package of full_size bytes
    delta: bool = False
    full_size: int = 0

    def as_dict(self) -> dict:
        d = asdict(self)
        d["percent"] = round(100 * self.downloaded / self.total, 1) if self.total else None
        return d


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, dest: Path, size: int | None = None, progress=None, allow=None) -> Path:
    """Download *url* to *dest*, resuming ``dest.part`` with HTTP Range; retried with back-off.

    *allow*: optional ``url -> bool`` every URL and redirect must pass (the app installer)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    if allow is not None and not allow(url):
        raise UpdateError("bad_url", "כתובת ההורדה אינה מורשית – ההורדה בוטלה.")
    local = _local_path(url)
    if local is not None:  # a local file (tests, CI, a copied release folder)
        if not local.exists():
            raise UpdateError("not_found", "קובץ העדכון לא נמצא.")
        shutil.copyfile(local, part)
        if progress:
            progress(part.stat().st_size, part.stat().st_size)
        os.replace(part, dest)
        return dest
    last: Exception | None = None
    for attempt in range(RETRIES):
        have = part.stat().st_size if part.exists() else 0
        if size and have > size:
            part.unlink()
            have = 0
        if size and have == size:
            break
        try:
            with _open(url, {"Range": f"bytes={have}-"} if have else None, timeout=60, allow=allow) as r:
                status = getattr(r, "status", None) or 200
                if have and status != 206:  # server ignored the range: start over
                    have = 0
                length = int(r.headers.get("Content-Length") or 0)
                total = size or (have + length if length else 0)
                with open(part, "ab" if have else "wb") as out:
                    got = have
                    while chunk := r.read(CHUNK):
                        out.write(chunk)
                        got += len(chunk)
                        if progress:
                            progress(got, total)
            break
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 416:  # range not satisfiable – the partial file is stale
                part.unlink(missing_ok=True)
            elif e.code in (403, 404, 410):
                raise _http_error(e) from e
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            last = e
        log.warning("download attempt %d failed: %s", attempt + 1, last)
        time.sleep(min(30, 2 * 2 ** attempt) if not os.environ.get("HMAPS_UPDATES_NO_SLEEP") else 0)
    else:
        raise UpdateError("download_failed", "ההורדה נכשלה אחרי כמה ניסיונות. בדקו את החיבור לאינטרנט ונסו שוב "
                                             "(ההורדה תמשיך מהמקום שבו נעצרה).") from last
    os.replace(part, dest)
    return dest


def _safe_member(name: str) -> bool:
    p = PurePosixPath(name)
    return bool(name) and not p.is_absolute() and ".." not in p.parts and "\\" not in name and ":" not in name


def extract(component_id: str, zip_path: Path, staging: Path) -> Path:
    """Extract the members that belong to the component layout into *staging*."""
    comp = catalog.get(component_id)
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as e:
        raise UpdateError("bad_zip", "קובץ העדכון פגום. נסו להוריד אותו שוב.") from e
    with zf:
        names = [i.filename for i in zf.infolist() if not i.is_dir()]
        if any(not _safe_member(n) for n in names):
            raise UpdateError("bad_zip", "קובץ העדכון מכיל נתיבים לא חוקיים ולא הותקן.")
        if catalog.DESCRIPTOR in names:
            desc = json.loads(zf.read(catalog.DESCRIPTOR).decode("utf-8"))
            if desc.get("component") != component_id:
                raise UpdateError("wrong_component", "קובץ העדכון שייך לרכיב אחר ולא הותקן.")
        missing = [r for r in comp.required if r not in names]
        if missing:
            raise UpdateError("bad_zip", "בקובץ העדכון חסרים קבצים: " + ", ".join(missing))
        for info in zf.infolist():
            if info.is_dir() or not catalog.member_allowed(comp, info.filename) or info.filename == catalog.DESCRIPTOR:
                continue
            target = staging.joinpath(*PurePosixPath(info.filename).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out, CHUNK)
    return staging


def _replace(src: Path, dst: Path, attempts: int = 5) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    for i in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:  # Windows: briefly open by a reader
            if i == attempts - 1:
                raise
            time.sleep(0.5)


def _publish_tiles(src: Path, tiles: Path, stem: str) -> Path:
    """New timestamped tile set; the server always serves the newest ``<stem>*.mbtiles``."""
    tiles.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    final = tiles / f"{stem}-{stamp}.mbtiles"
    n = 1
    while final.exists():
        final = tiles / f"{stem}-{stamp}-{n}.mbtiles"
        n += 1
    _replace(src, final)
    os.utime(final, None)
    for old in sorted(tiles.glob(f"{stem}*.mbtiles"), key=lambda p: p.stat().st_mtime_ns)[:-2]:
        try:
            old.unlink()
        except OSError:
            pass  # still open by the server (Windows) – removed next time
    return final


def _check_sqlite(path: Path, table: str) -> None:
    try:
        con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            con.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchall()
        finally:
            con.close()
    except sqlite3.Error as e:
        raise UpdateError("bad_data", "קובץ הנתונים שהתקבל פגום ולא הותקן.") from e


def _swap(component_id: str, staging: Path, data: Path) -> None:
    if component_id == "maps":
        staged = staging / "maps.db"
        _check_sqlite(staged, "meta")
        _reapply_zmanim(staged, data)
        db.publish(staged, data / "maps.db")
        graph = staging / "routing" / "graph.npz"
        if graph.exists():
            _replace(graph, data / "routing" / "graph.npz")
            if (staging / "routing" / "graph.json").exists():
                _replace(staging / "routing" / "graph.json", data / "routing" / "graph.json")
        reports = staging / "reports"
        if reports.is_dir():
            for f in reports.iterdir():
                if f.is_file():
                    _replace(f, data / "reports" / f.name)
    elif component_id == "basemap":
        _check_sqlite(staging / "israel.mbtiles", "tiles")
        _publish_tiles(staging / "israel.mbtiles", data / "tiles", "israel")
    elif component_id == "terrain":
        for stem in ("contours", "terrain"):
            _check_sqlite(staging / f"{stem}.mbtiles", "tiles")
        for stem in ("contours", "terrain"):
            _publish_tiles(staging / f"{stem}.mbtiles", data / "tiles", stem)
    elif component_id == "transit":
        meta = json.loads((staging / "transit.json").read_text(encoding="utf-8"))
        transit_dir = data / "transit"
        transit_dir.mkdir(parents=True, exist_ok=True)
        live = db.publish(staging / "transit.npz", transit_dir / "transit.npz")
        meta["file"] = live.name
        tmp = transit_dir / "transit.json.tmp"
        tmp.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        _replace(tmp, transit_dir / "transit.json")
    elif component_id == "media":
        src_root = staging / "media"
        if src_root.is_dir():
            for f in src_root.rglob("*"):
                if f.is_file() and f.name not in ("failed.txt", "pending.txt"):
                    _replace(f, data / "media" / f.relative_to(src_root))
    elif component_id == "zmanim":
        from ..etl import zmanim
        snap = staging / "zmanim.json"
        json.loads(snap.read_text(encoding="utf-8"))  # must be valid JSON
        _replace(snap, zmanim.snapshot_path(data))
        if db.resolve(data / "maps.db").exists():
            zmanim.cmd(argparse.Namespace(db=str(data / "maps.db"), fetch=False, apply=True, limit=None, workers=4))
    else:  # pragma: no cover – catalog and installer out of sync
        raise UpdateError("unknown_component", "רכיב לא מוכר.")


def _reapply_zmanim(staged_db: Path, data: Path) -> None:
    """Keep the locally installed prayer times when a new map database arrives."""
    from ..etl import zmanim
    if not zmanim.snapshot_path(data).exists():
        return
    try:
        con = sqlite3.connect(staged_db)
        try:
            zmanim.apply(con, zmanim.load_snapshot(data))
        finally:
            con.close()
    except Exception:  # noqa: BLE001 – the map must still be installed
        log.exception("could not re-apply the prayer times to the new database")


def notify_server(component_id: str) -> None:
    """Make a server running in this process switch to the new files immediately."""
    app_mod = sys.modules.get("hmaps.api.app")
    st = getattr(app_mod, "STATE", None) if app_mod else None
    if st is None:
        return
    for name in {"maps": ("ds",), "zmanim": ("ds",), "basemap": ("basemap",),
                 "terrain": ("contours", "terrain")}.get(component_id, ()):
        obj = getattr(st, name, None)
        if obj is not None and hasattr(obj, "reload"):
            try:
                obj.reload()
            except Exception:  # noqa: BLE001
                log.exception("reload of %s failed", name)


class _Lock:
    """Cross-process lock so the server and a scheduled CLI run never install at the same time."""

    def __init__(self, path: Path, stale: float = 6 * 3600):
        self.path, self.stale = path, stale

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                return self
            except FileExistsError:
                try:
                    if time.time() - self.path.stat().st_mtime > self.stale:
                        self.path.unlink()
                        continue
                except OSError:
                    continue
                raise UpdateError("busy", "עדכון אחר כבר רץ כרגע (אולי עדכון מתוזמן). נסו שוב בעוד כמה דקות.")
        raise UpdateError("busy", "עדכון אחר כבר רץ כרגע. נסו שוב בעוד כמה דקות.")

    def __exit__(self, *exc):
        try:
            self.path.unlink()
        except OSError:
            pass


def _record(component_id: str, data: Path, entry: dict, source: str, fallback_file: str,
            via: str = "full") -> dict:
    rec = {"version": entry.get("version"), "date": entry.get("date"), "release": entry.get("release"),
           "file": entry.get("file") or fallback_file, "sha256": entry.get("sha256"), "size": entry.get("size"),
           "source": source, "via": via,
           "installed_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    if not rec["version"]:
        rec.update({k: v for k, v in _stamp(time.time()).items() if k != "source"})
    with _state_lock:
        st = load_state(data)
        st["components"][component_id] = rec
        save_state(data, st)
    notify_server(component_id)
    log.info("installed %s %s (%s)", component_id, rec["version"], via)
    return rec


def install_zip(component_id: str, zip_path: Path, data: Path, entry: dict | None = None,
                source: str | None = None) -> dict:
    """Extract + swap a component ZIP into *data* and record it in components.json."""
    work = data / "updates"
    staging = extract(component_id, zip_path, work / "staging" / component_id)
    try:
        _keep_base(component_id, staging, data)
        _swap(component_id, staging, data)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return _record(component_id, data, entry or {}, source or ("online" if entry else "file"), zip_path.name)


# --------------------------------------------------------------------------- delta packages

def base_dir(data: Path) -> Path:
    """Pristine copies of installed files that the app modifies afterwards (the base of binary deltas)."""
    return data / "updates" / "base"


def _keep_base(component_id: str, staging: Path, data: Path) -> None:
    """maps.db gets the local prayer times written into it after install – keep the file as released,
    so the next week's binary delta has its base (~180 MB; skipped when the disk is nearly full)."""
    if component_id != "maps":
        return
    src = staging / "maps.db"
    dst = base_dir(data) / "maps" / "maps.db"
    try:
        if not src.is_file():
            return
        dst.parent.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(dst.parent).free < src.stat().st_size * 3:
            dst.unlink(missing_ok=True)
            return
        tmp = dst.with_name("maps.db.tmp")
        shutil.copyfile(src, tmp)
        os.replace(tmp, dst)
    except OSError:
        log.warning("could not keep the base copy of maps.db – the next update downloads the full package")
        try:
            dst.unlink(missing_ok=True)
        except OSError:
            pass


def base_file(component_id: str, data: Path, path: str) -> Path | None:
    """The installed file that a package member *path* replaced (the base of a delta)."""
    p = PurePosixPath(path)
    if component_id == "maps":
        if path == "maps.db":
            pristine = base_dir(data) / "maps" / "maps.db"
            return pristine if pristine.is_file() else db.resolve(data / "maps.db")
        return data.joinpath(*p.parts)  # routing/…, reports/…
    if component_id in ("basemap", "terrain") and path.endswith(".mbtiles") and len(p.parts) == 1:
        return _newest(data / "tiles", p.stem)
    if component_id == "transit":
        return db.resolve(data / "transit" / "transit.npz") if path == "transit.npz" else data / "transit" / path
    if component_id == "zmanim":
        from ..etl import zmanim
        return zmanim.snapshot_path(data)
    return None


def delta_plan(component_id: str, inst: dict | None, entry: dict | None) -> list[dict] | None:
    """The deltas to download instead of the full package (None: full package)."""
    if os.environ.get("HMAPS_UPDATES_DELTA", "1") == "0" or not inst or not entry:
        return None
    if not inst.get("version") or inst.get("source") == "local" or not entry.get("deltas"):
        return None
    return delta.plan(str(inst["version"]), str(entry.get("version")), entry["deltas"],
                      int(entry.get("size") or 0) or None)


def _install_delta(component_id: str, chain: list[dict], manifest: dict, entry: dict, data: Path, job: Job,
                   on_progress=None) -> dict:
    """Download + verify + apply a chain of deltas, then swap like a full package. Raises DeltaError
    (or UpdateError of a download) – the caller then falls back to the full package."""
    comp = catalog.get(component_id)
    work = data / "updates"
    is_dir = component_id in delta.DIR_COMPONENTS
    total = sum(int(d["size"]) for d in chain)
    job.delta, job.full_size, job.total, job.downloaded = True, int(entry.get("size") or 0), total, 0
    files: list[Path] = []
    stagings: list[Path] = []
    try:
        offset = 0
        for d in chain:  # download (and verify) every step first: no work is wasted on a missing one
            dest = work / "downloads" / d["file"]
            job.state = "downloading"

            def progress(got: int, _t: int, _o=offset) -> None:
                job.downloaded = _o + got
                if on_progress:
                    on_progress(job.downloaded, total)

            download(_asset_url(manifest, d), dest, int(d["size"]), progress)
            files.append(dest)
            job.state = "verifying"
            if dest.stat().st_size != int(d["size"]) or (d.get("sha256") and sha256_file(dest) != d["sha256"].lower()):
                dest.unlink(missing_ok=True)
                raise delta.DeltaError("checksum", f"{d['file']}: size / SHA-256 mismatch")
            offset += int(d["size"])
        job.state = "installing"
        deleted: set[str] = set()
        staging = work / "staging" / f"{component_id}-delta"
        shutil.rmtree(staging, ignore_errors=True)
        stagings.append(staging)
        prev: Path | None = None
        for i, (d, f) in enumerate(zip(chain, files)):
            if not is_dir and i:
                prev, staging = staging, work / "staging" / f"{component_id}-delta{i}"
                shutil.rmtree(staging, ignore_errors=True)
                stagings.append(staging)
            base = (lambda path, _p=prev: _p.joinpath(*PurePosixPath(path).parts)) if prev is not None else \
                (lambda path: base_file(component_id, data, path))
            desc = delta.apply(component_id, f, staging, base, expect_from=d.get("from"), expect_to=d.get("to"))
            for op in desc.get("files") or []:
                if not catalog.member_allowed(comp, op["path"]):
                    raise delta.DeltaError("bad_delta", f"{op['path']}: not part of the component")
                deleted.discard(op["path"])
            deleted |= {p for p in desc.get("deleted") or [] if catalog.member_allowed(comp, p)}
            if prev is not None:
                shutil.rmtree(prev, ignore_errors=True)
        if not is_dir:
            missing = [r for r in comp.required if not (staging / r).is_file()]
            if missing:
                raise delta.DeltaError("bad_delta", "missing after applying: " + ", ".join(missing))
        _keep_base(component_id, staging, data)
        _swap(component_id, staging, data)
        if is_dir:  # images removed from the release (never the user's own files: only listed paths)
            for p in sorted(deleted):
                try:
                    data.joinpath(*PurePosixPath(p).parts).unlink(missing_ok=True)
                except OSError:
                    pass
    finally:
        for st in stagings:
            shutil.rmtree(st, ignore_errors=True)
        for f in files:
            f.unlink(missing_ok=True)
    return _record(component_id, data, {**entry, "release": entry.get("release") or manifest.get("release")},
                   "online", entry.get("file") or "", via="delta")


def install(component_id: str, data: Path | None = None, job: Job | None = None, force: bool = False,
            manifest: dict | None = None, on_progress=None) -> dict:
    """Download, verify (size + SHA-256) and install one component from the newest manifest.

    Takes a delta chain when one leads from the installed version and is much smaller (see
    :mod:`hmaps.core.delta`); any problem with it falls back to the full package."""
    data = data or data_dir()
    job = job or Job(component_id)
    catalog.get(component_id)
    manifest = manifest or fetch_manifest()
    entry = manifest.get("components", {}).get(component_id)
    if not entry or entry.get("status") == "unavailable" or not entry.get("file"):
        raise UpdateError("unavailable", "הרכיב הזה עדיין לא זמין בגרסה המקוונת.")
    if not compatible(entry):
        raise UpdateError("app_too_old", f"העדכון דורש גרסה {entry.get('min_app_version')} של התוכנה או חדשה יותר. "
                                         "התקינו קודם את הגרסה החדשה של מפות ישראל.")
    inst = installed(data).get(component_id)
    if not force and not needs_update(inst, entry):
        job.state = "done"
        return load_state(data)["components"].get(component_id) or {}
    job.version = entry.get("version")
    job.total = int(entry.get("size") or 0)
    with _Lock(data / "updates" / ".lock"):
        free = shutil.disk_usage(data if data.exists() else data.parent).free
        if job.total and free < job.total * 3:
            raise UpdateError("disk_full", "אין מספיק מקום פנוי בדיסק להורדה ולהתקנה של העדכון.")
        chain = None if force else delta_plan(component_id, inst, entry)
        if chain:
            try:
                return _install_delta(component_id, chain, manifest, entry, data, job, on_progress)
            except (delta.DeltaError, UpdateError) as e:
                log.warning("delta update of %s failed (%s: %s) – downloading the full package", component_id,
                            getattr(e, "code", ""), getattr(e, "message", e))
            job.delta, job.downloaded, job.total = False, 0, int(entry.get("size") or 0)
        dest = data / "updates" / "downloads" / entry["file"]
        job.state = "downloading"

        def progress(got: int, total: int) -> None:
            job.downloaded = got
            if total:
                job.total = total
            if on_progress:
                on_progress(got, total)

        download(_asset_url(manifest, entry), dest, job.total or None, progress)
        _verify(dest, entry, job)
        job.state = "installing"
        try:
            rec = install_zip(component_id, dest, data, {**entry, "release": entry.get("release") or manifest.get("release")})
        finally:
            dest.unlink(missing_ok=True)
    return rec


def _verify(dest: Path, entry: dict, job: Job) -> None:
    """Size + SHA-256 of a downloaded / copied component ZIP against its manifest entry."""
    job.state = "verifying"
    offline = job.source == "file"
    if job.total and dest.stat().st_size != job.total:
        dest.unlink(missing_ok=True)
        raise UpdateError("size_mismatch", "קובץ העדכון פגום או חלקי (הגודל שגוי). העתיקו אותו שוב." if offline else
                          "גודל הקובץ שהורד שגוי (ייתכן שסינון האינטרנט חסם אותו). נסו שוב.")
    if entry.get("sha256") and sha256_file(dest) != entry["sha256"].lower():
        dest.unlink(missing_ok=True)
        raise UpdateError("checksum", "בדיקת השלמות של קובץ העדכון נכשלה – הקובץ פגום. הורידו והעתיקו אותו שוב."
                          if offline else "בדיקת השלמות של הקובץ שהורד נכשלה (ייתכן שסינון האינטרנט שינה אותו). "
                                          "הקובץ נמחק ולא הותקן.")


# =========================================================================== offline update file

#: file name of the offline update file on the release pages (``-partN`` when split, see tools/release.py)
BUNDLE_PREFIX = "HebrewMaps-Update"


def uploads_dir(data: Path) -> Path:
    return data / "updates" / "offline"


def _bad_bundle(msg: str = "") -> UpdateError:
    return UpdateError("bad_bundle", msg or "הקובץ שנבחר אינו קובץ עדכון של מפות ישראל. "
                                           f"בחרו את הקובץ {BUNDLE_PREFIX}-….zip שהורד מאתר ההורדה.")


def read_bundle(path: Path) -> dict:
    """The manifest of an offline update file, limited to the components that are inside it."""
    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            if "manifest.json" not in names:
                raise _bad_bundle()
            m = _validate_manifest(json.loads(zf.read("manifest.json").decode("utf-8")), str(path))
    except UpdateError:
        raise
    except FileNotFoundError:
        raise UpdateError("not_found", "קובץ העדכון לא נמצא.") from None
    except (zipfile.BadZipFile, ValueError, KeyError, OSError) as e:
        raise _bad_bundle("קובץ העדכון פגום או שהעתקתו לא הושלמה. העתיקו אותו שוב.") from e
    comps = {}
    for cid, e in m["components"].items():
        f = (e or {}).get("file")
        if cid in catalog.BY_ID and f and "/" not in f and "\\" not in f and f in names:
            comps[cid] = e
    if not comps:
        raise _bad_bundle("בקובץ העדכון אין חבילות נתונים.")
    m["components"] = comps
    m["_source"] = str(path)
    return m


def install_from_bundle(component_id: str, bundle: Path, data: Path | None = None, job: Job | None = None,
                        force: bool = False, manifest: dict | None = None, on_progress=None) -> dict:
    """Copy one component ZIP out of an offline update file, verify it (size + SHA-256) and install it."""
    data = data or data_dir()
    job = job or Job(component_id, source="file")
    job.source = "file"
    catalog.get(component_id)
    manifest = manifest or read_bundle(bundle)
    entry = manifest["components"].get(component_id)
    if not entry:
        raise UpdateError("unavailable", "הרכיב הזה לא נמצא בקובץ העדכון.")
    if not compatible(entry):
        raise UpdateError("app_too_old", f"העדכון דורש גרסה {entry.get('min_app_version')} של התוכנה או חדשה יותר. "
                                         "התקינו קודם את הגרסה החדשה של מפות ישראל (גם קובץ ההתקנה אפשר להעביר "
                                         "בדיסק-און-קי).")
    if not force and not needs_update(installed(data).get(component_id), entry):
        job.state = "done"
        return load_state(data)["components"].get(component_id) or {}
    job.version = entry.get("version")
    job.total = int(entry.get("size") or 0)
    with _Lock(data / "updates" / ".lock"):
        free = shutil.disk_usage(data if data.exists() else data.parent).free
        if job.total and free < job.total * 2:
            raise UpdateError("disk_full", "אין מספיק מקום פנוי בדיסק להתקנת העדכון.")
        dest = data / "updates" / "downloads" / entry["file"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        job.state = "downloading"  # copying out of the file; the UI words it by job.source
        job.downloaded = 0
        try:
            with zipfile.ZipFile(bundle) as zf, zf.open(entry["file"]) as src, open(part, "wb") as out:
                while chunk := src.read(CHUNK):
                    out.write(chunk)
                    job.downloaded += len(chunk)
                    if on_progress:
                        on_progress(job.downloaded, job.total)
            os.replace(part, dest)
        except (zipfile.BadZipFile, OSError, EOFError) as e:
            part.unlink(missing_ok=True)
            if isinstance(e, OSError) and getattr(e, "errno", None) == 28:
                raise UpdateError("disk_full", "אין מספיק מקום פנוי בדיסק להתקנת העדכון.") from e
            raise UpdateError("bad_bundle", "קובץ העדכון פגום או שהעתקתו לא הושלמה. העתיקו אותו שוב.") from e
        _verify(dest, entry, job)
        job.state = "installing"
        try:
            rec = install_zip(component_id, dest, data,
                              {**entry, "release": entry.get("release") or manifest.get("release")}, source="offline")
        finally:
            dest.unlink(missing_ok=True)
    return rec


def bundle_plan(manifest: dict, data: Path, force: bool = False) -> list[str]:
    """Components of an offline update file that are newer than the installed data (all with *force*)."""
    inst = installed(data)
    return [cid for cid in catalog.IDS if cid in manifest["components"]
            and (force or needs_update(inst.get(cid), manifest["components"][cid]))]


# =========================================================================== background jobs

_jobs: dict[str, Job] = {}
_jobs_lock = threading.Lock()
_queue: list[tuple[Job, bool, Path | None]] = []
_worker: threading.Thread | None = None


def status() -> dict:
    from . import appupdate
    with _jobs_lock:
        jobs = {cid: j.as_dict() for cid, j in _jobs.items()}
    return {"busy": any(j["state"] in ACTIVE for j in jobs.values()), "jobs": jobs, "app": appupdate.job_status()}


def _run_queue(data: Path) -> None:
    global _worker
    while True:
        with _jobs_lock:
            if not _queue:
                _worker = None
                _cleanup_uploads(data)
                return
            job, force, bundle = _queue.pop(0)
        try:
            if bundle is not None:
                install_from_bundle(job.component, bundle, data, job, force=force)
            else:
                install(job.component, data, job, force=force)
            job.state = "done"
        except UpdateError as e:
            job.state, job.code, job.error = "error", e.code, e.message
        except Exception as e:  # noqa: BLE001
            log.exception("update of %s failed", job.component)
            job.state, job.code, job.error = "error", "error", "ההתקנה נכשלה: " + (str(e) or e.__class__.__name__)
        job.finished = datetime.now(timezone.utc).isoformat(timespec="seconds")


def _cleanup_uploads(data: Path) -> None:
    """Offline update files uploaded through the browser are temporary copies: drop them once installed."""
    for p in uploads_dir(data).glob("upload-*"):
        try:
            p.unlink()
        except OSError:
            pass


def start(component_ids: list[str], data: Path | None = None, force: bool = False,
          bundle: Path | None = None) -> list[dict]:
    """Queue background installs (one at a time). Already active components are not queued twice.

    With *bundle* the components come from that offline update file instead of the internet."""
    global _worker
    data = data or data_dir()
    out = []
    with _jobs_lock:
        for cid in component_ids:
            cur = _jobs.get(cid)
            if cur and cur.state in ACTIVE:
                out.append(cur.as_dict())
                continue
            job = Job(cid, source="file" if bundle is not None else "online")
            _jobs[cid] = job
            _queue.append((job, force, bundle))
            out.append(job.as_dict())
        if _worker is None:
            if not _queue:
                if bundle is not None:
                    _cleanup_uploads(data)
                return out
            _worker = threading.Thread(target=_run_queue, args=(data,), name="hmaps-updates", daemon=True)
            _worker.start()
    return out


# =========================================================================== HTTP endpoints

def _routes():
    from starlette.concurrency import run_in_threadpool
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    def err(status: int, code: str, message: str) -> JSONResponse:
        return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)

    def _allowed(req: Request) -> bool:
        mode = os.environ.get("HMAPS_UPDATES", "local")  # local | any | off
        if mode == "off":
            return False
        host = req.client.host if req.client else ""
        return mode == "any" or host in ("127.0.0.1", "::1", "localhost", "testclient")

    async def updates_check(req: Request):
        force = req.query_params.get("refresh") in ("1", "true")
        body = await run_in_threadpool(check, force)
        return JSONResponse(body, headers={"Cache-Control": "no-store"})

    async def updates_status(req: Request):
        return JSONResponse(status(), headers={"Cache-Control": "no-store"})

    async def updates_offline(req: Request):
        """The offline update file as the request body (chosen in the "עדכונים" page): store, read, queue."""
        from starlette.requests import ClientDisconnect

        if not _allowed(req):
            return err(403, "forbidden", "התקנת עדכונים אפשרית רק מהמחשב שעליו רצה התוכנה.")
        if req.headers.get("x-hmaps-update") != "1":
            return err(400, "missing_header", "בקשה לא תקינה.")
        if status()["busy"]:
            return err(409, "busy", "עדכון אחר עדיין רץ. המתינו שיסתיים ונסו שוב.")
        data = data_dir()
        up = uploads_dir(data)
        up.mkdir(parents=True, exist_ok=True)
        length = int(req.headers.get("content-length") or 0)
        if length and shutil.disk_usage(up).free < length * 3:
            return err(507, "disk_full", "אין מספיק מקום פנוי בדיסק לקובץ העדכון ולהתקנתו.")
        path = up / f"upload-{int(time.time() * 1000)}.zip"
        part = path.with_name(path.name + ".part")
        try:
            with open(part, "wb") as f:
                async for chunk in req.stream():
                    if chunk:
                        f.write(chunk)
            os.replace(part, path)
        except (ClientDisconnect, OSError) as e:
            part.unlink(missing_ok=True)
            if isinstance(e, OSError) and getattr(e, "errno", None) == 28:
                return err(507, "disk_full", "אין מספיק מקום פנוי בדיסק לקובץ העדכון ולהתקנתו.")
            return err(400, "upload_failed", "העתקת הקובץ נקטעה. נסו שוב.")
        try:
            m = await run_in_threadpool(read_bundle, path)
        except UpdateError as e:
            path.unlink(missing_ok=True)
            return err(400, e.code, e.message)
        force = req.query_params.get("force") in ("1", "true")
        ids = await run_in_threadpool(bundle_plan, m, data, force)
        rel = m.get("release")
        if not ids:
            path.unlink(missing_ok=True)
            return JSONResponse({"jobs": [], "release": rel,
                                 "message": "הנתונים במחשב כבר עדכניים כמו בקובץ הזה (או חדשים ממנו)."})
        return JSONResponse({"jobs": start(ids, data, force=force, bundle=path), "release": rel}, status_code=202)

    async def updates_install(req: Request):
        if not _allowed(req):
            return err(403, "forbidden", "התקנת עדכונים אפשרית רק מהמחשב שעליו רצה התוכנה.")
        if req.headers.get("x-hmaps-update") != "1":  # blocks cross-site form posts (no CORS preflight)
            return err(400, "missing_header", "בקשה לא תקינה.")
        comp = req.path_params["component"]
        force = req.query_params.get("force") in ("1", "true")
        if comp == "all":
            info = await run_in_threadpool(check, False)
            if info["error"]:
                return err(502, info["error"]["code"], info["error"]["message"])
            ids = [c["id"] for c in info["components"] if c["update_available"]]
            if not ids:
                return JSONResponse({"jobs": [], "message": "כל הרכיבים מעודכנים."}, status_code=200)
        elif comp in catalog.BY_ID:
            ids = [comp]
        else:
            return err(404, "unknown_component", "רכיב עדכון לא מוכר.")
        return JSONResponse({"jobs": start(ids, force=force)}, status_code=202)

    async def app_check(req: Request):
        from . import appupdate
        force = req.query_params.get("refresh") in ("1", "true")
        return JSONResponse(await run_in_threadpool(appupdate.check_app, force), headers={"Cache-Control": "no-store"})

    async def app_install(req: Request):
        """The program itself: download + verify + start the installer (appupdate.py)."""
        from . import appupdate
        if not _allowed(req):
            return err(403, "forbidden", "עדכון התוכנה אפשרי רק מהמחשב שעליו היא רצה.")
        if req.headers.get("x-hmaps-update") != "1":
            return err(400, "missing_header", "בקשה לא תקינה.")
        ok, why = appupdate.can_self_update()
        if not ok:
            return err(400, "unsupported", why or "עדכון אוטומטי אינו זמין כאן.")
        if status()["busy"]:
            return err(409, "busy", "עדכון נתונים עדיין רץ. המתינו שיסתיים ונסו שוב.")
        info = await run_in_threadpool(appupdate.check_app, True)
        if info["error"]:
            return err(502, info["error"]["code"], info["error"]["message"])
        force = req.query_params.get("force") in ("1", "true")
        if not info["update_available"] and not force:
            return JSONResponse({"job": None, "message": "התוכנה כבר בגרסה העדכנית."})
        return JSONResponse({"job": appupdate.start(force=force), "version": info["available"]["version"]},
                            status_code=202)

    return [
        Route("/api/updates", updates_check),
        Route("/api/updates/status", updates_status),
        Route("/api/updates/app", app_check),  # before /api/updates/{component}
        Route("/api/updates/app", app_install, methods=["POST"]),
        Route("/api/updates/offline", updates_offline, methods=["POST"]),
        Route("/api/updates/{component}", updates_install, methods=["POST"]),
    ]


ROUTES = _routes()


# =========================================================================== CLI

def _fmt_size(n) -> str:
    n = int(n or 0)
    return f"{n / 1e9:.2f} GB" if n >= 1e9 else f"{n / 1e6:.0f} MB" if n >= 1e6 else f"{n / 1e3:.0f} KB"


def cli(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="python -m hmaps update",
                                 description="Online data updates from the weekly GitHub release")
    ap.add_argument("--check", action="store_true", help="only show what is installed and what is available")
    ap.add_argument("--component", "-c", action="append", choices=list(catalog.IDS),
                    help="update only this component (repeatable); default: every component with an update")
    ap.add_argument("--force", action="store_true", help="reinstall even if the installed version is current")
    ap.add_argument("--data", help="data directory (default: HMAPS_DATA or ./data)")
    ap.add_argument("--manifest", help="manifest URL or local path (default: the data-latest release)")
    ap.add_argument("--zip", help="install a local component ZIP (requires exactly one --component)")
    ap.add_argument("--bundle", action="append", metavar="FILE",
                    help=f"install from an offline update file ({BUNDLE_PREFIX}-<date>.zip, repeatable for split files)")
    ap.add_argument("--app", action="store_true",
                    help="update the program itself (Windows installer from the newest v* release); with --check: "
                         "only show the versions")
    ap.add_argument("--no-delta", action="store_true", help="always download full packages, never deltas")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    if args.manifest:
        os.environ["HMAPS_UPDATES_MANIFEST"] = args.manifest
    if args.no_delta:
        os.environ["HMAPS_UPDATES_DELTA"] = "0"
    if args.app:
        from . import appupdate
        return appupdate.cli(args.check, args.force)
    data = Path(args.data) if args.data else data_dir()
    if args.zip:
        if not args.component or len(args.component) != 1:
            ap.error("--zip needs exactly one --component")
        rec = install_zip(args.component[0], Path(args.zip), data)
        print(f"הותקן: {args.component[0]} ({rec['version']})")
        return 0
    if args.bundle:
        return _cli_bundles([Path(b) for b in args.bundle], data, args.component, args.force)

    info = check(force=True, data=data)
    if info["error"]:
        print(f"שגיאה: {info['error']['message']}")
        return 2
    app = info.get("app") or {}
    if app.get("update_available"):
        print(f"גרסה חדשה של התוכנה: {app['available']['version']} (מותקנת {app['installed']}) – "
              "לעדכון: python -m hmaps update --app")
    print(f"גרסת הנתונים המקוונת: {info['release']}")
    for c in info["components"]:
        inst = (c["installed"] or {}).get("date") or "לא מותקן"
        av = c["available"] or {}
        if not av.get("file"):
            mark = "לא זמין במקוון"
        elif not c["compatible"]:
            mark = "דורש גרסה חדשה של התוכנה"
        else:
            mark = "עדכון זמין" if c["update_available"] else "מעודכן"
        size = _fmt_size(av.get('size'))
        if c["update_available"] and av.get("delta"):
            mark += f" (עדכון חלקי: {_fmt_size(av['delta']['size'])} במקום {size})"
        print(f"  {c['id']:8} {c['name_he']:24} מותקן: {inst:10}  זמין: {av.get('date') or '—':10} "
              f"{size:>9}  {mark}")
    if args.check:
        return 0
    wanted = args.component or [c["id"] for c in info["components"] if c["update_available"]]
    if not wanted:
        print("כל הרכיבים מעודכנים.")
        return 0
    failed = 0
    for cid in wanted:
        job = Job(cid)
        last = [0.0]

        def progress(got, total, _job=job, _last=last):
            if time.monotonic() - _last[0] > 5:
                _last[0] = time.monotonic()
                pct = f"{100 * got / total:.0f}%" if total else _fmt_size(got)
                print(f"    {_job.component}: {pct}", flush=True)

        try:
            print(f"מעדכן {catalog.get(cid).name_he} …", flush=True)
            rec = install(cid, data, job, force=args.force, on_progress=progress)
            how = " (עדכון חלקי)" if rec.get("via") == "delta" else ""
            print(f"  ✓ {catalog.get(cid).name_he}: {rec.get('date') or rec.get('version') or 'מעודכן'}{how}")
        except UpdateError as e:
            failed += 1
            print(f"  ✗ {catalog.get(cid).name_he}: {e.message}")
    return 1 if failed else 0


def _cli_bundles(bundles: list[Path], data: Path, only: list[str] | None, force: bool) -> int:
    failed = 0
    for path in bundles:
        try:
            m = read_bundle(path)
        except UpdateError as e:
            print(f"שגיאה: {path.name}: {e.message}")
            failed += 1
            continue
        print(f"קובץ עדכון {path.name} · גרסת נתונים {m.get('release') or '—'}")
        ids = [c for c in bundle_plan(m, data, force) if not only or c in only]
        if not ids:
            print("  הנתונים במחשב כבר עדכניים כמו בקובץ הזה.")
        for cid in ids:
            try:
                print(f"מתקין {catalog.get(cid).name_he} …", flush=True)
                rec = install_from_bundle(cid, path, data, force=force, manifest=m)
                print(f"  ✓ {catalog.get(cid).name_he}: {rec.get('date') or rec.get('version') or 'מעודכן'}")
            except UpdateError as e:
                failed += 1
                print(f"  ✗ {catalog.get(cid).name_he}: {e.message}")
    return 1 if failed else 0
