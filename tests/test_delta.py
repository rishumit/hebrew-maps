"""Delta (incremental) data packages: build + apply for single files (zstd patch-from) and for
the image folder (changed files only), chain selection, every fallback to the full package, and
an end-to-end run against a fake release server over three weekly versions.

    python -m unittest tests.test_delta
"""

import json
import os
import random
import shutil
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

from hmaps.api import updates
from hmaps.core import delta
from hmaps.core import components as catalog
from tests import test_updates as tu
from tests.helpers import sample_db
from tools import release

V1, V2, V3 = "20261008", "20261015", "20261022"
T1, T2, T3 = "data-2026-10-08", "data-2026-10-15", "data-2026-10-22"
D1, D2, D3 = "2026-10-08", "2026-10-15", "2026-10-22"


def write_tiles(path: Path, seed: int, changed: int = 0) -> Path:
    """An MBTiles file with 300 incompressible tiles; *changed* of them differ from seed's."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    rnd = random.Random(seed)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE metadata (name TEXT, value TEXT)")
    con.execute("CREATE TABLE tiles (zoom_level INTEGER, tile_column INTEGER, tile_row INTEGER, tile_data BLOB)")
    con.executemany("INSERT INTO metadata VALUES (?, ?)", {"name": "t", "format": "pbf"}.items())
    alt = random.Random(seed + 1000)
    for i in range(300):
        blob = rnd.randbytes(2000)
        if i < changed:
            blob = alt.randbytes(2000)
        con.execute("INSERT INTO tiles VALUES (12, ?, ?, ?)", (i, i, blob))
    con.commit()
    con.close()
    return path


def write_media(media: Path, names: list[str], seed: int = 1) -> None:
    rnd = random.Random(seed)
    for n in names:
        f = media / n
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(rnd.randbytes(4000) + n.encode())


def edit_db(path: Path, suffix: str) -> None:
    con = sqlite3.connect(path)
    con.execute("UPDATE pois SET name = name || ? WHERE rowid % 7 = 0", (suffix,))
    con.execute("INSERT OR REPLACE INTO meta VALUES ('built', ?)", (f"2026-10-{suffix[-2:]}T20:00:00+00:00",))
    con.commit()
    con.execute("VACUUM")
    con.close()


class DeltaCoreTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp(prefix="hmaps-delta-"))

    def tmp(self, name: str) -> Path:
        p = Path(tempfile.mkdtemp(prefix=name + "-", dir=self.root))
        return p

    def package(self, folder: Path, cid: str, files: dict[str, Path], version: str) -> Path:
        out = folder / catalog.asset_name(cid, version)
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("component.json", json.dumps({"component": cid, "version": version}))
            for name, path in files.items():
                zf.write(path, name)
        return out

    @unittest.skipUnless(delta.zstd_available(), "zstandard not installed")
    def test_zstd_patch_is_small_and_exact(self):
        d = self.tmp("patch")
        old = d / "old.db"
        shutil.copy(sample_db(), old)
        new = d / "new.db"
        shutil.copy(old, new)
        edit_db(new, "-15")
        patch = delta.make_patch(old.read_bytes(), new.read_bytes())
        self.assertLess(len(patch), new.stat().st_size / 10, "a few changed rows give a small patch")
        out = d / "out.db"
        import io
        delta.apply_patch(old, io.BytesIO(patch), out)
        self.assertEqual(out.read_bytes(), new.read_bytes())

    @unittest.skipUnless(delta.zstd_available(), "zstandard not installed")
    def test_complete_component_build_apply_and_fallbacks(self):
        d = self.tmp("basemap")
        old_t, new_t = write_tiles(d / "v1" / "israel.mbtiles", 1), write_tiles(d / "v2" / "israel.mbtiles", 1, changed=5)
        z1 = self.package(d, "basemap", {"israel.mbtiles": old_t}, V1)
        z2 = self.package(d, "basemap", {"israel.mbtiles": new_t}, V2)
        dz = delta.build("basemap", z2, d / "out", V1, V2, base_zip=z1, log=lambda *_: None)
        self.assertEqual(dz.name, f"hmaps-basemap-{V2}-from-{V1}.delta.zip")
        desc = delta.describe(dz)
        self.assertEqual((desc["kind"], desc["from"], desc["to"], desc["complete"]), ("zstd", V1, V2, True))
        self.assertEqual([o["op"] for o in desc["files"]], ["zstd"])
        self.assertLess(dz.stat().st_size, z2.stat().st_size * 0.2)

        st = d / "staging"
        delta.apply("basemap", dz, st, lambda p: old_t, expect_from=V1, expect_to=V2)
        self.assertEqual(delta.sha256_file(st / "israel.mbtiles"), delta.sha256_file(new_t))

        # another base (a local rebuild) → base_mismatch, nothing written
        other = write_tiles(d / "other" / "israel.mbtiles", 7)
        with self.assertRaises(delta.DeltaError) as cm:
            delta.apply("basemap", dz, d / "st2", lambda p: other)
        self.assertEqual(cm.exception.code, "base_mismatch")
        self.assertFalse((d / "st2" / "israel.mbtiles").exists())
        with self.assertRaises(delta.DeltaError) as cm:
            delta.apply("basemap", dz, d / "st3", lambda p: None)
        self.assertEqual(cm.exception.code, "base_missing")
        with self.assertRaises(delta.DeltaError) as cm:
            delta.apply("basemap", dz, d / "st3", lambda p: old_t, expect_from="20200101")
        self.assertEqual(cm.exception.code, "base_mismatch")

        # a delta whose payload was altered (but still a valid patch) fails the SHA-256 of the result
        bad = d / "bad.delta.zip"
        with zipfile.ZipFile(dz) as src, zipfile.ZipFile(bad, "w") as dst:
            for info in src.infolist():
                data = src.read(info)
                if info.filename == "delta.json":
                    j = json.loads(data)
                    j["files"][0]["sha256"] = "0" * 64
                    data = json.dumps(j).encode()
                dst.writestr(info.filename, data)
        with self.assertRaises(delta.DeltaError) as cm:
            delta.apply("basemap", bad, d / "st4", lambda p: old_t)
        self.assertEqual(cm.exception.code, "checksum")
        self.assertFalse(list((d / "st4").rglob("*.mbtiles")), "no half-written file is left")
        # garbage instead of a ZIP
        junk = d / "junk.delta.zip"
        junk.write_bytes(b"not a zip")
        with self.assertRaises(delta.DeltaError):
            delta.apply("basemap", junk, d / "st5", lambda p: old_t)

    def test_unchanged_and_small_files_without_zstd(self):
        d = self.tmp("transit")
        npz, js = d / "transit.npz", d / "transit.json"
        npz.write_bytes(random.Random(3).randbytes(100_000))
        js.write_text('{"start": "2026-10-01"}', encoding="utf-8")
        z1 = self.package(d, "transit", {"transit.npz": npz, "transit.json": js}, V1)
        js2 = d / "v2.json"
        js2.write_text('{"start": "2026-10-08"}', encoding="utf-8")
        z2 = self.package(d, "transit", {"transit.npz": npz, "transit.json": js2}, V2)
        dz = delta.build("transit", z2, d / "out", V1, V2, base_index=delta.zip_index(z1), log=lambda *_: None)
        desc = delta.describe(dz)
        self.assertEqual(desc["kind"], "files")
        self.assertEqual({o["path"]: o["op"] for o in desc["files"]}, {"transit.npz": "keep", "transit.json": "full"})
        st = d / "st"
        delta.apply("transit", dz, st, lambda p: {"transit.npz": npz, "transit.json": js}[p])
        self.assertEqual((st / "transit.npz").read_bytes(), npz.read_bytes())
        self.assertEqual((st / "transit.json").read_text(encoding="utf-8"), '{"start": "2026-10-08"}')

    def test_directory_component(self):
        d = self.tmp("media")
        write_media(d / "v1" / "media", ["a/1.jpg", "a/2.jpg", "b/3.jpg"])
        write_media(d / "v2" / "media", ["a/1.jpg", "b/3.jpg", "c/4.jpg"])
        (d / "v2" / "media" / "b" / "3.jpg").write_bytes(b"changed image")
        files = lambda v: {f"media/{p.relative_to(d / v / 'media').as_posix()}": p  # noqa: E731
                           for p in (d / v / "media").rglob("*") if p.is_file()}
        z1 = self.package(d, "media", files("v1"), V1)
        z2 = self.package(d, "media", files("v2"), V2)
        dz = delta.build("media", z2, d / "out", V1, V2, base_index=delta.zip_index(z1), log=lambda *_: None)
        desc = delta.describe(dz)
        self.assertFalse(desc["complete"])
        self.assertEqual(sorted(o["path"] for o in desc["files"]), ["media/b/3.jpg", "media/c/4.jpg"])
        self.assertEqual(desc["deleted"], ["media/a/2.jpg"])
        st = d / "st"
        delta.apply("media", dz, st, lambda p: None)
        self.assertEqual(sorted(p.relative_to(st).as_posix() for p in st.rglob("*") if p.is_file()),
                         ["media/b/3.jpg", "media/c/4.jpg"])

    def test_unsafe_paths_are_rejected(self):
        d = self.tmp("evil")
        evil = d / "evil.delta.zip"
        with zipfile.ZipFile(evil, "w") as zf:
            zf.writestr("delta.json", json.dumps({"schema": 1, "component": "media", "from": V1, "to": V2,
                                                  "complete": False, "files": [], "deleted": ["../../x.txt"]}))
        with self.assertRaises(delta.DeltaError) as cm:
            delta.apply("media", evil, d / "st", lambda p: None)
        self.assertEqual(cm.exception.code, "bad_delta")

    def test_plan_picks_the_cheapest_chain(self):
        ds = [{"from": V1, "to": V3, "file": "a", "size": 50, "kind": "zstd"},
              {"from": V1, "to": V2, "file": "b", "size": 10, "kind": "files"},
              {"from": V2, "to": V3, "file": "c", "size": 15, "kind": "zstd"},
              {"from": "20261001", "to": V1, "file": "d", "size": 5, "kind": "files"}]
        self.assertEqual([d["file"] for d in delta.plan(V1, V3, ds, 1000, kinds={"files", "zstd"})], ["b", "c"])
        self.assertEqual([d["file"] for d in delta.plan("20261001", V3, ds, 1000, kinds={"files", "zstd"})],
                         ["d", "b", "c"])
        self.assertEqual(delta.plan(V1, V3, ds, 1000, kinds={"files"}), None, "zstd unsupported: no path")
        self.assertIsNone(delta.plan(V1, V3, ds, 40, kinds={"files", "zstd"}), "25 ≥ 60 % of 40: full package")
        self.assertIsNone(delta.plan("20200101", V3, ds, 1000), "unknown base")
        self.assertIsNone(delta.plan(V3, V3, ds, 1000))
        self.assertEqual([d["file"] for d in delta.plan("20261001", V3, ds, 1000, kinds={"files", "zstd"}, max_chain=2)],
                         ["d", "a"], "at most two steps: the direct delta of the second step")
        self.assertIsNone(delta.plan("20261001", V3, ds, 1000, kinds={"files", "zstd"}, max_chain=1))


class ReleaseDeltasTest(unittest.TestCase):
    def test_manifest_lists_deltas_history_and_files(self):
        root = Path(tempfile.mkdtemp(prefix="hmaps-reldelta-"))
        src = tu.build_source_data(root)
        write_media(src / "media", [f"n/{i}.jpg" for i in range(30)])
        old = root / "old"
        release.pack("media", src, old, V1)
        release.pack("basemap", src, old, V1)
        m1 = release.build_manifest(old, T1, V1, D1, None, repo="o/r")
        release.build_deltas(old, None, V1, D1, log=lambda *_: None)  # only the file lists
        self.assertTrue((old / f"hmaps-media-{V1}.files.json").is_file())
        m1 = release.build_manifest(old, T1, V1, D1, None, repo="o/r")
        self.assertEqual(m1["components"]["media"]["files"], f"hmaps-media-{V1}.files.json")

        write_media(src / "media", ["n/new1.jpg", "n/new2.jpg"], seed=4)
        write_tiles(src / "tiles" / "israel-20261022-200000.mbtiles", 2)
        dist = root / "dist"
        release.pack("media", src, dist, V2)
        release.pack("basemap", src, dist, V2)
        fetched = []

        def fetch(rel, name, folder):
            fetched.append((rel, name))
            shutil.copy(old / name, folder / name)
            return folder / name

        lines = release.build_deltas(dist, m1, V2, D2, fetch=fetch, log=lambda *_: None)
        self.assertIn((T1, f"hmaps-media-{V1}.files.json"), fetched, "images: only the old file list")
        self.assertIn((T1, f"hmaps-basemap-{V1}.zip"), fetched, "single files: the old package")
        self.assertTrue(any(line.startswith("media") for line in lines))
        m2 = release.build_manifest(dist, T2, V2, D2, m1, repo="o/r")
        media = m2["components"]["media"]
        (d,) = media["deltas"]
        self.assertEqual((d["from"], d["to"], d["kind"], d["release"]), (V1, V2, "files", T2))
        self.assertEqual(d["url"], f"https://github.com/o/r/releases/download/{T2}/{d['file']}")
        self.assertEqual([h["version"] for h in media["history"]], [V1])
        self.assertIn("עדכון חלקי", release.notes(m2))
        # basemap: a completely different tile set is not worth a delta
        self.assertEqual(m2["components"]["basemap"]["deltas"], [])
        # next week, unchanged: carried as they are; six weeks later the old ones are dropped
        m3 = release.build_manifest(root / "empty", T3, V3, D3, m2, repo="o/r")
        self.assertEqual(m3["components"]["media"]["status"], "unchanged")
        self.assertEqual(m3["components"]["media"]["deltas"], media["deltas"])
        late = release.build_manifest(root / "empty", "data-2026-12-31", "20261231", "2026-12-31", m2, repo="o/r")
        self.assertEqual(late["components"]["media"]["deltas"], [])
        self.assertEqual(late["components"]["media"]["history"], [])
        # verify ignores delta packages next to the full ones
        self.assertEqual(release.main(["verify", *map(str, sorted(dist.glob("*.zip")))]), 0)


@unittest.skipUnless(delta.zstd_available(), "zstandard not installed")
class DeltaEndToEndTest(unittest.TestCase):
    """Three weekly releases on a fake GitHub; the client goes v1 (full) → v2 (delta) → v3 (chain)."""

    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp(prefix="hmaps-delta-e2e-"))
        cls.web = cls.root / "web"
        cls.web.mkdir()
        cls.srv, cls.base = tu.serve(cls.web)
        os.environ["HMAPS_UPDATES_NO_SLEEP"] = "1"
        src = tu.build_source_data(cls.root)
        shutil.rmtree(src / "tiles")
        write_tiles(src / "tiles" / "israel-1.mbtiles", 5)
        write_media(src / "media", [f"p/{i}.jpg" for i in range(40)])
        cls.src = src
        cls.manifests = {}
        cls.publish(V1, T1, D1, None)
        # week 2: some POIs renamed, 4 tiles changed, 3 images added, one removed
        maps = updates.db.resolve(src / "maps.db")
        edit_db(maps, "-15")
        write_tiles(src / "tiles" / "israel-1.mbtiles", 5, changed=4)
        write_media(src / "media", ["p/new1.jpg", "p/new2.jpg", "p/new3.jpg"], seed=9)
        (src / "media" / "p" / "0.jpg").unlink()
        cls.publish(V2, T2, D2, cls.manifests[V1])
        # week 3: deltas only from the previous week (keep=1) – v1 has to chain v1→v2→v3
        edit_db(maps, "-22")
        write_tiles(src / "tiles" / "israel-1.mbtiles", 5, changed=8)
        cls.publish(V3, T3, D3, cls.manifests[V2], keep=1)

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        os.environ.pop("HMAPS_UPDATES_MANIFEST", None)

    @classmethod
    def publish(cls, version, tag, date, previous, keep=6):
        dist = cls.root / tag
        for cid in ("maps", "basemap", "media"):
            release.pack(cid, cls.src, dist, version)

        def fetch(rel, name, folder):
            shutil.copy(cls.root / rel / name, folder / name)
            return folder / name

        release.build_deltas(dist, previous, version, date, fetch=fetch, keep=keep, log=lambda *_: None)
        m = release.build_manifest(dist, tag, version, date, previous, repo="o/r")
        rel = cls.web / tag
        shutil.copytree(dist, rel, dirs_exist_ok=True)
        for e in m["components"].values():
            for item in [e] + list(e.get("deltas") or []):
                if item.get("file"):
                    item["url"] = f"{cls.base}/{item['release']}/{item['file']}"
        (rel / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
        cls.manifests[version] = m

    def use(self, version):
        os.environ["HMAPS_UPDATES_MANIFEST"] = f"{self.base}/{self.manifests[version]['release']}/manifest.json"
        updates._manifest_cache = None

    def setUp(self):
        tu._Handler.log.clear()
        tu._Handler.fail.clear()
        os.environ.pop("HMAPS_UPDATES_DELTA", None)
        self.data = Path(tempfile.mkdtemp(prefix="data-", dir=self.root))

    def install_all(self):
        return {cid: updates.install(cid, self.data) for cid in ("maps", "basemap", "media")}

    def requested(self):
        return [p.rsplit("/", 1)[-1] for p, _ in tu._Handler.log if p.endswith(".zip")]

    def pristine_sha(self):
        return delta.sha256_file(updates.base_dir(self.data) / "maps" / "maps.db")

    def target_sha(self, version, name="maps.db"):
        with zipfile.ZipFile(self.root / self.manifests[version]["release"] / catalog.asset_name("maps", version)) as zf:
            import hashlib
            return hashlib.sha256(zf.read(name)).hexdigest()

    def test_full_then_delta_then_chain(self):
        self.use(V1)
        recs = self.install_all()
        self.assertTrue(all(r["via"] == "full" for r in recs.values()))
        self.assertEqual(self.pristine_sha(), self.target_sha(V1), "maps.db is kept as released (delta base)")
        (self.data / "media" / "own.jpg").write_bytes(b"mine")

        self.use(V2)
        by = {c["id"]: c for c in updates.check(force=True, data=self.data)["components"]}
        for cid in ("maps", "basemap", "media"):
            av = by[cid]["available"]
            self.assertTrue(by[cid]["update_available"])
            self.assertEqual(av["delta"]["steps"], 1, cid)
            self.assertLess(av["download_size"], 0.6 * av["size"], cid)
        tu._Handler.log.clear()
        job = updates.Job("maps")
        recs = {cid: updates.install(cid, self.data, job if cid == "maps" else None) for cid in ("maps", "basemap", "media")}
        self.assertTrue(job.delta)
        self.assertEqual(job.full_size, self.manifests[V2]["components"]["maps"]["size"])
        self.assertTrue(all(r["via"] == "delta" and r["version"] == V2 for r in recs.values()), recs)
        self.assertTrue(all(".delta.zip" in f for f in self.requested()), self.requested())
        self.assertEqual(self.pristine_sha(), self.target_sha(V2))
        live = updates.db.resolve(self.data / "maps.db")
        con = sqlite3.connect(live)
        self.assertEqual(con.execute("SELECT value FROM meta WHERE key='built'").fetchone()[0], "2026-10-15T20:00:00+00:00")
        con.close()
        media = self.data / "media" / "p"
        self.assertTrue((media / "new3.jpg").is_file())
        self.assertFalse((media / "0.jpg").exists(), "removed from the release → removed")
        self.assertTrue((self.data / "media" / "own.jpg").exists(), "the user's own files are kept")
        self.assertFalse(any((self.data / "updates" / "staging").iterdir()))
        self.assertFalse(any((self.data / "updates" / "downloads").iterdir()))

        # v3 publishes deltas from v2 only; a computer still on v2 takes it, one on v1 chains
        self.use(V3)
        tu._Handler.log.clear()
        rec = updates.install("basemap", self.data)
        self.assertEqual((rec["via"], rec["version"]), ("delta", V3))
        self.assertEqual(self.requested(), [f"hmaps-basemap-{V3}-from-{V2}.delta.zip"])

        fresh = Path(tempfile.mkdtemp(prefix="data-v1-", dir=self.root))
        self.data = fresh
        self.use(V1)
        updates.install("maps", fresh)
        self.use(V3)
        tu._Handler.log.clear()
        rec = updates.install("maps", fresh)
        self.assertEqual(rec["via"], "delta")
        self.assertEqual(self.requested(), [f"hmaps-maps-{V2}-from-{V1}.delta.zip", f"hmaps-maps-{V3}-from-{V2}.delta.zip"])
        self.assertEqual(self.pristine_sha(), self.target_sha(V3))

    def test_fallbacks_to_the_full_package(self):
        self.use(V1)
        self.install_all()
        self.use(V2)
        # 1) the base was changed locally → the full package
        pristine = updates.base_dir(self.data) / "maps" / "maps.db"
        pristine.unlink()
        (self.data / "maps.current").unlink()
        for f in self.data.glob("maps-*.db"):
            f.unlink()
        shutil.copy(sample_db(), self.data / "maps.db")  # a database built on this computer
        tu._Handler.log.clear()
        rec = updates.install("maps", self.data)
        self.assertEqual(rec["via"], "full")
        self.assertEqual(self.requested(), [f"hmaps-maps-{V2}-from-{V1}.delta.zip", f"hmaps-maps-{V2}.zip"])
        # 2) a corrupt delta download (SHA-256 mismatch) → the full package, nothing broken
        m = json.loads(json.dumps(self.manifests[V2]))
        m["components"]["basemap"]["deltas"][0]["sha256"] = "0" * 64
        m["_source"] = "x"
        tu._Handler.log.clear()
        rec = updates.install("basemap", self.data, manifest=m)
        self.assertEqual(rec["via"], "full")
        self.assertEqual(self.requested()[-1], f"hmaps-basemap-{V2}.zip")
        # 3) the delta release was already deleted (404) → the full package
        m["components"]["media"]["deltas"][0]["url"] = f"{self.base}/gone/x.delta.zip"
        rec = updates.install("media", self.data, manifest=m)
        self.assertEqual(rec["via"], "full")
        # 4) deltas switched off
        os.environ["HMAPS_UPDATES_DELTA"] = "0"
        self.assertIsNone(updates.delta_plan("maps", {"version": V1, "source": "online"},
                                             self.manifests[V2]["components"]["maps"]))
        self.assertFalse(any((self.data / "updates" / "downloads").iterdir()))
        self.assertFalse((self.data / "updates" / ".lock").exists())


if __name__ == "__main__":
    unittest.main()
