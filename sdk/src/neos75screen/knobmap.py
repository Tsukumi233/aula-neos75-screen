"""Custom actions for the Neos75's two knobs: what a turn, a press, or a turn while held down does.

Neither the keyboard nor Aula Hub can remap the knobs. This module does it on the PC:

    knob     turn                                        press
    screen   the screen firmware (takeover, A5 3B 07)    Calculator key, taken by a keyboard hook
    volume   Volume Up/Down keys, taken by the hook      Mute key, taken by the hook

Each knob has five gestures: `cw`, `ccw` (turn), `press`, and `hold_cw`, `hold_ccw` (turn while pressed). Each
gesture maps to one action (`ACTIONS`). `default` keeps what the knob does out of the box: the screen knob
pages, opens Calculator; the volume knob sets the volume and mutes. For the hold gestures `default` means "the
same as an ordinary turn".

How the knobs are taken:
- Screen-knob turns reach only the screen. With `cw`/`ccw` mapped, the firmware's takeover is on for the whole
  session (a turn is only reported, no page moves); with only the hold gestures mapped it is switched on while
  the knob is held. Needs firmware with `knob_takeover`, and the screen in use (the service showing pages).
  The `page_next`/`page_prev` actions step the pages from the PC (they pass the takeover).
- The press keys and the volume keys come from the keyboard itself; a low-level keyboard hook takes them, only
  from the Neos75 keyboard (see `knobpress`). If a press has hold gestures mapped, the press action runs on
  release, and only if the knob was not turned while held.

`GestureMachine` is the pure decision logic; `KnobMapper` runs it with the hook, a worker thread and
`InputSender` (Windows `SendInput`, programs via `os.startfile`).
"""
from __future__ import annotations

import copy
import json
import os
import queue
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import knobpress

KNOBS = ("screen", "volume")
GESTURES = ("cw", "ccw", "press", "hold_cw", "hold_ccw")
TURNS = {1: "cw", -1: "ccw"}
HOLD_TURNS = {1: "hold_cw", -1: "hold_ccw"}

# id: (label, group, needs). needs: "keys" (a shortcut), "target" (a program, file or URL) or None.
ACTIONS: dict[str, tuple[str, str, str | None]] = {
    "default": ("原厂功能", "基本", None),
    "none": ("不做任何事", "基本", None),
    "page_next": ("屏幕下一页", "屏幕", None),
    "page_prev": ("屏幕上一页", "屏幕", None),
    "volume_up": ("音量 +", "音量与媒体", None),
    "volume_down": ("音量 −", "音量与媒体", None),
    "mute": ("静音", "音量与媒体", None),
    "play_pause": ("播放 / 暂停", "音量与媒体", None),
    "media_next": ("下一首", "音量与媒体", None),
    "media_prev": ("上一首", "音量与媒体", None),
    "scroll_up": ("向上滚动", "滚动与缩放", None),
    "scroll_down": ("向下滚动", "滚动与缩放", None),
    "scroll_left": ("向左滚动", "滚动与缩放", None),
    "scroll_right": ("向右滚动", "滚动与缩放", None),
    "zoom_in": ("放大（Ctrl + 滚轮）", "滚动与缩放", None),
    "zoom_out": ("缩小（Ctrl + 滚轮）", "滚动与缩放", None),
    "keys": ("快捷键…", "自定义", "keys"),
    "run": ("打开程序 / 网址…", "自定义", "target"),
}
GESTURE_LABELS = {"cw": "顺时针转", "ccw": "逆时针转", "press": "按下",
                  "hold_cw": "按住顺时针转", "hold_ccw": "按住逆时针转"}
KNOB_LABELS = {"screen": "屏幕旋钮", "volume": "音量旋钮"}
DEFAULT_MEANING = {  # what `default` does, per knob and gesture (shown in the app)
    ("screen", "cw"): "下一页", ("screen", "ccw"): "上一页", ("screen", "press"): "打开计算器",
    ("volume", "cw"): "音量 +", ("volume", "ccw"): "音量 −", ("volume", "press"): "静音",
}
STOCK = {("screen", 1): "page_next", ("screen", -1): "page_prev", ("screen", 0): "calculator",
         ("volume", 1): "volume_up", ("volume", -1): "volume_down", ("volume", 0): "mute"}


