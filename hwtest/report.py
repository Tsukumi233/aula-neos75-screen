"""Summarize one recorded run without accessing any device."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'sdk/src'))
from neos75screen import protocol as p


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_dir',type=Path)
    args=parser.parse_args(); run=args.run_dir.resolve()
    keep=[]; frame_done=[]; tx_classes={}; erase=[]; downloads=0; bad_checksums=0
    for line in (run/'events.jsonl').open(encoding='utf-8'):
        row=json.loads(line); kind=row['kind']
        if kind not in ('hid_tx','hid_rx','dfu_ctrl'): keep.append(row)
        if kind=='hid_tx':
            raw=bytes.fromhex(row['report']); key=f'{raw[0]:02X}'
            if raw[0] in (0xa5,0x11): key+=f' {raw[1]:02X}'
            tx_classes[key]=tx_classes.get(key,0)+1
            assert raw[0]==0xba or raw[:2] in (b'\xa5\x3b',b'\x11\x01',b'\x11\x02')
            if p.looks_like_ba_start(raw): assert raw[3] in (0x5a,0x5b)
        elif kind=='hid_rx':
            raw=bytes.fromhex(row['report'])
            if raw[:2]==b'\xa5\x3b': bad_checksums+=not p.a5_checksum_ok(raw)
            if p.is_frame_done(raw): frame_done.append(p.parse_frame_done(raw))
        elif kind=='dfu_ctrl' and not row['incoming'] and row['request']==1:
            if row['value']==0 and row['length']==5 and row.get('hex','').startswith('41'):
                erase.append(struct.unpack('<I',bytes.fromhex(row['hex'])[1:])[0])
            if row['value']==2: downloads+=1
    dev=(ROOT/'firmware/build/neos75_rt_dev.bin').read_bytes()
    app=(run/'after_controls/app_8000_7F000.bin').read_bytes()
    preboot=(run/'pre/bootloader_0000_8000.bin').read_bytes()
    boot=(run/'after_controls/bootloader_0000_8000.bin').read_bytes()
    config=(run/'pre/config_7F000_80000.bin').read_bytes()
    after=(run/'after_controls/config_7F000_80000.bin').read_bytes()
    assert app==dev+b'\xff'*(len(app)-len(dev))
    assert config==after
    boot_diff=[i for i,(a,b) in enumerate(zip(preboot,boot)) if a!=b]
    assert boot_diff==list(range(0x7f00,0x7f10))
    assert boot[0x7f00:0x7f10]==struct.pack('<I',0x55aaaa55)+bytes(12)
    assert erase==list(range(0x8000,0x4c000,4096)) and downloads==68
    assert bad_checksums==0
    assert [x.seq for x in frame_done]==list(range(1,len(frame_done)+1))
    audit={'approved_sha256':hashlib.sha256(dev).hexdigest(),
           'physical_full_app_sha256':hashlib.sha256(app).hexdigest(),
           'full_app_equal_approved_plus_ff':True,
           'boot_diff_addresses':[hex(i) for i in boot_diff],
           'trial_flag_row_before':preboot[0x7f00:0x7f10].hex(),
           'trial_flag_row_after':boot[0x7f00:0x7f10].hex(),
           'config_equal':True,'config_sha256':hashlib.sha256(config).hexdigest(),
           'app_erase_sectors':[hex(x) for x in erase],'data_downloads':downloads,
           'hid_tx_classes':tx_classes,'frame_events':len(frame_done),
           'shown_events':sum(x.status==1 for x in frame_done),
           'injected_error_events':sum(x.status!=1 for x in frame_done),
           'bad_rt_reply_checksums':bad_checksums,'seq_continuous':True,
           'final_event':vars(frame_done[-1]),
           'performance':[r for r in keep if r['kind']=='performance_summary'],
           'stream':next(r for r in keep if r['kind']=='stream_summary'),
           'faults':[r for r in keep if r['kind']=='fault_result']}
    (run/'audit.json').write_text(json.dumps(audit,indent=2,ensure_ascii=False),encoding='utf-8')
    samples=[r for r in keep if r['kind']=='sample']
    with (run/'samples.csv').open('w',encoding='utf-8',newline='') as f:
        import csv
        writer=csv.DictWriter(f,fieldnames=['label','total_ms','transfer_ms','decode_ms','seq','frames_ok'])
        writer.writeheader()
        for row in samples: writer.writerow({k:row[k] for k in writer.fieldnames})
    print(json.dumps({k:v for k,v in audit.items() if k not in ('performance','app_erase_sectors')},ensure_ascii=False,indent=2))


if __name__=='__main__': main()
