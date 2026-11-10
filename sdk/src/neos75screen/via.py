"""The Neos75 keyboard itself (not the screen): its main controller runs a vendor fork of QMK that speaks VIA
(protocol 12, verified 2026-11-09) on the raw HID interface (usage page FF60, 32-byte reports), wired
(1EA7:4884) and through the 2.4G receiver (6D66:8572). This replaces Aula Hub for what it does on this keyboard:
the keymap (3 layers, 7x15 matrix), both knobs (VIA encoders 0 = volume knob, 1 = screen knob; their presses
are matrix keys 6,8 and 5,8), RGB matrix lighting and the vendor's system settings, plus the battery level.

The VIA protocol itself is not ours: it comes from `qmk-via-api` (GPL-3.0, a port of the VIA app's keyboard
API: https://github.com/srwi/qmk-via-api). This module only adds what is specific to this keyboard and a guard:
the library object is wrapped in `SafeApi`, which exposes only the calls listed in `ALLOWED` and checks their
arguments, so its bootloader jump, EEPROM reset, macro reset and keymap clear can never be reached from here,
and the reboot/erase keycodes (`keycodes.forbidden`) are never written. Two library calls are not used because
their Python side is broken in 0.10.1: `get_custom_menu_value` returns the request header instead of the value
(we read the reply of `hid_command(CustomMenuGetValue)`), and `MatrixInfo` cannot be constructed (the keymap is
read with `get_keymap_buffer`). `save_lighting` is not used either: it sends 0x09 without a channel, which
protocol 12 takes as channel 0; lighting is saved with `save_custom_menu(3)` as the VIA app does.

Findings this relies on (hardware, read-only unless noted):
- 0x14 / 0x15 are the standard VIA get / set encoder (0x15 written once to layer 1 encoder 1 cw and restored).
- Lighting is VIA's built-in `qmk_rgb_matrix` menu (the official definition says so): channel 3, values
  1 brightness, 2 effect, 3 speed, 4 hue + saturation. Effects follow QMK's RGB matrix order; 0 is off.
- System settings: the "Tools" menu of the official definition, channel 0, value 4, item n.
- Battery: vendor command 0x51 mode 0x16 with a CRC-16/CCITT (init FFFF, little-endian at bytes 6..7); reply
  byte 8 = percent, byte 9 = charging.
- Macros (2026-11-10, read): VIA's macro buffer, 16 macros in 0x04A3 bytes, keycode QK_MACRO 0x7700 + n. Aula
  Hub writes the QMK send_string format: 01 01/02/03 kc = tap/down/up, 01 04 "ms" "|" = delay, other bytes
  are typed as text, 00 ends a macro. We write the whole buffer with 0x0F (never the 0x10 macro reset).
- Advanced keys (2026-11-10, read): the firmware is also Vial (protocol 6). Aula Hub's "MT" is Vial's tap
  dance: 0xFE 0x0D op 0 = entry counts (4 tap dances, 0 combos, 0 key overrides), op 1 idx = read, op 2 idx
  + {on_tap, on_hold, on_double_tap, on_tap_hold, tapping_term} (u16 LE) = write; keycode QK_TAP_DANCE
  0x5700 + n. Vial replies do not echo the command. Aula Hub's "TGL" (index 0x40 + n) answers 0xFF here: this
  firmware does not have it. No other Vial command is sent (no unlock, no QMK settings).
"""
from __future__ import annotations

import binascii
import json
import threading
from dataclasses import dataclass
from pathlib import Path

VIA_USAGE_PAGE = 0xFF60
DEVICES = ((0x1EA7, 0x4884, "wired"), (0x6D66, 0x8572, "2.4g"))
REPORT = 32
LAYERS_MAX = 8
ENCODERS = 2                      # 0 = volume knob, 1 = screen knob
KNOB_PRESS = {0: (6, 8), 1: (5, 8)}   # encoder -> matrix position of its press switch
LIGHT_CHANNEL, SYSTEM_CHANNEL = 3, 0
TIMEOUT_MS = 800
RETRIES = 4


class ViaError(Exception):
    pass


