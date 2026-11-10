from __future__ import annotations

import os
import random
import struct
import zlib
from datetime import datetime

import pytest

from fake_device import a5
from neos75screen import constants as c
from neos75screen import protocol as p


@pytest.mark.parametrize("cls, sub, expected", [(0x11, 0x01, "F9 2F"), (0x11, 0x02, "FD 1B"), (0x13, 0x02, "78 B7")])
def test_class_report_crc_vectors(cls: int, sub: int, expected: str) -> None:
    report = p.build_class_report(cls, sub)
    assert len(report) == 64
    assert report[:6] == bytes([cls, sub, 0, 0, 0, 0])
    assert report[6:8] == bytes.fromhex(expected)
    assert not any(report[8:])
    assert p.class_report_crc_ok(report)


def test_build_class11_rejects_other_subs() -> None:
    with pytest.raises(ValueError):
        p.build_class11(0x03)


def test_a5_query_frame() -> None:
    report = p.build_query(0x14)
    assert report[:6] == bytes([0xA5, 0x41, 0x00, 0x01, 0x14, (0x41 + 0x01 + 0x14) ^ 0xFF])
    assert not any(report[6:])
    assert p.a5_checksum_ok(report)


def test_a5_control_and_realtime_frames() -> None:
    assert p.build_control(0x01)[:7] == bytes.fromhex("A5 39 00 02 00 01 C3")
    assert p.build_realtime(c.RT_HELLO)[:7] == bytes.fromhex("A5 3B 00 02 00 01 C1")
    with pytest.raises(ValueError):
        p.build_control(0x03)


def test_time_sync_layout() -> None:
    report = p.build_time_sync(datetime(2026, 11, 6, 23, 18, 16))  # a Friday
    assert report[:5] == bytes([0xA5, 0x38, 0x00, 10, 0x00])
    assert report[5:14] == bytes([1, 0x07, 0xEA, 11, 6, 23, 18, 16, 5])
    assert p.a5_checksum_ok(report)
    assert p.time_sync_data(datetime(2026, 11, 8))[-1] == 0  # Sunday


def test_query_reply_parsing() -> None:
    report = p.pad(a5(0x41, struct.pack("<HHHH", 0x5542, 1, 320, 320) + bytes([2, 0, 1]), 0x14))
    data = p.query_reply_data(report, 0x14)
    assert data is not None
    info = p.parse_info(data)
    assert (info.vid, info.pid, info.width, info.height, info.supports_jpeg) == (0x5542, 1, 320, 320, True)
    assert p.query_reply_data(report, 0x10) is None
    assert p.parse_space(p.query_reply_data(p.pad(a5(0x41, struct.pack("<I", 520192), 0x10)), 0x10) or b"") == 520192


def test_class11_reply_text() -> None:
    report = p.pad(bytes([0x11, 0x01, 0, 0, 0, 0, 0, 0]) + b"ZG06_2.31_V2.0")
    assert p.class11_reply_text(report, 0x01) == "ZG06_2.31_V2.0"
    assert p.class11_reply_text(report, 0x02) is None


def test_hello_round_trip() -> None:
    fields = struct.pack("<2sBHHHHHBIBB", b"NS", 1, 0x0102, 480, 360, 320, 320, 3, 86016, 2, 0x80)
    hello = p.parse_hello(p.pad(a5(0x3B, b"\x01" + fields)))
    assert hello == p.Hello(True, 1, 0x0102, 480, 360, 320, 320, 3, 86016, 2, 0x80)
    assert hello.supports_jpeg and hello.supports_rgb565 and hello.image_size == (320, 320)


def test_hello_without_magic_or_other_version() -> None:
    assert p.parse_hello(p.pad(a5(0x3B, b"\x01\xEE"))) is None
    fields = struct.pack("<2sBHHHHHBIBB", b"NS", 3, 0, 0, 0, 320, 320, 1, 1000, 1, 0)
    hello = p.parse_hello(p.pad(a5(0x3B, b"\x01" + fields + struct.pack("<H", 5000))))
    assert hello is not None and not hello.realtime and hello.proto_version == 3


