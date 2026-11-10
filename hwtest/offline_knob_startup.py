"""Offline probe of the approved image's original cold-start input gate."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'firmware/tests'), str(ROOT / 'firmware')]
from stock_gui import StockGUI
from test_runtime import upload, stream
from unicorn import UC_HOOK_MEM_WRITE
from unicorn.arm_const import UC_ARM_REG_PC

APPROVED = 'f1fefb2e6a5711fe979ee27877862d041e17ebf57846454466248239f2dfe048'


class ColdGUI(StockGUI):
    def call(self, name, *args, **kwargs):
        if name == 'app_gui_boot':
            # Use the original cold-start branch instead of StockGUI's explicit
            # 0x5a5a5a5a fast-start fixture. No original code is replaced.
            self.uc.mem_write(0x20000004, bytes(4))
        return super().call(name, *args, **kwargs)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out', type=Path, required=True)
    args = ap.parse_args()
    assert args.out.resolve().is_relative_to(ROOT / 'hwtest')
    assert hashlib.sha256((ROOT / 'firmware/build/neos75_rt_dev.bin').read_bytes()).hexdigest() == APPROVED
    m = ColdGUI()
    rows = []

    def watch(uc, access, address, size, value, data):
        if address <= 0x2000bf77 < address + size:
            rows.append(dict(kind='gate_write', pc=hex(uc.reg_read(UC_ARM_REG_PC)),
                             address=hex(address), size=size, value=hex(value)))

    m.uc.hook_add(UC_HOOK_MEM_WRITE, watch)

    def snapshot(label):
        state = m.state()
        row = dict(kind='snapshot', label=label,
                   tick=struct.unpack('<I', m.uc.mem_read(0x20003a54, 4))[0],
                   gate=m.uc.mem_read(0x2000bf77, 1)[0],
                   page=m.uc.mem_read(0x200044ad, 1)[0],
                   active=state.active, armed=state.armed,
                   events=[p[:10].hex() for p in m.sent
                           if p[:2] == b'\xa5\x3b' and p[5] in (0x20, 0x21)])
        rows.append(row)
        m.sent.clear()

    m.bootstrap()
    m.tick(500)
    snapshot('cold_boot')
    assert rows[-1]['gate'] == 0
    m.control(2)
    upload(m, stream(16, 16))
    m.gui()
    snapshot('host_enter_and_frame')
    m.call('key_push', 2, 0)
    for _ in range(5): m.gui()
    snapshot('first_next_while_gate_closed')
    assert rows[-1]['active'] == 1
    assert [bytes.fromhex(p)[5] for p in rows[-1]['events']] == [0x21]
    # Run original LVGL timers, including the original startup animation.
    for tick in range(600, 6100, 100):
        m.tick(tick)
        m.gui()
    snapshot('after_original_startup_timers')
    assert rows[-1]['gate'] == 1
    m.call('key_push', 2, 0)
    for _ in range(5): m.gui()
    snapshot('next_after_startup')
    assert rows[-1]['active'] == 0 and rows[-1]['page'] == 0
    assert [bytes.fromhex(p)[5] for p in rows[-1]['events']] == [0x21, 0x20]
    rows.append(dict(kind='coverage', addresses=[hex(a) for a in
                (0xecc0, 0x37ef4, 0x395f8, 0x39660, 0x1d184, 0x1d094, 0x3c128)
                if a in m.covered], stubs=[hex(a) for a in sorted(m.stubs)]))
    args.out.write_text(json.dumps(rows, indent=2), encoding='utf-8')
    print(json.dumps(rows, indent=2))


if __name__ == '__main__':
    main()
