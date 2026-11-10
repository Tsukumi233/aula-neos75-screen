"""Real SDK against proto-2 firmware machine code, with offline knob/time injection."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "firmware" / "tests"))

from emulator import Machine  # noqa: E402
from neos75screen import Screen  # noqa: E402
from neos75screen import protocol as p  # noqa: E402
from neos75screen.errors import FrameRejected, ProtocolError  # noqa: E402
from neos75screen.protocol import KnobEvent, VisibilityEvent  # noqa: E402
from neos75screen.screen import Timeouts  # noqa: E402
from neos75screen.transport import MockTransport  # noqa: E402


class Link:
    """Pump all machine output through the SDK's unchanged guarded transport."""

    def __init__(self, variant: str = "dev") -> None:
        self.machine = Machine(variant)
        self.delivered = 0
        self.transport = MockTransport(self.respond)
        self.screen = Screen(self.transport, Timeouts(reply=.2, hello=.2, ack=.2, frame_done=.2),
                             keepalive=False)

    def drain(self) -> list[bytes]:
        # The real GUI task keeps running while the host waits; it sends one report per 10 ms tick. Run ticks
        # until the input ring and the TX ring are empty, so a backlog of knob events cannot hold back an ACK.
        for _ in range(512):
            self.machine.gui()
            state = self.machine.state()
            if state.head == state.tail and state.tx_head == state.tx_tail:
                break
        for _ in range(2):
            self.machine.gui()
        reports = self.machine.sent[self.delivered:]
        self.delivered = len(self.machine.sent)
        return reports

    def respond(self, report: bytes) -> list[bytes]:
        self.machine.hid(report)
        return self.drain()

    def publish(self) -> None:
        for report in self.drain():
            self.transport.inject(report)

    def knob(self, direction: int) -> None:
        self.machine.inject_knob(direction)
        self.publish()

    def advance(self, milliseconds: int) -> None:
        self.machine.advance(milliseconds)
        self.publish()

    def events(self) -> list[KnobEvent | VisibilityEvent]:
        return list(self.screen.events(timeout=0))

    def page(self) -> int:
        return self.machine.uc.mem_read(0x200044AD, 1)[0]


@pytest.fixture
def link():
    value = Link()
    try:
        yield value
    finally:
        value.screen.close(leave=False)


@pytest.mark.parametrize("variant", ["dev", "release"])
def test_proto2_hello_and_status_fields(variant: str) -> None:
    value = Link(variant)
    try:
        hello = value.screen.hello()
        assert hello.realtime and hello.knob
        assert (hello.proto_version, hello.patch_version) == (2, 3)
        assert hello.host_timeout_ms == 10000
        assert (hello.canvas_w, hello.canvas_h, hello.image_w, hello.image_h) == (480, 360, 480, 360)
        assert hello.max_frame_bytes == 172804 and hello.buffers == 1
        status = value.screen.status()
        assert status.visible is False and status.armed is False
    finally:
        value.screen.close(leave=False)


def test_enter_leave_visibility_events_and_status(link: Link) -> None:
    screen = link.screen
    screen.hello()
    screen.enter()
    assert screen.visible and screen.armed
    assert link.events() == [VisibilityEvent(True, 2)]
    status = screen.status()
    assert status.visible is True and status.armed is True
    screen.leave()
    assert link.events() == [VisibilityEvent(False, 2)]
    assert not screen.visible and not screen.armed
    status = screen.status()
    assert status.visible is False and status.armed is False


def test_knob_hides_and_returns_to_black_slot(link: Link) -> None:
    screen, machine = link.screen, link.machine
    screen.hello()
    assert screen.show_rgb565(b"\x00\xf8" * 16 * 16, width=16, height=16).shown
    assert link.events() == [VisibilityEvent(True, 2)]
    link.knob(1)
    assert not screen.visible and screen.armed and link.page() == 0
    assert link.events() == [KnobEvent(1), VisibilityEvent(False, 1)]
    writes = len(link.transport.written)
    assert screen.show_rgb565(b"\xe0\x07" * 4, width=2, height=2).hidden
    assert len(link.transport.written) == writes
    link.knob(-1)
    assert screen.visible and screen.armed
    assert link.events() == [KnobEvent(-1), VisibilityEvent(True, 1)]
    assert machine.objects[machine.state().image]["hidden"]
    assert screen.show_rgb565(b"\xe0\x07" * 4, width=2, height=2).shown
    assert not machine.objects[machine.state().image]["hidden"]
    assert screen.status().frames_ok == 2


