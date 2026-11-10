"""System monitor dashboard (CPU, RAM, GPU when nvidia-ml-py is installed, network) on the screen.

    python sysmon_dashboard.py                          stream to the screen, laid out for its HELLO size
    python sysmon_dashboard.py --preview [--size 320x320]  write one frame to sysmon.png (default 480x360)
"""
from __future__ import annotations

import argparse
import time
from collections.abc import Iterator

from PIL import Image

from neos75screen import Screen
from neos75screen.apps import PREVIEW_SIZE, SYSMON_VIEWS, Sampler, ViewCycle, render_dashboard


def frames(sampler: Sampler, interval: float, size: tuple[int, int], views: ViewCycle) -> Iterator[Image.Image]:
    while True:
        started = time.monotonic()
        yield render_dashboard(sampler.sample(), size, views.current)
        time.sleep(max(0.0, interval - (time.monotonic() - started)))


def parse_size(text: str) -> tuple[int, int]:
    width, height = text.lower().split("x")
    return int(width), int(height)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--size", type=parse_size, default=PREVIEW_SIZE, help="preview size, WxH")
    parser.add_argument("--view", choices=SYSMON_VIEWS, default=SYSMON_VIEWS[0], help="first view")
    args = parser.parse_args()

    sampler = Sampler()
    if args.preview:
        for _ in range(10):  # a few samples so the CPU history graph has something to show
            time.sleep(args.interval)
            sample = sampler.sample()
        render_dashboard(sample, args.size, args.view).save("sysmon.png")
        print("wrote sysmon.png")
        return
    with Screen.open() as screen:
        if not screen.realtime:
            raise SystemExit("stock firmware: realtime display unavailable")
        try:
            # Knob (proto 2): turning away and back onto the realtime page shows the next view.
            views = ViewCycle(SYSMON_VIEWS)
            while views.current != args.view:
                views.advance()
            screen.on_knob(views.on_event)
            screen.on_visibility(views.on_event)
            screen.stream(frames(sampler, args.interval, screen.capabilities.image_size, views), quality=80)
        except KeyboardInterrupt:
            pass  # leaving the context returns the screen to its previous page


if __name__ == "__main__":
    main()
