"""Packet builders and parsers for docs/PROTOCOL.md. Pure functions, no I/O.

Every builder returns a full 64-byte report (without the Windows report-ID byte).
"""
from __future__ import annotations

import binascii
import struct
import zlib
from dataclasses import dataclass
from datetime import datetime

from . import constants as c


def pad(payload: bytes) -> bytes:
    """Zero-pad a payload to one 64-byte report."""
    if len(payload) > c.REPORT_SIZE:
        raise ValueError(f"payload of {len(payload)} bytes exceeds {c.REPORT_SIZE}")
    return bytes(payload) + bytes(c.REPORT_SIZE - len(payload))


# --- A5 frames --------------------------------------------------------------------------------------

def a5_checksum(body: bytes) -> int:
    """Checksum of an A5 frame: (sum of the bytes after A5) & 0xFF ^ 0xFF."""
    return (sum(body) & 0xFF) ^ 0xFF


def build_a5(cmd: int, data: bytes = b"", index: int = 0) -> bytes:
    """Build `A5 cmd 00 L index data cs` with L = len(data) + 1."""
    body = bytes([cmd, 0x00, len(data) + 1, index]) + bytes(data)
    return pad(bytes([c.A5]) + body + bytes([a5_checksum(body)]))


def a5_frame_length(report: bytes) -> int | None:
    """Length of the A5 frame including its checksum, or None if the L field is out of range."""
    if len(report) < c.A5_HEADER_SIZE or report[0] != c.A5 or report[3] == 0:
        return None
    length = c.A5_HEADER_SIZE + report[3]
    return length if length <= len(report) else None


def a5_checksum_ok(report: bytes) -> bool:
    """True if the A5 frame's checksum (at byte 4 + L) matches."""
    length = a5_frame_length(report)
    return length is not None and report[length - 1] == a5_checksum(report[1:length - 1])


def build_query(kind: int) -> bytes:
    """Read form `A5 41 00 01 kind cs`."""
    return build_a5(c.CMD_QUERY, index=kind)


def build_control(code: int) -> bytes:
    """`A5 39`: page and brightness control (01/02/A1/A2). The device saves the result to flash."""
    if code not in c.CONTROL_CODES:
        raise ValueError(f"unknown control code 0x{code:02X}")
    return build_a5(c.CMD_CONTROL, bytes([code]))


def time_sync_data(when: datetime) -> bytes:
    """Data bytes of `A5 38`: [1, yearHi, yearLo, month, day, h, m, s, weekday]."""
    weekday = when.isoweekday() % 7 if c.TIME_SYNC_SUNDAY_IS_ZERO else when.isoweekday()
    return bytes([c.TIME_SYNC_MARKER, when.year >> 8, when.year & 0xFF, when.month, when.day,
                  when.hour, when.minute, when.second, weekday])


def build_time_sync(when: datetime) -> bytes:
    return build_a5(c.CMD_TIME_SYNC, time_sync_data(when))


def build_realtime(sub: int, args: bytes = b"") -> bytes:
    """Realtime request `A5 3B 00 L 00 sub args cs`."""
    return build_a5(c.CMD_REALTIME, bytes([sub]) + bytes(args))


def query_reply_data(report: bytes, kind: int) -> bytes | None:
    """Data of a reply to `A5 41 kind` (from byte 5), or None if the report is not that reply."""
    if len(report) > c.A5_DATA_OFFSET and report[0] == c.A5 and report[1] == c.CMD_QUERY and report[4] == kind:
        if c.REQUIRE_REPLY_CHECKSUM and not a5_checksum_ok(report):
            return None
        return bytes(report[c.A5_DATA_OFFSET:])
    return None


@dataclass(frozen=True)
class DeviceInfo:
    """Combined info (`A5 41 14`) plus image and GIF space."""

    vid: int
    pid: int
    width: int
    height: int
    screen_type: int
    supports_winusb: bool
    supports_jpeg: bool
    image_space: int | None = None
    gif_space: int | None = None


def parse_info(data: bytes) -> DeviceInfo:
    if len(data) < 11:
        raise ValueError("combined info reply too short")
    vid, pid, width, height = struct.unpack_from("<HHHH", data)
    return DeviceInfo(vid, pid, width, height, data[8], data[9] == 1, data[10] == 1)


def parse_space(data: bytes) -> int:
    if len(data) < 4:
        raise ValueError("space reply too short")
    return int(struct.unpack_from("<I", data)[0])


