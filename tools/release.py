"""Release tooling for the weekly data workflow (.github/workflows/weekly-data.yml).

    python tools/release.py plan     --previous prev/manifest.json        # which components to build (GITHUB_OUTPUT)
    python tools/release.py pack     --component maps --version 20261022  # data/ -> dist/hmaps-maps-20261022.zip
    python tools/release.py verify   dist/*.zip                           # layout + content checks
    python tools/release.py manifest --tag data-2026-10-22 --previous prev/manifest.json   # dist/manifest.json
    python tools/release.py prepare-db --artifacts dist --previous prev/manifest.json      # maps.db for the media job
    python tools/release.py prune    --manifest dist/manifest.json --releases releases.json  # old releases to delete
    python tools/release.py deltas   --dist dist --previous prev/manifest.json --version 20261022 --date 2026-10-22
                                     # *.files.json + hmaps-<c>-<to>-from-<from>.delta.zip (hmaps/core/delta.py)
    python tools/release.py app-manifest --installer HebrewMaps-Setup-1.2.0.exe --version 1.2.0   # installer.yml
    python tools/release.py bundle   --manifest dist/manifest.json --dist dist --dist old --date 2026-10-22 --out offline
                                     # offline update file(s) HebrewMaps-Update-<date>.zip (--list-missing first:
                                     # "<release> <file>" of every component ZIP that is not in the --dist folders)

The component catalog (ids, ZIP layout, Hebrew texts) lives in hmaps/core/components.py
and is shared with the online updater (hmaps/api/updates.py).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hmaps.core import components as catalog  # noqa: E402
from hmaps.core import db, delta  # noqa: E402

REPO = os.environ.get("GITHUB_REPOSITORY", "rishumit/hebrew-maps")
#: deltas are built from this many earlier versions of a component …
DELTA_BASES = 6
#: … and kept (also for chains) while their release is: prune keeps 6 weekly releases
DELTA_DAYS = 42
#: GitHub refuses release assets of 2 GiB and more
MAX_ASSET = 2 * 1024 ** 3 - 1
DATED = re.compile(r"^data-\d{4}-\d{2}-\d{2}(?:-\d+)?$")


def _israel_now() -> datetime:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Asia/Jerusalem"))
    except Exception:  # noqa: BLE001 – no tz database: IST is close enough for a date
        return datetime.now(timezone(timedelta(hours=2)))


def _output(**kv) -> None:
    lines = [f"{k}={v}" for k, v in kv.items()]
    print("\n".join(lines))
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")


def _load(path: str | None) -> dict | None:
    if not path or not Path(path).is_file():
        return None
    try:
        m = json.loads(Path(path).read_text(encoding="utf-8"))
        return m if isinstance(m.get("components"), dict) else None
    except (OSError, ValueError):
        return None


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


# =========================================================================== plan

def plan(event: str, inputs: dict, previous: dict | None, now: datetime | None = None) -> dict[str, bool]:
    """Which components to build.

    * manual run: the ticked components, plus every component that has never been published
      (no previous manifest, or no file for it) – so the very first run, even a manual one with
      the default inputs (terrain unticked), builds the complete data set;
    * schedule: every weekly component; terrain (the DEM never changes) only in the first
      week of the month, or when the previous release has no terrain at all.
    """
    now = now or _israel_now()
    have = {cid for cid, e in ((previous or {}).get("components") or {}).items()
            if e.get("file") and e.get("status") != "unavailable"}
    out = {}
    for comp in catalog.COMPONENTS:
        if event == "workflow_dispatch":
            v = inputs.get(comp.id)
            out[comp.id] = v is True or str(v).lower() == "true" or comp.id not in have
        else:
            out[comp.id] = comp.weekly or now.day <= 7 or comp.id not in have
    return out


def cmd_plan(args) -> int:
    inputs = json.loads(os.environ.get("INPUTS_JSON") or args.inputs or "{}") or {}
    now = _israel_now()
    previous = _load(args.previous)
    build = plan(args.event, inputs, previous, now)
    date = now.strftime("%Y-%m-%d")
    # previous=false on the first run: later jobs then skip every download of the
    # "previous-manifest" artifact instead of failing with "Artifact not found"
    _output(date=date, previous=str(previous is not None).lower(), version=now.strftime("%Y%m%d"), tag=f"data-{date}",
            week=now.strftime("%G-W%V"), month=now.strftime("%Y-%m"),
            any=str(any(build.values())).lower(), components=",".join(c for c, b in build.items() if b) or "none",
            **{c: str(b).lower() for c, b in build.items()})
    return 0


# =========================================================================== pack

def _newest(folder: Path, stem: str) -> Path | None:
    cands = [p for p in folder.glob(f"{stem}*.mbtiles") if p.is_file()]
    return max(cands, key=lambda p: p.stat().st_mtime_ns) if cands else None


def sources(component_id: str, data: Path) -> list[tuple[Path, str]]:
    """(file on disk, name in the ZIP) of a component, resolving versioned files and pointers."""
    out: list[tuple[Path, str]] = []

    def add(path: Path | None, name: str) -> None:
        if path is not None and path.is_file():
            out.append((path, name))

    if component_id == "maps":
        add(db.resolve(data / "maps.db"), "maps.db")
        add(data / "routing" / "graph.npz", "routing/graph.npz")
        add(data / "routing" / "graph.json", "routing/graph.json")
        rep = data / "reports"
        if rep.is_dir():
            for f in sorted(rep.iterdir()):
                add(f, f"reports/{f.name}")
    elif component_id == "basemap":
        add(_newest(data / "tiles", "israel"), "israel.mbtiles")
    elif component_id == "terrain":
        add(_newest(data / "tiles", "contours"), "contours.mbtiles")
        add(_newest(data / "tiles", "terrain"), "terrain.mbtiles")
    elif component_id == "transit":
        add(db.resolve(data / "transit" / "transit.npz"), "transit.npz")
        add(data / "transit" / "transit.json", "transit.json")
    elif component_id == "media":
        media = data / "media"
        if media.is_dir():
            for f in sorted(media.rglob("*")):
                if f.is_file() and f.name not in ("failed.txt", "pending.txt") and f.suffix not in (".part", ".tmp"):
                    add(f, "media/" + f.relative_to(media).as_posix())
    elif component_id == "zmanim":
        from hmaps.etl import zmanim
        add(zmanim.snapshot_path(data), "zmanim.json")
    return out


def pack(component_id: str, data: Path, dist: Path, version: str) -> Path:
    comp = catalog.get(component_id)
    files = sources(component_id, data)
    names = {n for _, n in files}
    missing = [r for r in comp.required if r not in names]
    if missing or not files:
        raise SystemExit(f"{component_id}: nothing to package – missing {', '.join(missing) or 'all files'} under {data}")
    dist.mkdir(parents=True, exist_ok=True)
    out = dist / catalog.asset_name(component_id, version)
    desc = {"component": component_id, "version": version, "schema": catalog.MANIFEST_SCHEMA,
            "built": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "commit": os.environ.get("GITHUB_SHA", ""), "files": len(files)}
    # images and tiles are already compressed: store them as-is (much faster, same size)
    stored = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".npz"}
    tmp = out.with_suffix(".zip.part")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as zf:
        zf.writestr(catalog.DESCRIPTOR, json.dumps(desc, ensure_ascii=False, indent=1))
        for path, name in files:
            zf.write(path, name, compress_type=zipfile.ZIP_STORED if path.suffix.lower() in stored else zipfile.ZIP_DEFLATED)
    os.replace(tmp, out)
    print(f"{out.name}: {len(files)} files, {out.stat().st_size / 1e6:.1f} MB")
    return out


def cmd_pack(args) -> int:
    pack(args.component, Path(args.data), Path(args.out), args.version)
    return 0


# =========================================================================== verify

def _component_of(zip_path: Path) -> str:
    m = re.match(r"^hmaps-([a-z]+)-\d{8}\.zip$", zip_path.name)
    if not m or m.group(1) not in catalog.BY_ID:
        raise SystemExit(f"{zip_path.name}: not a component asset (hmaps-<component>-<YYYYMMDD>.zip)")
    return m.group(1)


def _count(path: Path, sql: str) -> int:
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        if con.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise SystemExit(f"{path.name}: SQLite integrity check failed")
        return con.execute(sql).fetchone()[0]
    finally:
        con.close()


def verify(zip_path: Path, min_pois: int = 1) -> dict:
    """Layout (via the updater's own extractor) and content checks of one component ZIP."""
    from hmaps.api import updates

    cid = _component_of(zip_path)
    size = zip_path.stat().st_size
    if size > MAX_ASSET:
        raise SystemExit(f"{zip_path.name}: {size / 1e9:.2f} GB – larger than the 2 GiB GitHub release asset limit")
    with zipfile.ZipFile(zip_path) as zf:
        bad = zf.testzip()
        if bad:
            raise SystemExit(f"{zip_path.name}: corrupt member {bad}")
        names = [n for n in zf.namelist() if not n.endswith("/")]
    facts: dict = {"component": cid, "size": size, "files": len(names)}
    if cid == "media":
        imgs = [n for n in names if n.startswith("media/")]
        if not imgs:
            raise SystemExit(f"{zip_path.name}: no images")
        facts["images"] = len(imgs)
        return facts
    with tempfile.TemporaryDirectory(prefix="hmaps-verify-", dir=zip_path.parent) as tmp:
        try:
            st = updates.extract(cid, zip_path, Path(tmp) / cid)
        except updates.UpdateError as e:
            raise SystemExit(f"{zip_path.name}: {e.code}: {e.message}") from e
        if cid == "maps":
            facts["pois"] = _count(st / "maps.db", "SELECT count(*) FROM pois")
            facts["streets"] = _count(st / "maps.db", "SELECT count(*) FROM streets")
            if facts["pois"] < min_pois:
                raise SystemExit(f"{zip_path.name}: only {facts['pois']} POIs (expected ≥ {min_pois})")
        elif cid in ("basemap", "terrain"):
            for f in sorted(st.glob("*.mbtiles")):
                n = _count(f, "SELECT count(*) FROM tiles")
                if n == 0:
                    raise SystemExit(f"{zip_path.name}: {f.name} has no tiles")
                facts[f.stem + "_tiles"] = n
        elif cid == "transit":
            import numpy as np
            with np.load(st / "transit.npz") as z:
                if "stop_lat" not in z.files:
                    raise SystemExit(f"{zip_path.name}: transit.npz without stops")
                facts["stops"] = int(len(z["stop_lat"]))
            meta = json.loads((st / "transit.json").read_text(encoding="utf-8"))
            facts.update({k: meta.get(k) for k in ("start", "end") if k in meta})
        elif cid == "zmanim":
            snap = json.loads((st / "zmanim.json").read_text(encoding="utf-8"))
            mosads = snap.get("mosads") if isinstance(snap, dict) else None
            if not isinstance(mosads, list) or not mosads:
                raise SystemExit(f"{zip_path.name}: empty prayer-times snapshot")
            facts["synagogues"] = len(mosads)
    return facts


def cmd_verify(args) -> int:
    zips = [Path(z) for z in args.zips if Path(z).is_file() and not z.endswith(".delta.zip")]
    if not zips:
        print("no component ZIPs to verify")
        return 0 if args.allow_empty else 1
    for z in zips:
        facts = verify(z, args.min_pois)
        print(f"✓ {z.name}: " + ", ".join(f"{k}={v}" for k, v in facts.items() if k != "component"))
    return 0


# =========================================================================== manifest

def _tag_date(tag: str | None) -> datetime | None:
    m = re.match(r"^data-(\d{4}-\d{2}-\d{2})", tag or "")
    return datetime.strptime(m.group(1), "%Y-%m-%d") if m else None


def _recent(tag: str | None, date: str, days: int = DELTA_DAYS) -> bool:
    """Whether the release *tag* is still kept (prune keeps ~6 weekly releases)."""
    t, now = _tag_date(tag), datetime.strptime(date, "%Y-%m-%d")
    return t is not None and (now - t).days <= days


def _history(p: dict, date: str, keep: int = DELTA_BASES) -> list[dict]:
    """The versions before *p* (newest first) that a delta can start from: *p* itself + its history."""
    out, seen = [], set()
    for h in [p] + list(p.get("history") or []):
        v = h.get("version")
        if not v or v in seen or not h.get("file") or not _recent(h.get("release"), date):
            continue
        seen.add(v)
        out.append({k: h.get(k) for k in ("version", "date", "release", "file", "size", "sha256", "files")})
    return out[:keep]


def _delta_entries(dist: Path, cid: str, version: str, tag: str, date: str, repo: str) -> list[dict]:
    out = []
    for f in sorted(dist.glob(f"hmaps-{cid}-{version}-from-*.delta.zip")):
        d = delta.describe(f)
        if d.get("component") != cid or str(d.get("to")) != version:
            continue
        out.append({"from": str(d["from"]), "to": version, "kind": d.get("kind"), "file": f.name,
                    "size": f.stat().st_size, "sha256": sha256(f), "release": tag, "date": date,
                    "url": f"https://github.com/{repo}/releases/download/{tag}/{f.name}"})
    return out


def build_manifest(dist: Path, tag: str, version: str, date: str, previous: dict | None, repo: str = REPO) -> dict:
    comps = {}
    prev = (previous or {}).get("components") or {}
    for comp in catalog.COMPONENTS:
        zp = dist / catalog.asset_name(comp.id, version)
        base = {"name_he": comp.name_he, "description_he": comp.description_he, "min_app_version": comp.min_app_version}
        p = prev.get(comp.id) or {}
        if zp.is_file():
            files = dist / delta.files_asset_name(zp.name)
            # deltas of earlier weeks stay usable for chains while their release is kept
            carried = [d for d in p.get("deltas") or [] if _recent(d.get("release"), date)
                       and str(d.get("to")) != version]
            comps[comp.id] = {**base, "status": "full", "version": version, "date": date, "release": tag,
                              "file": zp.name, "size": zp.stat().st_size, "sha256": sha256(zp),
                              "url": f"https://github.com/{repo}/releases/download/{tag}/{zp.name}",
                              "files": files.name if files.is_file() else None,
                              "deltas": _delta_entries(dist, comp.id, version, tag, date, repo) + carried,
                              "history": _history(p, date) if p.get("file") and p.get("version") != version else []}
        elif p.get("file") and p.get("status") != "unavailable":
            comps[comp.id] = {**base, **{k: p.get(k) for k in ("version", "date", "release", "file", "size", "sha256", "url",
                                                                "files")},
                              "deltas": [d for d in p.get("deltas") or [] if _recent(d.get("release"), date)],
                              "history": [h for h in p.get("history") or [] if _recent(h.get("release"), date)],
                              "min_app_version": p.get("min_app_version") or comp.min_app_version, "status": "unchanged"}
        else:
            comps[comp.id] = {**base, "status": "unavailable", "version": None, "date": None, "file": None,
                              "size": None, "sha256": None}
    return {"schema": catalog.MANIFEST_SCHEMA, "repo": repo, "release": tag, "version": version, "date": date,
            "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "commit": os.environ.get("GITHUB_SHA", ""), "min_app_version": catalog.MIN_APP_VERSION, "components": comps}


def notes(m: dict) -> str:
    status_he = {"full": "חדש בגרסה זו", "unchanged": "ללא שינוי", "unavailable": "לא זמין"}
    rows = ["| רכיב | תאריך | גודל | מצב | קובץ | עדכון חלקי |", "|---|---|---|---|---|---|"]
    for cid, e in m["components"].items():
        size = f"{e['size'] / 1e6:.0f} MB" if e.get("size") else "—"
        new = [d for d in e.get("deltas") or [] if d.get("release") == m.get("release")]
        part = ", ".join(f"מ-{d['from']}: {d['size'] / 1e6:.1f} MB" for d in new) or "—"
        rows.append(f"| {e['name_he']} (`{cid}`) | {e.get('date') or '—'} | {size} | {status_he[e['status']]} | "
                    f"{e.get('file') or '—'} | {part} |")
    return (f"## עדכון נתונים שבועי – {m['date']}\n\n"
            "חבילות הנתונים של מפות ישראל. התוכנה מעדכנת אותן בעצמה: תפריט ← **עדכונים**, "
            "או `update-online-windows.cmd` / `python -m hmaps update`.\n\n"
            "**מחשב בלי אינטרנט?** הורידו את `HebrewMaps-Update-<תאריך>.zip` (כל החבילות בקובץ אחד), העבירו "
            "בדיסק-און-קי, ובתוכנה: עדכונים ← **עדכון מקובץ**.\n\n" + "\n".join(rows) +
            "\n\n`manifest.json` מפרט לכל רכיב גרסה, גודל ו-SHA-256. רכיבים שלא נבנו מחדש מצביעים על הקובץ "
            "מגרסה קודמת. קובצי `*.delta.zip` הם עדכונים חלקיים (רק מה שהשתנה מגרסה קודמת) – התוכנה בוחרת "
            "בהם לבד כשהם קטנים בהרבה מהחבילה המלאה.\n\nנתונים: © תורמי OpenStreetMap (ODbL), Copernicus DEM © DLR/Airbus/ESA, "
            "משרד התחבורה (GTFS), ויקיפדיה/ויקינתונים/ויקישיתוף, נדרים פלוס.\n")


def cmd_manifest(args) -> int:
    dist = Path(args.dist)
    m = build_manifest(dist, args.tag, args.version, args.date, _load(args.previous))
    out = Path(args.out or dist / "manifest.json")
    out.write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
    text = notes(m)
    if args.notes:
        Path(args.notes).write_text(text, encoding="utf-8")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(text)
    print(text)
    new = [c for c, e in m["components"].items() if e["status"] == "full"]
    _output(new=",".join(new) or "none", complete=str(all(e["status"] != "unavailable" for e in m["components"].values())).lower())
    return 0


# =========================================================================== deltas

def _gh_fetch(release: str, name: str, folder: Path) -> Path:
    import subprocess
    subprocess.run(["gh", "release", "download", release, "-R", REPO, "-p", name, "-D", str(folder), "--clobber"],
                   check=True)
    return folder / name


def build_deltas(dist: Path, previous: dict | None, version: str, date: str, fetch=_gh_fetch,
                 keep: int = DELTA_BASES, max_minutes: float = 60, log=print) -> list[str]:
    """File lists (``*.files.json``) of this run's packages + delta packages from up to *keep*
    earlier versions of every rebuilt component. Returns one summary line per component.

    Binary deltas need the old package: it is downloaded (one at a time, deleted right after);
    the image deltas only need the old file list. Skipped – and said so – when the disk is too
    full, the old release is gone, the time budget is used up or the delta is not worth it."""
    import shutil
    import time as _time

    start = _time.monotonic()
    prev = (previous or {}).get("components") or {}
    lines = []
    for comp in catalog.COMPONENTS:
        zp = dist / catalog.asset_name(comp.id, version)
        if not zp.is_file():
            continue
        index = delta.zip_index(zp)
        (dist / delta.files_asset_name(zp.name)).write_text(json.dumps(index, separators=(",", ":")), encoding="utf-8")
        if comp.id in delta.NO_DELTA:
            continue
        p = prev.get(comp.id) or {}
        bases = _history(p, date, keep) if p.get("file") and p.get("version") != version else []
        made, skipped = [], []
        for b in bases:
            if (_time.monotonic() - start) / 60 > max_minutes:
                skipped.append(f"{b['version']}: time budget used up")
                continue
            is_dir = comp.id in delta.DIR_COMPONENTS
            with tempfile.TemporaryDirectory(prefix="hmaps-base-", dir=dist) as tmp:
                try:
                    if is_dir:
                        if not b.get("files"):
                            skipped.append(f"{b['version']}: no file list published")
                            continue
                        base_index = json.loads(fetch(b["release"], b["files"], Path(tmp)).read_text(encoding="utf-8"))
                        kw = {"base_index": base_index}
                    else:
                        need = 4 * (int(b.get("size") or 0) + zp.stat().st_size)
                        if shutil.disk_usage(dist).free < need:
                            skipped.append(f"{b['version']}: not enough disk space ({need / 1e9:.1f} GB needed)")
                            continue
                        base_zip = fetch(b["release"], b["file"], Path(tmp))
                        if b.get("sha256") and sha256(base_zip) != b["sha256"]:
                            skipped.append(f"{b['version']}: old package does not match its SHA-256")
                            continue
                        kw = {"base_zip": base_zip}
                    out = delta.build(comp.id, zp, dist, b["version"], version, log=log, **kw)
                except Exception as e:  # noqa: BLE001 – a delta is an optimisation only
                    skipped.append(f"{b['version']}: {e.__class__.__name__}: {e}")
                    continue
            if out.stat().st_size >= delta.RATIO * zp.stat().st_size:
                skipped.append(f"{b['version']}: {out.stat().st_size / 1e6:.1f} MB – not worth it")
                out.unlink()
                continue
            made.append(f"{b['version']} → {out.stat().st_size / 1e6:.1f} MB")
        if bases or made:
            line = f"{comp.id} ({zp.stat().st_size / 1e6:.0f} MB): " + (", ".join(made) or "no deltas")
            if skipped:
                line += "; skipped " + "; ".join(skipped)
            lines.append(line)
            log(line)
    return lines


def cmd_deltas(args) -> int:
    lines = build_deltas(Path(args.dist), _load(args.previous), args.version, args.date,
                         keep=args.keep, max_minutes=args.max_minutes)
    for line in lines:
        if "skipped" in line:
            print(f"::notice::deltas {line}")
    if os.environ.get("GITHUB_STEP_SUMMARY") and lines:
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write("### Delta packages\n\n" + "\n".join(f"- {line}" for line in lines) + "\n\n")
    return 0


# =========================================================================== prepare-db (media job)

def cmd_prepare_db(args) -> int:
    """Put a maps.db into data/: this run's maps artifact, else the previous release's asset."""
    from hmaps.api import updates

    data = Path(args.data)
    zips = sorted(Path(args.artifacts).glob("hmaps-maps-*.zip")) if args.artifacts and Path(args.artifacts).is_dir() else []
    if zips:
        updates.install_zip("maps", zips[-1], data)
        print(f"maps.db from this run: {zips[-1].name}")
        return 0
    prev = _load(args.previous)
    if not prev:
        raise SystemExit("no maps.db: the maps job did not run and there is no previous release")
    prev["_source"] = args.previous
    updates.install("maps", data, manifest=prev, force=True)
    print(f"maps.db from {prev['components']['maps'].get('release')}")
    return 0


# =========================================================================== offline update file

#: a little under GitHub's 2 GiB asset limit (ZIP headers, manifest, README)
BUNDLE_LIMIT = MAX_ASSET - 32 * 1024 ** 2
BUNDLE_PREFIX = "HebrewMaps-Update"
BUNDLE_README = """\ufeffמפות ישראל – קובץ עדכון לנתונים, למחשב בלי אינטרנט
גרסת נתונים: {date}{part}

איך מעדכנים:
1. מעתיקים את הקובץ הזה (כמו שהוא, בלי לפתוח אותו) לדיסק-און-קי ומחברים למחשב שבו מותקנת התוכנה.
2. פותחים את "מפות ישראל" ← תפריט (☰) ← "עדכונים" ← "עדכון מקובץ…" ובוחרים את הקובץ.
3. התוכנה בודקת כל חבילה (SHA-256) ומתקינה רק מה שחדש יותר ממה שכבר במחשב. המפה ממשיכה לעבוד בינתיים.

התוכנה עצמה עוד לא מותקנת? מעתיקים גם את HebrewMaps-Setup.exe מדף ההורדה, מתקינים, ואז מעדכנים מהקובץ.
דף ההורדה: https://rishumit.github.io/hebrew-maps/download.html#offline

בחבילה:
{items}
"""


def _find(dirs: list[Path], name: str) -> Path | None:
    for d in dirs:
        p = d / name
        if p.is_file():
            return p
    return None


def bundle_missing(manifest: dict, dirs: list[Path]) -> list[tuple[str, str]]:
    """(release, file) of every component ZIP of the manifest that is not in *dirs* yet."""
    out = []
    for e in manifest["components"].values():
        if e.get("file") and not _find(dirs, e["file"]):
            out.append((e.get("release") or manifest.get("release"), e["file"]))
    return out


def bundle_names(date: str, parts: int) -> list[str]:
    if parts == 1:
        return [f"{BUNDLE_PREFIX}-{date}.zip"]
    return [f"{BUNDLE_PREFIX}-{date}-part{i}.zip" for i in range(1, parts + 1)]


def stable_name(name: str) -> str:
    """Remove the date from a full, split, or component-specific offline package name."""
    return re.sub(rf"^{BUNDLE_PREFIX}-\d{{4}}-\d{{2}}-\d{{2}}", BUNDLE_PREFIX, name)


def bundle(manifest: dict, dirs: list[Path], out: Path, date: str, limit: int = BUNDLE_LIMIT,
           components: list[str] | None = None) -> list[Path]:
    """Every component ZIP of *manifest* in one (or, above *limit*, a few) offline update file(s).

    Each file is self-contained: a manifest.json listing only the components inside it, the component
    ZIPs (stored – they are compressed already) and a Hebrew README.txt."""
    selected = set(components or [])
    unknown = selected - set(catalog.IDS)
    if unknown:
        raise SystemExit("bundle: unknown component(s): " + ", ".join(sorted(unknown)))
    entries = [(cid, e) for cid, e in manifest["components"].items()
               if e.get("file") and (not selected or cid in selected)]
    if not entries:
        raise SystemExit("bundle: the manifest lists no component files")
    missing = [f for _, f in bundle_missing(manifest, dirs)]
    if missing:
        raise SystemExit("bundle: missing component files: " + ", ".join(missing))
    paths = {cid: _find(dirs, e["file"]) for cid, e in entries}
    sizes = {cid: paths[cid].stat().st_size for cid, _ in entries}
    # first-fit decreasing; the order inside a file follows the catalog
    bins: list[list[str]] = []
    for cid in sorted(sizes, key=lambda c: -sizes[c]):
        if sizes[cid] > limit:
            raise SystemExit(f"bundle: {paths[cid].name} alone is larger than an offline file may be")
        for b in bins:
            if sum(sizes[c] for c in b) + sizes[cid] <= limit:
                b.append(cid)
                break
        else:
            bins.append([cid])
    order = {cid: i for i, cid in enumerate(catalog.IDS)}
    if selected:
        bins = [[cid] for cid in selected if cid in sizes]
    bins = sorted((sorted(b, key=order.get) for b in bins), key=lambda b: order[b[0]])
    out.mkdir(parents=True, exist_ok=True)
    result = []
    names = ([f"{BUNDLE_PREFIX}-{date}-{b[0]}.zip" for b in bins]
             if selected else bundle_names(date, len(bins)))
    for i, (b, name) in enumerate(zip(bins, names), 1):
        m = {k: v for k, v in manifest.items() if k != "components" and not k.startswith("_")}
        m["components"] = {cid: {k: v for k, v in manifest["components"][cid].items() if k != "url"} for cid in b}
        m["bundle"] = {"part": 1 if selected else i, "parts": 1 if selected else len(bins), "date": date}
        items = "\n".join(f"  • {manifest['components'][cid].get('name_he') or cid} – "
                          f"{manifest['components'][cid].get('date') or ''} ({sizes[cid] / 1e6:.0f} MB)" for cid in b)
        part = f" · קובץ {i} מתוך {len(bins)} (צריך את כולם)" if len(bins) > 1 and not selected else ""
        path = out / name
        tmp = path.with_name(path.name + ".tmp")
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED, allowZip64=True) as zf:
            zf.writestr("manifest.json", json.dumps(m, ensure_ascii=False, indent=1))
            zf.writestr("README.txt", BUNDLE_README.format(date=date, part=part, items=items))
            for cid in b:
                zf.write(paths[cid], manifest["components"][cid]["file"])
        os.replace(tmp, path)
        print(f"{name}: {', '.join(b)} – {path.stat().st_size / 1e6:.0f} MB")
        result.append(path)
    return result


def cmd_bundle(args) -> int:
    m = _load(args.manifest)
    if not m:
        raise SystemExit("bundle: no manifest")
    dirs = [Path(d) for d in (args.dist or ["dist"])]
    if args.list_missing:
        for rel, f in bundle_missing(m, dirs):
            print(rel, f)
        return 0
    if not args.date or not args.out:
        raise SystemExit("bundle: --date and --out are required")
    selected = [c.strip() for c in args.components.split(",") if c.strip()] if args.components else None
    files = bundle(m, dirs, Path(args.out), args.date, components=selected)
    if args.stable:
        st = Path(args.stable)
        st.mkdir(parents=True, exist_ok=True)
        for f in files:
            target = st / stable_name(f.name)
            target.unlink(missing_ok=True)
            try:
                os.link(f, target)
            except OSError:
                import shutil
                shutil.copyfile(f, target)
    _output(bundles=" ".join(f.name for f in files), parts=len(files))
    return 0


# =========================================================================== prune

def prune(manifest: dict, tags: list[str], keep: int) -> list[str]:
    """Dated releases that are neither among the newest *keep* nor referenced by the manifest."""
    referenced = {e.get("release") for e in manifest["components"].values()} | {manifest.get("release")}
    dated = sorted((t for t in tags if DATED.match(t)), reverse=True)
    return [t for t in dated[keep:] if t not in referenced]


def cmd_prune(args) -> int:
    m = _load(args.manifest)
    if not m:
        raise SystemExit("no manifest")
    rel = json.loads(Path(args.releases).read_text(encoding="utf-8"))
    tags = [r["tagName"] if isinstance(r, dict) else str(r) for r in rel]
    for t in prune(m, tags, args.keep):
        print(t)
    return 0


# =========================================================================== app manifest (installer.yml)

DEFAULT_APP_NOTES = "גרסה {version} של מפות ישראל. העדכון שומר את כל הנתונים שבמחשב."


def app_manifest(installer: Path, version: str, tag: str | None = None, notes: str | None = None,
                 min_data_version: str | None = None, repo: str = REPO) -> dict:
    """app-manifest.json of an app release (read by hmaps/api/appupdate.py)."""
    version = version.lstrip("vV")
    tag = tag or f"v{version}"
    m = {"schema": 1, "version": version, "tag": tag, "date": _israel_now().strftime("%Y-%m-%d"),
         "file": installer.name, "size": installer.stat().st_size, "sha256": sha256(installer),
         "url": f"https://github.com/{repo}/releases/download/{tag}/{installer.name}",
         "notes_he": (notes or "").strip() or DEFAULT_APP_NOTES.format(version=version),
         "commit": os.environ.get("GITHUB_SHA", "")}
    if min_data_version:
        m["min_data_version"] = min_data_version
    from hmaps.api import appupdate
    appupdate.parse_manifest(m)  # the client must accept what we publish
    return m


def cmd_app_manifest(args) -> int:
    notes = Path(args.notes).read_text(encoding="utf-8-sig") if args.notes and Path(args.notes).is_file() else None
    if notes and args.notes.endswith(".md"):
        # docs/release-notes/vX.md (also rendered on the site): "# title", "date: …", "- item"
        lines = []
        for raw in notes.splitlines():
            t = raw.strip()
            if not t or t.lower().startswith("date:"):
                continue
            lines.append(t.lstrip("#").strip() if t.startswith("#") else ("• " + t[2:].strip() if t[:2] in ("- ", "* ") else t))
        notes = "\n".join(lines)
    m = app_manifest(Path(args.installer), args.version, args.tag, notes, args.min_data_version)
    Path(args.out).write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(m, ensure_ascii=False, indent=1))
    return 0


