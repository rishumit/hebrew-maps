"""One app version everywhere: the Python server, the Tauri desktop exe and the installer.

    python tools/set_version.py 1.2.0      # write it into every file below (the release build does this from the tag)
    python tools/set_version.py --check    # exit 1 if the files disagree (tests/test_appupdate.py runs it too)
    python tools/set_version.py            # print the current version

Source of truth in the repository: ``hmaps/__init__.py`` (``__version__``, shown by ``/api/meta`` and
``/api/health`` and compared by the in-app updater). ``installer.yml`` calls this script with the
version of the ``v*`` tag before PyInstaller and cargo run, and passes it to Inno Setup as
``/DMyAppVersion``; ``installer/build.ps1`` reads it from ``hmaps/__init__.py`` when no
``-Version`` is given.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INIT = ROOT / "hmaps" / "__init__.py"
TAURI = ROOT / "src-tauri" / "tauri.conf.json"
CARGO = ROOT / "src-tauri" / "Cargo.toml"
LOCK = ROOT / "src-tauri" / "Cargo.lock"
CRATE = "hebrew-maps-desktop"
#: semver as Cargo and Tauri accept it (pre-release / build suffix allowed)
SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _write(p: Path, text: str) -> None:
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def current() -> dict[str, str | None]:
    """The version written in each file."""
    out: dict[str, str | None] = {}
    m = re.search(r'^__version__\s*=\s*"([^"]+)"', _read(INIT), re.M)
    out["hmaps/__init__.py"] = m.group(1) if m else None
    out["src-tauri/tauri.conf.json"] = json.loads(_read(TAURI)).get("version")
    m = re.search(r'^\[package\][^\[]*?^version\s*=\s*"([^"]+)"', _read(CARGO), re.M | re.S)
    out["src-tauri/Cargo.toml"] = m.group(1) if m else None
    if LOCK.exists():
        m = re.search(rf'^name = "{CRATE}"\nversion = "([^"]+)"', _read(LOCK), re.M)
        out["src-tauri/Cargo.lock"] = m.group(1) if m else None
    return out


def set_version(v: str) -> None:
    v = v.strip().lstrip("vV")
    if not SEMVER.match(v):
        raise SystemExit(f"not a semantic version: {v!r} (expected e.g. 1.2.0)")
    text = _read(INIT)
    _write(INIT, re.sub(r'^__version__\s*=\s*"[^"]*"', f'__version__ = "{v}"', text, count=1, flags=re.M))
    # tauri.conf.json: only the top-level "version" line (keep the file's formatting)
    text = _read(TAURI)
    _write(TAURI, re.sub(r'^(  "version":\s*)"[^"]*"', rf'\g<1>"{v}"', text, count=1, flags=re.M))
    text = _read(CARGO)
    _write(CARGO, re.sub(r'(^\[package\][^\[]*?^version\s*=\s*)"[^"]*"', rf'\g<1>"{v}"', text, count=1,
                         flags=re.M | re.S))
    if LOCK.exists():  # cargo build --locked refuses a lock file whose root version is stale
        text = _read(LOCK)
        _write(LOCK, re.sub(rf'(^name = "{CRATE}"\nversion = )"[^"]*"', rf'\g<1>"{v}"', text, count=1, flags=re.M))
    bad = {f: x for f, x in current().items() if x != v}
    if bad:
        raise SystemExit(f"could not set the version in: {bad}")


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print(current()["hmaps/__init__.py"])
        return 0
    if argv[0] == "--check":
        cur = current()
        if len(set(cur.values())) != 1:
            print("version mismatch: " + ", ".join(f"{f}={v}" for f, v in cur.items()))
            return 1
        print(f"version {next(iter(cur.values()))} everywhere")
        return 0
    set_version(argv[0])
    print(f"version set to {current()['hmaps/__init__.py']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
