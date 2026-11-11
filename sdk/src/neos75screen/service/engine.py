"""The service's core: owns the screen (real or fake), runs the widget runner on a thread, keeps the status
and publishes events to the API's subscribers.

The screen is opened only while the runner runs, so stopping the runner releases the HID device for other
tools. A missing screen is looked for every few seconds while the runner should run. A screen that goes away
(unplugged, the keyboard reconnected or left wired mode) ends the session at once; when it appears again it has
usually just booted, so it is opened APPEAR_WAIT_S later, after the stock boot animation.
"""
from __future__ import annotations

import collections
import io
import json
import os
import queue
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

from .. import knobmap
from ..live import library
from ..live.runner import WidgetRunner
from . import API_VERSION, catalog
from .paths import Settings, pages_file

RETRY_S = 3.0
APPEAR_WAIT_S = 5.0
TICK_S = 1.0
LOG_LINES = 300


class EventBus:
    """Fan-out of events to the API's stream subscribers; a slow subscriber loses old events, never blocks."""

    def __init__(self) -> None:
        self._subs: list[queue.Queue] = []
        self._lock = threading.Lock()

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=500)
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def publish(self, kind: str, data: dict | None = None) -> None:
        event = {"type": kind, "time": time.time(), **(data or {})}
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(event)
            except queue.Full:
                try:
                    q.get_nowait()
                    q.put_nowait(event)
                except (queue.Empty, queue.Full):
                    pass

    @property
    def subscribers(self) -> int:
        with self._lock:
            return len(self._subs)


class _Stop(Exception):
    """Raised inside the runner's loop to end it (the runner has no stop of its own)."""


class ServiceRunner(WidgetRunner):
    """WidgetRunner with a stop request, extra widget folders and a frame hook (live mirror, frame rate).

    Uses these runner internals: `_config_changed` (called first in every loop pass), `_show`, `_wake`,
    `_config_*`, `_stamp`, `frames`, `regions`, `index`, `pages`, `_keyboard`."""

    def __init__(self, *args, dirs=(), on_update: Callable | None = None, **kwargs) -> None:  # noqa: ANN001, ANN002, ANN003
        self.dirs = list(dirs)
        super().__init__(*args, **kwargs)
        self.stop_requested = threading.Event()
        self.on_update = on_update
        self.updates: collections.deque[float] = collections.deque(maxlen=240)
        self.last_frame = None

    def request_stop(self) -> None:
        self.stop_requested.set()
        self._wake.set()

    def _config_changed(self):  # noqa: ANN202
        if self.stop_requested.is_set():
            raise _Stop
        if self.config is None or time.monotonic() - self._config_at < 1.0:
            return None
        self._config_at = time.monotonic()
        stamp = self._stamp()
        if stamp == self._config_stamp:
            return None
        self._config_stamp = stamp
        try:
            pages = library.resolve(library.load(self.config), self.dirs)
        except (ValueError, OSError) as exc:
            self.log(f"pages not changed: {exc}")
            return None
        return pages if pages != self.pages else None

    def _show(self, frame, shown):  # noqa: ANN001, ANN202
        before = (self.frames, self.regions)
        ok = super()._show(frame, shown)
        if ok and (self.frames, self.regions) != before:
            self.updates.append(time.monotonic())
            self.last_frame = frame
            if self.on_update is not None:
                self.on_update(frame)
        elif ok and self.last_frame is None:
            self.last_frame = frame
        return ok

    def fps(self, window: float = 3.0) -> float:
        now = time.monotonic()
        recent = [t for t in list(self.updates) if now - t <= window]
        return round(len(recent) / window, 1)


