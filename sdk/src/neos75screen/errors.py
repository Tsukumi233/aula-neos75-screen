"""Exceptions raised by the SDK."""
from __future__ import annotations

from . import constants as c


class Neos75Error(Exception):
    """Base class of every SDK error."""


class DeviceNotFound(Neos75Error):
    """No screen interface (VID 5542, PID 0001, usage page FF60, usage 61) is present."""


class StockFirmware(Neos75Error):
    """The screen runs stock firmware, so realtime commands are unavailable."""


class ForbiddenPacket(Neos75Error):
    """The transport allow-list refused a packet; nothing was sent."""


class ProtocolError(Neos75Error):
    """The device answered with something the protocol does not allow (NACK, bad layout, error code)."""


class Timeout(Neos75Error, TimeoutError):
    """The device did not answer in time."""


class FlashWriteWarning(UserWarning):
    """Issued before an operation that erases or rewrites the screen's flash."""


class FrameRejected(Neos75Error):
    """The frame-done event reported a status other than "shown"."""

    def __init__(self, status: int) -> None:
        self.status = status
        name = c.FRAME_STATUS_NAMES.get(status, "unknown")
        super().__init__(f"frame rejected: status 0x{status:02X} ({name})")
