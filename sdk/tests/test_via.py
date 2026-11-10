"""The keyboard over VIA (in place of Aula Hub): client, call guard, keycodes, service backup/restore.
A fake with the qmk_via_api.KeyboardApi methods holds what the Neos75 answers (values as read on 2026-11-09)."""
from __future__ import annotations

import binascii
import json
from pathlib import Path

import pytest

from neos75screen import keycodes, via
from neos75screen.service.keyboard import KeyboardControl, kle_keys

from fake_device import FakeVia as FakeApi

ROWS, COLS, LAYERS = 7, 15, 3


@pytest.fixture
def fake() -> FakeApi:
    return FakeApi()


def test_guard_allows_only_known_calls(fake: FakeApi) -> None:
    api = via.Keyboard(fake).api
    for name in ("jump_to_bootloader", "reset_eeprom", "reset_macros", "set_macro_bytes", "fast_write_raw_matrix"):
        with pytest.raises(via.ForbiddenCommand):
            getattr(api, name)()
    bad = [("hid_command", ("BootloaderJump", [])), ("hid_command", ("EepromReset", [])),
           ("hid_command", ("CustomMenuSetValue", [2, 1, 1])), ("hid_command", ("CustomMenuSetValue", [0, 4, 3, 1])),
           ("hid_command", ("CustomMenuSetValue", [3, 9, 1])), ("save_custom_menu", (0,)),
           ("hid_send", ([0x53, 3] + [0] * 30,)), ("set_key", (0, 0, 0, 0x7C00)),
           ("set_encoder_value", (0, 1, True, 0x7E22))]
    for name, args in bad:
        with pytest.raises(via.ForbiddenCommand):
            getattr(api, name)(*args)
    api.hid_command("CustomMenuGetValue", [0, 4, 5])
    api.hid_command("CustomMenuSetValue", [3, 2, 5])
    api.allow = {0x7E22}
    api.set_key(1, 0, 13, 0x7E22)                                     # restoring the keyboard's own value


def test_stray_replies_are_retried(fake: FakeApi) -> None:
    fake.stray = via.RETRIES
    assert via.Keyboard(fake).layer_count() == 3
    fake.stray = via.RETRIES + 1
    with pytest.raises(via.ViaError):
        via.Keyboard(fake).layer_count()


def test_reads(fake: FakeApi) -> None:
    kb = via.Keyboard(fake)
    assert kb.protocol() == 12 and kb.layer_count() == 3
    km = kb.keymap()
    assert len(km) == 3 and len(km[0]) == ROWS and len(km[0][0]) == COLS and km == fake.keymap
    assert kb.encoders()[0] == [{"cw": 0xA9, "ccw": 0xAA, "press": 0xA8}, {"cw": 0x7E0F, "ccw": 0x7E10, "press": 0xB2}]
    assert kb.lighting() == {"brightness": 255, "effect": 43, "speed": 131, "hue": 85, "saturation": 100}
    assert kb.system() == {5: 0, 1: 5, 2: 1, 7: 60, 9: 1, 4: 1}
    assert kb.battery() == {"percent": 100, "charging": False}


def test_writes(fake: FakeApi) -> None:
    kb = via.Keyboard(fake)
    assert kb.set_encoder(0, 1, True, 0xDA) == 0xDA and fake.enc[0][1][1] == 0xDA
    assert kb.set_keycode(0, 5, 8, 0xAE) == 0xAE
    with pytest.raises(via.ForbiddenCommand):
        kb.set_keycode(0, 0, 0, 0x7C00)                     # QK_BOOT
    with pytest.raises(ValueError):
        kb.set_encoder(0, 2, True, 0xDA)
    light = kb.set_lighting(effect=2, brightness=300, hue=10)
    assert light["effect"] == 2 and light["brightness"] == 255 and light["hue"] == 10 and fake.saved == [3]
    with pytest.raises(ValueError):
        kb.set_lighting(effect=99)
    assert kb.set_system(5, 1) == 1
    with pytest.raises(ValueError):
        kb.set_system(5, 7)


