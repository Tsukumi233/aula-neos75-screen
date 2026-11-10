from __future__ import annotations

import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))

from fake_device import FakeScreen  # noqa: E402

from neos75screen import MockTransport, Screen, Timeouts  # noqa: E402

FAST = Timeouts(reply=0.5, hello=0.05, ack=0.5, frame_done=0.3)

Connect = Callable[..., tuple[Screen, FakeScreen, MockTransport]]


@pytest.fixture
def connect() -> Iterator[Connect]:
    """Open a Screen on a fake device built with the given FakeScreen options (plus `keepalive`)."""
    opened: list[tuple[Screen, FakeScreen]] = []

    def factory(keepalive: bool = True, **options: object) -> tuple[Screen, FakeScreen, MockTransport]:
        fake = FakeScreen(**options)  # type: ignore[arg-type]
        transport = MockTransport(fake)
        fake.attach(transport)
        screen = Screen.open(transport, FAST, keepalive=keepalive)
        opened.append((screen, fake))
        return screen, fake, transport

    yield factory
    for screen, fake in opened:
        screen.close()
        fake.stop()


def gradient(size: tuple[int, int] = (320, 320)) -> Image.Image:
    """A smooth test image."""
    width, height = size
    pixels = bytes(v for y in range(height) for x in range(width) for v in (x * 255 // width, y * 255 // height, 128))
    return Image.frombytes("RGB", size, pixels)


def noise(size: tuple[int, int] = (320, 320), seed: int = 1) -> Image.Image:
    """A hard-to-compress test image (large JPEGs exercise the block ACK path)."""
    import random
    rng = random.Random(seed)
    return Image.frombytes("RGB", size, bytes(rng.getrandbits(8) for _ in range(size[0] * size[1] * 3)))


def detail(size: tuple[int, int] = (320, 320)) -> Image.Image:
    """Moderately compressible test image (smoothed noise)."""
    return noise((size[0] // 4, size[1] // 4)).resize(size, Image.Resampling.BICUBIC)
