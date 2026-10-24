"""The dashboard and quick-check pages, loaded from the bundled static folder.

The HTML holds two placeholders that the server fills per request: __NONCE__ (the CSP nonce
for script tags) and __TOKEN__ (the API token, JSON encoded). Everything else is static.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parent / "static"

# Files the server may send. Anything not listed here is never served.
ASSET_TYPES = {
    "app.css": "text/css; charset=utf-8",
    "app.js": "text/javascript; charset=utf-8",
    "quick.js": "text/javascript; charset=utf-8",
    "common.js": "text/javascript; charset=utf-8",
    "icon.svg": "image/svg+xml",
}


def _read(name: str) -> str:
    return (STATIC_DIR / name).read_text(encoding="utf-8")


PAGE = _read("index.html")
QUICK_PAGE = _read("quick.html")


@lru_cache(maxsize=1)
def static_assets() -> dict[str, tuple[bytes, str]]:
    return {name: ((STATIC_DIR / name).read_bytes(), kind) for name, kind in ASSET_TYPES.items()}


def all_sources() -> dict[str, str]:
    """Every page and script, for the tests that forbid HTML injection APIs."""
    names = ["index.html", "quick.html"] + [n for n in ASSET_TYPES if n.endswith((".js", ".css"))]
    return {name: _read(name) for name in names}
