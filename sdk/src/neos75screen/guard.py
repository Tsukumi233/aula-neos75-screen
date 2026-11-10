"""Allow-list applied to every outgoing report before it can reach the device.

Allowed:
- A5 `41` queries of the known kinds, `39` with data 01/02/A1/A2, `38` time sync, `3B` realtime requests;
- 11-class `11 01` / `11 02` with a valid CRC;
- BA packets only inside a stream session opened by the SDK: the first packet must be type 5A or 5B
  (B0 only when the session was opened with persistent=True), followed by exactly the announced number
  of data bytes, in data packets that cannot be mistaken for a stream start.

Everything else (`34 xx`, `13 xx`, `FE`, other A5 commands, BA type FC, ...) raises ForbiddenPacket.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import constants as c
from . import protocol as p
from .errors import ForbiddenPacket


@dataclass
class _Stream:
    persistent: bool
    started: bool = False
    length: int = 0
    received: int = 0


def _require_zero(report: bytes, start: int, what: str) -> None:
    if any(report[start:]):
        raise ForbiddenPacket(f"{what}: non-zero bytes after offset {start}")


class PacketGuard:
    """Validates reports and tracks the BA stream the SDK has opened."""

    def __init__(self) -> None:
        self._stream: _Stream | None = None

    def open_stream(self, persistent: bool) -> None:
        if self._stream is not None:
            raise ForbiddenPacket("a BA stream is already in progress")
        self._stream = _Stream(persistent)

    def close_stream(self) -> None:
        self._stream = None

    def check(self, report: bytes) -> None:
        """Raise ForbiddenPacket unless the 64-byte report is allowed; update the stream state if it is."""
        if len(report) != c.REPORT_SIZE:
            raise ForbiddenPacket(f"report must be {c.REPORT_SIZE} bytes, got {len(report)}")
        head = report[0]
        if head == c.A5:
            self._check_a5(report)
        elif head == c.CLASS11:
            self._check_class11(report)
        elif head == c.BA:
            self._check_ba(report)
        else:
            raise ForbiddenPacket(f"report class 0x{head:02X} is not allowed")

    @staticmethod
    def _check_a5(report: bytes) -> None:
        length = p.a5_frame_length(report)
        if length is None or not p.a5_checksum_ok(report):
            raise ForbiddenPacket("malformed A5 frame")
        _require_zero(report, length, "A5 frame")
        cmd, index, data = report[1], report[4], report[c.A5_DATA_OFFSET:length - 1]
        if report[2] != 0:
            raise ForbiddenPacket("A5 byte 2 must be zero")
        if cmd == c.CMD_QUERY:
            if data or index not in c.QUERY_KINDS:
                raise ForbiddenPacket(f"A5 41 kind 0x{index:02X} is not allowed")
            return
        if index != 0:
            raise ForbiddenPacket("A5 byte 4 must be zero")
        if cmd == c.CMD_CONTROL:
            if len(data) != 1 or data[0] not in c.CONTROL_CODES:
                raise ForbiddenPacket(f"A5 39 data {data.hex()} is not allowed")
        elif cmd == c.CMD_TIME_SYNC:
            if len(data) != c.TIME_SYNC_DATA_LEN or data[0] != c.TIME_SYNC_MARKER:
                raise ForbiddenPacket("malformed A5 38 time sync")
        elif cmd == c.CMD_REALTIME:
            injection = len(data) == 2 and data[0] == c.RT_INJECT_KNOB and data[1] in (c.KNOB_NEXT, c.KNOB_PREVIOUS)
            pages = len(data) == 2 and data[0] == c.RT_SET_PAGES and 1 <= data[1] <= c.MAX_HOST_PAGES
            takeover = len(data) == 2 and data[0] == c.RT_KNOB_TAKEOVER and data[1] in (0, 1)
            if not injection and not pages and not takeover and (len(data) != 1 or data[0] not in c.RT_REQUESTS):
                raise ForbiddenPacket(f"A5 3B request {data.hex()} is not allowed")
        else:
            raise ForbiddenPacket(f"A5 command 0x{cmd:02X} is not allowed")

    @staticmethod
    def _check_class11(report: bytes) -> None:
        if report[1] not in c.CLASS11_SUBS:
            raise ForbiddenPacket(f"11-class sub 0x{report[1]:02X} is not allowed")
        _require_zero(report[:c.CLASS11_CRC_OFFSET], 2, "11-class report")
        _require_zero(report, c.CLASS11_CRC_OFFSET + 2, "11-class report")
        if not p.class_report_crc_ok(report):
            raise ForbiddenPacket("11-class report has a bad CRC")

    def _check_ba(self, report: bytes) -> None:
        stream = self._stream
        if stream is None:
            raise ForbiddenPacket("BA packet outside an SDK stream")
        if not stream.started:
            start = p.parse_ba_start(report)
            if start is None:
                raise ForbiddenPacket("first BA packet must be a stream start")
            stream_type, length = start
            if stream_type == c.STREAM_WALLPAPER and not stream.persistent:
                raise ForbiddenPacket("BA type B0 (flash wallpaper) requires persistent=True")
            if stream_type not in c.REALTIME_STREAM_TYPES and stream_type != c.STREAM_WALLPAPER:
                raise ForbiddenPacket(f"BA stream type 0x{stream_type:02X} is not allowed")
            if length == 0:
                raise ForbiddenPacket("empty BA stream")
            tag = report[c.BA_START_TAG_OFFSET]
            if tag and (stream_type not in c.REALTIME_STREAM_TYPES or tag > c.MAX_HOST_PAGES):
                raise ForbiddenPacket(f"BA start page tag {tag} is not allowed")
            _require_zero(report, c.BA_START_TAG_OFFSET + 1, "BA start")
            stream.started, stream.length = True, length
            return
        if p.looks_like_ba_start(report):
            raise ForbiddenPacket("BA data packet would be read as a stream start")
        n = report[1]
        if not 1 <= n <= c.BA_CHUNK or n > stream.length - stream.received:
            raise ForbiddenPacket(f"BA data length {n} is invalid at {stream.received}/{stream.length}")
        block_end = (stream.received + n) % c.BA_BLOCK == 0
        _require_zero(report, 2 + n + (c.BA_CRC_SIZE if block_end else 0), "BA data")
        stream.received += n
