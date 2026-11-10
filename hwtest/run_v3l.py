"""Release build with knob takeover (A5 3B 07, HELLO flags 127) over the 2710bdd4 release on the device (or the
original app); never compiles. Same flow and checks as run_v3k (whose stages it reuses), plus a takeover stage.

Stages:
- preflash: dry-run, read-only pre dump, device must hold release 2710bdd4 (or the original app), write, 20 s
  HID silence, HELLO flags 127.
- pages: as v3k (PC knob steps through 3 host pages, no stock save).
- takeover: takeover on: a PC step still pages and is not reported; takeover is refused without a session and
  ends with LEAVE. (A physical turn under takeover needs a person: checked by hand with the service.)
- confirm: as v3k (120 s since boot, read-only dump: bootloader, app, config page unchanged; trial flag;
  read-only DFU exit; HELLO again).
"""
import argparse
from dataclasses import asdict
from datetime import datetime
import time
import traceback

import run_v3k
import run_v3b as runner
from run_v3 import V3Run
from run import require, sha

APPROVED_SHA = '209be02c12840aca1c424a83148292fb93a21b305d17b9787bc5c4137d836f73'
run_v3k.APPROVED_SHA = APPROVED_SHA
run_v3k.OLD_SHA = '2710bdd4c309d2a28f9f80023c8ffe3301336656e379b418259a069de60caabd'   # release on the device
run_v3k.OLD_BYTES = 278712
runner.BUILD_LABEL = f'v3l {APPROVED_SHA[:8]} release (knob takeover)'


class V3lRun(run_v3k.V3kRun):
    def hello(self, screen):
        value = V3Run.hello(self, screen)
        require(value.flags == 127 and value.knob_takeover and value.test_knob and value.host_pages
                and value.page_cache and value.page_tag, f'Expected flags 127: {value}')
        self.event('capabilities', flags=value.flags)
        return value

    def takeover(self):
        screen = self.connect(keepalive=False)
        self.hello(screen)
        knobs = []
        screen.on_knob(knobs.append)
        screen.enter()
        require(screen.set_pages(3) == 0, 'Expected page 0 after host ENTER')
        require(screen.set_knob_takeover(True) is True, 'Takeover not switched on')
        screen.status()
        screen.inject_knob(1)
        time.sleep(0.5)
        status = screen.status()
        self.event('takeover_step', status=asdict(status), knob_events=[asdict(k) for k in knobs])
        require(status.visible and status.page == 1, f'A PC step under takeover did not page: {status}')
        require(not knobs, f'A PC step under takeover was reported: {knobs}')
        require(screen.set_knob_takeover(False) is False, 'Takeover not switched off')
        screen.inject_knob(-1)
        time.sleep(0.5)
        status = screen.status()
        require(status.page == 0 and [k.direction for k in knobs] == [-1] and not knobs[0].taken,
                f'Plain step after takeover: {status} {knobs}')
        require(screen.set_pages(1) == 0, 'Reset to one page failed')
        screen.set_knob_takeover(True)
        screen.leave()                                   # the session ends takeover
        screen.enter()
        screen._takeover = False                         # check the device state, not the SDK's re-send
        screen.leave()
        self.event('takeover_verified')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('stage', choices=['preflash', 'pages', 'takeover', 'confirm', 'restore'])
    ap.add_argument('--run-dir', type=runner.Path, required=True)
    args = ap.parse_args(); run = V3lRun(args.run_dir); verified = False
    try:
        import usb.core, libusb_package  # noqa: F401 - fail before any device workflow
        require(len(APPROVED_SHA) == 64, 'APPROVED_SHA not filled in')
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
        # Only a failed write or a failed post-write check restores stock; a failed behaviour check does not.
        if verified and args.stage in ('preflash', 'confirm') and run.state.get('write_started') \
                and run.state.get('firmware') != 'stock_restored':
            run.event('automatic_recovery', reason='Unexpected failure; restore only original backup')
            run.restore()
        return 1
    finally:
        run.close_screen(); run.log.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
