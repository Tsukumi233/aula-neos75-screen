"""Constrained app-only DFU writer. Imports USB support only for live operations.

Use --dry-run during offline development. Live operations require the screen to
already be in DFU; this tool never opens HID or requests entry into DFU.
"""
from __future__ import annotations

import argparse
import array
import hashlib
import json
import os
from pathlib import Path
import struct
import time

BASE, LIMIT, SECTOR = 0x8000, 0x7F000, 0x1000
# The stock-firmware backup of this keyboard: the folder in NEOS75_BACKUP, else ~/Tools/aula_neos75_backup.
BACKUP = Path(os.environ.get('NEOS75_BACKUP') or Path.home() / 'Tools' / 'aula_neos75_backup') / 'app_8000_7F000.bin'
BACKUP_SHA = '66679ff2cb06ab5a5e47673f679e14e68bf2082fb6401b3808ca58d7f2e1a40c'
MEMORY_NAME = '@Internal Flash  /0x00000000/8*4Ka,120*4Kg,3583*512Ka,4096*4Kg'
FUNC = bytes.fromhex('09 21 0b ff 00 00 10 1a 01')


def require(condition, message):
    # Safety checks must remain effective under python -O.
    if not condition:
        raise ValueError(message)


class Plan:
    def __init__(self, image: bytes):
        require(8 <= len(image) <= LIMIT - BASE, 'Image length outside app region')
        sp, reset = struct.unpack_from('<II', image)
        require(sp >> 24 == 0x20, 'Initial SP must be in 0x20xxxxxx')
        require(reset & 1 and BASE <= (reset & ~1) < BASE + len(image), 'Invalid reset vector')
        self.image = bytes(image)
        self.padded = image + b'\xff' * (-len(image) % SECTOR)
        self.end = BASE + len(self.padded)
        require(self.end <= LIMIT, 'Sector overlaps config page')
        self.sectors = tuple(range(BASE, self.end, SECTOR))
        self.sha256 = hashlib.sha256(image).hexdigest()

    def sector(self, address):
        require(type(address) is int and address in self.sectors, 'Sector not in image allow-list')
        offset = address - BASE
        return self.padded[offset:offset + SECTOR]


class Gate:
    """Validate every explicit USB transfer before the backend is called."""
    def __init__(self, plan=None):
        self.plan = plan
        self.address = None
        self.upload_next = None
        self.verified = False
        self.descriptors_ok = False
        self.erased = set()
        self.write_address = None

    def authorize(self, incoming, request, value, payload, descriptor=False):
        require(type(value) is int and 0 <= value <= 65535, 'Invalid wValue')
        if descriptor:
            require(incoming and request == 6 and value >> 8 in (1, 2, 3)
                    and type(payload) is int and 0 < payload <= 4096, 'Descriptor denied')
            return 0x80, 0x409 if value >> 8 == 3 else 0
        if incoming:
            require(request in (2, 3, 5) and type(payload) is int, 'IN request denied')
            if request == 2:
                require(self.descriptors_ok and self.address is not None
                        and value == self.upload_next and value >= 2, 'UPLOAD sequence denied')
                addr = self.address + (value - 2) * SECTOR
                require(0 < payload <= SECTOR and 0 <= addr < addr + payload <= 0x80000,
                        'Read outside internal flash')
            else:
                require(value == 0 and payload == (6 if request == 3 else 1), 'Status denied')
            return 0xA1, 0
        require(request in (1, 6), 'OUT request denied')
        require(isinstance(payload, bytes), 'OUT payload must be bytes')
        if request == 6:
            require(value == 0 and not payload, 'ABORT denied')
        elif not payload:
            require(value == 0 and self.verified and self.plan is not None,
                    'Exit requires exact image readback')
        elif value == 0 and len(payload) == 5 and payload[0] == 0x21:
            addr = struct.unpack_from('<I', payload, 1)[0]
            require(self.descriptors_ok and 0 <= addr < 0x80000, 'SET_ADDRESS denied')
        elif value == 0 and len(payload) == 5 and payload[0] == 0x41:
            require(self.descriptors_ok and self.plan is not None, 'Read-only session')
            self.plan.sector(struct.unpack_from('<I', payload, 1)[0])
        else:
            require(self.descriptors_ok and self.plan is not None and value == 2
                    and len(payload) == SECTOR and self.address is not None
                    and self.address in self.erased and self.write_address == self.address,
                    'DATA sequence denied')
            require(payload == self.plan.sector(self.address), 'DATA differs from approved image')
        return 0x21, 0

    def completed(self, incoming, request, value, payload, descriptor=False):
        if descriptor:
            return
        if incoming and request == 2:
            self.upload_next += 1
        elif not incoming and request == 1 and payload:
            if value == 0 and payload[0] == 0x21:
                self.address = struct.unpack_from('<I', payload, 1)[0]
                self.upload_next = 2
                self.write_address = self.address if self.address in self.erased else None
            elif value == 0 and payload[0] == 0x41:
                self.erased.add(struct.unpack_from('<I', payload, 1)[0])
                self.address = self.upload_next = self.write_address = None
                self.verified = False
            else:
                self.erased.discard(self.address)
                self.upload_next = self.write_address = None
                self.verified = False

    def verify(self, data):
        self.verified = False
        require(self.plan is not None and data == self.plan.padded, 'Readback mismatch; remain in DFU')
        self.verified = True


