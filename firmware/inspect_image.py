"""Offline inspection helpers; never imports a USB library."""
from pathlib import Path
import argparse
import os
import struct
from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB, CS_MODE_LITTLE_ENDIAN

# The stock-firmware backup of this keyboard: the folder in NEOS75_BACKUP, else ~/Tools/aula_neos75_backup.
BACKUP = Path(os.environ.get('NEOS75_BACKUP') or Path.home() / 'Tools' / 'aula_neos75_backup') / 'app_8000_7F000.bin'

def main():
    p = argparse.ArgumentParser()
    p.add_argument('addresses', nargs='*', type=lambda x: int(x, 16))
    p.add_argument('--size', type=lambda x: int(x, 16), default=0x80)
    args = p.parse_args()
    data = BACKUP.read_bytes()
    cs = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_LITTLE_ENDIAN)
    for addr in args.addresses:
        print(f'\nADDRESS {addr:08x}; pointer references:')
        needle = struct.pack('<I', addr | 1)
        print([hex(i+0x8000) for i in range(len(data)-3) if data[i:i+4] == needle])
        for i in cs.disasm(data[addr-0x8000:addr-0x8000+args.size], addr):
            print(f'{i.address:08x} {i.bytes.hex():10} {i.mnemonic:8} {i.op_str}')

if __name__ == '__main__':
    main()
