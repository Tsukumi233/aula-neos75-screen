"""Knob input captured on the PC (Windows).

The Neos75 has two knobs (verified on hardware 2026-11-07, wired, keyboard 1EA7:4884; 2.4G receiver 6D66:8572
2026-11-09):

    knob     turn                                           press
    screen   goes to the screen MCU only (page switching)   PC: Calculator (VK_LAUNCH_APP2)
    volume   PC: Volume Up / Volume Down                    PC: Mute (VK_VOLUME_MUTE)

Aula Hub cannot remap them. Screen-knob turns never reach the PC; the patched screen firmware reports them
(proto 2). Everything else arrives as media keys, which this module can take over.

A low-level keyboard hook cannot tell devices apart, so this also registers Raw Input for consumer controls:
Windows delivers each consumer report (with the sending device) to this thread just before it calls the
hook for the key that report produced, so the hook knows the source. Only keys from the Neos75 keyboard are
handled; a mouse's or another keyboard's media keys always pass. If the source is not known, the key passes.

On the Neos75 keyboard the volume knob and the Fn+key volume shortcuts send identical reports through the
same interface. They differ in how long the key is held: a knob step is released within about 1 ms, a key
press after tens of milliseconds. A volume key-down is therefore held back; a release within `tap_ms` is a
knob step (reported, not passed on), a later release is a key press and is replayed to Windows unchanged (so
Fn volume still works, taking effect on release). Mute cannot be told apart that way: while the volume
knob's press is taken over, an Fn+key Mute shortcut on the same keyboard (if it has one) is taken too.

Only Volume Up/Down, Mute and Calculator are examined; no other key is looked at or recorded.
"""
from __future__ import annotations

import ctypes
import re
import sys
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass

VK_VOLUME_MUTE = 0xAD
VK_VOLUME_DOWN = 0xAE
VK_VOLUME_UP = 0xAF
VK_LAUNCH_APP2 = 0xB7
USAGE_MUTE = 0x00E2
USAGE_VOLUME_UP = 0x00E9
USAGE_VOLUME_DOWN = 0x00EA
USAGE_CALCULATOR = 0x0192
USAGE_FOR_VK = {VK_VOLUME_UP: USAGE_VOLUME_UP, VK_VOLUME_DOWN: USAGE_VOLUME_DOWN, VK_VOLUME_MUTE: USAGE_MUTE,
                VK_LAUNCH_APP2: USAGE_CALCULATOR}
SCREEN, VOLUME = "screen", "volume"
PRESS, RELEASE = 0, 2                   # event values besides the +1 / -1 steps
# Neos75 keyboard wired, and its 2.4G receiver (verified 2026-11-09: same reports, a volume step <1 ms).
# Bluetooth not verified.
DEFAULT_DEVICES = ((0x1EA7, 0x4884), (0x6D66, 0x8572))
DEFAULT_TAP_MS = 25
REPLAY_MARK = 0x4E3735                  # dwExtraInfo of keys this module replays ('N75')
_KEYDOWN = (0x0100, 0x0104)
_KEYUP = (0x0101, 0x0105)


def device_matches(name: str, devices: Iterable[tuple[int, int]]) -> bool:
    """True if a Raw Input device path names one of the (vid, pid) pairs."""
    m = re.search(r"VID_([0-9A-F]{4})&PID_([0-9A-F]{4})", name, re.IGNORECASE)
    return bool(m) and (int(m.group(1), 16), int(m.group(2), 16)) in set(devices)


def consumer_usage(report: bytes) -> int | None:
    """First consumer usage in a report laid out as report ID + 16-bit usages (both observed devices)."""
    if len(report) < 3:
        return None
    return report[1] | report[2] << 8


@dataclass
class Decision:
    event: tuple[str, int] | None = None       # (knob, +1 / -1 step, 0 press, 2 release), None nothing
    swallow: bool = False                      # keep the key from Windows
    replay: tuple[tuple[int, bool], ...] = ()  # (vk, keyup) to send to Windows afterwards


