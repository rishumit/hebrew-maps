"""Build the full basemap without Docker: portable Java + Planetiler.

    python tools/basemap.py [--memory 3g]

Uses ``java`` from PATH when it is version 21+, otherwise downloads a portable
Eclipse Temurin JRE 21 into tools-bin/ (no installation or admin rights needed).
The result is published as data/tiles/israel-<timestamp>.mbtiles; the running
server switches to it automatically.
"""

from __future__ import annotations

import argparse
import io
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "tools-bin"
# absolute: Planetiler runs with cwd=DATA, so a relative HMAPS_DATA (as in CI: "data")
# would otherwise turn every path below into data/data/... and the build "fails" silently
DATA = Path(os.environ.get("HMAPS_DATA", ROOT / "data")).resolve()
PLANETILER_URL = "https://github.com/onthegomap/planetiler/releases/latest/download/planetiler.jar"
UA = {"User-Agent": "HebrewMaps-setup/1.0"}


def _download(url: str, timeout: int = 600) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read()


def java_version(java: str) -> int:
    try:
        out = subprocess.run([java, "-version"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return 0
    m = re.search(r'version "(\d+)', out.stderr + out.stdout)
    return int(m.group(1)) if m else 0


def find_java() -> str:
    exe = "java.exe" if os.name == "nt" else "java"
    for cand in sorted(BIN.glob(f"jre*/**/bin/{exe}")):
        if java_version(str(cand)) >= 21:
            return str(cand)
    sys_java = shutil.which("java")
    if sys_java and java_version(sys_java) >= 21:
        return sys_java
    osname = {"Windows": "windows", "Darwin": "mac", "Linux": "linux"}[platform.system()]
    arch = "aarch64" if platform.machine().lower() in ("arm64", "aarch64") else "x64"
    url = f"https://api.adoptium.net/v3/binary/latest/21/ga/{osname}/{arch}/jre/hotspot/normal/eclipse?project=jdk"
    print("  downloading a portable Java 21 runtime (~45 MB)…")
    data = _download(url)
    dest = BIN / "jre"
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    if data[:2] == b"PK":
        zipfile.ZipFile(io.BytesIO(data)).extractall(dest)
    else:
        tarfile.open(fileobj=io.BytesIO(data)).extractall(dest)
    for cand in dest.glob(f"**/bin/{exe}"):
        if os.name != "nt":
            cand.chmod(0o755)
        return str(cand)
    raise RuntimeError("Java download did not contain a java executable")


def planetiler() -> Path:
    jar = BIN / "planetiler.jar"
    if not jar.exists() or jar.stat().st_size < 1_000_000:
        print("  downloading Planetiler (~80 MB)…")
        BIN.mkdir(parents=True, exist_ok=True)
        tmp = jar.with_suffix(".part")
        tmp.write_bytes(_download(PLANETILER_URL))
        os.replace(tmp, jar)
    return jar


# Planetiler options of type java.time.Duration: a bare number ("--http_timeout=120") aborts the
# build with "Text cannot be parsed to a Duration" – they need a unit ("120s", "2m", "PT2M").
DURATION_ARGS = {"http_timeout", "http_retry_wait", "log_interval", "loginterval"}


def fix_durations(args: list[str]) -> list[str]:
    """Append "s" to bare-number values of Planetiler's duration options."""
    out = []
    for a in args:
        m = re.fullmatch(r"(--?)([A-Za-z_]+)=(\d+(?:\.\d+)?)", a)
        if m and m.group(2).lower() in DURATION_ARGS:
            a = f"{m.group(1)}{m.group(2)}={m.group(3)}s"
        out.append(a)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--memory", default=os.environ.get("PLANETILER_MEMORY", "3g"))
    args = ap.parse_args()
    try:
        java = find_java()
        jar = planetiler()
    except Exception as e:  # noqa: BLE001
        print(f"Basemap skipped – could not get Java/Planetiler: {e}\nThe map keeps working with the simple basemap.")
        return 2
    build_dir = DATA / "tiles" / "build"
    build_dir.mkdir(parents=True, exist_ok=True)
    out = build_dir / "israel.mbtiles"
    jvm = [f"-Xmx{args.memory}", "-Djava.net.useSystemProxies=true"]
    if os.name == "nt":
        # trust the Windows certificate store: needed behind filtering/inspecting proxies
        # (e.g. NetFree, Rimon, corporate firewalls) whose root certificate Windows knows but Java does not
        jvm.append("-Djavax.net.ssl.trustStoreType=Windows-ROOT")
    # HMAPS_PBF_PATH lets CI pre-download the PBF with retry logic so that
    # Planetiler never needs to call Geofabrik itself (avoids TimeoutException).
    pbf_env = os.environ.get("HMAPS_PBF_PATH", "")
    pbf = Path(pbf_env) if pbf_env else DATA / "sources" / "israel-and-palestine.osm.pbf"

    # PLANETILER_EXTRA_ARGS passes additional flags, e.g. "--http_timeout=120s"
    extra_args = fix_durations(os.environ.get("PLANETILER_EXTRA_ARGS", "").split())

    cmd = [java, *jvm, "-jar", str(jar), "--download", "--area=israel-and-palestine",
           f"--output={out}", "--force", "--languages=he,en,ar", "--maxzoom=14",
           f"--download_dir={DATA / 'sources'}", f"--tmpdir={DATA / 'tmp'}",
           *extra_args]
    if pbf.exists():
        # Pass the pre-downloaded PBF so Planetiler skips its own OSM download
        cmd.append(f"--osm_path={pbf}")
    print("  building the basemap (15-40 minutes; downloads ~1 GB of coastline data the first time)…")
    t0 = time.time()
    rc = subprocess.run(cmd, cwd=DATA).returncode
    if rc != 0 or not out.exists():
        print(f"Basemap build failed (exit code {rc}). The map keeps working with the simple basemap.")
        return 1
    final = DATA / "tiles" / f"israel-{time.strftime('%Y%m%d-%H%M%S')}.mbtiles"
    os.replace(out, final)
    for old in sorted((DATA / "tiles").glob("israel*.mbtiles"), key=lambda p: p.stat().st_mtime)[:-2]:
        if old != final:
            try:
                old.unlink()
            except OSError:
                pass
    print(f"Basemap ready: {final.name} ({final.stat().st_size / 1e6:.0f} MB, {(time.time() - t0) / 60:.0f} min)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
