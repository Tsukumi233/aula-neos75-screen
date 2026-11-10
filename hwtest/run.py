"""Authorized Neos75 hardware test stages. Never builds or selects a new firmware image."""
from __future__ import annotations
import argparse
from contextlib import ExitStack
from dataclasses import asdict
from datetime import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import statistics
import struct
import subprocess
import sys
import time
import traceback

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
sys.path.insert(0,str(ROOT/'sdk/src'))
DEV=ROOT/'firmware/build/neos75_rt_dev.bin'
APP_SHA='66679ff2cb06ab5a5e47673f679e14e68bf2082fb6401b3808ca58d7f2e1a40c'
DEV_SHA='baee341bd20d76f655764ec10ec44d1a5d7b5d550af596ee3e0299bf32cc8f8f'
# The stock-firmware backup of this keyboard: the folder in NEOS75_BACKUP, else ~/Tools/aula_neos75_backup.
BACKUP=Path(os.environ.get('NEOS75_BACKUP') or Path.home() / 'Tools' / 'aula_neos75_backup')
FWPY=ROOT/'firmware/.venv/Scripts/python.exe'
SDKPY=ROOT/'sdk/.venv/Scripts/python.exe'
BUILD='V1.0.3 Sep  6 2026 23:18:16'


def sha(data): return hashlib.sha256(data).hexdigest()
def require(ok,message):
    if not ok: raise RuntimeError(message)


