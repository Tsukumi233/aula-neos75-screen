"""Probe which device sends each volume/calculator key, and in which order Windows delivers them.

Runs, on one thread, a WH_KEYBOARD_LL hook plus Raw Input (RIDEV_INPUTSINK) for keyboards (page 1 usage 6)
and consumer controls (page 0x0C usage 1). Nothing is blocked. Only media/launch keys are recorded:
keyboard raw input and hook events are logged only for VK 0xA6..0xB7, consumer-control reports are logged
as raw bytes (they carry only media usages). Ordinary typing is never recorded.
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as w
import json
import threading
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MEDIA_VKS = range(0xA6, 0xB8)
WH_KEYBOARD_LL, HC_ACTION, WM_QUIT, WM_INPUT = 13, 0, 0x0012, 0x00FF
RIDEV_INPUTSINK, RID_INPUT, RIDI_DEVICENAME = 0x00000100, 0x10000003, 0x20000007
RIM_TYPEKEYBOARD, RIM_TYPEHID = 1, 2
HWND_MESSAGE = w.HWND(-3)

LRESULT = ctypes.c_ssize_t
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, w.WPARAM, w.LPARAM)
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, w.HWND, w.UINT, w.WPARAM, w.LPARAM)


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", w.DWORD), ("scanCode", w.DWORD), ("flags", w.DWORD), ("time", w.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [("usUsagePage", w.USHORT), ("usUsage", w.USHORT), ("dwFlags", w.DWORD), ("hwndTarget", w.HWND)]


class RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [("dwType", w.DWORD), ("dwSize", w.DWORD), ("hDevice", w.HANDLE), ("wParam", w.WPARAM)]


class RAWKEYBOARD(ctypes.Structure):
    _fields_ = [("MakeCode", w.USHORT), ("Flags", w.USHORT), ("Reserved", w.USHORT), ("VKey", w.USHORT),
                ("Message", w.UINT), ("ExtraInformation", w.ULONG)]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", w.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", w.HINSTANCE), ("hIcon", w.HICON),
                ("hCursor", w.HANDLE), ("hbrBackground", w.HBRUSH), ("lpszMenuName", w.LPCWSTR),
                ("lpszClassName", w.LPCWSTR)]


user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32.SetWindowsHookExW.restype = w.HHOOK
user32.SetWindowsHookExW.argtypes = (ctypes.c_int, HOOKPROC, w.HINSTANCE, w.DWORD)
user32.CallNextHookEx.restype = LRESULT
user32.CallNextHookEx.argtypes = (w.HHOOK, ctypes.c_int, w.WPARAM, w.LPARAM)
user32.UnhookWindowsHookEx.argtypes = (w.HHOOK,)
user32.DefWindowProcW.restype = LRESULT
user32.DefWindowProcW.argtypes = (w.HWND, w.UINT, w.WPARAM, w.LPARAM)
user32.RegisterClassW.argtypes = (ctypes.POINTER(WNDCLASSW),)
user32.CreateWindowExW.restype = w.HWND
user32.CreateWindowExW.argtypes = (w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, w.HWND, w.HMENU, w.HINSTANCE, w.LPVOID)
user32.RegisterRawInputDevices.argtypes = (ctypes.POINTER(RAWINPUTDEVICE), w.UINT, w.UINT)
user32.GetRawInputData.restype = w.UINT
user32.GetRawInputData.argtypes = (w.HANDLE, w.UINT, w.LPVOID, ctypes.POINTER(w.UINT), w.UINT)
user32.GetRawInputDeviceInfoW.restype = w.UINT
user32.GetRawInputDeviceInfoW.argtypes = (w.HANDLE, w.UINT, w.LPVOID, ctypes.POINTER(w.UINT))
user32.GetMessageW.argtypes = (ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT)
user32.DispatchMessageW.argtypes = (ctypes.POINTER(w.MSG),)
user32.PostThreadMessageW.argtypes = (w.DWORD, w.UINT, w.WPARAM, w.LPARAM)
kernel32.GetModuleHandleW.restype = w.HMODULE
kernel32.GetModuleHandleW.argtypes = (w.LPCWSTR,)

_names: dict[int, str] = {}


def device_name(handle: int) -> str:
    if not handle:
        return "<none>"
    if handle not in _names:
        size = w.UINT(0)
        user32.GetRawInputDeviceInfoW(handle, RIDI_DEVICENAME, None, ctypes.byref(size))
        buf = ctypes.create_unicode_buffer(size.value + 1)
        user32.GetRawInputDeviceInfoW(handle, RIDI_DEVICENAME, buf, ctypes.byref(size))
        _names[handle] = buf.value
    return _names[handle]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=30)
    parser.add_argument("--log", type=Path, default=ROOT / "hwtest/knob_device_probe.jsonl")
    args = parser.parse_args()
    output = args.log.open("a", encoding="utf-8", buffering=1)

    def record(**row) -> None:
        row = dict(time=datetime.now().astimezone().isoformat(), t=round(time.perf_counter(), 4), **row)
        line = json.dumps(row, ensure_ascii=False)
        output.write(line + "\n")
        print(line, flush=True)

    @WNDPROC
    def wndproc(hwnd, msg, wparam, lparam):  # noqa: ANN001, ANN202
        if msg == WM_INPUT:
            size = w.UINT(0)
            user32.GetRawInputData(lparam, RID_INPUT, None, ctypes.byref(size), ctypes.sizeof(RAWINPUTHEADER))
            buf = (ctypes.c_ubyte * size.value)()
            user32.GetRawInputData(lparam, RID_INPUT, buf, ctypes.byref(size), ctypes.sizeof(RAWINPUTHEADER))
            header = RAWINPUTHEADER.from_buffer(buf)
            body = ctypes.sizeof(RAWINPUTHEADER)
            if header.dwType == RIM_TYPEKEYBOARD:
                kb = RAWKEYBOARD.from_buffer(buf, body)
                if kb.VKey in MEDIA_VKS:
                    record(kind="raw_keyboard", vk=hex(kb.VKey), make=hex(kb.MakeCode), flags=hex(kb.Flags),
                           message=hex(kb.Message), device=device_name(header.hDevice))
            elif header.dwType == RIM_TYPEHID:
                hid_size, count = (w.DWORD * 2).from_buffer(buf, body)
                data = bytes(buf[body + 8: body + 8 + hid_size * count])
                record(kind="raw_hid", data=data.hex(), size=hid_size, count=count,
                       device=device_name(header.hDevice))
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    hinstance = kernel32.GetModuleHandleW(None)
    cls = WNDCLASSW(lpfnWndProc=wndproc, hInstance=hinstance, lpszClassName="neos75_device_probe")
    if not user32.RegisterClassW(ctypes.byref(cls)):
        raise ctypes.WinError(ctypes.get_last_error())
    hwnd = user32.CreateWindowExW(0, cls.lpszClassName, None, 0, 0, 0, 0, 0, HWND_MESSAGE, None, hinstance, None)
    if not hwnd:
        raise ctypes.WinError(ctypes.get_last_error())
    devices = (RAWINPUTDEVICE * 2)(RAWINPUTDEVICE(0x01, 0x06, RIDEV_INPUTSINK, hwnd),
                                   RAWINPUTDEVICE(0x0C, 0x01, RIDEV_INPUTSINK, hwnd))
    if not user32.RegisterRawInputDevices(devices, 2, ctypes.sizeof(RAWINPUTDEVICE)):
        raise ctypes.WinError(ctypes.get_last_error())

    hook = None

    @HOOKPROC
    def proc(code, wparam, lparam):  # noqa: ANN001, ANN202
        if code == HC_ACTION:
            info = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            if info.vkCode in MEDIA_VKS:
                record(kind="hook", vk=hex(info.vkCode), scan=hex(info.scanCode), flags=hex(info.flags),
                       message=hex(wparam), extra=info.dwExtraInfo)
        return user32.CallNextHookEx(hook, code, wparam, lparam)

    hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, proc, hinstance, 0)
    if not hook:
        raise ctypes.WinError(ctypes.get_last_error())
    thread_id = kernel32.GetCurrentThreadId()
    threading.Timer(args.seconds, lambda: user32.PostThreadMessageW(thread_id, WM_QUIT, 0, 0)).start()
    record(kind="start", seconds=args.seconds)
    msg = w.MSG()
    try:
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.DispatchMessageW(ctypes.byref(msg))
    finally:
        user32.UnhookWindowsHookEx(hook)
        record(kind="end")
        output.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
