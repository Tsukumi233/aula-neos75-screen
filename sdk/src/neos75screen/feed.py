"""Pull frames from an iterable on a background thread, one at a time.

`Screen.stream` uses this so it can react to visibility events while the frame source is busy (a clock or
slideshow generator that sleeps until its next frame). The producer hands over one item and waits until
it is taken, so a paused stream also pauses the source.
"""
from __future__ import annotations

import queue
import threading
from collections.abc import Iterable
from typing import Generic, TypeVar

T = TypeVar("T")

_END = object()
_PUT_POLL_S = 0.1


class FrameFeed(Generic[T]):
    def __init__(self, items: Iterable[T]) -> None:
        self._items = items
        self._queue: queue.Queue[object] = queue.Queue(maxsize=1)
        self._stop = threading.Event()
        self._error: BaseException | None = None
        self.finished = False
        self._thread = threading.Thread(target=self._run, name="neos75-feed", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            for item in self._items:
                if not self._put(item):
                    return
        except BaseException as exc:  # handed to the consumer thread
            self._error = exc
        self._put(_END)

    def _put(self, item: object) -> bool:
        while not self._stop.is_set():
            try:
                self._queue.put(item, timeout=_PUT_POLL_S)
                return True
            except queue.Full:
                continue
        return False

    def ready(self) -> bool:
        """True if an item (or the end) is waiting."""
        return not self._queue.empty()

    def get(self, timeout: float) -> T | None:
        """The next item, or None if none arrived within `timeout` or the source ended (`finished` is then
        True). An exception raised by the source is re-raised here."""
        try:
            item = self._queue.get(timeout=timeout)
        except queue.Empty:
            return None
        if item is _END:
            self.finished = True
            if self._error is not None:
                raise self._error
            return None
        return item  # type: ignore[return-value]

    def stop(self) -> None:
        """Release the producer thread (it ends at its next hand-over)."""
        self._stop.set()
