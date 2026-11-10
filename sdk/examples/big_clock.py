"""Large clock: weekday, time, date and a seconds bar.

    python big_clock.py                                 stream to the screen, laid out for its HELLO size
    python big_clock.py --preview [--size 320x320]      write one frame to clock.png (default 480x360)
"""
from __future__ import annotations

import argparse
from datetime import datetime

from neos75screen import Screen
from neos75screen.apps import PREVIEW_SIZE, clock_frames, render_clock


def parse_size(text: str) -> tuple[int, int]:
    width, height = text.lower().split("x")
    return int(width), int(height)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-seconds", action="store_true", help="update once a minute")
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--size", type=parse_size, default=PREVIEW_SIZE, help="preview size, WxH")
    args = parser.parse_args()

    if args.preview:
        render_clock(datetime.now(), args.size, seconds=not args.no_seconds).save("clock.png")
        print("wrote clock.png")
        return
    with Screen.open() as screen:
        if not screen.realtime:
            raise SystemExit("stock firmware: realtime display unavailable")
        screen.sync_time()  # keeps the stock clock page right as well (no flash write)
        try:
            screen.stream(clock_frames(screen.capabilities.image_size, seconds=not args.no_seconds))
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
