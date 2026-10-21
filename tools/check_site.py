"""Checks the static product site (site/) before it is published to GitHub Pages.

    python tools/check_site.py site

* every local href / src / srcset / <use href> target exists (files, and #anchors in pages / ids in SVG sprites);
* absolute links to the project site (/hebrew-maps/…, as 404.html uses) resolve inside site/;
* every page has <html lang="he" dir="rtl">, a <title>, a meta description and exactly one <h1>;
* every <img> has alt, width and height.
Exit code 1 with a list of problems, 0 when everything is fine. Standard library only.
"""

from __future__ import annotations

import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

PREFIX = "/hebrew-maps/"


class Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: set[str] = set()
        self.refs: list[tuple[str, str]] = []  # (attribute, url)
        self.html_attrs: dict[str, str] = {}
        self.title = False
        self.description = False
        self.h1 = 0
        self.img_problems: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if "id" in a:
            self.ids.add(a["id"])
        if tag == "html":
            self.html_attrs = a
        elif tag == "title":
            self.title = True
        elif tag == "meta" and a.get("name") == "description" and a.get("content"):
            self.description = True
        elif tag == "h1":
            self.h1 += 1
        if tag == "img":
            missing = [k for k in ("alt", "width", "height") if k not in a]
            if missing:
                self.img_problems.append(f"<img src={a.get('src')!r}> without {', '.join(missing)}")
        for attr in ("href", "src"):
            if attr in a and tag not in ("meta",):
                if tag == "link" and a.get("rel") in ("canonical", "alternate"):
                    continue
                self.refs.append((f"{tag}[{attr}]", a[attr]))
        for attr in ("srcset",):
            if attr in a:
                for part in a[attr].split(","):
                    url = part.strip().split(" ")[0]
                    if url:
                        self.refs.append((f"{tag}[srcset]", url))


def svg_ids(path: Path) -> set[str]:
    p = Page()
    p.feed(path.read_text(encoding="utf-8"))
    return p.ids


def main(root: Path) -> int:
    root = root.resolve()
    pages = {p: Page() for p in sorted(root.rglob("*.html"))}
    for path, page in pages.items():
        page.feed(path.read_text(encoding="utf-8"))
    problems: list[str] = []
    for path, page in pages.items():
        rel = path.relative_to(root)
        if page.html_attrs.get("lang") != "he" or page.html_attrs.get("dir") != "rtl":
            problems.append(f"{rel}: <html> must have lang=he dir=rtl")
        if not page.title:
            problems.append(f"{rel}: no <title>")
        if not page.description:
            problems.append(f"{rel}: no meta description")
        if page.h1 != 1:
            problems.append(f"{rel}: {page.h1} <h1> elements (want 1)")
        problems += [f"{rel}: {m}" for m in page.img_problems]
        for what, url in page.refs:
            parts = urlsplit(url)
            if parts.scheme in ("http", "https", "mailto", "data", "tel") or url.startswith("//"):
                continue
            if parts.path.startswith(PREFIX):
                target = root / unquote(parts.path[len(PREFIX):])
            elif parts.path.startswith("/"):
                problems.append(f"{rel}: {what} {url!r} is root-absolute (breaks under {PREFIX})")
                continue
            elif parts.path:
                target = (path.parent / unquote(parts.path)).resolve()
            else:
                target = path
            if target.is_dir():
                target = target / "index.html"
            if not target.exists():
                problems.append(f"{rel}: {what} {url!r} -> missing {target.relative_to(root) if target.is_relative_to(root) else target}")
                continue
            if parts.fragment:
                if target.suffix == ".html":
                    ids = pages[target].ids if target in pages else set()
                elif target.suffix == ".svg":
                    ids = svg_ids(target)
                else:
                    ids = None
                if ids is not None and parts.fragment not in ids:
                    problems.append(f"{rel}: {what} {url!r} -> no #{parts.fragment} in {target.relative_to(root)}")
    for p in problems:
        print("✗", p)
    print(f"{len(pages)} pages checked, {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1] if len(sys.argv) > 1 else "site")))