class ForbiddenCommand(ViaError):
    pass


class KeyboardNotFound(ViaError):
    pass


def crc16(data: bytes) -> int:
    """CRC-16/CCITT-FALSE, as the vendor battery command wants it."""
    return binascii.crc_hqx(data, 0xFFFF)


# The vendor "Tools" menu (official definition): item -> (label, options). Options: value -> label.
SYSTEM_ITEMS: dict[int, tuple[str, dict[int, str]]] = {
    5: ("系统", {0: "Windows", 1: "macOS"}),
    1: ("按键响应间隔", {v: f"{v} ms" for v in (*range(1, 21), 25, 30, 35, 40, 45, 50, 60, 70, 80, 90, 100, 200)}),
    2: ("防抖方式", {0: "关", 1: "先发送后防抖", 2: "先防抖后发送"}),
    # Aula Hub: "休眠设置", idle minutes before the keyboard sleeps, slider 1-60; the same row wired and over 2.4G
    7: ("休眠时间", {v: f"{v} 分钟" for v in range(1, 61)}),
    9: ("感应唤醒", {0: "关", 1: "开"}),
    4: ("电脑灯光同步", {0: "开", 1: "关"}),
}

# QMK RGB matrix effects in this firmware's order (names are ours).
EFFECTS: dict[int, str] = {
    0: "关闭", 2: "纯色", 3: "字母与修饰键", 4: "上下渐变", 5: "左右渐变", 6: "呼吸", 7: "饱和度光带",
    8: "亮度光带", 9: "风车（饱和度）", 10: "风车（亮度）", 11: "螺旋（饱和度）", 12: "螺旋（亮度）", 13: "整体变色",
    14: "左右彩虹流动", 15: "上下彩虹流动", 16: "彩虹人字", 17: "由外向内", 18: "双向由外向内", 19: "彩虹风车",
    20: "彩虹螺旋", 21: "双灯塔", 22: "彩虹灯塔", 23: "彩虹多风车", 24: "雨滴", 25: "彩色雨滴", 26: "色相呼吸",
    27: "色相摆动", 28: "色相波浪", 29: "像素雨", 30: "像素流", 31: "像素分形", 32: "按键热力图", 33: "数字雨",
    34: "按键单点亮", 35: "按键点亮", 36: "按键扩散", 37: "多键扩散", 38: "十字", 39: "多键十字", 40: "连线",
    41: "多键连线", 42: "水花", 43: "多键水花", 44: "纯色水花", 45: "纯色多键水花",
}
NO_SPEED = {1, 2, 3, 4, 29, 42, 43}
NO_COLOR = {3, 4, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 29, 30, 38, 39, 42, 43}

# qmk-via-api calls this module may make (everything else on the library object is refused).
ALLOWED = {
    "get_protocol_version", "get_layer_count", "get_key", "set_key", "get_keymap_buffer",
    "get_encoder_value", "set_encoder_value",
    "get_rgb_matrix_brightness", "get_rgb_matrix_effect", "get_rgb_matrix_effect_speed", "get_rgb_matrix_color",
    "set_rgb_matrix_brightness", "set_rgb_matrix_effect", "set_rgb_matrix_effect_speed", "set_rgb_matrix_color",
    "save_custom_menu", "hid_command", "hid_send", "hid_read",
    "get_macro_count", "get_macro_buffer_size", "get_macro_bytes",
}
MENU_COMMANDS = {"CustomMenuGetValue", "CustomMenuSetValue"}
MACRO_SET = "DynamicKeymapMacroSetBuffer"
QK_MACRO, QK_TAP_DANCE = 0x7700, 0x5700
MACRO_CHUNK = 28
VIAL, VIAL_DYNAMIC = 0xFE, 0x0D
TD_COUNT, TD_GET, TD_SET = 0, 1, 2
TD_FIELDS = ("tap", "hold", "double_tap", "tap_hold")
TD_TERM_MAX = 2000


def _battery_packet() -> bytes:
    packet = bytearray(REPORT)
    packet[0], packet[1] = 0x51, 0x16
    c = crc16(bytes(packet))
    packet[6], packet[7] = c & 0xFF, c >> 8
    return bytes(packet)


