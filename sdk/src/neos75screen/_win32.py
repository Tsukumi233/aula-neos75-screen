"""Windows ctypes HID transport, used when the `hidapi` package is unavailable.

Opens the device shared (read and write) with overlapped I/O. Interfaces are listed from the registry;
each candidate is opened with zero access only to read its HID caps (usage page and usage).
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as w
import winreg
from collections.abc import Iterator

from . import constants as c
from .errors import DeviceNotFound
from .transport import ReaderThread, Transport

_GENERIC_RW = 0x80000000 | 0x40000000
_FILE_SHARE_RW = 0x00000003
_OPEN_EXISTING = 3
_FILE_FLAG_OVERLAPPED = 0x40000000
_INVALID_HANDLE = ctypes.c_void_p(-1).value
_WAIT_OBJECT_0 = 0
_HIDP_STATUS_SUCCESS = 0x00110000
_HID_GUID = "{4d1e55b2-f16f-11cf-88cb-001111000030}"
_REPORT_LEN = c.REPORT_SIZE + 1  # report ID + 64 bytes
_WRITE_TIMEOUT_MS = 2000


class _Overlapped(ctypes.Structure):
    _fields_ = [("Internal", ctypes.c_size_t), ("InternalHigh", ctypes.c_size_t),
                ("Offset", w.DWORD), ("OffsetHigh", w.DWORD), ("hEvent", w.HANDLE)]


class _HidpCaps(ctypes.Structure):
    _fields_ = [("Usage", w.USHORT), ("UsagePage", w.USHORT), ("InputReportByteLength", w.USHORT),
                ("OutputReportByteLength", w.USHORT), ("FeatureReportByteLength", w.USHORT),
                ("Reserved", w.USHORT * 17), ("Counts", w.USHORT * 10)]


_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.CreateFileW.restype = w.HANDLE
_k32.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, w.LPVOID, w.DWORD, w.DWORD, w.HANDLE]
_k32.CreateEventW.restype = w.HANDLE
_k32.CreateEventW.argtypes = [w.LPVOID, w.BOOL, w.BOOL, w.LPCWSTR]
_k32.CloseHandle.argtypes = [w.HANDLE]
_k32.ResetEvent.argtypes = [w.HANDLE]
_k32.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
_k32.CancelIoEx.argtypes = [w.HANDLE, w.LPVOID]
_k32.WriteFile.argtypes = [w.HANDLE, w.LPCVOID, w.DWORD, w.LPVOID, ctypes.POINTER(_Overlapped)]
_k32.ReadFile.argtypes = [w.HANDLE, w.LPVOID, w.DWORD, w.LPVOID, ctypes.POINTER(_Overlapped)]
_k32.GetOverlappedResult.argtypes = [w.HANDLE, ctypes.POINTER(_Overlapped), ctypes.POINTER(w.DWORD), w.BOOL]

_hid = ctypes.WinDLL("hid", use_last_error=True)
_hid.HidD_GetPreparsedData.argtypes = [w.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
_hid.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]
_hid.HidP_GetCaps.restype = ctypes.c_long
_hid.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.POINTER(_HidpCaps)]


def _interface_paths() -> Iterator[str]:
    """Device interface paths of VID 5542 / PID 0001 from the registry (no handle is opened)."""
    root = rf"SYSTEM\CurrentControlSet\Control\DeviceClasses\{_HID_GUID}"
    needle = f"VID_{c.VID:04X}&PID_{c.PID:04X}"
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, root) as key:
        index = 0
        while True:
            try:
                name = winreg.EnumKey(key, index)
            except OSError:
                return
            index += 1
            if needle in name.upper():
                yield "\\\\?\\" + name[4:]


def _usage(path: str) -> tuple[int, int] | None:
    """(usage page, usage) of an interface, read through a zero-access handle."""
    handle = _k32.CreateFileW(path, 0, _FILE_SHARE_RW, None, _OPEN_EXISTING, 0, None)
    if handle in (None, _INVALID_HANDLE):
        return None
    try:
        data = ctypes.c_void_p()
        if not _hid.HidD_GetPreparsedData(handle, ctypes.byref(data)):
            return None
        try:
            caps = _HidpCaps()
            if _hid.HidP_GetCaps(data, ctypes.byref(caps)) != _HIDP_STATUS_SUCCESS:
                return None
            return caps.UsagePage, caps.Usage
        finally:
            _hid.HidD_FreePreparsedData(data)
    finally:
        _k32.CloseHandle(handle)


def _finish_io(handle: int, ov: _Overlapped, timeout_ms: int) -> int | None:
    """Bytes transferred by an overlapped operation, or None (I/O cancelled after the timeout)."""
    if _k32.WaitForSingleObject(ov.hEvent, timeout_ms) != _WAIT_OBJECT_0:
        _k32.CancelIoEx(handle, ctypes.byref(ov))
    count = w.DWORD(0)
    # Wait for completion either way: the buffer and OVERLAPPED must stay valid until the I/O ends.
    if not _k32.GetOverlappedResult(handle, ctypes.byref(ov), ctypes.byref(count), True):
        return None
    return count.value


class WindowsTransport(Transport):
    def __init__(self, path: str) -> None:
        super().__init__()
        handle = _k32.CreateFileW(path, _GENERIC_RW, _FILE_SHARE_RW, None, _OPEN_EXISTING,
                                  _FILE_FLAG_OVERLAPPED, None)
        if handle in (None, _INVALID_HANDLE):
            raise DeviceNotFound(f"cannot open the screen interface (error {ctypes.get_last_error()})")
        self._handle = handle
        self._read_event = _k32.CreateEventW(None, True, False, None)
        self._write_event = _k32.CreateEventW(None, True, False, None)
        self._read_buf = ctypes.create_string_buffer(_REPORT_LEN)
        self._closed = False
        self._reader = ReaderThread(self._read_raw, self._deliver)
        self._reader.start()

    @staticmethod
    def find_path() -> str:
        for path in _interface_paths():
            if _usage(path) == (c.USAGE_PAGE, c.USAGE):
                return path
        raise DeviceNotFound("no AULA Neos75 screen interface found")

    def _read_raw(self, timeout_ms: int) -> bytes | None:
        ov = _Overlapped(hEvent=self._read_event)
        _k32.ResetEvent(self._read_event)
        _k32.ReadFile(self._handle, self._read_buf, _REPORT_LEN, None, ctypes.byref(ov))
        count = _finish_io(self._handle, ov, timeout_ms)
        return self._read_buf.raw[1:count] if count else None

    def _write_report(self, report: bytes) -> None:
        ov = _Overlapped(hEvent=self._write_event)
        _k32.ResetEvent(self._write_event)
        data = bytes([c.REPORT_ID]) + report
        _k32.WriteFile(self._handle, data, _REPORT_LEN, None, ctypes.byref(ov))
        if _finish_io(self._handle, ov, _WRITE_TIMEOUT_MS) != _REPORT_LEN:
            raise OSError("HID write failed")

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._reader.stop()
            for handle in (self._read_event, self._write_event, self._handle):
                _k32.CloseHandle(handle)