# --- 11-class queries -------------------------------------------------------------------------------

def crc16_ccitt(data: bytes, crc: int = c.CRC16_INIT) -> int:
    """CRC16-CCITT, poly 0x1021, MSB first (the standard library's `binascii.crc_hqx`)."""
    return binascii.crc_hqx(data, crc)


def class_report_crc(report: bytes) -> int:
    """CRC over all 64 bytes with bytes 6..7 zeroed."""
    body = bytearray(pad(report))
    body[c.CLASS11_CRC_OFFSET:c.CLASS11_CRC_OFFSET + 2] = b"\x00\x00"
    return crc16_ccitt(bytes(body))


def build_class_report(cls: int, sub: int) -> bytes:
    """`cls sub 00 00 00 00 crcLo crcHi` + zeros. Only used with cls 0x11 by the SDK."""
    body = bytearray(pad(bytes([cls, sub])))
    struct.pack_into("<H", body, c.CLASS11_CRC_OFFSET, class_report_crc(bytes(body)))
    return bytes(body)


def build_class11(sub: int) -> bytes:
    if sub not in c.CLASS11_SUBS:
        raise ValueError(f"unknown 11-class query 0x{sub:02X}")
    return build_class_report(c.CLASS11, sub)


def class_report_crc_ok(report: bytes) -> bool:
    stored: int = struct.unpack_from("<H", report, c.CLASS11_CRC_OFFSET)[0]
    return stored == class_report_crc(report)


def class11_reply_text(report: bytes, sub: int) -> str | None:
    """Text of a reply to `11 sub` (from byte 8, NUL-terminated), or None if not that reply.

    ASSUMED: the reply echoes `11 sub` in bytes 0..1.
    """
    if len(report) <= c.CLASS11_TEXT_OFFSET or report[0] != c.CLASS11 or report[1] != sub:
        return None
    text = bytes(report[c.CLASS11_TEXT_OFFSET:]).split(b"\x00", 1)[0]
    return text.decode("latin-1")


# --- Realtime (A5 3B) replies and events ------------------------------------------------------------

@dataclass(frozen=True)
class Hello:
    """Capabilities from HELLO. `realtime` is False for stock firmware (other fields are defaults)."""

    realtime: bool
    proto_version: int = 0
    patch_version: int = 0
    canvas_w: int = 0
    canvas_h: int = 0
    image_w: int = c.STOCK_IMAGE_SIZE[0]
    image_h: int = c.STOCK_IMAGE_SIZE[1]
    formats: int = 0
    max_frame_bytes: int = 0
    buffers: int = 0
    flags: int = 0
    host_timeout_ms: int = 0   # proto 2 only; 0 on proto 1

    @property
    def test_knob(self) -> bool:
        """True if the firmware turns the knob on request (`Screen.inject_knob`): the desktop app, CLI, tests."""
        return self.knob and bool(self.flags & c.FLAG_TEST_KNOB)

    @property
    def knob_takeover(self) -> bool:
        """True if the screen knob's turns can be taken over (`Screen.set_knob_takeover`): reported only,
        no page change, so the PC can map them to its own actions."""
        return self.knob and bool(self.flags & c.FLAG_KNOB_TAKEOVER)

    @property
    def page_tag(self) -> bool:
        """True if frames can name the host page they show (`Screen.frame_page`): the device drops a frame
        whose page the knob has already left, instead of flashing the previous page."""
        return self.realtime and bool(self.flags & c.FLAG_PAGE_TAG)

    @property
    def page_cache(self) -> bool:
        """True if the device keeps one JPEG per host page and shows it the moment the knob gets there
        (`Screen.cache_page`); also: pipelined frame starts and a 5 ms GUI tick while shown."""
        return self.realtime and bool(self.flags & c.FLAG_PAGE_CACHE)

    @property
    def region(self) -> bool:
        """True if the device accepts region frames (`Screen.show_region`): part of the shown canvas."""
        return self.realtime and bool(self.flags & c.FLAG_REGION)

    @property
    def host_pages(self) -> bool:
        """True if the knob can step through several host pages inside the slot (`Screen.set_pages`)."""
        return self.knob and bool(self.flags & c.FLAG_HOST_PAGES)

    @property
    def telemetry(self) -> bool:
        """True if the firmware answers `Screen.keyboard()` (battery, charging and connection)."""
        return self.knob and bool(self.flags & c.FLAG_TELEMETRY)

    @property
    def knob(self) -> bool:
        """True if the device has the knob slot, visibility/knob events and the host keepalive."""
        return self.realtime and self.proto_version >= c.PROTO_KNOB

    @property
    def image_size(self) -> tuple[int, int]:
        return self.image_w, self.image_h

    @property
    def supports_jpeg(self) -> bool:
        return bool(self.formats & c.FORMAT_JPEG)

    @property
    def supports_rgb565(self) -> bool:
        return bool(self.formats & c.FORMAT_RGB565)


