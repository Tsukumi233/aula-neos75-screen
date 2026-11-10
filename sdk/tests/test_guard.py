"""Allow-list tests: forbidden packets raise before reaching the transport (and the fake device)."""
from __future__ import annotations

import struct
from datetime import datetime

import pytest

from conftest import gradient
from fake_device import FakeScreen, a5
from neos75screen import ForbiddenPacket, MockTransport
from neos75screen import protocol as p


@pytest.fixture
def wired() -> tuple[MockTransport, FakeScreen]:
    fake = FakeScreen()
    transport = MockTransport(fake)
    fake.attach(transport)
    return transport, fake


def _a5_raw(cmd: int, data: bytes = b"", index: int = 0) -> bytes:
    return a5(cmd, data, index)


FORBIDDEN = {
    "34 02 factory reset": bytes([0x34, 0x02]),
    "34 03 bootloader": bytes([0x34, 0x03]),
    "13 01 reboot": p.build_class_report(0x13, 0x01),
    "13 02 DFU": p.build_class_report(0x13, 0x02),
    "13 03 clear image": p.build_class_report(0x13, 0x03),
    "FE": bytes([0xFE, 0x01]),
    "11 03": p.build_class_report(0x11, 0x03),
    "11 01 bad CRC": bytes([0x11, 0x01, 0, 0, 0, 0, 0, 0]),
    "11 01 extra bytes": p.build_class_report(0x11, 0x01)[:8] + b"\x01",
    "A5 32 power": _a5_raw(0x32, b"\x00"),
    "A5 34": _a5_raw(0x34, b"\x02"),
    "A5 35 percent": _a5_raw(0x35, b"\x50"),
    "A5 3A key label": _a5_raw(0x3A, b"\x01"),
    "A5 FE": _a5_raw(0xFE, b"\x00"),
    "A5 39 unknown data": _a5_raw(0x39, b"\x03"),
    "A5 39 two bytes": _a5_raw(0x39, b"\x01\x01"),
    "A5 41 unknown kind": _a5_raw(0x41, b"", 0x12),
    "A5 41 with data": _a5_raw(0x41, b"\x00", 0x14),
    "A5 38 wrong length": _a5_raw(0x38, b"\x01\x07"),
    "A5 3B event sub": _a5_raw(0x3B, b"\x10"),
    "A5 3B unknown sub": _a5_raw(0x3B, b"\x06"),
    "A5 3B with args": _a5_raw(0x3B, b"\x01\x00"),
    "A5 bad checksum": p.build_query(0x14)[:5] + b"\x00",
    "A5 trailing bytes": p.build_query(0x14)[:6] + b"\x01",
    "BA outside a stream": p.build_ba_start(0x5A, 100),
    "BA data outside a stream": bytes([0xBA, 0x02, 0x55, 0x42]),
    "empty report": b"",
}


@pytest.mark.parametrize("name", sorted(FORBIDDEN))
def test_forbidden_packets_never_reach_the_device(name: str, wired: tuple[MockTransport, FakeScreen]) -> None:
    transport, fake = wired
    with pytest.raises(ForbiddenPacket):
        transport.write(FORBIDDEN[name])
    assert transport.written == [] and fake.log == []


def test_allowed_commands_pass(wired: tuple[MockTransport, FakeScreen]) -> None:
    transport, fake = wired
    allowed = [p.build_query(k) for k in (0x14, 0x10, 0x11)]
    allowed += [p.build_control(code) for code in (0x01, 0x02, 0xA1, 0xA2)]
    allowed += [p.build_time_sync(datetime(2026, 1, 2, 3, 4, 5)), p.build_class11(1), p.build_class11(2)]
    allowed += [p.build_realtime(sub) for sub in (1, 2, 3, 4)]
    for report in allowed:
        transport.write(report)
    assert transport.written == allowed and len(fake.log) == len(allowed)


@pytest.mark.parametrize("stream_type, persistent", [(0xFC, True), (0xB0, False), (0x00, False), (0x5C, False)])
def test_stream_types_refused(stream_type: int, persistent: bool, wired: tuple[MockTransport, FakeScreen]) -> None:
    transport, fake = wired
    with transport.stream_session(persistent=persistent), pytest.raises(ForbiddenPacket):
        transport.write(p.build_ba_start(stream_type, 100))
    assert transport.written == [] and fake.log == []


