"""Play an animated GIF (or APNG / animated WebP) at its own timing.

Frames are encoded once up front; frames that cannot be sent in time are skipped, so the animation keeps
its speed even when the HID link is slower than the GIF (roughly 3 to 5 small frames per second).

    python gif_player.py PATH [--loops 0] [--quality 70]
"""
from __future__ import annotations

import argparse

from PIL import Image

from neos75screen import Screen
from neos75screen.apps import gif_frames


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path")
    parser.add_argument("--loops", type=int, default=0, help="0 repeats forever")
    parser.add_argument("--fit", choices=("contain", "cover", "stretch"), default="contain")
    parser.add_argument("--quality", type=int, default=70, help="lower is smaller and faster")
    args = parser.parse_args()

    with Screen.open() as screen:
        if not screen.realtime:
            raise SystemExit("stock firmware: realtime display unavailable")

        def encode(img: Image.Image) -> bytes:
            return screen.encode(img, fit=args.fit, quality=args.quality)

        try:
            stats = screen.stream(gif_frames(args.path, args.loops, encode))
            print(f"sent {stats.sent}, shown {stats.shown}, dropped {stats.dropped}, timeouts {stats.timeouts}")
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
