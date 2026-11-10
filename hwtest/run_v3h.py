"""Region frames dev build 1d4c029f over the e63f0f73 device; never compiles.

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

APPROVED_SHA = '1d4c029f67c4abc7af5bc107dd985c269733f75dba32e3eaaf5f6de8dde43847'
OLD_SHA = 'e63f0f73ec1bcaeaa1e9a19fb86d188e7faccb7389768d08eaad756898b90183'
OLD_BYTES = 277552
OLD_DUMP = ROOT / 'hwtest/20261107_235213_v3g_dev/after_pages/app_8000_7F000.bin'
runner.DEV_SHA = APPROVED_SHA
runner.BUILD_LABEL = 'v3h 1d4c029f region frames dev'


class V3hRun(run_v3f.V3fRun):
    def hello(self, screen):
        value = V3Run.hello(self, screen)
        require(value.flags == 15 and value.test_knob and value.telemetry and value.host_pages and value.region,
                f'Expected dev flags 15: {value}')
        self.event('capabilities', flags=value.flags, host_pages=value.host_pages, region=value.region)
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
        # e63f0f73, or the original app after the automatic recovery of 2026-11-08 00:56 (run 005615)
        stock = sha(app) == runner.APP_SHA
        require(app == old or stock, 'Device app is neither the expected e63f0f73 image nor the original')
        config = (before / 'config_7F000_80000.bin').read_bytes()
        self.event('preflight_verified', app_sha256=sha(app),
                   comparison='original backup' if stock else 'exact v3g e63f0f73 plus FF',
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
        self.state['final_state'] = '1d4c029f dev; stock page; realtime disarmed'; self.persist()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('stage', choices=['preflash', 'regions', 'pages', 'restore'])
    ap.add_argument('--run-dir', type=runner.Path, required=True)
    args = ap.parse_args(); run = V3hRun(args.run_dir); verified = False
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
