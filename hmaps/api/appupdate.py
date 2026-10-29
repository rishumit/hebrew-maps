"""Self-update of the program itself ("גרסת התוכנה" on the "עדכונים" page).

App releases are the ``v<semver>`` tags built by ``.github/workflows/installer.yml``; they are the
repository's "latest" release (the weekly data releases are published with ``--latest=false``).
Next to ``HebrewMaps-Setup-<version>.exe`` every app release carries ``app-manifest.json``::

    {"schema": 1, "version": "1.2.0", "tag": "v1.2.0", "date": "2026-11-02",
     "file": "HebrewMaps-Setup-1.2.0.exe", "size": 123456789, "sha256": "…",
     "url": "https://github.com/rishumit/hebrew-maps/releases/download/v1.2.0/HebrewMaps-Setup-1.2.0.exe",
     "notes_he": "…", "min_data_version": null}

Where it is read from:

1. ``https://github.com/<repo>/releases/latest/download/app-manifest.json`` – a plain download, no
   API rate limit, and it is what filtered networks (NetFree…) usually let through;
2. fallback: ``https://api.github.com/repos/<repo>/releases/latest`` (rate limited to 60 requests an
   hour per IP): its ``app-manifest.json`` asset, else the setup asset with the ``digest`` GitHub
   reports for it. Without a SHA-256 nothing is installed.

    GET  /api/updates/app[?refresh=1]   installed vs. available version (also part of GET /api/updates)
    POST /api/updates/app               download + verify + start the installer (localhost +
                                        ``X-HMaps-Update: 1`` only, like the data updates)

``python -m hmaps update --app [--check]`` does the same from the command line.

Install: the installer is downloaded to ``%TEMP%\\HebrewMaps-update\\`` (resumed with HTTP Range,
progress in the job), checked against size + SHA-256, and started detached – outside the Job
Object of the desktop window (``CREATE_BREAKAWAY_FROM_JOB``; Task Scheduler as a fallback) – as::

    HebrewMaps-Setup-1.2.0.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS
        /FORCECLOSEAPPLICATIONS /NOCANCEL /LOG=<temp>\\HebrewMaps-update\\install-1.2.0.log /AUTOUPDATE=1

The installer (``installer/hebrew-maps.iss``) closes the running window and server, replaces the
program files (never the data in ``{app}\\data``) and, because of ``/AUTOUPDATE=1``, starts the
program again. Downloads only ever come from GitHub (``ALLOWED_HOSTS``, checked for every redirect).
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .. import __version__
from . import updates as U

log = logging.getLogger("hmaps.appupdate")

SETUP_RE = re.compile(r"^HebrewMaps-Setup-(?:Program-)?[0-9A-Za-z.+-]+\.exe$")
APP_MANIFEST = "app-manifest.json"
DOWNLOAD_PAGE = "https://rishumit.github.io/hebrew-maps/download.html"
#: the only hosts an installer (and its manifest) may come from
ALLOWED_HOSTS = frozenset({"github.com", "api.github.com", "objects.githubusercontent.com",
                           "release-assets.githubusercontent.com"})
#: "host:port" of a fake release server, also over plain http – for the tests only
TEST_HOSTS: set[str] = {h.strip() for h in os.environ.get("HMAPS_APP_TEST_HOSTS", "").split(",") if h.strip()}
TTL = 15 * 60
ERROR_TTL = 5 * 60
CREATE_NEW_PROCESS_GROUP = 0x00000200
DETACHED_PROCESS = 0x00000008
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
CREATE_NO_WINDOW = 0x08000000


def manifest_url() -> str:
    return os.environ.get("HMAPS_APP_MANIFEST") or f"https://github.com/{U.REPO}/releases/latest/download/{APP_MANIFEST}"


def api_url() -> str:
    return os.environ.get("HMAPS_APP_API") or f"https://api.github.com/repos/{U.REPO}/releases/latest"


def url_allowed(url: str) -> bool:
    """https on a GitHub host (exact match), nothing else."""
    try:
        u = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    if u.username or u.password:
        return False
    if u.scheme == "https" and host in ALLOWED_HOSTS:
        return True
    return u.scheme in ("http", "https") and f"{host}:{u.port}" in TEST_HOSTS


# =========================================================================== versions

_SEMVER = re.compile(r"^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$")


def version_key(v: str | None) -> tuple:
    """Semantic-version sort key: 1.2.0-rc.1 < 1.2.0 < 1.2.1 < 1.10.0; unparsable → lowest."""
    m = _SEMVER.match(str(v or "").strip())
    if not m:
        return ((-1,), 0, ())
    nums = tuple(int(x or 0) for x in m.group(1, 2, 3))
    pre = m.group(4)
    if not pre:
        return (nums, 1, ())
    parts = tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in pre.split("."))
    return (nums, 0, parts)


def is_newer(available: str | None, installed: str | None) -> bool:
    return _SEMVER.match(str(available or "").strip()) is not None and version_key(available) > version_key(installed)


# =========================================================================== manifest

def parse_manifest(m: dict, source: str = "") -> dict:
    """Validate an app-manifest.json; raises ValueError for anything that must not be installed."""
    if not isinstance(m, dict):
        raise ValueError("app manifest is not an object")
    version = str(m.get("version") or "").strip().lstrip("vV")
    if not _SEMVER.match(version):
        raise ValueError(f"bad version {version!r}")
    file = str(m.get("file") or "")
    if not SETUP_RE.match(file):
        raise ValueError(f"bad installer name {file!r}")
    size = m.get("size")
    if not isinstance(size, int) or size <= 0:
        raise ValueError("bad size")
    sha = str(m.get("sha256") or "").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise ValueError("missing SHA-256")
    tag = str(m.get("tag") or f"v{version}")
    url = m.get("url") or f"https://github.com/{U.REPO}/releases/download/{tag}/{file}"
    if not url_allowed(url):
        raise ValueError(f"installer URL not allowed: {url}")
    notes = m.get("notes_he")
    return {"version": version, "tag": tag, "date": m.get("date"), "file": file, "size": size, "sha256": sha,
            "url": url, "notes_he": str(notes)[:4000] if notes else None,
            "min_data_version": m.get("min_data_version"), "source": source}


def _from_api() -> dict:
    rel = U._read_json(api_url(), allow=url_allowed, headers={"Accept": "application/vnd.github+json"})
    if not isinstance(rel, dict) or not str(rel.get("tag_name", "")).startswith("v"):
        raise U.UpdateError("not_found", "לא נמצאה גרסת תוכנה לעדכון.")
    assets = rel.get("assets") or []
    for a in assets:
        if a.get("name") == APP_MANIFEST and url_allowed(a.get("browser_download_url", "")):
            return parse_manifest(U._read_json(a["browser_download_url"], allow=url_allowed), a["browser_download_url"])
    version = rel["tag_name"][1:]
    candidates = sorted(assets, key=lambda a: (0 if str(a.get("name", "")).startswith("HebrewMaps-Setup-Program-") else 1,
                                                str(a.get("name", ""))))
    for a in candidates:
        name = a.get("name", "")
        digest = str(a.get("digest") or "")
        if SETUP_RE.match(name) and digest.startswith("sha256:"):
            return parse_manifest({"version": version, "tag": rel["tag_name"], "file": name, "size": a.get("size"),
                                   "sha256": digest[7:], "url": a.get("browser_download_url"),
                                   "date": (rel.get("published_at") or "")[:10] or None,
                                   "notes_he": rel.get("body")}, api_url())
    raise U.UpdateError("bad_manifest", "לגרסה החדשה אין קובץ בדיקה (SHA-256) – היא לא תותקן אוטומטית. "
                                        "אפשר להוריד אותה מאתר ההורדה.")


_cache: tuple[float, dict | None, U.UpdateError | None] | None = None
_cache_lock = threading.Lock()


def fetch(force: bool = False) -> dict:
    """The newest app release (cached; errors are cached briefly so the page never hangs twice)."""
    global _cache
    with _cache_lock:
        now = time.monotonic()
        if not force and _cache is not None:
            age = now - _cache[0]
            if _cache[1] is not None and age < TTL:
                return _cache[1]
            if _cache[2] is not None and age < ERROR_TTL:
                raise _cache[2]
        url = manifest_url()
        try:
            try:
                info = parse_manifest(U._read_json(url, timeout=15, allow=None if U._local_path(url) else url_allowed),
                                      url)
            except Exception as first:  # noqa: BLE001
                if os.environ.get("HMAPS_APP_MANIFEST") and not os.environ.get("HMAPS_APP_API"):
                    raise U._http_error(first) from first
                log.warning("app manifest download failed (%s) – trying the GitHub API", first)
                try:
                    info = _from_api()
                except Exception as second:  # noqa: BLE001
                    import urllib.error
                    raise U._http_error(second if isinstance(first, urllib.error.HTTPError) or
                                        isinstance(second, U.UpdateError) else first) from second
        except U.UpdateError as e:
            _cache = (now, None, e)
            raise
        _cache = (now, info, None)
        return info


# =========================================================================== state

def can_self_update() -> tuple[bool, str | None]:
    """Only the installed Windows program can replace itself; elsewhere the page links to the site."""
    mode = os.environ.get("HMAPS_APP_UPDATE", "auto")  # auto | off
    if mode == "off":
        return False, "העדכון האוטומטי של התוכנה כבוי במחשב הזה."
    if sys.platform != "win32":
        return False, "עדכון אוטומטי של התוכנה זמין רק בגרסת Windows המותקנת."
    if not getattr(sys, "frozen", False):
        return False, "התוכנה רצה מקוד המקור – עדכנו אותה עם git pull או התקינו את הגרסה החדשה מאתר ההורדה."
    return True, None


@dataclass
class AppJob:
    version: str
    state: str = "queued"  # queued | downloading | verifying | launching | launched | error
    downloaded: int = 0
    total: int = 0
    error: str | None = None
    code: str | None = None
    started: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    finished: str | None = None

    def as_dict(self) -> dict:
        d = asdict(self)
        d["percent"] = round(100 * self.downloaded / self.total, 1) if self.total else None
        return d


_job: AppJob | None = None
_job_lock = threading.Lock()
APP_ACTIVE = ("queued", "downloading", "verifying", "launching")


def job_status() -> dict | None:
    with _job_lock:
        return _job.as_dict() if _job else None


def check_app(force: bool = False) -> dict:
    """Installed vs. newest program version; never raises."""
    ok, why = can_self_update()
    out = {"installed": __version__, "available": None, "update_available": False, "can_install": ok,
           "reason": why, "download_page": DOWNLOAD_PAGE, "error": None, "job": job_status()}
    if os.environ.get("HMAPS_APP_UPDATE") == "off":
        return out
    try:
        info = fetch(force=force)
    except U.UpdateError as e:
        out["error"] = {"code": e.code, "message": e.message}
        return out
    except Exception as e:  # noqa: BLE001
        err = U._http_error(e)
        out["error"] = {"code": err.code, "message": err.message}
        return out
    out["available"] = {k: info.get(k) for k in ("version", "tag", "date", "file", "size", "notes_he",
                                                 "min_data_version")}
    out["update_available"] = is_newer(info["version"], __version__)
    return out


# =========================================================================== install

def download_dir() -> Path:
    return Path(os.environ.get("HMAPS_APP_DOWNLOAD_DIR") or Path(tempfile.gettempdir()) / "HebrewMaps-update")


def download_installer(info: dict, job: AppJob | None = None, on_progress=None) -> Path:
    """The verified installer in the temp folder (resumes a partial download; reuses a complete one)."""
    if not url_allowed(info["url"]):
        raise U.UpdateError("bad_url", "כתובת ההורדה של התוכנה אינה מורשית – ההורדה בוטלה.")
    job = job or AppJob(info["version"])
    dest = download_dir() / info["file"]
    job.total = int(info["size"])
    if dest.is_file() and dest.stat().st_size == job.total and U.sha256_file(dest) == info["sha256"]:
        job.downloaded = job.total
        return dest
    dest.unlink(missing_ok=True)
    job.state = "downloading"

    def progress(got: int, total: int) -> None:
        job.downloaded = got
        if on_progress:
            on_progress(got, total or job.total)

    U.download(info["url"], dest, job.total, progress, allow=url_allowed)
    job.state = "verifying"
    if dest.stat().st_size != job.total or U.sha256_file(dest) != info["sha256"]:
        dest.unlink(missing_ok=True)
        raise U.UpdateError("checksum", "בדיקת השלמות של קובץ ההתקנה נכשלה (ייתכן שסינון האינטרנט שינה אותו). "
                                        "הקובץ נמחק ולא הופעל.")
    return dest


def installer_command(installer: Path, log_file: Path) -> list[str]:
    """Inno Setup command line of an unattended update (see installer/hebrew-maps.iss)."""
    return [str(installer), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS",
            "/FORCECLOSEAPPLICATIONS", "/NOCANCEL", f"/LOG={log_file}", "/AUTOUPDATE=1"]


def _schtasks_launch(cmd: list[str]) -> None:
    """Fallback when the Job Object does not allow breakaway: Task Scheduler starts the installer
    (in the user's session, outside our job). /TR is limited to 261 characters – a .cmd wraps it."""
    folder = Path(cmd[0]).parent
    wrapper = folder / "run-update.cmd"
    wrapper.write_text("@echo off\r\nstart \"\" " + subprocess.list2cmdline(cmd) + "\r\n", encoding="utf-8")
    name = "HebrewMaps self-update"
    when = time.strftime("%H:%M", time.localtime(time.time() + 3600))
    base = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
            "creationflags": CREATE_NO_WINDOW, "timeout": 30}
    subprocess.run(["schtasks", "/Create", "/F", "/SC", "ONCE", "/ST", when, "/TN", name, "/TR", f'"{wrapper}"'],
                   check=True, **base)
    subprocess.run(["schtasks", "/Run", "/TN", name], check=True, **base)


