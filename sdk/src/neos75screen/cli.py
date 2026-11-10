"""`neos75` command line."""
from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable, Iterable, Sequence

from PIL import Image

from . import constants as c
from .errors import Neos75Error
from .screen import Frame, Screen

FITS = ("contain", "cover", "stretch")


def _info(screen: Screen, _: argparse.Namespace) -> None:
    info, version = screen.info(), screen.version()
    print(f"model      {version.model}")
    print(f"build      {version.build}")
    print(f"usb        {info.vid:04X}:{info.pid:04X}")
    print(f"size       {info.width}x{info.height}  screenType={info.screen_type}  jpeg={info.supports_jpeg}")
    print(f"image/gif  {info.image_space} / {info.gif_space} bytes")


def _hello(screen: Screen, _: argparse.Namespace) -> None:
    hello = screen.capabilities
    if not hello.realtime:
        print("stock firmware: realtime display unavailable (stock commands still work)")
        return
    formats = [name for bit, name in ((c.FORMAT_JPEG, "jpeg"), (c.FORMAT_RGB565, "rgb565")) if hello.formats & bit]
    print(f"protocol   v{hello.proto_version}, patch {hello.patch_version}")
    print(f"image      {hello.image_w}x{hello.image_h} on a {hello.canvas_w}x{hello.canvas_h} canvas")
    print(f"formats    {', '.join(formats) or 'none'}")
    print(f"max frame  {hello.max_frame_bytes} bytes, {hello.buffers} buffer(s), flags 0x{hello.flags:02X}")


def _keyboard(screen: Screen, args: argparse.Namespace) -> None:
    """Battery, charging and connection as the screen last received them; --watch repeats every 5 s."""
    while True:
        k = screen.keyboard()
        if not k.reported:
            print("no keyboard status received since the screen booted")
        else:
            channel = f" {k.bluetooth_channel}" if k.bluetooth_channel else ""
            print(f"battery {k.battery}%{'  charging' if k.charging else ''}  connection {k.connection}{channel}"
                  f"  raw {k.raw.hex(' ')}")
        if not args.watch:
            return
        time.sleep(5)


def _hold(screen: Screen, args: argparse.Namespace) -> None:
    """Keep the realtime page until Ctrl+C, unless --stay leaves the image up after exit."""
    if args.stay:
        screen.close(leave=False)
        return
    print("showing; press Ctrl+C to return to the previous page")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


def _show(screen: Screen, args: argparse.Namespace) -> None:
    if args.rgb565:
        result = screen.show_rgb565(args.path, fit=args.fit)
    else:
        result = screen.show(args.path, fit=args.fit, quality=args.quality, max_bytes=args.max_bytes)
    if result.timed_out:
        print(f"no frame-done event; device status: {result.device_status}")
    else:
        print(f"shown (seq {result.seq}, decode {result.decode_ms} ms)")
    _hold(screen, args)


def _run_stream(screen: Screen, frames: Iterable[Frame], args: argparse.Namespace) -> None:
    try:
        stats = screen.stream(frames, fit=args.fit, quality=args.quality)
        print(f"done: {stats}")
    except KeyboardInterrupt:
        pass


def _clock(screen: Screen, args: argparse.Namespace) -> None:
    from .apps import clock_frames
    _run_stream(screen, clock_frames(screen.capabilities.image_size, seconds=not args.no_seconds), args)


def _sysmon(screen: Screen, args: argparse.Namespace) -> None:
    from .apps import SYSMON_VIEWS, ViewCycle, sysmon_frames
    views = ViewCycle(SYSMON_VIEWS)  # proto 2: coming back to the slot with the knob shows the next view
    screen.on_knob(views.on_event)
    screen.on_visibility(views.on_event)
    if args.knob_press:
        screen.capture_knob_press()  # a press switches views; Calculator stays closed while this runs
    _run_stream(screen, sysmon_frames(args.interval, screen.capabilities.image_size, lambda: views.current), args)


def _slideshow(screen: Screen, args: argparse.Namespace) -> None:
    from .apps import slideshow_frames
    _run_stream(screen, slideshow_frames(args.directory, args.interval, loop=not args.once), args)


def _gif(screen: Screen, args: argparse.Namespace) -> None:
    from .apps import gif_frames

    def encode(img: Image.Image) -> bytes:
        return screen.encode(img, fit=args.fit, quality=args.quality)

    _run_stream(screen, gif_frames(args.path, args.loops, encode), args)