def test_six_stock_pages_skip_slot_when_disarmed(link: Link) -> None:
    link.screen.hello()
    assert link.page() == 0
    for expected in (1, 2, 3, 4, 5, 0):
        link.knob(1)
        assert link.page() == expected
        assert not link.machine.state().active and not link.machine.state().armed
    for expected in (5, 4, 3, 2, 1, 0):
        link.knob(-1)
        assert link.page() == expected
    assert link.events() == []


@pytest.mark.parametrize("direction", [1, -1])
def test_armed_knob_emits_every_turn_across_all_stock_pages(link: Link, direction: int) -> None:
    screen = link.screen
    screen.hello()
    screen.enter()
    link.events()
    sequence = list(range(6)) if direction == 1 else list(range(5, -1, -1))
    for index, expected in enumerate(sequence):
        link.knob(direction)
        assert link.page() == expected and not screen.visible and screen.armed
        expected_events = [KnobEvent(direction)]
        if index == 0:
            expected_events.append(VisibilityEvent(False, 1))
        assert link.events() == expected_events
    link.knob(direction)
    assert screen.visible and screen.armed
    assert link.events() == [KnobEvent(direction), VisibilityEvent(True, 1)]


def test_leave_while_hidden_keeps_the_users_stock_page(link: Link) -> None:
    screen = link.screen
    screen.hello()
    for _ in range(3):
        link.knob(1)
    screen.enter()
    link.knob(1)
    link.events()
    assert link.page() == 0 and not screen.visible and screen.armed
    screen.leave()
    assert link.page() == 0 and not screen.visible and not screen.armed
    assert link.events() == []
    link.knob(1)
    assert link.page() == 1 and link.events() == []


@pytest.mark.parametrize("hidden", [False, True])
def test_timeout_disarms_visible_and_hidden_sessions(link: Link, hidden: bool) -> None:
    screen = link.screen
    hello = screen.hello()
    for _ in range(3):
        link.knob(1)
    assert link.page() == 3
    screen.enter()
    link.events()
    if hidden:
        link.knob(1)
        link.events()
    link.advance(hello.host_timeout_ms + 1)
    assert not link.machine.state().active and not link.machine.state().armed
    assert link.events() == ([] if hidden else [VisibilityEvent(False, 3)])
    assert link.page() == (0 if hidden else 3)
    status = screen.status()
    assert status.visible is False and status.armed is False
    assert not screen.visible and not screen.armed
    screen.enter()
    assert screen.visible and screen.armed


def test_real_sdk_keepalive_maintains_session_then_idle_expires(link: Link) -> None:
    screen = link.screen
    hello = screen.hello()
    screen.enter()
    link.events()
    for _ in range(6):
        link.advance(3400)
        # Invoke the actual scheduler decision at a simulated idle deadline;
        # the worker thread is disabled to keep Unicorn single-threaded.
        screen._last_activity = time.monotonic() - hello.host_timeout_ms / 3000 - .01
        screen._keepalive_tick()
        assert link.machine.state().armed and link.machine.state().active
    statuses = [r for r in link.transport.written if r[:2] == b"\xa5\x3b" and r[5] == 4]
    assert len(statuses) == 6
    assert link.events() == []
    link.advance(hello.host_timeout_ms + 1)
    assert link.events() == [VisibilityEvent(False, 3)]
    assert not screen.visible and not screen.armed