class Session:
    def __init__(self, backend, device, plan=None):
        require((device.idVendor, device.idProduct) == (0x5542, 0x1000), 'Wrong DFU device')
        self.backend, self.device, self.gate = backend, device, Gate(plan)
        self.handle = backend.open_device(device._ctx.dev)
        try:
            backend.claim_interface(self.handle, 0)
        except BaseException:
            backend.close_device(self.handle)
            raise

    def ctrl(self, incoming, request, value, payload, descriptor=False):
        require((self.device.idVendor, self.device.idProduct) == (0x5542, 0x1000), 'Wrong DFU device')
        request_type, index = self.gate.authorize(incoming, request, value, payload, descriptor)
        buf = array.array('B', [0] * payload if incoming else payload)
        count = self.backend.ctrl_transfer(self.handle, request_type, request, value, index, buf, 5000)
        require(count == len(buf), 'Short USB transfer; remain in DFU')
        self.gate.completed(incoming, request, value, payload, descriptor)
        return bytes(buf) if incoming else count

    def descriptor(self, kind, index=0):
        head_len = {1: 18, 2: 9, 3: 2}[kind]
        head = self.ctrl(True, 6, kind << 8 | index, head_len, True)
        size = struct.unpack_from('<H', head, 2)[0] if kind == 2 else head[0]
        require(head_len <= size <= 4096, 'Descriptor size mismatch')
        return head if size == head_len else self.ctrl(True, 6, kind << 8 | index, size, True)

    def string(self, index):
        require(index > 0, 'Missing string descriptor')
        raw = self.descriptor(3, index)
        require(raw[1] == 3 and raw[0] == len(raw), 'Invalid string descriptor')
        return raw[2:].decode('utf-16-le')

    def validate(self):
        raw = self.descriptor(1)
        require(raw[1] == 1 and struct.unpack_from('<HH', raw, 8) == (0x5542, 0x1000)
                and raw[17] == 1, 'Device descriptor mismatch')
        require(self.string(raw[15]) == 'UBEST DFU Screen Firmware Update', 'Wrong DFU product')
        config = self.descriptor(2)
        require(config[1] == 2 and config[4] == 1, 'Configuration mismatch')
        pos, alts, funcs = 0, [], []
        while pos < len(config):
            require(pos + 2 <= len(config), 'Truncated descriptor')
            length, kind = config[pos:pos+2]
            require(length >= 2 and pos + length <= len(config), 'Malformed descriptor')
            part = config[pos:pos+length]
            if kind == 4:
                require(length == 9 and part[2] == 0 and part[5:8] == b'\xfe\x01\x02',
                        'Interface descriptor mismatch')
                require(self.string(part[8]) == MEMORY_NAME, 'Wrong memory map')
                alts.append(part[3])
            elif kind == 0x21:
                require(part == FUNC, 'DFU functional descriptor mismatch')
                funcs.append(part)
            pos += length
        require(alts == [0, 1] and bool(funcs), 'Alternate setting mismatch')
        self.gate.descriptors_ok = True

    def status(self):
        raw = self.ctrl(True, 3, 0, 6)
        require(raw[0] == 0 and raw[4] != 10, f'DFU error {raw.hex()}')
        return int.from_bytes(raw[1:4], 'little'), raw[4]

    def idle(self):
        _, state = self.status()
        require(state in (2, 5, 9), f'Unexpected idle state {state}')
        if state != 2:
            self.ctrl(False, 6, 0, b'')
            require(self.status()[1] == 2, 'ABORT failed')

    def poll(self):
        deadline = time.monotonic() + 60
        while True:
            delay, state = self.status()
            if state == 5:
                return
            require(state in (3, 4) and time.monotonic() + delay / 1000 < deadline,
                    'DFU download timeout')
            time.sleep(delay / 1000)

    def set_address(self, address):
        self.idle()
        self.ctrl(False, 1, 0, b'\x21' + struct.pack('<I', address))
        self.poll()

    def read(self, address, length):
        require(0 < length and 0 <= address < address + length <= 0x80000, 'Read denied')
        self.set_address(address)
        self.idle()
        out = bytearray()
        while len(out) < length:
            out.extend(self.ctrl(True, 2, 2 + len(out) // SECTOR, min(SECTOR, length - len(out))))
        self.idle()
        return bytes(out)

    def write(self):
        plan = self.gate.plan
        require(plan is not None, 'Read-only session')
        for address in plan.sectors:
            self.idle()
            self.ctrl(False, 1, 0, b'\x41' + struct.pack('<I', address))
            self.poll()
            self.set_address(address)
            self.ctrl(False, 1, 2, plan.sector(address))
            self.poll()
            print(f'Written 0x{address:05X}', flush=True)
        readback = self.read(BASE, len(plan.padded))
        require(readback == plan.padded, 'Readback mismatch; remain in DFU')
        # The bootloader, not this tool, sets the trial flag during app erase.
        require(self.read(0x7F00, 4) == struct.pack('<I', 0x55AAAA55),
                'Trial flag mismatch; remain in DFU')
        self.gate.verify(readback)
        print('Exact readback verified: ' + plan.sha256, flush=True)
        self.idle()
        self.ctrl(False, 1, 0, b'')
        # The original bootloader disconnects after manifestation.
        delay, state = self.status()
        require(state in (6, 7, 8), 'Unexpected manifestation state')
        if state == 6:
            time.sleep(delay / 1000)
            require(self.status()[1] in (7, 8), 'Manifestation did not advance')

    def close(self):
        try:
            self.backend.release_interface(self.handle, 0)
        finally:
            self.backend.close_device(self.handle)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--image', type=Path)
    group.add_argument('--restore', action='store_true')
    group.add_argument('--dump', type=Path)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--backup', type=Path, help='folder of the stock-firmware backup (default: NEOS75_BACKUP, else ~/Tools/aula_neos75_backup)')
    args = parser.parse_args(argv)
    plan = None
    if args.image or args.restore:
        backup = args.backup / BACKUP.name if args.backup else BACKUP
        data = (backup if args.restore else args.image).read_bytes()
        if args.restore:
            require(hashlib.sha256(data).hexdigest() == BACKUP_SHA, 'Backup SHA-256 mismatch')
        plan = Plan(data)
        print(json.dumps({'sha256': plan.sha256, 'bytes': len(data),
                          'erase_write_sectors': [f'0x{x:05X}' for x in plan.sectors]}, indent=2))
    else:
        print('Read-only dump: 0x00000..0x7FFFF; no erase, write or DFU exit')
    if args.dry_run:
        return 0
    # No USB or HID module is imported before the dry-run return.
    import libusb_package
    import usb.backend.libusb1
    import usb.core
    backend = usb.backend.libusb1.get_backend(find_library=libusb_package.find_library)
    require(backend is not None, 'libusb backend unavailable')
    devices = list(usb.core.find(find_all=True, idVendor=0x5542, idProduct=0x1000, backend=backend))
    require(len(devices) == 1, 'Exactly one screen must already be in DFU')
    session = Session(backend, devices[0], plan)
    try:
        session.validate()
        session.idle()
        if args.dump:
            args.dump.mkdir(parents=True, exist_ok=True)
            hashes = {}
            for address, length, name in ((0, BASE, 'bootloader_0000_8000.bin'),
                                           (BASE, LIMIT-BASE, 'app_8000_7F000.bin'),
                                           (LIMIT, SECTOR, 'config_7F000_80000.bin')):
                data = session.read(address, length)
                (args.dump / name).write_bytes(data)
                hashes[name] = hashlib.sha256(data).hexdigest()
            (args.dump / 'sha256.json').write_text(json.dumps(hashes, indent=2), encoding='utf-8')
        else:
            session.write()
    finally:
        session.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
