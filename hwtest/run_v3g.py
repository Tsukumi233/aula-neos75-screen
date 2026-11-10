"""Host pages dev build e63f0f73 over the 74484752 device; never compiles.

Stages: preflash (dry-run, read-only pre dump, device must hold 74484752 as dumped after the v3f keyboard
stage, write, 20 s HID silence, HELLO flags 7), pages (set 3 pages, dev-injected knob steps through the pages,
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

APPROVED_SHA = 'e63f0f73ec1bcaeaa1e9a19fb86d188e7faccb7389768d08eaad756898b90183'
OLD_SHA = '74484752ffe24519675c24a73e119ad27f25b58e65bcadbd7a8abca53968c1e3'
OLD_BYTES = 277348
OLD_DUMP = ROOT / 'hwtest/20261107_224014_v3f_dev/after_keyboard/app_8000_7F000.bin'
runner.DEV_SHA = APPROVED_SHA
runner.BUILD_LABEL = 'v3g e63f0f73 host pages dev'


class V3gRun(run_v3f.V3fRun):
    def hello(self, screen):
        value = V3Run.hello(self, screen)
        require(value.flags == 7 and value.test_knob and value.telemetry and value.host_pages,
                f'Expected dev flags 7: {value}')
        self.event('capabilities', flags=value.flags, host_pages=value.host_pages)
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
        require(app == old, 'Device app is not the expected 74484752 image')
        config = (before / 'config_7F000_80000.bin').read_bytes()
        self.event('preflight_verified', app_sha256=sha(app), comparison='exact v3f 74484752 plus FF',
                   trial_row=boot[0x7f00:0x7f10].hex(), config_sha256=sha(config))
        self.state['write_started'] = True; self.persist(); self.hashes()
        self.flash_main(['--image', str(DEV)])           # V3eRun adds the 20 s silence
        self.state.update(firmware='approved_v3_dev', build_label=runner.BUILD_LABEL, flashed_sha256=APPROVED_SHA)
        self.persist(); self.event('approved_dev_written', sha256=APPROVED_SHA)
        screen = self.connect(keepalive=False)
        self.hello(screen)
        self.event('startup_verified', version=asdict(screen.version()), status=asdict(screen.status()))

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
        self.state['final_state'] = 'e63f0f73 dev; stock page; realtime disarmed'; self.persist()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('stage', choices=['preflash', 'pages', 'restore'])
    ap.add_argument('--run-dir', type=runner.Path, required=True)
    args = ap.parse_args(); run = V3gRun(args.run_dir); verified = False
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
