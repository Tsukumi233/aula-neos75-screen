"""Which parts of a widget frame changed: rectangles for region frames (`Screen.show_region`).

The frame is compared with the last frame sent, in 16x16 tiles (whole JPEG MCUs, as region frames require).
Changed tiles are grouped into connected clusters; each cluster's bounding box is one region. When the
regions together are a large share of the screen a full frame is cheaper, and the caller sends that instead.
"""
from __future__ import annotations

from PIL import Image, ImageChops

TILE = 16
FULL_SHARE = 0.45          # changed area above this share of the screen: send a full frame
MAX_REGIONS = 4            # more clusters than this: send a full frame
MAX_PIXELS = 40960         # firmware limit per region (81920 bytes beside the canvas; the page cache follows)

Rect = tuple[int, int, int, int]   # x, y, w, h


def changed_tiles(previous: Image.Image, current: Image.Image) -> set[tuple[int, int]]:
    """(column, row) of every 16x16 tile that differs (the last row may be a partial tile)."""
    # RGB, not greyscale: a one-step change in one channel would round to 0 in L
    diff = ImageChops.difference(previous.convert("RGB"), current.convert("RGB"))
    box = diff.getbbox()
    if box is None:
        return set()
    # Exact per tile (an average would round a one-pixel change away): bands of 16 rows first, then the
    # tiles inside each changed band's own bounding box.
    w, h = current.size
    tiles = set()
    for r in range(box[1] // TILE, -(-box[3] // TILE)):
        band = diff.crop((0, r * TILE, w, min(h, (r + 1) * TILE))).getbbox()
        if band is None:
            continue
        for c in range(band[0] // TILE, -(-band[2] // TILE)):
            x0 = c * TILE
            if diff.crop((x0, r * TILE, min(w, x0 + TILE), min(h, (r + 1) * TILE))).getbbox():
                tiles.add((c, r))
    return tiles


def _clusters(tiles: set[tuple[int, int]]) -> list[set[tuple[int, int]]]:
    left, groups = set(tiles), []
    while left:
        stack, group = [left.pop()], set()
        while stack:
            c, r = stack.pop()
            group.add((c, r))
            for n in ((c + dc, r + dr) for dc in (-1, 0, 1) for dr in (-1, 0, 1)):
                if n in left:
                    left.remove(n)
                    stack.append(n)
        groups.append(group)
    return groups


def _rect(group: set[tuple[int, int]], size: tuple[int, int]) -> Rect:
    w, h = size
    x0 = min(c for c, _ in group) * TILE
    y0 = min(r for _, r in group) * TILE
    x1 = min(w, (max(c for c, _ in group) + 1) * TILE)
    y1 = (max(r for _, r in group) + 1) * TILE
    if y1 > h:                       # 360 is not a multiple of 16: keep whole tiles, move the box up
        y0, y1 = y0 - (y1 - h), h
    return x0, y0, x1 - x0, y1 - y0


def _merge(rects: list[Rect]) -> list[Rect]:
    """Merge overlapping boxes (clusters can overlap after rounding) until none overlap."""
    rects = list(rects)
    merged = True
    while merged:
        merged = False
        for i in range(len(rects)):
            for j in range(i + 1, len(rects)):
                a, b = rects[i], rects[j]
                if a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]:
                    x0, y0 = min(a[0], b[0]), min(a[1], b[1])
                    x1, y1 = max(a[0] + a[2], b[0] + b[2]), max(a[1] + a[3], b[1] + b[3])
                    rects[i] = (x0, y0, x1 - x0, y1 - y0)
                    del rects[j]
                    merged = True
                    break
            if merged:
                break
    return rects


def dirty_regions(previous: Image.Image | None, current: Image.Image) -> list[Rect] | None:
    """[] if nothing changed, the regions to send, or None when a full frame is the better choice."""
    if previous is None or previous.size != current.size:
        return None
    tiles = changed_tiles(previous, current)
    if not tiles:
        return []
    rects = _merge([_rect(g, current.size) for g in _clusters(tiles)])
    area = sum(w * h for _, _, w, h in rects)
    if len(rects) > MAX_REGIONS or area > FULL_SHARE * current.size[0] * current.size[1] \
            or any(w * h > MAX_PIXELS for _, _, w, h in rects):
        return None
    return sorted(rects, key=lambda r: (r[1], r[0]))