@dataclass(frozen=True)
class Status:
    state: int
    frames_ok: int
    frames_err: int
    last_error: int
    last_decode_ms: int
    visible: bool | None = None   # proto 2 only
    armed: bool | None = None     # proto 2 only
    page: int | None = None       # host pages firmware only (HELLO flags bit 2)
    pages: int | None = None

    @property
    def state_name(self) -> str:
        return c.STATE_NAMES.get(self.state, str(self.state))


@dataclass(frozen=True)
class KeyboardStatus:
    """The keyboard status the screen received last (`A5 35` from the keyboard), as the stock status bar
    shows it: battery percent, charging, and the active connection. All zero until the keyboard reports."""

    raw: bytes
    battery: int
    charging: bool
    link: int            # 1..3 Bluetooth channel, 4 2.4G, 5 wired, other: unknown

    @property
    def connection(self) -> str:
        """'bluetooth', '2.4g', 'wired' or 'unknown'."""
        return "bluetooth" if 1 <= self.link <= 3 else {4: "2.4g", 5: "wired"}.get(self.link, "unknown")

    @property
    def bluetooth_channel(self) -> int | None:
        return self.link if 1 <= self.link <= 3 else None

    @property
    def reported(self) -> bool:
        """False before the keyboard has sent any status since the screen booted."""
        return any(self.raw)


@dataclass(frozen=True)
class FrameDone:
    status: int
    seq: int
    decode_ms: int

    @property
    def shown(self) -> bool:
        return self.status == c.FRAME_SHOWN


@dataclass(frozen=True)
class VisibilityEvent:
    """The realtime slot became visible or hidden (reason: knob, host ENTER/LEAVE, or host timeout)."""

    visible: bool
    reason: int


@dataclass(frozen=True)
class PageEvent:
    """The screen knob moved to host page `page` (0-based) of `count` (`Screen.set_pages`). Sent before the
    visibility event when the knob enters the slot, so the right page's frame can be sent first."""

    page: int
    count: int


@dataclass(frozen=True)
class KnobEvent:
    """A knob event: +1 clockwise, -1 counter-clockwise, 0 press.

    `knob` is "screen" (the knob that switches screen pages) or "volume" (the knob that sets the volume).
    `source` is "screen" for events the screen firmware reports (screen-knob turns, while the slot is armed)
    and "pc" for events `Screen.capture_knob` picks up from the keyboard's media keys on the PC. `taken`: the
    firmware took the turn over (`Screen.set_knob_takeover`) and moved no page."""

    direction: int
    knob: str = "screen"
    source: str = "screen"
    taken: bool = False


def is_realtime_report(report: bytes, sub: int) -> bool:
    """True if the report is `A5 3B ... sub` (a response, or the frame-done event for sub 0x10)."""
    ok = len(report) > c.RT_FIELDS_OFFSET and report[0] == c.A5 and report[1] == c.CMD_REALTIME \
        and report[c.A5_DATA_OFFSET] == sub
    return ok and (a5_checksum_ok(report) or not c.REQUIRE_REPLY_CHECKSUM)


def realtime_not_understood(report: bytes) -> bool:
    return report[c.RT_FIELDS_OFFSET] == c.RT_NOT_UNDERSTOOD


def parse_hello(report: bytes) -> Hello | None:
    """Parse a HELLO response; None if the report is not one or lacks the magic."""
    if not is_realtime_report(report, c.RT_HELLO):
        return None
    fields = struct.unpack_from(c.HELLO_FORMAT, report, c.RT_FIELDS_OFFSET)
    magic, proto, patch, canvas_w, canvas_h, image_w, image_h, formats, max_bytes, buffers, flags = fields
    if magic != c.HELLO_MAGIC:
        return None
    host_timeout_ms = 0
    if proto >= c.PROTO_KNOB:
        offset = c.RT_FIELDS_OFFSET + struct.calcsize(c.HELLO_FORMAT)
        host_timeout_ms = struct.unpack_from(c.HELLO_V2_FORMAT, report, offset)[0] or c.DEFAULT_HOST_TIMEOUT_MS
    return Hello(proto in c.PROTO_VERSIONS, proto, patch, canvas_w, canvas_h, image_w, image_h, formats,
                 max_bytes, buffers, flags, host_timeout_ms)


