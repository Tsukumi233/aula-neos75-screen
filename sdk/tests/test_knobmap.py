"""Custom knob actions: configuration, gesture decisions, the mapper's hook/takeover handling."""
from __future__ import annotations

import queue
import time

import pytest

from neos75screen import knobmap, knobpress
from neos75screen.knobmap import Action, GestureMachine, KnobMapper, parse_keys, validate


def config(**gestures: str | dict) -> dict:
    """config(screen_cw="scroll_down", volume_press={"action": "keys", "keys": "ctrl+z"})"""
    raw: dict = {"screen": {}, "volume": {}}
    for name, value in gestures.items():
        knob, gesture = name.split("_", 1)
        raw[knob][gesture] = value
    return validate(raw)


def kinds(actions: list[Action]) -> list[str]:
    return [a.kind for a in actions]


# --- configuration ---

def test_defaults_take_nothing() -> None:
    m = GestureMachine(knobmap.default_config())
    c = m.capture()
    assert not c.hook and not c.screen_turns and not c.screen_hold and not m.takeover()


def test_validate_fills_defaults_and_checks_values() -> None:
    c = validate({"screen": {"cw": "scroll_down", "press": {"action": "keys", "keys": " Ctrl+Shift+T "}}})
    assert c["screen"]["cw"] == {"action": "scroll_down"}
    assert c["screen"]["press"] == {"action": "keys", "keys": "Ctrl+Shift+T"}
    assert c["volume"]["hold_ccw"] == {"action": "default"} and c["enabled"] is True
    for bad in ({"screen": {"spin": "none"}}, {"screen": {"cw": "explode"}}, {"volume": {"cw": {"action": "keys"}}},
                {"volume": {"cw": {"action": "keys", "keys": "ctrl+nope"}}}, {"screen": {"press": {"action": "run"}}},
                {"enabled": "yes"}, ["screen"]):
        with pytest.raises(ValueError):
            validate(bad)


def test_save_and_load_round_trip(tmp_path) -> None:  # noqa: ANN001
    path = tmp_path / "knobs.json"
    assert knobmap.load(path) == knobmap.default_config()          # missing file
    saved = knobmap.save(path, {"volume": {"cw": "media_next"}})
    assert knobmap.load(path) == saved and saved["volume"]["cw"] == {"action": "media_next"}
    path.write_text("{broken", encoding="utf-8")
    assert knobmap.load(path) == knobmap.default_config()


def test_parse_keys() -> None:
    assert parse_keys("ctrl+shift+t") == (0x11, 0x10, ord("T"))
    assert parse_keys("T+Ctrl") == (0x11, ord("T"))                 # modifiers first
    assert parse_keys("win+d") == (0x5B, ord("D"))
    assert parse_keys("alt+f4") == (0x12, 0x73)
    assert parse_keys("ctrl++") == (0x11, 0xBB) and parse_keys("ctrl+-") == (0x11, 0xBD)
    assert parse_keys("play_pause") == (0xB3,) and parse_keys("pagedown") == (0x22,)
    for bad in ("", "ctrl+", "ctrl+banana", "+"):
        with pytest.raises(ValueError):
            parse_keys(bad)


def test_catalog_lists_every_action_and_gesture() -> None:
    cat = knobmap.catalog()
    assert [a["id"] for a in cat["actions"]] == list(knobmap.ACTIONS)
    assert [g["id"] for g in cat["gestures"]] == list(knobmap.GESTURES)
    assert cat["default"]["screen.cw"] == "下一页"


# --- gestures ---

def test_screen_turns_mapped_take_the_knob_for_the_session() -> None:
    m = GestureMachine(config(screen_cw="scroll_down", screen_ccw="scroll_up"))
    c = m.capture()
    assert c.screen_turns and not c.hook and m.takeover()
    assert kinds(m.turn("screen", 1)) == ["scroll_down"] and kinds(m.turn("screen", -1)) == ["scroll_up"]


def test_one_direction_mapped_keeps_the_other_paging_from_the_pc() -> None:
    m = GestureMachine(config(screen_cw="media_next"))
    assert m.takeover()
    assert kinds(m.turn("screen", 1)) == ["media_next"]
    assert kinds(m.turn("screen", -1)) == ["page_prev"]   # default under takeover: the PC steps the page