# --- configuration ------------------------------------------------------------------------------------

def default_config() -> dict:
    return {"enabled": True, **{k: {g: {"action": "default"} for g in GESTURES} for k in KNOBS}}


def validate(raw: object) -> dict:
    """A complete, checked configuration from `raw` (missing parts take the defaults). ValueError if bad."""
    if not isinstance(raw, dict):
        raise ValueError("knob settings must be an object")
    config = default_config()
    if "enabled" in raw:
        if not isinstance(raw["enabled"], bool):
            raise ValueError("enabled must be true or false")
        config["enabled"] = raw["enabled"]
    for knob in KNOBS:
        gestures = raw.get(knob, {})
        if not isinstance(gestures, dict):
            raise ValueError(f"{knob} must be an object")
        for gesture, value in gestures.items():
            if gesture not in GESTURES:
                raise ValueError(f"unknown gesture {knob}.{gesture}")
            if isinstance(value, str):
                value = {"action": value}
            if not isinstance(value, dict) or value.get("action") not in ACTIONS:
                raise ValueError(f"{knob}.{gesture}: unknown action {value!r}")
            action = value["action"]
            entry = {"action": action}
            needs = ACTIONS[action][2]
            if needs == "keys":
                keys = value.get("keys", "")
                if not isinstance(keys, str) or not keys.strip():
                    raise ValueError(f"{knob}.{gesture}: the shortcut is empty")
                parse_keys(keys)   # raises ValueError for an unknown key
                entry["keys"] = keys.strip()
            elif needs == "target":
                target = value.get("target", "")
                if not isinstance(target, str) or not target.strip():
                    raise ValueError(f"{knob}.{gesture}: the program or URL is empty")
                entry["target"] = target.strip()
            config[knob][gesture] = entry
    return config


def load(path: Path) -> dict:
    try:
        return validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return default_config()


def save(path: Path, config: dict) -> dict:
    config = validate(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    return config


def catalog() -> dict:
    """What the app needs to draw the settings: knobs, gestures, actions and what `default` does."""
    return {"knobs": [{"id": k, "label": KNOB_LABELS[k]} for k in KNOBS],
            "gestures": [{"id": g, "label": GESTURE_LABELS[g]} for g in GESTURES],
            "actions": [{"id": a, "label": label, "group": group, "needs": needs}
                        for a, (label, group, needs) in ACTIONS.items()],
            "default": {f"{k}.{g}": m for (k, g), m in DEFAULT_MEANING.items()}}


# --- keys ---------------------------------------------------------------------------------------------

MODIFIERS = {"ctrl": 0x11, "control": 0x11, "shift": 0x10, "alt": 0x12, "win": 0x5B, "meta": 0x5B, "cmd": 0x5B}
NAMED = {
    "enter": 0x0D, "return": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B, "space": 0x20,
    "backspace": 0x08, "delete": 0x2E, "del": 0x2E, "insert": 0x2D, "ins": 0x2D, "home": 0x24, "end": 0x23,
    "pageup": 0x21, "pgup": 0x21, "pagedown": 0x22, "pgdn": 0x22,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28, "printscreen": 0x2C, "prtsc": 0x2C,
    "capslock": 0x14, "numlock": 0x90, "scrolllock": 0x91, "pause": 0x13, "apps": 0x5D, "menu": 0x5D,
    "volume_up": 0xAF, "volume_down": 0xAE, "mute": 0xAD, "play_pause": 0xB3, "media_next": 0xB0,
    "media_prev": 0xB1, "media_stop": 0xB2, "calculator": 0xB7, "browser_back": 0xA6, "browser_forward": 0xA7,
    "browser_refresh": 0xA8, "browser_home": 0xAC, "mail": 0xB4, "mycomputer": 0xB6,
    "-": 0xBD, "minus": 0xBD, "=": 0xBB, "plus": 0xBB, "equal": 0xBB, ",": 0xBC, "comma": 0xBC, ".": 0xBE,
    "period": 0xBE, "/": 0xBF, "slash": 0xBF, ";": 0xBA, "semicolon": 0xBA, "'": 0xDE, "quote": 0xDE,
    "[": 0xDB, "]": 0xDD, "\\": 0xDC, "backslash": 0xDC, "`": 0xC0, "backquote": 0xC0,
    **{f"f{n}": 0x6F + n for n in range(1, 25)},
    **{f"num{n}": 0x60 + n for n in range(10)},
}
EXTENDED = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E, 0x5B, 0x5D, 0x2C, 0x90}