def parse_result_status(report: bytes) -> int:
    """`status` u8 of an ENTER or LEAVE response."""
    return report[c.RT_FIELDS_OFFSET]


def parse_status(report: bytes, proto_version: int = 1) -> Status:
    """STATUS response; proto 2 adds `visible` and `armed`."""
    fields = struct.unpack_from(c.STATUS_FORMAT, report, c.RT_FIELDS_OFFSET)
    if proto_version < c.PROTO_KNOB:
        return Status(*fields)
    visible, armed = struct.unpack_from(c.STATUS_V2_FORMAT, report, c.RT_FIELDS_OFFSET + struct.calcsize(c.STATUS_FORMAT))
    state, frames_ok, frames_err, last_error, last_decode_ms = fields
    page = pages = None
    if report[c.A5_LEN_OFFSET] >= 2 + c.STATUS_PAGES_END:  # index + sub echo + fields: page, pages appended
        page, pages = report[c.RT_FIELDS_OFFSET + c.STATUS_PAGES_END - 2:c.RT_FIELDS_OFFSET + c.STATUS_PAGES_END]
    return Status(state, frames_ok, frames_err, last_error, last_decode_ms, bool(visible), bool(armed), page, pages)


def parse_keyboard_status(report: bytes) -> KeyboardStatus:
    """TELEMETRY response: the 26 raw status bytes."""
    raw = bytes(report[c.RT_FIELDS_OFFSET:c.RT_FIELDS_OFFSET + c.TELEMETRY_SIZE])
    return KeyboardStatus(raw, raw[c.TELEMETRY_BATTERY], raw[c.TELEMETRY_MODE] == c.TELEMETRY_CHARGING,
                          raw[c.TELEMETRY_LINK])


def is_frame_done(report: bytes) -> bool:
    return is_realtime_report(report, c.RT_EVENT_FRAME_DONE)


def parse_frame_done(report: bytes) -> FrameDone:
    return FrameDone(*struct.unpack_from(c.FRAME_DONE_FORMAT, report, c.RT_FIELDS_OFFSET))


def parse_event(report: bytes) -> VisibilityEvent | KnobEvent | PageEvent | None:
    """A visibility (`20`), knob (`21`) or page (`22`) event, or None for any other report."""
    if is_realtime_report(report, c.RT_EVENT_PAGE):
        return PageEvent(report[c.RT_FIELDS_OFFSET], report[c.RT_FIELDS_OFFSET + 1])
    if is_realtime_report(report, c.RT_EVENT_VISIBILITY):
        return VisibilityEvent(bool(report[c.RT_FIELDS_OFFSET]), report[c.RT_FIELDS_OFFSET + 1])
    if is_realtime_report(report, c.RT_EVENT_KNOB):
        code = report[c.RT_FIELDS_OFFSET]
        return KnobEvent({c.KNOB_NEXT: 1, c.KNOB_PREVIOUS: -1}.get(code, 0),
                         taken=report[3] >= 4 and report[c.RT_FIELDS_OFFSET + 1] == 1)  # 3 fields: taken byte
    return None


# --- BA streams -------------------------------------------------------------------------------------

def _stream_header(magic: bytes, width: int, height: int) -> bytes:
    if width % 2 or height % 2:
        raise ValueError(f"frame size {width}x{height} must be even")
    return magic + struct.pack(">HHHB", width, height, c.STREAM_FRAME_COUNT, c.STREAM_FRAME_DELAY)


def build_jpeg_stream(jpeg: bytes, width: int, height: int) -> bytes:
    """`55 42 | W | H | 00 01 | 00 | jpeg_len_be32 | jpeg`, used by types 5A and B0."""
    return _stream_header(c.JPEG_STREAM_MAGIC, width, height) + struct.pack(">I", len(jpeg)) + jpeg


