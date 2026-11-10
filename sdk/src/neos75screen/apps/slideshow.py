"""Image slideshow over a directory."""
from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

from PIL import Image

from ..imaging import load_image
from ._common import wait_until

IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp", ".tif", ".tiff"})


def list_images(directory: str | Path) -> list[Path]:
    """Image files directly inside `directory`, sorted by name."""
    return sorted(path for path in Path(directory).iterdir()
                  if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES)


def slideshow_frames(directory: str | Path, interval: float = 5.0, loop: bool = True) -> Iterator[Image.Image]:
    """Each image for `interval` seconds; unreadable files are skipped."""
    paths = list_images(directory)
    if not paths:
        raise FileNotFoundError(f"no images in {directory}")
    next_at = time.monotonic()
    while True:
        for path in paths:
            try:
                img = load_image(path)
            except OSError:
                continue
            wait_until(next_at)
            next_at = time.monotonic() + interval
            yield img
        if not loop:
            return
