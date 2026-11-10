"""Offline checks of a page (no service, no device): load it in headless Chrome like the runner does, feed
it data, and report what would go wrong on the screen. Used by `neos75ctl check` and `neos75ctl new`."""
from __future__ import annotations

import time
from pathlib import Path

from ..live import library
from . import catalog

TEMPLATE = Path(__file__).parent / "templates" / "page.html"
CUSTOM_DIR = library.CUSTOM_DIR
SAMPLE = {"cpu": 37, "gpu": 52, "mem": 61, "temp": 58, "disk": 34, "net": 12.4, "title": "Aerial Cartography",
          "artist": "Low Tide Orchestra", "pos": 134, "dur": 338, "playing": True, "has_media": True,
          "kb_battery": 86, "kb_charging": False, "kb_link": 4}


def new_page(name: str, title: str, description: str = "", folder: Path | None = None, force: bool = False) -> Path:
    """Write a new page from the template into design/custom (or `folder`)."""
    if not name.replace("_", "").replace("-", "").isalnum() or not name[0].isalpha():
        raise ValueError("name: letters, digits, '_' or '-', starting with a letter")
    folder = folder or CUSTOM_DIR
    path = folder / f"{name}.html"
    if path.exists() and not force:
        raise ValueError(f"{path} exists (use --force to overwrite)")
    if name in library.widgets() and library.widgets()[name] != path.resolve():
        raise ValueError(f"a widget named {name!r} already exists: {library.widgets()[name]}")
    html = TEMPLATE.read_text(encoding="utf-8")
    html = html.replace("{{TITLE}}", _attr(title)).replace("{{DESCRIPTION}}", _attr(description))
    folder.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return path


def _attr(text: str) -> str:
    return text.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")


def check(entry: str, out: Path | None = None, label: str = "Page 7", dirs=()) -> dict:  # noqa: ANN001
    """Load `entry` (a widget name or .html path, with optional ?options); returns a report dict:
    ok, problems (list), warnings, console errors, metadata, size, and the PNG path when `out` is given."""
    from playwright.sync_api import sync_playwright
    widget = library.resolve([entry], dirs)[0]
    meta = catalog.describe(widget.stem, widget.path, dirs)
    problems: list[str] = []
    warnings: list[str] = []
    console: list[str] = []
    with sync_playwright() as pw:
        browser = library.launch_browser(pw, ["--allow-file-access-from-files", "--autoplay-policy=no-user-gesture-required"])
        page = browser.new_page(viewport={"width": 480, "height": 360}, device_scale_factor=1)
        page.on("console", lambda m: console.append(f"{m.type}: {m.text}") if m.type in ("error", "warning") else None)
        page.on("pageerror", lambda e: problems.append(f"script error: {e}"))
        failed: list[str] = []
        page.on("requestfailed", lambda r: failed.append(r.url))
        page.goto(widget.url, wait_until="load", timeout=20000)
        try:
            page.wait_for_function("document.fonts && document.fonts.status === 'loaded'", timeout=5000)
        except Exception:  # noqa: BLE001
            warnings.append("web fonts still loading after 5 s")
        has_update = page.evaluate("() => typeof window.neos75Update === 'function'")
        if not has_update:
            warnings.append("no window.neos75Update: the page gets no live data (include ../widgets/data.js)")
        t0 = time.monotonic()
        try:
            page.evaluate("d => window.neos75Update && window.neos75Update(d)",
                          {"now": time.time() * 1000, "page_label": label, **SAMPLE})
        except Exception as exc:  # noqa: BLE001
            problems.append(f"neos75Update threw: {exc}")
        update_ms = round((time.monotonic() - t0) * 1000, 1)
        time.sleep(0.6)
        size = page.evaluate("() => [document.documentElement.scrollWidth, document.documentElement.scrollHeight]")
        if size[0] > 480 or size[1] > 360:
            warnings.append(f"content is {size[0]}x{size[1]}: anything outside 480x360 is cut off")
        png = page.screenshot(type="png")
        browser.close()
    for url in failed:
        problems.append(f"failed to load {url}")
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(png)
    if meta["title"] == widget.stem:
        warnings.append('no <meta name="neos75:title">: the app shows the file name')
    return {"ok": not problems, "entry": entry, "path": str(widget.path), "url": widget.url, "problems": problems,
            "warnings": warnings, "console": console, "has_update": has_update, "update_ms": update_ms,
            "content_size": size, "meta": meta, "png": str(out) if out else None}
