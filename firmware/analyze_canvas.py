"""Export bounded, reproducible v2 evidence from the installed backup offline."""
from pathlib import Path
import struct

from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB, CS_MODE_LITTLE_ENDIAN
from make_patch import BACKUP, BASE, SHA256, check_input


def main():
    data = BACKUP.read_bytes()
    check_input(data)
    cs = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_LITTLE_ENDIAN)
    lines = [f'Installed app SHA-256: {SHA256}',
             'VA-addressed static disassembly; no device or peripheral execution.', '']
    ranges = ((0x3230c, 0x3256c, 'Resource JPEG lookup and output arguments'),
              (0x235d4, 0x235e2, 'Resource cookie invalidation'),
              (0x23fa0, 0x23ff6, 'LVGL cache invalidation'),
              (0x24814, 0x2483a, 'LVGL cache close'),
              (0x2aa60, 0x2aacc, 'Zero all four padding style properties'),
              (0x94c6, 0x950c, 'JPEG dimensions and packed output address'),
              (0x9570, 0x95a8, 'RGB565 pitch derives from parsed width'),
              (0x9624, 0x962e, 'Pitch register write'))
    for start, end, title in ranges:
        lines.extend([f'{title} [{start:08X},{end:08X}):'])
        for i in cs.disasm(data[start-BASE:end-BASE], start):
            lines.append(f'{i.address:08X} {i.bytes.hex():10} {i.mnemonic:8} {i.op_str}')
        lines.append('')
    for address in (0x98b8, 0x98c0, 0x98c8):
        raw = data[address-BASE:address-BASE+8]
        lines.append(f'{address:08X} {raw.hex()} double={struct.unpack("<d", raw)[0]}')
    lines += ['', 'Input: [800D4200,800FE504), cap 0002A304.',
              'Resource pixels: [800FE604,8017CF04), cap 0007E900.',
              'Realtime packed pixels: [800FE604,80152C04), cap 00054600.',
              'Runtime reservation: [8017CF08,8017E108).',
              'GUI heap: [8017E108,801DCF08).', '']
    out = Path(__file__).parent / 'analysis/canvas_v2.txt'
    out.write_text('\n'.join(lines), encoding='utf-8')
    print(out)


if __name__ == '__main__':
    main()
