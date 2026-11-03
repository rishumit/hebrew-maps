"""The user's own saved things – favourites, saved routes and saved searches – and the data folder.

Everything stays on this computer (``<data>/user/*.json``): no account, no server elsewhere.
Only the local machine may read or write it (like the updater, see ``HMAPS_UPDATES``).

    GET  /api/user/{kind}            kind = favorites | routes | searches  → {"items": [...]}
    PUT  /api/user/{kind}            {"items": [...]}  (the whole list; header X-HMaps-User: 1)
    GET  /api/settings/data-dir      current / default / configured folder, free space
    POST /api/settings/data-dir      {"path": "...", "copy": true}  choose another folder (after a restart)
    POST /api/settings/pick-folder   Windows folder picker on this computer → {"path"}
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from ..core import userconfig
from .settings import settings

log = logging.getLogger(__name__)

KINDS = ("favorites", "routes", "searches")
MAX_ITEMS = 1000
MAX_BYTES = 2_000_000
_LOCK = threading.Lock()


def user_dir(data: Path | None = None) -> Path:
    return (data or settings.data_dir) / "user"


def load(kind: str, data: Path | None = None) -> list:
    f = user_dir(data) / f"{kind}.json"
    try:
        items = json.loads(f.read_text(encoding="utf-8")).get("items", [])
        return items if isinstance(items, list) else []
    except (OSError, ValueError, AttributeError):
        return []


def save(kind: str, items: list, data: Path | None = None) -> None:
    d = user_dir(data)
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{kind}.json"
    tmp = f.with_suffix(".tmp")
    with _LOCK:
        tmp.write_text(json.dumps({"version": 1, "saved": time.strftime("%Y-%m-%dT%H:%M:%S"), "items": items},
                                  ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, f)


def _valid(items) -> bool:
    return isinstance(items, list) and len(items) <= MAX_ITEMS and all(isinstance(i, dict) for i in items)


# ---------------------------------------------------------------------------- data folder

def _free(path: Path) -> int | None:
    p = path
    while not p.exists() and p.parent != p:
        p = p.parent
    try:
        return shutil.disk_usage(p).free
    except OSError:
        return None


def _size(path: Path, limit_s: float = 2.0) -> int | None:
    """Total size of the folder (None when it takes too long to count)."""
    t0 = time.monotonic()
    total = 0
    try:
        for root, _dirs, files in os.walk(path):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
            if time.monotonic() - t0 > limit_s:
                return None
    except OSError:
        return None
    return total


def data_dir_info() -> dict:
    cfg = userconfig.load()
    cur = settings.data_dir
    return {
        "current": str(cur),
        "default": str(userconfig.default_data_dir()),
        "configured": cfg.get("data_dir"),
        "pending": bool(cfg.get("data_dir")) and Path(cfg["data_dir"]).resolve() != cur.resolve(),
        "env_override": bool(os.environ.get("HMAPS_DATA_FROM_ENV")
                             or (not getattr(sys, "frozen", False) and os.environ.get("HMAPS_DATA"))),
        "free": _free(cur),
        "size": _size(cur),
        "copy_job": dict(COPY_JOB) if COPY_JOB else None,
        "can_pick": sys.platform == "win32",
    }


COPY_JOB: dict = {}


def _copy_tree(src: Path, dst: Path) -> None:
    """Copy the data folder (big files, so in a thread with progress); existing files are kept."""
    files = [p for p in src.rglob("*") if p.is_file() and "tmp" not in p.parts and p.suffix not in (".part", ".tmp")]
    total = sum(p.stat().st_size for p in files) or 1
    done = 0
    COPY_JOB.update(state="copying", total=total, done=0, error=None)
    try:
        for p in files:
            rel = p.relative_to(src)
            out = dst / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            if not (out.exists() and out.stat().st_size == p.stat().st_size):
                shutil.copy2(p, out)
            done += p.stat().st_size
            COPY_JOB["done"] = done
        COPY_JOB["state"] = "done"
    except OSError as e:
        COPY_JOB.update(state="error", error=str(e))
        log.warning("copying the data folder failed: %s", e)


def check_folder(path: str) -> Path:
    p = Path(os.path.expandvars(os.path.expanduser(path.strip().strip('"'))))
    if not p.is_absolute():
        raise ValueError("יש לבחור נתיב מלא של תיקייה (למשל D:\\מפות ישראל)")
    try:
        p.mkdir(parents=True, exist_ok=True)
        probe = p / ".hmaps-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as e:
        raise ValueError(f"אי אפשר לכתוב לתיקייה הזו ({e.strerror or e})") from e
    return p


def pick_folder(initial: str | None = None) -> str | None:
    """Native folder picker (Windows): a small PowerShell dialog on top of the map window."""
    if sys.platform != "win32":
        return None
    init = (initial or "").replace("'", "''")
    ps = ("Add-Type -AssemblyName System.Windows.Forms;"
          "$o=New-Object System.Windows.Forms.Form;$o.TopMost=$true;"
          "$d=New-Object System.Windows.Forms.FolderBrowserDialog;"
          "$d.Description='בחירת תיקייה לנתוני המפה';$d.ShowNewFolderButton=$true;"
          f"$d.SelectedPath='{init}';"
          "if($d.ShowDialog($o) -eq 'OK'){[Console]::OutputEncoding=[Text.Encoding]::UTF8;$d.SelectedPath}")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", ps], capture_output=True, timeout=600,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return None
    text = out.stdout.decode("utf-8", "replace").strip()
    return text or None


# ---------------------------------------------------------------------------- routes

def _routes():
    from starlette.concurrency import run_in_threadpool
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    def err(status: int, code: str, message: str) -> JSONResponse:
        return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)

    def _allowed(req: Request) -> bool:
        mode = os.environ.get("HMAPS_UPDATES", "local")
        if mode == "off":
            return False
        host = req.client.host if req.client else ""
        return mode == "any" or host in ("127.0.0.1", "::1", "localhost", "testclient")

    def _write_ok(req: Request) -> JSONResponse | None:
        if not _allowed(req):
            return err(403, "forbidden", "אפשר לשנות את ההגדרות רק מהמחשב שבו התוכנה מותקנת.")
        if req.headers.get("x-hmaps-user") != "1":  # blocks cross-site form posts (no CORS preflight)
            return err(400, "missing_header", "בקשה לא תקינה.")
        return None

    async def user_get(req: Request):
        kind = req.path_params["kind"]
        if kind not in KINDS:
            return err(404, "not_found", "סוג לא מוכר")
        if not _allowed(req):
            return err(403, "forbidden", "הנתונים האישיים זמינים רק במחשב שבו התוכנה מותקנת.")
        return JSONResponse({"items": await run_in_threadpool(load, kind)}, headers={"Cache-Control": "no-store"})

    async def user_put(req: Request):
        kind = req.path_params["kind"]
        if kind not in KINDS:
            return err(404, "not_found", "סוג לא מוכר")
        bad = _write_ok(req)
        if bad:
            return bad
        raw = await req.body()
        if len(raw) > MAX_BYTES:
            return err(413, "too_large", "הרשימה גדולה מדי")
        try:
            items = json.loads(raw).get("items")
        except (ValueError, AttributeError):
            return err(400, "bad_json", "בקשה לא תקינה")
        if not _valid(items):
            return err(400, "bad_items", "רשימה לא תקינה")
        try:
            await run_in_threadpool(save, kind, items)
        except OSError as e:
            return err(500, "write_failed", f"השמירה נכשלה: {e.strerror or e}")
        return JSONResponse({"ok": True, "count": len(items)})

    async def datadir_get(req: Request):
        if not _allowed(req):
            return err(403, "forbidden", "זמין רק במחשב שבו התוכנה מותקנת.")
        return JSONResponse(await run_in_threadpool(data_dir_info), headers={"Cache-Control": "no-store"})

    async def datadir_set(req: Request):
        bad = _write_ok(req)
        if bad:
            return bad
        try:
            body = await req.json()
        except ValueError:
            return err(400, "bad_json", "בקשה לא תקינה")
        path = (body or {}).get("path")
        reset = bool((body or {}).get("reset"))
        if reset:
            await run_in_threadpool(userconfig.set_data_dir, None)
            return JSONResponse({"ok": True, "restart": True, **await run_in_threadpool(data_dir_info)})
        if not isinstance(path, str) or not path.strip():
            return err(400, "bad_path", "לא נבחרה תיקייה")
        try:
            target = await run_in_threadpool(check_folder, path)
        except ValueError as e:
            return err(400, "bad_path", str(e))
        cur = settings.data_dir.resolve()
        if target.resolve() == cur:
            await run_in_threadpool(userconfig.set_data_dir, str(target))
            return JSONResponse({"ok": True, "restart": False, **await run_in_threadpool(data_dir_info)})
        if str(target.resolve()).startswith(str(cur) + os.sep):
            return err(400, "bad_path", "אי אפשר לבחור תיקייה שנמצאת בתוך תיקיית הנתונים הנוכחית")
        if (body or {}).get("copy") and cur.exists():
            if COPY_JOB.get("state") == "copying":
                return err(409, "busy", "העתקה כבר מתבצעת")
            need = _size(cur, 10.0) or 0
            free = _free(target)
            if free is not None and need > free:
                return err(400, "no_space", "אין מספיק מקום פנוי בכונן שנבחר")

            def run():
                _copy_tree(cur, target)
                if COPY_JOB.get("state") == "done":
                    userconfig.set_data_dir(str(target))

            threading.Thread(target=run, name="copy-data", daemon=True).start()
            return JSONResponse({"ok": True, "copying": True, "restart": True, **await run_in_threadpool(data_dir_info)})
        await run_in_threadpool(userconfig.set_data_dir, str(target))
        return JSONResponse({"ok": True, "restart": True, **await run_in_threadpool(data_dir_info)})

    async def pick(req: Request):
        bad = _write_ok(req)
        if bad:
            return bad
        if sys.platform != "win32":
            return err(501, "not_supported", "בחירת תיקייה בחלון זמינה רק ב-Windows – הקלידו את הנתיב.")
        path = await run_in_threadpool(pick_folder, str(settings.data_dir))
        return JSONResponse({"path": path})

    return [
        Route("/api/user/{kind}", user_get, methods=["GET"]),
        Route("/api/user/{kind}", user_put, methods=["PUT"]),
        Route("/api/settings/data-dir", datadir_get, methods=["GET"]),
        Route("/api/settings/data-dir", datadir_set, methods=["POST"]),
        Route("/api/settings/pick-folder", pick, methods=["POST"]),
    ]


ROUTES = _routes()
