"""Talk to the AULA Neos75 screen over HID (VID 0x5542 / PID 0x0001) for the DFU read-only probe.

Allowed packets, nothing else:
  --version : 11-class read-only queries 11 01 (model string) and 11 02 (build string)
  --enter   : 13 02 00 00 00 00 78 B7 + zeros, the exact "enter upgrade mode" packet Aula Hub sends
All packets are 64 bytes; bytes 6..7 carry the firmware's CRC16 (CCITT table, init 0xFFFF, over all 64 bytes
with 6..7 zeroed). The 13 02 value 78 B7 was checked against the firmware's own table.
"""
import argparse
import ctypes
import ctypes.wintypes as w
import winreg

GENERIC_RW = 0x80000000 | 0x40000000
FILE_SHARE_RW = 3
OPEN_EXISTING = 3
FILE_FLAG_OVERLAPPED = 0x40000000
INVALID = ctypes.c_void_p(-1).value
HID_GUID = '{4d1e55b2-f16f-11cf-88cb-001111000030}'
REPORT_LEN = 65

k32 = ctypes.WinDLL('kernel32', use_last_error=True)
k32.CreateFileW.restype = w.HANDLE
k32.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, w.LPVOID, w.DWORD, w.DWORD, w.HANDLE]
k32.CreateEventW.restype = w.HANDLE
k32.CreateEventW.argtypes = [w.LPVOID, w.BOOL, w.BOOL, w.LPCWSTR]
k32.CloseHandle.argtypes = [w.HANDLE]
k32.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
k32.CancelIoEx.argtypes = [w.HANDLE, w.LPVOID]


class OVERLAPPED(ctypes.Structure):
    _fields_ = [('Internal', ctypes.c_size_t), ('InternalHigh', ctypes.c_size_t),
                ('Offset', w.DWORD), ('OffsetHigh', w.DWORD), ('hEvent', w.HANDLE)]


k32.WriteFile.argtypes = [w.HANDLE, w.LPCVOID, w.DWORD, ctypes.POINTER(w.DWORD), ctypes.POINTER(OVERLAPPED)]
k32.ReadFile.argtypes = [w.HANDLE, w.LPVOID, w.DWORD, ctypes.POINTER(w.DWORD), ctypes.POINTER(OVERLAPPED)]
k32.GetOverlappedResult.argtypes = [w.HANDLE, ctypes.POINTER(OVERLAPPED), ctypes.POINTER(w.DWORD), w.BOOL]


def _crc_table():
    t = []
    for i in range(256):
        c = i << 8
        for _ in range(8):
            c = ((c << 1) ^ 0x1021) if c & 0x8000 else (c << 1)
        t.append(c & 0xFFFF)
    return t


TABLE = _crc_table()


def build(cls, sub):
    pkt = bytearray(64)
    pkt[0], pkt[1] = cls, sub
    c = 0xFFFF
    for b in pkt:
        c = (TABLE[(b ^ (c >> 8)) & 0xFF] ^ (c << 8)) & 0xFFFF
    pkt[6], pkt[7] = c & 0xFF, c >> 8
    return bytes(pkt)


ALLOWED = {build(0x11, 0x01), build(0x11, 0x02), build(0x13, 0x02)}
assert build(0x13, 0x02)[:8] == bytes.fromhex('130200000000 78B7'.replace(' ', '')), 'CRC mismatch with Aula Hub constant'


def paths():
    root = r'SYSTEM\CurrentControlSet\Control\DeviceClasses' + '\\' + HID_GUID
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, root) as key:
        i = 0
        while True:
            try:
                name = winreg.EnumKey(key, i)
            except OSError:
                return
            i += 1
            if 'VID_5542&PID_0001' in name.upper():
                yield '\\\\?\\' + name[4:]


def wait(h, ov, ev, ms):
    if k32.WaitForSingleObject(ev, ms) != 0:
        k32.CancelIoEx(h, ctypes.byref(ov))
        return None
    n = w.DWORD(0)
    return n.value if k32.GetOverlappedResult(h, ctypes.byref(ov), ctypes.byref(n), False) else None


def send(h, pkt, expect_reply=True, timeout_ms=1000):
    assert pkt in ALLOWED, 'packet not on the allow-list'
    ev = k32.CreateEventW(None, True, False, None)
    buf = ctypes.create_string_buffer(REPORT_LEN)
    rd = OVERLAPPED(hEvent=ev)
    if expect_reply:
        k32.ReadFile(h, buf, REPORT_LEN, None, ctypes.byref(rd))
    ev2 = k32.CreateEventW(None, True, False, None)
    wr = OVERLAPPED(hEvent=ev2)
    k32.WriteFile(h, b'\x00' + pkt, REPORT_LEN, None, ctypes.byref(wr))
    written = wait(h, wr, ev2, 1000)
    print('TX', pkt[:8].hex(' '), '+ zeros, wrote', written)
    k32.CloseHandle(ev2)
    reply = None
    if expect_reply:
        n = wait(h, rd, ev, timeout_ms)
        reply = buf.raw[1:n] if n else None
        print('RX', reply[:48].hex(' ') if reply else 'none')
    k32.CloseHandle(ev)
    assert written == REPORT_LEN, f'HID short/failed write: {written}'
    return reply


def text(reply):
    if not reply:
        return None
    body = reply[8:]
    return body.split(b'\x00')[0].decode('latin1', 'replace')


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--version', action='store_true')
    g.add_argument('--enter', action='store_true')
    a = ap.parse_args()
    for p in paths():
        h = k32.CreateFileW(p, GENERIC_RW, FILE_SHARE_RW, None, OPEN_EXISTING, FILE_FLAG_OVERLAPPED, None)
        if h in (None, INVALID):
            continue
        try:
            if a.version:
                for sub in (0x01, 0x02):
                    r = send(h, build(0x11, sub))
                    print(f'  11 {sub:02X} text:', repr(text(r)))
            else:
                send(h, build(0x13, 0x02), expect_reply=False)
                print('sent enter-upgrade-mode; the screen should drop off USB and come back as a DFU device')
        finally:
            k32.CloseHandle(h)
        return
    print('screen HID device not found')


if __name__ == '__main__':
    main()
