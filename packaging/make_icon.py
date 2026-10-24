"""Render the SVG app icon to a multi-size Windows .ico.

The SVG (src/decision_agent/desktop/static/icon.svg) is the source of truth. Chromium (through
Playwright) rasterises it at every size so small sizes stay crisp, then Pillow packs the PNGs.
The generated app.ico is committed, so builds do not need Playwright.

    pip install playwright pillow && python -m playwright install chromium
    python packaging/make_icon.py
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SVG = ROOT / "src" / "decision_agent" / "desktop" / "static" / "icon.svg"
ICO = SVG.with_name("app.ico")
SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]


def render(svg: str, size: int, page) -> Image.Image:
    page.set_viewport_size({"width": size, "height": size})
    page.set_content(
        "<html><body style='margin:0;background:transparent'>"
        f"<div style='width:{size}px;height:{size}px'>{svg}</div></body></html>"
    )
    page.eval_on_selector("svg", f"e => {{ e.setAttribute('width', {size}); e.setAttribute('height', {size}); }}")
    png = page.screenshot(omit_background=True, clip={"x": 0, "y": 0, "width": size, "height": size})
    return Image.open(io.BytesIO(png)).convert("RGBA")


def main() -> None:
    from playwright.sync_api import sync_playwright

    svg = SVG.read_text(encoding="utf-8")
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(device_scale_factor=1)
        images = [render(svg, size, page) for size in SIZES]
        browser.close()
    largest = images[-1]
    largest.save(ICO, format="ICO", sizes=[(s, s) for s in SIZES], append_images=images[:-1])
    print(f"wrote {ICO}")


if __name__ == "__main__":
    main()
