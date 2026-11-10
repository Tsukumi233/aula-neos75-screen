"""Fake screen behind MockTransport, written from docs/PROTOCOL.md independently of the SDK's builders.

`patched=False` behaves like stock firmware (ignores A5 3B and BA types 5A/5B); `patched=True` adds the
realtime protocol: HELLO/ENTER/LEAVE/STATUS, BA 5A/5B reception into `buffers` RAM buffers with block
CRC checks and ACKs, and frame-done events (shown, decode error, too large, busy, not realtime).

Profiles of the patched firmware:
- "v2": image 480 x 360 (the whole panel), patch_version 2; smaller frames are centred on black.
- "v1": image 320 x 320, patch_version 1; frames appear in a 320 x 320 square on the panel's right side.
- "v2-knob": v2 plus PROTOCOL.md section 5 (proto_version 2): the realtime slot sits in a knob cycle with
  STOCK_PAGES stock pages, is armed by ENTER and disarmed by LEAVE or `host_timeout_ms` without host
  activity, sends visibility (20) and knob (21) events, and answers frames that end while hidden with 05.
Frames larger than the image are rejected with status 03. `panel` holds what the 480 x 360 panel shows.
"""
from __future__ import annotations

import binascii
import io
import struct
import threading
import time
import zlib
from dataclasses import dataclass, field

from PIL import Image

from neos75screen.transport import MockTransport

MODEL = "ZG06_2.31_V2.0"
BUILD = "V1.0.3 Sep  6 2026 23:18:16"
IMAGE_SPACE = 520192
GIF_SPACE = 13807616
PANEL = (480, 360)
MAX_FRAME_BYTES = 172804


STOCK_PAGES = 3


@dataclass(frozen=True)
class Profile:
    patch_version: int
    image: tuple[int, int]
    area_origin: tuple[int, int]   # top-left of the realtime area on the panel
    knob: bool = False             # section 5 (proto_version 2)


PROFILES = {"v1": Profile(1, (320, 320), (160, 20)), "v2": Profile(2, PANEL, (0, 0)),
            "v2-knob": Profile(2, PANEL, (0, 0), knob=True)}


def a5(cmd: int, data: bytes, index: int = 0) -> bytes:
    body = bytes([cmd, 0, len(data) + 1, index]) + data
    return bytes([0xA5]) + body + bytes([(sum(body) & 0xFF) ^ 0xFF])


def ack(code: int = 0xFE) -> bytes:
    return bytes([0xBA, 0, 0, 0, 0, 0, code])


@dataclass
class _Rx:
    stream_type: int
    length: int
    data: bytearray = field(default_factory=bytearray)
    block_fill: int = 0
    dropped: bool = False


