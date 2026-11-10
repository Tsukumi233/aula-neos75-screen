"""Hardware regression for the one approved c7d2a4c1 development image."""
import argparse
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
import shutil
import struct
import time
import traceback

from run import ROOT, DEV, BACKUP, APP_SHA, FWPY, sha, require
from run_v2 import distribution
from run_v3 import V3Run

DEV_SHA='c7d2a4c10438299f495a3f14989953961a6c9ee59dd6cbf7cec79d92a6bbb406'
OLD_SHA='e8b668629aff05c655dbb763c3cc439ec7e1281f4aa10bf9a8a1a95cfee65e0e'
OLD_BYTES=277160
OLD_DESCRIPTION='first v3'
BUILD_LABEL='v3b c7d2a4c1'
PREVIOUS=ROOT/'hwtest/20261107_135104_v3_dev'


class V3bRun(V3Run):
    def hashes(self,restore_only=False):
        values={'original_app':sha((BACKUP/'app_8000_7F000.bin').read_bytes())}
        require(values['original_app']==APP_SHA,'Original backup SHA mismatch')
        if not restore_only:
            values['approved_dev']=sha(DEV.read_bytes())
            require(values['approved_dev']==DEV_SHA,'Approved v3b SHA mismatch: STOP; never rebuild')
        self.event('hashes',**values); self.state['hashes']=values; self.persist()

    def preflash(self):
        require(not self.state.get('write_started'),'Do not repeat the flash stage')
        self.hashes()
        self.command([FWPY,'-B',ROOT/'tools/flash.py','--image',DEV,'--dry-run'],'approved_dry_run')
        self.enter_dfu(); before=self.dump('pre')
        boot=(before/'bootloader_0000_8000.bin').read_bytes()
        ref=(BACKUP/'bootloader_0000_8000.bin').read_bytes()
        require(boot[:0x7f00]==ref[:0x7f00] and boot[0x7f10:]==ref[0x7f10:],'Bootloader mismatch')
        app=(before/'app_8000_7F000.bin').read_bytes(); sp,reset=struct.unpack_from('<II',app)
        require(sp>>24==0x20 and reset&1 and 0x8000<=reset&~1<0x7f000,'Invalid app vectors')
        old=(PREVIOUS/'after_controls/app_8000_7F000.bin').read_bytes()
        require(sha(old[:OLD_BYTES])==OLD_SHA and old[OLD_BYTES:]==b'\xff'*(len(old)-OLD_BYTES),'Old reference invalid')
        require(app==old,f'Device app is not the expected {OLD_DESCRIPTION} image')
        config=(before/'config_7F000_80000.bin').read_bytes()
        self.event('preflight_verified',app_sha256=sha(app),comparison=f'exact {OLD_DESCRIPTION} plus FF',
                   trial_row=boot[0x7f00:0x7f10].hex(),sp=hex(sp),reset=hex(reset),
                   boot_diff_addresses=[hex(i) for i,(a,b) in enumerate(zip(boot,ref)) if a!=b],
                   config_sha256=sha(config),
                   config_diff_original=sum(a!=b for a,b in zip(config,(BACKUP/'config_7F000_80000.bin').read_bytes())))
        self.state['write_started']=True; self.persist(); self.hashes()
        self.flash_main(['--image',str(DEV)])
        self.state.update(firmware='approved_v3_dev',build_label=BUILD_LABEL,flashed_sha256=DEV_SHA)
        self.persist(); self.event('approved_dev_written',sha256=DEV_SHA)

    def sessions(self):
        self.controls()
        self.timeout()
        self.keepalive()
        self.config_check('after_sessions')

    def frames(self):
        from PIL import Image
        from neos75screen.imaging import encode_jpeg
        from neos75screen import protocol as p
        screen=self.connect(); self.hello(screen); screen.enter()
        image=Image.open(self.dir/'assets/check_v3.png').convert('RGB')
        for sampling in ('4:4:4','4:2:2','4:2:0'):
            jpg=encode_jpeg(image,85,sampling)
            self.measured(screen,f'JPEG 480x360 {sampling} q85',lambda:screen.show_jpeg(jpg))
        for w,h in ((320,320),(160,120)):
            jpg=encode_jpeg(image.resize((w,h)),85,'4:2:0')
            self.measured(screen,f'center JPEG {w}x{h}',lambda:screen.show_jpeg(jpg))
        self.measured(screen,'RGB565 100x100 multiblock',
                      lambda:screen.show_rgb565(bytes((i*37)&255 for i in range(20000)),width=100,height=100))
        data=p.build_jpeg_stream(encode_jpeg(Image.new('RGB',(482,360),'red'),50,'4:2:0'),482,360)
        before=screen.status(); screen._drain(); screen._events.clear()
        with self.transport.stream_session():
            for packet in p.split_ba_packets(0x5a,data):
                self.transport.write(packet.report)
                if packet.needs_ack: require(screen._wait_ack(),'Bounds stream aborted early')
            reply=screen._wait(p.is_frame_done,2); require(reply is not None,'Missing bounds event')
            result=p.parse_frame_done(reply)
        after=screen.status()
        require(result.status==3 and after.frames_err==before.frames_err+1 and after.frames_ok==before.frames_ok,
                'Bounds status/counters mismatch')
        self.event('fault_result',label='oversize_482x360',result=asdict(result),guard_relaxations=[])
        self.measured(screen,'recovery after oversize',lambda:screen.show_rgb565(b'\x00\xf8'*4,width=2,height=2))
        screen.leave()

    def performance(self):
        import statistics
        screen=self.connect(); self.hello(screen); screen.enter(); summaries=[]
        for target in (20,45):
            jpg=(self.dir/f'assets/perf_{target}k.jpg').read_bytes()
            before=screen.status(); offset=len(self.metrics); started=time.perf_counter()
            for _ in range(30): screen.show_jpeg(jpg)
            elapsed=time.perf_counter()-started; after=screen.status(); samples=self.metrics[offset:]
            require(len(samples)==30 and after.frames_ok-before.frames_ok==30
                    and after.frames_err==before.frames_err and after.last_error==0,'Batch counters mismatch')
            summary=dict(target_kib=target,actual_bytes=len(jpg),frames=30,elapsed_s=elapsed,fps=30/elapsed,
                         per_frame_status_queries=0)
            for key in ('total_ms','transfer_ms','transfer_start_window_ms','decode_ms'):
                summary[key]=distribution([s[key] for s in samples])
            queries=[]
            for _ in range(30):
                started=time.perf_counter(); status=screen.status(); queries.append((time.perf_counter()-started)*1000)
                require(status==after,'Counters changed during query-only benchmark')
            summary['separate_status_query_ms']=distribution(queries)
            summary['estimated_two_queries_per_frame_ms']=2*statistics.median(queries)
            summaries.append(summary); self.event('performance_summary',**summary)
        jpg=(self.dir/'assets/perf_20k.jpg').read_bytes(); before=screen.status(); started=time.perf_counter()
        def continuous():
            while time.perf_counter()-started<30: yield jpg
        stats=screen.stream(continuous()); elapsed=time.perf_counter()-started; after=screen.status()
        require(stats.sent==stats.shown and stats.dropped==stats.timeouts==stats.hidden==0
                and after.frames_ok-before.frames_ok==stats.shown and after.frames_err==before.frames_err,
                'Continuous stream unstable')
        self.event('stream_summary',duration_s=elapsed,stats=asdict(stats),fps=stats.shown/elapsed,status=asdict(after))
        self.state['performance']=summaries; self.persist()

    def finish(self):
        screen=self.connect(keepalive=False); self.hello(screen); screen.enter()
        jpg=(self.dir/'assets/perf_20k.jpg').read_bytes()
        self.measured(screen,f'final {BUILD_LABEL} frame',lambda:screen.show_jpeg(jpg))
        status=screen.status(); require(status.visible and status.armed,'Final session not visible')
        self.event('final_realtime_check',status=asdict(status),image='assets/perf_20k.jpg',expected_timeout_after_close_s=10)
        self.close_screen(); self.event('natural_close',leave=False,no_host_traffic_s=12)
        time.sleep(12)
        screen=self.connect(keepalive=False); self.hello(screen); status=screen.status()
        require(not status.visible and not status.armed and status.state==0,'Natural timeout failed')
        self.event('final_idle_confirmed',status=asdict(status),firmware=BUILD_LABEL,mode='stock page; disarmed')
        self.state['final_state']=f'{BUILD_LABEL} dev; stock page visible, realtime disarmed after natural timeout'
        self.persist()


