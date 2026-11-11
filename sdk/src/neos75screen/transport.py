"""HID transports. Every write passes the allow-list in guard.py first.

`open_transport()` uses the `hidapi` package (import name `hid`) and falls back to a Windows ctypes
implementation when it is unavailable. `MockTransport` replaces the device in tests.
"""
from __future__ import annotations

import queue
import sys
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from types import ModuleType, TracebackType

from . import constants as c
from .errors import DeviceLost, DeviceNotFound
from .guard import PacketGuard
from .protocol import pad


class Transport(ABC):
    """64-byte report pipe: guarded writes, and a queue of input reports."""

    def __init__(self) -> None:
        self._guard = PacketGuard()
        self._reports: queue.Queue[bytes] = queue.Queue()
        self._filter: Callable[[bytes], bool] | None = None
        self._reader: ReaderThread | None = None   # device transports: the thread that reads input reports

    @property
    def lost(self) -> OSError | None:
        """Why the device went away (its input reads failed: unplugged, keyboard reconnected), or None."""
        return self._reader.error if self._reader is not None else None

    def set_report_filter(self, report_filter: Callable[[bytes], bool] | None) -> None:
        """Install a function that sees every input report first, on the receiving thread; reports for which
        it returns True are consumed and never reach `read()` (used to route unsolicited events)."""
        self._filter = report_filter

    def _deliver(self, report: bytes) -> None:
        report = pad(report[:c.REPORT_SIZE])
        if self._filter is not None and self._filter(report):
            return
        self._reports.put(report)

    def write(self, payload: bytes) -> None:
        """Pad to 64 bytes, check against the allow-list, then send. Raises ForbiddenPacket, and DeviceLost
        once the device has gone away."""
        report = pad(payload)
        self._guard.check(report)
        if (lost := self.lost) is not None:
            raise DeviceLost(lost)
        self._write_report(report)

    def read(self, timeout: float) -> bytes | None:
        """Next input report (64 bytes), or None after `timeout` seconds."""
        try:
            return self._reports.get(timeout=max(timeout, 0.0))
        except queue.Empty:
            return None

    def read_nowait(self) -> bytes | None:
        try:
            return self._reports.get_nowait()
        except queue.Empty:
            return None

    @contextmanager
    def stream_session(self, persistent: bool = False) -> Iterator[None]:
        """Allow the BA packets of exactly one stream. B0 needs persistent=True."""
        self._guard.open_stream(persistent)
        try:
            yield
        finally:
            self._guard.close_stream()

    @abstractmethod
    def _write_report(self, report: bytes) -> None:
        """Send one checked 64-byte report."""

    def close(self) -> None:
        """Release the device. Safe to call twice."""

    def __enter__(self) -> Transport:
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None,
                 tb: TracebackType | None) -> None:
        self.close()


class ReaderThread(threading.Thread):
    """Drains input reports into a queue so a stale report can never pass for a fresh reply."""

    def __init__(self, read_report: Callable[[int], bytes | None], deliver: Callable[[bytes], None]) -> None:
        super().__init__(name="neos75-reader", daemon=True)
        self._read_report = read_report
        self._deliver = deliver
        self._stop_event = threading.Event()
        self.error: OSError | None = None   # set when a read fails: the device is gone

    def run(self) -> None:
        while not self._stop_event.is_set():
            try:
                data = self._read_report(c.READ_POLL_MS)
            except OSError as exc:
                self.error = exc
                return
            if data:
                self._deliver(data)

    def stop(self) -> None:
        self._stop_event.set()
        self.join(timeout=1.0)


def _hidapi() -> ModuleType | None:
    """The cython `hidapi` module, or None (not installed, or another package named `hid`)."""
    try:
        import hid
    except ImportError:
        return None
    return hid if hasattr(hid, "enumerate") and hasattr(hid, "device") else None


class HidapiTransport(Transport):
    """Transport over the `hidapi` package."""

    def __init__(self, hid: ModuleType, path: bytes) -> None:
        super().__init__()
        self._dev = hid.device()
        try:
            self._dev.open_path(path)
        except OSError as exc:
            raise DeviceNotFound(f"cannot open the screen interface: {exc}") from exc
        self._closed = False
        self._reader = ReaderThread(self._read_raw, self._deliver)
        self._reader.start()

    @staticmethod
    def find_path(hid: ModuleType) -> bytes:
        for entry in hid.enumerate(c.VID, c.PID):
            if entry.get("usage_page") == c.USAGE_PAGE and entry.get("usage") == c.USAGE:
                return bytes(entry["path"])
        raise DeviceNotFound("no AULA Neos75 screen interface found")

    def _read_raw(self, timeout_ms: int) -> bytes | None:
        return bytes(self._dev.read(c.REPORT_SIZE, timeout_ms)) or None

    def _write_report(self, report: bytes) -> None:
        if self._dev.write(bytes([c.REPORT_ID]) + report) < 0:
            raise OSError("HID write failed")

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._reader.stop()
            self._dev.close()


def open_transport() -> Transport:
    """Open the screen interface in shared mode. Raises DeviceNotFound."""
    hid = _hidapi()
    if hid is not None:
        return HidapiTransport(hid, HidapiTransport.find_path(hid))
    if sys.platform == "win32":
        from ._win32 import WindowsTransport
        return WindowsTransport(WindowsTransport.find_path())
    raise DeviceNotFound("the hidapi package is not installed")


def screen_present() -> bool:
    """Whether the screen interface is enumerated. Opens nothing for I/O and sends nothing."""
    hid = _hidapi()
    try:
        if hid is not None:
            HidapiTransport.find_path(hid)
        elif sys.platform == "win32":
            from ._win32 import WindowsTransport
            WindowsTransport.find_path()
        else:
            return False
    except DeviceNotFound:
        return False
    return True


class MockTransport(Transport):
    """In-memory transport for tests.

    `written` records every report that passed the allow-list. `responder` receives each written report
    and returns the reports to queue as replies; `inject` queues a report at any time (for async events).
    """

    def __init__(self, responder: Callable[[bytes], Iterable[bytes]] | None = None) -> None:
        super().__init__()
        self.written: list[bytes] = []
        self._responder = responder
        self._lost: OSError | None = None

    @property
    def lost(self) -> OSError | None:
        return self._lost

    def disconnect(self) -> None:
        """Act as if the device went away: every later write raises DeviceLost."""
        self._lost = OSError("device disconnected")

    def _write_report(self, report: bytes) -> None:
        self.written.append(report)
        if self._responder is not None:
            for reply in self._responder(report):
                self.inject(reply)

    def inject(self, report: bytes) -> None:
        self._deliver(report)
