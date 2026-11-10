"""Shared drawing and timing helpers for the bundled apps."""
from __future__ import annotations

import threading
import time
from collections.abc import Sequence
from functools import lru_cache
from typing import Union

from PIL import ImageDraw, ImageFont

from .. import constants as c
from ..protocol import KnobEvent, VisibilityEvent

PREVIEW_SIZE = c.PANEL_SIZE  # used when no device reports its size
BACKGROUND = (8, 10, 16)
FOREGROUND = (235, 238, 245)
MUTED = (120, 128, 145)
TRACK = (40, 44, 56)
ACCENTS = {"cpu": (64, 160, 255), "ram": (170, 110, 255), "gpu": (60, 200, 120), "net": (255, 170, 60),
           "disk": (240, 90, 120)}

_REGULAR = ("DejaVuSans.ttf", "arial.ttf", "segoeui.ttf", "Helvetica.ttc")
_BOLD = ("DejaVuSans-Bold.ttf", "arialbd.ttf", "segoeuib.ttf", "Helvetica.ttc")

Font = ImageFont.FreeTypeFont | ImageFont.ImageFont


@lru_cache(maxsize=32)
def font(size: int, bold: bool = False) -> Font:
    """A TrueType font from the usual system locations, or Pillow's built-in font."""
    for name in _BOLD if bold else _REGULAR:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def fitting_font(draw: ImageDraw.ImageDraw, text: str, size: int, max_width: int, bold: bool = False) -> Font:
    """The largest font up to `size` whose rendering of `text` fits in `max_width` pixels."""
    while size > 8:
        candidate = font(size, bold)
        left, _, right, _ = draw.textbbox((0, 0), text, font=candidate)
        if right - left <= max_width:
            return candidate
        size -= 2
    return font(size, bold)


def scale(size: tuple[int, int]) -> float:
    """Layout scale: 1.0 on a 320 x 320 image, 1.125 on the 480 x 360 panel."""
    return min(size) / 320


class ViewCycle:
    """Switches between named views on knob input; register `on_event` with `Screen.on_knob` and
    `Screen.on_visibility`. Turning the knob while the realtime page is visible moves to a stock page (the
    knob keeps its stock role), so the view advances when the user comes back onto the realtime slot with
    the knob, or on a knob press if the firmware reports presses."""

    def __init__(self, views: Sequence[str]) -> None:
        if not views:
            raise ValueError("at least one view is needed")
        self.views = tuple(views)
        self._index = 0
        self._lock = threading.Lock()

    @property
    def current(self) -> str:
        return self.views[self._index]

    def advance(self) -> None:
        with self._lock:
            self._index = (self._index + 1) % len(self.views)

    def on_event(self, event: Union[KnobEvent, VisibilityEvent]) -> None:
        if isinstance(event, KnobEvent):
            if event.direction == 0:
                self.advance()
        elif event.visible and event.reason == c.VISIBILITY_REASON_KNOB:
            self.advance()


def wait_until(deadline: float) -> None:
    """Sleep until time.monotonic() reaches `deadline`."""
    delay = deadline - time.monotonic()
    if delay > 0:
        time.sleep(delay)
