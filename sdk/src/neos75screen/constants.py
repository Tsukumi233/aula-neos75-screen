"""Every protocol value in one place (docs/PROTOCOL.md, v1).

Values marked TBD are decided by the firmware track; values marked ASSUMED are the SDK's reading of an
ambiguity in PROTOCOL.md. Change them here only.
"""
from __future__ import annotations

# --- 1. Transport ---------------------------------------------------------------------------------
VID = 0x5542
PID = 0x0001
USAGE_PAGE = 0xFF60
USAGE = 0x61
REPORT_SIZE = 64
REPORT_ID = 0x00

# --- 2.1 A5 command frame ---------------------------------------------------------------------------
A5 = 0xA5
A5_HEADER_SIZE = 5           # A5, cmd, 00, L, index
A5_DATA_OFFSET = 5           # replies: data (or the realtime sub echo) starts here
A5_LEN_OFFSET = 3            # L = 1 (index byte) + data length

CMD_QUERY = 0x41
CMD_TIME_SYNC = 0x38
CMD_CONTROL = 0x39

QUERY_INFO = 0x14            # <HHHH vid, pid, width, height, then screenType, supportWinUsb, supportJpg
QUERY_IMAGE_SPACE = 0x10     # <I bytes
QUERY_GIF_SPACE = 0x11       # <I bytes
QUERY_KINDS = frozenset({QUERY_INFO, QUERY_IMAGE_SPACE, QUERY_GIF_SPACE})

CONTROL_NEXT_PAGE = 0x01     # saves the page to flash about 2 s later
CONTROL_PREV_PAGE = 0x02
CONTROL_BRIGHTNESS_UP = 0xA1  # saved to flash
CONTROL_BRIGHTNESS_DOWN = 0xA2
CONTROL_CODES = frozenset({CONTROL_NEXT_PAGE, CONTROL_PREV_PAGE, CONTROL_BRIGHTNESS_UP, CONTROL_BRIGHTNESS_DOWN})

TIME_SYNC_MARKER = 0x01      # first data byte of 38
TIME_SYNC_DATA_LEN = 9       # [1, yearHi, yearLo, month, day, h, m, s, weekday]
# ASSUMED: weekday uses JavaScript Date.getDay() numbering (Sunday = 0), as Aula Hub is a web app.
TIME_SYNC_SUNDAY_IS_ZERO = True

# ASSUMED: PROTOCOL.md does not say whether replies carry a valid checksum; do not require it.
REQUIRE_REPLY_CHECKSUM = False

# --- 2.2 11-class query -----------------------------------------------------------------------------
CLASS11 = 0x11
CLASS11_MODEL = 0x01
CLASS11_BUILD = 0x02
CLASS11_SUBS = frozenset({CLASS11_MODEL, CLASS11_BUILD})
CLASS11_CRC_OFFSET = 6       # CRC16 stored little-endian in bytes 6..7
CLASS11_TEXT_OFFSET = 8
CRC16_POLY = 0x1021
CRC16_INIT = 0xFFFF

