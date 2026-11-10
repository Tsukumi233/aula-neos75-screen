"""Probe how the knob press reaches Windows, and whether a low-level keyboard hook can stop it.

The keyboard sends the press to the PC itself (by default the "Calculator" media key); it never reaches the
screen MCU. This installs a WH_KEYBOARD_LL hook for a fixed time and logs ONLY media/launch virtual keys
(VK 0xA6..0xB7). Ordinary typing is never recorded. With --block, VK_LAUNCH_APP2 (calculator) is swallowed.
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
WH_KEYBOARD_LL = 13
WM_QUIT = 0x0012
HC_ACTION = 0
VK_LAUNCH_APP2 = 0xB7
MEDIA_VKS = range(0xA6, 0xB8)
MESSAGES = {0x0100: "keydown", 0x0101: "keyup", 0x0104: "syskeydown", 0x0105: "syskeyup"}

LRESULT = ctypes.c_ssize_t
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, w.WPARAM, w.LPARAM)


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", w.DWORD), ("scanCode", w.DWORD), ("flags", w.DWORD), ("time", w.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32.SetWindowsHookExW.restype = w.HHOOK
user32.SetWindowsHookExW.argtypes = (ctypes.c_int, HOOKPROC, w.HINSTANCE, w.DWORD)
user32.CallNextHookEx.restype = LRESULT
user32.CallNextHookEx.argtypes = (w.HHOOK, ctypes.c_int, w.WPARAM, w.LPARAM)
user32.UnhookWindowsHookEx.argtypes = (w.HHOOK,)
user32.GetMessageW.argtypes = (ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT)
user32.PostThreadMessageW.argtypes = (w.DWORD, w.UINT, w.WPARAM, w.LPARAM)
kernel32.GetModuleHandleW.restype = w.HMODULE
kernel32.GetModuleHandleW.argtypes = (w.LPCWSTR,)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=60)
    parser.add_argument("--block", action="store_true", help="swallow VK_LAUNCH_APP2 (calculator)")
    parser.add_argument("--log", type=Path, default=ROOT / "hwtest/knob_press_probe.jsonl")
    args = parser.parse_args()
    output = args.log.open("a", encoding="utf-8", buffering=1)

    def record(**row) -> None:
        row = dict(time=datetime.now().astimezone().isoformat(), **row)
        line = json.dumps(row, ensure_ascii=False)
        output.write(line + "\n")
        print(line, flush=True)

    hook = None

    @HOOKPROC
    def proc(code: int, wparam: int, lparam: int) -> int:
        if code == HC_ACTION:
            info = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            if info.vkCode in MEDIA_VKS:
                blocked = args.block and info.vkCode == VK_LAUNCH_APP2
                record(kind="key", vk=hex(info.vkCode), scan=hex(info.scanCode), flags=hex(info.flags),
                       message=MESSAGES.get(wparam, hex(wparam)), blocked=blocked)
                if blocked:
                    return 1
        return user32.CallNextHookEx(hook, code, wparam, lparam)

    hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, proc, kernel32.GetModuleHandleW(None), 0)
    if not hook:
        raise ctypes.WinError(ctypes.get_last_error())
    thread_id = kernel32.GetCurrentThreadId()
    threading.Timer(args.seconds, lambda: user32.PostThreadMessageW(thread_id, WM_QUIT, 0, 0)).start()
    record(kind="start", seconds=args.seconds, block=args.block)
    message = w.MSG()
    try:
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            pass
    finally:
        user32.UnhookWindowsHookEx(hook)
        record(kind="end")
        output.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