def spawn(cmd: list[str]) -> None:
    """Start the installer detached, so it survives the server and the desktop window it closes."""
    if sys.platform != "win32":
        raise U.UpdateError("unsupported", "עדכון אוטומטי של התוכנה זמין רק ב-Windows.")
    flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB
    kw = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
          "close_fds": True, "cwd": str(Path(cmd[0]).parent)}
    try:
        subprocess.Popen(cmd, creationflags=flags, **kw)
        return
    except OSError as e:  # ERROR_ACCESS_DENIED: a job without JOB_OBJECT_LIMIT_BREAKAWAY_OK
        log.warning("could not start the installer outside the job (%s) – using Task Scheduler", e)
    try:
        _schtasks_launch(cmd)
    except (OSError, subprocess.SubprocessError) as e:
        raise U.UpdateError("launch_failed", "הפעלת ההתקנה נכשלה. סגרו את התוכנה והפעילו ידנית את הקובץ "
                                             f"{cmd[0]}") from e


def install(job: AppJob | None = None, force: bool = False, on_progress=None, launch: bool = True) -> dict:
    """Download, verify and start the installer of the newest version. Returns the release info."""
    ok, why = can_self_update()
    if not ok:
        raise U.UpdateError("unsupported", why or "עדכון אוטומטי אינו זמין כאן.")
    info = fetch(force=True)
    if not force and not is_newer(info["version"], __version__):
        raise U.UpdateError("up_to_date", "התוכנה כבר בגרסה העדכנית.")
    job = job or AppJob(info["version"])
    job.version = info["version"]
    path = download_installer(info, job, on_progress)
    if launch:
        job.state = "launching"
        log_file = path.with_name(f"install-{info['version']}.log")
        spawn(installer_command(path, log_file))
        log.info("installer %s started", path.name)
    job.state = "launched"
    return info