def parse_keys(text: str) -> tuple[int, ...]:
    """'ctrl+shift+t' -> virtual-key codes in press order (modifiers first). ValueError for unknown keys."""
    parts = [p.strip().lower() for p in text.replace(" ", "").split("+")]
    if text.strip().endswith("++"):          # "ctrl++": the plus key
        parts = parts[:-2] + ["plus"]
    if not parts or any(not p for p in parts):
        raise ValueError(f"bad shortcut {text!r}")
    vks: list[int] = []
    for part in parts:
        if part in MODIFIERS:
            vk = MODIFIERS[part]
        elif part in NAMED:
            vk = NAMED[part]
        elif len(part) == 1 and (part.isascii() and part.isalnum()):
            vk = ord(part.upper())
        else:
            raise ValueError(f"unknown key {part!r} in {text!r}")
        vks.append(vk)
    mods = [vk for vk in vks if vk in MODIFIERS.values()]
    rest = [vk for vk in vks if vk not in MODIFIERS.values()]
    if not rest and not mods:
        raise ValueError(f"bad shortcut {text!r}")
    return tuple(dict.fromkeys(mods)) + tuple(rest)


# --- decisions ----------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Action:
    """One thing to do: `kind` is an action id (never `default`), or "calculator" (the stock screen press)."""

    kind: str
    keys: str = ""
    target: str = ""
    knob: str = ""
    gesture: str = ""


@dataclass
class Capture:
    """What has to be taken from the keyboard and the screen for a configuration."""

    screen_press: bool = False      # hook: Calculator from the Neos75 keyboard
    volume_press: bool = False      # hook: Mute
    volume_turns: bool = False      # hook: Volume Up/Down knob steps
    screen_turns: bool = False      # firmware takeover for the whole session
    screen_hold: bool = False       # firmware takeover while the screen knob is held

    @property
    def hook(self) -> bool:
        return self.screen_press or self.volume_press or self.volume_turns