def build_region_stream(jpeg: bytes, x: int, y: int, width: int, height: int) -> bytes:
    """A region frame: `jpeg` (width x height) replaces that rectangle of the shown full frame at (x, y)."""
    return c.REGION_STREAM_MAGIC + struct.pack(">HHHHI", width, height, x, y, len(jpeg)) + jpeg


def build_cache_stream(jpeg: bytes, page: int) -> bytes:
    """A page cache upload: the device stores `jpeg` (480x360) for host page `page`, without showing it."""
    return c.CACHE_STREAM_MAGIC + bytes([page, 0]) + struct.pack(">I", len(jpeg)) + jpeg


def build_rgb565_stream(pixels: bytes, width: int, height: int) -> bytes:
    """`57 51 | W | H | 00 01 | 00 | pixels`, used by type 5B."""
    if len(pixels) != width * height * 2:
        raise ValueError(f"expected {width * height * 2} bytes of RGB565, got {len(pixels)}")
    return _stream_header(c.RGB565_STREAM_MAGIC, width, height) + pixels


@dataclass(frozen=True)
class BaPacket:
    report: bytes
    needs_ack: bool


def build_ba_start(stream_type: int, length: int, page: int | None = None) -> bytes:
    """`page`: page-tag firmware drops the frame (frame-done 07) if the knob has left that host page."""
    tag = b"" if page is None else bytes([page + 1])
    return pad(bytes([c.BA]) + c.BA_START + bytes([stream_type]) + struct.pack(">I", length) + tag)


def _ba_data(chunk: bytes, crc: bytes = b"") -> bytes:
    return pad(bytes([c.BA, len(chunk)]) + chunk + crc)


def looks_like_ba_start(report: bytes) -> bool:
    """True if a report has the stream-start signature `BA 06 AA <type>`. A data packet with n == 6 whose
    data starts with `AA <type>` is byte-identical to a start packet, and the firmware treats it as one."""
    return (len(report) >= 4 and report[0] == c.BA and report[1:3] == c.BA_START
            and report[3] in c.BA_START_TYPES)


def _segment_chunks(segment: bytes) -> list[bytes]:
    """Split one block (or the tail) into data chunks of at most BA_CHUNK bytes. A 6-byte chunk that would
    look like a stream start is sent as 5 + 1 bytes instead (same block, so the framing stays valid)."""
    chunks: list[bytes] = []
    for pos in range(0, len(segment), c.BA_CHUNK):
        chunk = segment[pos:pos + c.BA_CHUNK]
        if looks_like_ba_start(bytes([c.BA, len(chunk)]) + chunk):
            chunks += [chunk[:-1], chunk[-1:]]
        else:
            chunks.append(chunk)
    return chunks


def split_ba_packets(stream_type: int, stream: bytes, page: int | None = None) -> list[BaPacket]:
    """Split a stream into BA packets. The length byte counts data bytes only (at most 62); the last packet
    of every full 4096-byte block carries the block's CRC-32 (little-endian, not counted in the length)
    and waits for an ACK; the shorter tail has neither. No data packet looks like a stream start."""
    packets = [BaPacket(build_ba_start(stream_type, len(stream), page), c.FIRST_PACKET_ACK)]
    whole = len(stream) - len(stream) % c.BA_BLOCK
    for start in range(0, whole, c.BA_BLOCK):
        block = stream[start:start + c.BA_BLOCK]
        crc = struct.pack("<I", zlib.crc32(block))
        chunks = _segment_chunks(block)
        for i, chunk in enumerate(chunks):
            last = i == len(chunks) - 1
            packets.append(BaPacket(_ba_data(chunk, crc if last else b""), last))
    packets += [BaPacket(_ba_data(chunk), False) for chunk in _segment_chunks(stream[whole:])]
    return packets


def parse_ba_start(report: bytes) -> tuple[int, int] | None:
    """(stream type, stream length) of a BA first packet, or None."""
    if len(report) < 8 or report[0] != c.BA or report[1:3] != c.BA_START:
        return None
    return report[3], struct.unpack_from(">I", report, 4)[0]


@dataclass(frozen=True)
class Ack:
    ok: bool
    code: int


def parse_ack(report: bytes) -> Ack | None:
    """ACK report: byte 0 BA, byte 6 FE (anything else in byte 6 is a NACK)."""
    if len(report) <= c.BA_ACK_INDEX or report[0] != c.BA:
        return None
    code = report[c.BA_ACK_INDEX]
    return Ack(code == c.BA_ACK_OK, code)
