"""Execute stages for the single approved v3 image; never compile firmware."""
from __future__ import annotations
import argparse
from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path
import struct
import sys
import time
import traceback

from run import ROOT, DEV, BACKUP, APP_SHA, FWPY, BUILD, sha, require
from run_v2 import V2Run, assets as assets_v2
from guarded_screen import GuardedTransport, TimedScreen

DEV_SHA='e8b668629aff05c655dbb763c3cc439ec7e1281f4aa10bf9a8a1a95cfee65e0e'


class V3Run(V2Run):
    def hashes(self, restore_only=False):
        values={'original_app':sha((BACKUP/'app_8000_7F000.bin').read_bytes())}
        require(values['original_app']==APP_SHA,'Original backup SHA mismatch')
        if not restore_only:
            values['approved_dev']=sha(DEV.read_bytes())
            require(values['approved_dev']==DEV_SHA,'Approved v3 SHA mismatch: STOP; do not rebuild')
        self.event('hashes',**values); self.state['hashes']=values; self.persist()

    def connect(self, timeout=60, keepalive=True):
        self.close_screen(); deadline=time.perf_counter()+timeout
        def sample(value):
            self.metrics.append(value); self.event('frame_result',**value)
            require(value['result']['status']==1,f'Frame failed: {value}')
        while True:
            try:
                self.transport=GuardedTransport(self.event)
                self.screen=TimedScreen(self.transport,sample,keepalive=keepalive)
                self.event('screen_opened',keepalive=keepalive,timer='perf_counter',
                           resolution=time.get_clock_info('perf_counter').resolution)
                return self.screen
            except Exception:
                if self.transport: self.transport.close(); self.transport=None
                if time.perf_counter()>=deadline: raise
                time.sleep(.5)

    def hello(self, screen):
        value=screen.hello(); self.event('hello',**asdict(value))
        require(value.realtime and value.proto_version==2 and value.patch_version==3
                and (value.canvas_w,value.canvas_h,value.image_w,value.image_h)==(480,360,480,360)
                and value.max_frame_bytes==172804 and value.host_timeout_ms==10000
                and value.formats==3 and value.buffers==1,f'v3 HELLO mismatch: {value}')
        return value

    def preflash(self):
        require(not self.state.get('write_started'),'Do not repeat the flash stage')
        self.hashes()
        self.command([FWPY,'-B',ROOT/'tools/flash.py','--image',DEV,'--dry-run'],'approved_dry_run')
        self.enter_dfu(); before=self.dump('pre')
        boot=(before/'bootloader_0000_8000.bin').read_bytes()
        ref=(BACKUP/'bootloader_0000_8000.bin').read_bytes()
        require(boot[:0x7f00]==ref[:0x7f00] and boot[0x7f10:]==ref[0x7f10:],'Bootloader mismatch')
        app=(before/'app_8000_7F000.bin').read_bytes()
        require(sha(app)==APP_SHA,'Current application is not the original backup')
        config=(before/'config_7F000_80000.bin').read_bytes()
        self.event('preflight_verified',app_sha256=sha(app),trial_row=boot[0x7f00:0x7f10].hex(),
                   boot_diff_addresses=[hex(i) for i,(a,b) in enumerate(zip(boot,ref)) if a!=b],
                   config_sha256=sha(config),
                   config_diff_original=sum(a!=b for a,b in zip(config,(BACKUP/'config_7F000_80000.bin').read_bytes())))
        self.state['write_started']=True; self.persist()
        self.hashes(); self.flash_main(['--image',str(DEV)])
        self.state['firmware']='approved_v3_dev'; self.state['flashed_sha256']=DEV_SHA; self.persist()
        self.event('approved_dev_written',sha256=DEV_SHA)

    def startup(self):
        super().startup()
        status=self.screen.status()
        require(not status.visible and not status.armed,'Startup session unexpectedly armed')

    def config_check(self,label):
        self.enter_dfu(); after=self.dump(label)
        before=(self.dir/'pre/config_7F000_80000.bin').read_bytes()
        current=(after/'config_7F000_80000.bin').read_bytes()
        changes=[dict(address=hex(0x7f000+i),before=a,after=b)
                 for i,(a,b) in enumerate(zip(before,current)) if a!=b]
        boot=(after/'bootloader_0000_8000.bin').read_bytes()
        preboot=(self.dir/'pre/bootloader_0000_8000.bin').read_bytes()
        require(boot[:0x7f00]==preboot[:0x7f00] and boot[0x7f10:]==preboot[0x7f10:],'Bootloader changed')
        flag=boot[0x7f00:0x7f04]
        require(flag==struct.pack('<I',0x55aaaa55),'Dev flag cleared')
        app=(after/'app_8000_7F000.bin').read_bytes(); approved=DEV.read_bytes()
        require(app==approved+b'\xff'*(len(app)-len(approved)),'Approved app changed')
        self.event('config_comparison',label=label,diff_bytes=len(changes),differences=changes,
                   before_sha256=sha(before),after_sha256=sha(current),trial_flag=flag.hex())
        require(not changes,'Config page changed: stop and restore stock')
        self.exit_verified_dev(); self.hello(self.connect())

    def controls(self):
        from neos75screen.protocol import VisibilityEvent
        screen=self.connect(); self.hello(screen); baseline=screen.status()
        list(screen.events(timeout=0))
        for i in range(20):
            screen.enter(); active=screen.status(); entered=list(screen.events(timeout=0))
            require(active.state==1 and active.visible and active.armed,'ENTER status mismatch')
            require(entered==[VisibilityEvent(True,2)],f'ENTER events: {entered}')
            screen.leave(); off=screen.status(); left=list(screen.events(timeout=0))
            require(off.state==0 and not off.visible and not off.armed,'LEAVE status mismatch')
            require(left==[VisibilityEvent(False,2)],f'LEAVE events: {left}')
            require((off.frames_ok,off.frames_err)==(baseline.frames_ok,baseline.frames_err),'Control counters changed')
            require(self.transport.frames_received==0,'Unexpected frame event')
            self.event('control_cycle',index=i+1,active=asdict(active),off=asdict(off),
                       entered=[asdict(e) for e in entered],left=[asdict(e) for e in left])
        self.config_check('after_controls')

    def timeout(self):
        from neos75screen.protocol import VisibilityEvent
        screen=self.connect(keepalive=False); self.hello(screen); screen.enter()
        # The ENTER reply precedes its asynchronous visibility event. STATUS is
        # queued after that event, so its reply is an explicit receive barrier.
        entered_status=screen.status()
        require(entered_status.visible and entered_status.armed,'ENTER before timeout failed')
        entered=list(screen.events(timeout=0))
        require(entered==[VisibilityEvent(True,2)],f'Initial ENTER events: {entered}')
        count=self.transport.tx_count; started=time.perf_counter()
        self.event('timeout_silence_begin',seconds=12,tx_count=count)
        while time.perf_counter()-started<12: time.sleep(min(1,12-(time.perf_counter()-started)))
        require(self.transport.tx_count==count,'Host transmitted during timeout silence')
        ev=list(screen.events(timeout=0)); self.event('timeout_silence_end',events=[asdict(e) for e in ev],
                                                     seconds=time.perf_counter()-started,tx_count=self.transport.tx_count)
        require(ev==[VisibilityEvent(False,3)],f'Timeout event mismatch: {ev}')
        status=screen.status(); require(not status.visible and not status.armed,'Timeout did not disarm')
        screen.enter(); require(screen.status().visible,'Re-enter after timeout failed')
        self.measured(screen,'re-enter after timeout',lambda:screen.show_rgb565(b'\x00\xf8'*4,width=2,height=2))
        screen.leave(); self.config_check('after_timeout')

    def keepalive(self):
        screen=self.connect(); self.hello(screen); screen.enter(); count=self.transport.tx_count
        started=time.perf_counter(); self.event('keepalive_begin',seconds=30)
        for i in range(6):
            time.sleep(5); self.event('keepalive_observation',elapsed_s=time.perf_counter()-started)
        status=screen.status()
        require(status.visible and status.armed,'Default keepalive failed')
        self.event('keepalive_passed',duration_s=time.perf_counter()-started,status=asdict(status),
                   transmitted_reports=self.transport.tx_count-count)
        screen.leave()

    def frames(self):
        from PIL import Image
        from neos75screen.imaging import encode_jpeg
        screen=self.connect(); self.hello(screen); screen.enter()
        image=Image.open(self.dir/'assets/check_v3.png').convert('RGB')
        for sampling in ('4:4:4','4:2:2','4:2:0'):
            for quality in (50,85,95):
                jpg=encode_jpeg(image,quality,sampling)
                self.measured(screen,f'JPEG 480x360 {sampling} q{quality}',lambda:screen.show_jpeg(jpg))
        for w,h in ((320,320),(160,120),(480,2),(2,360)):
            jpg=encode_jpeg(image.resize((w,h)),85,'4:2:0')
            self.measured(screen,f'center JPEG {w}x{h}',lambda:screen.show_jpeg(jpg))
        for w,h in ((2,2),(16,16),(64,64),(100,100),(320,240)):
            raw=bytes((i*37)&255 for i in range(w*h*2))
            self.measured(screen,f'RGB565 {w}x{h}',lambda:screen.show_rgb565(raw,width=w,height=h))
        self.bounds(screen); screen.leave()

    def final(self):
        from PIL import Image
        from neos75screen.imaging import encode_jpeg
        screen=self.connect(); self.hello(screen); screen.enter()
        jpg=encode_jpeg(Image.open(self.dir/'assets/check_v3.png'),95,'4:4:4')
        self.measured(screen,'final v3 visual check',lambda:screen.show_jpeg(jpg))
        self.event('final_realtime_check',status=asdict(screen.status()),image='assets/check_v3.png',
                   jpeg_bytes=len(jpg),expected_timeout_after_close_s=10)
        self.state['final_state']='v3 dev; realtime check displayed, natural timeout to stock page after closing'
        self.persist()