def _load_fake_module():  # noqa: ANN202
    """sdk/tests/fake_device.py: the protocol-level fake screen the SDK's tests use."""
    import importlib.util
    path = library.ROOT / "sdk" / "tests" / "fake_device.py"
    spec = importlib.util.spec_from_file_location("neos75_fake_device", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"fake device not found: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module   # dataclasses look their module up while the file executes
    spec.loader.exec_module(module)
    return module


def fake_keyboard_raw(battery: int | None = None, charging: bool = False, link: int = 4) -> bytes:
    """Keyboard status bytes for the fake screen; battery None: nothing reported (the default, so a demo
    never shows a made-up battery level that could pass for the real keyboard's)."""
    raw = bytearray(26)
    if battery is not None:
        raw[13], raw[14], raw[16] = 2 if charging else 0, battery, link
    return bytes(raw)


class Engine:
    def __init__(self, settings: Settings | None = None, fake: bool = False, config: Path | None = None,
                 settings_path: Path | None = None, previewer=None, log_stdout: bool = True) -> None:  # noqa: ANN001
        self.settings = settings or Settings.load(settings_path)
        self.settings_path = settings_path
        self.fake = fake
        self.config = config or pages_file()
        self.bus = EventBus()
        self.previewer = previewer
        self.started_at = time.time()
        self.log_lines: collections.deque[dict] = collections.deque(maxlen=LOG_LINES)
        self._log_stdout = log_stdout
        self._lock = threading.RLock()
        self._want_run = False
        self._interrupt = threading.Event()   # set by stop(): ends the waits between connection attempts
        self._thread: threading.Thread | None = None
        self._runner: ServiceRunner | None = None
        self._screen = None
        self._fake_device = None
        self._fake_module = None
        self.runner_state = "stopped"      # stopped | starting | running | waiting | stopping | error
        self.runner_error: str | None = None
        self.device_state = "idle"          # idle | connecting | connected | absent | error
        self.device_error: str | None = None
        self.device_info: dict = {}
        self.visible: bool | None = None
        self.host_page: int | None = None
        self.keyboard: dict = {"reported": False, **({"simulated": True} if fake else {})}
        self.run_started: float | None = None
        self._frame_version = 0
        self._frame_event_at = 0.0
        self._mirror: tuple[int, bytes] | None = None
        self._last_tick: dict = {}
        self._selection_stamp = self._stamp()
        self._closed = threading.Event()
        self._ticker = threading.Thread(target=self._tick_loop, name="neos75d-tick", daemon=True)
        self._ticker.start()
        # The knobs' own actions (knobmap): next to the selection file, independent of the runner (the volume
        # knob and the presses work without the screen; screen-knob turns need the screen in use).
        self.knobs_path = self.config.parent / "knobs.json"
        self.knobs = knobmap.KnobMapper(knobmap.load(self.knobs_path), set_takeover=self._set_knob_takeover,
                                        step_page=self._step_page, on_action=self._on_knob_action, log=self.log)
        self.knobs.start()
        from .keyboard import KeyboardControl
        self.kb = KeyboardControl(self.config.parent, log=self.log)   # the keyboard itself (VIA), not the screen
        if self.fake:                                                 # a simulated keyboard next to the fake screen
            from .. import via
            if self._fake_module is None:
                self._fake_module = _load_fake_module()
            fake_via = self._fake_module.FakeVia()
            self.kb = KeyboardControl(self.config.parent / "fake-keyboard", log=self.log, simulated=True,
                                      opener=lambda: via.Keyboard(fake_via, self.kb.definition, "wired"))

    # --- logging / events ----------------------------------------------------------------------------

    def log(self, message: str) -> None:
        line = {"time": time.time(), "message": str(message)}
        self.log_lines.append(line)
        if self._log_stdout:
            print(time.strftime("%H:%M:%S"), message, flush=True)
        self.bus.publish("log", {"message": str(message)})

    # --- selection -----------------------------------------------------------------------------------

    def pages(self) -> list[str]:
        try:
            return library.load(self.config)
        except (ValueError, OSError) as exc:
            self.log(f"selection file unreadable: {exc}")
            return list(library.DEFAULT_PAGES)

    def set_pages(self, entries: list[str]) -> list[dict]:
        """Validate and save the selection (the runner follows the file within a second)."""
        if not isinstance(entries, list) or not all(isinstance(e, str) and e for e in entries):
            raise ValueError("pages must be a list of widget entries")
        library.save(self.config, entries, self.settings.dirs)
        self._selection_stamp = self._stamp()
        selection = catalog.selection(entries, self.settings.dirs)
        self.bus.publish("selection", {"pages": selection})
        return selection

    def selection(self) -> list[dict]:
        return catalog.selection(self.pages(), self.settings.dirs)

    def widgets(self) -> list[dict]:
        return catalog.widgets(self.settings.dirs)

    def _stamp(self) -> tuple[int, int] | None:
        try:
            st = self.config.stat()
        except OSError:
            return None
        return st.st_mtime_ns, st.st_size

    # --- settings ------------------------------------------------------------------------------------

    def update_settings(self, changes: dict) -> dict:
        if not isinstance(changes, dict):
            raise ValueError("settings must be an object")
        updated = Settings(**self.settings.to_dict()).update(changes)
        restart = any(getattr(updated, k) != getattr(self.settings, k) for k in ("interval", "quality", "extra_dirs"))
        self.settings = updated
        updated.save(self.settings_path)
        if self.previewer is not None:
            self.previewer.dirs = updated.dirs
        self.bus.publish("settings", {"settings": updated.to_dict()})
        if restart and self._want_run:
            self.restart()
        return updated.to_dict()

    # --- runner lifecycle ----------------------------------------------------------------------------

    def start(self) -> None:
        with self._lock:
            self._want_run = True
            self._interrupt.clear()
            if self._thread is not None and self._thread.is_alive():
                return
            self._set_runner("starting")
            self._thread = threading.Thread(target=self._run_loop, name="neos75d-runner", daemon=True)
            self._thread.start()

    def stop(self, wait: float = 15.0) -> None:
        with self._lock:
            self._want_run = False
            self._interrupt.set()
            runner, thread = self._runner, self._thread
        if runner is not None:
            self._set_runner("stopping")
            runner.request_stop()
        if thread is not None:
            thread.join(wait)
        self._set_runner("stopped")

    def restart(self) -> None:
        self.stop()
        self.start()

    def close(self) -> None:
        self.stop()
        self.knobs.stop()
        self._closed.set()
        if self.previewer is not None:
            self.previewer.close()

    def _set_runner(self, state: str, error: str | None = None) -> None:
        changed = (state, error) != (self.runner_state, self.runner_error)
        self.runner_state, self.runner_error = state, error
        if changed:
            self.bus.publish("runner", {"state": state, "error": error})

    def _set_device(self, state: str, error: str | None = None) -> None:
        changed = (state, error) != (self.device_state, self.device_error)
        self.device_state, self.device_error = state, error
        if changed:
            self.bus.publish("device", {"state": state, "error": error, "info": self.device_info})

    def _open_screen(self):  # noqa: ANN202
        from ..screen import Screen
        if not self.fake:
            return Screen.open()
        from ..transport import MockTransport
        if self._fake_module is None:
            self._fake_module = _load_fake_module()
        fake = self._fake_module.FakeScreen(profile="v2-knob", host_pages=True, knob_takeover=True,
                                            keyboard_status=fake_keyboard_raw(), host_timeout_ms=10000)
        transport = MockTransport(fake)
        fake.attach(transport)
        self._fake_device = fake
        return Screen.open(transport)

    def _describe_device(self, screen) -> dict:  # noqa: ANN001
        caps = screen.capabilities
        info = {"fake": self.fake, "realtime": caps.realtime,
                "capabilities": {k: getattr(caps, k, None) for k in
                                 ("proto_version", "patch_version", "knob", "host_pages", "telemetry", "region",
                                  "test_knob", "knob_takeover")}}
        if caps.realtime:
            info["capabilities"]["image"] = list(caps.image_size)
        try:
            version = screen.version()
            info["model"], info["build"] = version.model, version.build
        except Exception:  # noqa: BLE001 - the model string is cosmetic
            pass
        return info

    def _run_loop(self) -> None:
        from ..errors import DeviceLost, DeviceNotFound
        from ..transport import screen_present
        absent = False   # the last attempt found no screen
        while self._want_run:
            screen = None
            try:
                self._set_device("connecting")
                if absent and not self.fake and screen_present():
                    self.log(f"screen found, connecting in {APPEAR_WAIT_S:.0f} s")
                    if self._interrupt.wait(APPEAR_WAIT_S):
                        break
                screen = self._open_screen()
                absent = False
                self._screen = screen
                self.device_info = self._describe_device(screen)
                if not screen.realtime:
                    raise RuntimeError("stock firmware: realtime display unavailable")
                self._set_device("connected")
                self.log(f"screen connected ({'fake' if self.fake else self.device_info.get('model', 'device')})")
                screen.on_page(self._on_page)
                screen.on_visibility(self._on_visibility)
                screen.on_knob(self._on_knob)
                pages = library.resolve(self.pages(), self.settings.dirs)
                runner = ServiceRunner(pages, screen=screen, interval=self.settings.interval,
                                       quality=self.settings.quality, log=self.log, config=self.config,
                                       dirs=self.settings.dirs, on_update=self._on_update)
                if not self.fake and screen.capabilities.knob and not runner.paged \
                        and not self.knobs.machine.capture().screen_press:
                    try:
                        screen.capture_knob_press()   # as `neos75 widgets`: a press switches pages
                    except OSError:
                        pass
                with self._lock:
                    self._runner = runner
                self.knobs.screen_session()   # a new screen session: send the knob takeover again
                if not self._want_run:
                    break
                self.run_started = time.time()
                self._set_runner("running")
                runner.run(None)
                if not self._want_run:
                    break
                raise RuntimeError("runner ended")
            except _Stop:
                break
            except DeviceLost as exc:
                absent = True
                self.log(f"screen disconnected: {exc.cause}")
                self._set_device("absent", str(exc))
                self._set_runner("waiting", "键盘屏幕已断开")
            except DeviceNotFound as exc:
                absent = True
                self._set_device("absent", str(exc))
                self._set_runner("waiting", "键盘屏幕未连接")
            except Exception as exc:  # noqa: BLE001 - reported, then retried
                self.log(f"runner error: {exc!r}")
                if self._screen is not None:
                    self._set_device("error", str(exc))
                self._set_runner("waiting" if self._want_run else "error", str(exc))
            finally:
                with self._lock:
                    self._runner = None
                self._screen = None
                self.run_started = None
                if screen is not None:
                    try:
                        screen.close()
                    except Exception as exc:  # noqa: BLE001
                        self.log(f"close: {exc}")
                if self._fake_device is not None:
                    self._fake_device.stop()
                    self._fake_device = None
            if self._want_run:
                self._interrupt.wait(RETRY_S)
        self.visible, self.host_page = None, None
        self._set_device("idle")
        self._set_runner("stopped")

    # --- screen events (dispatcher thread: keep short) -----------------------------------------------

    def _on_page(self, event) -> None:  # noqa: ANN001
        self.host_page = event.page
        self.bus.publish("page", {"index": event.page, "count": event.count,
                                  "label": library.page_label(event.page)})

    def _on_visibility(self, event) -> None:  # noqa: ANN001
        self.visible = bool(event.visible)
        self.bus.publish("visibility", {"visible": self.visible, "reason": event.reason})

    def _on_knob(self, event) -> None:  # noqa: ANN001
        taken = bool(getattr(event, "taken", False))
        self.bus.publish("knob", {"direction": event.direction, "knob": getattr(event, "knob", "screen"),
                                  "taken": taken})
        if taken and getattr(event, "knob", "screen") == "screen":
            self.knobs.screen_turn(event.direction)

    # --- knob actions (knobmap) ----------------------------------------------------------------------

    def knob_settings(self) -> dict:
        caps = self.device_info.get("capabilities", {}) if self.device_state == "connected" else {}
        return {"config": self.knobs.config, "catalog": knobmap.catalog(),
                "status": {**self.knobs.status(), "connected": self.device_state == "connected",
                           "firmware_takeover": caps.get("knob_takeover"), "pc_input": sys.platform == "win32"}}

    def set_knob_settings(self, config: dict) -> dict:
        saved = knobmap.save(self.knobs_path, config)
        self.knobs.configure(saved)
        self.bus.publish("knobs", {"config": saved})
        return self.knob_settings()

    def _set_knob_takeover(self, on: bool) -> None:
        """Mapper worker thread: switch the firmware's takeover of the screen knob's turns."""
        screen = self._screen
        if screen is None:
            if on:
                raise RuntimeError("键盘屏幕未连接")
            return
        screen.set_knob_takeover(on)

    def _step_page(self, direction: int) -> None:
        """Mapper worker thread: the "screen next/previous page" actions."""
        if self.fake:
            fake = self._fake_device
            if fake is None:
                raise RuntimeError("the fake screen is not running")
            fake.knob(direction, from_host=True)
            return
        screen = self._screen
        if screen is None:
            raise RuntimeError("键盘屏幕未连接")
        screen.inject_knob(direction)

    def _on_knob_action(self, info: dict) -> None:
        self.bus.publish("knob_action", info)

    MIRROR_HZ = 10   # "frame" events for the app's live mirror (the 1 s tick alone made it a 1 fps view)

    def _on_update(self, frame) -> None:  # noqa: ANN001
        self._frame_version += 1
        now = time.monotonic()
        if now - self._frame_event_at >= 1 / self.MIRROR_HZ and self.bus.subscribers:
            self._frame_event_at = now
            self.bus.publish("frame", {"frame_version": self._frame_version})

    def warm_previews(self) -> int:
        """Render the previews the app shows (the knob pages with their labels, the library cards as Page 7)
        in the background, so opening the app finds them cached."""
        if self.previewer is None:
            return 0
        items = [(p["entry"], p["label"]) for p in self.selection()]
        items += [(w["name"], "Page 7") for w in self.widgets()]
        return self.previewer.warm(items, self.preview_data())

    # --- fake device controls ------------------------------------------------------------------------

    def turn_knob(self, direction: int) -> None:
        """Turn the screen knob one step (+1 / -1), as a person turning it would; the fake device can also
        press it (0). On the keyboard this needs firmware whose HELLO has `test_knob` (patch 3, both builds
        from 2710bdd4 on); the step goes through the screen's own key queue, so stock pages save the page as
        after a real turn."""
        if direction not in (-1, 0, 1):
            raise ValueError("direction must be -1, 0 or 1")
        if not self.fake:
            screen = self._screen
            if screen is None:
                raise RuntimeError("键盘屏幕未连接：先开始显示")
            if direction == 0:
                raise PermissionError("从电脑只能转动旋钮，按下只有模拟设备支持")
            if not getattr(screen.capabilities, "test_knob", False):
                raise PermissionError("屏幕固件不支持从电脑转动旋钮，需要刷入新版固件")
            from ..errors import Neos75Error
            try:
                screen.inject_knob(direction)
            except Neos75Error as exc:
                raise RuntimeError(f"旋钮没有转动：{exc}") from exc
            return
        fake = self._fake_device
        if fake is None:
            raise RuntimeError("the fake screen is not running; start the runner first")
        if direction not in (-1, 0, 1):
            raise ValueError("direction must be -1, 0 or 1")
        fake.knob(direction)

    def set_fake_keyboard(self, battery: int, charging: bool, link: int) -> None:
        if not self.fake or self._fake_device is None:
            raise PermissionError("fake device only")
        self._fake_device.keyboard_status = fake_keyboard_raw(battery, charging, link)

    # --- status --------------------------------------------------------------------------------------

    def mirror_jpeg(self) -> bytes | None:
        """The frame the screen shows now (as rendered), JPEG; None before the first frame."""
        runner = self._runner
        frame = runner.last_frame if runner is not None else None
        if frame is None:
            return self._mirror[1] if self._mirror else None
        version = self._frame_version
        if self._mirror is None or self._mirror[0] != version:
            out = io.BytesIO()
            frame.save(out, "JPEG", quality=88)
            self._mirror = (version, out.getvalue())
        return self._mirror[1]

    def _keyboard(self) -> dict:
        runner = self._runner
        raw = dict(runner._keyboard) if runner is not None else {}
        simulated = {"simulated": True} if self.fake else {}
        if not raw:
            if self.keyboard.get("reported"):
                return dict(self.keyboard, live=False)
            return {"reported": False, **simulated}
        battery, link = raw.get("kb_battery"), raw.get("kb_link")
        if battery is None:
            return {"reported": False, "live": True, **simulated}
        connection = "bluetooth" if link and 1 <= link <= 3 else {4: "2.4g", 5: "wired"}.get(link, "unknown")
        return {"reported": True, "live": True, "battery": battery, "charging": bool(raw.get("kb_charging")),
                "link": link, "connection": connection, **simulated}

    def preview_data(self) -> dict:
        """Keyboard values for previews: the real keyboard's when known, else none (the pages' 86% sample
        must not look like a reading)."""
        k = self.keyboard
        if k.get("reported") and not k.get("simulated"):
            return {"kb_battery": k["battery"], "kb_charging": k["charging"], "kb_link": k["link"]}
        return {"kb_battery": None}

    def status(self, port: int | None = None) -> dict:
        runner = self._runner
        pages = self.selection()
        index = runner.index if runner is not None else None
        return {
            "api": API_VERSION,
            "service": {"pid": os.getpid(), "port": port, "fake": self.fake,
                        "uptime": round(time.time() - self.started_at), "clients": self.bus.subscribers},
            "device": {"state": self.device_state, "error": self.device_error, "fake": self.fake, **self.device_info},
            "keyboard": self.keyboard,
            "runner": {
                "state": self.runner_state, "error": self.runner_error,
                "fps": runner.fps() if runner is not None else 0.0,
                "frames": runner.frames if runner is not None else 0,
                "regions": runner.regions if runner is not None else 0,
                "paged": runner.paged if runner is not None else None,
                "page_index": index,
                "page": pages[index]["entry"] if index is not None and index < len(pages) else None,
                "label": library.page_label(index) if index is not None else None,
                "visible": self.visible,
                "since": self.run_started,
            },
            "pages": pages,
            "settings": self.settings.to_dict(),
        }

    def _tick_loop(self) -> None:
        """Once a second: keyboard changes, selection file edited by someone else, and a frame-rate tick."""
        while not self._closed.wait(TICK_S):
            try:
                keyboard = self._keyboard()
                if keyboard != self.keyboard:
                    self.keyboard = keyboard
                    self.bus.publish("keyboard", keyboard)
                stamp = self._stamp()
                if stamp != self._selection_stamp:
                    self._selection_stamp = stamp
                    self.bus.publish("selection", {"pages": self.selection()})
                runner = self._runner
                tick = {"fps": runner.fps() if runner else 0.0, "frames": runner.frames if runner else 0,
                        "page_index": runner.index if runner else None, "visible": self.visible,
                        "frame_version": self._frame_version}
                if tick != self._last_tick:
                    self._last_tick = tick
                    self.bus.publish("tick", tick)
            except Exception as exc:  # noqa: BLE001 - the ticker must survive
                self.log(f"tick: {exc!r}")


def dumps(data: object) -> bytes:
    return json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