def main(argv: list[str] | None = None) -> int:
    # Windows runners default to cp1252: Hebrew in our output would crash print()
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="python tools/release.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--event", default=os.environ.get("GITHUB_EVENT_NAME", "schedule"))
    p.add_argument("--inputs", help="workflow_dispatch inputs as JSON (or env INPUTS_JSON)")
    p.add_argument("--previous")
    p = sub.add_parser("pack")
    p.add_argument("--component", required=True, choices=list(catalog.IDS))
    p.add_argument("--data", default=os.environ.get("HMAPS_DATA", "data"))
    p.add_argument("--out", default="dist")
    p.add_argument("--version", required=True, help="YYYYMMDD")
    p = sub.add_parser("verify")
    p.add_argument("zips", nargs="*")
    p.add_argument("--min-pois", type=int, default=1)
    p.add_argument("--allow-empty", action="store_true")
    p = sub.add_parser("manifest")
    p.add_argument("--dist", default="dist")
    p.add_argument("--tag", required=True)
    p.add_argument("--version", required=True)
    p.add_argument("--date", required=True)
    p.add_argument("--previous")
    p.add_argument("--out")
    p.add_argument("--notes")
    p = sub.add_parser("prepare-db")
    p.add_argument("--data", default=os.environ.get("HMAPS_DATA", "data"))
    p.add_argument("--artifacts")
    p.add_argument("--previous")
    p = sub.add_parser("prune")
    p.add_argument("--manifest", required=True)
    p.add_argument("--releases", required=True, help="JSON from: gh release list --json tagName")
    p.add_argument("--keep", type=int, default=6)
    p = sub.add_parser("deltas")
    p.add_argument("--dist", default="dist")
    p.add_argument("--previous")
    p.add_argument("--version", required=True)
    p.add_argument("--date", required=True)
    p.add_argument("--keep", type=int, default=DELTA_BASES)
    p.add_argument("--max-minutes", type=float, default=60)
    p = sub.add_parser("app-manifest")
    p.add_argument("--installer", required=True, help="HebrewMaps-Setup-<version>.exe")
    p.add_argument("--version", required=True)
    p.add_argument("--tag")
    p.add_argument("--notes", help="file with the Hebrew release notes (Markdown/text)")
    p.add_argument("--min-data-version")
    p.add_argument("--out", default="app-manifest.json")
    p = sub.add_parser("bundle")
    p.add_argument("--manifest", required=True)
    p.add_argument("--dist", action="append", help="folder with component ZIPs (repeatable)")
    p.add_argument("--date", help="YYYY-MM-DD")
    p.add_argument("--out", help="folder for the offline update file(s)")
    p.add_argument("--components", help="comma-separated component ids; emit one offline ZIP per component")
    p.add_argument("--stable", help="also link them here under the stable names of the data-latest release")
    p.add_argument("--list-missing", action="store_true", help="only print '<release> <file>' of absent ZIPs")
    args = ap.parse_args(argv)
    return {"plan": cmd_plan, "pack": cmd_pack, "verify": cmd_verify, "manifest": cmd_manifest,
            "prepare-db": cmd_prepare_db, "prune": cmd_prune, "bundle": cmd_bundle, "deltas": cmd_deltas,
            "app-manifest": cmd_app_manifest}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