class FakeScreen:
    def __init__(self, *, patched: bool = True, profile: str = "v2", buffers: int = 1,
                 hello_buffers: int | None = None, formats: int = 0x03, image: tuple[int, int] | None = None,
                 hello_image: tuple[int, int] | None = None, max_frame_bytes: int = MAX_FRAME_BYTES,
                 proto_version: int | None = None, decode_delay: float = 0.0, force_status: int | None = None,
                 send_events: bool = True, nack_block: int | None = None, unknown_subs: frozenset[int] = frozenset(),
                 host_timeout_ms: int = 10000, hide_on_frame: int | None = None,
                 keyboard_status: bytes | None = None, host_pages: bool = False, knob_takeover: bool = False):
        self.profile = PROFILES[profile]
        self.host_timeout_ms = host_timeout_ms
        self.keyboard_status = keyboard_status  # 26 raw bytes; HELLO flags bit 1 when set (knob profile)
        self.host_pages = host_pages            # HELLO flags bit 2: sub 06 and page events
        self.knob_takeover = knob_takeover      # HELLO flags bit 6: sub 07 (turns only reported)
        self.takeover = False
        self.pages, self.host_page = 1, 0
        self.page_events: list[tuple[int, int]] = []
        self.hide_on_frame = hide_on_frame  # turn the knob away when this realtime frame (1-based) starts
        self.patched = patched
        self.buffers = buffers
        self.hello_buffers = buffers if hello_buffers is None else hello_buffers
        self.formats = formats
        self.image = image or self.profile.image          # largest frame the device accepts
        self.hello_image = hello_image or self.image       # what HELLO claims
        self.max_frame_bytes = max_frame_bytes
        self.proto_version = proto_version if proto_version is not None else 2 if self.profile.knob else 1
        self.decode_delay = decode_delay
        self.force_status = force_status
        self.send_events = send_events
        self.nack_block = nack_block
        self.unknown_subs = unknown_subs

        self.log: list[bytes] = []
        self.shown: list[Image.Image] = []
        self.panel = Image.new("RGB", PANEL)
        self.statuses: list[int] = []
        self.page = 0
        self.brightness = 3
        self.clock: bytes | None = None
        self.wallpaper: bytes | None = None
        self.realtime = False              # the realtime page is visible
        self.armed = False
        self.knob_events: list[int] = []
        self.visibility_events: list[tuple[int, int]] = []
        self.last_activity = time.monotonic()
        self.realtime_frames = 0
        self.frames_ok = 0
        self.frames_err = 0
        self.last_error = 0
        self.last_decode_ms = 0
        self.seq = 0
        self.blocks = 0
        self.pending = 0
        self.max_pending = 0
        self._rx: _Rx | None = None
        self._lock = threading.Lock()
        self._io_lock = threading.RLock()
        self._inject = lambda report: None
        self._stop = threading.Event()

    def attach(self, transport: MockTransport) -> None:
        self._inject = transport.inject
        if self.profile.knob:
            threading.Thread(target=self._watchdog, daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def __call__(self, report: bytes) -> list[bytes]:
        assert len(report) == 64, "every report must be 64 bytes"
        with self._io_lock:
            self.log.append(report)
            handler = {0xA5: self._a5, 0x11: self._class11, 0xBA: self._ba}.get(report[0])
            return handler(report) if handler else []

    # --- knob cycle (v2-knob) ---------------------------------------------------------------------

    def knob(self, direction: int, from_host: bool = False) -> None:
        """Turn the knob one step: +1 next, -1 previous, 0 press. `from_host`: a step the host asked for
        (A5 3B 30), which pages even under takeover and is then not reported."""
        with self._io_lock:
            taken = self.armed and self.takeover and direction != 0
            if self.armed and not (taken and from_host):
                self.knob_events.append(direction)
                self._inject(a5(0x3B, bytes([0x21, {1: 0x01, -1: 0xFF, 0: 0x00}[direction], int(taken)])))
            if direction == 0 or (taken and not from_host):
                return
            if self.realtime:
                target = self.host_page + direction
                if 0 <= target < self.pages:  # move between host pages inside the slot
                    self.host_page = target
                    self._page_event()
                    return
                # leave the slot for the neighbouring stock page
                self.page = 0 if direction > 0 else STOCK_PAGES - 1
                self._visibility(False, 0x01)
                return
            target = self.page + direction
            if self.armed and not 0 <= target < STOCK_PAGES:
                # the slot sits between the last and the first stock page
                self.host_page = 0 if direction > 0 else self.pages - 1
                if self.pages > 1:
                    self._page_event()
                self._visibility(True, 0x01)
            else:
                self.page = target % STOCK_PAGES

    def _page_event(self) -> None:
        self.page_events.append((self.host_page, self.pages))
        self._inject(a5(0x3B, bytes([0x22, self.host_page, self.pages])))

    def _visibility(self, visible: bool, reason: int) -> None:
        self.realtime = visible
        self.visibility_events.append((int(visible), reason))
        self._inject(a5(0x3B, bytes([0x20, int(visible), reason])))

    def _activity(self) -> None:
        self.last_activity = time.monotonic()

    def _watchdog(self) -> None:
        while not self._stop.wait(0.01):
            with self._io_lock:
                idle = time.monotonic() - self.last_activity
                if self.armed and idle * 1000 > self.host_timeout_ms:
                    self.armed = self.takeover = False
                    if self.realtime:
                        self._visibility(False, 0x03)

    # --- A5 ---------------------------------------------------------------------------------------

    def _a5(self, report: bytes) -> list[bytes]:
        length = report[3]
        assert report[4 + length] == (sum(report[1:4 + length]) & 0xFF) ^ 0xFF, "bad A5 checksum"
        cmd, index, data = report[1], report[4], report[5:4 + length]
        if cmd == 0x41:
            payload = {0x14: struct.pack("<HHHH", 0x5542, 0x0001, 320, 320) + bytes([2, 0, 1]),
                       0x10: struct.pack("<I", IMAGE_SPACE), 0x11: struct.pack("<I", GIF_SPACE)}.get(index)
            return [a5(0x41, payload, index)] if payload else []
        if cmd == 0x39:
            code = data[0]
            if code in (0x01, 0x02):
                self.page = (self.page + (1 if code == 0x01 else -1)) % STOCK_PAGES
            elif code in (0xA1, 0xA2):
                self.brightness += 1 if code == 0xA1 else -1
            return []
        if cmd == 0x38:
            self.clock = bytes(data)
            return []
        if cmd == 0x3B and self.patched:
            self._activity()
            if data[0] == 0x06 and len(data) == 2 and self.host_pages:
                if not 1 <= data[1] <= 8:
                    return [a5(0x3B, bytes([0x06, 0xEE]))]
                self.pages = data[1]
                self.host_page = min(self.host_page, self.pages - 1)
                return [a5(0x3B, bytes([0x06, 0, self.host_page, self.pages]))]
            if data[0] == 0x07 and len(data) == 2 and self.knob_takeover:
                if data[1] > 1 or not self.armed:
                    return [a5(0x3B, bytes([0x07, 0xEE, int(self.takeover)]))]
                self.takeover = bool(data[1])
                return [a5(0x3B, bytes([0x07, 0, int(self.takeover)]))]
            if data[0] == 0x30 and len(data) == 2 and self.knob_takeover:
                if data[1] not in (0x01, 0xFF):
                    return [a5(0x3B, bytes([0x30, 0xEE]))]
                threading.Timer(0.002, self.knob, (1 if data[1] == 0x01 else -1, True)).start()
                return [a5(0x3B, bytes([0x30, 0]))]
            reply = self._realtime(data[0])
            if self.profile.knob and data[0] in (0x02, 0x03):
                self._inject(reply)
                self._visibility(data[0] == 0x02, 0x02)
                return []
            return [reply]
        return []

    def _realtime(self, sub: int) -> bytes:
        if sub in self.unknown_subs:
            return a5(0x3B, bytes([sub, 0xEE]))
        if sub == 0x01:
            fields = struct.pack("<2sBHHHHHBIBB", b"NS", self.proto_version, self.profile.patch_version, *PANEL,
                                 *self.hello_image, self.formats, self.max_frame_bytes, self.hello_buffers,
                                 (2 if self.keyboard_status is not None else 0) | (4 if self.host_pages else 0)
                                 | (0x41 if self.knob_takeover else 0))
            if self.proto_version >= 2:
                fields += struct.pack("<H", self.host_timeout_ms)
        elif sub == 0x02:
            self.realtime, self.armed, fields = True, True, b"\x00"
        elif sub == 0x03:
            self.realtime, self.armed, self.takeover, fields = False, False, False, b"\x00"
        elif sub == 0x04:
            fields = struct.pack("<BIIBH", int(self.realtime), self.frames_ok, self.frames_err,
                                 self.last_error, self.last_decode_ms)
            if self.proto_version >= 2:
                fields += bytes([int(self.realtime), int(self.armed)])
                if self.host_pages:
                    fields += bytes([self.host_page, self.pages])
        elif sub == 0x05 and self.keyboard_status is not None:
            fields = self.keyboard_status
        else:
            fields = b"\xEE"
        return a5(0x3B, bytes([sub]) + fields)

    # --- 11-class ---------------------------------------------------------------------------------

    def _class11(self, report: bytes) -> list[bytes]:
        body = bytearray(report)
        stored = struct.unpack_from("<H", body, 6)[0]
        body[6:8] = b"\x00\x00"
        assert binascii.crc_hqx(bytes(body), 0xFFFF) == stored, "bad 11-class CRC"
        text = {0x01: MODEL, 0x02: BUILD}.get(report[1])
        return [bytes([0x11, report[1], 0, 0, 0, 0, 0, 0]) + text.encode()] if text else []

    # --- BA ---------------------------------------------------------------------------------------

    def _ba(self, report: bytes) -> list[bytes]:
        rx = self._rx
        if rx is None:
            assert report[1:3] == b"\x06\xAA", "expected a BA start packet"
            stream_type, length = report[3], struct.unpack_from(">I", report, 4)[0]
            if stream_type in (0x5A, 0x5B) and not self.patched:
                return []  # stock firmware does not know these types
            self._rx = _Rx(stream_type, length)
            if stream_type in (0x5A, 0x5B):
                self._activity()
                self.realtime_frames += 1
                if self.realtime_frames == self.hide_on_frame:
                    self.knob(+1)
            if stream_type in (0x5A, 0x5B):
                with self._lock:
                    if self.pending >= self.buffers:
                        self._rx.dropped = True
                    else:
                        self.pending += 1
                        self.max_pending = max(self.max_pending, self.pending)
            return [ack()]
        # Like the firmware, a packet with the start signature starts a new stream; the SDK must never send one.
        assert not (report[1:3] == bytes([0x06, 0xAA]) and report[3] in (0x5A, 0x5B, 0xB0, 0xFC)), (
            "data packet is ambiguous with a stream start")
        n = report[1]
        assert 1 <= n <= 62, f"bad BA data length {n}"
        if rx.stream_type in (0x5A, 0x5B):
            self._activity()
        rx.data += report[2:2 + n]
        rx.block_fill += n
        replies = []
        if rx.block_fill == 4096:
            crc = struct.unpack_from("<I", report, 2 + n)[0]
            ok = crc == zlib.crc32(bytes(rx.data[-4096:])) and self.blocks != self.nack_block
            self.blocks += 1
            rx.block_fill = 0
            replies.append(ack(0xFE if ok else 0x01))
            if not ok:
                self._abort(rx)
                return replies
        else:
            assert not any(report[2 + n:]), "padding must be zero"
        assert len(rx.data) <= rx.length, "more data than announced"
        if len(rx.data) == rx.length:
            self._rx = None
            self._finish(rx)
        return replies

    def _abort(self, rx: _Rx) -> None:
        self._rx = None
        if rx.stream_type in (0x5A, 0x5B) and not rx.dropped:
            with self._lock:
                self.pending -= 1

    def _finish(self, rx: _Rx) -> None:
        if rx.stream_type == 0xB0:
            self.wallpaper = bytes(rx.data)
            return
        if not self.realtime:
            self._abort(rx)
            self._event(0x05)
        elif rx.dropped:
            self._event(0x04)
        elif self.decode_delay:
            threading.Timer(self.decode_delay, self._decode, (rx,)).start()
        else:
            self._decode(rx)

    def _decode(self, rx: _Rx) -> None:
        status, img = self._check(bytes(rx.data), rx.stream_type)
        with self._lock:
            self.pending -= 1
            if status == 0x01 and img is not None:
                self.shown.append(img)
                self._present(img.convert("RGB"))
                self.frames_ok += 1
            else:
                self.frames_err += 1
                self.last_error = status
        self._event(status)

    def _check(self, stream: bytes, stream_type: int) -> tuple[int, Image.Image | None]:
        if self.force_status is not None:
            return self.force_status, None
        if len(stream) > self.max_frame_bytes or len(stream) < 9:
            return 0x03, None
        magic, width, height, count, delay = struct.unpack_from(">2sHHHB", stream)
        if count != 1 or delay != 0 or width > self.image[0] or height > self.image[1] or width % 2 or height % 2:
            return 0x03, None
        if stream_type == 0x5B:
            if magic != b"\x57\x51" or len(stream) != 9 + width * height * 2:
                return 0x03, None
            return 0x01, _rgb565_image(stream[9:], width, height)
        jpeg_len = struct.unpack_from(">I", stream, 9)[0]
        if magic != b"\x55\x42" or jpeg_len != len(stream) - 13:
            return 0x03, None
        try:
            img = Image.open(io.BytesIO(stream[13:]))
            img.load()
        except OSError:
            return 0x02, None
        if img.size != (width, height) or img.info.get("progressive"):
            return 0x02, None
        return 0x01, img

    def _present(self, img: Image.Image) -> None:
        """Clear the realtime area to black and centre the frame in it."""
        (ox, oy), (aw, ah) = self.profile.area_origin, self.profile.image
        self.panel.paste((0, 0, 0), (ox, oy, ox + aw, oy + ah))
        self.panel.paste(img, (ox + (aw - img.width) // 2, oy + (ah - img.height) // 2))

    def _event(self, status: int) -> None:
        self.statuses.append(status)
        self.seq = (self.seq + 1) & 0xFFFF
        if self.send_events:
            self._inject(a5(0x3B, bytes([0x10, status]) + struct.pack("<HH", self.seq, 3)))


def _rgb565_image(pixels: bytes, width: int, height: int) -> Image.Image:
    out = bytearray()
    for (value,) in struct.iter_unpack("<H", pixels):
        out += bytes([(value >> 11) << 3, ((value >> 5) & 0x3F) << 2, (value & 0x1F) << 3])
    return Image.frombytes("RGB", (width, height), bytes(out))


# --- the keyboard's main controller over VIA / Vial (not the screen) ---------------------------------------

ROWS, COLS, LAYERS = 7, 15, 3
QK_KB = 0x7E00


# The Neos75's factory keymap and knobs as read from the keyboard on 2026-11-10 ([layer][row][col]).
NEOS75_KEYMAP = [
    [[0x0029, 0x003A, 0x003B, 0x003C, 0x003D, 0x003E, 0x003F, 0x0040, 0x0041, 0x0042, 0x0043, 0x0044, 0x0045, 0x0046, 0x004C],
     [0x0035, 0x001E, 0x001F, 0x0020, 0x0021, 0x0022, 0x0023, 0x0024, 0x0025, 0x0026, 0x0027, 0x002D, 0x002E, 0x002A, 0x004A],
     [0x002B, 0x0014, 0x001A, 0x0008, 0x0015, 0x0017, 0x001C, 0x0018, 0x000C, 0x0012, 0x0013, 0x002F, 0x0030, 0x0031, 0x004D],
     [0x0039, 0x0004, 0x0016, 0x0007, 0x0009, 0x000A, 0x000B, 0x000D, 0x000E, 0x000F, 0x0033, 0x0034, 0x0028, 0x0049, 0x004B],
     [0x00E1, 0x001D, 0x001B, 0x0006, 0x0019, 0x0005, 0x0011, 0x0010, 0x0036, 0x0037, 0x0038, 0x00E5, 0x00E1, 0x0052, 0x004E],
     [0x00E0, 0x00E3, 0x00E2, 0x002C, 0x002C, 0x002A, 0x00E6, 0x5221, 0x00B2, 0x7E2A, 0x00AC, 0x00E4, 0x0050, 0x0051, 0x004F],
     [0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x00A8, 0x00AE, 0x00AB, 0x0000, 0x0000, 0x0000, 0x0000]],
    [[0x0000, 0x00BE, 0x00BD, 0x00B3, 0x00B5, 0x00B2, 0x00AF, 0x00AC, 0x00AE, 0x00AB, 0x00A8, 0x00AA, 0x00A9, 0x7E22, 0x7E0E],
     [0x0001, 0x0001, 0x0001, 0x0001, 0x0001, 0x0001, 0x0001, 0x0001, 0x0001, 0x0001, 0x0001, 0x0001, 0x0001, 0x0000, 0x0001],
     [0x0001, 0x7E04, 0x7E05, 0x0001, 0x0001, 0x0001, 0x0001, 0x0001, 0x0001, 0x7E26, 0x7823, 0x7825, 0x7821, 0x7820, 0x0001],
     [0x7E07, 0x0001, 0x0001, 0x0001, 0x0001, 0x0001, 0x0001, 0x0001, 0x0001, 0x7E23, 0x7824, 0x7826, 0x7E17, 0x0001, 0x7E0F],
     [0x0001, 0x7E09, 0x7E0A, 0x7E0B, 0x7E0D, 0x0001, 0x7013, 0x7E14, 0x0001, 0x0001, 0x7E06, 0x5222, 0x0001, 0x7827, 0x7E10],
     [0x0001, 0x7E0C, 0x0001, 0x7E08, 0x7E08, 0x0000, 0x0001, 0x0001, 0x00A8, 0x0001, 0x7E29, 0x0001, 0x782A, 0x7828, 0x7829],
     [0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0001, 0x7E28, 0x7E27, 0x0000, 0x0000, 0x0000, 0x0000]],
    [[0x0001, 0x0001, 0x0001, 0x0001, 0x0001, 0x0001, 0x0001, 0x0001, 0x0001, 0x0001, 0x0001, 0x0001, 0x0001, 0x7E22, 0x7E0E],
     [0x0001, 0x0001, 0x0001, 0x0001, 0x0001, 0x0001, 0x0001, 0x0001, 0x0001, 0x0001, 0x0001, 0x0001, 0x0001, 0x0001, 0x0001],
     [0x0001, 0x0001, 0x0001, 0x0001, 0x7C00, 0x0001, 0x0001, 0x0001, 0x0001, 0x0001, 0x0001, 0x0001, 0x0001, 0x0001, 0x0001],
     [0x0001, 0x0001, 0x0001, 0x0001, 0x0001, 0x0001, 0x7E15, 0x0001, 0x0001, 0x0001, 0x0001, 0x0001, 0x7E16, 0x0001, 0x7E0F],
     [0x0001, 0x0001, 0x0001, 0x0001, 0x0001, 0x0001, 0x0001, 0x0001, 0x0001, 0x0001, 0x0001, 0x5222, 0x0001, 0x0001, 0x7E10],
     [0x0001, 0x0001, 0x0001, 0x0001, 0x0001, 0x0001, 0x0001, 0x0001, 0x0001, 0x0001, 0x0001, 0x0001, 0x0001, 0x0001, 0x0001],
     [0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0001, 0x0001, 0x0001, 0x0000, 0x0000, 0x0000, 0x0000]],
]
NEOS75_ENCODERS = [  # [layer][encoder] = [ccw, cw]
    [[0x00AA, 0x00A9], [0x7E10, 0x7E0F]],
    [[0x7828, 0x7827], [0x7E10, 0x7E0F]],
    [[0x7828, 0x7827], [0x7E10, 0x7E0F]],
]


class CommandResponseError(Exception):
    """Same name as the library's: a reply that does not echo the command."""


class FakeVia:
    """The qmk_via_api.KeyboardApi methods neos75screen.via uses, holding what the Neos75 keyboard answers
    (values read on 2026-11-09/10), plus dangerous ones that must never be reached. Used by the tests and
    by the service's --fake mode (a simulated keyboard next to the simulated screen)."""

    def __init__(self) -> None:
        self.keymap = [[list(row) for row in layer] for layer in NEOS75_KEYMAP]   # knob presses 6,8 / 5,8
        self.enc = [[list(e) for e in layer] for layer in NEOS75_ENCODERS]       # [layer][enc][ccw, cw]
        self.light = {"brightness": 255, "effect": 43, "speed": 131, "color": (85, 100)}
        self.system = {1: 5, 2: 1, 4: 1, 5: 0, 7: 60, 9: 1}
        self.saved: list[int] = []
        self.pending: list[bytes] = []
        self.stray = 0                       # replies meant for another program, read before ours
        self.macro = bytearray(0x04A3)       # 16 macros, empty
        self.td = [[0] * 5 for _ in range(4)]   # Vial tap dances: tap, hold, double tap, tap-hold, term
        self.sent: list[bytes] = []

    def get_protocol_version(self) -> int:
        return 12

    def get_layer_count(self) -> int:
        if self.stray:
            self.stray -= 1
            raise CommandResponseError("command failure for DynamicKeymapGetLayerCount")
        return LAYERS

    def get_key(self, layer: int, row: int, col: int) -> int:
        return self.keymap[layer][row][col]

    def set_key(self, layer: int, row: int, col: int, value: int) -> None:
        self.keymap[layer][row][col] = value

    def get_keymap_buffer(self, offset: int, size: int) -> bytes:
        flat = b"".join(v.to_bytes(2, "big") for l in self.keymap for row in l for v in row)
        return flat[offset:offset + size]

    def get_encoder_value(self, layer: int, index: int, cw: bool) -> int:
        return self.enc[layer][index][int(cw)]

    def set_encoder_value(self, layer: int, index: int, cw: bool, value: int) -> None:
        self.enc[layer][index][int(cw)] = value

    def get_rgb_matrix_brightness(self) -> int:
        return self.light["brightness"]

    def get_rgb_matrix_effect(self) -> int:
        return self.light["effect"]

    def get_rgb_matrix_effect_speed(self) -> int:
        return self.light["speed"]

    def get_rgb_matrix_color(self) -> tuple[int, int]:
        return self.light["color"]

    def set_rgb_matrix_brightness(self, v: int) -> None:
        self.light["brightness"] = v

    def set_rgb_matrix_effect(self, v: int) -> None:
        self.light["effect"] = v

    def set_rgb_matrix_effect_speed(self, v: int) -> None:
        self.light["speed"] = v

    def set_rgb_matrix_color(self, h: int, s: int) -> None:
        self.light["color"] = (h, s)

    def save_custom_menu(self, channel: int) -> None:
        self.saved.append(channel)

    def get_macro_count(self) -> int:
        return 16

    def get_macro_buffer_size(self) -> int:
        return len(self.macro)

    def get_macro_bytes(self) -> bytes:
        return bytes(self.macro)

    def hid_command(self, command, data: list[int]) -> bytes:  # noqa: ANN001
        name = str(command).split(".")[-1]
        if name == "DynamicKeymapMacroSetBuffer":
            off, n = data[0] << 8 | data[1], data[2]
            assert off + n <= len(self.macro)
            self.macro[off:off + n] = bytes(data[3:3 + n])
            return bytes([0x0F, *data]) + bytes(32 - 1 - len(data))
        r = bytearray(32)
        r[0] = 0x08 if name == "CustomMenuGetValue" else 0x07
        r[1:1 + len(data)] = bytes(data)
        if data[:2] == [0, 4]:
            if name == "CustomMenuSetValue":
                self.system[data[2]] = data[3]
            r[4] = self.system.get(data[2], 0)
        return bytes(r)

    def hid_send(self, data: list[int]) -> None:
        p = bytes(data)
        self.sent.append(p)
        if p[0] == 0xFE:                      # Vial dynamic entries: tap dance
            assert p[1] == 0x0D
            r = bytearray(p)
            if p[2] == 0:
                r[:] = bytes(32)
                r[0] = len(self.td)
            elif p[2] == 1:
                r[0] = 0
                r[1:11] = b"".join(v.to_bytes(2, "little") for v in self.td[p[3]])
            elif p[2] == 2:
                self.td[p[3]] = [p[i] | p[i + 1] << 8 for i in range(4, 14, 2)]
                r[0] = 0
            if self.stray:                    # Aula Hub's lighting poll answered first
                self.stray -= 1
                self.pending.append(bytes([0x08, 3, 1, 255]) + bytes(28))
            self.pending.append(bytes(r))
            return
        assert binascii.crc_hqx(p[:6] + bytes(2) + p[8:], 0xFFFF) == p[6] | p[7] << 8
        r = bytearray(p)
        r[2] = 0
        r[8], r[9] = 100, 0
        self.pending.append(bytes(r))

    def hid_read(self) -> bytes:
        return self.pending.pop(0) if self.pending else bytes(32)

    def jump_to_bootloader(self) -> None:
        raise AssertionError("must never be called")

    reset_eeprom = reset_macros = jump_to_bootloader