class SafeApi:
    """Wraps a `qmk_via_api.KeyboardApi` (or a test fake with the same methods): only `ALLOWED` calls get
    through, keycode writes are checked against `keycodes.forbidden` (except the keycodes in `allow`, the
    keyboard's own values from a backup), menu commands only touch lighting and the system items, and the only
    raw packet is the battery query. Library errors become `ViaError`."""

    def __init__(self, api) -> None:  # noqa: ANN001
        self._api = api
        self.allow: frozenset[int] | set[int] = frozenset()
        self.macro_size = 0          # set once read: macro buffer writes must stay inside it
        self.tap_dances = 0          # set once read: tap dance entries that exist

    def __getattr__(self, name: str):  # noqa: ANN204
        if name not in ALLOWED:
            raise ForbiddenCommand(f"{name} is not allowed")
        fn = getattr(self._api, name)

        def call(*args):  # noqa: ANN002, ANN202
            self._check(name, args)
            for attempt in range(RETRIES + 1):
                try:
                    return fn(*args)
                except ViaError:
                    raise
                except Exception as exc:  # qmk_via_api raises its own exception types
                    # The library reads exactly one report per command. Replies to another program on the
                    # same interface (Aula Hub polls the lighting) reach us too and then fail the echo check:
                    # each retry consumes one stray report. Every call made here is idempotent.
                    if type(exc).__name__ == "CommandResponseError" and attempt < RETRIES:
                        continue
                    raise ViaError(f"{name}: {exc}") from exc
            raise AssertionError("unreachable")
        return call

    def _check(self, name: str, args: tuple) -> None:
        from .keycodes import forbidden
        if name in ("set_key", "set_encoder_value"):
            keycode = int(args[-1])
            if forbidden(keycode) and keycode not in self.allow:
                raise ForbiddenCommand(f"keycode 0x{keycode:04X} is not allowed")
        elif name == "save_custom_menu":
            if args[0] != LIGHT_CHANNEL:
                raise ForbiddenCommand(f"save of channel {args[0]} is not allowed")
        elif name == "hid_command":
            command, data = args
            cname = getattr(command, "name", str(command).split(".")[-1])
            data = list(data)
            if cname == MACRO_SET:
                n = data[2] if len(data) >= 3 else -1
                offset = (data[0] << 8 | data[1]) if len(data) >= 2 else -1
                if not (0 < n <= MACRO_CHUNK and len(data) == 3 + n and 0 <= offset
                        and offset + n <= self.macro_size):
                    raise ForbiddenCommand(f"macro buffer write {data[:3]} is not allowed")
                return
            if cname not in MENU_COMMANDS:
                raise ForbiddenCommand(f"command {command} is not allowed")
            if data[:1] == [LIGHT_CHANNEL]:
                ok = len(data) >= 2 and data[1] in (1, 2, 3, 4)
            elif data[:2] == [SYSTEM_CHANNEL, 0x04]:
                ok = len(data) >= 3 and data[2] in SYSTEM_ITEMS
            else:
                ok = False
            if not ok:
                raise ForbiddenCommand(f"custom menu value {data[:3]} is not allowed")
        elif name == "hid_send":
            packet = bytes(args[0])
            if packet != _battery_packet() and not self._tap_dance_packet(packet):
                raise ForbiddenCommand("only the battery query and tap dance entries may be sent raw")

    def _tap_dance_packet(self, p: bytes) -> bool:
        """Vial dynamic-entry packets for tap dances only: count, read entry n, write entry n."""
        from .keycodes import forbidden
        if len(p) != REPORT or p[:2] != bytes((VIAL, VIAL_DYNAMIC)):
            return False
        op = p[2]
        if op == TD_COUNT:
            return not any(p[3:])
        if op == TD_GET:
            return p[3] < self.tap_dances and not any(p[4:])
        if op == TD_SET:
            if p[3] >= self.tap_dances or any(p[14:]):
                return False
            codes = [p[i] | p[i + 1] << 8 for i in range(4, 12, 2)]
            term = p[12] | p[13] << 8
            return term <= TD_TERM_MAX and not any(forbidden(c) or QK_TAP_DANCE <= c < QK_TAP_DANCE + 0x100
                                                    for c in codes)
        return False