def test_wallpaper_type_needs_persistent(wired: tuple[MockTransport, FakeScreen]) -> None:
    transport, _ = wired
    with transport.stream_session(persistent=True):
        transport.write(p.build_ba_start(0xB0, 100))
    assert transport.written[0][3] == 0xB0


def test_stream_session_limits_data_to_announced_length(wired: tuple[MockTransport, FakeScreen]) -> None:
    transport, _ = wired
    stream = bytes(range(100))
    packets = p.split_ba_packets(0x5A, stream)
    with transport.stream_session():
        with pytest.raises(ForbiddenPacket):
            transport.write(packets[1].report)  # data before the start packet
        for packet in packets:
            transport.write(packet.report)
        with pytest.raises(ForbiddenPacket):
            transport.write(packets[-1].report)  # beyond the announced length
        with pytest.raises(ForbiddenPacket):
            transport.write(p.build_ba_start(0x5A, 10))  # a second stream in the same session
    with pytest.raises(ForbiddenPacket):
        transport.write(packets[1].report)  # session closed
    assert len(transport.written) == len(packets)


def test_ba_data_packet_rules(wired: tuple[MockTransport, FakeScreen]) -> None:
    transport, _ = wired
    with transport.stream_session():
        transport.write(p.build_ba_start(0x5B, 4096 + 10))
        with pytest.raises(ForbiddenPacket):
            transport.write(bytes([0xBA, 63]) + bytes(62))  # n > 62
        with pytest.raises(ForbiddenPacket):
            transport.write(bytes([0xBA, 0]))  # n == 0
        with pytest.raises(ForbiddenPacket):
            transport.write(bytes([0xBA, 2, 1, 2, 9]))  # garbage after the data
        for _ in range(66):
            transport.write(bytes([0xBA, 62]) + bytes(62))
        transport.write(bytes([0xBA, 4]) + bytes(4) + struct.pack("<I", 0x12345678))  # block end carries a CRC
        with pytest.raises(ForbiddenPacket):
            transport.write(bytes([0xBA, 4]) + bytes(4) + b"\x01")  # mid-block packet carries no CRC


def test_start_lookalike_data_packet_refused(wired: tuple[MockTransport, FakeScreen]) -> None:
    transport, fake = wired
    with transport.stream_session():
        transport.write(p.build_ba_start(0x5A, 100))
        with pytest.raises(ForbiddenPacket, match="stream start"):
            transport.write(bytes([0xBA, 0x06, 0xAA, 0x5B, 0, 0, 0, 0x10]))
    assert len(fake.log) == 1


def test_nested_sessions_refused(wired: tuple[MockTransport, FakeScreen]) -> None:
    transport, _ = wired
    with transport.stream_session(), pytest.raises(ForbiddenPacket):
        with transport.stream_session():
            pass


def test_screen_wallpaper_requires_explicit_persistent(connect) -> None:  # type: ignore[no-untyped-def]
    screen, fake, transport = connect(patched=False)
    before = len(transport.written)
    with pytest.raises(ValueError):
        screen.upload_wallpaper(gradient())
    with pytest.raises(ValueError):
        screen.upload_wallpaper(gradient(), persistent=1)  # type: ignore[arg-type]
    assert len(transport.written) == before and fake.wallpaper is None



def test_knob_injection_request_allowed_only_with_a_step() -> None:
    from neos75screen import constants as c
    from neos75screen import protocol as p
    from neos75screen.errors import ForbiddenPacket
    from neos75screen.guard import PacketGuard as Guard

    guard = Guard()
    guard.check(p.build_realtime(c.RT_INJECT_KNOB, bytes([c.KNOB_NEXT])))
    guard.check(p.build_realtime(c.RT_INJECT_KNOB, bytes([c.KNOB_PREVIOUS])))
    for bad in (b"", b"\x00", b"\x02", b"\x01\x01"):
        with pytest.raises(ForbiddenPacket):
            guard.check(p.build_realtime(c.RT_INJECT_KNOB, bad))



def test_page_tag_only_on_realtime_starts() -> None:
    from neos75screen.guard import PacketGuard
    from neos75screen.errors import ForbiddenPacket
    for stream_type, tag, ok in ((0x5A, 1, True), (0x5A, 8, True), (0x5A, 9, False), (0xB0, 1, False)):
        guard = PacketGuard(); guard.open_stream(persistent=stream_type == 0xB0)
        start = bytearray(p.build_ba_start(stream_type, 100)); start[8] = tag
        try:
            guard.check(bytes(start)); passed = True
        except ForbiddenPacket:
            passed = False
        assert passed == ok, (stream_type, tag)
