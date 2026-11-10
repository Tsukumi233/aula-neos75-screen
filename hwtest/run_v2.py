"""Run only the explicitly approved v2 hardware build, with stock-only recovery."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path
import statistics
import struct
import subprocess
import sys
import time
import traceback

from run import Run, ROOT, DEV, BACKUP, APP_SHA, FWPY, SDKPY, BUILD, sha, require

DEV_SHA = '3655f260054814c55e286908edf44f38c2ee9345e039d85209adc5d53664049d'
V1_SHA = 'baee341bd20d76f655764ec10ec44d1a5d7b5d550af596ee3e0299bf32cc8f8f'


def distribution(values):
    values = sorted(values)
    return dict(min=min(values), median=statistics.median(values),
                p95=values[max(0, (len(values)*95+99)//100-1)], max=max(values))


class V2Run(Run):
    def __init__(self, directory):
        super().__init__(directory)
        self.log = (self.dir/'events.jsonl').open('a', encoding='utf-8', buffering=262144)
        self.metrics = []

    def event(self, kind, **fields):
        record = dict(time=datetime.now().astimezone().isoformat(),
                      perf_counter=time.perf_counter(), kind=kind, **fields)
        self.log.write(json.dumps(record, ensure_ascii=False, default=str)+'\n')
        if kind not in ('hid_tx', 'hid_rx', 'dfu_ctrl', 'frame_result', 'sample',
                        'control_cycle', 'status_timing'):
            self.log.flush()
            print(json.dumps(record, ensure_ascii=False, default=str), flush=True)

    def hashes(self, restore_only=False):
        values = {'original_app': sha((BACKUP/'app_8000_7F000.bin').read_bytes())}
        require(values['original_app'] == APP_SHA, 'Original backup SHA mismatch')
        if not restore_only:
            values['approved_dev'] = sha(DEV.read_bytes())
            require(values['approved_dev'] == DEV_SHA, 'Approved v2 SHA mismatch: STOP')
        self.event('hashes', **values)
        self.state['hashes'] = values; self.persist()

    def connect(self, timeout=60):
        from neos75screen.screen import Screen
        from neos75screen.transport import Transport, open_transport
        from neos75screen import protocol as p
        self.close_screen()
        run = self

        class AuditedTransport(Transport):
            def __init__(self, inner):
                super().__init__(); self.inner = inner
                self.first_tx = self.last_tx = self.last_write_end = None
                self.frames_received = 0

            def _write_report(self, report):
                require(report[0] == 0xba or report[:2] in
                        (b'\xa5\x3b', b'\x11\x01', b'\x11\x02'), 'Command denied')
                if p.looks_like_ba_start(report):
                    require(report[3] in (0x5a, 0x5b), 'Persistent stream forbidden')
                now = time.perf_counter()
                if report[0] == 0xba:
                    if self.first_tx is None: self.first_tx = now
                    self.last_tx = now
                self.inner.write(report)
                if report[0] == 0xba: self.last_write_end = time.perf_counter()
                run.event('hid_tx', report=report.hex())

            def receive(self, report):
                if report:
                    run.event('hid_rx', report=report.hex())
                    if p.is_frame_done(report): self.frames_received += 1
                return report

            def read(self, timeout): return self.receive(self.inner.read(timeout))
            def read_nowait(self): return self.receive(self.inner.read_nowait())

            def stream_session(self, persistent=False):
                require(not persistent, 'Persistent stream forbidden')
                stack = ExitStack(); stack.enter_context(super().stream_session(False))
                stack.enter_context(self.inner.stream_session(False)); return stack

            def close(self): self.inner.close()

        class AuditedScreen(Screen):
            def _send_stream(self, *args, **kwargs):
                self._t.first_tx = self._t.last_tx = self._t.last_write_end = None
                self.frame_start = time.perf_counter()
                return super()._send_stream(*args, **kwargs)

            def _collect(self):
                result = super()._collect()
                sample = dict(result=asdict(result), total_ms=(time.perf_counter()-self.frame_start)*1000,
                              transfer_ms=(self._t.last_write_end-self._t.first_tx)*1000,
                              transfer_start_window_ms=(self._t.last_tx-self._t.first_tx)*1000,
                              decode_ms=result.decode_ms, seq=result.seq)
                run.metrics.append(sample); run.event('frame_result', **sample)
                require(result.shown, f'Unexpected frame failure: {result}')
                return result

        deadline = time.perf_counter()+timeout
        while True:
            try:
                self.transport = AuditedTransport(open_transport())
                self.screen = AuditedScreen(self.transport)
                self.event('screen_opened', timer='perf_counter', resolution=time.get_clock_info('perf_counter').resolution)
                return self.screen
            except Exception:
                if time.perf_counter() >= deadline: raise
                time.sleep(.5)

    def hello(self, screen):
        hello = screen.hello(); self.event('hello', **asdict(hello))
        require(hello.realtime and hello.proto_version == 1 and hello.patch_version == 2
                and (hello.canvas_w, hello.canvas_h, hello.image_w, hello.image_h) == (480, 360, 480, 360)
                and hello.max_frame_bytes == 172804 and hello.buffers == 1 and hello.formats == 3,
                f'v2 HELLO mismatch: {hello}')
        return hello

    def preflash(self):
        self.hashes()
        self.command([FWPY, '-B', ROOT/'tools/flash.py', '--image', DEV, '--dry-run'], 'approved_dry_run')
        self.enter_dfu(); before = self.dump('pre')
        boot = (before/'bootloader_0000_8000.bin').read_bytes()
        ref = (BACKUP/'bootloader_0000_8000.bin').read_bytes()
        require(boot[:0x7f00] == ref[:0x7f00] and boot[0x7f10:] == ref[0x7f10:],
                'Bootloader differs outside the expected trial row')
        require(boot[0x7f00:0x7f04] == struct.pack('<I', 0x55aaaa55), 'Unexpected v1 dev flag')
        app = (before/'app_8000_7F000.bin').read_bytes()
        sp, reset = struct.unpack_from('<II', app)
        require(sp >> 24 == 0x20 and reset & 1 and 0x8000 <= reset & ~1 < 0x7f000, 'Invalid app vectors')
        old = ROOT/'firmware/build/v1/neos75_rt_dev.bin'
        if old.exists():
            old_data = old.read_bytes()
            require(sha(old_data) == V1_SHA, 'Archived v1 hash mismatch')
            require(app == old_data + b'\xff'*(len(app)-len(old_data)), 'Current app differs from expected v1')
            comparison = 'exact archived v1 plus FF; archive never flashed or rebuilt'
        else:
            require(sha(app) == '9c0d33f96a08a6727fbd9eb7e87541465c4dff0d80f78468d6a2d8b86f109692',
                    'Full app differs from previous v1 hardware dump')
            comparison = 'exact full-app hash from v1 hardware test'
        config = (before/'config_7F000_80000.bin').read_bytes()
        self.event('preflight_verified', trial_row=boot[0x7f00:0x7f10].hex(),
                   boot_diff_addresses=[hex(i) for i, (a,b) in enumerate(zip(boot,ref)) if a != b],
                   app_sha256=sha(app), comparison=comparison, sp=hex(sp), reset=hex(reset),
                   config_sha256=sha(config),
                   config_diff_original=sum(a != b for a,b in zip(config,(BACKUP/'config_7F000_80000.bin').read_bytes())))
        self.state['write_started'] = True; self.persist()
        self.hashes(); self.flash_main(['--image', str(DEV)])
        self.state['flashed_sha256'] = DEV_SHA; self.state['firmware'] = 'approved_v2_dev'; self.persist()
        self.event('approved_dev_written', sha256=DEV_SHA)

    def startup(self):
        screen = self.connect(); version = screen.version(); self.event('version', **asdict(version))
        require(version.build == BUILD, 'Build string differs')
        self.hello(screen); initial = screen.status(); self.event('startup_status', **asdict(initial))
        start = time.perf_counter()
        while time.perf_counter()-start < 60:
            self.event('idle_observation', elapsed_s=time.perf_counter()-start)
            reader = getattr(self.transport.inner, '_reader', None)
            require(reader is None or reader.error is None, 'Reader disconnected during idle')
            time.sleep(max(0, min(10, 60-(time.perf_counter()-start))))
        self.hello(screen); after = screen.status()
        require(asdict(initial) == asdict(after), 'Idle status changed')
        self.event('idle_passed', duration_s=time.perf_counter()-start, status=asdict(after),
                   evidence='same HID handle/reader; no uptime field available')

    def measured(self, screen, label, callback):
        t0 = time.perf_counter(); before = screen.status(); t1 = time.perf_counter()
        result = callback(); t2 = time.perf_counter(); after = screen.status(); t3 = time.perf_counter()
        require(result.shown and after.frames_ok == before.frames_ok+1
                and after.frames_err == before.frames_err and after.last_error == 0 and after.state == 1,
                'Frame counters/status mismatch')
        sample = dict(label=label, total_ms=(t2-t1)*1000,
                      status_query_ms=((t1-t0)+(t3-t2))*1000,
                      transfer_ms=(self.transport.last_write_end-self.transport.first_tx)*1000,
                      decode_ms=result.decode_ms, seq=result.seq, frames_ok=after.frames_ok)
        self.event('sample', **sample); return sample

    def frames(self):
        from PIL import Image
        from neos75screen.imaging import encode_jpeg
        screen = self.connect(); self.hello(screen); screen.enter()
        image = Image.open(self.dir/'assets/check_v2.png').convert('RGB')
        for sampling in ('4:4:4', '4:2:2', '4:2:0'):
            for quality in (50, 85, 95):
                jpg = encode_jpeg(image, quality, sampling)
                self.measured(screen, f'JPEG 480x360 {sampling} q{quality}', lambda:screen.show_jpeg(jpg))
        for w,h in ((320,320), (160,120), (480,2), (2,360)):
            jpg = encode_jpeg(image.resize((w,h)), 85, '4:2:0')
            self.measured(screen, f'center JPEG {w}x{h}', lambda:screen.show_jpeg(jpg))
        for w,h in ((2,2), (16,16), (64,64), (100,100), (320,240)):
            raw = bytes((i*37)&255 for i in range(w*h*2))
            self.measured(screen, f'RGB565 {w}x{h}', lambda:screen.show_rgb565(raw,width=w,height=h))
        self.bounds(screen)
        screen.leave()

    def bounds(self, screen):
        from PIL import Image
        from neos75screen.imaging import encode_jpeg
        from neos75screen import protocol as p
        for w,h in ((482,360), (480,362)):
            jpg = encode_jpeg(Image.new('RGB',(w,h),'red'), 50, '4:2:0')
            data = p.build_jpeg_stream(jpg,w,h)
            before = screen.status(); screen._drain(); screen._events.clear()
            with self.transport.stream_session():
                for packet in p.split_ba_packets(0x5a,data):
                    self.transport.write(packet.report)
                    if packet.needs_ack: screen._wait_ack()
                reply = screen._wait(p.is_frame_done,2)
                require(reply is not None,'Missing bounds frame-done')
                result = p.parse_frame_done(reply)
            after = screen.status()
            require(result.status == 3 and after.frames_err == before.frames_err+1
                    and after.frames_ok == before.frames_ok, 'Bounds failure mismatch')
            self.event('fault_result',label=f'oversize_{w}x{h}',result=asdict(result),guard_relaxations=[])
            self.measured(screen, f'recovery after {w}x{h}',
                          lambda:screen.show_rgb565(b'\x00\xf8'*4,width=2,height=2))

    def performance(self):
        screen = self.connect(); self.hello(screen); screen.enter(); summaries = []
        for target in (10,20,30,45):
            jpg = (self.dir/f'assets/perf_{target}k.jpg').read_bytes()
            before = screen.status(); offset = len(self.metrics); start = time.perf_counter()
            for _ in range(30): screen.show_jpeg(jpg)
            elapsed = time.perf_counter()-start
            after = screen.status(); samples = self.metrics[offset:]
            require(len(samples) == 30 and after.frames_ok-before.frames_ok == 30
                    and after.frames_err == before.frames_err and after.last_error == 0, 'Batch counters mismatch')
            summary = dict(target_kib=target,actual_bytes=len(jpg),frames=30,elapsed_s=elapsed,fps=30/elapsed,
                           per_frame_status_queries=0,logging='buffered HID application log')
            for field in ('total_ms','transfer_ms','transfer_start_window_ms','decode_ms'):
                summary[field] = distribution([s[field] for s in samples])
            query_times = []
            for _ in range(30):
                start = time.perf_counter(); status = screen.status()
                query_times.append((time.perf_counter()-start)*1000)
                require(status == after, 'Counters changed during query-only benchmark')
            summary['separate_status_query_ms'] = distribution(query_times)
            summary['estimated_two_queries_per_frame_ms'] = 2*statistics.median(query_times)
            summaries.append(summary); self.event('performance_summary', **summary)
        jpg = (self.dir/'assets/perf_20k.jpg').read_bytes()
        before = screen.status(); start = time.perf_counter()
        def continuous():
            while time.perf_counter()-start < 60: yield jpg
        stats = screen.stream(continuous()); elapsed = time.perf_counter()-start; after = screen.status()
        require(stats.sent == stats.shown and stats.timeouts == stats.dropped == 0
                and after.frames_ok-before.frames_ok == stats.shown and after.frames_err == before.frames_err,
                'Unstable continuous stream')
        self.event('stream_summary',duration_s=elapsed,stats=asdict(stats),fps=stats.shown/elapsed,status=asdict(after))
        self.state['performance'] = summaries; self.persist(); screen.leave()

    def resources(self):
        screen = self.connect(); self.hello(screen); screen.leave(); baseline = screen.status()
        require(baseline.state == 0,'Not on original page'); self.hello(screen)
        for i in range(5):
            screen.enter(); require(screen.status().state == 1,'Resource ENTER failed')
            screen.leave(); time.sleep(2)
            self.hello(screen); current = screen.status(); screen._drain()
            require(current.state == 0 and current.frames_ok == baseline.frames_ok
                    and current.frames_err == baseline.frames_err and self.transport.frames_received == 0,
                    'Resource cycle changed counters or emitted events')
            reader = getattr(self.transport.inner,'_reader',None)
            require(reader is None or reader.error is None,'Resource cycle reader disconnected')
            self.event('resource_cycle',index=i+1,wait_s=2,status=asdict(current),
                       visual='Original icons/images require human confirmation')
        self.state['resources_status'] = asdict(current); self.persist()

    def final(self):
        from PIL import Image
        from neos75screen.imaging import encode_jpeg
        screen = self.connect(); self.hello(screen); screen.enter()
        jpg = encode_jpeg(Image.open(self.dir/'assets/check_v2.png'),95,'4:4:4')
        self.measured(screen,'final v2 visual check',lambda:screen.show_jpeg(jpg))
        self.event('final_realtime_check',status=asdict(screen.status()),image='assets/check_v2.png',jpeg_bytes=len(jpg))
        self.state['final_state'] = 'approved v2 dev; realtime active; full-canvas visual check retained'
        self.persist()

    def confirm_restore(self):
        require(self.state.get('firmware') == 'stock_restored', 'Stock recovery not recorded')
        screen = self.connect(); version = screen.version(); hello = screen.hello()
        require(version.build == BUILD and not hello.realtime, 'Stock final confirmation failed')
        self.event('stock_final_confirmed',version=asdict(version),hello=asdict(hello),new_frames_sent=0)


def assets(run):
    from PIL import Image, ImageDraw, ImageFont
    from neos75screen.imaging import encode_jpeg
    import numpy as np
    out = run.dir/'assets'; out.mkdir(exist_ok=True)
    image = Image.new('RGB',(480,360),(205,205,205)); draw = ImageDraw.Draw(image)
    for x in range(0,480,40): draw.line((x,0,x,359),fill=(65,65,65))
    for y in range(0,360,40): draw.line((0,y,479,y),fill=(65,65,65))
    font = ImageFont.truetype(r'C:\Windows\Fonts\arialbd.ttf',16)
    for box,color,label,ink in (((0,0,39,39),'red','TL','white'),
                              ((440,0,479,39),(0,255,0),'TR','black'),
                              ((0,320,39,359),'blue','BL','white'),
                              ((440,320,479,359),'yellow','BR','black')):
        draw.rectangle(box,fill=color)
        draw.text((box[0]+20,box[1]+20),label,font=font,anchor='mm',fill=ink)
    draw.polygon([(240,85),(195,145),(225,145),(225,205),(255,205),(255,145),(285,145)],fill='black')
    draw.text((240,250),'NEOS75 RT v2 480x360',font=font,anchor='mm',fill='black')
    draw.rectangle((0,0,479,359),outline='white',width=2)
    image.save(out/'check_v2.png')
    rng = np.random.default_rng(752); noise = rng.integers(0,256,(360,480,3),dtype=np.uint8)
    y,x = np.mgrid[:360,:480]; base = np.stack((x*255//479,y*255//359,(x+y)*255//838),axis=2)
    candidates = []
    for blend in (.1,.2,.35,.5,.75,1):
        pic = Image.fromarray((base*(1-blend)+noise*blend).astype('uint8'))
        for quality in range(15,96,2):
            data = encode_jpeg(pic,quality,'4:2:0'); candidates.append((len(data),data,blend,quality))
    chosen = []
    for target in (10,20,30,45):
        size,data,blend,quality = min(candidates,key=lambda c:abs(c[0]-target*1024))
        require(abs(size-target*1024)/(target*1024)<.1,'Cannot prepare JPEG target')
        (out/f'perf_{target}k.jpg').write_bytes(data)
        chosen.append(dict(target_kib=target,bytes=size,blend=blend,quality=quality,sha256=sha(data)))
    (out/'manifest.json').write_text(json.dumps(chosen,indent=2),encoding='utf-8')
    run.event('assets_prepared',images=chosen,check_png_sha256=sha((out/'check_v2.png').read_bytes()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage',choices=['assets','preflash','startup','controls','frames','performance','resources','final','restore','confirm_restore'])
    parser.add_argument('--run-dir',type=Path,required=True)
    args = parser.parse_args(); run = V2Run(args.run_dir)
    hashes_verified = False
    try:
        run.hashes(restore_only=args.stage in ('restore','confirm_restore'))
        run.event('stage_start',stage=args.stage)
        hashes_verified = True
        if args.stage in ('startup','controls','frames','performance','resources','final'):
            require(run.state.get('firmware') == 'approved_v2_dev', 'v2 is not currently recorded as installed')
        if args.stage == 'assets': assets(run)
        else: getattr(run,args.stage)()
        run.state['stages'][args.stage] = dict(passed=True,time=datetime.now().astimezone().isoformat())
        run.persist(); run.event('stage_passed',stage=args.stage)
    except BaseException as exc:
        run.event('stage_failed',stage=args.stage,error=repr(exc),traceback=traceback.format_exc())
        run.state['stages'][args.stage] = dict(passed=False,error=repr(exc)); run.persist(); run.close_screen()
        if (hashes_verified and args.stage != 'restore' and run.state.get('write_started')
                and run.state.get('firmware') != 'stock_restored'):
            run.event('automatic_recovery',reason='Stop testing; restore only the original backup')
            result = subprocess.run([str(FWPY),'-B',str(Path(__file__).resolve()),'restore','--run-dir',str(run.dir)],cwd=ROOT)
            run.event('automatic_recovery_finished',exit_code=result.returncode)
        return 1
    finally:
        run.close_screen(); run.log.close()
    return 0


if __name__ == '__main__': raise SystemExit(main())
