"""Large digital clock: weekday, time, date and a seconds bar, laid out for any image size."""
from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import datetime

from PIL import Image, ImageDraw

from ._common import (ACCENTS, BACKGROUND, FOREGROUND, MUTED, PREVIEW_SIZE, TRACK, fitting_font, font, scale,
                      wait_until)


def render_clock(now: datetime, size: tuple[int, int] = PREVIEW_SIZE, seconds: bool = True) -> Image.Image:
    img = Image.new("RGB", size, BACKGROUND)
    draw = ImageDraw.Draw(img)
    width, height = size
    s = scale(size)
    cx = width / 2
    margin = round(24 * s)

    draw.text((cx, height * 0.15), now.strftime("%A"), font=font(round(28 * s)), fill=MUTED, anchor="mm")
    hhmm = now.strftime("%H:%M")
    big = fitting_font(draw, hhmm, round(height * 0.42), width - 2 * margin, bold=True)
    draw.text((cx, height * 0.45), hhmm, font=big, fill=FOREGROUND, anchor="mm")
    draw.text((cx, height * 0.71), f"{now.day} {now.strftime('%B %Y')}", font=font(round(28 * s)), fill=MUTED,
              anchor="mm")

    if seconds:
        label_font = font(round(24 * s), bold=True)
        bar_y, bar_h = height * 0.88, max(4, round(8 * s))
        right = width - margin - round(44 * s)
        draw.rounded_rectangle((margin, bar_y - bar_h / 2, right, bar_y + bar_h / 2), radius=bar_h / 2, fill=TRACK)
        filled = margin + (right - margin) * (now.second + now.microsecond / 1e6) / 60
        if filled > margin + bar_h:
            draw.rounded_rectangle((margin, bar_y - bar_h / 2, filled, bar_y + bar_h / 2), radius=bar_h / 2,
                                   fill=ACCENTS["cpu"])
        draw.text((width - margin, bar_y), now.strftime("%S"), font=label_font, fill=ACCENTS["cpu"], anchor="rm")
    return img


def clock_frames(size: tuple[int, int] = PREVIEW_SIZE, seconds: bool = True) -> Iterator[Image.Image]:
    """One frame per second (or per minute without seconds), aligned to the wall clock."""
    step = 1 if seconds else 60
    while True:
        now = datetime.now()
        yield render_clock(now, size, seconds)
        elapsed = now.second % step + now.microsecond / 1e6
        wait_until(time.monotonic() + step - elapsed - (datetime.now() - now).total_seconds())