class KnobKeyFilter:
    """Decides, per hook message, what a media key from the Neos75 keyboard means. Pure logic."""

    def __init__(self, screen_press: bool = True, block_screen_press: bool = True, volume_turns: bool = False,
                 volume_press: bool = False, tap_ms: int = DEFAULT_TAP_MS, releases: bool = False) -> None:
        self.releases = releases            # also report the release of a press (RELEASE)
        self.screen_press, self.block_screen_press = screen_press, block_screen_press
        self.volume_turns, self.volume_press, self.tap_ms = volume_turns, volume_press, tap_ms
        self._held: dict[int, int] = {}     # volume vk -> key-down time (ms), held back, undecided
        self._passing: set[int] = set()     # volume vk held long enough to be a key: let it through
        self._down: set[int] = set()        # press keys currently down (one event per physical press)

    def _press(self, vk: int, down: bool, up: bool, knob: str, swallow: bool) -> Decision:
        event = None
        if down and vk not in self._down:
            event = (knob, PRESS)
        if down:
            self._down.add(vk)
        elif up:
            if vk in self._down and self.releases:
                event = (knob, RELEASE)
            self._down.discard(vk)
        return Decision(event, swallow)

    def handle(self, vk: int, message: int, time_ms: int, from_knob: bool, replayed: bool = False) -> Decision:
        if replayed or not from_knob:
            return Decision()
        down, up = message in _KEYDOWN, message in _KEYUP
        if vk == VK_LAUNCH_APP2:
            return self._press(vk, down, up, SCREEN, self.block_screen_press) if self.screen_press else Decision()
        if vk == VK_VOLUME_MUTE:
            return self._press(vk, down, up, VOLUME, True) if self.volume_press else Decision()
        if vk not in (VK_VOLUME_UP, VK_VOLUME_DOWN) or not self.volume_turns:
            return Decision()
        if down:
            if vk in self._passing:
                return Decision()
            if vk in self._held:  # a repeat while held: a key, not the knob; hand over the held key-down
                del self._held[vk]
                self._passing.add(vk)
                return Decision(swallow=True, replay=((vk, False),))
            self._held[vk] = time_ms
            return Decision(swallow=True)
        if up:
            if vk in self._passing:
                self._passing.discard(vk)
                return Decision()
            start = self._held.pop(vk, None)
            if start is None:
                return Decision()
            if (time_ms - start) & 0xFFFFFFFF <= self.tap_ms:
                return Decision((VOLUME, 1 if vk == VK_VOLUME_UP else -1), True)
            return Decision(swallow=True, replay=((vk, False), (vk, True)))
        return Decision()