@dataclass
class Found:
    device: object      # qmk_via_api.KeyboardDeviceInfo
    vid: int
    pid: int
    link: str


def find() -> list[Found]:
    """The keyboard's VIA interfaces (wired first, then the 2.4G receiver). The screen (5542:0001) also has a
    FF60 interface; it is never picked."""
    import qmk_via_api
    try:
        devices = qmk_via_api.scan_keyboards()
    except Exception as exc:  # noqa: BLE001
        raise ViaError(f"HID scan failed: {exc}") from exc
    out = []
    for vid, pid, link in DEVICES:
        out += [Found(d, vid, pid, link) for d in devices
                if d.vendor_id == vid and d.product_id == pid and d.usage_page == VIA_USAGE_PAGE]
    return out


class Keyboard:
    """VIA client for the Neos75 keyboard on top of qmk-via-api. `api` is a `qmk_via_api.KeyboardApi` (tests pass
    a fake with the same methods); every call goes through `SafeApi`. Thread-safe."""

    def __init__(self, api, definition: dict | None = None, connection: str = "unknown") -> None:  # noqa: ANN001
        self.api = SafeApi(api)
        self.connection = connection
        self.definition = definition
        self._lock = threading.RLock()
        self.rows, self.cols = 7, 15
        if definition:
            self.rows = int(definition["matrix"]["rows"])
            self.cols = int(definition["matrix"]["cols"])

    @classmethod
    def open(cls, definition: dict | None = None) -> Keyboard:
        import qmk_via_api
        found = find()
        if not found:
            raise KeyboardNotFound("Neos75 keyboard not found (wired or 2.4G receiver)")
        for attempt in range(RETRIES + 1):      # opening reads the protocol version (see SafeApi on retries)
            try:
                api = qmk_via_api.KeyboardApi.from_device(found[0].device, TIMEOUT_MS)
                return cls(api, definition, found[0].link)
            except Exception as exc:  # noqa: BLE001
                if type(exc).__name__ != "CommandResponseError" or attempt == RETRIES:
                    raise ViaError(f"cannot open the keyboard: {exc}") from exc
        raise AssertionError("unreachable")

    def close(self) -> None:
        self.api = None  # type: ignore[assignment]   # the library closes the device when the object goes

    def _menu_get(self, *data: int) -> bytes:
        return bytes(self.api.hid_command(_command("CustomMenuGetValue"), list(data)))

    def _menu_set(self, *data: int) -> None:
        self.api.hid_command(_command("CustomMenuSetValue"), list(data))

    # --- reads ---

    def protocol(self) -> int:
        with self._lock:
            return int(self.api.get_protocol_version())

    def layer_count(self) -> int:
        with self._lock:
            return max(1, min(LAYERS_MAX, int(self.api.get_layer_count())))

    def keycode(self, layer: int, row: int, col: int) -> int:
        with self._lock:
            return int(self.api.get_key(layer, row, col))

    def keymap(self, layers: int | None = None) -> list[list[list[int]]]:
        """[layer][row][col] keycodes, read with the keymap buffer (28 bytes per request)."""
        layers = layers or self.layer_count()
        size = layers * self.rows * self.cols * 2
        raw = bytearray()
        with self._lock:
            while len(raw) < size:
                n = min(28, size - len(raw))
                raw += bytes(self.api.get_keymap_buffer(len(raw), n))[:n]
        codes = [raw[i] << 8 | raw[i + 1] for i in range(0, size, 2)]
        per = self.rows * self.cols
        return [[codes[l * per + r * self.cols:l * per + (r + 1) * self.cols] for r in range(self.rows)]
                for l in range(layers)]

    def encoder(self, layer: int, index: int, clockwise: bool) -> int:
        with self._lock:
            return int(self.api.get_encoder_value(layer, index, clockwise))

    def encoders(self, layers: int | None = None) -> list[list[dict]]:
        """[layer][encoder] -> {cw, ccw, press} keycodes (press = the knob's matrix key)."""
        layers = layers or self.layer_count()
        return [[{"cw": self.encoder(l, e, True), "ccw": self.encoder(l, e, False),
                  "press": self.keycode(l, *KNOB_PRESS[e])} for e in range(ENCODERS)] for l in range(layers)]

    def lighting(self) -> dict:
        with self._lock:
            hue, sat = self.api.get_rgb_matrix_color()
            return {"brightness": int(self.api.get_rgb_matrix_brightness()), "effect": int(self.api.get_rgb_matrix_effect()),
                    "speed": int(self.api.get_rgb_matrix_effect_speed()), "hue": int(hue), "saturation": int(sat)}

    def system(self) -> dict[int, int]:
        with self._lock:
            return {item: self._menu_get(SYSTEM_CHANNEL, 0x04, item)[4] for item in SYSTEM_ITEMS}

    def battery(self) -> dict | None:
        """{percent, charging} from the keyboard itself (works over 2.4G too); None if it does not answer."""
        packet = _battery_packet()
        try:
            with self._lock:
                self.api.hid_send(list(packet))
                for _ in range(4):
                    r = bytes(self.api.hid_read())
                    if r[:1] == b"\x51":
                        break
                else:
                    return None
        except ViaError:
            return None
        if r[1] != 0x16 or r[2] != 0:
            return None
        return {"percent": r[8], "charging": bool(r[9])}

    # --- macros (VIA) ---

    def macro_info(self) -> tuple[int, int]:
        """(number of macros, buffer size in bytes)."""
        with self._lock:
            count, size = int(self.api.get_macro_count()), int(self.api.get_macro_buffer_size())
            self.api.macro_size = size
            return count, size

    def macro_bytes(self) -> bytes:
        with self._lock:
            self.macro_info()
            return bytes(self.api.get_macro_bytes())

    def macros(self) -> list[list[dict]]:
        count, _ = self.macro_info()
        return parse_macros(self.macro_bytes(), count)

    def set_macro_bytes(self, data: bytes) -> bytes:
        """Write the whole macro buffer (data padded with zeros) with 0x0F, never the 0x10 reset. As VIA does,
        the last byte is set non-zero first and cleared last, so a half-written buffer is not played."""
        with self._lock:
            _, size = self.macro_info()
            if len(data) > size:
                raise ValueError(f"宏太长：{len(data)} 字节，键盘只有 {size} 字节")
            buf = bytes(data) + bytes(size - len(data))
            command = _command(MACRO_SET)
            self.api.hid_command(command, [(size - 1) >> 8, (size - 1) & 0xFF, 1, 0xFF])
            for off in range(0, size - 1, MACRO_CHUNK):
                chunk = buf[off:min(off + MACRO_CHUNK, size - 1)]
                self.api.hid_command(command, [off >> 8, off & 0xFF, len(chunk), *chunk])
            self.api.hid_command(command, [(size - 1) >> 8, (size - 1) & 0xFF, 1, 0x00])
            return self.macro_bytes()

    def set_macros(self, macros: list[list[dict]]) -> list[list[dict]]:
        count, _ = self.macro_info()
        if len(macros) > count:
            raise ValueError(f"键盘只能存 {count} 个宏")
        data = encode_macros(macros + [[] for _ in range(count - len(macros))])
        return parse_macros(self.set_macro_bytes(data), count)

    # --- advanced keys: Vial tap dance ---

    def _vial(self, packet: bytes, accept) -> bytes:  # noqa: ANN001
        """Send a raw Vial packet and return the first reply `accept` takes. Vial replies do not echo the command,
        so replies to another program on the interface are told apart by their shape; a read that times out
        comes back as zeros after the full timeout and is not taken."""
        import time
        for _ in range(RETRIES):
            self.api.hid_send(list(packet))
            for _ in range(4):
                t = time.monotonic()
                r = bytes(self.api.hid_read())
                timed_out = not any(r) and time.monotonic() - t >= TIMEOUT_MS / 1000 * 0.9
                if not timed_out and accept(r):
                    return r
        raise ViaError(f"no Vial reply to {packet[:4].hex()}")

    def tap_dance_count(self) -> int:
        with self._lock:
            packet = bytes((VIAL, VIAL_DYNAMIC, TD_COUNT)) + bytes(REPORT - 3)
            seen = []
            while len(seen) < 2 or seen[-1] != seen[-2]:     # the count reply could be confused with a stray one
                r = self._vial(packet, lambda r: r[0] <= 64 and r[1] <= 64 and not any(r[3:]))
                seen.append(r[0])
                if len(seen) > 5:
                    raise ViaError("tap dance count is not stable")
            self.api.tap_dances = seen[-1]
            return seen[-1]

    def tap_dance(self, index: int) -> dict:
        with self._lock:
            if not self.api.tap_dances:
                self.tap_dance_count()
            if not 0 <= index < self.api.tap_dances:
                raise ValueError(f"advanced key {index} does not exist")
            packet = bytes((VIAL, VIAL_DYNAMIC, TD_GET, index)) + bytes(REPORT - 4)
            r = self._vial(packet, lambda r: r[0] in (0x00, 0xFF) and not any(r[11:]))
            if r[0]:
                raise ViaError(f"advanced key {index}: keyboard said 0x{r[0]:02X}")
            values = [r[i] | r[i + 1] << 8 for i in range(1, 11, 2)]
            return {"index": index, **dict(zip(TD_FIELDS, values[:4])), "term": values[4]}

    def tap_dances(self) -> list[dict]:
        with self._lock:
            return [self.tap_dance(i) for i in range(self.tap_dance_count())]

    def set_tap_dance(self, index: int, tap: int = 0, hold: int = 0, double_tap: int = 0, tap_hold: int = 0,
                      term: int = 200) -> dict:
        with self._lock:
            if not self.api.tap_dances:
                self.tap_dance_count()
            if not 0 <= index < self.api.tap_dances:
                raise ValueError(f"advanced key {index} does not exist")
            term = int(term)
            if not 0 <= term <= TD_TERM_MAX:
                raise ValueError(f"term must be 0..{TD_TERM_MAX} ms")
            values = [int(v) & 0xFFFF for v in (tap, hold, double_tap, tap_hold)] + [term]
            packet = bytes((VIAL, VIAL_DYNAMIC, TD_SET, index)) + b"".join(v.to_bytes(2, "little") for v in values)
            packet += bytes(REPORT - len(packet))
            r = self._vial(packet, lambda r: r[0] in (0x00, 0xFF) and r[1:4] == packet[1:4])
            if r[0]:
                raise ViaError(f"advanced key {index}: keyboard said 0x{r[0]:02X}")
            return self.tap_dance(index)

    # --- writes ---

    def set_keycode(self, layer: int, row: int, col: int, keycode: int,
                    allow: frozenset[int] | set[int] = frozenset()) -> int:
        self._range(layer, row, col)
        with self._lock:
            self.api.allow = allow
            try:
                self.api.set_key(layer, row, col, keycode)
            finally:
                self.api.allow = frozenset()
            return self.keycode(layer, row, col)

    def set_encoder(self, layer: int, index: int, clockwise: bool, keycode: int) -> int:
        if not 0 <= index < ENCODERS:
            raise ValueError("encoder is 0 (volume knob) or 1 (screen knob)")
        self._range(layer, 0, 0)
        with self._lock:
            self.api.set_encoder_value(layer, index, clockwise, keycode)
            return self.encoder(layer, index, clockwise)

    def set_lighting(self, brightness: int | None = None, effect: int | None = None, speed: int | None = None,
                     hue: int | None = None, saturation: int | None = None, save: bool = True) -> dict:
        def byte(v: int) -> int:
            return max(0, min(255, int(v)))
        with self._lock:
            if effect is not None:
                if effect not in EFFECTS:
                    raise ValueError(f"unknown effect {effect}")
                self.api.set_rgb_matrix_effect(effect)
            if brightness is not None:
                self.api.set_rgb_matrix_brightness(byte(brightness))
            if speed is not None:
                self.api.set_rgb_matrix_effect_speed(byte(speed))
            if hue is not None or saturation is not None:
                now = self.lighting()
                self.api.set_rgb_matrix_color(now["hue"] if hue is None else byte(hue),
                                              now["saturation"] if saturation is None else byte(saturation))
            if save:
                self.api.save_custom_menu(LIGHT_CHANNEL)
            return self.lighting()

    def set_system(self, item: int, value: int) -> int:
        if item not in SYSTEM_ITEMS or value not in SYSTEM_ITEMS[item][1]:
            raise ValueError(f"system item {item} value {value} not allowed")
        with self._lock:
            self._menu_set(SYSTEM_CHANNEL, 0x04, item, value)
            return self._menu_get(SYSTEM_CHANNEL, 0x04, item)[4]

    def _range(self, layer: int, row: int, col: int) -> None:
        if not (0 <= layer < LAYERS_MAX and 0 <= row < self.rows and 0 <= col < self.cols):
            raise ValueError("layer/row/col out of range")


