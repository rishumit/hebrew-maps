"""Delta (incremental) data packages: download only what changed since the installed version.

Shared by the release tooling (``tools/release.py delta``, run by the publish job of
``.github/workflows/weekly-data.yml``) and the online updater (``hmaps/api/updates.py``).

A delta is a ZIP ``hmaps-<component>-<to>-from-<from>.delta.zip`` with a ``delta.json`` and the
payload it needs. Every file of the target version is described by one operation:

``keep``   the file did not change – copied from the installed (base) file
``full``   new or changed small file, shipped whole (``files/<path>``)
``zstd``   changed large file, shipped as a zstd "patch-from" frame (``patch/<path>.zst``): the
           new file compressed with the old file as a raw-content dictionary (long-distance
           matching, window over both files). Measured on the real data (docs/12-operations.md):
           maps.db 177 MB with 1 % of the POIs changed → 1.25 MB (level 9) / 0.13 MB (level 19)
           instead of a 60 MB package; a Planetiler basemap with 3 % of the tiles changed and
           every tile id shifted → 2.6 MB instead of 96 MB. A fixed-block (page hash) diff was
           measured too: 1.6 MB for maps.db but 72 MB for the basemap – the shifted ids change
           every page – so the binary delta of single files is always zstd.

Two kinds of components:

* complete (maps, basemap, terrain, transit, zmanim): ``delta.json`` lists *every* file of the
  target payload, so applying a delta rebuilds the same staging folder a full package would
  give – the normal atomic swap of the updater then installs it. ``keep``/``zstd`` name the
  SHA-256 of the base file they need; the installed file is hashed first and a mismatch (a
  locally rebuilt or modified file) makes the updater fall back to the full package.
* directory (media): merged into the installed folder, so the delta only holds the added or
  changed images (``full``) and the paths that were removed (``deleted``). Built from the
  small file list of the base version (``hmaps-media-<version>.files.json``) – the old package
  is not needed.

Every file written is checked against the size and SHA-256 in ``delta.json``; the delta ZIP itself
is checked against the SHA-256 in the release manifest before it is opened.
"""

from __future__ import annotations

import hashlib
import heapq
import json
import os
import shutil
import zipfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath

SCHEMA = 1
DESCRIPTOR = "delta.json"
#: components whose package is merged into an existing folder (only changed files are listed)
DIR_COMPONENTS = frozenset({"media"})
#: never worth a delta (tiny packages)
NO_DELTA = frozenset({"zmanim"})
KIND_FILES = "files"
KIND_ZSTD = "zstd"
CHUNK = 1 << 20
#: smaller files are shipped whole even when changed
MIN_PATCH = 64 * 1024
#: larger single files get no binary delta (memory: both files are held in memory)
MAX_PATCH = 1536 * 1024 ** 2
#: the client takes a delta (chain) only below this share of the full package
RATIO = 0.6
#: at most this many deltas applied one after another
MAX_CHAIN = 4