def _widgets(screen: Screen, args: argparse.Namespace) -> None:
    """Run the selected pages (`neos75 pages`); the selection file is followed while running. --pages runs
    a fixed list instead."""
    from pathlib import Path
    from .live import library
    from .live.runner import WidgetRunner
    extra = [Path(args.folder)] if args.folder else []
    if args.pages:
        runner = WidgetRunner(library.resolve(args.pages.split(","), extra), screen=screen, interval=args.interval)
    else:
        runner = WidgetRunner(screen=screen, interval=args.interval, config=Path(args.config))
    # With host pages the knob turns to each widget like a stock page and a press stays as on stock
    # pages; otherwise the widgets share one slot and a press switches (Calculator kept closed).
    if screen.capabilities.knob and not runner.paged:
        try:
            screen.capture_knob_press()
        except OSError:
            pass
    try:
        runner.run(args.seconds)
    except KeyboardInterrupt:
        pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="neos75", description="AULA Neos75 screen SDK")
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name: str, func: Callable[[Screen, argparse.Namespace], None], help_text: str,
            realtime: bool = True) -> argparse.ArgumentParser:
        cmd = sub.add_parser(name, help=help_text)
        cmd.set_defaults(func=func, realtime=realtime)
        if realtime:
            cmd.add_argument("--fit", choices=FITS, default="contain")
            cmd.add_argument("--quality", type=int, default=85)
        return cmd

    add("info", _info, "stock device info (read-only)", realtime=False)
    add("hello", _hello, "realtime capabilities of the patched firmware", realtime=False)
    add("keyboard", _keyboard, "keyboard battery and connection (read-only)", realtime=False).add_argument(
        "--watch", action="store_true", help="repeat every 5 s until Ctrl+C")
    show = add("show", _show, "show one image")
    show.add_argument("path")
    show.add_argument("--max-bytes", type=int)
    show.add_argument("--rgb565", action="store_true", help="send raw RGB565 instead of JPEG")
    show.add_argument("--stay", action="store_true", help="exit without leaving the realtime page")
    clock = add("clock", _clock, "large clock")
    clock.add_argument("--no-seconds", action="store_true")
    widgets = add("widgets", _widgets, "live widget pages in the screen knob's cycle (selection: neos75 pages)")
    widgets.add_argument("--config", default=str(_pages_config()), help="selection file, followed while running")
    widgets.add_argument("--pages", help="comma-separated widget names: run these instead of the selection")
    widgets.add_argument("--folder", help="extra widget folder")
    widgets.add_argument("--interval", type=float, default=1.0)
    widgets.add_argument("--seconds", type=float, default=None, help="stop after this long (default: run until Ctrl+C)")
    pages = sub.add_parser("pages", help="show, list or change the widget pages (no device needed)")
    pages.set_defaults(func=_pages, realtime=False, device=False)
    pages.add_argument("names", nargs="*", help="set the pages to these widgets, in this order (at most 8)")
    pages.add_argument("--list", action="store_true", help="list the widget library")
    pages.add_argument("--config", default=str(_pages_config()))
    sysmon = add("sysmon", _sysmon, "system monitor dashboard")
    sysmon.add_argument("--interval", type=float, default=1.0)
    sysmon.add_argument("--knob-press", action="store_true",
                        help="Windows: a knob press switches views (Calculator does not open while running)")
    slideshow = add("slideshow", _slideshow, "images of a directory")
    slideshow.add_argument("directory")
    slideshow.add_argument("--interval", type=float, default=5.0)
    slideshow.add_argument("--once", action="store_true")
    gif = add("gif", _gif, "animated GIF")
    gif.add_argument("path")
    gif.add_argument("--loops", type=int, default=0, help="0 repeats forever")
    return parser


def _pages_config():  # noqa: ANN202
    from .live import library
    return library.default_config()


def _pages(_: None, args: argparse.Namespace) -> None:
    """The selection lives in a file; a running `neos75 widgets` picks a change up within a second."""
    from pathlib import Path
    from .live import library
    config = Path(args.config)
    if args.list:
        for name, path in library.widgets().items():
            print(f"{name:24} {path}")
        return
    if args.names:
        names = [n for arg in args.names for n in arg.split(",") if n]
        library.save(config, names)
    for i, name in enumerate(library.load(config)):
        print(f"{library.page_label(i):8} {name}")
    print(f"({config})")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not getattr(args, "device", True):
        try:
            args.func(None, args)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        return 0
    try:
        with Screen.open() as screen:
            if args.realtime and not screen.realtime:
                print("stock firmware: realtime display unavailable", file=sys.stderr)
                return 1
            args.func(screen, args)
    except Neos75Error as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