class Run:
    def __init__(self,directory):
        self.dir=Path(directory).resolve()
        require(self.dir.is_relative_to(ROOT/'hwtest'),'Run directory must be inside hwtest')
        self.dir.mkdir(parents=True,exist_ok=True)
        self.state_path=self.dir/'state.json'
        self.state=json.loads(self.state_path.read_text()) if self.state_path.exists() else {'stages':{}}
        self.screen=None; self.transport=None

    def event(self,kind,**fields):
        record={'time':datetime.now().astimezone().isoformat(),'monotonic':time.monotonic(),'kind':kind,**fields}
        with (self.dir/'events.jsonl').open('a',encoding='utf-8') as f:
            f.write(json.dumps(record,ensure_ascii=False,default=str)+'\n')
        if kind not in ('hid_tx','hid_rx','dfu_ctrl'):
            print(json.dumps(record,ensure_ascii=False,default=str),flush=True)

    def persist(self):
        self.state_path.write_text(json.dumps(self.state,indent=2,ensure_ascii=False,default=str),encoding='utf-8')

    def hashes(self,restore_only=False):
        values={'original_app':sha((BACKUP/'app_8000_7F000.bin').read_bytes())}
        if not restore_only:
            values['approved_dev']=sha(DEV.read_bytes())
            require(values['approved_dev']==DEV_SHA,'Approved dev hash mismatch: stop')
        require(values['original_app']==APP_SHA,'Original backup hash mismatch: stop')
        self.event('hashes',**values); self.state['hashes']=values; self.persist()

    def command(self,args,name,timeout=90):
        result=subprocess.run([str(a) for a in args],cwd=ROOT,capture_output=True,text=True,timeout=timeout)
        (self.dir/(name+'.log')).write_text(result.stdout+'\n'+result.stderr,encoding='utf-8')
        self.event('command',name=name,args=[str(a) for a in args],exit_code=result.returncode,
                   output=result.stdout[-1500:],stderr=result.stderr[-1500:])
        require(result.returncode==0,f'{name} failed; see its log')
        return result.stdout

    def dfu_backend(self):
        import libusb_package
        import usb.backend.libusb1
        backend=usb.backend.libusb1.get_backend(find_library=libusb_package.find_library)
        require(backend is not None,'No libusb backend')
        return backend

    def dfu_devices(self):
        import usb.core
        return list(usb.core.find(find_all=True,idVendor=0x5542,idProduct=0x1000,backend=self.dfu_backend()))

    def enter_dfu(self):
        self.close_screen()
        if self.dfu_devices(): return
        self.command([SDKPY,'-B',ROOT/'tools/enter_dfu.py','--enter'],'enter_dfu_'+str(time.time_ns()),10)
        deadline=time.monotonic()+60; next_log=0
        while time.monotonic()<deadline:
            devices=self.dfu_devices()
            if devices:
                require(len(devices)==1,'Multiple target DFU devices')
                self.event('dfu_present',vid='5542',pid='1000'); return
            if time.monotonic()>=next_log:
                self.event('waiting_dfu',remaining_s=round(deadline-time.monotonic(),1)); next_log=time.monotonic()+5
            time.sleep(.25)
        self.state['needs_replug']=True; self.persist()
        raise RuntimeError('DFU absent after 60 seconds; stop and request a keyboard unplug/replug')

    def flash_main(self,args):
        import flash
        run=self; original=flash.Session
        class LoggedSession(original):
            def ctrl(self,incoming,request,value,payload,descriptor=False):
                address=self.gate.address
                result=super().ctrl(incoming,request,value,payload,descriptor)
                data=result if incoming else payload
                fields={'incoming':incoming,'request':request,'value':value,'address':address,
                        'descriptor':descriptor,'length':len(data),'sha256':sha(bytes(data))}
                if len(data)<=18: fields['hex']=bytes(data).hex()
                run.event('dfu_ctrl',**fields)
                if incoming and request==2 and address==0x7f00 and value==2:
                    run.event('trial_flag_read',hex=bytes(data).hex(),value=f'{int.from_bytes(data,"little"):08X}')
                return result
        flash.Session=LoggedSession
        try: return flash.main(args)
        finally: flash.Session=original

    def dump(self,label):
        target=self.dir/label
        require(not target.exists(),f'Dump already exists: {target}')
        self.flash_main(['--dump',str(target)])
        self.event('dump',directory=str(target),hashes=json.loads((target/'sha256.json').read_text()))
        return target

    def exit_verified_dev(self):
        import flash
        self.hashes(); plan=flash.Plan(DEV.read_bytes()); devices=self.dfu_devices()
        require(len(devices)==1,'Expected one DFU screen')
        session=flash.Session(self.dfu_backend(),devices[0],plan)
        try:
            session.validate(); session.idle()
            session.gate.verify(session.read(flash.BASE,len(plan.padded)))
            flag=session.read(0x7f00,4)
            require(flag==struct.pack('<I',0x55aaaa55),'Dev trial flag unexpectedly cleared')
            self.event('readonly_exit_verified',sha256=plan.sha256,trial_flag=flag.hex(),erase_commands=0)
            session.idle(); session.ctrl(False,1,0,b'')
            delay,state=session.status()
            require(state in (6,7,8),'Unexpected manifestation state')
            if state==6:
                time.sleep(delay/1000); require(session.status()[1] in (7,8),'Manifestation incomplete')
        finally: session.close()

    def connect(self,timeout=60):
        from neos75screen.screen import Screen
        from neos75screen.transport import Transport,open_transport
        run=self
        class AuditedTransport(Transport):
            def __init__(self,inner):
                super().__init__(); self.inner=inner; self.first_tx=None; self.last_tx=None
                self.frames_received=0
            def _write_report(self,report):
                # A second, task-specific allow-list excludes all stock flash/config commands.
                require(report[0]==0xba or report[:2] in (b'\xa5\x3b',b'\x11\x01',b'\x11\x02'),
                        'Hardware test command is outside its narrow allow-list')
                if report[0]==0xba:
                    if self.first_tx is None: self.first_tx=time.monotonic()
                    self.last_tx=time.monotonic()
                self.inner.write(report)
                run.event('hid_tx',report=report.hex())
            def receive(self,report):
                from neos75screen import protocol as p
                if report:
                    run.event('hid_rx',report=report.hex())
                    if p.is_frame_done(report): self.frames_received+=1
                return report
            def read(self,timeout): return self.receive(self.inner.read(timeout))
            def read_nowait(self): return self.receive(self.inner.read_nowait())
            def stream_session(self,persistent=False):
                require(not persistent,'Persistent wallpaper is forbidden')
                stack=ExitStack(); stack.enter_context(super().stream_session(False))
                stack.enter_context(self.inner.stream_session(False)); return stack
            def close(self): self.inner.close()
        class AuditedScreen(Screen):
            def _send_stream(self,*args,**kwargs):
                self._t.first_tx=self._t.last_tx=None
                self._frame_start=time.monotonic()
                return super()._send_stream(*args,**kwargs)
            def _collect(self):
                result=super()._collect()
                run.event('frame_result',result=asdict(result),total_ms=(time.monotonic()-self._frame_start)*1000,
                          transfer_ms=(self._t.last_tx-self._t.first_tx)*1000)
                require(result.shown,f'Frame failed: {result}')
                return result
        deadline=time.monotonic()+timeout
        while True:
            try:
                self.transport=AuditedTransport(open_transport()); self.screen=AuditedScreen(self.transport)
                self.event('screen_opened'); return self.screen
            except Exception:
                if time.monotonic()>=deadline: raise
                time.sleep(.5)

    def close_screen(self):
        if self.screen:
            try: self.screen.close(leave=False)
            except Exception as exc: self.event('screen_close_error',error=repr(exc))
            finally: self.screen=None; self.transport=None

    def hello(self,screen):
        hello=screen.hello(); self.event('hello',**asdict(hello))
        require(hello.realtime and hello.proto_version==1 and hello.patch_version==1
                and (hello.canvas_w,hello.canvas_h,hello.image_w,hello.image_h)==(480,360,320,320)
                and hello.max_frame_bytes==172804 and hello.buffers==1 and hello.formats==3,
                f'HELLO mismatch: {hello}')
        return hello

    def preflash(self):
        self.hashes()
        self.command([FWPY,'-B',ROOT/'tools/flash.py','--image',DEV,'--dry-run'],'approved_dry_run')
        self.enter_dfu(); before=self.dump('pre')
        for filename in ('bootloader_0000_8000.bin','app_8000_7F000.bin'):
            require((before/filename).read_bytes()==(BACKUP/filename).read_bytes(),f'Preflight mismatch: {filename}')
        flag=(before/'bootloader_0000_8000.bin').read_bytes()[0x7f00:0x7f04]
        require(flag==b'\xff'*4,'Preflight trial flag is not FFFFFFFF')
        config=(before/'config_7F000_80000.bin').read_bytes(); ref=(BACKUP/'config_7F000_80000.bin').read_bytes()
        self.event('preflight_verified',trial_flag=flag.hex(),config_equal=config==ref,
                   config_diff_bytes=sum(a!=b for a,b in zip(config,ref)))
        self.state['write_started']=True; self.persist()
        self.hashes(); self.flash_main(['--image',str(DEV)])
        self.state['flashed_sha256']=DEV_SHA; self.state['firmware']='approved_dev'; self.persist()
        self.event('approved_dev_written',sha256=DEV_SHA)

    def startup(self):
        screen=self.connect(); version=screen.version(); self.event('version',**asdict(version))
        require(version.build==BUILD,'Build string differs')
        self.hello(screen); initial=screen.status(); self.event('startup_status',**asdict(initial))
        start=time.monotonic()
        while time.monotonic()-start<60:
            self.event('idle_observation',elapsed_s=round(time.monotonic()-start,2))
            reader=getattr(self.transport.inner,'_reader',None)
            require(reader is None or reader.error is None,'HID reader disconnected during idle')
            time.sleep(min(10,60-(time.monotonic()-start)))
        self.hello(screen); after=screen.status()
        require((after.frames_ok,after.frames_err)==(initial.frames_ok,initial.frames_err),'Idle counters changed')
        self.event('idle_passed',duration_s=time.monotonic()-start,status=asdict(after),
                   evidence='same open HID handle and reader, no observed disconnect; protocol has no uptime field')

    def controls(self):
        screen=self.connect(); self.hello(screen); baseline=screen.status()
        for i in range(20):
            screen.enter(); active=screen.status(); require(active.state==1,'ENTER state mismatch')
            screen.leave(); off=screen.status(); require(off.state==0,'LEAVE state mismatch')
            require((off.frames_ok,off.frames_err)==(baseline.frames_ok,baseline.frames_err),'Unexpected control counters')
            require(self.transport.frames_received==0,'Unexpected frame event during control loop')
            self.event('control_cycle',index=i+1,active=asdict(active),off=asdict(off))
        self.enter_dfu(); after=self.dump('after_controls')
        before=(self.dir/'pre/config_7F000_80000.bin').read_bytes()
        current=(after/'config_7F000_80000.bin').read_bytes()
        require(current==before,'ENTER/LEAVE changed config flash')
        flag=(after/'bootloader_0000_8000.bin').read_bytes()[0x7f00:0x7f04]
        require(flag==struct.pack('<I',0x55aaaa55),'Dev flag cleared')
        self.event('controls_flash_verified',config_sha256=sha(current),diff_bytes=0,trial_flag=flag.hex())
        self.exit_verified_dev(); screen=self.connect(); self.hello(screen)

    def measured(self,screen,label,callback):
        before=screen.status(); started=time.monotonic(); result=callback(); duration=time.monotonic()-started
        after=screen.status()
        require(result.shown and after.frames_ok==before.frames_ok+1 and after.frames_err==before.frames_err
                and after.last_error==0 and after.state==1,'Frame result/counter mismatch')
        sample={'label':label,'total_ms':duration*1000,'transfer_ms':(self.transport.last_tx-self.transport.first_tx)*1000,
                'decode_ms':result.decode_ms,'seq':result.seq,'frames_ok':after.frames_ok}
        self.event('sample',**sample); return sample

    def frames(self):
        from PIL import Image
        from neos75screen.imaging import encode_jpeg
        screen=self.connect(); self.hello(screen)
        for w,h in ((2,2),(16,16),(64,64),(100,100)):
            raw=bytes((i*37)&255 for i in range(w*h*2))
            self.measured(screen,f'RGB565 {w}x{h}',lambda:screen.show_rgb565(raw,width=w,height=h))
        for size in (16,320):
            image=Image.open(self.dir/'assets/check.png').convert('RGB').resize((size,size))
            for sampling in ('4:4:4','4:2:2','4:2:0'):
                for quality in (50,85,95):
                    jpg=encode_jpeg(image,quality,sampling)
                    self.measured(screen,f'JPEG {size}x{size} {sampling} q{quality}',lambda:screen.show_jpeg(jpg))
        screen.leave()

    def performance(self):
        screen=self.connect(); self.hello(screen); summaries=[]
        for target in (8,15,25,40):
            jpg=(self.dir/f'assets/perf_{target}k.jpg').read_bytes(); samples=[]
            start=time.monotonic()
            for i in range(30): samples.append(self.measured(screen,f'{target}KiB frame {i+1}',lambda:screen.show_jpeg(jpg)))
            elapsed=time.monotonic()-start
            summary={'target_kib':target,'actual_bytes':len(jpg),'frames':30,'elapsed_s':elapsed,'fps':30/elapsed}
            for field in ('total_ms','transfer_ms','decode_ms'):
                values=sorted(s[field] for s in samples)
                summary[field]={'min':min(values),'median':statistics.median(values),'p95':values[28],'max':max(values)}
            summaries.append(summary); self.event('performance_summary',**summary)
        jpg=(self.dir/'assets/perf_15k.jpg').read_bytes(); before=screen.status(); start=time.monotonic()
        def continuous():
            while time.monotonic()-start<60: yield jpg
        stats=screen.stream(continuous()); elapsed=time.monotonic()-start; after=screen.status()
        require(stats.sent==stats.shown and stats.timeouts==stats.dropped==0
                and after.frames_ok-before.frames_ok==stats.shown and after.frames_err==before.frames_err,
                'Unstable continuous stream')
        self.event('stream_summary',duration_s=elapsed,stats=asdict(stats),fps=stats.shown/elapsed,status=asdict(after))
        self.state['performance']=summaries; self.persist(); screen.leave()

    def faults(self):
        from neos75screen import protocol as p
        screen=self.connect(); self.hello(screen); screen.enter()
        good=b'\x00\xf8'*4
        tests=[('bad_magic',0x5b,b'XX'+struct.pack('>HHH',2,2,1)+b'\0'+good,{3}),
               ('oversize',0x5b,b'WQ'+struct.pack('>HHH',322,2,1)+b'\0'+good,{3}),
               ('odd_width',0x5b,b'WQ'+struct.pack('>HHH',3,2,1)+b'\0'+good,{3})]
        jpg=(self.dir/'assets/perf_8k.jpg').read_bytes()[:-2]
        tests.append(('truncated_jpeg',0x5a,p.build_jpeg_stream(jpg,320,320),{2,3}))
        tests.append(('not_entered',0x5b,p.build_rgb565_stream(good,2,2),{5}))
        for label,kind,data,expected in tests:
            if label=='not_entered': screen.leave()
            screen._drain(); screen._events.clear()
            # SDK protocol packetizer and both original transport guards remain active.
            with self.transport.stream_session():
                packets=p.split_ba_packets(kind,data)
                self.transport.write(packets[0].report)
                if label=='not_entered':
                    reply=screen._wait(p.is_frame_done,2); require(reply is not None,'No not-entered event')
                    result=p.parse_frame_done(reply)
                else:
                    screen._wait_ack()
                    for packet in packets[1:]:
                        self.transport.write(packet.report)
                        if packet.needs_ack: screen._wait_ack()
                    reply=screen._wait(p.is_frame_done,2); require(reply is not None,'No failure event')
                    result=p.parse_frame_done(reply)
            require(result.status in expected,f'Unexpected fault status: {label}: {result}')
            self.event('fault_result',label=label,result=asdict(result),guard_relaxations=[])
            self.measured(screen,'recovery after '+label,lambda:screen.show_rgb565(good,width=2,height=2))
        screen.leave()

    def final(self):
        from neos75screen.imaging import encode_jpeg
        from PIL import Image
        screen=self.connect(); self.hello(screen); screen.leave()
        require(screen.status().state==0,'Final LEAVE state mismatch'); self.hello(screen)
        jpg=encode_jpeg(Image.open(self.dir/'assets/check.png'),95,'4:4:4')
        self.measured(screen,'final visual check',lambda:screen.show_jpeg(jpg))
        self.event('final_realtime_check',status=asdict(screen.status()),image='assets/check.png',jpeg_bytes=len(jpg))
        self.state['final_state']='approved dev; realtime mode active with visual check image'; self.persist()

    def restore(self):
        # Recovery must not depend on the dev artifact still being present or unchanged.
        self.hashes(restore_only=True); self.enter_dfu()
        self.command([FWPY,'-B',ROOT/'tools/flash.py','--restore','--dry-run'],'restore_dry_run')
        self.flash_main(['--restore'])
        self.state['firmware']='stock_restored'; self.persist()
        screen=self.connect(); version=screen.version(); hello=screen.hello()
        self.event('restore_boot_verified',version=asdict(version),hello=asdict(hello))
        require(version.build==BUILD and not hello.realtime,'Original app boot verification failed')
        self.state['final_state']='stock restored and responding in application mode'; self.persist()

    def confirm(self):
        screen=self.connect(); self.hello(screen); status=screen.status()
        audit=json.loads((self.dir/'audit.json').read_text())
        require(status.state==1 and status.frames_ok==audit['shown_events']
                and status.frames_err==audit['injected_error_events'] and status.last_error==0,
                'Final counters or realtime state changed unexpectedly')
        self.event('final_state_confirmed',status=asdict(status),new_frames_sent=0)


