"""Frozen launcher for Hebrew Maps (PyInstaller entry point).

When running as a PyInstaller bundle (sys.frozen is set), this module:
  - Sets HMAPS_DATA and HMAPS_WEB env vars to the directories next to the EXE
    BEFORE any hmaps.* import that reads them at module level.
  - Redirects stdout/stderr to a log file (windowed mode has no console).
  - Starts uvicorn programmatically.
  - Opens the browser after the server is ready.

When running normally (development), it just starts the server via uvicorn,
leaving all env vars untouched so settings.py uses its existing defaults.

``python -m hmaps update [--check] [--component X]`` runs the online data
updater instead (hmaps/api/updates.py).
"""

from __future__ import annotations

import os
import sys

# ---------------------------------------------------------------------------
# Step 1: set env vars BEFORE any hmaps.* import.
# settings.py instantiates Settings() at module level; each field calls
# _env() in its default_factory — so env vars must be set now.
# ---------------------------------------------------------------------------

os.environ.setdefault("PYTHONUTF8", "1")

if getattr(sys, "frozen", False):
    # Running inside a PyInstaller --onedir bundle.
    # sys.executable == <dist>/hebrew-maps/hebrew-maps.exe
    # _internal/ (sys._MEIPASS) is a sibling of the EXE.
    _base = os.path.dirname(sys.executable)

    if not os.environ.get("HMAPS_DATA"):
        os.environ["HMAPS_DATA"] = os.path.join(_base, "data")
    if not os.environ.get("HMAPS_WEB"):
        os.environ["HMAPS_WEB"] = os.path.join(_base, "web")

    # No Docker routing in standalone mode; fall back to local graph.
    os.environ.setdefault("HMAPS_ROUTER", "local")

    # ---------------------------------------------------------------------------
    # Step 2: redirect stdout/stderr to a log file when there is no console
    # (PyInstaller windowed mode sets sys.stdout = None).
    # ---------------------------------------------------------------------------
    if sys.stdout is None or sys.stderr is None:
        import logging

        _log_path = os.path.join(_base, "hebrew-maps.log")
        logging.basicConfig(
            filename=_log_path,
            level=logging.WARNING,
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
        )

        class _FileStream:
            """Minimal write-only stream that routes to the log file."""

            def __init__(self, level: int) -> None:
                self._level = level
                self._logger = logging.getLogger("hmaps.stdout")

            def write(self, msg: str) -> int:
                msg = msg.rstrip()
                if msg:
                    self._logger.log(self._level, msg)
                return len(msg)

            def flush(self) -> None:
                pass

            # uvicorn/logging probe these on the stream; a log file is not a TTY.
            def isatty(self) -> bool:
                return False

            def writable(self) -> bool:
                return True

            encoding = "utf-8"
            errors = "replace"

        if sys.stdout is None:
            sys.stdout = _FileStream(logging.INFO)  # type: ignore[assignment]
        if sys.stderr is None:
            sys.stderr = _FileStream(logging.WARNING)  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Step 3: read port (after env vars are set, but before importing uvicorn or
# any hmaps module).
# ---------------------------------------------------------------------------

_port = int(os.environ.get("HMAPS_PORT", "8080"))
_host = os.environ.get("HMAPS_HOST", "127.0.0.1")


# ---------------------------------------------------------------------------
# Step 4: browser-open helper — polls /api/health then opens browser.
# Runs in a daemon thread so it does not block uvicorn startup.
# ---------------------------------------------------------------------------

def _open_browser_when_ready(host: str, port: int) -> None:
    """Poll /api/health up to 20 s then open the default browser."""
    import threading
    import time
    import urllib.request
    import webbrowser

    url = f"http://{host}:{port}"
    health = f"{url}/api/health"

    def _poll() -> None:
        started = False
        for _ in range(40):          # 40 × 500 ms = 20 s
            time.sleep(0.5)
            try:
                with urllib.request.urlopen(health, timeout=1):
                    started = True
                    break
            except Exception:        # noqa: BLE001
                continue
        if started:
            webbrowser.open(url)
        else:
            import logging
            logging.getLogger("hmaps.launcher").error(
                "Server did not respond at %s after 20 s — check hebrew-maps.log", health
            )

    t = threading.Thread(target=_poll, daemon=True)
    t.start()


# ---------------------------------------------------------------------------
# Step 5: start uvicorn.
# Using the string form "hmaps.api.app:app" so the import of hmaps.api.app
# (and thus the import of settings.py) is deferred until uvicorn starts the
# ASGI server — at which point the env vars above are already in place.
# ---------------------------------------------------------------------------

def main() -> None:
    import uvicorn

    _open_browser_when_ready(_host, _port)

    config = uvicorn.Config(
        "hmaps.api.app:app",
        host=_host,
        port=_port,
        log_level="warning",
        use_colors=False,
    )
    server = uvicorn.Server(config)
    server.run()


if __name__ == "__main__":
    if sys.argv[1:2] == ["update"]:  # python -m hmaps update [--check] [--component X]
        from hmaps.api.updates import cli

        sys.exit(cli(sys.argv[2:]))
    main()
