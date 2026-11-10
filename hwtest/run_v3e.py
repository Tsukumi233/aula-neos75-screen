"""Pinned f1fefb2e retest with 20 seconds of HID silence after every DFU exit."""
import argparse
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
import struct
import time
import traceback

from run_v3d import V3dRun, APPROVED_SHA, runner, records
from run import DEV, BACKUP, APP_SHA, FWPY, ROOT, require, sha


def audit_records(before, after):
    """Accept only appends changing keys 0F/10/11 plus old validity markers."""
    old, new = records(before), records(after)
    valid = [(i, r) for i, r in old if r[:4] == bytes.fromhex('55aaaa55')]
    require(len(valid) == 1, 'Expected one valid baseline record')
    offset, previous = valid[0][0], valid[0][1][4:]
    added = [(i, r) for i, r in new if before[i:i+32] == b'\xff'*32]
    issues, transitions = [], []
    if len(new) != len(old) + len(added): issues.append('Records erased/reorganized')
    if old[-1][0] != offset: issues.append('Baseline valid record is not the last occupied slot')
    if [i for i, _ in added] != list(range(offset+32, offset+32+32*len(added), 32)):
        issues.append('New records do not form a contiguous append sequence')
    if previous[:12] != bytes(12): issues.append('Cannot reconstruct invalidation of nonzero low keys')
    allowed = bytearray(before)
    if added: allowed[offset:offset+16] = bytes(16)
    for number, (i, record) in enumerate(added, 1):
        payload = record[4:]
        changed = [k for k, (a, b) in enumerate(zip(previous, payload)) if a != b]
        transitions.append(dict(number=number, address=hex(0x7f000+i),
                                changed_keys=[hex(k) for k in changed],
                                changes={hex(k):[previous[k],payload[k]] for k in changed},
                                page=payload[15], overlay=payload[16], mode=payload[17],
                                raw=record.hex()))
        if set(changed)-{15,16,17}: issues.append(f'Unexpected keys at {hex(0x7f000+i)}: {changed}')
        if payload[15] >= 6: issues.append('Invalid stock page')
        expected_marker = bytes.fromhex('55aaaa55') if number == len(added) else bytes(4)
        if record[:4] != expected_marker: issues.append('Unexpected appended record validity marker')
        allowed[i:i+32] = record
        previous = payload
    if bytes(allowed) != after: issues.append('Changes outside appended records and baseline invalidation')
    if sum(r[:4] == bytes.fromhex('55aaaa55') for _, r in new) != 1:
        issues.append('Expected one final valid record')
    valid_after = [r for _,r in new if r[:4] == bytes.fromhex('55aaaa55')]
    if valid_after and valid_after[0][19] >= 6: issues.append('Invalid final page')
    return dict(before_sha256=sha(before),after_sha256=sha(after),
                diff_bytes=sum(a != b for a,b in zip(before,after)),
                appended_records=len(added),key11_positions=[r['number'] for r in transitions if '0x11' in r['changed_keys']],
                transitions=transitions,issues=issues)