class KnobInputHook:
    """WH_KEYBOARD_LL hook plus Raw Input on one thread with a message loop. `on_event(knob, direction)` is
    called on that thread and must return quickly (Windows drops slow hooks)."""

    def __init__(self, on_event: Callable[[str, int], None], screen_press: bool = True,
                 block_screen_press: bool = True, volume_turns: bool = False, volume_press: bool = False,
                 devices: Iterable[tuple[int, int]] = DEFAULT_DEVICES, tap_ms: int = DEFAULT_TAP_MS,
                 trace: Callable[[dict], None] | None = None, releases: bool = False) -> None:
        if sys.platform != "win32":
            raise OSError("knob capture on the PC needs Windows")
        self._trace = trace  # diagnostics: consumer reports and the four handled keys only
        self._filter = KnobKeyFilter(screen_press, block_screen_press, volume_turns, volume_press, tap_ms, releases)
        self._devices = tuple(devices)
        self._on_event = on_event
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._ready = threading.Event()
        self._error: BaseException | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="neos75-knob-input", daemon=True)
        self._thread.start()
        self._ready.wait(5)
        if self._error is not None:
            self._thread = None
            raise self._error

    def stop(self) -> None:
        thread = self._thread
        if thread is None:
            return
        self._thread = None
        if self._thread_id:
            ctypes.WinDLL("user32").PostThreadMessageW(self._thread_id, 0x0012, 0, 0)  # WM_QUIT
        thread.join(2)

    def _run(self) -> None:  # noqa: C901 - one Win32 setup, kept together
        import ctypes.wintypes as w

        lresult = ctypes.c_ssize_t
        hookproc = ctypes.WINFUNCTYPE(lresult, ctypes.c_int, w.WPARAM, w.LPARAM)
        wndproc_t = ctypes.WINFUNCTYPE(lresult, w.HWND, w.UINT, w.WPARAM, w.LPARAM)
        wm_input, wm_replay = 0x00FF, 0x8000 + 0x4E

        class KbdLLHookStruct(ctypes.Structure):
            _fields_ = [("vkCode", w.DWORD), ("scanCode", w.DWORD), ("flags", w.DWORD), ("time", w.DWORD),
                        ("dwExtraInfo", ctypes.c_size_t)]

        class RawInputDevice(ctypes.Structure):
            _fields_ = [("usUsagePage", w.USHORT), ("usUsage", w.USHORT), ("dwFlags", w.DWORD),
                        ("hwndTarget", w.HWND)]

        class RawInputHeader(ctypes.Structure):
            _fields_ = [("dwType", w.DWORD), ("dwSize", w.DWORD), ("hDevice", w.HANDLE), ("wParam", w.WPARAM)]

        class WndClass(ctypes.Structure):
            _fields_ = [("style", w.UINT), ("lpfnWndProc", wndproc_t), ("cbClsExtra", ctypes.c_int),
                        ("cbWndExtra", ctypes.c_int), ("hInstance", w.HINSTANCE), ("hIcon", w.HICON),
                        ("hCursor", w.HANDLE), ("hbrBackground", w.HBRUSH), ("lpszMenuName", w.LPCWSTR),
                        ("lpszClassName", w.LPCWSTR)]

        class KeybdInput(ctypes.Structure):
            _fields_ = [("wVk", w.WORD), ("wScan", w.WORD), ("dwFlags", w.DWORD), ("time", w.DWORD),
                        ("dwExtraInfo", ctypes.c_size_t)]

        class MouseInput(ctypes.Structure):
            _fields_ = [("dx", w.LONG), ("dy", w.LONG), ("mouseData", w.DWORD), ("dwFlags", w.DWORD),
                        ("time", w.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

        class InputUnion(ctypes.Union):
            _fields_ = [("ki", KeybdInput), ("mi", MouseInput)]

        class Input(ctypes.Structure):
            _fields_ = [("type", w.DWORD), ("u", InputUnion)]

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        user32.SetWindowsHookExW.restype = w.HHOOK
        user32.SetWindowsHookExW.argtypes = (ctypes.c_int, hookproc, w.HINSTANCE, w.DWORD)
        user32.CallNextHookEx.restype = lresult
        user32.CallNextHookEx.argtypes = (w.HHOOK, ctypes.c_int, w.WPARAM, w.LPARAM)
        user32.UnhookWindowsHookEx.argtypes = (w.HHOOK,)
        user32.DefWindowProcW.restype = lresult
        user32.DefWindowProcW.argtypes = (w.HWND, w.UINT, w.WPARAM, w.LPARAM)
        user32.RegisterClassW.argtypes = (ctypes.POINTER(WndClass),)
        user32.CreateWindowExW.restype = w.HWND
        user32.CreateWindowExW.argtypes = (w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD, ctypes.c_int, ctypes.c_int,
                                           ctypes.c_int, ctypes.c_int, w.HWND, w.HMENU, w.HINSTANCE, w.LPVOID)
        user32.DestroyWindow.argtypes = (w.HWND,)
        user32.RegisterRawInputDevices.argtypes = (ctypes.POINTER(RawInputDevice), w.UINT, w.UINT)
        user32.GetRawInputData.restype = w.UINT
        user32.GetRawInputData.argtypes = (w.HANDLE, w.UINT, w.LPVOID, ctypes.POINTER(w.UINT), w.UINT)
        user32.GetRawInputDeviceInfoW.restype = w.UINT
        user32.GetRawInputDeviceInfoW.argtypes = (w.HANDLE, w.UINT, w.LPVOID, ctypes.POINTER(w.UINT))
        user32.GetMessageW.argtypes = (ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT)
        user32.DispatchMessageW.argtypes = (ctypes.POINTER(w.MSG),)
        user32.PostThreadMessageW.argtypes = (w.DWORD, w.UINT, w.WPARAM, w.LPARAM)
        user32.SendInput.argtypes = (w.UINT, ctypes.POINTER(Input), ctypes.c_int)
        kernel32.GetModuleHandleW.restype = w.HMODULE
        kernel32.GetModuleHandleW.argtypes = (w.LPCWSTR,)

        knob_device: dict[int, bool] = {}
        last = {"knob": False, "usage": None}  # the consumer report Windows delivered most recently
        pending_replay: list[tuple[int, bool]] = []

        def is_knob_device(handle: int) -> bool:
            if handle not in knob_device:
                size = w.UINT(0)
                user32.GetRawInputDeviceInfoW(handle, 0x20000007, None, ctypes.byref(size))  # RIDI_DEVICENAME
                buf = ctypes.create_unicode_buffer(size.value + 1)
                user32.GetRawInputDeviceInfoW(handle, 0x20000007, buf, ctypes.byref(size))
                knob_device[handle] = device_matches(buf.value, self._devices)
            return knob_device[handle]

        @wndproc_t
        def wndproc(hwnd, msg, wparam, lparam):  # noqa: ANN001, ANN202
            if msg == wm_input:
                header_size = ctypes.sizeof(RawInputHeader)
                size = w.UINT(0)
                user32.GetRawInputData(lparam, 0x10000003, None, ctypes.byref(size), header_size)  # RID_INPUT
                buf = (ctypes.c_ubyte * max(size.value, header_size))()
                if user32.GetRawInputData(lparam, 0x10000003, buf, ctypes.byref(size), header_size) != 0xFFFFFFFF:
                    header = RawInputHeader.from_buffer(buf)
                    if header.dwType == 2 and size.value >= header_size + 8:  # RIM_TYPEHID
                        hid_size = int.from_bytes(bytes(buf[header_size:header_size + 4]), "little")
                        report = bytes(buf[header_size + 8:header_size + 8 + hid_size])
                        last["knob"] = is_knob_device(header.hDevice)
                        last["usage"] = consumer_usage(report)
                        if self._trace:
                            self._trace({"kind": "raw", "knob": last["knob"], "usage": last["usage"]})
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        hook = None

        @hookproc
        def proc(code: int, wparam: int, lparam: int) -> int:
            if code == 0:  # HC_ACTION
                info = ctypes.cast(lparam, ctypes.POINTER(KbdLLHookStruct)).contents
                vk = info.vkCode
                if vk in USAGE_FOR_VK:
                    down = wparam in _KEYDOWN
                    expected = USAGE_FOR_VK[vk] if down else 0
                    from_knob = bool(last["knob"]) and last["usage"] == expected
                    last["knob"], last["usage"] = False, None  # one consumer report explains one key
                    decision = self._filter.handle(vk, wparam, info.time, from_knob,
                                                   replayed=info.dwExtraInfo == REPLAY_MARK)
                    if self._trace:
                        self._trace({"kind": "key", "vk": vk, "down": down, "from_knob": from_knob,
                                     "replayed": info.dwExtraInfo == REPLAY_MARK, "event": decision.event,
                                     "swallow": decision.swallow, "replay": decision.replay})
                    if decision.event is not None:
                        try:
                            self._on_event(*decision.event)
                        except Exception:  # noqa: BLE001 - never let a callback break the system-wide hook
                            pass
                    if decision.replay:
                        pending_replay.extend(decision.replay)
                        user32.PostThreadMessageW(self._thread_id, wm_replay, 0, 0)
                    if decision.swallow:
                        return 1
            return user32.CallNextHookEx(hook, code, wparam, lparam)

        def replay() -> None:
            keys = list(pending_replay)
            pending_replay.clear()
            if not keys:
                return
            inputs = (Input * len(keys))()
            for i, (vk, keyup) in enumerate(keys):
                inputs[i].type = 1  # INPUT_KEYBOARD
                inputs[i].u.ki = KeybdInput(vk, 0, 0x0001 | (0x0002 if keyup else 0), 0, REPLAY_MARK)
            user32.SendInput(len(keys), inputs, ctypes.sizeof(Input))

        hwnd = None
        try:
            self._thread_id = kernel32.GetCurrentThreadId()
            hinstance = kernel32.GetModuleHandleW(None)
            cls = WndClass(lpfnWndProc=wndproc, hInstance=hinstance, lpszClassName=f"neos75_knob_{id(self)}")
            if not user32.RegisterClassW(ctypes.byref(cls)):
                raise ctypes.WinError(ctypes.get_last_error())
            hwnd = user32.CreateWindowExW(0, cls.lpszClassName, None, 0, 0, 0, 0, 0, w.HWND(-3), None,
                                          hinstance, None)  # HWND_MESSAGE
            if not hwnd:
                raise ctypes.WinError(ctypes.get_last_error())
            device = RawInputDevice(0x0C, 0x01, 0x00000100, hwnd)  # consumer control, RIDEV_INPUTSINK
            if not user32.RegisterRawInputDevices(ctypes.byref(device), 1, ctypes.sizeof(RawInputDevice)):
                raise ctypes.WinError(ctypes.get_last_error())
            hook = user32.SetWindowsHookExW(13, proc, hinstance, 0)  # WH_KEYBOARD_LL
            if not hook:
                raise ctypes.WinError(ctypes.get_last_error())
        except BaseException as exc:  # noqa: BLE001 - reported to start()
            self._error = exc
            if hwnd:
                user32.DestroyWindow(hwnd)
            self._ready.set()
            return
        self._ready.set()
        msg = w.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if not msg.hWnd and msg.message == wm_replay:
                    replay()
                else:
                    user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            user32.UnhookWindowsHookEx(hook)
            remove = RawInputDevice(0x0C, 0x01, 0x00000001, None)  # RIDEV_REMOVE
            user32.RegisterRawInputDevices(ctypes.byref(remove), 1, ctypes.sizeof(RawInputDevice))
            user32.DestroyWindow(hwnd)
