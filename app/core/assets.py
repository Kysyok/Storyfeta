"""Cache-busting URLs for static files.

``static_url("js/room.js")`` -> ``/static/js/room.js?v=1759123456``. The version is
the file's modification time, so browsers fetch a fresh copy whenever a file
changes and never run an old script against a new page. Swapping the logo or
background image in ``app/static/img`` works the same way, with no code change.
"""

from __future__ import annotations

from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def static_url(path: str, root: Path = STATIC_DIR) -> str:
    """URL for a file under ``static/``, versioned by mtime (unversioned if missing)."""
    url = f"/static/{path}"
    try:
        return f"{url}?v={int((root / path).stat().st_mtime)}"
    except OSError:
        return url
