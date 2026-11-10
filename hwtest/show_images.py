"""Show a list of 480x360 images on the screen in turn (realtime slot), then leave.

Usage: sdk/.venv/Scripts/python -B hwtest/show_images.py --seconds 12 --rounds 2 img1.png img2.png ...
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sdk/src"))
from neos75screen import Screen  # noqa: E402
from neos75screen.transport import open_transport  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("images", nargs="+", type=Path)
    parser.add_argument("--seconds", type=float, default=12)
    parser.add_argument("--rounds", type=int, default=1)
    parser.add_argument("--quality", type=int, default=92)
    args = parser.parse_args()
    with Screen(open_transport()) as screen:
        hello = screen.hello()
        print(f"patch {hello.patch_version}, canvas {hello.canvas_w}x{hello.canvas_h}")
        screen.enter()
        for r in range(args.rounds):
            for img in args.images:
                result = screen.show(img, quality=args.quality)
                print(f"{time.strftime('%H:%M:%S')} {img.name}: shown={result.shown} hidden={result.hidden} decode={result.decode_ms} ms")
                time.sleep(args.seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