MACRO_KINDS = {1: "tap", 2: "down", 3: "up"}


def parse_macros(buf: bytes, count: int) -> list[list[dict]]:
    """Macro buffer -> `count` lists of actions: {type: tap/down/up, code}, {type: delay, ms}, {type: text, text}.
    Format as Aula Hub and QMK's send_string write it (see the module docstring)."""
    macros: list[list[dict]] = []
    i = 0
    for _ in range(count):
        actions: list[dict] = []
        text = bytearray()

        def flush() -> None:
            if text:
                actions.append({"type": "text", "text": text.decode("latin-1")})
                text.clear()
        while i < len(buf) and buf[i] != 0:
            b = buf[i]
            if b == 1 and i + 2 < len(buf) and buf[i + 1] in MACRO_KINDS:
                flush()
                actions.append({"type": MACRO_KINDS[buf[i + 1]], "code": buf[i + 2]})
                i += 3
            elif b == 1 and i + 1 < len(buf) and buf[i + 1] == 4:
                flush()
                end = buf.find(b"|", i + 2)
                end = len(buf) if end < 0 else end
                digits = bytes(c for c in buf[i + 2:end] if 0x30 <= c <= 0x39)
                actions.append({"type": "delay", "ms": int(digits or b"0")})
                i = end + 1
            elif 0x20 <= b < 0x7F:
                text.append(b)
                i += 1
            else:
                i += 1                        # an encoding we do not write (e.g. Vial's 16-bit codes): skipped
        flush()
        macros.append(actions)
        i += 1
    return macros