# --- 3. Realtime control (A5 3B) --------------------------------------------------------------------
CMD_REALTIME = 0x3B
RT_HELLO = 0x01
RT_ENTER = 0x02
RT_LEAVE = 0x03
RT_STATUS = 0x04
RT_TELEMETRY = 0x05                   # HELLO flags bit 1: the keyboard status the stock A5 35 handler stored
RT_SET_PAGES = 0x06                   # HELLO flags bit 2: arg count 1..8 host pages in the knob cycle
MAX_HOST_PAGES = 8
RT_REQUESTS = frozenset({RT_HELLO, RT_ENTER, RT_LEAVE, RT_STATUS, RT_TELEMETRY})
RT_INJECT_KNOB = 0x30                 # HELLO flags bit 0: knob step from the PC, arg 01 next / FF previous
FLAG_TEST_KNOB = 0x01
FLAG_TELEMETRY = 0x02
FLAG_HOST_PAGES = 0x04
FLAG_REGION = 0x08                    # region frames: 'UR' streams update part of the shown canvas
FLAG_PAGE_CACHE = 0x10                # 'UC' page cache, pipelined starts, 5 ms tick while shown
FLAG_PAGE_TAG = 0x20                  # BA start byte 8 = 1 + host page: frames of a page the knob left are dropped
FLAG_KNOB_TAKEOVER = 0x40             # sub 07: screen-knob turns only reported (21), no paging, this session
RT_KNOB_TAKEOVER = 0x07               # arg 01 take over / 00 stock paging; needs an armed session
BA_START_TAG_OFFSET = 8
TELEMETRY_SIZE = 26                   # raw bytes; stock status bar fields below (offsets into them)
TELEMETRY_MODE = 13                   # 2 = charging (stock dot turns green)
TELEMETRY_BATTERY = 14                # percent
TELEMETRY_LINK = 16                   # 1..3 Bluetooth (channel), 4 2.4G, 5 wired
TELEMETRY_CHARGING = 2
RT_EVENT_FRAME_DONE = 0x10
RT_NOT_UNDERSTOOD = 0xEE     # byte 6 of the reply
RT_FIELDS_OFFSET = 6         # response fields start after the sub echo in byte 5

HELLO_MAGIC = b"NS"
PROTO_VERSIONS = frozenset({1, 2})   # protocol versions this SDK speaks
PROTO_KNOB = 2                       # first version with the knob slot, events and keepalive (section 5)
# magic, proto_version, patch_version, canvas_w, canvas_h, image_w, image_h, formats, max_frame_bytes,
# buffers, flags
HELLO_FORMAT = "<2sBHHHHHBIBB"
# state, frames_ok, frames_err, last_error, last_decode_ms
STATUS_FORMAT = "<BIIBH"
STATUS_PAGES_END = 16        # host pages firmware: page u8, pages u8 after visible, armed (field offsets)
# status, seq, decode_ms (after the 0x10 sub byte)
FRAME_DONE_FORMAT = "<BHH"
# proto 2 additions: HELLO host_timeout_ms after flags; STATUS visible, armed after last_decode_ms
HELLO_V2_FORMAT = "<H"
STATUS_V2_FORMAT = "<BB"
# ASSUMED: a proto 2 HELLO reporting host_timeout_ms = 0 means the documented default.
DEFAULT_HOST_TIMEOUT_MS = 10000
KEEPALIVE_DIVISOR = 3                # send STATUS after host_timeout_ms / 3 without realtime traffic

RT_EVENT_VISIBILITY = 0x20           # visible u8 (1 shown, 0 hidden), reason u8
RT_EVENT_KNOB = 0x21                 # dir u8: 01 next, FF previous, 00 press
RT_EVENT_PAGE = 0x22                 # page u8 (0-based), count u8: the knob moved to another host page
VISIBILITY_REASON_KNOB = 0x01
VISIBILITY_REASON_HOST = 0x02        # host ENTER / LEAVE
VISIBILITY_REASON_TIMEOUT = 0x03
KNOB_NEXT = 0x01
KNOB_PREVIOUS = 0xFF
KNOB_PRESS = 0x00                    # never sent by the firmware: the press goes to the PC (Screen.capture_knob_press)
EVENT_QUEUE_SIZE = 256               # events kept for Screen.events(); the oldest are dropped

FORMAT_JPEG = 0x01           # HELLO formats bit 0: baseline JPEG
FORMAT_RGB565 = 0x02         # HELLO formats bit 1: raw RGB565

ENTER_OK = 0x00              # TBD (firmware): non-zero ENTER/LEAVE status codes
STATE_NAMES: dict[int, str] = {}  # TBD (firmware): meaning of STATUS.state; shown as numbers until defined

