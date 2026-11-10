"""Read-only probe of the AULA Neos75 screen's DFU bootloader (VID 0x5542, DFU class FE/01).

Every control transfer goes through ctrl(), which allows only:
  IN : standard GET_DESCRIPTOR, DFU UPLOAD(2), GETSTATUS(3), GETSTATE(5)
  OUT: DFU CLRSTATUS(4), ABORT(6), DNLOAD(1) carrying exactly the 5-byte DfuSe SET_ADDRESS (0x21 + addr),
       and a zero-length DNLOAD (leave DFU) only with --exit.
DfuSe ERASE (0x41) and any data DNLOAD are impossible through ctrl().
"""
import argparse
import hashlib
import json
import struct
import sys
import time
from pathlib import Path

import libusb_package
import usb.backend.libusb1
import usb.core
import usb.util

DNLOAD, UPLOAD, GETSTATUS, CLRSTATUS, GETSTATE, ABORT = 1, 2, 3, 4, 5, 6
STATES = {0: 'appIDLE', 1: 'appDETACH', 2: 'dfuIDLE', 3: 'dfuDNLOAD-SYNC', 4: 'dfuDNBUSY', 5: 'dfuDNLOAD-IDLE',
          6: 'dfuMANIFEST-SYNC', 7: 'dfuMANIFEST', 8: 'dfuMANIFEST-WAIT-RESET', 9: 'dfuUPLOAD-IDLE', 10: 'dfuERROR'}


class Probe:
    def __init__(self, dev, intf, allow_exit):
        self.dev, self.intf, self.allow_exit = dev, intf, allow_exit

    def ctrl(self, direction_in, req, value, data_or_len, std_descriptor=False):
        if std_descriptor:
            assert direction_in and req == 6, 'only GET_DESCRIPTOR is allowed as a standard request'
            return self.dev.ctrl_transfer(0x80, 6, value, 0 if value >> 8 != 3 else 0x0409, data_or_len, 2000)
        if direction_in:
            assert req in (UPLOAD, GETSTATUS, GETSTATE), f'IN request {req} not allowed'
            return self.dev.ctrl_transfer(0xA1, req, value, self.intf, data_or_len, 5000)
        assert req in (DNLOAD, CLRSTATUS, ABORT), f'OUT request {req} not allowed'
        if req == DNLOAD:
            data = bytes(data_or_len)
            ok_setaddr = len(data) == 5 and data[0] == 0x21 and value == 0
            ok_exit = len(data) == 0 and self.allow_exit
            assert ok_setaddr or ok_exit, 'DNLOAD payload not allowed (only SET_ADDRESS, or zero-length with --exit)'
            return self.dev.ctrl_transfer(0x21, req, value, self.intf, data, 5000)
        return self.dev.ctrl_transfer(0x21, req, value, self.intf, None, 5000)

    def status(self):
        r = bytes(self.ctrl(True, GETSTATUS, 0, 6))
        poll = r[1] | r[2] << 8 | r[3] << 16
        return r[0], poll, r[4]

    def to_idle(self):
        st, _, state = self.status()
        if state == 10:
            self.ctrl(False, CLRSTATUS, 0, None)
        elif state != 2:
            self.ctrl(False, ABORT, 0, None)
        st, _, state = self.status()
        return state

    def set_address(self, addr):
        self.to_idle()
        self.ctrl(False, DNLOAD, 0, b'\x21' + struct.pack('<I', addr))
        for _ in range(100):
            st, poll, state = self.status()
            if state != 4:
                break
            time.sleep(max(poll, 5) / 1000)
        if st != 0:
            raise RuntimeError(f'SET_ADDRESS {addr:#x} failed: status {st} state {STATES.get(state, state)}')
        self.to_idle()

    def read(self, addr, length, xfer):
        self.set_address(addr)
        out, block = bytearray(), 2
        while len(out) < length:
            chunk = bytes(self.ctrl(True, UPLOAD, block, min(xfer, length - len(out))))
            if not chunk:
                break
            out += chunk
            block += 1
        self.to_idle()
        return bytes(out)


