"""Run the approved test-knob development build and constrained DFU snapshots."""
import argparse
from datetime import datetime
import hashlib
import json
import traceback

import run_v3b as runner

APPROVED_SHA='f1fefb2e6a5711fe979ee27877862d041e17ebf57846454466248239f2dfe048'
PREVIOUS=runner.ROOT/'hwtest/20261107_154846_v3c_dev'
runner.DEV_SHA=APPROVED_SHA
runner.OLD_SHA='cba6f2c716e184668371f1cdd1e2f618b1851bc1fe565629296b35d210c9cc1c'
runner.OLD_BYTES=277276
runner.OLD_DESCRIPTION='v3c cba6f2c7'
runner.BUILD_LABEL='v3d f1fefb2e test-knob dev'
runner.PREVIOUS=PREVIOUS


def records(data):
    return [(i,data[i:i+32]) for i in range(0,4096,32) if data[i:i+32]!=b'\xff'*32]


def page_changes(before,after,settles):
    old=records(before); new=records(after)
    valid=[(i,r) for i,r in old if r[:4]==bytes.fromhex('55aaaa55')]
    runner.require(len(valid)==1,'Expected exactly one valid baseline config record')
    last,payload=valid[0][0],valid[0][1][4:]
    added=[(i,r) for i,r in new if before[i:i+32]==b'\xff'*32]
    issues=[]; transitions=[]
    if len(new)!=len(old)+len(added): issues.append('Config records erased/reorganized; cannot count appends')
    if payload[:12]!=bytes(12): issues.append('Baseline low config keys are nonzero; invalidated records lose those bytes')
    allowed=bytearray(before)
    if added: allowed[last:last+16]=bytes(16)
    for i,r in added:
        diff=[k for k,(a,b) in enumerate(zip(payload,r[4:])) if a!=b]
        transitions.append(dict(address=hex(0x7f000+i),changed_keys=[hex(k) for k in diff],
                                page=r[4+15],overlay=r[4+16],mode=r[4+17]))
        if set(diff)-{15,16}: issues.append(f'Unexpected changed keys at {hex(0x7f000+i)}: {diff}')
        if r[4+15]>=6: issues.append(f'Invalid persisted stock page at {hex(0x7f000+i)}')
        if r[:4] not in (bytes(4),bytes.fromhex('55aaaa55')): issues.append('Invalid record marker')
        allowed[i:i+32]=r; payload=r[4:]
    if bytes(allowed)!=after: issues.append('Changes outside appended records and baseline invalidation')
    if len(added)>settles+2: issues.append(f'{len(added)} records exceeds settles+2={settles+2}')
    valid_after=[(i,r) for i,r in new if r[:4]==bytes.fromhex('55aaaa55')]
    if len(valid_after)!=1: issues.append('Expected exactly one final valid record')
    return dict(before_sha256=hashlib.sha256(before).hexdigest(),after_sha256=hashlib.sha256(after).hexdigest(),
                diff_bytes=sum(a!=b for a,b in zip(before,after)),appended_records=len(added),
                settles=settles,record_limit=settles+2,transitions=transitions,issues=issues)


class V3dRun(runner.V3bRun):
    def hello(self,screen):
        value=super().hello(screen)
        runner.require(value.flags==1 and value.test_knob,'Expected test-knob dev flag')
        self.event('test_knob_capability',flags=value.flags,test_knob=value.test_knob)
        return value

    def knob_baseline(self):
        self.config_check('pre_knob')

    def knob_audit(self):
        self.enter_dfu(); after=self.dump('after_knob')
        before=(self.dir/'pre_knob/config_7F000_80000.bin').read_bytes()
        current=(after/'config_7F000_80000.bin').read_bytes()
        settles=0; summaries=[]
        for seed in (1,2):
            rows=[json.loads(s) for s in (self.dir/f'knob_auto_s{seed}.jsonl').read_text(encoding='utf-8').splitlines()]
            summary=next(r for r in reversed(rows) if r['kind']=='summary')
            settles+=summary['stock_page_settles']; summaries.append(summary)
        result=page_changes(before,current,settles); result['summaries']=summaries
        (self.dir/'knob_config_audit.json').write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf-8')
        self.event('knob_config_audit',**result)
        # Verify the approved app before a zero-write DFU exit, even on an audit issue.
        self.exit_verified_dev(); self.hello(self.connect())
        runner.require(not result['issues'],'Knob config audit failed; see knob_config_audit.json')


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('stage',choices=['assets','preflash','startup','sessions','frames','performance',
                                   'knob_baseline','knob_audit','finish','restore'])
    ap.add_argument('--run-dir',type=runner.Path,required=True)
    args=ap.parse_args(); run=V3dRun(args.run_dir); verified=False
    try:
        run.hashes(restore_only=args.stage=='restore'); verified=True; run.event('stage_start',stage=args.stage)
        if args.stage not in ('assets','preflash','restore'):
            runner.require(run.state.get('flashed_sha256')==APPROVED_SHA
                           and run.state.get('firmware')=='approved_v3_dev','Approved test-knob build not installed')
        if args.stage=='assets': runner.assets(run)
        else: getattr(run,args.stage)()
        run.state['stages'][args.stage]=dict(passed=True,time=datetime.now().astimezone().isoformat())
        run.persist(); run.event('stage_passed',stage=args.stage)
    except BaseException as exc:
        run.event('stage_failed',stage=args.stage,error=repr(exc),traceback=traceback.format_exc())
        run.state['stages'][args.stage]=dict(passed=False,error=repr(exc)); run.persist(); run.close_screen()
        if verified and args.stage!='restore' and run.state.get('write_started') and run.state.get('firmware')!='stock_restored':
            run.event('automatic_recovery',reason='Stop testing and restore original backup'); run.restore()
        return 1
    finally: run.close_screen(); run.log.close()
    return 0


if __name__=='__main__': raise SystemExit(main())
