"""High-level API: the `Screen` class."""
from __future__ import annotations

import logging
import struct
import threading
import time
import warnings
from collections import deque
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field, replace
from datetime import datetime
from types import TracebackType
from typing import Union

from . import constants as c
from . import protocol as p
from .errors import FlashWriteWarning, FrameRejected, Neos75Error, ProtocolError, StockFirmware, Timeout
from .events import Event, EventHub, Keepalive, KnobCallback, PageCallback, VisibilityCallback
from .feed import FrameFeed
from .imaging import (Fit, ImageSource, Subsampling, encode_jpeg, inspect_jpeg, prepare_image,
                      strip_app_segments, to_rgb565)
from .protocol import DeviceInfo, FrameDone, Hello, KnobEvent, PageEvent, Status, VisibilityEvent
from .transport import Transport, open_transport

Frame = Union[ImageSource, bytes]  # an image to encode, or ready JPEG bytes

_log = logging.getLogger(__name__)
_FEED_POLL_S = 0.05          # how often stream() checks visibility while the frame source is busy
_KEEPALIVE_TICK_MAX_S = 0.5
_KNOB_RETRIES = 3            # show(): resend a frame the knob interrupted while the slot is visible
_STALE_EVENT_GRACE_S = 0.5  # how long a start's ACK may trail a stale frame-done
_HIDDEN_EVENT_WAIT_S = 0.25  # a 05 frame-done precedes the device's hidden event by one report


@dataclass(frozen=True)
class Timeouts:
    """Seconds to wait for each kind of answer."""

    reply: float = c.REPLY_TIMEOUT_S
    hello: float = c.HELLO_TIMEOUT_S
    ack: float = c.ACK_TIMEOUT_S
    frame_done: float = c.FRAME_DONE_TIMEOUT_S


@dataclass(frozen=True)
class VersionInfo:
    model: str
    build: str


@dataclass(frozen=True)
class FrameResult:
    """Outcome of one frame. Without a frame-done event before the timeout, `status` is None and
    `device_status` holds the STATUS read afterwards. `hidden` is True when the knob hid the realtime slot
    (proto 2): the frame was not sent, or the device answered 05 because the slot was hidden meanwhile."""

    status: int | None
    seq: int | None = None
    decode_ms: int | None = None
    device_status: Status | None = None
    hidden: bool = False

    @property
    def shown(self) -> bool:
        return self.status == c.FRAME_SHOWN

    @property
    def timed_out(self) -> bool:
        return self.status is None and not self.hidden


@dataclass
class StreamStats:
    sent: int = 0
    shown: int = 0
    dropped: int = 0     # status 04 (busy)
    timeouts: int = 0    # no frame-done event; STATUS was read instead
    hidden: int = 0      # status 05 because the knob hid the slot during the transfer (resent later)


@dataclass
class _StreamRun:
    depth: int
    stats: StreamStats = field(default_factory=StreamStats)
    in_flight: deque[int] = field(default_factory=deque)  # hidden-epoch at send time, one per frame
    resend: bool = False