def string(dev, idx):
    if not idx:
        return None
    try:
        return usb.util.get_string(dev, idx, 0x0409)
    except Exception as e:  # noqa: BLE001 - report and continue
        return f'<error {e}>'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', required=True)
    ap.add_argument('--dump', action='store_true', help='read internal flash 0x0..0x80000')
    ap.add_argument('--exit', action='store_true', help='send zero-length DNLOAD at the end to leave DFU')
    ap.add_argument('--wait', type=float, default=0, help='seconds to wait for the DFU device to appear')
    ap.add_argument('--alt', type=int, default=None, help='force this alternate setting')
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    be = usb.backend.libusb1.get_backend(find_library=libusb_package.find_library)
    deadline = time.time() + a.wait
    while True:
        devs = list(usb.core.find(find_all=True, idVendor=0x5542, backend=be))
        if any(d.idProduct == 0x1000 for d in devs) or time.time() > deadline:
            break
        time.sleep(0.5)
    devs = [d for d in devs if d.idProduct == 0x1000] or devs
    info = {'devices': []}
    target, alts, func_desc = None, [], b''
    for d in devs:
        rec = {'vid': f'{d.idVendor:04x}', 'pid': f'{d.idProduct:04x}', 'bcdDevice': f'{d.bcdDevice:04x}'}
        try:
            rec['product'] = string(d, d.iProduct)
            rec['manufacturer'] = string(d, d.iManufacturer)
            rec['serial'] = string(d, d.iSerialNumber)
            for cfg in d:
                for itf in cfg:
                    if itf.bInterfaceClass == 0xFE and itf.bInterfaceSubClass == 0x01:
                        alt = {'interface': itf.bInterfaceNumber, 'alt': itf.bAlternateSetting,
                               'protocol': itf.bInterfaceProtocol, 'name': string(d, itf.iInterface),
                               'extra': bytes(itf.extra_descriptors).hex(' ')}
                        rec.setdefault('dfu_alts', []).append(alt)
                        alts.append((d, itf, alt['name'] or ''))
                        if itf.extra_descriptors:
                            func_desc = bytes(itf.extra_descriptors)
        except Exception as e:  # noqa: BLE001
            rec['error'] = repr(e)
        info['devices'].append(rec)
    print(json.dumps(info, indent=2, ensure_ascii=False))
    for d, itf, name in alts:
        if '/0x00000000/' in name.replace(' ', '') or (a.alt is not None and itf.bAlternateSetting == a.alt):
            target = (d, itf)
            break
    if not target and alts:
        target = (alts[0][0], alts[0][1])
    if not target:
        print('no DFU interface found (driver missing, or not in DFU mode)')
        (out / 'probe.json').write_text(json.dumps(info, indent=2, ensure_ascii=False), encoding='utf-8')
        return 1
    dev, itf = target
    info['selected_alt'] = itf.bAlternateSetting
    extra = func_desc
    xfer = 1024
    if len(extra) >= 9 and extra[1] == 0x21:
        attrs, detach, xfer, bcd = extra[2], struct.unpack('<H', extra[3:5])[0], struct.unpack('<H', extra[5:7])[0], struct.unpack('<H', extra[7:9])[0]
        info['dfu_functional'] = {'bmAttributes': attrs, 'canDnload': bool(attrs & 1), 'canUpload': bool(attrs & 2),
                                  'manifestTolerant': bool(attrs & 4), 'willDetach': bool(attrs & 8),
                                  'wDetachTimeout': detach, 'wTransferSize': xfer, 'bcdDFU': f'{bcd:04x}'}
    try:
        dev.set_configuration()
    except usb.core.USBError as e:
        info['set_configuration'] = repr(e)
    usb.util.claim_interface(dev, itf.bInterfaceNumber)
    dev.set_interface_altsetting(itf.bInterfaceNumber, itf.bAlternateSetting)
    p = Probe(dev, itf.bInterfaceNumber, a.exit)
    st, poll, state = p.status()
    info['initial_status'] = {'status': st, 'state': STATES.get(state, state)}
    if a.dump:
        regions = [(0x00000, 0x8000, 'bootloader_0000_8000.bin'), (0x08000, 0x77000, 'app_8000_7F000.bin'),
                   (0x7F000, 0x1000, 'config_7F000_80000.bin')]
        info['reads'] = []
        for addr, length, name in regions:
            rec = {'addr': f'{addr:#x}', 'len': length, 'file': name}
            t0 = time.time()
            try:
                data = p.read(addr, length, xfer)
                (out / name).write_bytes(data)
                rec.update(got=len(data), sha256=hashlib.sha256(data).hexdigest(), seconds=round(time.time() - t0, 2),
                           all_ff=data.count(0xFF) == len(data), all_00=data.count(0) == len(data))
            except Exception as e:  # noqa: BLE001 - a protected region is a result, not a crash
                rec['error'] = repr(e)
                try:
                    p.to_idle()
                except Exception:  # noqa: BLE001
                    pass
            print(rec)
            info['reads'].append(rec)
    if a.exit:
        p.to_idle()
        try:
            p.ctrl(False, DNLOAD, 0, b'')
            info['exit'] = 'zero-length DNLOAD sent'
            st, poll, state = p.status()
            info['exit_status'] = {'status': st, 'state': STATES.get(state, state)}
        except Exception as e:  # noqa: BLE001 - device usually drops off the bus here
            info['exit'] = f'zero-length DNLOAD sent; device left the bus or errored: {e!r}'
    usb.util.release_interface(dev, itf.bInterfaceNumber)
    (out / 'probe.json').write_text(json.dumps(info, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps({k: v for k, v in info.items() if k != 'devices'}, indent=2, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
