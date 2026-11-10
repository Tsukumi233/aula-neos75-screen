"""Approved 74484752 dev build (keyboard status query A5 3B 05) over the f1fefb2e device; never compiles.

Stages: preflash (dry-run, read-only pre dump, device must hold f1fefb2e, write, 20 s HID silence, HELLO),
sessions (ENTER/LEAVE, timeout, keepalive, config page unchanged), keyboard (read-only status reads, config
page unchanged). Recovery on unexpected failure restores only the original backup, as in earlier runs.
"""
import argparse
from dataclasses import asdict
from datetime import datetime
import time
import traceback

import run_v3e
import run_v3b as runner
from run_v3 import V3Run
from run import require

APPROVED_SHA = '74484752ffe24519675c24a73e119ad27f25b58e65bcadbd7a8abca53968c1e3'
# run_v3e (via run_v3d) pinned f1fefb2e at import; re-pin to this build.
runner.DEV_SHA = APPROVED_SHA
runner.OLD_SHA = 'f1fefb2e6a5711fe979ee27877862d041e17ebf57846454466248239f2dfe048'
runner.OLD_BYTES = 277324
runner.OLD_DESCRIPTION = 'v3e f1fefb2e'
runner.BUILD_LABEL = 'v3f 74484752 keyboard status dev'
runner.PREVIOUS = runner.ROOT / 'hwtest/20261107_190736_v3e_dev'


class V3fRun(run_v3e.V3eRun):
    def hello(self, screen):
        value = V3Run.hello(self, screen)
        require(value.flags == 3 and value.test_knob and value.telemetry, f'Expected dev flags 3: {value}')
        self.event('capabilities', flags=value.flags, test_knob=value.test_knob, telemetry=value.telemetry)
        return value

    def preflash(self):
        runner.V3bRun.preflash(self)          # flash_main adds the 20 s silence (V3eRun)
        screen = self.connect(keepalive=False)
        self.hello(screen)
        self.event('startup_verified', version=asdict(screen.version()), status=asdict(screen.status()))

    def keyboard(self):
        screen = self.connect(keepalive=False)
        self.hello(screen)
        reads = []
        for _ in range(6):
            k = screen.keyboard()
            reads.append(dict(raw=k.raw.hex(), battery=k.battery, charging=k.charging, link=k.link,
                              connection=k.connection, reported=k.reported))
            self.event('keyboard_status', **reads[-1])
            time.sleep(2)
        status = screen.status()
        require(not status.visible and not status.armed, 'Keyboard reads opened a session')
        if not reads[-1]['reported']: self.event('keyboard_not_reported')  # evidence, not a fault
        self.state['keyboard'] = reads[-1]; self.persist()
        self.config_check('after_keyboard')
        self.state['final_state'] = '74484752 dev; stock page; realtime disarmed'; self.persist()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('stage', choices=['preflash', 'sessions', 'keyboard', 'restore'])
    ap.add_argument('--run-dir', type=runner.Path, required=True)
    args = ap.parse_args(); run = V3fRun(args.run_dir); verified = False
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
