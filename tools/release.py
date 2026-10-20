"""Release tooling for the weekly data workflow (.github/workflows/weekly-data.yml).

    python tools/release.py plan     --previous prev/manifest.json        # which components to build (GITHUB_OUTPUT)
    python tools/release.py pack     --component maps --version 20261022  # data/ -> dist/hmaps-maps-20261022.zip
    python tools/release.py verify   dist/*.zip                           # layout + content checks
    python tools/release.py manifest --tag data-2026-10-22 --previous prev/manifest.json   # dist/manifest.json
    python tools/release.py prepare-db --artifacts dist --previous prev/manifest.json      # maps.db for the media job
    python tools/release.py prune    --manifest dist/manifest.json --releases releases.json  # old releases to delete

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
from hmaps.core import db  # noqa: E402

REPO = os.environ.get("GITHUB_REPOSITORY", "rishumit/hebrew-maps")
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

    * manual run: exactly the ticked components;
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
            out[comp.id] = v is True or str(v).lower() == "true"
        else:
            out[comp.id] = comp.weekly or now.day <= 7 or comp.id not in have
    return out


def cmd_plan(args) -> int:
    inputs = json.loads(os.environ.get("INPUTS_JSON") or args.inputs or "{}") or {}
    now = _israel_now()
    build = plan(args.event, inputs, _load(args.previous), now)
    date = now.strftime("%Y-%m-%d")
    _output(date=date, version=now.strftime("%Y%m%d"), tag=f"data-{date}",
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
    zips = [Path(z) for z in args.zips if Path(z).is_file()]
    if not zips:
        print("no component ZIPs to verify")
        return 0 if args.allow_empty else 1
    for z in zips:
        facts = verify(z, args.min_pois)
        print(f"✓ {z.name}: " + ", ".join(f"{k}={v}" for k, v in facts.items() if k != "component"))
    return 0


# =========================================================================== manifest

def build_manifest(dist: Path, tag: str, version: str, date: str, previous: dict | None, repo: str = REPO) -> dict:
    comps = {}
    prev = (previous or {}).get("components") or {}
    for comp in catalog.COMPONENTS:
        zp = dist / catalog.asset_name(comp.id, version)
        base = {"name_he": comp.name_he, "description_he": comp.description_he, "min_app_version": comp.min_app_version}
        if zp.is_file():
            comps[comp.id] = {**base, "status": "full", "version": version, "date": date, "release": tag,
                              "file": zp.name, "size": zp.stat().st_size, "sha256": sha256(zp),
                              "url": f"https://github.com/{repo}/releases/download/{tag}/{zp.name}"}
        elif prev.get(comp.id, {}).get("file") and prev[comp.id].get("status") != "unavailable":
            p = prev[comp.id]
            comps[comp.id] = {**base, **{k: p.get(k) for k in ("version", "date", "release", "file", "size", "sha256", "url")},
                              "min_app_version": p.get("min_app_version") or comp.min_app_version, "status": "unchanged"}
        else:
            comps[comp.id] = {**base, "status": "unavailable", "version": None, "date": None, "file": None,
                              "size": None, "sha256": None}
    return {"schema": catalog.MANIFEST_SCHEMA, "repo": repo, "release": tag, "version": version, "date": date,
            "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "commit": os.environ.get("GITHUB_SHA", ""), "min_app_version": catalog.MIN_APP_VERSION, "components": comps}


def notes(m: dict) -> str:
    status_he = {"full": "חדש בגרסה זו", "unchanged": "ללא שינוי", "unavailable": "לא זמין"}
    rows = ["| רכיב | תאריך | גודל | מצב | קובץ |", "|---|---|---|---|---|"]
    for cid, e in m["components"].items():
        size = f"{e['size'] / 1e6:.0f} MB" if e.get("size") else "—"
        rows.append(f"| {e['name_he']} (`{cid}`) | {e.get('date') or '—'} | {size} | {status_he[e['status']]} | "
                    f"{e.get('file') or '—'} |")
    return (f"## עדכון נתונים שבועי – {m['date']}\n\n"
            "חבילות הנתונים של מפות בעברית. התוכנה מעדכנת אותן בעצמה: תפריט ← **עדכונים**, "
            "או `update-online-windows.cmd` / `python -m hmaps update`.\n\n" + "\n".join(rows) +
            "\n\n`manifest.json` מפרט לכל רכיב גרסה, גודל ו-SHA-256. רכיבים שלא נבנו מחדש מצביעים על הקובץ "
            "מגרסה קודמת.\n\nנתונים: © תורמי OpenStreetMap (ODbL), Copernicus DEM © DLR/Airbus/ESA, "
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


def main(argv: list[str] | None = None) -> int:
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
    args = ap.parse_args(argv)
    return {"plan": cmd_plan, "pack": cmd_pack, "verify": cmd_verify, "manifest": cmd_manifest,
            "prepare-db": cmd_prepare_db, "prune": cmd_prune}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