def assets(run):
    from PIL import Image, ImageDraw, ImageFont
    assets_v2(run)
    image=Image.new('RGB',(480,360),(205,205,205)); draw=ImageDraw.Draw(image)
    for x in range(0,480,40): draw.line((x,0,x,359),fill=(65,65,65))
    for y in range(0,360,40): draw.line((0,y,479,y),fill=(65,65,65))
    font=ImageFont.truetype(r'C:\Windows\Fonts\arialbd.ttf',16)
    for box,color,label,ink in (((0,0,39,39),'red','TL','white'),((440,0,479,39),(0,255,0),'TR','black'),
                              ((0,320,39,359),'blue','BL','white'),((440,320,479,359),'yellow','BR','black')):
        draw.rectangle(box,fill=color); draw.text((box[0]+20,box[1]+20),label,font=font,anchor='mm',fill=ink)
    draw.polygon([(240,85),(195,145),(225,145),(225,205),(255,205),(255,145),(285,145)],fill='black')
    draw.text((240,250),'NEOS75 RT v3 480x360',font=font,anchor='mm',fill='black')
    draw.rectangle((0,0,479,359),outline='white',width=2)
    image.save(run.dir/'assets/check_v3.png')
    run.event('visual_asset',path='assets/check_v3.png',sha256=sha((run.dir/'assets/check_v3.png').read_bytes()))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage',choices=['assets','preflash','startup','controls','timeout','keepalive',
                                       'frames','performance','resources','final','restore','confirm_restore'])
    parser.add_argument('--run-dir',type=Path,required=True)
    args=parser.parse_args(); run=V3Run(args.run_dir); verified=False
    try:
        run.hashes(restore_only=args.stage in ('restore','confirm_restore')); verified=True
        run.event('stage_start',stage=args.stage)
        if args.stage not in ('assets','preflash','restore','confirm_restore'):
            require(run.state.get('firmware')=='approved_v3_dev','Approved v3 not recorded as installed')
        if args.stage=='assets': assets(run)
        else: getattr(run,args.stage)()
        run.state['stages'][args.stage]=dict(passed=True,time=datetime.now().astimezone().isoformat())
        run.persist(); run.event('stage_passed',stage=args.stage)
    except BaseException as exc:
        run.event('stage_failed',stage=args.stage,error=repr(exc),traceback=traceback.format_exc())
        run.state['stages'][args.stage]=dict(passed=False,error=repr(exc)); run.persist(); run.close_screen()
        if verified and args.stage!='restore' and run.state.get('write_started') and run.state.get('firmware')!='stock_restored':
            run.event('automatic_recovery',reason='Stop testing; restore only the original backup')
            try: run.restore()
            except BaseException as failure: run.event('automatic_recovery_failed',error=repr(failure)); raise
        return 1
    finally: run.close_screen(); run.log.close()
    return 0


if __name__=='__main__': raise SystemExit(main())