def encode_macros(macros: list[list[dict]]) -> bytes:
    out = bytearray()
    kinds = {v: k for k, v in MACRO_KINDS.items()}
    for actions in macros:
        for a in actions:
            t = a.get("type")
            if t in kinds:
                code = int(a["code"])
                if not 0 < code <= 0xFF:
                    raise ValueError(f"宏里只能用基本按键（0x01–0xFF），不是 0x{code:04X}")
                out += bytes((1, kinds[t], code))
            elif t == "delay":
                ms = int(a["ms"])
                if not 0 <= ms <= 60000:
                    raise ValueError("延时是 0–60000 毫秒")
                out += b"\x01\x04" + str(ms).encode() + b"|"
            elif t == "text":
                s = str(a["text"])
                if not all(0x20 <= ord(ch) < 0x7F for ch in s):
                    raise ValueError("文字只能是英文字母、数字和符号（键盘按美式布局输入）")
                out += s.encode("ascii")
            else:
                raise ValueError(f"未知的宏动作 {t}")
        out.append(0)
    return bytes(out)


def _command(name: str):  # noqa: ANN202
    """A `qmk_via_api.ViaCommandId` member (its name when the library is not installed, as in some tests)."""
    try:
        import qmk_via_api
    except ImportError:
        return name
    return getattr(qmk_via_api.ViaCommandId, name)


def load_definition(*candidates: Path) -> dict | None:
    """The official VIA definition (`AULA-Neos75_via_v3.json`, from Aula Hub; kept out of git), if present."""
    for path in candidates:
        try:
            return json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    return None
