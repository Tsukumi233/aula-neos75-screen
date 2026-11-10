"""Offline provenance probe: native status persistence while the slot is hidden."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'firmware/tests'), str(ROOT / 'firmware')]
from stock_gui import StockGUI
from test_runtime import upload, stream


def run(armed):
    m = StockGUI()
    m.bootstrap()
    m.tick(500)
    if armed:
        m.control(2)
        upload(m, stream(16, 16))
        m.gui()
        m.call('key_push', 2, 0)
        for _ in range(5): m.gui()
    else:
        # Select the same stock page through the real original key/page chain.
        m.call('key_push', 2, 0)
        for _ in range(5): m.gui()
    assert not m.state().active
    assert m.uc.mem_read(0x200044ad, 1)[0] == 0
    result = []
    for tick, mode in ((600, 2), (700, 3)):
        m.trace.clear()
        before = bytes(m.uc.mem_read(0x7f000, 4096))
        # A constructed UART telemetry message, only inside Unicorn. The actual
        # factory dispatcher, RAM update, GUI timer and config chain execute.
        m.telemetry(mode)
        m.tick(tick)
        m.gui()
        after = bytes(m.uc.mem_read(0x7f000, 4096))
        result.append(dict(mode=mode, armed=bool(m.state().armed),
                           restoring=m.state().restoring,
                           diff_bytes=sum(a != b for a, b in zip(before, after)),
                           trace=[r for r in m.trace if r['kind'] in ('config_call', 'ram_write')]))
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out', type=Path, required=True)
    args = ap.parse_args()
    assert args.out.resolve().is_relative_to(ROOT / 'hwtest')
    assert hashlib.sha256((ROOT / 'firmware/build/neos75_rt_dev.bin').read_bytes()).hexdigest() == \
        'f1fefb2e6a5711fe979ee27877862d041e17ebf57846454466248239f2dfe048'
    result = dict(armed_hidden=run(True), disarmed_stock=run(False))
    for rows in result.values():
        for row in rows:
            saves = [r for r in row['trace'] if r['kind'] == 'config_call' and r['address'] == '0x179d8']
            assert len(saves) == 1 and saves[0]['args'][:3] == ['0x2000466d', '0x11', '0x2']
            assert row['diff_bytes'] > 0 and row['restoring'] == 0
    args.out.write_text(json.dumps(result, indent=2), encoding='utf-8')
    for label, rows in result.items():
        print(label, [(r['mode'], r['diff_bytes'], [t['address'] for t in r['trace']
                      if t['kind'] == 'config_call']) for r in rows])


if __name__ == '__main__':
    main()
