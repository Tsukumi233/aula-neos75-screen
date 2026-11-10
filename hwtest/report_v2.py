"""Audit recorded v2 application/DFU traffic offline; never opens a device."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'sdk/src'))
from neos75screen import protocol as p
from run_v2 import DEV_SHA, BACKUP


def sha(data): return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_dir', type=Path)
    run = parser.parse_args().run_dir.resolve()
    rows = [json.loads(line) for line in (run/'events.jsonl').open(encoding='utf-8')]
    events, erase, download_rows, classes = [], [], [], {}
    bad_checksums = 0
    for row in rows:
        kind = row['kind']
        if kind in ('hid_tx', 'hid_rx'):
            raw = bytes.fromhex(row['report'])
            if kind == 'hid_tx':
                key = f'{raw[0]:02X}' + (f' {raw[1]:02X}' if raw[0] in (0xa5,0x11) else '')
                classes[key] = classes.get(key,0)+1
                assert raw[0] == 0xba or raw[:2] in (b'\xa5\x3b',b'\x11\x01',b'\x11\x02')
                if p.looks_like_ba_start(raw): assert raw[3] in (0x5a,0x5b)
            elif raw[:2] == b'\xa5\x3b':
                bad_checksums += not p.a5_checksum_ok(raw)
                if p.is_frame_done(raw): events.append(p.parse_frame_done(raw))
        elif kind == 'dfu_ctrl' and not row['incoming'] and row['request'] == 1:
            if row['value'] == 0 and row['length'] == 5 and row.get('hex','').startswith('41'):
                erase.append(struct.unpack('<I', bytes.fromhex(row['hex'])[1:])[0])
            if row['value'] == 2: download_rows.append(row)
    dev = (ROOT/'firmware/build/neos75_rt_dev.bin').read_bytes()
    pre = run/'pre'; after = run/'after_controls'
    app = (after/'app_8000_7F000.bin').read_bytes()
    beforeboot = (pre/'bootloader_0000_8000.bin').read_bytes()
    boot = (after/'bootloader_0000_8000.bin').read_bytes()
    config = (pre/'config_7F000_80000.bin').read_bytes()
    assert sha(dev) == DEV_SHA
    assert app == dev+b'\xff'*(len(app)-len(dev))
    config_after = (after/'config_7F000_80000.bin').read_bytes()
    config_diff = [dict(address=hex(0x7f000+i),offset=hex(i),before=a,after=b)
                   for i,(a,b) in enumerate(zip(config,config_after)) if a != b]
    assert boot == beforeboot
    assert boot[0x7f00:0x7f10] == struct.pack('<I',0x55aaaa55)+bytes(12)
    restored = any(r['kind'] == 'restore_boot_verified' for r in rows)
    images = [dev]
    if restored: images.append((BACKUP/'app_8000_7F000.bin').read_bytes())
    expected_erase, expected_downloads = [], []
    for image in images:
        padded = image+b'\xff'*(-len(image)%4096)
        for offset in range(0,len(padded),4096):
            expected_erase.append(0x8000+offset)
            expected_downloads.append(sha(padded[offset:offset+4096]))
    assert erase == expected_erase
    assert [r['sha256'] for r in download_rows] == expected_downloads
    assert bad_checksums == 0
    assert [e.seq for e in events] == list(range(1,len(events)+1))
    faults = [r for r in rows if r['kind'] == 'fault_result']
    last = next((r for r in reversed(rows) if r['kind'] == 'final_realtime_check'), None)
    if restored:
        restore = next(r for r in reversed(rows) if r['kind'] == 'restore_boot_verified')
        assert not restore['hello']['realtime']
        assert len(config_diff) == 68 and not events
    else:
        assert not config_diff
        assert sum(e.status != 1 for e in events) == 2
        assert len(faults) == 2 and all(r['result']['status'] == 3 for r in faults)
        assert last['status']['state'] == 1 and last['status']['frames_ok'] == sum(e.status == 1 for e in events)
    audit = dict(approved_sha256=DEV_SHA,full_app_sha256=sha(app),config_sha256=sha(config),
                 outcome='stopped and stock restored' if restored else 'completed',
                 config_equal=not config_diff,config_after_sha256=sha(config_after),config_differences=config_diff,
                 boot_equal_pre=True,trial_row=boot[0x7f00:0x7f10].hex(),
                 app_erase_sectors=erase,data_downloads=len(download_rows),hid_tx_classes=classes,
                 bad_rt_checksums=bad_checksums,frame_events=len(events),seq_continuous=True,
                 shown_events=sum(e.status == 1 for e in events),injected_error_events=len(faults),
                 control_cycles=sum(r['kind'] == 'control_cycle' for r in rows),
                 resource_cycles=sum(r['kind'] == 'resource_cycle' for r in rows),
                 performance=[r for r in rows if r['kind'] == 'performance_summary'],
                 stream=next((r for r in rows if r['kind'] == 'stream_summary'), None),final=last,
                 samples=[r for r in rows if r['kind'] == 'sample'],faults=faults,
                 failures=[r for r in rows if r['kind'] == 'stage_failed'],
                 restoration=next((r for r in rows if r['kind'] == 'restore_boot_verified'), None))
    (run/'audit.json').write_text(json.dumps(audit,indent=2,ensure_ascii=False),encoding='utf-8')
    with (run/'samples.csv').open('w',encoding='utf-8',newline='') as f:
        names=['label','total_ms','transfer_ms','status_query_ms','decode_ms','seq','frames_ok']
        writer=csv.DictWriter(f,fieldnames=names); writer.writeheader()
        writer.writerows({k:r[k] for k in names} for r in audit['samples'])
    print(json.dumps({k:v for k,v in audit.items() if k not in
                     ('samples','performance','app_erase_sectors','config_differences','failures')},
                     indent=2,ensure_ascii=False))


if __name__ == '__main__': main()
