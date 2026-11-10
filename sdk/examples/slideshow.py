"""Show every image of a directory in turn.

    python slideshow.py DIR [--interval 5] [--fit cover] [--once]
"""
from __future__ import annotations

import argparse

from neos75screen import Screen
from neos75screen.apps import slideshow_frames


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("directory")
    parser.add_argument("--interval", type=float, default=5.0, help="seconds per image")
    parser.add_argument("--fit", choices=("contain", "cover", "stretch"), default="cover")
    parser.add_argument("--once", action="store_true", help="stop after the last image")
    args = parser.parse_args()

    with Screen.open() as screen:
        if not screen.realtime:
            raise SystemExit("stock firmware: realtime display unavailable")
        try:
            stats = screen.stream(slideshow_frames(args.directory, args.interval, loop=not args.once),
                                  fit=args.fit, quality=90)
            print(f"shown {stats.shown} of {stats.sent} images")
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
