"""Dirty-region detection for region frames."""
from __future__ import annotations

from PIL import Image, ImageDraw

from neos75screen.live.regions import dirty_regions


def canvas() -> Image.Image:
    return Image.new("RGB", (480, 360), (10, 10, 12))


def changed(box) -> Image.Image:  # noqa: ANN001
    img = canvas()
    ImageDraw.Draw(img).rectangle(box, fill=(255, 255, 255))
    return img


def valid(rects) -> bool:  # noqa: ANN001
    return all(w % 16 == 0 and h % 16 == 0 and x % 2 == 0 and y % 2 == 0 and x + w <= 480 and y + h <= 360
               for x, y, w, h in rects)


def test_no_previous_or_no_change() -> None:
    assert dirty_regions(None, canvas()) is None
    assert dirty_regions(canvas(), canvas()) == []


def test_small_change_is_one_aligned_region_covering_it() -> None:
    rects = dirty_regions(canvas(), changed((150, 147, 460, 180)))
    assert rects == [(144, 144, 320, 48)] and valid(rects)


def test_two_separate_changes_are_two_regions() -> None:
    img = changed((20, 20, 40, 40))
    ImageDraw.Draw(img).rectangle((400, 300, 420, 320), fill=(255, 0, 0))
    rects = dirty_regions(canvas(), img)
    assert len(rects) == 2 and valid(rects)
    assert any(x <= 20 and y <= 20 for x, y, _, _ in rects) and any(x <= 400 <= x + w for x, _, w, _ in rects)


def test_bottom_edge_keeps_whole_tiles_inside_the_canvas() -> None:
    rects = dirty_regions(canvas(), changed((10, 355, 20, 359)))
    assert rects == [(0, 344, 32, 16)] and valid(rects)


def test_large_change_prefers_a_full_frame() -> None:
    assert dirty_regions(canvas(), changed((0, 0, 400, 300))) is None


def test_a_single_changed_pixel_is_found() -> None:
    img = canvas()
    img.putpixel((237, 101), (11, 10, 12))          # one channel, one step
    rects = dirty_regions(canvas(), img)
    assert rects == [(224, 96, 16, 16)]