def assets(run):
    out=run.dir/'assets'; out.mkdir(exist_ok=True)
    for name in ('check_v3.png','perf_20k.jpg','perf_45k.jpg'):
        shutil.copyfile(PREVIOUS/'assets'/name,out/name)
    run.event('assets_prepared',source=str(PREVIOUS/'assets'),
              hashes={f.name:sha(f.read_bytes()) for f in out.iterdir()})


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('stage',choices=['assets','preflash','startup','sessions','frames','performance','finish','restore'])
    ap.add_argument('--run-dir',type=Path,required=True)
    args=ap.parse_args(); run=V3bRun(args.run_dir); verified=False
    try:
        run.hashes(restore_only=args.stage=='restore'); verified=True; run.event('stage_start',stage=args.stage)
        if args.stage not in ('assets','preflash','restore'):
            require(run.state.get('flashed_sha256')==DEV_SHA and run.state.get('firmware')=='approved_v3_dev',
                    'Approved v3b is not recorded as installed')
        if args.stage=='assets': assets(run)
        else: getattr(run,args.stage)()
        run.state['stages'][args.stage]=dict(passed=True,time=datetime.now().astimezone().isoformat())
        run.persist(); run.event('stage_passed',stage=args.stage)
    except BaseException as exc:
        run.event('stage_failed',stage=args.stage,error=repr(exc),traceback=traceback.format_exc())
        run.state['stages'][args.stage]=dict(passed=False,error=repr(exc)); run.persist(); run.close_screen()
        if verified and args.stage!='restore' and run.state.get('write_started') and run.state.get('firmware')!='stock_restored':
            run.event('automatic_recovery',reason='Stop regression; restore only original backup'); run.restore()
        return 1
    finally: run.close_screen(); run.log.close()
    return 0


if __name__=='__main__': raise SystemExit(main())
