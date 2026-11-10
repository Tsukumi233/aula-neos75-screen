"""Animated GIF (or APNG / WebP) player that keeps the file's timing by skipping late frames."""
from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Union

from PIL import Image, ImageSequence

from ._common import wait_until

DEFAULT_DURATION_MS = 100   # what browsers use for a missing or zero delay
MIN_DURATION_MS = 20

Encoder = Callable[[Image.Image], bytes]


def _load(path: str | Path) -> list[tuple[Image.Image, float]]:
    with Image.open(path) as im:
        frames = []
        for frame in ImageSequence.Iterator(im):
            duration = frame.info.get("duration") or DEFAULT_DURATION_MS
            frames.append((frame.convert("RGBA"), max(duration, MIN_DURATION_MS) / 1000))
    return frames


def gif_frames(path: str | Path, loops: int = 0,
               encode: Encoder | None = None) -> Iterator[Union[Image.Image, bytes]]:
    """Yield the animation's frames on schedule; `loops` 0 repeats forever.

    With `encode` (for example `screen.encode`), every frame is encoded once up front and JPEG bytes are
    yielded, so replays cost no CPU. A frame whose display slot has already passed is skipped.
    """
    frames = _load(path)
    payloads: list[Union[Image.Image, bytes]] = [encode(img) if encode else img for img, _ in frames]
    played = 0
    due = time.monotonic()
    while loops == 0 or played < loops:
        for payload, (_, duration) in zip(payloads, frames):
            if time.monotonic() > due + duration:
                due += duration
                continue
            wait_until(due)
            due += duration
            yield payload
        played += 1
