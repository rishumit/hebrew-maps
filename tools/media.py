"""Download the POI images once, so the map shows them without internet.

    python tools/media.py [--width 500] [--workers 2] [--interval 1.0] [--retry-failed] [--max-minutes N]

Reads the image URLs from the live database (data/maps.db), stores a resized
copy of each one under data/media/ and prints a summary. Already downloaded
images are skipped, so the script can be stopped (Ctrl+C or closing the
window) and re-run at any time – e.g. after a data update only new images are
fetched. The running server picks new images up immediately.

Results of each image:
* ok        – saved in data/media/;
* pending   – a content filter (e.g. NetFree) returned its "sent for review"
              stand-in picture instead of the photo. Nothing is saved; the image
              is tried again on the next run (after the filter approved it).
              Listed in data/media/pending.txt;
* failed    – permanent error (deleted from Wikimedia, blocked, not an image…).
              Listed in data/media/failed.txt and not retried unless
              --retry-failed is given;
* temporary errors (no connection, rate limiting, timeouts) are simply tried
  again on the next run.

--max-minutes stops cleanly after that many minutes (exit code 0) and keeps
everything downloaded so far – used by CI, where a job has a hard time limit:
each weekly run continues where the previous one stopped.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hmaps.core import db, media  # noqa: E402

DATA = Path(os.environ.get("HMAPS_DATA", ROOT / "data"))
UA = "HebrewMaps/1.0 (self-hosted offline map of Israel; one-time image download)"
MAX_BYTES = 4_000_000
PLACEHOLDER_MAX_BYTES = 60_000  # existing files above this size are real photos
RETRY_DELAY = float(os.environ.get("HMAPS_MEDIA_RETRY_DELAY", "3"))
PAUSE_MIN, PAUSE_MAX = RETRY_DELAY * 10, RETRY_DELAY * 100  # 30 s … 5 min


class Permanent(Exception):
    """Will not succeed by retrying (404, not an image…)."""


class Pending(Exception):
    """The filter returned its 'under review' stand-in picture."""


class Temporary(Exception):
    """Network trouble / rate limiting – try again on the next run."""


class Throttle:
    """Shared pacing for all download threads.

    Wikimedia limits how fast one address may request thumbnails ("Too many
    requests"). Requests are spaced at least ``interval`` seconds apart, and a
    429 answer pauses *all* threads (30 s, doubling up to 5 min while it repeats).
    """

    def __init__(self, interval: float):
        self.interval = interval
        self.lock = threading.Lock()
        self.next_at = 0.0
        self.pause = 0.0
        self.paused_until = 0.0

    def wait(self) -> None:
        with self.lock:
            now = time.monotonic()
            at = max(now, self.next_at, self.paused_until)
            self.next_at = at + self.interval
        if at > now:
            time.sleep(at - now)

    def too_many(self, retry_after: float | None = None) -> float:
        with self.lock:
            now = time.monotonic()
            if now < self.paused_until:
                return self.paused_until - now  # another thread already paused everyone
            self.pause = min(max(self.pause * 2, PAUSE_MIN), PAUSE_MAX)
            wait = max(self.pause, retry_after or 0)
            self.paused_until = now + wait
        print(f"  … Wikimedia asked to slow down – pausing {wait:.0f} s", flush=True)
        return wait

    def success(self) -> None:
        with self.lock:
            self.pause /= 2


THROTTLE = Throttle(1.0)


def fetch(url: str, timeout: int = 60, tries: int = 6) -> bytes:
    delay = RETRY_DELAY
    last: Exception | None = None
    for attempt in range(tries):
        THROTTLE.wait()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "image/*"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read(MAX_BYTES + 1)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                last = e
                retry_after = e.headers.get("Retry-After") if e.headers else None
                THROTTLE.too_many(min(float(retry_after), 600) if retry_after and retry_after.isdigit() else None)
                continue
            if e.code in (408, 425, 500, 502, 503, 504):
                last = e
                time.sleep(delay)
                delay *= 2
                continue
            raise Permanent(f"HTTP {e.code}") from e
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            last = e
            time.sleep(delay)
            delay *= 2
            continue
        THROTTLE.success()
        if len(data) > MAX_BYTES:
            raise Permanent("too large")
        if not media.sniff(data):
            raise Permanent("not an image (blocked by a filter?)")
        if media.is_filter_placeholder(data):
            raise Pending("sent to the filter for review")
        return data
    raise Temporary(f"{type(last).__name__}: {getattr(last, 'reason', None) or last}")


def image_urls(db_path: Path, gallery: bool = True) -> list[str]:
    """Main POI photos, plus (schema 5) every Commons photo of the site / trail galleries."""
    import urllib.parse

    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT DISTINCT image FROM pois WHERE image IS NOT NULL AND image != ''").fetchall()
        files: list = []
        if gallery:
            try:
                files = con.execute("SELECT DISTINCT file FROM media WHERE source = 'commons'").fetchall()
            except sqlite3.OperationalError:  # database built before the galleries existed
                files = []
    finally:
        con.close()
    urls = {r[0].strip() for r in rows if r[0].strip().startswith("http")}
    # same URL form as the API uses for gallery photos (hmaps.api.rich.commons_url) → same /media/<key>
    urls |= {media.COMMONS_FILEPATH + urllib.parse.quote(f[0]) for f in files if f[0]}
    return sorted(urls)


def _read_list(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if path.exists():
        for ln in path.read_text(encoding="utf-8").splitlines():
            k, _, rest = ln.partition("\t")
            if k:
                out[k] = rest
    return out


def _write_list(path: Path, items: dict[str, str]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text("".join(f"{k}\t{v}\n" for k, v in sorted(items.items())), encoding="utf-8")
    os.replace(tmp, path)


def remove_placeholders(out: Path) -> int:
    """Delete stand-in pictures saved by an older version of this script."""
    removed = 0
    for p in out.iterdir():
        if media.KEY_RE.match(p.name) and p.stat().st_size <= PLACEHOLDER_MAX_BYTES:
            if media.is_filter_placeholder(p.read_bytes()):
                p.unlink()
                removed += 1
    return removed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--width", type=int, default=500, help="thumbnail width for Wikimedia images (default 500)")
    ap.add_argument("--workers", type=int, default=2, help="parallel downloads (default 2)")
    ap.add_argument("--interval", type=float, default=1.0,
                    help="minimum seconds between requests (default 1.0; Wikimedia limits fast clients)")
    ap.add_argument("--retry-failed", action="store_true", help="try again the images that failed permanently before")
    ap.add_argument("--max-minutes", type=float, default=0,
                    help="stop cleanly after this many minutes and keep what was downloaded (default: no limit)")
    args = ap.parse_args()
    THROTTLE.interval = max(0.0, args.interval)

    try:
        import PIL  # noqa: F401  (needed to recognise the filter's stand-in pictures)
    except ImportError:
        print("Pillow is missing. Run download-images.cmd (it installs it), or: pip install pillow")
        return 1
    db_path = db.resolve(DATA / "maps.db")
    if not db_path.exists():
        print("The map data was not built yet – run setup-windows.cmd first.")
        return 1
    out = DATA / "media"
    out.mkdir(parents=True, exist_ok=True)
    failed_file, pending_file = out / "failed.txt", out / "pending.txt"

    cleaned = remove_placeholders(out)
    if cleaned:
        print(f"Removed {cleaned} 'sent for review' stand-in pictures saved earlier – they will be retried.")

    failures = {} if args.retry_failed else _read_list(failed_file)
    pending: dict[str, str] = {}  # rebuilt every run: pending images are always retried
    urls = image_urls(db_path)
    have = sum(1 for u in urls if (out / media.key(u)).exists())
    todo = [u for u in urls if not (out / media.key(u)).exists() and media.key(u) not in failures]
    skipped = len(urls) - have - len(todo)
    print(f"Images in the data: {len(urls)} · already offline: {have} · to download: {len(todo)}"
          + (f" · skipped (failed before): {skipped}" if skipped else ""), flush=True)
    if not todo:
        print("Nothing to do – all images are available offline.")
        _write_list(pending_file, pending)
        return 0

    lock = threading.Lock()
    counts = {"done": 0, "ok": 0, "pending": 0, "failed": 0, "temporary": 0}
    total_bytes = 0
    last_error = ""
    t0 = last_print = time.time()
    stop = threading.Event()
    deadline = t0 + args.max_minutes * 60 if args.max_minutes > 0 else None
    out_of_time = False

    def one(url: str) -> tuple[str, int, str, str]:
        nonlocal out_of_time
        if deadline is not None and time.time() >= deadline and not stop.is_set():
            out_of_time = True
            stop.set()
        if stop.is_set():
            return url, 0, "skipped", ""
        k = media.key(url)
        try:
            data = fetch(media.download_url(url, args.width))
        except Pending as e:
            return url, 0, "pending", str(e)
        except Permanent as e:
            return url, 0, "failed", str(e)
        except Temporary as e:
            return url, 0, "temporary", str(e)
        except Exception as e:  # noqa: BLE001
            return url, 0, "temporary", f"{type(e).__name__}: {e}"
        tmp = out / (k + ".part")
        tmp.write_bytes(data)
        os.replace(tmp, out / k)
        return url, len(data), "ok", ""

    def report(final: bool = False) -> None:
        elapsed = max(time.time() - t0, 0.001)
        rate = counts["done"] / elapsed
        eta = (len(todo) - counts["done"]) / rate if rate else 0
        line = (f"  {counts['done']}/{len(todo)}  ok {counts['ok']}  pending review {counts['pending']}  "
                f"failed {counts['failed']}  temporary errors {counts['temporary']}  {total_bytes / 1e6:.0f} MB")
        if not final:
            line += f"  ~{eta / 60:.0f} min left"
        if last_error and not final:
            line += f"  | last error: {last_error[:70]}"
        print(line, flush=True)
        _write_list(failed_file, failures)
        _write_list(pending_file, pending)

    pool = ThreadPoolExecutor(max_workers=max(1, args.workers))
    interrupted = False
    try:
        futures = [pool.submit(one, u) for u in todo]
        for fut in as_completed(futures):
            url, size, kind, err = fut.result()
            if kind == "skipped":
                continue
            k = media.key(url)
            with lock:
                counts["done"] += 1
                counts[kind] += 1
                if kind == "ok":
                    total_bytes += size
                    failures.pop(k, None)
                elif kind == "pending":
                    pending[k] = url
                elif kind == "failed":
                    failures[k] = f"{url}\t{err}"
                    last_error = err
                else:
                    last_error = err
                if counts["done"] == 40 and counts["ok"] == 0 and counts["temporary"] >= 30:
                    print("  ! nothing could be downloaded so far – is the internet connection working? "
                          f"Last error: {err}", flush=True)
                if time.time() - last_print > 5:
                    last_print = time.time()
                    report()
    except KeyboardInterrupt:
        interrupted = True
        stop.set()
        print("\nStopping… (run again to continue where it stopped)", flush=True)
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
        report(final=True)

    if out_of_time:
        print(f"\nTime limit of {args.max_minutes:.0f} min reached – stopped cleanly; "
              f"{len(todo) - counts['done']} images left for the next run.", flush=True)
    offline = sum(1 for u in urls if (out / media.key(u)).exists())
    print(f"\nDone: {counts['ok']} new images ({total_bytes / 1e6:.0f} MB) in {(time.time() - t0) / 60:.0f} min."
          f" Offline now: {offline} of {len(urls)}.")
    if counts["pending"]:
        print(f"{counts['pending']} images were sent by the content filter (NetFree) for review and were not saved."
              " Run download-images.cmd again in a day or two to fetch the approved ones.")
    if counts["temporary"]:
        print(f"{counts['temporary']} images hit temporary errors (connection / rate limit) – run again later.")
    if failures:
        print(f"{len(failures)} images cannot be downloaded – see data\\media\\failed.txt"
              " (run with --retry-failed to try them again).")
    return 130 if interrupted else 0


if __name__ == "__main__":
    sys.exit(main())
