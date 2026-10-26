"""Render the release notes (docs/release-notes/vX.Y.Z.md) into the product site's "מה חדש" page.

Each notes file:   # Title            (first heading)
                   date: YYYY-MM-DD   (optional)
                   - bullet …         (list items; plain text, `code` and [links](url) allowed)
The same file is used by installer.yml for the in-app update notes (app-manifest.json).

The entries are written between <!-- releases:start --> and <!-- releases:end --> in
site/changelog.html, newest first; the hand-written history below the markers stays as is.
Run:  python tools/build_changelog.py [--check]
"""
from __future__ import annotations

import argparse
import html
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NOTES = ROOT / "docs" / "release-notes"
PAGE = ROOT / "site" / "changelog.html"
START, END = "<!-- releases:start -->", "<!-- releases:end -->"
MONTHS = ["ינואר", "פברואר", "מרץ", "אפריל", "מאי", "יוני", "יולי", "אוגוסט", "ספטמבר", "אוקטובר", "נובמבר", "דצמבר"]


def _version_key(p: Path) -> tuple:
    return tuple(int(x) if x.isdigit() else 0 for x in re.findall(r"\d+", p.stem))


def _inline(text: str) -> str:
    out = html.escape(text, quote=False)
    out = re.sub(r"`([^`]+)`", r'<code dir="ltr">\1</code>', out)
    return re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+|[\w./#-]+)\)",
                  lambda m: f'<a href="{html.escape(m.group(2))}">{m.group(1)}</a>', out)


def parse(path: Path) -> dict:
    title, date, items = None, None, []
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#") and title is None:
            title = line.lstrip("#").strip()
        elif line.lower().startswith("date:"):
            date = line.split(":", 1)[1].strip()
        elif line[:2] in ("- ", "* "):
            items.append(line[2:].strip())
        elif items:
            items[-1] += " " + line
    version = path.stem.lstrip("v")
    return {"version": version, "title": title or f"גרסה {version}", "date": date, "items": items}


def render(entries: list[dict]) -> str:
    out = []
    for e in entries:
        when = f"גרסה {e['version']}"
        if e["date"] and re.fullmatch(r"\d{4}-\d{2}-\d{2}", e["date"]):
            y, m, _ = e["date"].split("-")
            when = f'<time datetime="{e["date"]}">{MONTHS[int(m) - 1]} {y}</time> · {when}'
        items = "\n".join(f"                <li>{_inline(i)}</li>" for i in e["items"])
        out.append(f"""          <li>
            <p class="when">{when}</p>
            <div>
              <h2>{_inline(e['title'])}</h2>
              <ul>
{items}
              </ul>
            </div>
          </li>""")
    return "\n".join(out)


def build() -> str:
    page = PAGE.read_text(encoding="utf-8")
    if START not in page or END not in page:
        raise SystemExit(f"{PAGE}: markers {START} / {END} not found")
    files = sorted(NOTES.glob("v*.md"), key=_version_key, reverse=True)
    block = render([parse(f) for f in files])
    head, rest = page.split(START, 1)
    _, tail = rest.split(END, 1)
    return f"{head}{START}\n{block}\n          {END}{tail}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="exit 1 if site/changelog.html is out of date")
    args = ap.parse_args(argv)
    new = build()
    old = PAGE.read_text(encoding="utf-8")
    if args.check:
        if new != old:
            print("site/changelog.html is out of date – run: python tools/build_changelog.py")
            return 1
        print("changelog up to date")
        return 0
    if new != old:
        PAGE.write_text(new, encoding="utf-8")
        print("site/changelog.html updated")
    else:
        print("changelog up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
