"""Widget library and the page selection.

Every HTML file in a library folder is a widget, named by its file stem (`neos_clock`). A selection entry
may add options as a query string, which the widget reads from its URL (`neos_photo?src=D:/a.jpg&full=1`), so
one widget can be several pages. The selection is a
small JSON file, `{"pages": ["neos_clock", "neos_system", ...]}`: which widgets are pages of the screen
knob's cycle and in what order (at most 8, the firmware's host page limit). The widget runner reloads it
while it runs, so the pages can be changed at any time without touching the firmware; the desktop app, the
CLI (`neos75 pages`) and agents all edit the same file.
"""
from __future__ import annotations

import json
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from .. import constants as c

# The folder holding `design/` (and `sdk/tests/fake_device.py`): the repository for an editable install; the
# installed desktop app sets NEOS75_ROOT to its resources folder, which carries a copy of the pages.
ROOT = Path(os.environ.get("NEOS75_ROOT") or Path(__file__).resolve().parents[4])
# Your own pages (new pages written by you or an agent). They sit next to `design/neos` so `../neos/neos.css`,
# `../widgets/data.js` and the artwork resolve the same way; the installed app points NEOS75_CUSTOM at the
# repository's `design/custom` it was built from, so pages written there show up without a rebuild.
CUSTOM_DIR = Path(os.environ.get("NEOS75_CUSTOM") or ROOT / "design" / "custom")
# Néos pages, the classic set, then your own pages
LIBRARY_DIRS = [ROOT / "design" / "neos", ROOT / "design" / "widgets", CUSTOM_DIR]
DEFAULT_PAGES = ["neos_clock", "neos_system", "neos_player"]
STOCK_PAGES = 6                                      # the first host page is "Page 7"


@dataclass(frozen=True)
class Widget:
    """A selected page: the widget file and its options (query string, may be empty)."""

    path: Path
    query: str = ""

    @property
    def name(self) -> str:
        return self.path.stem + (f"?{self.query}" if self.query else "")

    @property
    def stem(self) -> str:
        return self.path.stem

    @property
    def url(self) -> str:
        return self.path.as_uri() + (f"?{quote(self.query, safe='=&:/,.-_~%')}" if self.query else "")


def launch_browser(pw, args: Sequence[str]):  # noqa: ANN001, ANN201
    """Headless Chromium for rendering pages: the system Chrome, else Edge (always on Windows 10/11), else
    Playwright's own Chromium (`playwright install chromium`)."""
    error: Exception | None = None
    for channel in ("chrome", "msedge", None):
        try:
            return pw.chromium.launch(channel=channel, headless=True, args=list(args))
        except Exception as exc:  # noqa: BLE001 - this browser is not installed: try the next
            error = exc
    raise RuntimeError(f"no browser to render pages (Chrome, Edge or Playwright's Chromium): {error}")


def default_config() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    return Path(base) / "neos75screen" / "pages.json"


def widgets(dirs: Sequence[Path] = ()) -> dict[str, Path]:
    """name -> HTML file for every widget in the library folders (later folders win on a name clash)."""
    found: dict[str, Path] = {}
    for folder in [*LIBRARY_DIRS, *dirs]:
        if folder.is_dir():
            for path in sorted(folder.glob("*.html")):
                found[path.stem] = path.resolve()
    return found


def resolve(names: Sequence[str], dirs: Sequence[Path] = ()) -> list[Widget]:
    """Widget names (or paths to HTML files), each with optional `?options`, to widgets; raises ValueError
    for unknown names or too many."""
    if not names:
        raise ValueError("select at least one page")
    if len(names) > c.MAX_HOST_PAGES:
        raise ValueError(f"at most {c.MAX_HOST_PAGES} pages (the screen knob cycle has room for {c.MAX_HOST_PAGES})")
    library = widgets(dirs)
    selected = []
    for entry in names:
        name, _, query = entry.partition("?")
        if name in library:
            selected.append(Widget(library[name], query))
        elif name.endswith(".html") and Path(name).is_file():
            selected.append(Widget(Path(name).resolve(), query))
        else:
            raise ValueError(f"unknown widget {name!r}; available: {', '.join(sorted(library))}")
    return selected


def load(config: Path) -> list[str]:
    """The selected page names; the default selection if the file does not exist yet."""
    if not config.is_file():
        return list(DEFAULT_PAGES)
    data = json.loads(config.read_text(encoding="utf-8"))
    pages = data.get("pages")
    if not isinstance(pages, list) or not all(isinstance(p, str) for p in pages):
        raise ValueError(f"{config}: 'pages' must be a list of widget names")
    return pages


def save(config: Path, names: Sequence[str], dirs: Sequence[Path] = ()) -> None:
    resolve(names, dirs)  # validate before writing
    config.parent.mkdir(parents=True, exist_ok=True)
    tmp = config.with_suffix(".tmp")
    tmp.write_text(json.dumps({"pages": list(names)}, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(config)  # atomic: a running runner never reads a half-written file


def page_label(index: int) -> str:
    return f"Page {STOCK_PAGES + 1 + index}"