@dataclass
class GestureMachine:
    """Turns knob input into actions for one configuration. Pure: no I/O, no threads."""

    config: dict = field(default_factory=default_config)
    held: dict = field(default_factory=lambda: {k: False for k in KNOBS})
    turned_while_held: dict = field(default_factory=lambda: {k: False for k in KNOBS})

    def _entry(self, knob: str, gesture: str) -> dict:
        return self.config[knob][gesture]

    def _custom(self, knob: str, gesture: str) -> bool:
        return self.config.get("enabled", True) and self._entry(knob, gesture)["action"] != "default"

    def capture(self) -> Capture:
        hold = {k: self._custom(k, "hold_cw") or self._custom(k, "hold_ccw") for k in KNOBS}
        turns = {k: self._custom(k, "cw") or self._custom(k, "ccw") for k in KNOBS}
        return Capture(screen_press=self._custom("screen", "press") or hold["screen"],
                       volume_press=self._custom("volume", "press") or hold["volume"],
                       volume_turns=turns["volume"] or hold["volume"],
                       screen_turns=turns["screen"], screen_hold=hold["screen"])

    def takeover(self) -> bool:
        """Should the screen firmware take the screen knob's turns over right now?"""
        c = self.capture()
        return c.screen_turns or (c.screen_hold and self.held["screen"])

    def _action(self, knob: str, gesture: str, stock: str) -> Action | None:
        entry = self._entry(knob, gesture) if self.config.get("enabled", True) else {"action": "default"}
        kind = entry["action"]
        if kind == "none":
            return None
        if kind == "default":
            kind = stock
        return Action(kind, entry.get("keys", ""), entry.get("target", ""), knob, gesture)

    def turn(self, knob: str, direction: int) -> list[Action]:
        """A step of `knob` (+1 / -1) that was taken (the hook swallowed it, or the firmware took it over)."""
        stock = STOCK[(knob, direction)]
        if self.held[knob] and self._custom(knob, HOLD_TURNS[direction]):
            self.turned_while_held[knob] = True
            action = self._action(knob, HOLD_TURNS[direction], stock)
        else:
            if self.held[knob]:
                self.turned_while_held[knob] = True
            action = self._action(knob, TURNS[direction], stock)
        return [action] if action else []

    def press(self, knob: str) -> list[Action]:
        """The knob was pressed (only reported while its press is taken)."""
        self.held[knob] = True
        self.turned_while_held[knob] = False
        if self._hold_mapped(knob):
            return []   # decided on release: a press, or a turn while held
        action = self._action(knob, "press", STOCK[(knob, 0)])
        return [action] if action else []

    def release(self, knob: str) -> list[Action]:
        was_held, turned = self.held[knob], self.turned_while_held[knob]
        self.held[knob] = False
        self.turned_while_held[knob] = False
        if not was_held or turned or not self._hold_mapped(knob):
            return []
        action = self._action(knob, "press", STOCK[(knob, 0)])
        return [action] if action else []

    def _hold_mapped(self, knob: str) -> bool:
        return self._custom(knob, "hold_cw") or self._custom(knob, "hold_ccw")


# --- doing ------------------------------------------------------------------------------------------

