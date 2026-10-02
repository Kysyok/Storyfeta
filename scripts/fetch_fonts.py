"""Download the app's fonts as self-hosted woff2 files into app/static/fonts/.

Run once (``python scripts/fetch_fonts.py``); the Docker build runs it for you.
This is a build-time download only: at runtime the browser loads the fonts from
this app, never from a Google CDN. The @font-face rules that use these files are
already in app/static/css/app.css. If a file is missing the site falls back to a
system font (cursive for Caveat, monospace for LXGW WenKai Mono TC).

Fonts
-----
* Caveat (handwriting): story lines and the logo-style accents.
* LXGW WenKai Mono TC (Latin subset only): buttons, titles and labels in the design.
"""

from __future__ import annotations

import re
import sys
import urllib.request
from pathlib import Path

# A modern browser User-Agent makes Google serve woff2 files.
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
FONT_DIR = Path(__file__).resolve().parent.parent / "app" / "static" / "fonts"

# prefix -> (Google Fonts CSS URL, subsets to keep). File names: <prefix>-<weight>-<subset>.woff2
FAMILIES = {
    "caveat": (
        "https://fonts.googleapis.com/css2?family=Caveat:wght@400..700&display=swap",
        {"latin", "latin-ext", "cyrillic", "cyrillic-ext"},
    ),
    "lxgw": (
        "https://fonts.googleapis.com/css2?family=LXGW+WenKai+Mono+TC:wght@400;700&display=swap",
        {"latin", "latin-ext"},
    ),
}

# Each block: /* subset */ @font-face { ... font-weight: 700; ... src: url(https://...woff2) ... }
BLOCK = re.compile(r"/\*\s*([\w\[\]-]+)\s*\*/\s*@font-face\s*{([^}]*)}")


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - fixed https URLs
        return response.read()


def parse_blocks(css: str) -> list[tuple[str, str, str]]:
    """Return ``(subset, weight, url)`` for every @font-face block that has a woff2 URL."""
    found = []
    for subset, body in BLOCK.findall(css):
        url = re.search(r"url\((https://[^)]+\.woff2)\)", body)
        weight = re.search(r"font-weight:\s*([\d ]+);", body)
        if url:
            found.append((subset, (weight.group(1).strip() if weight else "400").replace(" ", "-"), url.group(1)))
    return found


def main(only_if_missing: bool = False) -> int:
    FONT_DIR.mkdir(parents=True, exist_ok=True)
    status = 0
    for prefix, (css_url, wanted) in FAMILIES.items():
        if only_if_missing and any(FONT_DIR.glob(f"{prefix}-*-latin.woff2")):
            print(f"{prefix}: already present.")
            continue
        try:
            blocks = parse_blocks(fetch(css_url).decode())
        except OSError as exc:
            print(f"{prefix}: download failed ({exc})", file=sys.stderr)
            status = 1
            continue
        saved = 0
        for subset, weight, url in blocks:
            if subset in wanted:
                (FONT_DIR / f"{prefix}-{weight}-{subset}.woff2").write_bytes(fetch(url))
                print(f"saved {prefix}-{weight}-{subset}.woff2")
                saved += 1
        if saved == 0:
            print(f"{prefix}: no font files found in the response.", file=sys.stderr)
            status = 1
    return status


if __name__ == "__main__":
    sys.exit(main("--if-missing" in sys.argv))
