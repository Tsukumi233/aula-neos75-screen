"""Randomised stress: the real SDK uploading frames while knob turns, input-queue overflows and visibility
changes land at random points, against the patched firmware's machine code.

Written after the 2026-11-07 hardware sessions, where every bug in this area was found by a person spinning
the knob. Each seed is deterministic; a failure prints the seed and step so it can be replayed. Invariants:

- show() never times out, and no upload inside it waits out an ACK timeout unless an input-queue overflow was
  injected in that call (the SDK retries then; anywhere else a timeout means the device dropped a stream
  without saying so);
- show() returns shown or hidden, or raises 04 only in a call during which an input-queue overflow was
  injected (the documented busy case; the SDK retries it itself when the knob moved during that attempt);
- after every show() the device is idle (state 0): nothing is left half-received;
- frame-done events never outnumber the stream starts the SDK sent (no stale events), and only starts that
  an injected input-queue overflow discarded may go without one (the SDK then retries the frame);
- at the end, with the slot visible, a frame is shown.
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_knob import Link  # noqa: E402

from neos75screen.errors import FrameRejected, Timeout  # noqa: E402

SIZES = [(2, 2), (16, 16), (48, 48), (64, 40), (90, 60)]


def _is_start(report: bytes) -> bool:
    return report[0] == 0xBA and report[1:3] == b"\x06\xaa" and report[3] in (0x5A, 0x5B)


def _frame_dones(reports: list[bytes]) -> int:
    return sum(1 for r in reports if r[0] == 0xA5 and r[1] == 0x3B and r[5] == 0x10)


@pytest.mark.parametrize("seed", range(96))
def test_random_knob_storms_during_uploads(seed: int) -> None:
    rng = random.Random(seed)
    link = Link()
    screen, machine = link.screen, link.machine
    respond = link.respond
    state = {"overflow": False, "knob": False, "overflows": 0}

    back_after = [0]

    def chaos(report: bytes) -> list[bytes]:
        if report[0] == 0xBA and back_after[0]:
            back_after[0] -= 1
            if not back_after[0]:  # come back onto the slot while the hidden stream is still arriving
                machine.inject_knob(-1)
                state["knob"] = True
        if _is_start(report) and not back_after[0] and rng.random() < 0.2:
            machine.inject_knob(1)  # the slot is hidden before the device sees this start
            back_after[0] = rng.randint(1, 60)
            state["knob"] = True
        elif report[0] == 0xBA:
            roll = rng.random()
            if roll < 0.06:
                for _ in range(rng.choice([1, 1, 2, 3, 6, 7, 8])):
                    machine.inject_knob(rng.choice([1, -1]))
                state["knob"] = True
            elif roll < 0.075:
                machine.set("overflow", 1)
                state["overflow"] = True
                state["overflows"] += 1
        return respond(report)

    wait_ack = screen._wait_ack
    timeouts: list[int] = []

    def watched_wait_ack(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        try:
            return wait_ack(*args, **kwargs)
        except Timeout:  # seen here even when the SDK then recovers by itself
            timeouts.append(1)
            raise

    screen._wait_ack = watched_wait_ack
    try:
        screen.hello()
        screen.enter()
        link.transport._responder = chaos
        for step in range(30):
            state["overflow"] = state["knob"] = False
            timeouts.clear()
            w, h = rng.choice(SIZES)
            where = f"seed {seed} step {step} ({w}x{h})"
            try:
                result = screen.show_rgb565(rng.randbytes(w * h * 2), width=w, height=h)
                assert result.shown or result.hidden, where
            except FrameRejected as exc:
                assert exc.status == 0x04 and state["overflow"], f"{where}: {exc}"
            except Timeout as exc:  # pragma: no cover - the failure we are hunting
                pytest.fail(f"{where}: {exc}")
            assert not timeouts or state["overflow"], f"{where}: upload timed out without an overflow"
            assert machine.state().state == 0, f"{where}: device left in state {machine.state().state}"
            link.publish()
            if rng.random() < 0.15:  # sometimes turn between frames too
                link.knob(rng.choice([1, -1]))
        link.transport._responder = respond
        written = link.transport.written
        starts, dones = sum(1 for r in written if _is_start(r)), _frame_dones(machine.sent)
        assert starts - state["overflows"] <= dones <= starts, f"seed {seed}: {starts} starts, {dones} frame-dones"
        for _ in range(7):
            if screen.visible:
                break
            link.knob(1)
        assert screen.visible and screen.armed, f"seed {seed}"
        assert screen.show_rgb565(b"\xe0\x07" * 4, width=2, height=2).shown, f"seed {seed}"
    finally:
        screen.close(leave=False)
