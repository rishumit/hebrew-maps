"""installer/make_icon.py – kept for existing build scripts.

All icons (web PNGs, installer/icon.ico, src-tauri/icons/*) are rendered locally
from the master web/icons/icon.svg by tools/make_icons.py; see that file.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.make_icons import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
