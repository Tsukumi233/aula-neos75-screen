"""Knob input on the PC (hardware 2026-11-07): two knobs. Screen knob: turns go to the screen only, press =
Calculator. Volume knob: turns = Volume Up/Down, press = Mute."""
from __future__ import annotations

import sys
import time

import pytest

from neos75screen.knobpress import (SCREEN, VK_LAUNCH_APP2, VK_VOLUME_DOWN, VK_VOLUME_MUTE, VK_VOLUME_UP, VOLUME,
                                    Decision, KnobInputHook, KnobKeyFilter, consumer_usage, device_matches)

DOWN, UP, SYSDOWN = 0x0100, 0x0101, 0x0104
NEOS = r"\\?\HID#VID_1EA7&PID_4884&MI_00&Col04#8&1&0&0003#{4d1e55b2-f16f-11cf-88cb-001111000030}"
MOUSE = r"\\?\HID#VID_046D&PID_C547&MI_01&Col02#7&25da7313&0&0001#{4d1e55b2-f16f-11cf-88cb-001111000030}"


def everything() -> KnobKeyFilter:
    return KnobKeyFilter(screen_press=True, volume_turns=True, volume_press=True)


def test_device_match_and_consumer_usage_from_observed_reports() -> None:
    assert device_matches(NEOS, [(0x1EA7, 0x4884)])
    assert not device_matches(MOUSE, [(0x1EA7, 0x4884)])
    assert not device_matches("garbage", [(0x1EA7, 0x4884)])
    assert consumer_usage(bytes.fromhex("04e900")) == 0xE9          # volume knob clockwise
    assert consumer_usage(bytes.fromhex("04ea00")) == 0xEA          # volume knob counter-clockwise
    assert consumer_usage(bytes.fromhex("04e200")) == 0xE2          # volume knob press (Mute)
    assert consumer_usage(bytes.fromhex("049201")) == 0x192         # screen knob press (AL Calculator)
    assert consumer_usage(bytes.fromhex("040000")) == 0             # release
    assert consumer_usage(bytes.fromhex("03e9000000")) == 0xE9      # Logitech receiver (mouse)
    assert consumer_usage(b"\x04") is None


def test_volume_knob_step_is_a_fast_tap_and_is_swallowed() -> None:
    f = everything()
    assert f.handle(VK_VOLUME_UP, DOWN, 1000, True) == Decision(swallow=True)
    assert f.handle(VK_VOLUME_UP, UP, 1001, True) == Decision((VOLUME, 1), True)
    assert f.handle(VK_VOLUME_DOWN, DOWN, 2000, True) == Decision(swallow=True)
    assert f.handle(VK_VOLUME_DOWN, UP, 2002, True) == Decision((VOLUME, -1), True)


def test_fn_volume_key_is_held_longer_and_replayed_on_release() -> None:
    f = everything()
    assert f.handle(VK_VOLUME_UP, DOWN, 1000, True) == Decision(swallow=True)
    assert f.handle(VK_VOLUME_UP, UP, 1100, True) == Decision(None, True, ((VK_VOLUME_UP, False), (VK_VOLUME_UP, True)))
    assert f.handle(VK_VOLUME_UP, DOWN, 3000, True) == Decision(swallow=True)  # state reset afterwards


def test_key_repeat_while_held_hands_the_key_over() -> None:
    f = everything()
    f.handle(VK_VOLUME_DOWN, DOWN, 0, True)
    assert f.handle(VK_VOLUME_DOWN, DOWN, 500, True) == Decision(swallow=True, replay=((VK_VOLUME_DOWN, False),))
    assert f.handle(VK_VOLUME_DOWN, DOWN, 530, True) == Decision()   # further repeats pass
    assert f.handle(VK_VOLUME_DOWN, UP, 600, True) == Decision()     # and the release
    assert f.handle(VK_VOLUME_DOWN, DOWN, 700, True) == Decision(swallow=True)


def test_tick_counter_wraps() -> None:
    f = everything()
    f.handle(VK_VOLUME_UP, DOWN, 0xFFFFFFFF, True)
    assert f.handle(VK_VOLUME_UP, UP, 1, True).event == (VOLUME, 1)


def test_presses_once_per_physical_press_per_knob() -> None:
    f = everything()
    assert f.handle(VK_LAUNCH_APP2, DOWN, 0, True) == Decision((SCREEN, 0), True)
    assert f.handle(VK_LAUNCH_APP2, DOWN, 30, True) == Decision(None, True)
    assert f.handle(VK_VOLUME_MUTE, DOWN, 40, True) == Decision((VOLUME, 0), True)  # independent of the other
    assert f.handle(VK_LAUNCH_APP2, UP, 60, True) == Decision(None, True)
    assert f.handle(VK_VOLUME_MUTE, UP, 200, True) == Decision(None, True)
    assert f.handle(VK_LAUNCH_APP2, SYSDOWN, 90, True) == Decision((SCREEN, 0), True)
    assert KnobKeyFilter(block_screen_press=False).handle(VK_LAUNCH_APP2, DOWN, 0, True) == Decision((SCREEN, 0), False)


