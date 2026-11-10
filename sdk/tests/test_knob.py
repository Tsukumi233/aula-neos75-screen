"""Knob slot, visibility, events and keepalive (PROTOCOL.md section 5) against the v2-knob fake device."""
from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator

import pytest
from PIL import Image

from conftest import Connect, gradient, noise
from fake_device import STOCK_PAGES
from neos75screen import FrameRejected, ProtocolError, StreamStats
from neos75screen.protocol import KnobEvent, PageEvent, VisibilityEvent
from neos75screen.screen import Screen

KNOB = "v2-knob"


def wait_for(predicate: Callable[[], bool], timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def status_requests(log: list[bytes]) -> int:
    return sum(1 for r in log if r[:2] == b"\xa5\x3b" and r[5] == 0x04)


class Source:
    """Endless frame source for stream(): a new frame every `interval` seconds until stopped."""

    def __init__(self, interval: float = 0.03) -> None:
        self.interval = interval
        self.stopped = threading.Event()
        self.produced = 0

    def __iter__(self) -> Iterator[Image.Image]:
        while not self.stopped.is_set():
            self.produced += 1
            yield Image.new("RGB", (480, 360), (self.produced * 37 % 256, 80, 160))
            time.sleep(self.interval)


class Runner(threading.Thread):
    """Runs screen.stream() in the background and keeps its result or exception."""

    def __init__(self, screen: Screen, frames: object) -> None:
        super().__init__(daemon=True)
        self.screen, self.frames = screen, frames
        self.stats: StreamStats | None = None
        self.error: BaseException | None = None
        self.start()

    def run(self) -> None:
        try:
            self.stats = self.screen.stream(self.frames)  # type: ignore[arg-type]
        except BaseException as exc:
            self.error = exc


def test_proto2_hello_and_status(connect: Connect) -> None:
    screen, _, _ = connect(profile=KNOB, host_timeout_ms=3000)
    hello = screen.capabilities
    assert hello.knob and hello.proto_version == 2 and hello.host_timeout_ms == 3000
    assert not screen.visible and not screen.armed
    screen.enter()
    status = screen.status()
    assert status.visible is True and status.armed is True
    assert screen.visible and screen.armed
    screen.leave()
    assert not screen.visible and not screen.armed


def test_proto1_device_has_no_knob_features(connect: Connect) -> None:
    screen, fake, _ = connect(profile="v2")
    hello = screen.capabilities
    assert not hello.knob and hello.host_timeout_ms == 0
    calls: list[object] = []
    screen.on_knob(calls.append)
    screen.enter()
    assert screen.visible and screen.armed
    assert screen.status().visible is None
    before = status_requests(fake.log)
    time.sleep(0.3)
    assert status_requests(fake.log) == before  # no keepalive on proto 1
    assert screen.show(gradient()).shown
    screen.leave()
    assert not screen.visible and calls == []
    assert list(screen.events(timeout=0.05)) == []


def test_callbacks_and_event_iterator(connect: Connect) -> None:
    screen, fake, _ = connect(profile=KNOB)
    knobs: list[int] = []
    visibility: list[tuple[bool, int]] = []
    screen.on_knob(lambda e: knobs.append(e.direction))
    screen.on_visibility(lambda e: visibility.append((e.visible, e.reason)))
    screen.enter()
    fake.knob(+1)
    assert wait_for(lambda: not screen.visible)
    assert screen.armed and fake.page == 0
    fake.knob(-1)
    assert wait_for(lambda: screen.visible)
    fake.knob(0)
    assert wait_for(lambda: knobs == [1, -1, 0])
    assert wait_for(lambda: visibility == [(True, 2), (False, 1), (True, 1)])
    events = list(screen.events(timeout=0.1))
    assert events == [VisibilityEvent(True, 2), KnobEvent(1), VisibilityEvent(False, 1), KnobEvent(-1),
                      VisibilityEvent(True, 1), KnobEvent(0)]


def test_knob_skips_the_slot_when_disarmed(connect: Connect) -> None:
    _, fake, _ = connect(profile=KNOB)
    for _ in range(STOCK_PAGES + 1):
        fake.knob(+1)
    assert not fake.realtime and fake.visibility_events == [] and fake.knob_events == []


def test_stream_pauses_while_hidden_and_resends_on_return(connect: Connect) -> None:
    screen, fake, _ = connect(profile=KNOB)
    source = Source()
    runner = Runner(screen, source)
    assert wait_for(lambda: len(fake.shown) >= 3)
    fake.knob(+1)
    assert wait_for(lambda: not screen.visible)
    time.sleep(0.1)  # a frame in flight at the switch may still finish (with 05)
    sent, produced = fake.realtime_frames, source.produced
    time.sleep(0.3)
    assert fake.realtime_frames == sent  # nothing sent while hidden
    assert source.produced <= produced + 1  # the frame source is paused too
    shown = len(fake.shown)
    fake.knob(-1)
    assert wait_for(lambda: len(fake.shown) > shown, timeout=0.5)
    source.stopped.set()
    runner.join(3)
    assert runner.error is None and runner.stats is not None
    assert runner.stats.dropped == 0 and runner.stats.timeouts == 0


def test_resend_does_not_wait_for_a_slow_source(connect: Connect) -> None:
    screen, fake, _ = connect(profile=KNOB)
    release = threading.Event()

    def slow() -> Iterator[Image.Image]:
        yield Image.new("RGB", (480, 360), (200, 40, 40))
        release.wait(5)  # e.g. a slideshow waiting for its next slide

    runner = Runner(screen, slow())
    assert wait_for(lambda: len(fake.shown) == 1)
    fake.knob(+1)
    assert wait_for(lambda: not screen.visible)
    fake.knob(-1)
    assert wait_for(lambda: len(fake.shown) == 2, timeout=0.5)  # resent while the source still sleeps
    assert fake.shown[1].getpixel((240, 180)) == fake.shown[0].getpixel((240, 180))
    release.set()
    runner.join(3)
    assert runner.error is None and runner.stats is not None and runner.stats.shown == 2


def test_status_05_while_hidden_is_a_pause(connect: Connect) -> None:
    screen, fake, _ = connect(profile=KNOB, hide_on_frame=3)
    source = Source()
    runner = Runner(screen, source)
    assert wait_for(lambda: not screen.visible)
    assert wait_for(lambda: 0x05 in fake.statuses)
    assert runner.is_alive() and runner.error is None
    shown = len(fake.shown)
    fake.knob(-1)
    assert wait_for(lambda: len(fake.shown) > shown, timeout=0.5)
    source.stopped.set()
    runner.join(3)
    assert runner.error is None and runner.stats is not None
    assert runner.stats.hidden == 1 and runner.stats.dropped == 0


def test_show_while_hidden_sends_nothing(connect: Connect) -> None:
    screen, fake, _ = connect(profile=KNOB)
    assert screen.show(gradient()).shown
    fake.knob(-1)
    assert wait_for(lambda: not screen.visible)
    sent = fake.realtime_frames
    result = screen.show(gradient())
    assert result.hidden and not result.shown and not result.timed_out
    assert fake.realtime_frames == sent


def test_show_answered_05_after_hiding_returns_hidden(connect: Connect) -> None:
    screen, fake, _ = connect(profile=KNOB, hide_on_frame=2)
    assert screen.show(gradient()).shown
    result = screen.show(gradient())
    assert result.hidden and result.status == 0x05


def test_05_while_visible_is_still_an_error(connect: Connect) -> None:
    screen, fake, _ = connect(profile=KNOB)
    screen.enter()
    fake.realtime = False  # the device answers 05 without a hidden event: a real error
    with pytest.raises(FrameRejected):
        screen.show(gradient())


def test_host_timeout_disarms_without_keepalive(connect: Connect) -> None:
    screen, fake, _ = connect(profile=KNOB, host_timeout_ms=200, keepalive=False)
    fake.page = 1
    screen.enter()
    events = []
    for event in screen.events(timeout=1.0):
        events.append(event)
        if event == VisibilityEvent(False, 3):
            break
    assert VisibilityEvent(False, 3) in events
    assert not screen.visible and not screen.armed and not fake.armed and fake.page == 1
    assert screen.show(gradient()).shown  # the next frame starts a new session
    assert fake.armed and screen.armed


def test_stream_reenters_after_host_timeout(connect: Connect) -> None:
    screen, fake, _ = connect(profile=KNOB, host_timeout_ms=150, keepalive=False)

    def frames() -> Iterator[Image.Image]:
        yield gradient((480, 360))
        time.sleep(0.4)
        yield gradient((480, 360))

    stats = screen.stream(frames())
    assert stats.shown == 2
    assert (0, 3) in fake.visibility_events and fake.visibility_events[-1] == (1, 2)


def test_keepalive_keeps_the_slot_armed(connect: Connect) -> None:
    screen, fake, _ = connect(profile=KNOB, host_timeout_ms=300)
    screen.enter()
    time.sleep(1.0)
    assert fake.armed and screen.armed and (0, 3) not in fake.visibility_events
    assert status_requests(fake.log) >= 3
    screen.close()
    count = len(fake.log)
    time.sleep(0.3)
    assert len(fake.log) == count  # nothing after close


def test_keepalive_stops_on_leave(connect: Connect) -> None:
    screen, fake, _ = connect(profile=KNOB, host_timeout_ms=150)
    screen.enter()
    screen.leave()
    count = status_requests(fake.log)
    time.sleep(0.3)
    assert status_requests(fake.log) == count


def test_keepalive_never_interleaves_with_a_stream(connect: Connect) -> None:
    screen, fake, _ = connect(profile=KNOB, host_timeout_ms=60, decode_delay=0.08)
    stats = screen.stream([noise((480, 360), seed=i) for i in range(4)], quality=60)
    assert stats.shown == 4
    remaining = 0
    for report in fake.log:
        if remaining:
            assert report[0] == 0xBA, "a non-BA packet inside a BA stream"
            remaining -= report[1]
        elif report[:3] == b"\xba\x06\xaa":
            remaining = int.from_bytes(report[4:8], "big")
    assert status_requests(fake.log) >= 1  # the keepalive ran between frames


def test_events_do_not_disturb_requests(connect: Connect) -> None:
    screen, fake, _ = connect(profile=KNOB)
    screen.enter()
    stop = threading.Event()

    def spin() -> None:
        while not stop.is_set():
            fake.knob(+1)
            fake.knob(-1)
            fake.knob(0)

    spinner = threading.Thread(target=spin, daemon=True)
    spinner.start()
    try:
        for _ in range(30):
            assert screen.status().frames_err == 0
        assert screen.info().width == 320 and screen.version().model
        result = screen.show(gradient((480, 360)))
        assert result.shown or result.hidden
    finally:
        stop.set()
        spinner.join(2)
    events = list(screen.events(timeout=0.1))
    assert any(isinstance(e, KnobEvent) for e in events)
    assert any(isinstance(e, VisibilityEvent) for e in events)


def _status_bytes(mode: int, battery: int, link: int) -> bytes:
    raw = bytearray(26)
    raw[13], raw[14], raw[16] = mode, battery, link
    return bytes(raw)


@pytest.mark.parametrize("mode,battery,link,connection,charging", [
    (2, 57, 5, "wired", True), (1, 99, 4, "2.4g", False), (0, 12, 2, "bluetooth", False), (0, 0, 0, "unknown", False)])
def test_keyboard_status(connect: Connect, mode: int, battery: int, link: int, connection: str, charging: bool) -> None:
    screen, _, _ = connect(profile="v2-knob", keyboard_status=_status_bytes(mode, battery, link))
    assert screen.capabilities.telemetry
    status = screen.keyboard()
    assert (status.battery, status.charging, status.connection) == (battery, charging, connection)
    assert status.bluetooth_channel == (link if connection == "bluetooth" else None)
    assert status.reported == bool(mode or battery or link)


def test_keyboard_status_needs_firmware_support(connect: Connect) -> None:
    screen, fake, _ = connect(profile="v2-knob")
    assert not screen.capabilities.telemetry
    with pytest.raises(ProtocolError, match="keyboard status"):
        screen.keyboard()
    assert not any(r[0] == 0xA5 and r[1] == 0x3B and r[5] == 0x05 for r in fake.log)


def test_host_pages_follow_the_knob(connect: Connect) -> None:
    screen, fake, _ = connect(profile=KNOB, host_pages=True)
    assert screen.capabilities.host_pages
    pages: list[tuple[int, int]] = []
    screen.on_page(lambda e: pages.append((e.page, e.count)))
    screen.enter()
    assert screen.set_pages(3) == 0 and fake.pages == 3
    fake.knob(+1); fake.knob(+1)
    assert wait_for(lambda: pages == [(1, 3), (2, 3)]) and screen.page == 2 and screen.visible
    fake.knob(+1)                                   # past the last page: a stock page
    assert wait_for(lambda: not screen.visible)
    for _ in range(STOCK_PAGES - 1):
        fake.knob(+1)
    fake.knob(+1)                                   # clockwise entry: first page, before visibility
    assert wait_for(lambda: screen.visible) and screen.page == 0
    assert wait_for(lambda: pages[-1] == (0, 3))   # callbacks run on the dispatcher thread
    events = [e for e in screen.events(timeout=0.1) if not isinstance(e, KnobEvent)]
    assert events[-2:] == [PageEvent(0, 3), VisibilityEvent(True, 1)]
    fake.knob(-1)                                   # before the first page: back to stock
    assert wait_for(lambda: not screen.visible)
    fake.knob(+1)
    assert wait_for(lambda: screen.visible) and screen.page == 0   # stock last -> slot from the front
    status = screen.status()
    assert (status.page, status.pages) == (0, 3)


def test_set_pages_needs_firmware_support(connect: Connect) -> None:
    screen, _, _ = connect(profile=KNOB)
    with pytest.raises(ProtocolError, match="single realtime page"):
        screen.set_pages(3)
    with pytest.raises(ValueError):
        screen.set_pages(9)
    assert screen.status().page is None


def test_requests_from_another_thread_while_frames_stream() -> None:
    """Replies arrive a few ms after the request, as from the device; a frame waiting for its frame-done
    must not read and drop another thread's reply (hardware 2026-11-08: "no reply to 3B 05")."""
    from fake_device import FakeScreen
    from neos75screen.transport import MockTransport

    class Delayed(MockTransport):
        def _write_report(self, report: bytes) -> None:
            self.written.append(report)
            for reply in self._responder(report):
                threading.Timer(0.003, self.inject, (reply,)).start()

    raw = bytearray(26); raw[14] = 100; raw[16] = 5
    fake = FakeScreen(profile=KNOB, keyboard_status=bytes(raw), decode_delay=0.02)
    transport = Delayed(fake); fake.attach(transport)
    screen = Screen(transport, keepalive=False)
    stop, errors, reads = threading.Event(), [], []

    def stream() -> None:
        try:
            while not stop.is_set():
                screen.show(noise((64, 64)))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    t = threading.Thread(target=stream); t.start()
    try:
        for _ in range(20):
            reads.append(screen.keyboard().battery)
            time.sleep(0.005)
    finally:
        stop.set(); t.join(5); fake.stop()
    assert not errors and reads == [100] * 20


def test_knob_takeover_reports_turns_without_paging_and_is_resent_after_enter(connect: Connect) -> None:
    screen, fake, _ = connect(profile=KNOB, host_pages=True, knob_takeover=True)
    assert screen.capabilities.knob_takeover
    events: list[KnobEvent] = []
    screen.on_knob(events.append)
    assert screen.set_knob_takeover(True) is False      # not entered yet: remembered only
    screen.enter()
    assert fake.takeover                                 # sent with ENTER
    screen.set_pages(3)
    fake.knob(+1)
    fake.knob(+1)
    assert wait_for(lambda: len(events) == 2)
    assert events == [KnobEvent(1, taken=True)] * 2 and fake.host_page == 0 and screen.visible
    screen.inject_knob(+1)                               # a step from the PC still pages, unreported
    assert wait_for(lambda: fake.host_page == 1)
    time.sleep(0.05)
    assert len(events) == 2
    screen.leave()
    assert not fake.takeover                             # the session ended it
    screen.enter()
    assert fake.takeover                                 # wanted: sent again
    assert screen.set_knob_takeover(False) is False and not fake.takeover
    fake.knob(+1)
    assert wait_for(lambda: len(events) == 3) and events[-1] == KnobEvent(1, taken=False)


def test_knob_takeover_needs_the_firmware(connect: Connect) -> None:
    screen, _, _ = connect(profile=KNOB)
    assert not screen.capabilities.knob_takeover
    with pytest.raises(ProtocolError):
        screen.set_knob_takeover(True)
    assert screen.set_knob_takeover(False) is False