def test_press_without_hold_gestures_acts_on_press() -> None:
    m = GestureMachine(config(screen_press="play_pause"))
    assert m.capture().screen_press and not m.capture().screen_hold and not m.takeover()
    assert kinds(m.press("screen")) == ["play_pause"]
    assert m.release("screen") == []


def test_hold_gestures_take_over_while_held_and_decide_the_press_on_release() -> None:
    m = GestureMachine(config(screen_hold_cw="zoom_in", screen_hold_ccw="zoom_out"))
    c = m.capture()
    assert c.screen_press and c.screen_hold and not c.screen_turns
    assert not m.takeover()
    assert m.press("screen") == [] and m.takeover()                   # takeover only while held
    assert kinds(m.turn("screen", 1)) == ["zoom_in"]
    assert kinds(m.turn("screen", -1)) == ["zoom_out"]
    assert m.release("screen") == [] and not m.takeover()             # turned while held: no press action
    m.press("screen")
    assert kinds(m.release("screen")) == ["calculator"]               # a plain press keeps the stock press


def test_unmapped_hold_direction_is_an_ordinary_turn() -> None:
    m = GestureMachine(config(volume_hold_cw="media_next", volume_press="none"))
    m.press("volume")
    assert kinds(m.turn("volume", -1)) == ["volume_down"]
    assert kinds(m.turn("volume", 1)) == ["media_next"]
    assert m.release("volume") == []


def test_volume_knob_mapping_and_stock_replays() -> None:
    m = GestureMachine(config(volume_cw="scroll_down", volume_ccw="scroll_up"))
    c = m.capture()
    assert c.volume_turns and not c.volume_press and not c.screen_press and not m.takeover()
    assert kinds(m.turn("volume", 1)) == ["scroll_down"]
    m = GestureMachine(config(volume_hold_cw="media_next"))
    assert m.capture().volume_turns and m.capture().volume_press
    assert kinds(m.turn("volume", 1)) == ["volume_up"]   # not held: the stock step is sent again
    m.press("volume")
    assert kinds(m.release("volume")) == ["mute"]


def test_none_does_nothing_and_disabled_restores_stock() -> None:
    m = GestureMachine(config(screen_press="none", volume_cw="none"))
    assert m.press("screen") == [] and m.turn("volume", 1) == []
    off = config(screen_cw="scroll_down", volume_press="mute")
    off["enabled"] = False
    m = GestureMachine(off)
    assert not m.capture().hook and not m.takeover()


def test_custom_keys_and_run_carry_their_argument() -> None:
    m = GestureMachine(config(screen_press={"action": "keys", "keys": "ctrl+z"},
                              volume_press={"action": "run", "target": "https://example.com"}))
    (a,) = m.press("screen")
    assert (a.kind, a.keys, a.knob, a.gesture) == ("keys", "ctrl+z", "screen", "press")
    (b,) = m.press("volume")
    assert (b.kind, b.target) == ("run", "https://example.com")


# --- mapper ---

class FakeHook:
    made: list[FakeHook] = []

    def __init__(self, on_event, **options) -> None:  # noqa: ANN001, ANN003
        self.on_event, self.options, self.running = on_event, options, False
        FakeHook.made.append(self)

    def start(self) -> None:
        self.running = True

    def stop(self) -> None:
        self.running = False


class FakeSender:
    def __init__(self) -> None:
        self.sent: list[tuple] = []

    def keys(self, vks: tuple[int, ...]) -> None:
        self.sent.append(("keys", vks))

    def wheel(self, notches: int, horizontal: bool = False, ctrl: bool = False) -> None:
        self.sent.append(("wheel", notches, horizontal, ctrl))

    def run(self, target: str) -> None:
        self.sent.append(("run", target))


def wait_for(check, timeout: float = 2.0) -> None:  # noqa: ANN001
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.01)
    assert check()


def make_mapper(cfg: dict):  # noqa: ANN202
    FakeHook.made.clear()
    takeovers: list[bool] = []
    steps: list[int] = []
    done: queue.Queue = queue.Queue()
    sender = FakeSender()
    mapper = KnobMapper(cfg, set_takeover=takeovers.append, step_page=steps.append, on_action=done.put,
                        sender=sender, hook_factory=FakeHook)
    mapper.start()
    return mapper, takeovers, steps, done, sender


