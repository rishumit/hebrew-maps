"""installer/make_icon.py

Generate installer/icon.ico from the first suitable PNG found in web/icons/,
falling back to a generated 256×256 blue square if no PNG is available.

Called by build.ps1 before running PyInstaller.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEST = Path(__file__).resolve().parent / "icon.ico"

# Candidate PNGs in preference order (largest first for best quality)
_CANDIDATES = [
    ROOT / "web" / "icons" / "icon-512.png",
    ROOT / "web" / "icons" / "icon-192.png",
    ROOT / "web" / "icons" / "icon-maskable-512.png",
    ROOT / "web" / "icons" / "favicon.png",
    ROOT / "web" / "icons" / "icon.png",
]


def main() -> None:
    from PIL import Image  # Pillow is in requirements.txt

    # Find the first existing candidate
    src = next((p for p in _CANDIDATES if p.is_file()), None)

    # Also scan the whole icons dir as a last resort
    if src is None:
        icons_dir = ROOT / "web" / "icons"
        if icons_dir.is_dir():
            pngs = sorted(icons_dir.glob("*.png"), key=lambda p: p.stat().st_size, reverse=True)
            src = pngs[0] if pngs else None

    if src:
        img = Image.open(src).convert("RGBA").resize((256, 256), Image.LANCZOS)
        print(f"  icon source: {src.name}")
    else:
        # Fallback: solid blue square
        img = Image.new("RGBA", (256, 256), (0, 90, 180, 255))
        print("  icon source: generated blue square (no PNG found in web/icons/)")

    img.save(
        DEST,
        format="ICO",
        sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)],
    )
    print(f"  icon saved:  {DEST}")


if __name__ == "__main__":
    main()