def check_image(run):
    from PIL import Image,ImageDraw,ImageFont
    out=run.dir/'assets'; out.mkdir(exist_ok=True)
    image=Image.new('RGB',(320,320)); draw=ImageDraw.Draw(image)
    for box,color in (((0,0,159,159),'red'),((160,0,319,159),(0,255,0)),
                      ((0,160,159,319),'blue'),((160,160,319,319),'white')): draw.rectangle(box,fill=color)
    draw.polygon([(160,85),(115,140),(145,140),(145,205),(175,205),(175,140),(205,140)],fill='black')
    font=ImageFont.truetype(r'C:\Windows\Fonts\arialbd.ttf',16)
    text='NEOS75 RT OK'; box=draw.textbbox((0,0),text,font=font); width=box[2]-box[0]
    draw.text((240-width//2,270),text,font=font,fill='black')
    image.save(out/'check.png')
    run.event('visual_asset',path='assets/check.png',sha256=sha((out/'check.png').read_bytes()))


def assets(run):
    from PIL import Image
    from neos75screen.imaging import encode_jpeg
    import numpy as np
    check_image(run)
    out=run.dir/'assets'
    rng=np.random.default_rng(7501); noise=rng.integers(0,256,(320,320,3),dtype=np.uint8)
    y,x=np.mgrid[:320,:320]; base=np.stack((x*255//319,y*255//319,(x+y)*255//638),axis=2)
    candidates=[]
    for blend in (.15,.3,.5,.75,1):
        pic=Image.fromarray((base*(1-blend)+noise*blend).astype('uint8'))
        for quality in range(15,96,2):
            data=encode_jpeg(pic,quality,'4:2:0'); candidates.append((len(data),data,blend,quality))
    chosen=[]
    for target in (8,15,25,40):
        size,data,blend,quality=min(candidates,key=lambda c:abs(c[0]-target*1024))
        require(abs(size-target*1024)/(target*1024)<.1,'Cannot create target size JPEG')
        (out/f'perf_{target}k.jpg').write_bytes(data)
        chosen.append({'target_kib':target,'bytes':size,'blend':blend,'quality':quality,'sha256':sha(data)})
    (out/'manifest.json').write_text(json.dumps(chosen,indent=2),encoding='utf-8')
    run.event('assets_prepared',images=chosen)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage',choices=['assets','preflash','startup','controls','frames','performance','faults','final','confirm','restore'])
    parser.add_argument('--run-dir',type=Path,required=True)
    args=parser.parse_args(); run=Run(args.run_dir)
    run.hashes(restore_only=args.stage=='restore'); run.event('stage_start',stage=args.stage)
    try:
        if args.stage=='assets': assets(run)
        else: getattr(run,args.stage)()
        run.state['stages'][args.stage]={'passed':True,'time':datetime.now().astimezone().isoformat()}
        run.persist(); run.event('stage_passed',stage=args.stage)
    except BaseException as exc:
        run.event('stage_failed',stage=args.stage,error=repr(exc),traceback=traceback.format_exc())
        run.state['stages'][args.stage]={'passed':False,'error':repr(exc)}; run.persist(); run.close_screen()
        if args.stage!='restore' and run.state.get('write_started'):
            run.event('automatic_recovery',reason='Stop all testing after failure and restore the approved original backup')
            result=subprocess.run([str(FWPY),'-B',str(Path(__file__).resolve()),'restore','--run-dir',str(run.dir)],cwd=ROOT)
            run.event('automatic_recovery_finished',exit_code=result.returncode)
        return 1
    finally: run.close_screen()
    return 0


if __name__=='__main__': raise SystemExit(main())