FRAME_SHOWN = 0x01
FRAME_DECODE_ERROR = 0x02
FRAME_TOO_LARGE = 0x03       # too large / bad header
FRAME_DROPPED_BUSY = 0x04
FRAME_NOT_REALTIME = 0x05
FRAME_NO_CANVAS = 0x06       # region frame before a full 480x360 frame is shown (send a full frame)
FRAME_SUPERSEDED = 0x07      # the knob moved to another page while this frame was arriving
FRAME_STATUS_NAMES = {
    FRAME_SHOWN: "shown",
    FRAME_DECODE_ERROR: "decode error",
    FRAME_TOO_LARGE: "too large / bad header",
    FRAME_DROPPED_BUSY: "dropped (busy)",
    FRAME_NOT_REALTIME: "not in realtime mode",
    FRAME_NO_CANVAS: "region needs a full frame first",
    FRAME_SUPERSEDED: "superseded by a page change",
}

MAX_PIPELINE_DEPTH = 2       # HELLO buffers is 1 or 2

# --- 4. BA transport --------------------------------------------------------------------------------
BA = 0xBA
BA_START = bytes([0x06, 0xAA])   # first packet: BA 06 AA <type> len32_BE
BA_CHUNK = 62
BA_BLOCK = 4096
BA_CRC_SIZE = 4
BA_ACK_INDEX = 6
BA_ACK_OK = 0xFE
# ASSUMED: the first packet is acknowledged like a block end. PROTOCOL.md is silent, but the stock
# upload (verified on the device) waits for an ACK after it, and section 4 reuses that transport unchanged.
FIRST_PACKET_ACK = True

STREAM_JPEG = 0x5A           # RAM JPEG frame
STREAM_RGB565 = 0x5B         # RAM RGB565 frame
STREAM_WALLPAPER = 0xB0      # stock persistent wallpaper; erases external flash
STREAM_GIF = 0xFC            # stock GIF upload; never sent by the SDK
REALTIME_STREAM_TYPES = frozenset({STREAM_JPEG, STREAM_RGB565})
# Types the firmware recognises in `BA 06 AA <type>`: a data packet must never match this signature.
BA_START_TYPES = frozenset({STREAM_JPEG, STREAM_RGB565, STREAM_WALLPAPER, STREAM_GIF})

JPEG_STREAM_MAGIC = b"\x55\x42"
RGB565_STREAM_MAGIC = b"\x57\x51"
REGION_STREAM_MAGIC = b"\x55\x52"   # 'UR': w, h, x, y (u16 BE), jpeg_len (u32 BE), JPEG
REGION_ALIGN = 16                     # region width/height: whole 16x16 JPEG MCUs
REGION_MAX_PIXELS = 40960             # decoded beside the canvas in 81920 bytes (page cache firmware)
CACHE_STREAM_MAGIC = b"UC"             # page u8, 0, jpeg_len (u32 BE), JPEG 480x360: stored, not shown
STREAM_FRAME_COUNT = 1
STREAM_FRAME_DELAY = 0
JPEG_STREAM_HEADER_SIZE = 13     # magic, W, H, count, delay, jpeg_len
RGB565_STREAM_HEADER_SIZE = 9    # magic, W, H, count, delay

# --- Defaults ---------------------------------------------------------------------------------------
STOCK_IMAGE_SIZE = (320, 320)    # stock API size and v1 realtime image; fallback when HELLO has no answer
PANEL_SIZE = (480, 360)          # physical panel; v2 realtime image; default for offline previews
REPLY_TIMEOUT_S = 1.0
HELLO_TIMEOUT_S = 0.5            # no magic within this time -> stock firmware
ACK_TIMEOUT_S = 1.5
FRAME_DONE_TIMEOUT_S = 2.0       # v1 flow control
WALLPAPER_SETTLE_S = 0.4         # Aula Hub waits this long after the last packet
READ_POLL_MS = 100