def test_hello_proto2_host_timeout() -> None:
    base = struct.pack("<2sBHHHHHBIBB", b"NS", 2, 2, 480, 360, 480, 360, 3, 172804, 1, 0)
    hello = p.parse_hello(p.pad(a5(0x3B, b"\x01" + base + struct.pack("<H", 4000))))
    assert hello is not None and hello.realtime and hello.knob and hello.host_timeout_ms == 4000
    hello = p.parse_hello(p.pad(a5(0x3B, b"\x01" + base + b"\x00\x00")))
    assert hello is not None and hello.host_timeout_ms == c.DEFAULT_HOST_TIMEOUT_MS
    v1 = struct.pack("<2sBHHHHHBIBB", b"NS", 1, 2, 480, 360, 480, 360, 3, 172804, 1, 0)
    hello = p.parse_hello(p.pad(a5(0x3B, b"\x01" + v1)))
    assert hello is not None and hello.realtime and not hello.knob and hello.host_timeout_ms == 0


def test_status_and_frame_done() -> None:
    status = p.parse_status(p.pad(a5(0x3B, b"\x04" + struct.pack("<BIIBH", 1, 10, 2, 3, 42))))
    assert status == p.Status(1, 10, 2, 3, 42)
    assert status.visible is None and status.armed is None
    v2 = p.pad(a5(0x3B, b"\x04" + struct.pack("<BIIBHBB", 1, 10, 2, 3, 42, 1, 0)))
    assert p.parse_status(v2, 2) == p.Status(1, 10, 2, 3, 42, True, False)
    event = p.pad(a5(0x3B, bytes([0x10, 0x01, 0x34, 0x12, 0x07, 0x00])))
    assert p.is_frame_done(event)
    assert p.parse_frame_done(event) == p.FrameDone(1, 0x1234, 7)
    assert not p.is_frame_done(p.pad(a5(0x3B, b"\x04\x00")))


def test_knob_and_visibility_events() -> None:
    assert p.parse_event(p.pad(a5(0x3B, bytes([0x20, 0x01, 0x01])))) == p.VisibilityEvent(True, 1)
    assert p.parse_event(p.pad(a5(0x3B, bytes([0x20, 0x00, 0x03])))) == p.VisibilityEvent(False, 3)
    assert p.parse_event(p.pad(a5(0x3B, bytes([0x21, 0x01])))) == p.KnobEvent(1)
    assert p.parse_event(p.pad(a5(0x3B, bytes([0x21, 0xFF])))) == p.KnobEvent(-1)
    assert p.parse_event(p.pad(a5(0x3B, bytes([0x21, 0x00])))) == p.KnobEvent(0)
    assert p.parse_event(p.pad(a5(0x3B, bytes([0x10, 0x01, 0, 0, 0, 0])))) is None


def test_result_status_and_not_understood() -> None:
    assert p.parse_result_status(p.pad(a5(0x3B, b"\x02\x00"))) == 0
    assert p.realtime_not_understood(p.pad(a5(0x3B, b"\x02\xEE")))


def test_ack_parsing() -> None:
    assert p.parse_ack(p.pad(bytes([0xBA, 0, 0, 0, 0, 0, 0xFE]))) == p.Ack(True, 0xFE)
    assert p.parse_ack(p.pad(bytes([0xBA, 0, 0, 0, 0, 0, 0x01]))) == p.Ack(False, 0x01)
    assert p.parse_ack(p.pad(b"\xA5")) is None


def test_stream_headers() -> None:
    jpeg = b"\xff\xd8abc"
    stream = p.build_jpeg_stream(jpeg, 320, 240)
    assert stream == bytes.fromhex("5542 0140 00F0 0001 00 00000005") + jpeg
    pixels = bytes(4 * 2 * 2)
    assert p.build_rgb565_stream(pixels, 4, 2) == bytes.fromhex("5751 0004 0002 0001 00") + pixels
    with pytest.raises(ValueError):
        p.build_jpeg_stream(jpeg, 321, 240)
    with pytest.raises(ValueError):
        p.build_rgb565_stream(pixels, 4, 4)