def test_mapper_installs_the_hook_only_when_needed_and_reconfigures() -> None:
    mapper, takeovers, _, _, _ = make_mapper(knobmap.default_config())
    try:
        wait_for(lambda: takeovers == [False])
        assert FakeHook.made == []
        mapper.configure(config(volume_cw="scroll_down"))
        wait_for(lambda: len(FakeHook.made) == 1 and FakeHook.made[0].running)
        opts = FakeHook.made[0].options
        assert opts["volume_turns"] and not opts["volume_press"] and not opts["screen_press"] and opts["releases"]
        mapper.configure(knobmap.default_config())
        wait_for(lambda: not FakeHook.made[0].running)
        assert mapper.status()["hook"] is False
    finally:
        mapper.stop()


def test_mapper_runs_actions_from_the_hook_and_the_firmware() -> None:
    cfg = config(screen_cw="scroll_down", screen_ccw="page_prev", volume_cw={"action": "keys", "keys": "alt+tab"},
                 screen_hold_cw="zoom_in")
    mapper, takeovers, steps, done, sender = make_mapper(cfg)
    try:
        wait_for(lambda: takeovers == [True])
        hook = FakeHook.made[0]
        mapper.screen_turn(1)
        mapper.screen_turn(-1)
        hook.on_event("volume", 1)
        wait_for(lambda: done.qsize() == 3)
        assert sender.sent == [("wheel", -1, False, False), ("keys", (0x12, 0x09))]
        assert steps == [-1]
        hook.on_event("screen", knobpress.PRESS)
        mapper.screen_turn(1)
        hook.on_event("screen", knobpress.RELEASE)
        wait_for(lambda: done.qsize() == 4)
        assert sender.sent[-1] == ("wheel", 1, False, True)       # zoom in: ctrl + wheel
        assert takeovers == [True]                                # takeover stayed on (turns mapped)
    finally:
        mapper.stop()


def test_mapper_switches_takeover_around_a_hold_and_survives_a_missing_screen() -> None:
    calls: list[bool] = []

    def flaky(on: bool) -> None:
        calls.append(on)
        if on and len(calls) == 2:
            raise RuntimeError("键盘屏幕未连接")

    FakeHook.made.clear()
    mapper = KnobMapper(config(screen_hold_cw="zoom_in"), set_takeover=flaky, sender=FakeSender(),
                        hook_factory=FakeHook)
    mapper.start()
    try:
        wait_for(lambda: calls == [False] and FakeHook.made)
        hook = FakeHook.made[0]
        hook.on_event("screen", knobpress.PRESS)       # no screen: takeover fails, nothing breaks
        hook.on_event("screen", knobpress.RELEASE)
        hook.on_event("screen", knobpress.PRESS)
        wait_for(lambda: calls == [False, True, False, True])
        hook.on_event("screen", knobpress.RELEASE)
        wait_for(lambda: calls == [False, True, False, True, False])
        mapper.screen_session()                        # a new session re-sends the wanted state
        wait_for(lambda: calls == [False, True, False, True, False, False])
    finally:
        mapper.stop()


def test_a_failing_action_is_logged_and_the_worker_goes_on() -> None:
    logs: list[str] = []

    class Broken(FakeSender):
        def run(self, target: str) -> None:
            raise OSError("not found")

    sender = Broken()
    FakeHook.made.clear()
    mapper = KnobMapper(config(volume_press={"action": "run", "target": "nothing.exe"}, volume_cw="scroll_down"),
                        sender=sender, hook_factory=FakeHook, log=logs.append)
    mapper.start()
    try:
        wait_for(lambda: FakeHook.made)
        FakeHook.made[0].on_event("volume", knobpress.PRESS)
        FakeHook.made[0].on_event("volume", 1)
        wait_for(lambda: sender.sent == [("wheel", -1, False, False)])
        assert any("not found" in line for line in logs)
    finally:
        mapper.stop()


def test_filter_reports_releases_when_asked() -> None:
    f = knobpress.KnobKeyFilter(screen_press=True, releases=True)
    assert f.handle(knobpress.VK_LAUNCH_APP2, 0x0100, 0, True).event == ("screen", knobpress.PRESS)
    assert f.handle(knobpress.VK_LAUNCH_APP2, 0x0101, 300, True).event == ("screen", knobpress.RELEASE)
    assert f.handle(knobpress.VK_LAUNCH_APP2, 0x0101, 400, True).event is None   # no press seen