def test_keycode_names() -> None:
    d = keycodes.describe
    assert d(0xA9)["name"] == "音量 +" and d(0xB2)["label"] == "Calc" and d(0x7E0F)["name"] == "屏幕下一页"
    assert d(0x7827)["name"] == "灯光亮度 +" and d(0x0001)["label"] == "▽"
    assert d(0x0106)["label"] == "Ctrl+C" and d(0x0A16)["label"] == "Shift+Win+S"
    assert d(0x5221)["label"] == "MO(1)" and d(0x4104)["label"] == "LT1(A)"
    assert d(0x7FFF)["label"] == "0x7FFF"
    codes = {k["code"] for g in keycodes.catalog() for k in g["keys"]}
    assert 0x7E00 + 34 not in codes and 0x7C00 not in codes and 0xDA in codes and 0x7E0F in codes


def test_kle_parsing() -> None:
    rows = [["0,0", {"x": 0.5}, "0,1", {"w": 2}, "0,2", "6,8\n\n\n\n\n\n\n\n\ne0"], [{"y": 0.25}, "1,0"]]
    keys = kle_keys(rows)
    assert [(k["row"], k["col"], k["x"], k["y"], k["w"]) for k in keys] == [
        (0, 0, 0, 0, 1), (0, 1, 1.5, 0, 1), (0, 2, 2.5, 0, 2), (6, 8, 4.5, 0, 1), (1, 0, 0, 1.25, 1)]
    assert keys[3]["encoder"] == 0


def test_service_backup_and_restore(tmp_path: Path, fake: FakeApi) -> None:
    kb = KeyboardControl(tmp_path, opener=lambda: via.Keyboard(fake))
    state = kb.state()
    assert state["connection"] == "unknown" and state["layers"] == 3 and (tmp_path / "keyboard_backup.json").exists()
    assert kb.set_knob(0, 1, "cw", 0xDA)["code"] == 0xDA
    assert kb.set_knob(0, 1, "press", 0xAE)["code"] == 0xAE
    assert kb.set_key(1, 0, 13, 0x46)["code"] == 0x46          # over the factory screen-reset key
    kb.set_lighting({"effect": 2, "nonsense": 1})
    kb.set_system(5, 1)
    assert kb.restore()["changed"] == 3
    assert fake.enc[0][1][1] == 0x7E0F and fake.keymap[0][5][8] == 0xB2
    assert fake.keymap[1][0][13] == keycodes.QK_KB + 34       # the keyboard's own value came back
    assert fake.light["effect"] == 43 and fake.system[5] == 0
    backup = json.loads((tmp_path / "keyboard_backup.json").read_text(encoding="utf-8"))
    assert backup["encoders"][0][1]["cw"] == 0x7E0F           # the backup is the state before any change
    with pytest.raises(ValueError):
        kb.set_knob(0, 1, "spin", 0xDA)


def test_macro_codec() -> None:
    macros = [[{"type": "down", "code": 0xE0}, {"type": "tap", "code": 0x06}, {"type": "up", "code": 0xE0},
               {"type": "delay", "ms": 150}, {"type": "text", "text": "hi!"}], [], [{"type": "tap", "code": 0x28}]]
    raw = via.encode_macros(macros)
    assert raw == bytes.fromhex("0102e0010106" "0103e00104") + b"150|hi!\x00\x00\x01\x01\x28\x00"
    assert via.parse_macros(raw + bytes(20), 4) == macros + [[]]
    for bad in ([{"type": "tap", "code": 0x7C00}], [{"type": "text", "text": "中"}], [{"type": "delay", "ms": -1}]):
        with pytest.raises(ValueError):
            via.encode_macros([bad])


