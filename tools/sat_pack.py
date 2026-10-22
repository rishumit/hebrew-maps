"""Package the aerial-photo cache for the satellite workflow (.github/workflows/satellite.yml).

    python tools/sat_pack.py checkpoint data/tiles/satellite.mbtiles
    python tools/sat_pack.py pack data/tiles/satellite.mbtiles --name HebrewMaps-Satellite-z16 --out sat-dist

``checkpoint`` folds the SQLite write-ahead log into the file (so the cache / the release hold one
complete file). ``pack`` prints the tile count per zoom, splits the file into parts below GitHub's
2 GiB asset limit (``<name>.mbtiles.001``, ``.002``…; one plain ``<name>.mbtiles`` when it fits) and
writes ``<name>-install.cmd``: it joins the parts on Windows, checks the SHA-256 and puts the result
where the installed app keeps its aerial photos (data\\tiles\\satellite.mbtiles).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

#: a little under GitHub's 2 GiB release-asset limit
PART = 1900 * 1024 * 1024
CHUNK = 8 * 1024 * 1024

INSTALL_CMD = r"""@echo off
rem Hebrew Maps - aerial photos for offline use (govmap orthophoto, zoom {minzoom}-{maxzoom}).
rem Put this file next to the downloaded part(s) and double-click it. Close Hebrew Maps first.
rem The aerial photos are (c) Survey of Israel / govmap - personal use only.
chcp 65001 >nul
cd /d "%~dp0"
setlocal
set "TILES=%LOCALAPPDATA%\Programs\HebrewMaps\data\tiles"
if not exist "%TILES%" set "TILES=%~dp0"
set "OUT=%TILES%\satellite.mbtiles"
echo תצלומי אוויר למפות בעברית ({tiles} אריחים, זום {minzoom}-{maxzoom}, {gb} GB)
echo היעד: %OUT%
echo סגרו את "מפות בעברית" לפני שממשיכים.
pause
{checks}
echo מחבר ובודק את הקובץ... (כמה דקות)
copy /b {parts} "%OUT%.tmp" >nul || goto :fail
certutil -hashfile "%OUT%.tmp" SHA256 | findstr /i /x "{sha256}" >nul || goto :badsum
if exist "%OUT%-wal" del /q "%OUT%-wal"
if exist "%OUT%-shm" del /q "%OUT%-shm"
move /y "%OUT%.tmp" "%OUT%" >nul || goto :fail
echo.
echo הותקן. פתחו את המפה ובחרו בשכבות "תצלומי אוויר" - הם יוצגו גם בלי אינטרנט.
pause
exit /b 0
:badsum
del /q "%OUT%.tmp"
echo בדיקת השלמות נכשלה - אחד הקבצים פגום או חלקי. הורידו אותו שוב.
pause
exit /b 1
:missing
echo חסר הקובץ %MISSING% - הורידו את כל החלקים לאותה תיקייה.
pause
exit /b 1
:fail
echo ההעתקה נכשלה (מקום בדיסק? המפה פתוחה?).
pause
exit /b 1
"""


def checkpoint(path: Path) -> None:
    con = sqlite3.connect(path)
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    con.execute("PRAGMA journal_mode=DELETE")
    con.close()


def stats(path: Path) -> dict:
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    by = dict(con.execute("SELECT zoom_level, COUNT(*) FROM tiles GROUP BY zoom_level ORDER BY zoom_level"))
    ok = con.execute("PRAGMA quick_check").fetchone()[0]
    con.close()
    if ok != "ok":
        raise SystemExit(f"{path}: SQLite quick_check failed: {ok}")
    return {"by_zoom": {int(z): n for z, n in by.items()}, "tiles": sum(by.values())}


def pack(src: Path, name: str, out: Path, part: int = PART) -> dict:
    checkpoint(src)
    st = stats(src)
    if not st["tiles"]:
        raise SystemExit(f"{src}: no tiles")
    out.mkdir(parents=True, exist_ok=True)
    size = src.stat().st_size
    h = hashlib.sha256()
    files: list[Path] = []
    with open(src, "rb") as f:
        if size <= part:
            files.append(out / f"{name}.mbtiles")
        else:
            files = [out / f"{name}.mbtiles.{i:03d}" for i in range(1, (size + part - 1) // part + 1)]
        for p in files:
            left = part
            with open(p, "wb") as o:
                while left and (chunk := f.read(min(CHUNK, left))):
                    o.write(chunk)
                    h.update(chunk)
                    left -= len(chunk)
    sha = h.hexdigest()
    zooms = sorted(st["by_zoom"])
    names = [p.name for p in files]
    checks = "\n".join(f'if not exist "{n}" (set "MISSING={n}" & goto :missing)' for n in names)
    cmd = INSTALL_CMD.format(minzoom=zooms[0], maxzoom=zooms[-1], tiles=f"{st['tiles']:,}", gb=f"{size / 1e9:.1f}",
                             checks=checks, parts="+".join(f'"{n}"' for n in names), sha256=sha)
    (out / f"{name}-install.cmd").write_bytes(cmd.replace("\n", "\r\n").encode("utf-8"))
    info = {"file": f"{name}.mbtiles", "size": size, "sha256": sha, "parts": names, **st}
    (out / f"{name}.json").write_text(json.dumps(info, indent=1), encoding="utf-8")
    return info


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python tools/sat_pack.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("checkpoint")
    c.add_argument("mbtiles")
    p = sub.add_parser("pack")
    p.add_argument("mbtiles")
    p.add_argument("--name", required=True)
    p.add_argument("--out", required=True)
    s = sub.add_parser("stats")
    s.add_argument("mbtiles")
    args = ap.parse_args(argv)
    path = Path(args.mbtiles)
    if args.cmd == "checkpoint":
        if path.exists():
            checkpoint(path)
        return 0
    if args.cmd == "stats":
        if not path.exists():
            print("no file yet")
            return 0
        st = stats(path)
        print(f"{st['tiles']:,} tiles · " + " · ".join(f"z{z}: {n:,}" for z, n in st["by_zoom"].items()))
        return 0
    info = pack(path, args.name, Path(args.out))
    print(f"{info['tiles']:,} tiles, {info['size'] / 1e9:.2f} GB, sha256 {info['sha256']}: {', '.join(info['parts'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
