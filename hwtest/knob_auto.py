"""Unattended knob soak on hardware (dev firmware with test knob injection, HELLO flags bit 0).

Turns the screen knob with `Screen.inject_knob` (the step enters the stock key queue like a physical turn)
while frames are being sent, and checks after every step:
- exactly one screen-knob event per step, in the right direction;
- the slot position follows the 7-position cycle (6 stock pages + realtime);
- visibility events and `Screen.visible` agree with that position;
- no SDK error or timeout; every frame shown or reported hidden;
- STATUS reports armed, and visible exactly when on the slot.
Fast steps (default 0.15-0.6 s apart) rarely settle on a stock page for the 2 s that triggers the stock
page save, so flash writes stay few; the summary counts the settles.

Usage: sdk/.venv/Scripts/python -B hwtest/knob_auto.py --steps 200 --seed 1 --log hwtest/<dir>/knob_auto.jsonl
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sdk/src"))
from neos75screen import Screen  # noqa: E402
from neos75screen.errors import Neos75Error  # noqa: E402
from neos75screen.protocol import KnobEvent, VisibilityEvent  # noqa: E402
from guarded_screen import GuardedTransport  # noqa: E402

SLOT = 6  # position of the realtime slot in the 7-position cycle


def frame(step: int, size=(480, 360)):
    from PIL import Image, ImageDraw
    img = Image.new("RGB", size, ((step * 37) % 256, (step * 91) % 256, (step * 53) % 256))
    ImageDraw.Draw(img).text((20, 20), f"auto {step}", fill="white")
    return img


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--steps", type=int, default=200)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--min-gap", type=float, default=0.15)
    parser.add_argument("--max-gap", type=float, default=0.6)
    parser.add_argument("--log", type=Path, default=ROOT / "hwtest/knob_auto.jsonl")
    parser.add_argument("--sim", action="store_true", help="run against the firmware emulator, no device")
    args = parser.parse_args()
    if not args.log.resolve().is_relative_to(ROOT / "hwtest"):
        parser.error("--log must be inside hwtest/")
    args.log.parent.mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)
    failures: list[str] = []
    settles = 0

    with args.log.open("a", encoding="utf-8", buffering=1) as out:
        def record(kind: str, **fields) -> None:
            row = dict(time=datetime.now().astimezone().isoformat(), kind=kind, **fields)
            out.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
            if kind not in ("hid_tx", "hid_rx"):
                print(json.dumps(row, ensure_ascii=False, default=str), flush=True)

        if args.sim:
            sys.path[:0] = [str(ROOT / "tests/integration"), str(ROOT / "firmware/tests")]
            from test_knob import Link
            link = Link()
            screen = link.screen
            args.min_gap = args.max_gap = 0.0
        else:
            screen = Screen(GuardedTransport(record))
        try:
            hello = screen.hello()
            record("hello", **asdict(hello))
            if not hello.test_knob:
                raise SystemExit("this firmware has no test knob injection (needs the dev build)")
            screen.enter()
            result = screen.show(frame(0))
            record("frame", step=0, **asdict(result))
            list(screen.events(timeout=0.3))
            # Right after a reboot the stock boot animation keeps page switching locked for a few seconds
            # (0x2000BF77 = 0 until its end callback, hardware 2026-11-07): a step then only reports the turn.
            # Wait until a step really leaves the slot, then come back, so the position model starts in sync.
            for attempt in range(20):
                screen.inject_knob(1)
                events = list(screen.events(timeout=1.0))
                record("sync", attempt=attempt, events=[asdict(e) for e in events])
                if VisibilityEvent(False, 1) in events:
                    screen.inject_knob(-1)
                    events = list(screen.events(timeout=1.0))
                    record("sync_back", events=[asdict(e) for e in events])
                    if VisibilityEvent(True, 1) not in events:
                        raise SystemExit("sync: the slot did not come back")
                    screen.show(frame(0))
                    break
                time.sleep(1.0)
            else:
                raise SystemExit("sync: page switching stayed locked for 40 s")
            list(screen.events(timeout=0.3))
            position = SLOT
            last_step_time = time.monotonic()
            for step in range(1, args.steps + 1):
                direction = rng.choice([1, -1])
                screen.inject_knob(direction)
                expected = (position + direction) % 7
                deadline = time.monotonic() + 1.5
                knobs, vis = [], []
                while time.monotonic() < deadline:
                    for event in screen.events(timeout=0.05):
                        (knobs if isinstance(event, KnobEvent) else vis).append(event)
                    want_vis = (position == SLOT) != (expected == SLOT)
                    if knobs and (vis or not want_vis):
                        break
                time.sleep(0.05)
                for event in screen.events(timeout=0):
                    (knobs if isinstance(event, KnobEvent) else vis).append(event)
                record("step", step=step, direction=direction, position=expected,
                       knob=[asdict(k) for k in knobs], visibility=[asdict(v) for v in vis])
                if knobs != [KnobEvent(direction)]:
                    failures.append(f"step {step}: knob events {knobs}")
                want = [VisibilityEvent(expected == SLOT, 1)] if (position == SLOT) != (expected == SLOT) else []
                if vis != want:
                    failures.append(f"step {step}: visibility {vis}, expected {want}")
                position = expected
                if screen.visible != (position == SLOT):
                    failures.append(f"step {step}: Screen.visible {screen.visible} at position {position}")
                if position == SLOT:
                    try:
                        result = screen.show(frame(step))
                        record("frame", step=step, **asdict(result))
                        if not (result.shown or result.hidden):
                            failures.append(f"step {step}: frame {result}")
                    except Neos75Error as exc:
                        failures.append(f"step {step}: show raised {exc!r}")
                if step % 10 == 0:
                    status = screen.status()
                    record("status", step=step, **asdict(status))
                    if not status.armed or bool(status.visible) != (position == SLOT):
                        failures.append(f"step {step}: STATUS {status} at position {position}")
                gap = rng.uniform(args.min_gap, args.max_gap)
                now = time.monotonic()
                if position != SLOT and now - last_step_time >= 2.0:
                    settles += 1
                last_step_time = now
                time.sleep(gap)
            record("summary", steps=args.steps, seed=args.seed, failures=failures, stock_page_settles=settles)
        except Neos75Error as exc:
            failures.append(f"aborted: {exc!r}")
            record("summary", steps=args.steps, seed=args.seed, failures=failures, stock_page_settles=settles)
        finally:
            screen.close(leave=True)
    print(f"{len(failures)} failure(s)")
    for f in failures[:20]:
        print(" -", f)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