def test_defaults_take_only_the_screen_press() -> None:
    f = KnobKeyFilter()
    assert f.handle(VK_LAUNCH_APP2, DOWN, 0, True).event == (SCREEN, 0)
    for vk in (VK_VOLUME_UP, VK_VOLUME_DOWN, VK_VOLUME_MUTE):
        assert f.handle(vk, DOWN, 0, True) == Decision()
        assert f.handle(vk, UP, 1, True) == Decision()


def test_other_devices_unknown_source_and_replays_pass_untouched() -> None:
    f = everything()
    for vk in (VK_VOLUME_UP, VK_VOLUME_DOWN, VK_VOLUME_MUTE, VK_LAUNCH_APP2):
        assert f.handle(vk, DOWN, 0, False) == Decision()
        assert f.handle(vk, UP, 1, False) == Decision()
        assert f.handle(vk, DOWN, 2, True, replayed=True) == Decision()
    assert f.handle(0x41, DOWN, 0, True) == Decision()               # any other key: never touched


@pytest.mark.skipif(sys.platform != "win32", reason="Windows hook")
def test_hook_installs_survives_messages_and_stops_cleanly() -> None:
    hook = KnobInputHook(lambda knob, direction: None)
    hook.start()
    hook.stop()
    hook.stop()
    again = KnobInputHook(lambda knob, direction: None)  # window class and raw input registration released
    again.start()
    import ctypes
    ctypes.WinDLL("user32").PostThreadMessageW(again._thread_id, 0x0400, 0, 0)  # WM_USER
    ctypes.WinDLL("user32").keybd_event(0x87, 0, 2, 0)  # F24 key-up only: passes every filter, changes nothing
    time.sleep(0.2)
    assert again._thread is not None and again._thread.is_alive()
    again.stop()


@pytest.mark.skipif(sys.platform == "win32", reason="non-Windows behaviour")
def test_other_platforms_refuse() -> None:
    with pytest.raises(OSError):
        KnobInputHook(lambda knob, direction: None)


class FakeHook:
    made: list[FakeHook] = []

    def __init__(self, on_event, **options) -> None:  # noqa: ANN001, ANN003
        self.on_event, self.options, self.running = on_event, options, False
        FakeHook.made.append(self)

    def start(self) -> None:
        self.running = True

    def stop(self) -> None:
        self.running = False


def test_screen_reports_both_knobs_and_keeps_screen_turns(monkeypatch: pytest.MonkeyPatch) -> None:
    import neos75screen.knobpress as kp
    from neos75screen import Screen
    from neos75screen.protocol import KnobEvent
    from neos75screen.transport import MockTransport

    FakeHook.made = []
    monkeypatch.setattr(kp, "KnobInputHook", FakeHook)
    transport = MockTransport()
    screen = Screen(transport, keepalive=False)
    seen: list[KnobEvent] = []
    screen.on_knob(seen.append)
    screen.capture_knob(volume_knob=True)
    screen.capture_knob()  # idempotent
    hook = FakeHook.made[0]
    assert len(FakeHook.made) == 1 and hook.running
    assert hook.options["screen_press"] and hook.options["block_screen_press"]
    assert hook.options["volume_turns"] and hook.options["volume_press"]
    hook.on_event(VOLUME, 1)
    hook.on_event(SCREEN, 0)
    knob = bytearray(64)
    knob[0:7] = bytes([0xA5, 0x3B, 0x00, 0x03, 0x00, 0x21, 0x01])
    knob[7] = (~sum(knob[1:7])) & 0xFF
    transport.inject(bytes(knob))   # a screen-knob turn reported by the firmware: still delivered
    deadline = time.monotonic() + 2
    while len(seen) < 3 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert seen == [KnobEvent(1, VOLUME, "pc"), KnobEvent(0, SCREEN, "pc"), KnobEvent(1, SCREEN, "screen")]
    screen.close(leave=False)
    assert not hook.running


def test_capture_knob_press_takes_only_the_screen_press(monkeypatch: pytest.MonkeyPatch) -> None:
    import neos75screen.knobpress as kp
    from neos75screen import Screen
    from neos75screen.transport import MockTransport

    FakeHook.made = []
    monkeypatch.setattr(kp, "KnobInputHook", FakeHook)
    screen = Screen(MockTransport(), keepalive=False)
    screen.capture_knob_press(block=False)
    options = FakeHook.made[0].options
    assert options["screen_press"] and options["block_screen_press"] is False
    assert options["volume_turns"] is False and options["volume_press"] is False
    screen.close(leave=False)