class Screen:
    """The keyboard screen. Use `Screen.open()` (or the context manager) rather than the constructor.

    On proto 2 devices the realtime page is one slot of the knob's page cycle: the slot stays armed while
    the session is alive (a background keepalive sends STATUS when idle), `visible` follows the knob, and
    knob/visibility events reach `on_knob`, `on_visibility` and `events()`.
    """

    def __init__(self, transport: Transport, timeouts: Timeouts | None = None, keepalive: bool = True) -> None:
        self._t = transport
        self._timeouts = timeouts or Timeouts()
        self._events: deque[FrameDone] = deque()
        self._hello: Hello | None = None
        self._entered = False
        self._closed = False
        self._io = threading.RLock()            # one writer at a time; held for whole BA streams
        self._state = threading.Condition()     # guards the visibility state below
        self._visible = False
        self._armed = False
        self.page = 0  # current host page (`set_pages`), updated from PageEvents
        self.cache_slot_bytes = 0  # page cache JPEG limit per page (`set_pages`, page cache firmware)
        # Host page the next frames show (page-tag firmware): the device drops them once the knob has left
        # that page. None: untagged. Set by whoever renders per page (the widget runner).
        self.frame_page: int | None = None
        # ACKs a region-frame upload may have outstanding (1: wait for every start/block ACK). Waiting only
        # paces the host (the device handles packets in order); it is what protects the device's 32-report
        # input ring while the device redraws. A full frame redraws the whole screen after its frame-done
        # (tens of ms, longer than the ring lasts at 1 report/ms), so a full frame and the frame after one
        # always wait (hardware 2026-11-08: window 2 after a full frame lost the start in a ring overflow).
        self.ack_window = 1
        self._after_full = True
        self._shown_epoch = 0                   # +1 whenever the slot becomes visible
        self._hidden_epoch = 0                  # +1 whenever it becomes hidden
        self._last_activity = time.monotonic()  # last A5 3B request or BA packet
        self._use_keepalive = keepalive
        self._keepalive: Keepalive | None = None
        self._hub = EventHub()
        self._press_hook = None
        self._takeover = False                  # wanted knob takeover; the device drops it with the session
        transport.set_report_filter(self._on_report)

    @classmethod
    def open(cls, transport: Transport | None = None, timeouts: Timeouts | None = None,
             keepalive: bool = True) -> Screen:
        """Open the screen (or wrap `transport`) and probe it with HELLO. `keepalive=False` disables the
        proto 2 keepalive (the device then disarms the slot after host_timeout_ms without traffic)."""
        transport = transport or open_transport()
        screen = cls(transport, timeouts, keepalive)
        try:
            screen.hello()
        except BaseException:
            screen._hub.close()
            transport.close()
            raise
        return screen

    def close(self, leave: bool = True) -> None:
        """Leave realtime mode (unless `leave` is False, which keeps the last frame) and release the device.
        Safe to call twice. The keepalive and event threads stop before the device is released."""
        if self._closed:
            return
        self._stop_keepalive()
        if self._press_hook is not None:
            self._press_hook.stop()
            self._press_hook = None
        try:
            if leave and self._entered:
                self.leave()
        finally:
            self._closed = True
            with self._state:
                self._state.notify_all()
            self._hub.close()
            self._t.close()

    def __enter__(self) -> Screen:
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None,
                 tb: TracebackType | None) -> None:
        self.close()

    # --- report plumbing ----------------------------------------------------------------------------

    def _on_report(self, report: bytes) -> bool:
        """Runs on the receiving thread for every input report. Consumes knob and visibility events and
        peeks at STATUS replies; everything else goes to the reply queue untouched."""
        event = p.parse_event(report)
        if event is not None:
            if isinstance(event, VisibilityEvent):
                self._apply_visibility(event)
            elif isinstance(event, PageEvent):
                self.page = event.page
            self._hub.publish(event)
            return True
        hello = self._hello
        if hello is not None and hello.knob and p.is_realtime_report(report, c.RT_STATUS) \
                and not p.realtime_not_understood(report):
            status = p.parse_status(report, hello.proto_version)
            self._set_state(bool(status.visible), bool(status.armed))
        return False

    def _apply_visibility(self, event: VisibilityEvent) -> None:
        if event.visible:
            armed = True
        elif event.reason in (c.VISIBILITY_REASON_HOST, c.VISIBILITY_REASON_TIMEOUT):
            armed = False
        else:
            armed = self._armed
        self._set_state(event.visible, armed)

    def _set_state(self, visible: bool, armed: bool) -> None:
        with self._state:
            if visible and not self._visible:
                self._shown_epoch += 1
            if not visible and self._visible:
                self._hidden_epoch += 1
            self._visible, self._armed = visible, armed
            if not armed and self._knob():
                self._entered = False  # the device ended the session; the next frame enters again
            self._state.notify_all()

    def _knob(self) -> bool:
        return self._hello is not None and self._hello.knob

    def _send(self, report: bytes) -> None:
        with self._io:
            self._t.write(report)
            if report[0] == c.BA or (report[0] == c.A5 and report[1] == c.CMD_REALTIME):
                self._last_activity = time.monotonic()

    def _route(self, report: bytes) -> None:
        """Keep frame-done events that arrive while waiting for something else; drop other reports."""
        if p.is_frame_done(report):
            self._events.append(p.parse_frame_done(report))

    def _drain(self) -> None:
        while (report := self._t.read_nowait()) is not None:
            self._route(report)

    def _wait(self, match: Callable[[bytes], bool], timeout: float) -> bytes | None:
        deadline = time.monotonic() + timeout
        while (left := deadline - time.monotonic()) > 0:
            report = self._t.read(left)
            if report is None:
                return None
            if match(report):
                return report
            self._route(report)
        return None

    def _request(self, report: bytes, match: Callable[[bytes], bool], what: str) -> bytes:
        with self._io:
            self._drain()
            self._send(report)
            reply = self._wait(match, self._timeouts.reply)
        if reply is None:
            raise Timeout(f"no reply to {what}")
        return reply

    # --- realtime control ---------------------------------------------------------------------------

    def hello(self) -> Hello:
        """Probe for the patched firmware. Stock firmware (no magic in time) gives `realtime=False`."""
        with self._io:
            self._drain()
            self._send(p.build_realtime(c.RT_HELLO))
            reply = self._wait(lambda r: p.is_realtime_report(r, c.RT_HELLO), self._timeouts.hello)
        hello = p.parse_hello(reply) if reply is not None else None
        if hello is None:
            hello = Hello(realtime=False)
        elif not hello.realtime:
            supported = ", ".join(f"v{v}" for v in sorted(c.PROTO_VERSIONS))
            warnings.warn(f"screen speaks realtime protocol v{hello.proto_version}; this SDK supports "
                          f"{supported}, so realtime is disabled", stacklevel=2)
        self._hello = hello
        return hello

    @property
    def capabilities(self) -> Hello:
        return self._hello if self._hello is not None else self.hello()

    @property
    def realtime(self) -> bool:
        return self.capabilities.realtime

    @property
    def visible(self) -> bool:
        """True while the realtime page is on screen (proto 1: while entered)."""
        return self._visible if self.capabilities.knob else self._entered

    @property
    def armed(self) -> bool:
        """True while the realtime slot is in the knob cycle (proto 1: while entered)."""
        return self._armed if self.capabilities.knob else self._entered

    def on_knob(self, callback: KnobCallback) -> None:
        """Call `callback(KnobEvent)` for every knob event (proto 2; never called on proto 1). Callbacks run
        on a background thread: keep them short and do not call Screen methods from them (set a flag)."""
        self._hub.on_knob(callback)

    def on_visibility(self, callback: VisibilityCallback) -> None:
        """Call `callback(VisibilityEvent)` when the realtime slot is shown or hidden (proto 2). Same rules
        as `on_knob`."""
        self._hub.on_visibility(callback)

    def on_page(self, callback: PageCallback) -> None:
        """Call `callback(PageEvent)` when the screen knob moves to another host page (`set_pages`). Same
        rules as `on_knob`."""
        self._hub.on_page(callback)

    def set_pages(self, count: int) -> int:
        """Make the realtime slot `count` pages (1..8) in the screen knob's cycle: a turn moves between
        them like between stock pages, and only a turn past the first or last leaves for a stock page.
        Clockwise entry lands on page 0, counter-clockwise on the last. Each move is a `PageEvent`; send
        that page's frame. Returns the current page. Needs `capabilities.host_pages`; 1 is the default."""
        if not 1 <= count <= c.MAX_HOST_PAGES:
            raise ValueError(f"count must be 1..{c.MAX_HOST_PAGES}")
        if not self._require_realtime().host_pages:
            raise ProtocolError("this firmware has a single realtime page")
        reply = self._request(p.build_realtime(c.RT_SET_PAGES, bytes([count])),
                              lambda r: p.is_realtime_report(r, c.RT_SET_PAGES), "3B 06")
        if p.realtime_not_understood(reply):
            raise ProtocolError(f"device refused {count} pages")
        self.page = reply[c.RT_FIELDS_OFFSET + 1]
        if self._hello is not None and self._hello.page_cache:
            self.cache_slot_bytes = struct.unpack_from("<I", reply, c.RT_FIELDS_OFFSET + 3)[0]
        return self.page

    def inject_knob(self, direction: int) -> None:
        """Turn the screen knob by one step from the PC (`capabilities.test_knob`; patch 3 dev builds, release
        builds from 2710bdd4 on), for the desktop app, the CLI and automated hardware tests. The step enters the screen's stock key queue exactly like a physical turn, so the
        page code, the realtime slot and the events behave as for a person turning it (a step that lands
        on a stock page leads to the stock page save about 2 s later, as a real turn does). Not retried."""
        if direction not in (1, -1):
            raise ValueError("direction must be +1 or -1")
        hello = self._require_realtime()
        if not hello.test_knob:
            raise ProtocolError("this firmware cannot turn the knob from the PC")
        code = c.KNOB_NEXT if direction == 1 else c.KNOB_PREVIOUS
        reply = self._request(p.build_realtime(c.RT_INJECT_KNOB, bytes([code])),
                              lambda r: p.is_realtime_report(r, c.RT_INJECT_KNOB), "3B 30")
        if p.realtime_not_understood(reply):
            raise ProtocolError("device refused the injected knob step")

    def set_knob_takeover(self, on: bool) -> bool:
        """Take the screen knob's turns over (`capabilities.knob_takeover`): while on, a turn moves no page and
        is only reported (`KnobEvent`, source "screen"), so the caller can map it to its own action;
        `inject_knob` steps still page as usual (and are not reported). RAM only, no flash write. The device
        ends takeover with the session; it is sent again after every `enter()` while wanted. Returns whether
        the device has it on now."""
        self._takeover = bool(on)
        if not self._require_realtime().knob_takeover:
            if on:
                raise ProtocolError("this firmware cannot take the knob over")
            return False
        if not self._entered:
            return False
        return self._send_takeover()

    def _send_takeover(self) -> bool:
        arg = 1 if self._takeover else 0
        reply = self._request(p.build_realtime(c.RT_KNOB_TAKEOVER, bytes([arg])),
                              lambda r: p.is_realtime_report(r, c.RT_KNOB_TAKEOVER), "3B 07")
        if p.realtime_not_understood(reply):
            return False
        return bool(reply[c.RT_FIELDS_OFFSET + 1])

    @property
    def knob_takeover(self) -> bool:
        """The takeover asked for with `set_knob_takeover`."""
        return self._takeover

    def capture_knob(self, screen_press: bool = True, block_screen_press: bool = True, volume_knob: bool = False,
                     devices: Iterable[tuple[int, int]] | None = None, tap_ms: int | None = None) -> None:
        """Pick knob input up on the PC (Windows only) and report it as `KnobEvent(direction, knob,
        source="pc")` to `on_knob` callbacks and `events()`.

        The Neos75 has two knobs. Screen-knob turns go to the screen only (the firmware reports them as
        `knob="screen"` events); its press reaches the PC as the Calculator key. The volume knob reaches the PC
        as Volume Up/Down and Mute. While this Screen is open:
        - `screen_press`: the screen knob's press becomes `KnobEvent(0, "screen", "pc")`; with
          `block_screen_press` Calculator does not open.
        - `volume_knob`: the volume knob is taken over: its turns become `KnobEvent(+-1, "volume", "pc")` and no
          longer change the volume, its press becomes `KnobEvent(0, "volume", "pc")` and no longer mutes.
          The keyboard's Fn volume shortcuts still work (told apart by how long the key is held; they take
          effect on release); an Fn mute shortcut on the Neos75, if any, is taken too.
        Only keys from the Neos75 keyboard (USB VID/PID, `devices`, default the wired keyboard) are handled;
        other devices' media keys are untouched. Ends with `close()`."""
        if self._press_hook is not None:
            return
        from . import knobpress
        hook = knobpress.KnobInputHook(lambda knob, direction: self._hub.publish(KnobEvent(direction, knob, "pc")),
                                       screen_press=screen_press, block_screen_press=block_screen_press,
                                       volume_turns=volume_knob, volume_press=volume_knob,
                                       devices=knobpress.DEFAULT_DEVICES if devices is None else devices,
                                       tap_ms=knobpress.DEFAULT_TAP_MS if tap_ms is None else tap_ms)
        hook.start()
        self._press_hook = hook

    def capture_knob_press(self, block: bool = True) -> None:
        """Only the screen knob's press (`capture_knob` with its defaults and `block_screen_press=block`)."""
        self.capture_knob(screen_press=True, block_screen_press=block)

    def events(self, timeout: float | None = None) -> Iterator[Event]:
        """Yield knob and visibility events as they arrive (buffered; beyond 256 the oldest are dropped).
        Stops after `timeout` seconds without an event, or when the screen is closed."""
        return self._hub.iterate(timeout)

    def _require_realtime(self) -> Hello:
        hello = self.capabilities
        if not hello.realtime:
            raise StockFirmware("stock firmware: realtime display is unavailable (stock helpers still work)")
        return hello

    def _realtime_request(self, sub: int) -> bytes:
        self._require_realtime()
        build, match = p.build_realtime(sub), (lambda r: p.is_realtime_report(r, sub))
        try:
            reply = self._request(build, match, f"3B {sub:02X}")
        except Timeout:
            # All realtime requests are idempotent. While fast knob turns stall the device's GUI task its
            # input queue can overflow and drop a request; ask once more.
            if not self._knob():
                raise
            reply = self._request(build, match, f"3B {sub:02X}")
        if p.realtime_not_understood(reply):
            raise ProtocolError(f"device did not understand realtime request 0x{sub:02X}")
        return reply

    def enter(self) -> None:
        """Show the realtime page and (proto 2) arm its knob slot. No flash write."""
        status = p.parse_result_status(self._realtime_request(c.RT_ENTER))
        if status != c.ENTER_OK:
            raise ProtocolError(f"ENTER failed with status 0x{status:02X}")
        self._set_state(True, True)
        self._entered = True
        self._start_keepalive()
        if self._takeover and self.capabilities.knob_takeover:
            try:
                self._send_takeover()
            except Neos75Error as exc:
                _log.warning("knob takeover not restored: %s", exc)

    def leave(self) -> None:
        """Return to the page shown before the realtime page and disarm its slot. No flash write."""
        self._stop_keepalive()
        status = p.parse_result_status(self._realtime_request(c.RT_LEAVE))
        self._entered = False
        self._set_state(False, False)
        if status != c.ENTER_OK:
            raise ProtocolError(f"LEAVE failed with status 0x{status:02X}")

    def status(self) -> Status:
        """Device counters; proto 2 adds `visible` and `armed`."""
        return p.parse_status(self._realtime_request(c.RT_STATUS), self.capabilities.proto_version)

    def keyboard(self) -> p.KeyboardStatus:
        """Battery, charging and connection of the keyboard, as last reported to the screen (the values
        the stock status bar draws). Read-only; needs firmware with `capabilities.telemetry`."""
        if not self._require_realtime().telemetry:
            raise ProtocolError("this firmware does not report keyboard status")
        return p.parse_keyboard_status(self._realtime_request(c.RT_TELEMETRY))

    def _ensure_entered(self) -> None:
        if not self._entered:
            self.enter()

    # --- keepalive (proto 2) ------------------------------------------------------------------------

    def _keepalive_period(self) -> float:
        return self.capabilities.host_timeout_ms / 1000 / c.KEEPALIVE_DIVISOR

    def _start_keepalive(self) -> None:
        if self._use_keepalive and self._knob() and self._keepalive is None:
            interval = min(self._keepalive_period() / 4, _KEEPALIVE_TICK_MAX_S)
            self._keepalive = Keepalive(self._keepalive_tick, interval)
            self._keepalive.start()

    def _stop_keepalive(self) -> None:
        if self._keepalive is not None:
            self._keepalive.stop()
            self._keepalive = None

    def _keepalive_tick(self) -> None:
        """Send STATUS when nothing realtime was sent for host_timeout_ms / 3. Skipped while another thread
        holds the I/O lock (a request or a BA stream in progress, which is activity anyway)."""
        if self._closed or not self._entered:
            return
        if time.monotonic() - self._last_activity < self._keepalive_period():
            return
        if not self._io.acquire(blocking=False):
            return
        try:
            if not self._closed and self._entered:
                self._send(p.build_realtime(c.RT_STATUS))  # the reply only refreshes visible/armed
        except (Neos75Error, OSError) as exc:
            _log.warning("keepalive failed: %s", exc)
        finally:
            self._io.release()

    # --- frames -------------------------------------------------------------------------------------

    def encode(self, image: ImageSource, fit: Fit = "contain", quality: int = 85,
               max_bytes: int | None = None, subsampling: Subsampling = "4:2:0") -> bytes:
        """Fit `image` to the realtime image size and encode it as a JPEG that fits one frame."""
        hello = self._require_realtime()
        budget = hello.max_frame_bytes - c.JPEG_STREAM_HEADER_SIZE
        img = prepare_image(image, hello.image_size, fit)
        return encode_jpeg(img, quality, subsampling, budget if max_bytes is None else min(max_bytes, budget))

    def _jpeg_stream(self, jpeg: bytes) -> bytes:
        hello = self._require_realtime()
        if not hello.supports_jpeg:
            raise ProtocolError("the device does not accept JPEG frames")
        jpeg = strip_app_segments(jpeg)
        info = inspect_jpeg(jpeg)
        if info.width > hello.image_w or info.height > hello.image_h:
            raise ValueError(f"JPEG is {info.width}x{info.height}; the maximum is {hello.image_w}x{hello.image_h}")
        stream = p.build_jpeg_stream(jpeg, info.width, info.height)
        if len(stream) > hello.max_frame_bytes:
            raise ValueError(f"frame of {len(stream)} bytes exceeds max_frame_bytes {hello.max_frame_bytes}")
        return stream

    def _send_stream(self, stream_type: int, stream: bytes, persistent: bool = False) -> None:
        region = stream[:2] == c.REGION_STREAM_MAGIC
        window = self.ack_window if region and not self._after_full else 1
        self._after_full = not region
        self._send_stream_windowed(stream_type, stream, persistent, window)

    def _send_stream_windowed(self, stream_type: int, stream: bytes, persistent: bool, window: int) -> None:
        """Send one BA stream. On proto 2 the device ends an upload early with frame-done 05 when the knob
        hides the slot; the remaining packets would be ignored and no further ACK would come, so stop. The
        05 stays queued for _collect. (Other frame-done events may belong to an earlier, pipelined frame.)"""
        with self._io, self._t.stream_session(persistent):
            self._drain()
            known = self._aborts()  # anything queued before our start is not about this stream
            epochs = (self._hidden_epoch, self._shown_epoch)
            # ACK window: up to `ack_window` ACKs may be outstanding. The device processes packets in order
            # whether or not the host waits, so waiting only paces the host; with a window of 1 every start
            # and block waits (~10 ms each, the device answers once per 10 ms GUI tick).
            pending: list[bool] = []          # outstanding ACKs, True for the start packet's
            tag = self.frame_page if (stream_type in c.REALTIME_STREAM_TYPES and stream[:2] != c.CACHE_STREAM_MAGIC
                                      and self._hello is not None and self._hello.page_tag) else None
            for index, packet in enumerate(p.split_ba_packets(stream_type, stream, tag)):
                if packet.needs_ack and not pending:
                    self._drain()             # stale replies only while nothing of ours is in flight
                if self._aborts() > known:
                    return
                self._send(packet.report)
                if not packet.needs_ack:
                    continue
                pending.append(index == 0)
                while len(pending) >= window:
                    if not self._ack(pending.pop(0), epochs):
                        return
            while pending:
                if not self._ack(pending.pop(0), epochs):
                    return

    def _ack(self, first: bool, epochs: tuple[int, int]) -> bool:
        try:
            return self._wait_ack(first=first)
        except Timeout:
            # Patch 3 drops a stream silently when the knob shows the slot again mid-upload. If the
            # visibility changed during this upload, report the frame as hidden instead of failing.
            if self._knob() and (self._hidden_epoch, self._shown_epoch) != epochs:
                self._events.append(p.FrameDone(c.FRAME_NOT_REALTIME, 0, 0))
                return False
            raise

    def _ends_upload(self, done: FrameDone) -> bool:
        """A frame-done that arrives during an upload ends it (the rest would be ignored, with no ACK):
        05 on proto 2 when the knob hides the slot; with a single device buffer, any status (e.g. 04 when
        the device's input queue overflowed), since no earlier frame can still be pending."""
        if self._hello is not None and self._hello.buffers <= 1:
            return True
        return self._knob() and done.status == c.FRAME_NOT_REALTIME

    def _aborts(self) -> int:
        return sum(1 for done in self._events if self._ends_upload(done))

    def _wait_ack(self, first: bool = False) -> bool:
        """True on ACK; False if the device ended the upload early (the frame-done is queued for _collect)."""
        def ends_upload(r: bytes) -> bool:
            return p.is_frame_done(r) and self._ends_upload(p.parse_frame_done(r))
        reply = self._wait(lambda r: p.parse_ack(r) is not None or ends_upload(r), self._timeouts.ack)
        if reply is None:
            raise Timeout("no ACK for a BA block")
        if ends_upload(reply):
            done = p.parse_frame_done(reply)
            if first and self._knob():
                # A frame-done before the start's ACK is either the answer to this start or a late event of
                # an earlier stream (seen on patch 3 c7d2a4c1 after an input-queue overflow). If the start's
                # ACK follows shortly, the event was stale and the upload goes on.
                later = self._wait(lambda r: p.parse_ack(r) is not None, _STALE_EVENT_GRACE_S)
                if later is not None:
                    ack = p.parse_ack(later)
                    if ack is not None and not ack.ok:
                        raise ProtocolError(f"BA block rejected (code 0x{ack.code:02X})")
                    _log.debug("ignored a stale frame-done %s before the start ACK", done)
                    return True
            self._events.append(done)
            return False
        ack = p.parse_ack(reply)
        if ack is not None and not ack.ok:
            raise ProtocolError(f"BA block rejected (code 0x{ack.code:02X})")
        return True

    def _collect(self) -> FrameResult:
        """Wait for the next frame-done event; after a timeout, read STATUS instead (v1 flow control)."""
        if self._events:
            done: FrameDone | None = self._events.popleft()
        else:
            reply = self._wait(p.is_frame_done, self._timeouts.frame_done)
            done = p.parse_frame_done(reply) if reply is not None else None
        if done is None:
            return FrameResult(None, device_status=self.status())
        return FrameResult(done.status, done.seq, done.decode_ms)

    def _hidden(self) -> bool:
        """True while the knob hides the realtime slot (always False on proto 1)."""
        return self._knob() and not self._visible

    def _hidden_since(self, epoch: int, wait: float = 0.0) -> bool:
        """True if the slot is hidden now or was hidden at some point after `epoch` was read. With `wait`,
        give the hidden event (sent by the device right after the 05 frame-done) that long to arrive on the
        receiving thread."""
        deadline = time.monotonic() + wait
        with self._state:
            while True:
                if self._hidden() or (self._knob() and self._hidden_epoch != epoch):
                    return True
                left = deadline - time.monotonic()
                if left <= 0 or not self._knob():
                    return False
                self._state.wait(left)

    def _show_stream(self, stream_type: int, stream: bytes) -> FrameResult:
        # One frame owns the link from its start to its frame-done: a request from another thread (keyboard,
        # STATUS) waiting for its reply would otherwise have that reply read, and dropped, by this frame's
        # _collect (hardware 2026-11-08: "no reply to 3B 05" while the widget sender streamed).
        with self._io:
            return self._show_stream_locked(stream_type, stream)

    def _show_stream_locked(self, stream_type: int, stream: bytes) -> FrameResult:
        self._ensure_entered()
        for attempt in range(_KNOB_RETRIES + 1):
            if self._hidden():
                return FrameResult(None, hidden=True)
            self._drain()
            self._events.clear()
            epoch = self._hidden_epoch
            epochs = (self._hidden_epoch, self._shown_epoch)
            try:
                self._send_stream(stream_type, stream)
            except Timeout:
                # An input-queue overflow can discard a whole queued start or block (seen with fast knob
                # turns). On proto 2 a new start replaces a stream the device is still receiving, so sending
                # the frame again is safe.
                if not self._knob() or attempt == _KNOB_RETRIES:
                    raise
                continue
            result = self._collect()
            if result.status == c.FRAME_NOT_REALTIME and self._hidden_since(epoch, _HIDDEN_EVENT_WAIT_S):
                if self._hidden() or attempt == _KNOB_RETRIES:
                    return replace(result, hidden=True)
                continue  # hidden and shown again during the upload: the frame is wanted, send it again
            if result.status == c.FRAME_DROPPED_BUSY and self._knob_moved(epochs) and attempt < _KNOB_RETRIES:
                continue  # fast knob turns stalled the device's GUI task and its input queue overflowed
            break
        if result.status == c.FRAME_SUPERSEDED:
            return result  # the knob moved on; the next frame is for the new page
        if result.status is not None and not result.shown:
            raise FrameRejected(result.status)
        return result

    def _knob_moved(self, epochs: tuple[int, int]) -> bool:
        return self._knob() and (self._hidden_epoch, self._shown_epoch) != epochs

    def show(self, image: ImageSource, fit: Fit = "contain", quality: int = 85, max_bytes: int | None = None,
             subsampling: Subsampling = "4:2:0") -> FrameResult:
        """Show a PIL image, numpy array or image file (entering realtime mode if needed).

        Raises FrameRejected if the device reports anything but "shown". While the knob hides the realtime
        slot (proto 2) nothing is sent and the result has `hidden=True`.
        """
        return self.show_jpeg(self.encode(image, fit, quality, max_bytes, subsampling))

    def show_jpeg(self, jpeg: bytes) -> FrameResult:
        """Show ready JPEG bytes (baseline, even size, at most image_w x image_h). APPn except APP0 is
        stripped."""
        return self._show_stream(c.STREAM_JPEG, self._jpeg_stream(jpeg))

    def show_region(self, jpeg: bytes, x: int, y: int) -> FrameResult:
        """Replace a rectangle of the shown full 480x360 frame with `jpeg` placed at (x, y): only the change
        crosses the link, so small areas (meters, a clock's seconds) update many times a second. The JPEG's
        width and height must be multiples of 16, x and y even, the area at most 86400 pixels and inside the
        canvas. Needs `capabilities.region`; after entering, or once the slot was hidden, the device answers
        FrameRejected(FRAME_NO_CANVAS) until a full frame is shown again."""
        hello = self._require_realtime()
        if not hello.region:
            raise ProtocolError("this firmware has no region frames")
        jpeg = strip_app_segments(jpeg)
        info = inspect_jpeg(jpeg)
        w, h = info.width, info.height
        if w % c.REGION_ALIGN or h % c.REGION_ALIGN or x % 2 or y % 2 or x < 0 or y < 0                 or x + w > hello.canvas_w or y + h > hello.canvas_h or w * h > c.REGION_MAX_PIXELS:
            raise ValueError(f"region {w}x{h} at ({x},{y}): sizes must be multiples of {c.REGION_ALIGN}, "
                             f"positions even, at most {c.REGION_MAX_PIXELS} pixels inside the canvas")
        stream = p.build_region_stream(jpeg, x, y, w, h)
        if len(stream) > hello.max_frame_bytes:
            raise ValueError(f"frame of {len(stream)} bytes exceeds max_frame_bytes {hello.max_frame_bytes}")
        return self._show_stream(c.STREAM_JPEG, stream)

    def cache_page(self, page: int, jpeg: bytes) -> FrameResult:
        """Store `jpeg` (480x360, at most `cache_slot_bytes`) as host page `page`'s picture. When the knob
        turns to that page, or enters the slot on it, the device shows it at once (instead of the last frame
        or black) until the host's own frame arrives. Not shown now. Needs `capabilities.page_cache`; changing
        the page count forgets the cache. Kept in RAM only."""
        hello = self._require_realtime()
        if not hello.page_cache:
            raise ProtocolError("this firmware has no page cache")
        jpeg = strip_app_segments(jpeg)
        info = inspect_jpeg(jpeg)
        if (info.width, info.height) != (hello.canvas_w, hello.canvas_h):
            raise ValueError(f"a cached page is {hello.canvas_w}x{hello.canvas_h}, not {info.width}x{info.height}")
        if not 0 <= page < c.MAX_HOST_PAGES or (self.cache_slot_bytes and len(jpeg) > self.cache_slot_bytes):
            raise ValueError(f"page {page} / {len(jpeg)} bytes: pages 0..7, at most {self.cache_slot_bytes} bytes")
        return self._show_stream(c.STREAM_JPEG, p.build_cache_stream(jpeg, page))

    def show_rgb565(self, source: ImageSource | bytes, width: int | None = None, height: int | None = None,
                    fit: Fit = "contain") -> FrameResult:
        """Show raw RGB565 (little-endian, row-major; pass width and height) or an image converted to it."""
        hello = self._require_realtime()
        if not hello.supports_rgb565:
            raise ProtocolError("the device does not accept RGB565 frames")
        if isinstance(source, (bytes, bytearray, memoryview)):
            if width is None or height is None:
                raise ValueError("raw RGB565 needs width and height")
            pixels = bytes(source)
        else:
            img = prepare_image(source, hello.image_size, fit)
            pixels, (width, height) = to_rgb565(img), img.size
        if width > hello.image_w or height > hello.image_h:
            raise ValueError(f"frame is {width}x{height}; the maximum is {hello.image_w}x{hello.image_h}")
        stream = p.build_rgb565_stream(pixels, width, height)
        if len(stream) > hello.max_frame_bytes:
            raise ValueError(f"frame of {len(stream)} bytes exceeds max_frame_bytes {hello.max_frame_bytes}")
        return self._show_stream(c.STREAM_RGB565, stream)

    def stream(self, frames: Iterable[Frame], fps: float | None = None, fit: Fit = "contain", quality: int = 85,
               max_bytes: int | None = None, subsampling: Subsampling = "4:2:0") -> StreamStats:
        """Show frames (images, or JPEG bytes) one after another, at most `fps` per second.

        Flow control: with one device buffer, each frame waits for the previous frame-done event (or the
        timeout, then STATUS). With `buffers >= 2` one more frame is sent while the previous one decodes.
        Frames dropped as busy are counted; other rejections raise FrameRejected.

        Knob (proto 2): while the slot is hidden nothing is sent and the frame source is paused; when the
        slot becomes visible again the newest frame is sent at once. A frame answered 05 because the slot
        was hidden meanwhile is counted in `hidden` and resent, not raised. If the device disarmed the slot
        (host timeout), the next new frame enters realtime mode again.
        """
        hello = self._require_realtime()
        self._ensure_entered()
        run = _StreamRun(depth=max(1, min(hello.buffers, c.MAX_PIPELINE_DEPTH)))
        period = 1.0 / fps if fps else 0.0
        next_at = time.monotonic()
        current: bytes | None = None
        seen_epoch = self._shown_epoch
        self._drain()
        self._events.clear()
        feed: FrameFeed[Frame] = FrameFeed(frames)
        try:
            while not feed.finished:
                if self._hidden() and self._entered:
                    self._settle(run)
                    if not self._wait_until_visible():
                        break
                    continue
                # Not entered here means the device disarmed the slot (host timeout); the next new frame
                # enters again (in _push) rather than pulling the page back for an old one.
                if self._entered and self._shown_epoch != seen_epoch:
                    seen_epoch = self._shown_epoch
                    run.resend = current is not None
                if run.resend and current is not None and self._entered and not feed.ready():
                    self._push(run, current)
                    seen_epoch = self._shown_epoch
                    continue
                frame = feed.get(_FEED_POLL_S)
                if frame is None:
                    continue
                jpeg = bytes(frame) if isinstance(frame, (bytes, bytearray)) else \
                    self.encode(frame, fit, quality, max_bytes, subsampling)
                current = self._jpeg_stream(jpeg)
                if period:
                    time.sleep(max(0.0, next_at - time.monotonic()))
                    next_at = max(next_at + period, time.monotonic())
                self._push(run, current)
                seen_epoch = self._shown_epoch  # the newest frame went out; no resend for an ENTER in _push
            self._settle(run)
        finally:
            feed.stop()
        return run.stats

    def _push(self, run: _StreamRun, stream: bytes) -> None:
        """Send one frame once a device buffer is free, unless the slot got hidden meanwhile."""
        while len(run.in_flight) >= run.depth:
            self._account(run, self._collect(), run.in_flight.popleft())
        self._ensure_entered()
        run.resend = False
        if self._hidden():
            run.resend = True
            return
        run.in_flight.append(self._hidden_epoch)
        self._send_stream(c.STREAM_JPEG, stream)
        run.stats.sent += 1

    def _settle(self, run: _StreamRun) -> None:
        while run.in_flight:
            self._account(run, self._collect(), run.in_flight.popleft())

    def _wait_until_visible(self) -> bool:
        """Block while the slot is armed but hidden. False if the screen was closed meanwhile."""
        with self._state:
            while not self._closed and self._entered and not self._visible:
                self._state.wait(_FEED_POLL_S)
        return not self._closed

    def _account(self, run: _StreamRun, result: FrameResult, epoch: int) -> None:
        stats = run.stats
        if result.timed_out:
            stats.timeouts += 1
        elif result.shown:
            stats.shown += 1
        elif result.status == c.FRAME_DROPPED_BUSY:
            stats.dropped += 1
        elif result.status == c.FRAME_NOT_REALTIME and self._hidden_since(epoch, _HIDDEN_EVENT_WAIT_S):
            stats.hidden += 1
            run.resend = True
        else:
            raise FrameRejected(result.status or 0)

    # --- stock helpers ------------------------------------------------------------------------------

    def _query(self, kind: int) -> bytes:
        reply = self._request(p.build_query(kind), lambda r: p.query_reply_data(r, kind) is not None,
                              f"A5 41 {kind:02X}")
        return p.query_reply_data(reply, kind) or b""

    def info(self) -> DeviceInfo:
        """Stock combined info (A5 41 14) plus image (10) and GIF (11) space. Read-only."""
        base = p.parse_info(self._query(c.QUERY_INFO))
        return replace(base, image_space=p.parse_space(self._query(c.QUERY_IMAGE_SPACE)),
                       gif_space=p.parse_space(self._query(c.QUERY_GIF_SPACE)))

    def _class11_text(self, sub: int) -> str:
        reply = self._request(p.build_class11(sub), lambda r: p.class11_reply_text(r, sub) is not None,
                              f"11 {sub:02X}")
        return p.class11_reply_text(reply, sub) or ""

    def version(self) -> VersionInfo:
        """Model and build strings (11 01 / 11 02). Read-only."""
        return VersionInfo(self._class11_text(c.CLASS11_MODEL), self._class11_text(c.CLASS11_BUILD))

    def next_page(self) -> None:
        """Show the next stock page. Warning: the firmware saves the page to internal flash about 2 s later."""
        self._send(p.build_control(c.CONTROL_NEXT_PAGE))

    def prev_page(self) -> None:
        """Show the previous stock page. Warning: the firmware saves the page to internal flash about 2 s later."""
        self._send(p.build_control(c.CONTROL_PREV_PAGE))

    def brightness_up(self) -> None:
        """Raise the brightness one step. Warning: the firmware saves the brightness to internal flash."""
        self._send(p.build_control(c.CONTROL_BRIGHTNESS_UP))

    def brightness_down(self) -> None:
        """Lower the brightness one step. Warning: the firmware saves the brightness to internal flash."""
        self._send(p.build_control(c.CONTROL_BRIGHTNESS_DOWN))

    def sync_time(self, when: datetime | None = None) -> None:
        """Set the screen clock (A5 38) to `when` (default: now, local time). No flash write is known."""
        self._send(p.build_time_sync(when or datetime.now()))

    def upload_wallpaper(self, image: ImageSource, persistent: bool = False, fit: Fit = "contain",
                         quality: int = 90, subsampling: Subsampling = "4:2:0") -> None:
        """Store `image` as the stock static wallpaper through the stock B0 upload.

        Warning: this ERASES AND REWRITES THE SCREEN'S EXTERNAL FLASH (one fixed slot, no wear levelling)
        and replaces the current wallpaper, which cannot be read back. It runs only when called with
        `persistent=True`. Do not run it while Aula Hub uploads. Works on stock firmware too.
        """
        if persistent is not True:
            raise ValueError("upload_wallpaper writes flash: call it with persistent=True")
        warnings.warn("upload_wallpaper erases and rewrites the screen's external flash",
                      FlashWriteWarning, stacklevel=2)
        if self._entered:
            self.leave()
        info = self.info()
        if not info.supports_jpeg:
            raise ProtocolError("the device does not report JPEG support")
        img = prepare_image(image, (info.width, info.height), fit)
        budget = info.image_space - c.JPEG_STREAM_HEADER_SIZE if info.image_space else None
        jpeg = encode_jpeg(img, quality, subsampling, budget)
        self._send_stream(c.STREAM_WALLPAPER, p.build_jpeg_stream(jpeg, *img.size), persistent=True)
        time.sleep(c.WALLPAPER_SETTLE_S)