def test_hidden_ba_stream_returns_05_without_shared_buffer_writes(link: Link) -> None:
    screen, machine = link.screen, link.machine
    screen.hello()
    screen.enter()
    link.knob(1)
    link.events()
    original_rx = bytes(machine.uc.mem_read(0x800D4200, 172804))
    original_rgb = bytes(machine.uc.mem_read(0x800FE604, 345600))
    # Bypass only the public API's hidden early return. The real SDK builders,
    # uploader and transport allow-list still drive the firmware BA receiver.
    stream = p.build_rgb565_stream(b"\xff\xff" * 64 * 64, 64, 64)
    screen._send_stream(0x5B, stream)
    assert screen._collect().status == 5
    assert bytes(machine.uc.mem_read(0x800D4200, 172804)) == original_rx
    assert bytes(machine.uc.mem_read(0x800FE604, 345600)) == original_rgb
    assert screen.status().frames_err == 1


@pytest.mark.parametrize("packet", [3, 70, 120])
def test_knob_hiding_the_slot_mid_upload_ends_the_frame_as_hidden(link: Link, packet: int) -> None:
    """Hardware 2026-11-07: a knob turn during an upload made the SDK wait for an ACK that never came."""
    screen, machine = link.screen, link.machine
    screen.hello()
    screen.enter()
    link.events()
    seen = 0
    respond = link.respond

    def turn_during_upload(report: bytes) -> list[bytes]:
        nonlocal seen
        if report[0] == 0xBA:
            seen += 1
            if seen == packet:
                machine.inject_knob(1)
        return respond(report)

    link.transport._responder = turn_during_upload
    frame = bytes(range(256)) * (2 * 120 * 90 // 256 + 1)
    result = screen.show_rgb565(frame[:2 * 120 * 90], width=120, height=90)
    assert result.hidden and not result.shown
    assert not screen.visible and screen.armed
    assert KnobEvent(1) in link.events()
    machine.inject_knob(-1)
    link.publish()
    assert screen.visible
    assert screen.show_rgb565(b"\xe0\x07" * 4, width=2, height=2).shown


def _starts(link: Link) -> int:
    return sum(1 for r in link.transport.written if r[0] == 0xBA and r[1:3] == b"\x06\xaa")


def test_knob_away_and_back_while_an_upload_is_in_flight(link: Link) -> None:
    """Hardware 2026-11-07 (fast spinning): the slot was hidden when the upload started (the device
    discards such a stream) and shown again before it ended. The device ends the stream with 05 and,
    since the slot is visible again, show() sends the frame once more."""
    screen, machine = link.screen, link.machine
    screen.hello()
    screen.enter()
    link.events()
    seen = 0
    respond = link.respond

    def spin(report: bytes) -> list[bytes]:
        nonlocal seen
        if report[0] == 0xBA:
            seen += 1
            if seen == 1:
                machine.inject_knob(1)
            elif seen == 50:
                machine.inject_knob(-1)
        return respond(report)

    link.transport._responder = spin
    starts = _starts(link)
    result = screen.show_rgb565(bytes(range(256)) * 18, width=48, height=48)  # 75 packets
    assert result.shown and screen.visible and screen.armed
    assert _starts(link) - starts == 2
    assert KnobEvent(1) in link.events()


def test_input_queue_overflow_during_knob_turns_is_retried(link: Link) -> None:
    """Hardware 2026-11-07: fast spinning stalled the GUI task, the 32-report input queue overflowed
    mid-upload and the device answered 04; the SDK kept sending, waited for an ACK and raised."""
    screen, machine = link.screen, link.machine
    screen.hello()
    screen.enter()
    link.events()
    seen = 0
    respond = link.respond

    def storm(report: bytes) -> list[bytes]:
        nonlocal seen
        if report[0] == 0xBA:
            seen += 1
            if seen == 30:
                machine.inject_knob(1)
                machine.inject_knob(-1)
                machine.set("overflow", 1)
        return respond(report)

    link.transport._responder = storm
    screen._timeouts = Timeouts(reply=.2, hello=.2, ack=5, frame_done=1)
    starts = _starts(link)
    start = time.monotonic()
    result = screen.show_rgb565(bytes(range(256)) * 18, width=48, height=48)
    assert time.monotonic() - start < 2  # stopped at the 04 instead of waiting 5 s for an ACK
    assert result.shown and _starts(link) - starts == 2
    link.transport._responder = respond


def test_busy_without_knob_activity_is_still_reported(link: Link) -> None:
    screen, machine = link.screen, link.machine
    screen.hello()
    screen.enter()
    seen = 0
    respond = link.respond

    def overflow(report: bytes) -> list[bytes]:
        nonlocal seen
        if report[0] == 0xBA:
            seen += 1
            if seen == 30:
                machine.set("overflow", 1)
        return respond(report)

    link.transport._responder = overflow
    with pytest.raises(FrameRejected) as caught:
        screen.show_rgb565(bytes(range(256)) * 18, width=48, height=48)
    assert caught.value.status == 0x04
    link.transport._responder = respond
    assert screen.show_rgb565(b"\xe0\x07" * 4, width=2, height=2).shown


def test_stale_frame_done_before_the_start_ack_is_ignored(link: Link) -> None:
    """Hardware 2026-11-07 (c7d2a4c1): after an input-queue overflow the device sent a 04 with no stream in
    progress; it reached the SDK just after its next start, which then gave up a frame the device had
    accepted (the device stayed in 'receiving'). The SDK now waits briefly for the start's ACK."""
    screen = link.screen
    screen.hello()
    screen.enter()
    link.events()
    respond = link.respond
    def frame_done(status: int, seq: int) -> bytes:
        report = bytearray(64)
        report[0:6] = bytes([0xA5, 0x3B, 0x00, 0x07, 0x00, 0x10])
        report[6:11] = bytes([status, seq & 0xFF, seq >> 8, 0, 0])
        report[11] = (~sum(report[1:11])) & 0xFF
        return bytes(report)

    stale = frame_done(0x04, 999)

    def late_event(report: bytes) -> list[bytes]:
        replies = respond(report)
        if report[0] == 0xBA and report[1:3] == b"\x06\xaa":
            return [stale] + replies
        return replies

    link.transport._responder = late_event
    result = screen.show_rgb565(bytes(range(256)) * 18, width=48, height=48)
    assert result.shown
    assert link.machine.state().state == 0
    link.transport._responder = respond



def test_sdk_inject_knob_drives_the_slot_on_dev_firmware(link: Link) -> None:
    screen = link.screen
    hello = screen.hello()
    assert hello.test_knob
    screen.enter()
    link.events()
    screen.inject_knob(1)
    link.publish()
    assert not screen.visible and screen.armed and link.page() == 0
    assert link.events() == [KnobEvent(1), VisibilityEvent(False, 1)]
    screen.inject_knob(-1)
    link.publish()
    assert screen.visible
    assert link.events() == [KnobEvent(-1), VisibilityEvent(True, 1)]
    with pytest.raises(ValueError):
        screen.inject_knob(0)


def test_release_firmware_turns_the_knob_from_the_pc() -> None:
    value = Link("release")
    try:
        hello = value.screen.hello()
        assert hello.knob and hello.test_knob and hello.knob_takeover and hello.flags == 127
        value.screen.inject_knob(1)   # a stock page step: refused firmware would raise ProtocolError
    finally:
        value.screen.close(leave=False)


@pytest.mark.parametrize("variant", ["dev", "release"])
def test_host_pages_capability(variant: str) -> None:
    value = Link(variant)
    try:
        assert value.screen.capabilities.host_pages
        assert value.screen.set_pages(3) == 0
        status = value.screen.status()
        assert (status.page, status.pages) == (0, 3)
    finally:
        value.screen.close(leave=False)


def test_sdk_steps_through_host_pages_on_the_real_firmware(link: Link) -> None:
    from neos75screen import PageEvent
    screen = link.screen
    screen.enter()
    assert screen.set_pages(3) == 0
    link.events()
    for expected in (1, 2):
        screen.inject_knob(1)
        link.publish()
        assert screen.visible and screen.page == expected
        assert link.events() == [KnobEvent(1), PageEvent(expected, 3)]
    screen.inject_knob(1)                                    # past the last host page
    link.publish()
    assert not screen.visible and link.page() == 0
    assert link.events() == [KnobEvent(1), VisibilityEvent(False, 1)]
    screen.inject_knob(-1)                                   # counter-clockwise entry: the last page
    link.publish()
    assert screen.visible and screen.page == 2
    assert link.events() == [KnobEvent(-1), PageEvent(2, 3), VisibilityEvent(True, 1)]
    frame = screen.show(Image.new("RGB", (480, 360), "white"))
    assert frame.shown
    assert screen.set_pages(1) == 0 and screen.status().pages == 1


def test_sdk_page_cache_is_shown_by_the_knob_on_the_real_firmware(link: Link) -> None:
    import zlib
    from neos75screen.imaging import encode_jpeg
    screen, machine = link.screen, link.machine
    assert screen.capabilities.page_cache
    screen.enter()
    assert screen.set_pages(3) == 0 and screen.cache_slot_bytes == ((0x7E900 - 345600 - 81920) // 3) & ~3
    assert screen.show(Image.new("RGB", (480, 360), "black")).shown
    picture = encode_jpeg(Image.new("RGB", (480, 360), (200, 30, 30)), 60, optimize=True,
                          max_bytes=screen.cache_slot_bytes)
    assert screen.cache_page(1, picture).shown
    machine.decode_pattern = "input"
    link.events()
    screen.inject_knob(1)
    link.publish()
    assert screen.page == 1 and screen.visible
    decoded = bytes(machine.uc.mem_read(0x800FE604, 4))
    assert decoded == (zlib.crc32(picture) & 0xFFFF).to_bytes(2, "little") * 2
    with pytest.raises(ValueError):
        screen.cache_page(0, encode_jpeg(Image.new("RGB", (320, 320)), 80))



def test_sdk_page_tag_drops_frames_of_the_page_the_knob_left(link: Link) -> None:
    from neos75screen import constants as c
    from neos75screen.imaging import encode_jpeg
    screen, machine = link.screen, link.machine
    assert screen.capabilities.page_tag
    screen.enter(); screen.set_pages(3)
    assert screen.show(Image.new("RGB", (480, 360), "black")).shown
    patch = encode_jpeg(Image.new("RGB", (64, 32), "red"), 85)
    screen.frame_page = 0
    assert screen.show_region(patch, 96, 48).shown
    starts = [r for r in link.transport.written if r[:3] == bytes.fromhex("ba06aa")]
    assert starts[-1][8] == 1
    screen.inject_knob(1); link.publish()
    assert screen.page == 1
    result = screen.show_region(patch, 96, 48)            # still drawn for page 0
    assert result.status == c.FRAME_SUPERSEDED and not result.shown
    screen.frame_page = 1
    assert screen.show_region(patch, 96, 48).shown


@pytest.mark.parametrize("variant", ["dev", "release"])
def test_sdk_knob_takeover_on_the_real_firmware(variant: str) -> None:
    """Takeover from the SDK: physical turns are reported as taken and move nothing, a step from the PC still
    pages unreported, and the SDK sends takeover again after a new ENTER."""
    from neos75screen import PageEvent
    link = Link(variant)
    screen = link.screen
    try:
        screen.enter()
        screen.set_pages(3)
        assert screen.set_knob_takeover(True) is True
        link.knob(1)
        link.knob(-1)
        assert link.events() == [VisibilityEvent(True, 2), KnobEvent(1, taken=True), KnobEvent(-1, taken=True)]
        assert screen.status().page == 0 and screen.visible
        screen.inject_knob(1)
        link.publish()
        assert link.events() == [PageEvent(1, 3)] and screen.status().page == 1
        screen.leave()
        assert not link.machine.state().takeover
        screen.enter()
        assert link.machine.state().takeover          # wanted: sent again with ENTER
        assert screen.set_knob_takeover(False) is False
        link.events()
        link.knob(1)
        assert KnobEvent(1, taken=False) in link.events()
    finally:
        screen.close(leave=False)