class DeltaError(Exception):
    """A delta could not be built or applied (the updater then installs the full package)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# =========================================================================== helpers

def zstd_available() -> bool:
    try:
        import zstandard  # noqa: F401
        return True
    except ImportError:
        return False


def supported_kinds() -> set[str]:
    return {KIND_FILES, KIND_ZSTD} if zstd_available() else {KIND_FILES}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def safe_member(name: str) -> bool:
    p = PurePosixPath(name)
    return bool(name) and not p.is_absolute() and ".." not in p.parts and "\\" not in name and ":" not in name


def asset_name(component_id: str, to_version: str, from_version: str) -> str:
    """``hmaps-<component>-<to>-from-<from>.delta.zip``"""
    return f"hmaps-{component_id}-{to_version}-from-{from_version}.delta.zip"


def files_asset_name(package_file: str) -> str:
    """The file list published next to a package: ``hmaps-media-20261022.zip`` → ``….files.json``."""
    return package_file[:-4] + ".files.json" if package_file.endswith(".zip") else package_file + ".files.json"


def zip_index(zip_path: Path, skip: tuple[str, ...] = ("component.json",)) -> dict[str, list]:
    """``{path: [size, sha256]}`` of the payload members of a package ZIP."""
    out: dict[str, list] = {}
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if info.is_dir() or info.filename in skip:
                continue
            h = hashlib.sha256()
            with zf.open(info) as f:
                while chunk := f.read(CHUNK):
                    h.update(chunk)
            out[info.filename] = [info.file_size, h.hexdigest()]
    return out


def _zstd_params(zs, size: int, level: int):
    # the window spans the dictionary (old file) and the new file; long-distance matching finds
    # moved blocks anywhere in the old file
    wl = max(20, min(31, (max(size, 1) * 2 - 1).bit_length()))
    return zs.ZstdCompressionParameters.from_level(level, window_log=wl, enable_ldm=True,
                                                   hash_log=min(wl - 1, 26), chain_log=min(wl - 1, 26))


def default_level(size: int) -> int:
    # level 19 is the smallest by far on the real maps.db (0.13 vs 1.25 MB) but takes ~80 s and
    # ~1.8 GB for 177 MB; level 12 for bigger files keeps the publish job short
    return 19 if size <= 256 * 1024 ** 2 else 12


def make_patch(old: bytes, new: bytes, level: int | None = None) -> bytes:
    import zstandard as zs

    params = _zstd_params(zs, max(len(old), len(new)), level or default_level(len(new)))
    d = zs.ZstdCompressionDict(old, dict_type=zs.DICT_TYPE_RAWCONTENT)
    d.precompute_compress(compression_params=params)
    return zs.ZstdCompressor(dict_data=d, compression_params=params).compress(new)


def apply_patch(old_path: Path, patch_stream, out_path: Path) -> None:
    import zstandard as zs

    old = old_path.read_bytes()
    d = zs.ZstdCompressionDict(old, dict_type=zs.DICT_TYPE_RAWCONTENT)
    dctx = zs.ZstdDecompressor(dict_data=d, max_window_size=1 << 31)
    with open(out_path, "wb") as out:
        dctx.copy_stream(patch_stream, out, read_size=CHUNK, write_size=CHUNK)


# =========================================================================== build

def build(component_id: str, new_zip: Path, out_dir: Path, from_version: str, to_version: str, *,
          base_zip: Path | None = None, base_index: dict[str, list] | None = None,
          level: int | None = None, log: Callable[[str], None] = print) -> Path:
    """Build the delta from *from_version* (``base_zip`` or its ``base_index``) to *new_zip*.

    Complete components need ``base_zip`` for binary patches (with only ``base_index`` changed
    files are shipped whole); directory components need only ``base_index``."""
    if base_zip is None and base_index is None:
        raise DeltaError("no_base", "a delta needs the base package or its file list")
    is_dir = component_id in DIR_COMPONENTS
    if base_index is None:
        base_index = zip_index(base_zip)
    can_patch = base_zip is not None and not is_dir and zstd_available()
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / asset_name(component_id, to_version, from_version)
    tmp = out.with_name(out.name + ".part")
    ops: list[dict] = []
    deleted: list[str] = []
    patched = full = kept = 0
    with zipfile.ZipFile(new_zip) as nz, \
            zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as dz, \
            (zipfile.ZipFile(base_zip) if can_patch else _Null()) as bz:
        new_index = zip_index(new_zip)
        for name, (size, sha) in new_index.items():
            old = base_index.get(name)
            if old and old[1] == sha:
                if not is_dir:
                    ops.append({"path": name, "op": "keep", "size": size, "sha256": sha, "base_sha256": old[1]})
                kept += 1
                continue
            op = None
            if can_patch and old and MIN_PATCH <= size <= MAX_PATCH and old[0] <= MAX_PATCH:
                try:
                    patch = make_patch(bz.read(name), nz.read(name), level)
                except MemoryError:
                    patch = None
                    log(f"  {name}: not enough memory for a binary delta – shipped whole")
                if patch is not None and len(patch) < size * 0.5:
                    dz.writestr(f"patch/{name}.zst", patch, compress_type=zipfile.ZIP_STORED)
                    op = {"path": name, "op": "zstd", "size": size, "sha256": sha, "base_sha256": old[1],
                          "patch_size": len(patch)}
                    patched += 1
            if op is None:
                info = zipfile.ZipInfo(f"files/{name}", date_time=(2020, 1, 1, 0, 0, 0))
                # images and npz are compressed already: store them as-is (like the full packages)
                stored = name.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".gif", ".npz"))
                info.compress_type = zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED
                with nz.open(name) as src, dz.open(info, "w", force_zip64=size > 2 ** 31) as dst:
                    shutil.copyfileobj(src, dst, CHUNK)
                op = {"path": name, "op": "full", "size": size, "sha256": sha}
                full += 1
            ops.append(op)
        deleted = sorted(p for p in base_index if p not in new_index)
        desc = {"schema": SCHEMA, "component": component_id, "from": from_version, "to": to_version,
                "kind": KIND_ZSTD if patched else KIND_FILES, "complete": not is_dir,
                "files": ops, "deleted": deleted}
        dz.writestr(DESCRIPTOR, json.dumps(desc, ensure_ascii=False, separators=(",", ":")))
    os.replace(tmp, out)
    log(f"{out.name}: {out.stat().st_size / 1e6:.2f} MB – {patched} patched, {full} whole, {kept} unchanged, "
        f"{len(deleted)} deleted")
    return out


class _Null:
    def __enter__(self):
        return None

    def __exit__(self, *exc):
        return False


def describe(delta_zip: Path) -> dict:
    with zipfile.ZipFile(delta_zip) as zf:
        return json.loads(zf.read(DESCRIPTOR).decode("utf-8"))


# =========================================================================== apply

def apply(component_id: str, delta_zip: Path, staging: Path, base: Callable[[str], Path | None], *,
          expect_from: str | None = None, expect_to: str | None = None, progress=None) -> dict:
    """Apply one delta into *staging*; returns its ``delta.json`` (``deleted`` for directory components).

    Complete components: *staging* must be empty or missing and receives the whole target payload.
    Directory components: added/changed files are written into *staging* (which may already hold
    the files of a previous delta of a chain). *base(path)* is the local file of the base version."""
    try:
        zf = zipfile.ZipFile(delta_zip)
    except (zipfile.BadZipFile, OSError) as e:
        raise DeltaError("bad_delta", f"not a delta package: {e}") from e
    with zf:
        try:
            desc = json.loads(zf.read(DESCRIPTOR).decode("utf-8"))
        except (KeyError, ValueError) as e:
            raise DeltaError("bad_delta", "delta.json missing or invalid") from e
        if desc.get("schema") != SCHEMA or desc.get("component") != component_id:
            raise DeltaError("bad_delta", "delta of another component or schema")
        if expect_from is not None and str(desc.get("from")) != str(expect_from):
            raise DeltaError("base_mismatch", f"delta from {desc.get('from')}, installed {expect_from}")
        if expect_to is not None and str(desc.get("to")) != str(expect_to):
            raise DeltaError("bad_delta", f"delta to {desc.get('to')}, expected {expect_to}")
        is_dir = component_id in DIR_COMPONENTS
        if bool(desc.get("complete")) == is_dir:
            raise DeltaError("bad_delta", "delta layout does not match the component")
        ops = desc.get("files") or []
        paths = [o.get("path", "") for o in ops] + list(desc.get("deleted") or [])
        if any(not isinstance(p, str) or not safe_member(p) for p in paths):
            raise DeltaError("bad_delta", "invalid path in delta")
        staging.mkdir(parents=True, exist_ok=True)
        hashed: dict[Path, str] = {}

        def base_file(op: dict) -> Path:
            src = base(op["path"])
            if src is None or not src.is_file():
                raise DeltaError("base_missing", f"{op['path']}: base file not installed")
            if src not in hashed:
                hashed[src] = sha256_file(src)
            if hashed[src] != op.get("base_sha256"):
                raise DeltaError("base_mismatch", f"{op['path']}: the installed file differs from the delta base")
            return src

        total = sum(int(o.get("size") or 0) for o in ops) or 1
        done = 0
        for op in ops:
            target = staging.joinpath(*PurePosixPath(op["path"]).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(target.name + ".delta-tmp")
            kind = op.get("op")
            try:
                if kind == "keep":
                    shutil.copyfile(base_file(op), tmp)
                elif kind == "zstd":
                    src = base_file(op)
                    try:
                        with zf.open(f"patch/{op['path']}.zst") as patch:
                            apply_patch(src, patch, tmp)
                    except ImportError as e:
                        raise DeltaError("unsupported", "zstandard is not installed") from e
                    except MemoryError as e:
                        raise DeltaError("memory", "not enough memory to apply the binary delta") from e
                    except KeyError as e:
                        raise DeltaError("bad_delta", f"{op['path']}: patch missing") from e
                    except Exception as e:  # noqa: BLE001 – zstd.ZstdError and friends
                        if isinstance(e, DeltaError):
                            raise
                        raise DeltaError("bad_delta", f"{op['path']}: patch failed: {e}") from e
                elif kind == "full":
                    try:
                        with zf.open(f"files/{op['path']}") as src, open(tmp, "wb") as out:
                            shutil.copyfileobj(src, out, CHUNK)
                    except KeyError as e:
                        raise DeltaError("bad_delta", f"{op['path']}: file missing") from e
                else:
                    raise DeltaError("bad_delta", f"unknown operation {kind!r}")
                if tmp.stat().st_size != int(op.get("size", -1)) or sha256_file(tmp) != op.get("sha256"):
                    raise DeltaError("checksum", f"{op['path']}: result does not match its SHA-256")
                os.replace(tmp, target)
            except (zipfile.BadZipFile, OSError, EOFError) as e:
                raise DeltaError("bad_delta", f"{op['path']}: {e}") from e
            finally:
                if tmp.exists():
                    tmp.unlink()
            done += int(op.get("size") or 0)
            if progress:
                progress(done, total)
        if is_dir:
            for p in desc.get("deleted") or []:
                f = staging.joinpath(*PurePosixPath(p).parts)
                if f.is_file():
                    f.unlink()
    return desc


# =========================================================================== choose

def plan(installed: str | None, target: str | None, deltas: list[dict], full_size: int | None, *,
         kinds: set[str] | None = None, ratio: float = RATIO, max_chain: int = MAX_CHAIN) -> list[dict] | None:
    """The cheapest chain of deltas from *installed* to *target* (by download size), or None when
    there is none or it is not smaller than *ratio* × the full package."""
    if not installed or not target or str(installed) == str(target) or not deltas:
        return None
    kinds = supported_kinds() if kinds is None else kinds
    edges: dict[str, list[dict]] = {}
    for d in deltas:
        if not isinstance(d, dict) or d.get("kind") not in kinds or not d.get("file") or not d.get("size"):
            continue
        edges.setdefault(str(d.get("from")), []).append(d)
    # Dijkstra over versions, limited to max_chain steps
    heap: list[tuple[int, int, str, list[dict]]] = [(0, 0, str(installed), [])]
    best: dict[tuple[str, int], int] = {}
    n = 0
    while heap:
        cost, _, v, path = heapq.heappop(heap)
        if v == str(target):
            if full_size and cost >= ratio * full_size:
                return None
            return path
        if len(path) >= max_chain:
            continue
        for d in edges.get(v, []):
            nxt = str(d.get("to"))
            c = cost + int(d["size"])
            key = (nxt, len(path) + 1)
            if best.get(key, 1 << 62) <= c:
                continue
            best[key] = c
            n += 1
            heapq.heappush(heap, (c, n, nxt, path + [d]))
    return None
