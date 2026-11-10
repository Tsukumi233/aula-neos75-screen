"""Smoothness dev build 04f09a6d (5 ms tick, pipelined starts, page cache) over 1d4c029f; never compiles.

Stages: preflash (dry-run, read-only pre dump, device must hold e63f0f73 as dumped after the v3g pages
stage, write, 20 s HID silence, HELLO flags 15), regions (full frame, then region frames: refused before a
canvas, shown after, measured rate), pages (set 3 pages, dev-injected knob steps through the pages,
inside the slot only (a step onto a stock page starts the stock 2 s page save, a config write the check would
treat as a fault; leaving and entering is covered by the emulator and the hands-on check), config page
unchanged, pages reset to 1). Recovery on unexpected
failure restores only the original backup, as in earlier runs.
"""
import argparse
from dataclasses import asdict
from datetime import datetime
import struct
import time
import traceback

import run_v3f
import run_v3b as runner
from run_v3 import V3Run
from run import BACKUP, DEV, FWPY, ROOT, require, sha

APPROVED_SHA = '04f09a6d771572ea11abf3b5bb21cf1d7662c98296ef764d46889fab01b36302'
OLD_SHA = '1d4c029f67c4abc7af5bc107dd985c269733f75dba32e3eaaf5f6de8dde43847'
OLD_BYTES = 278004
OLD_DUMP = ROOT / 'hwtest/20261108_011222_v3h_dev/after_pages/app_8000_7F000.bin'
runner.DEV_SHA = APPROVED_SHA
runner.BUILD_LABEL = 'v3i 04f09a6d smoothness dev'