class V3eRun(V3dRun):
    defer_config = False

    def quiet_after_dfu(self, source):
        self.close_screen()
        started = time.perf_counter()
        self.event('dfu_silence_begin',source=source,required_s=20)
        time.sleep(20)
        self.event('dfu_silence_end',source=source,elapsed_s=time.perf_counter()-started)

    def flash_main(self, args):
        result = super().flash_main(args)
        if '--dry-run' not in args and ('--image' in args or '--restore' in args):
            self.quiet_after_dfu('verified_image_write')
        return result

    def exit_verified_dev(self):
        super().exit_verified_dev()
        self.quiet_after_dfu('verified_readonly_exit')

    def preflash(self):
        require(not self.state.get('write_started'),'Do not repeat the flash stage')
        self.hashes()
        self.command([FWPY,'-B',ROOT/'tools/flash.py','--image',DEV,'--dry-run'],'approved_dry_run')
        self.enter_dfu(); before=self.dump('pre')
        boot=(before/'bootloader_0000_8000.bin').read_bytes()
        ref=(BACKUP/'bootloader_0000_8000.bin').read_bytes()
        require(boot[:0x7f00]==ref[:0x7f00] and boot[0x7f10:]==ref[0x7f10:],'Bootloader mismatch')
        app=(before/'app_8000_7F000.bin').read_bytes()
        require(sha(app)==APP_SHA,'Expected original app')
        sp,reset=struct.unpack_from('<II',app)
        require(sp>>24==0x20 and reset&1 and 0x8000<=reset&~1<0x7f000,'Invalid app vectors')
        config=(before/'config_7F000_80000.bin').read_bytes()
        empty=sum(config[i:i+32]==b'\xff'*32 for i in range(0,4096,32))
        self.event('preflight_verified',app_sha256=sha(app),trial_row=boot[0x7f00:0x7f10].hex(),
                   sp=hex(sp),reset=hex(reset),config_sha256=sha(config),empty_config_slots=empty,
                   boot_diff_addresses=[hex(i) for i,(a,b) in enumerate(zip(boot,ref)) if a!=b])
        require(empty>=16,'Insufficient free config slots for complete append-history audit')
        self.state['write_started']=True; self.persist(); self.hashes()
        self.flash_main(['--image',str(DEV)])
        self.state.update(firmware='approved_v3_dev',build_label='v3e f1fefb2e 20s retest',flashed_sha256=APPROVED_SHA)
        self.persist(); self.event('approved_dev_written',sha256=APPROVED_SHA)
        screen=self.connect(keepalive=False)
        self.hello(screen)
        self.event('startup_verified',version=asdict(screen.version()),status=asdict(screen.status()))

    def config_check(self,label):
        if self.defer_config:
            self.event('config_check_deferred',original_label=label)
        else: super().config_check(label)

    def sessions(self):
        self.defer_config=True
        try:
            self.controls(); self.timeout(); self.keepalive()
        finally: self.defer_config=False
        self.config_check('after_controls')

    def knob_audit(self):
        self.enter_dfu(); after=self.dump('after_knob')
        before=(self.dir/'after_controls/config_7F000_80000.bin').read_bytes()
        current=(after/'config_7F000_80000.bin').read_bytes()
        result=audit_records(before,current)
        result['summaries']=[]
        for seed in (1,2,3):
            rows=[json.loads(s) for s in (self.dir/f'knob_auto_s{seed}.jsonl').read_text(encoding='utf-8').splitlines()]
            summary=next(r for r in reversed(rows) if r['kind']=='summary')
            result['summaries'].append(summary)
        (self.dir/'knob_config_audit.json').write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf-8')
        self.event('knob_config_audit',**result)
        # Only unexpected keys/addresses/formats trigger recovery. Key 11 is evidence.
        require(not result['issues'],'Unexpected config key/address/format; restore required')
        self.exit_verified_dev()
        screen=self.connect(keepalive=False); self.hello(screen)
        status=screen.status()
        require(not status.visible and not status.armed,'Unexpected active session after boot')
        self.event('final_idle_confirmed',status=asdict(status),firmware='f1fefb2e',mode='stock page; disarmed')
        self.state['final_state']='f1fefb2e dev; stock page; realtime disarmed'
        self.persist()


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('stage',choices=['preflash','sessions','knob_audit','restore'])
    ap.add_argument('--run-dir',type=runner.Path,required=True)
    args=ap.parse_args(); run=V3eRun(args.run_dir); verified=False
    try:
        # Fail missing dependencies before entering any device workflow.
        import usb.core, libusb_package
        run.hashes(restore_only=args.stage=='restore'); verified=True
        run.event('stage_start',stage=args.stage)
        if args.stage not in ('preflash','restore'):
            require(run.state.get('flashed_sha256')==APPROVED_SHA and
                    run.state.get('firmware')=='approved_v3_dev','Approved image not recorded as installed')
        getattr(run,args.stage)()
        run.state['stages'][args.stage]=dict(passed=True,time=datetime.now().astimezone().isoformat())
        run.persist(); run.event('stage_passed',stage=args.stage)
    except BaseException as exc:
        run.event('stage_failed',stage=args.stage,error=repr(exc),traceback=traceback.format_exc())
        run.state['stages'][args.stage]=dict(passed=False,error=repr(exc)); run.persist(); run.close_screen()
        if verified and args.stage!='restore' and run.state.get('write_started') and run.state.get('firmware')!='stock_restored':
            run.event('automatic_recovery',reason='Unexpected failure; restore only original backup')
            run.restore()
        return 1
    finally: run.close_screen(); run.log.close()
    return 0


if __name__=='__main__': raise SystemExit(main())
