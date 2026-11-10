"""Background helpers for proto 2 sessions: event delivery to callbacks and iterators, and the keepalive."""
from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable, Iterator
from functools import partial
from typing import Union

from . import constants as c
from .protocol import KnobEvent, PageEvent, VisibilityEvent

Event = Union[KnobEvent, VisibilityEvent, PageEvent]
KnobCallback = Callable[[KnobEvent], None]
VisibilityCallback = Callable[[VisibilityEvent], None]
PageCallback = Callable[[PageEvent], None]

_log = logging.getLogger(__name__)
_POLL_S = 0.1


class EventHub:
    """Buffers events for `iterate()` and runs callbacks on one dispatcher thread, so a slow callback never
    blocks the HID reader. `publish` is called from the reader thread."""

    def __init__(self) -> None:
        self._buffer: queue.Queue[Event] = queue.Queue(maxsize=c.EVENT_QUEUE_SIZE)
        self._pending: queue.Queue[Event] = queue.Queue()
        self._knob: list[KnobCallback] = []
        self._visibility: list[VisibilityCallback] = []
        self._page: list[PageCallback] = []
        self._closed = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def on_knob(self, callback: KnobCallback) -> None:
        with self._lock:
            self._knob.append(callback)
            self._start()

    def on_visibility(self, callback: VisibilityCallback) -> None:
        with self._lock:
            self._visibility.append(callback)
            self._start()

    def on_page(self, callback: PageCallback) -> None:
        with self._lock:
            self._page.append(callback)
            self._start()

    def _start(self) -> None:
        if self._thread is None and not self._closed.is_set():
            self._thread = threading.Thread(target=self._dispatch, name="neos75-events", daemon=True)
            self._thread.start()

    def publish(self, event: Event) -> None:
        if self._closed.is_set():
            return
        while True:
            try:
                self._buffer.put_nowait(event)
                break
            except queue.Full:
                try:
                    self._buffer.get_nowait()  # drop the oldest
                except queue.Empty:
                    pass
        if self._thread is not None:
            self._pending.put(event)

    def iterate(self, timeout: float | None) -> Iterator[Event]:
        """Yield buffered and new events; stop after `timeout` seconds without one, or when closed."""
        while not self._closed.is_set():
            try:
                yield self._buffer.get(timeout=_POLL_S if timeout is None else timeout)
            except queue.Empty:
                if timeout is not None:
                    return

    def _dispatch(self) -> None:
        while not self._closed.is_set():
            try:
                event = self._pending.get(timeout=_POLL_S)
            except queue.Empty:
                continue
            with self._lock:
                if isinstance(event, KnobEvent):
                    calls = [partial(callback, event) for callback in self._knob]
                elif isinstance(event, PageEvent):
                    calls = [partial(callback, event) for callback in self._page]
                else:
                    calls = [partial(callback, event) for callback in self._visibility]
            for call in calls:
                try:
                    call()
                except Exception:
                    _log.exception("neos75screen event callback failed")

    def close(self) -> None:
        self._closed.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=1.0)


class Keepalive(threading.Thread):
    """Calls `tick()` every `interval` seconds until stopped. `tick` decides whether to send anything."""

    def __init__(self, tick: Callable[[], None], interval: float) -> None:
        super().__init__(name="neos75-keepalive", daemon=True)
        self._tick = tick
        self._interval = interval
        self._stop_event = threading.Event()

    def run(self) -> None:
        while not self._stop_event.wait(self._interval):
            self._tick()

    def stop(self) -> None:
        self._stop_event.set()
        if self is not threading.current_thread():
            self.join(timeout=2.0)