class V3iRun(run_v3f.V3fRun):
    def hello(self, screen):
        value = V3Run.hello(self, screen)
        require(value.flags == 31 and value.test_knob and value.telemetry and value.host_pages and value.region
                and value.page_cache, f'Expected dev flags 31: {value}')
        self.event('capabilities', flags=value.flags, page_cache=value.page_cache)
        return value

    def preflash(self):
        require(not self.state.get('write_started'), 'Do not repeat the flash stage')
        self.hashes()
        self.command([FWPY, '-B', ROOT / 'tools/flash.py', '--image', DEV, '--dry-run'], 'approved_dry_run')
        self.enter_dfu(); before = self.dump('pre')
        boot = (before / 'bootloader_0000_8000.bin').read_bytes()
        ref = (BACKUP / 'bootloader_0000_8000.bin').read_bytes()
        require(boot[:0x7f00] == ref[:0x7f00] and boot[0x7f10:] == ref[0x7f10:], 'Bootloader mismatch')
        app = (before / 'app_8000_7F000.bin').read_bytes(); sp, reset = struct.unpack_from('<II', app)
        require(sp >> 24 == 0x20 and reset & 1 and 0x8000 <= reset & ~1 < 0x7f000, 'Invalid app vectors')
        old = OLD_DUMP.read_bytes()
        require(sha(old[:OLD_BYTES]) == OLD_SHA and old[OLD_BYTES:] == b'\xff' * (len(old) - OLD_BYTES),
                'Old reference invalid')
        stock = sha(app) == runner.APP_SHA   # the original app, e.g. after an automatic recovery
        require(app == old or stock, 'Device app is neither the expected 1d4c029f image nor the original')
        config = (before / 'config_7F000_80000.bin').read_bytes()
        self.event('preflight_verified', app_sha256=sha(app),
                   comparison='original backup' if stock else 'exact v3h 1d4c029f plus FF',
                   trial_row=boot[0x7f00:0x7f10].hex(), config_sha256=sha(config))
        self.state['write_started'] = True; self.persist(); self.hashes()
        self.flash_main(['--image', str(DEV)])           # V3eRun adds the 20 s silence
        self.state.update(firmware='approved_v3_dev', build_label=runner.BUILD_LABEL, flashed_sha256=APPROVED_SHA)
        self.persist(); self.event('approved_dev_written', sha256=APPROVED_SHA)
        screen = self.connect(keepalive=False)
        self.hello(screen)
        self.event('startup_verified', version=asdict(screen.version()), status=asdict(screen.status()))

    def regions(self):
        from PIL import Image, ImageDraw
        from neos75screen import FrameRejected
        from neos75screen.imaging import encode_jpeg
        screen = self.connect(keepalive=False)
        self.hello(screen)
        screen.enter()
        patch = encode_jpeg(Image.new('RGB', (320, 32), 'white'), 85)
        # The frame monitor requires "shown"; this one refusal (06, no canvas yet) is expected.
        strict = screen.sample
        def refusal(value):
            self.event('frame_result', expected_refusal=True, **value)
            require(value['result']['status'] == 6, f'Expected status 6: {value}')
        screen.sample = refusal
        try:
            screen.show_region(patch, 144, 144)
            require(False, 'Region accepted before a full frame')
        except FrameRejected as exc:
            require(exc.status == 6, f'Expected status 6, got {exc.status}')
        finally:
            screen.sample = strict
        require(screen.show(Image.new('RGB', (480, 360), (20, 20, 24))).shown, 'Full frame not shown')
        sizes, started = [], time.perf_counter()
        for i in range(60):   # a moving level bar, as the Hi-Fi player sends it
            img = Image.new('RGB', (320, 32), (20, 20, 24))
            ImageDraw.Draw(img).rectangle((0, 8, 10 + (i * 37) % 300, 24), fill=(240, 240, 240))
            jpeg = encode_jpeg(img, 88); sizes.append(len(jpeg))
            require(screen.show_region(jpeg, 144, 144).shown, f'Region {i} not shown')
        elapsed = time.perf_counter() - started
        status = screen.status()
        self.event('region_rate', frames=60, seconds=elapsed, fps=60 / elapsed, mean_bytes=sum(sizes) / len(sizes),
                   status=asdict(status))
        require(status.frames_err == 1, f'Only the refused region should count as an error: {status}')
        screen.leave()
        self.config_check('after_regions')

    def smooth(self):
        from PIL import Image, ImageDraw
        from neos75screen.imaging import encode_jpeg
        screen = self.connect(keepalive=False)
        self.hello(screen)
        screen.enter()
        screen.ack_window = 2
        require(screen.set_pages(3) == 0, 'Expected page 0 after host ENTER')
        slot = screen.cache_slot_bytes
        require(slot == ((0x7E900 - 345600 - 81920) // 3) & ~3, f'Unexpected cache slot {slot}')
        require(screen.show(Image.new('RGB', (480, 360), (20, 20, 24))).shown, 'Full frame not shown')
        rates = {}
        for name, size, draw in (('level', (320, 32), lambda d, i: d.rectangle((0, 8, 10 + (i * 37) % 300, 24), fill=(240, 240, 240))),
                                 ('spectrum', (320, 64), lambda d, i: [d.rectangle((b * 13, 64 - (b * 7 + i * 11) % 60 - 4, b * 13 + 9, 63), fill=(236, 236, 236)) for b in range(24)])):
            frames = []
            for i in range(60):
                img = Image.new('RGB', size, (20, 20, 24)); draw(ImageDraw.Draw(img), i)
                frames.append(encode_jpeg(img, 88, optimize=True))
            t = time.perf_counter()
            for j in frames:
                require(screen.show_region(j, 144, 144).shown, f'{name} region not shown')
            rates[name] = dict(fps=len(frames) / (time.perf_counter() - t), bytes=sum(map(len, frames)) / len(frames))
        self.event('smooth_rates', **rates)
        colours = {1: (180, 40, 40), 2: (40, 160, 60)}
        for page, colour in colours.items():
            picture = encode_jpeg(Image.new('RGB', (480, 360), colour), 70, optimize=True, max_bytes=slot)
            require(screen.cache_page(page, picture).shown, f'Cache upload {page} not shown')
        steps = []
        for direction in (1, 1, -1, -1):
            before = screen.status()
            screen.inject_knob(direction)
            time.sleep(0.4)
            after = screen.status()
            steps.append(dict(direction=direction, page=after.page, visible=after.visible,
                              cache_decode_ms=after.last_decode_ms, ok=after.frames_ok - before.frames_ok,
                              errors=after.frames_err - before.frames_err))
        self.event('cache_steps', steps=steps)
        require([s['page'] for s in steps] == [1, 2, 1, 0] and all(s['visible'] for s in steps), f'Page walk: {steps}')
        require(all(s['errors'] == 0 for s in steps), f'Errors during the walk: {steps}')
        require(screen.set_pages(1) == 0, 'Reset to one page failed')
        screen.leave()
        self.config_check('after_smooth')

    def pages(self):
        screen = self.connect(keepalive=False)
        self.hello(screen)
        screen.enter()
        require(screen.set_pages(3) == 0, 'Expected page 0 after host ENTER')
        seen = []
        for direction in (1, 1, -1, -1):
            screen.status()  # host activity, as the runner's frames would be
            screen.inject_knob(direction)
            time.sleep(0.5)
            status = screen.status()
            seen.append(('P', status.page) if status.visible else 'stock')
            self.event('page_step', direction=direction, status=asdict(status))
        require(seen == [('P', 1), ('P', 2), ('P', 1), ('P', 0)], f'Unexpected page walk: {seen}')
        require(screen.set_pages(1) == 0, 'Reset to one page failed')
        screen.leave()
        self.config_check('after_pages')
        self.state['final_state'] = '04f09a6d dev; stock page; realtime disarmed'; self.persist()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('stage', choices=['preflash', 'smooth', 'regions', 'pages', 'restore'])
    ap.add_argument('--run-dir', type=runner.Path, required=True)
    args = ap.parse_args(); run = V3iRun(args.run_dir); verified = False
    try:
        import usb.core, libusb_package  # noqa: F401 - fail before any device workflow
        run.hashes(restore_only=args.stage == 'restore'); verified = True
        run.event('stage_start', stage=args.stage)
        if args.stage not in ('preflash', 'restore'):
            require(run.state.get('flashed_sha256') == APPROVED_SHA and
                    run.state.get('firmware') == 'approved_v3_dev', 'Approved image not recorded as installed')
        getattr(run, args.stage)()
        run.state['stages'][args.stage] = dict(passed=True, time=datetime.now().astimezone().isoformat())
        run.persist(); run.event('stage_passed', stage=args.stage)
    except BaseException as exc:
        run.event('stage_failed', stage=args.stage, error=repr(exc), traceback=traceback.format_exc())
        run.state['stages'][args.stage] = dict(passed=False, error=repr(exc)); run.persist(); run.close_screen()
        if verified and args.stage != 'restore' and run.state.get('write_started') \
                and run.state.get('firmware') != 'stock_restored':
            run.event('automatic_recovery', reason='Unexpected failure; restore only original backup')
            run.restore()
        return 1
    finally:
        run.close_screen(); run.log.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