def _reassemble(packets: list[p.BaPacket]) -> tuple[int, bytes, int]:
    start = p.parse_ba_start(packets[0].report)
    assert start is not None
    stream_type, length = start
    out = bytearray()
    acks = int(packets[0].needs_ack)
    for packet in packets[1:]:
        report = packet.report
        assert len(report) == 64 and report[0] == 0xBA
        assert not (report[1:3] == bytes([0x06, 0xAA]) and report[3] in (0x5A, 0x5B, 0xB0, 0xFC)), "ambiguous packet"
        n = report[1]
        assert 1 <= n <= 62
        assert len(out) // 4096 == (len(out) + n - 1) // 4096, "packet crosses a block boundary"
        out += report[2:2 + n]
        if packet.needs_ack:
            acks += 1
            assert len(out) % 4096 == 0
            assert struct.unpack_from("<I", report, 2 + n)[0] == zlib.crc32(bytes(out[-4096:]))
            assert not any(report[6 + n:])
        else:
            assert not any(report[2 + n:])
    assert len(out) == length
    return stream_type, bytes(out), acks


@pytest.mark.parametrize("length", [1, 62, 63, 4095, 4096, 4097, 8192, 20000])
def test_ba_split_round_trip(length: int) -> None:
    stream = os.urandom(length)
    packets = p.split_ba_packets(0x5A, stream)
    stream_type, data, acks = _reassemble(packets)
    assert (stream_type, data) == (0x5A, stream)
    assert acks == int(c.FIRST_PACKET_ACK) + length // 4096
    assert packets[0].report[:8] == bytes([0xBA, 0x06, 0xAA, 0x5A]) + struct.pack(">I", length)


@pytest.mark.parametrize("prefix_len", [0, 62, 4096, 4096 + 62 * 3, 3 * 4096 + 62])
@pytest.mark.parametrize("stream_type", [0x5A, 0x5B, 0xB0, 0xFC])
def test_ba_split_avoids_start_lookalike(prefix_len: int, stream_type: int) -> None:
    tail = bytes([0xAA, stream_type, 0x00, 0x00, 0x10, 0x00])
    stream = os.urandom(prefix_len) + tail
    # The naive stock split puts exactly these 6 bytes in one data packet: `BA 06 AA <type> ...`.
    assert (len(stream) % 4096) % 62 == 6
    packets = p.split_ba_packets(0x5A, stream)
    assert not any(p.looks_like_ba_start(packet.report) for packet in packets[1:])
    _, data, acks = _reassemble(packets)
    assert data == stream and acks == int(c.FIRST_PACKET_ACK) + len(stream) // 4096


def test_ba_split_property() -> None:
    rng = random.Random(75)
    patterns = [bytes([0xAA, t]) for t in (0x5A, 0x5B, 0xB0, 0xFC)]
    lengths = sorted({*range(1, 300), *(4096 * k + d for k in range(1, 5) for d in range(-70, 70) if 4096 * k + d > 0),
                      *(rng.randrange(1, 60000) for _ in range(300))})
    for length in lengths:
        if rng.random() < 0.5:
            stream = bytes(rng.getrandbits(8) for _ in range(length))
        else:  # every even offset starts with `AA <type>`, so every naive 6-byte chunk is ambiguous
            stream = (rng.choice(patterns) * (length // 2 + 1))[:length]
        packets = p.split_ba_packets(0x5B, stream)
        assert not any(p.looks_like_ba_start(packet.report) for packet in packets[1:]), length
        stream_type, data, acks = _reassemble(packets)
        assert (stream_type, data) == (0x5B, stream), length
        assert acks == int(c.FIRST_PACKET_ACK) + length // 4096, length


def test_pad_rejects_long_payload() -> None:
    with pytest.raises(ValueError):
        p.pad(bytes(65))
