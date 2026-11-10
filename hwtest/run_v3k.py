"""Release build 2710bdd4 (the 1cb11444 dev features, the knob step from the PC kept, trial flag cleared after
60 s uptime and 1000 GUI callbacks) over the dev 1cb11444 device or the original app; never compiles.
(First run, 2e4806b0 with a 6000-callback threshold: flag still set at 120 s, recovered to stock.)

Stages:
- preflash: dry-run, read-only pre dump, device must hold dev 1cb11444 (or the original app), write, 20 s HID
  silence, HELLO flags 63.
- pages: set 3 pages, PC knob steps through them inside the slot (no stock page save), pages reset to 1.
- confirm: wait until the app has run 120 s since its boot (the release clears the trial flag after 60 s of
  uptime and 1000 GUI callbacks), then a read-only dump: bootloader otherwise unchanged, app exactly the
  release image, config page unchanged (faults); trial flag cleared or not (recorded, not a fault: kept, the
  release runs as the dev build did); read-only DFU exit, 20 s silence, HELLO again.
Recovery on unexpected failure restores only the original backup, as in earlier runs.
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
from run import BACKUP, FWPY, ROOT, require, sha

RELEASE = ROOT / 'firmware/build/neos75_rt_release.bin'
APPROVED_SHA = '2710bdd4c309d2a28f9f80023c8ffe3301336656e379b418259a069de60caabd'
OLD_SHA = '1cb11444d4599fa0111c570f155c227c6a8f67675db26a5a2eb828e24873cc09'
OLD_BYTES = 278664
TRIAL = struct.pack('<I', 0x55AAAA55)
BOOT_WAIT_S = 150
runner.BUILD_LABEL = 'v3k 2710bdd4 release'


class V3kRun(run_v3f.V3fRun):
    def hashes(self, restore_only=False):
        values = {'original_app': sha((BACKUP / 'app_8000_7F000.bin').read_bytes())}
        require(values['original_app'] == runner.APP_SHA, 'Original backup SHA mismatch')
        if not restore_only:
            values['approved_release'] = sha(RELEASE.read_bytes())
            require(values['approved_release'] == APPROVED_SHA, 'Release SHA mismatch: STOP; never rebuild')
        self.event('hashes', **values); self.state['hashes'] = values; self.persist()

    def hello(self, screen):
        value = V3Run.hello(self, screen)
        require(value.flags == 63 and value.test_knob and value.telemetry and value.host_pages and value.region
                and value.page_cache and value.page_tag, f'Expected flags 63: {value}')
        self.event('capabilities', flags=value.flags)
        return value

    def booted(self):
        self.state['boot_time'] = time.time(); self.persist()

    def preflash(self):
        require(not self.state.get('write_started'), 'Do not repeat the flash stage')
        self.hashes()
        self.command([FWPY, '-B', ROOT / 'tools/flash.py', '--image', RELEASE, '--dry-run'], 'approved_dry_run')
        self.enter_dfu(); before = self.dump('pre')
        boot = (before / 'bootloader_0000_8000.bin').read_bytes()
        ref = (BACKUP / 'bootloader_0000_8000.bin').read_bytes()
        require(boot[:0x7f00] == ref[:0x7f00] and boot[0x7f10:] == ref[0x7f10:], 'Bootloader mismatch')
        app = (before / 'app_8000_7F000.bin').read_bytes(); sp, reset = struct.unpack_from('<II', app)
        require(sp >> 24 == 0x20 and reset & 1 and 0x8000 <= reset & ~1 < 0x7f000, 'Invalid app vectors')
        dev = sha(app[:OLD_BYTES]) == OLD_SHA and app[OLD_BYTES:] == b'\xff' * (len(app) - OLD_BYTES)
        stock = sha(app) == runner.APP_SHA
        require(dev or stock, 'Device app is neither dev 1cb11444 nor the original')
        config = (before / 'config_7F000_80000.bin').read_bytes()
        self.event('preflight_verified', app_sha256=sha(app), comparison='original backup' if stock else 'dev 1cb11444 plus FF',
                   trial_row=boot[0x7f00:0x7f10].hex(), config_sha256=sha(config))
        self.state['write_started'] = True; self.persist(); self.hashes()
        self.flash_main(['--image', str(RELEASE)])          # V3eRun adds the 20 s silence
        self.booted()
        self.state.update(firmware='approved_v3_release', build_label=runner.BUILD_LABEL, flashed_sha256=APPROVED_SHA)
        self.persist(); self.event('approved_release_written', sha256=APPROVED_SHA)
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
            screen.status()
            screen.inject_knob(direction)
            time.sleep(0.5)
            status = screen.status()
            seen.append(('P', status.page) if status.visible else 'stock')
            self.event('page_step', direction=direction, status=asdict(status))
        require(seen == [('P', 1), ('P', 2), ('P', 1), ('P', 0)], f'Unexpected page walk: {seen}')
        require(screen.set_pages(1) == 0, 'Reset to one page failed')
        screen.leave()

    def confirm(self):
        import flash
        wait = self.state['boot_time'] + BOOT_WAIT_S - time.time()
        self.event('confirm_wait', seconds=round(max(0, wait), 1))
        if wait > 0:
            self.close_screen(); time.sleep(wait)
        self.enter_dfu(); after = self.dump('after_confirm')
        boot = (after / 'bootloader_0000_8000.bin').read_bytes()
        pre = (self.dir / 'pre/bootloader_0000_8000.bin').read_bytes()
        require(boot[:0x7f00] == pre[:0x7f00] and boot[0x7f10:] == pre[0x7f10:], 'Bootloader changed')
        flag = boot[0x7f00:0x7f04]
        app = (after / 'app_8000_7F000.bin').read_bytes(); image = RELEASE.read_bytes()
        require(app == image + b'\xff' * (len(app) - len(image)), 'Release app changed')
        before = (self.dir / 'pre/config_7F000_80000.bin').read_bytes()
        current = (after / 'config_7F000_80000.bin').read_bytes()
        changes = [hex(0x7f000 + i) for i, (a, b) in enumerate(zip(before, current)) if a != b]
        self.event('confirm_checked', trial_row=boot[0x7f00:0x7f10].hex(), config_diff=len(changes),
                   config_changes=changes[:64])
        require(not changes, 'Config page changed')
        cleared = flag != TRIAL
        # Not a fault: with the flag kept the release runs exactly as the dev build did (DFU window at power-up).
        self.state['trial_flag_cleared'] = cleared; self.persist()
        self.event('trial_flag', cleared=cleared)
        # Read-only DFU exit: verify the app once more, then manifest (no erase, no write).
        plan = flash.Plan(image); devices = self.dfu_devices()
        require(len(devices) == 1, 'Expected one DFU screen')
        session = flash.Session(self.dfu_backend(), devices[0], plan)
        try:
            session.validate(); session.idle()
            session.gate.verify(session.read(flash.BASE, len(plan.padded)))
            self.event('readonly_exit_verified', sha256=plan.sha256, erase_commands=0)
            session.idle(); session.ctrl(False, 1, 0, b'')
            delay, state = session.status()
            require(state in (6, 7, 8), 'Unexpected manifestation state')
            if state == 6:
                time.sleep(delay / 1000); require(session.status()[1] in (7, 8), 'Manifestation incomplete')
        finally:
            session.close()
        self.quiet_after_dfu('verified_readonly_exit')
        screen = self.connect(keepalive=False)
        self.hello(screen)
        self.event('after_confirm_verified', version=asdict(screen.version()), status=asdict(screen.status()))
        self.state['final_state'] = f"{APPROVED_SHA[:8]} release; trial flag {'cleared' if cleared else 'kept'}; stock page"
        self.persist()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('stage', choices=['preflash', 'pages', 'confirm', 'restore'])
    ap.add_argument('--run-dir', type=runner.Path, required=True)
    args = ap.parse_args(); run = V3kRun(args.run_dir); verified = False
    try:
        import usb.core, libusb_package  # noqa: F401 - fail before any device workflow
        run.hashes(restore_only=args.stage == 'restore'); verified = True
        run.event('stage_start', stage=args.stage)
        if args.stage not in ('preflash', 'restore'):
            require(run.state.get('flashed_sha256') == APPROVED_SHA and
                    run.state.get('firmware') == 'approved_v3_release', 'Approved image not recorded as installed')
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
