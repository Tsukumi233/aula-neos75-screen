"""Host SDK for the realtime screen protocol of a patched AULA Neos75 keyboard screen."""
from .errors import (DeviceNotFound, FlashWriteWarning, ForbiddenPacket, FrameRejected, Neos75Error,
                     ProtocolError, StockFirmware, Timeout)
from .events import Event
from .protocol import DeviceInfo, FrameDone, Hello, KeyboardStatus, KnobEvent, PageEvent, Status, VisibilityEvent
from .screen import FrameResult, Screen, StreamStats, Timeouts, VersionInfo
from .transport import MockTransport, Transport, open_transport

__version__ = "0.1.0"

__all__ = [
    "DeviceInfo", "DeviceNotFound", "Event", "FlashWriteWarning", "ForbiddenPacket", "FrameDone",
    "FrameRejected", "FrameResult", "Hello", "KeyboardStatus", "KnobEvent", "PageEvent", "MockTransport", "Neos75Error", "ProtocolError",
    "Screen", "Status", "StockFirmware", "StreamStats", "Timeout", "Timeouts", "Transport", "VersionInfo",
    "VisibilityEvent", "open_transport",
]