def start(force: bool = False) -> dict:
    """Background install (one at a time)."""
    global _job
    with _job_lock:
        if _job and _job.state in APP_ACTIVE:
            return _job.as_dict()
        _job = AppJob("")
        job = _job

    def run():
        try:
            install(job, force=force)
        except U.UpdateError as e:
            job.state, job.code, job.error = "error", e.code, e.message
        except Exception as e:  # noqa: BLE001
            log.exception("app update failed")
            job.state, job.code, job.error = "error", "error", "עדכון התוכנה נכשל: " + (str(e) or e.__class__.__name__)
        job.finished = datetime.now(timezone.utc).isoformat(timespec="seconds")

    threading.Thread(target=run, name="hmaps-app-update", daemon=True).start()
    return job.as_dict()


# =========================================================================== CLI
# (the HTTP endpoints are in updates.py, next to the data endpoints)

def cli(check_only: bool, force: bool = False) -> int:
    info = check_app(force=True)
    print(f"גרסת התוכנה המותקנת: {info['installed']}")
    if info["error"]:
        print(f"שגיאה: {info['error']['message']}")
        return 2
    av = info["available"] or {}
    print(f"הגרסה החדשה ביותר: {av.get('version')} ({av.get('date') or '—'}, {U._fmt_size(av.get('size'))})")
    if av.get("notes_he"):
        print(av["notes_he"].strip())
    if check_only:
        return 0
    if not info["update_available"] and not force:
        print("התוכנה כבר בגרסה העדכנית.")
        return 0
    if not info["can_install"]:
        print(info["reason"] or "")
        print(f"להורדה: {DOWNLOAD_PAGE}")
        return 1
    last = [0.0]

    def progress(got, total):
        if time.monotonic() - last[0] > 5:
            last[0] = time.monotonic()
            print(f"    {100 * got / total:.0f}%" if total else f"    {U._fmt_size(got)}", flush=True)

    try:
        print("מוריד את קובץ ההתקנה …", flush=True)
        install(force=force, on_progress=progress)
    except U.UpdateError as e:
        print(f"  ✗ {e.message}")
        return 1
    print("  ✓ ההתקנה התחילה. התוכנה תיסגר ותיפתח מחדש בגרסה החדשה.")
    return 0