def test_macros_write_whole_buffer_without_reset(fake: FakeApi) -> None:
    kb = via.Keyboard(fake)
    assert kb.macros() == [[] for _ in range(16)]
    fake.macro[700:710] = b"old stuff!"
    out = kb.set_macros([[{"type": "tap", "code": 0x04}], [{"type": "text", "text": "abc"}]])
    assert out[0] == [{"type": "tap", "code": 0x04}] and out[1] == [{"type": "text", "text": "abc"}]
    assert fake.macro[700:710] == bytes(10) and fake.macro[-1] == 0        # old bytes cleared, guard byte cleared
    with pytest.raises(ValueError):
        kb.set_macros([[{"type": "text", "text": "x" * 2000}]])
    api = kb.api
    with pytest.raises(via.ForbiddenCommand):
        api.hid_command("DynamicKeymapMacroSetBuffer", [0x04, 0xA0, 4, 1, 2, 3, 4])   # past the buffer end
    with pytest.raises(via.ForbiddenCommand):
        api.reset_macros()


def test_tap_dance(fake: FakeApi) -> None:
    kb = via.Keyboard(fake)
    assert kb.tap_dance_count() == 4
    assert kb.tap_dances()[0] == {"index": 0, "tap": 0, "hold": 0, "double_tap": 0, "tap_hold": 0, "term": 0}
    fake.stray = 1
    td = kb.set_tap_dance(1, tap=0x29, hold=0xE0, term=220)
    assert td == {"index": 1, "tap": 0x29, "hold": 0xE0, "double_tap": 0, "tap_hold": 0, "term": 220}
    assert fake.td[1] == [0x29, 0xE0, 0, 0, 220]
    for bad in ({"tap": 0x7C00}, {"tap": 0x5701}, {"term": 5000}):
        with pytest.raises((via.ForbiddenCommand, ValueError)):
            kb.set_tap_dance(0, **bad)
    with pytest.raises(ValueError):
        kb.set_tap_dance(4, tap=4)
    api = kb.api
    for p in ([0xFE, 0x00], [0xFE, 0x06], [0xFE, 0x0D, 0x01, 0x40], [0xFE, 0x0D, 0x03, 0]):
        with pytest.raises(via.ForbiddenCommand):
            api.hid_send(p + [0] * (32 - len(p)))
    assert all(p[0] in (0x51, 0xFE) and (p[0] != 0xFE or p[1] == 0x0D) for p in fake.sent)


def test_service_macros_and_advanced_keys(tmp_path: Path, fake: FakeApi) -> None:
    kb = KeyboardControl(tmp_path, opener=lambda: via.Keyboard(fake))
    state = kb.state()
    assert state["macro_space"] == {"count": 16, "size": 0x04A3, "used": 16} and len(state["tap_dances"]) == 4
    assert "macro_bytes" not in state
    out = kb.set_macros([[{"type": "text", "text": "hello"}]])
    assert out["macros"][0] == [{"type": "text", "text": "hello"}] and out["macro_space"]["used"] == 21
    assert kb.set_tap_dance(0, {"tap": 0x04, "hold": 0xE1})["names"]["225"]["label"] == "LShift"
    kb.set_label("macros", 0, "  问候  ")
    assert kb.state()["labels"]["macros"] == {"0": "问候"}
    assert kb.restore()["changed"] == 2
    assert fake.macro == bytearray(0x04A3) and fake.td[0] == [0, 0, 0, 0, 0]
    assert keycodes.describe(0x7700)["name"] == "宏 1" and keycodes.describe(0x5703)["label"] == "TD4"


def test_old_backup_gets_macros_added(tmp_path: Path, fake: FakeApi) -> None:
    (tmp_path / "keyboard_backup.json").write_text(json.dumps({"keymap": [], "encoders": [], "lighting": {},
                                                              "system": {}}), encoding="utf-8")
    KeyboardControl(tmp_path, opener=lambda: via.Keyboard(fake)).state()
    backup = json.loads((tmp_path / "keyboard_backup.json").read_text(encoding="utf-8"))
    assert backup["macro_bytes"] == "00" * 0x04A3 and len(backup["tap_dances"]) == 4