class InputSender:
    """Sends keys, wheel steps and programs on Windows. Every injected key carries `knobpress.REPLAY_MARK`, so
    the knob hook lets it through instead of taking it as a knob."""

    WHEEL, HWHEEL = 0x0800, 0x1000

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise OSError("sending input needs Windows")
        import ctypes
        import ctypes.wintypes as w
        self._ctypes = ctypes

        class KeybdInput(ctypes.Structure):
            _fields_ = [("wVk", w.WORD), ("wScan", w.WORD), ("dwFlags", w.DWORD), ("time", w.DWORD),
                        ("dwExtraInfo", ctypes.c_size_t)]

        class MouseInput(ctypes.Structure):
            _fields_ = [("dx", w.LONG), ("dy", w.LONG), ("mouseData", w.DWORD), ("dwFlags", w.DWORD),
                        ("time", w.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

        class InputUnion(ctypes.Union):
            _fields_ = [("ki", KeybdInput), ("mi", MouseInput)]

        class Input(ctypes.Structure):
            _fields_ = [("type", w.DWORD), ("u", InputUnion)]

        self._KeybdInput, self._MouseInput, self._Input = KeybdInput, MouseInput, Input
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._user32.SendInput.argtypes = (w.UINT, ctypes.POINTER(Input), ctypes.c_int)
        self._user32.MapVirtualKeyW.argtypes = (w.UINT, w.UINT)

    def _key(self, vk: int, up: bool):  # noqa: ANN202
        flags = (0x0002 if up else 0) | (0x0001 if vk in EXTENDED or 0xA6 <= vk <= 0xB7 else 0)
        scan = self._user32.MapVirtualKeyW(vk, 0)
        item = self._Input(type=1)
        item.u.ki = self._KeybdInput(vk, scan, flags, 0, knobpress.REPLAY_MARK)
        return item

    def _wheel(self, flag: int, amount: int):  # noqa: ANN202
        item = self._Input(type=0)
        item.u.mi = self._MouseInput(0, 0, amount & 0xFFFFFFFF, flag, 0, knobpress.REPLAY_MARK)
        return item

    def _send(self, items: list) -> None:
        array = (self._Input * len(items))(*items)
        self._user32.SendInput(len(items), array, self._ctypes.sizeof(self._Input))

    def keys(self, vks: tuple[int, ...]) -> None:
        self._send([self._key(vk, False) for vk in vks] + [self._key(vk, True) for vk in reversed(vks)])

    def wheel(self, notches: int, horizontal: bool = False, ctrl: bool = False) -> None:
        items = [self._key(0x11, False)] if ctrl else []
        items.append(self._wheel(self.HWHEEL if horizontal else self.WHEEL, 120 * notches))
        if ctrl:
            items.append(self._key(0x11, True))
        self._send(items)

    def run(self, target: str) -> None:
        target = os.path.expandvars(target)
        if "://" not in target:
            target = os.path.normpath(target)   # the app's file picker gives forward slashes
        os.startfile(target)  # type: ignore[attr-defined]  # Windows only


VK_ACTIONS = {"volume_up": 0xAF, "volume_down": 0xAE, "mute": 0xAD, "play_pause": 0xB3, "media_next": 0xB0,
              "media_prev": 0xB1, "calculator": 0xB7}
WHEEL_ACTIONS = {"scroll_up": (1, False, False), "scroll_down": (-1, False, False),
                 "scroll_left": (-1, True, False), "scroll_right": (1, True, False),
                 "zoom_in": (1, False, True), "zoom_out": (-1, False, True)}


def perform(action: Action, sender, step_page: Callable[[int], None]) -> None:  # noqa: ANN001
    """Do `action` with `sender` (an `InputSender`); page steps go to `step_page(+1 / -1)`."""
    kind = action.kind
    if kind in ("page_next", "page_prev"):
        step_page(1 if kind == "page_next" else -1)
    elif kind in VK_ACTIONS:
        sender.keys((VK_ACTIONS[kind],))
    elif kind in WHEEL_ACTIONS:
        notches, horizontal, ctrl = WHEEL_ACTIONS[kind]
        sender.wheel(notches, horizontal, ctrl)
    elif kind == "keys":
        sender.keys(parse_keys(action.keys))
    elif kind == "run":
        sender.run(action.target)


class KnobMapper:
    """Runs a configuration: owns the keyboard hook (when needed) and a worker thread that does the actions.

    `set_takeover(on)` is called (on the worker thread) whenever the screen firmware's takeover should change;
    `step_page(direction)` steps the screen pages from the PC; `on_action(dict)` reports what was done. Feed
    screen-knob turns the firmware reported under takeover to `screen_turn`."""

    def __init__(self, config: dict | None = None, *, set_takeover: Callable[[bool], None] | None = None,
                 step_page: Callable[[int], None] | None = None, on_action: Callable[[dict], None] | None = None,
                 log: Callable[[str], None] | None = None, sender=None, hook_factory=None) -> None:  # noqa: ANN001
        self._config = validate(config or default_config())
        self.machine = GestureMachine(copy.deepcopy(self._config))
        self._set_takeover = set_takeover or (lambda on: None)
        self._step_page = step_page or (lambda direction: None)
        self._on_action = on_action or (lambda info: None)
        self._log = log or (lambda message: None)
        self._sender = sender
        self._hook_factory = hook_factory if hook_factory is not None else knobpress.KnobInputHook
        self._hook = None
        self._hook_capture: Capture | None = None
        self.hook_error: str | None = None
        self._queue: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._takeover: bool | None = None
        self._takeover_error = ""
        self._lock = threading.Lock()

    # --- lifecycle ---

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._work, name="neos75-knob-actions", daemon=True)
            self._thread.start()
        self._queue.put(("apply", None))

    def stop(self) -> None:
        if self._thread is not None:
            self._queue.put(("stop", None))
            self._thread.join(3)
            self._thread = None
        self._stop_hook()

    def configure(self, config: dict, wait: float = 1.0) -> None:
        """Apply `config`; waits up to `wait` seconds for the worker (hook and takeover switched)."""
        self._config = validate(config)
        done = threading.Event()
        self._queue.put(("config", (copy.deepcopy(self._config), done)))
        if self._thread is not None and wait:
            done.wait(wait)

    @property
    def config(self) -> dict:
        """The configuration asked for last (the worker applies it a moment later)."""
        return copy.deepcopy(self._config)

    def status(self) -> dict:
        capture = self.machine.capture()
        return {"hook": self._hook is not None, "hook_error": self.hook_error,
                "takeover": bool(self._takeover), "screen_turns": capture.screen_turns,
                "screen_hold": capture.screen_hold, "held": dict(self.machine.held)}

    # --- input (any thread) ---

    def screen_turn(self, direction: int) -> None:
        """A screen-knob turn the firmware took over (`KnobEvent.taken`: it moved no page)."""
        self._queue.put(("screen_turn", direction))

    def screen_session(self) -> None:
        """The screen (re)connected: takeover has to be sent again."""
        self._queue.put(("resync", None))

    def _hook_event(self, knob: str, value: int) -> None:   # hook thread: only queue
        self._queue.put(("hook", (knob, value)))

    # --- worker ---

    def _work(self) -> None:
        while True:
            kind, arg = self._queue.get()
            try:
                if kind == "stop":
                    return
                if kind == "config":
                    self.machine.config, done = arg
                    self.machine.held = {k: False for k in KNOBS}
                    try:
                        self._apply()
                    finally:
                        done.set()
                elif kind in ("apply", "resync"):
                    if kind == "resync":
                        self._takeover = None
                    self._apply()
                elif kind == "screen_turn":
                    self._do(self.machine.turn("screen", arg))
                elif kind == "hook":
                    knob, value = arg
                    if value == knobpress.PRESS:
                        actions = self.machine.press(knob)
                    elif value == knobpress.RELEASE:
                        actions = self.machine.release(knob)
                    else:
                        actions = self.machine.turn(knob, value)
                    self._sync_takeover()
                    self._do(actions)
            except Exception as exc:  # noqa: BLE001 - one bad action must not end the worker
                self._log(f"knob action failed: {exc!r}")

    def _apply(self) -> None:
        capture = self.machine.capture()
        if capture.hook:
            if self._hook is None or self._hook_capture != capture:
                self._stop_hook()
                self._start_hook(capture)
        else:
            self._stop_hook()
        self._sync_takeover()

    def _start_hook(self, capture: Capture) -> None:
        try:
            hook = self._hook_factory(self._hook_event, screen_press=capture.screen_press, block_screen_press=True,
                                      volume_turns=capture.volume_turns, volume_press=capture.volume_press,
                                      releases=True)
            hook.start()
        except Exception as exc:  # noqa: BLE001 - reported in status, the rest keeps working
            self.hook_error = str(exc)
            self._log(f"knob hook not installed: {exc}")
            return
        self._hook, self._hook_capture, self.hook_error = hook, capture, None

    def _stop_hook(self) -> None:
        hook, self._hook, self._hook_capture = self._hook, None, None
        if hook is not None:
            hook.stop()

    def _sync_takeover(self) -> None:
        want = self.machine.takeover()
        if want == self._takeover:
            return
        try:
            self._set_takeover(want)
            self._takeover = want
            self._takeover_error = ""
        except Exception as exc:  # noqa: BLE001 - no screen, old firmware: the knob just pages
            self._takeover = None if want else False
            if want and str(exc) != self._takeover_error:   # once per reason, not per press
                self._log(f"screen knob not taken over: {exc}")
            self._takeover_error = str(exc) if want else self._takeover_error

    def _do(self, actions: list[Action]) -> None:
        for action in actions:
            if action.kind in ("page_next", "page_prev"):
                perform(action, None, self._step_page)
            else:
                if self._sender is None:
                    self._sender = InputSender()
                perform(action, self._sender, self._step_page)
            self._on_action({"knob": action.knob, "gesture": action.gesture, "action": action.kind})
