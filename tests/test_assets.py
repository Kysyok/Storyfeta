"""Static URLs carry a version so browsers never reuse a stale script."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from app.core.assets import STATIC_DIR, static_url


def test_url_changes_when_the_file_changes():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "a.js").write_text("1")
        os.utime(root / "a.js", (1000, 1000))
        first = static_url("a.js", root)
        os.utime(root / "a.js", (2000, 2000))
        assert first == "/static/a.js?v=1000"
        assert static_url("a.js", root) == "/static/a.js?v=2000"


def test_missing_file_gives_plain_url():
    assert static_url("nope.js", Path("/nonexistent")) == "/static/nope.js"


def test_real_assets_are_versioned():
    for name in ("css/app.css", "js/room.js", "img/logo.svg"):
        assert static_url(name).startswith(f"/static/{name}?v="), name
    assert (STATIC_DIR / "js" / "room.js").exists()
