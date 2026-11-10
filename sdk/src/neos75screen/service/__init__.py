"""`neos75d`: the local screen service, the single owner of the keyboard screen.

One process may hold the screen's HID device, so the desktop app, the `neos75ctl` CLI and agents do not open
it themselves: they talk to this service over a localhost-only JSON API (`server.py`, documented in
`API.md`). The service runs the widget runner (`live.runner.WidgetRunner`), follows the page selection file
(`live.library`, `%APPDATA%\\neos75screen\\pages.json`), renders page previews, and streams events (page,
knob, visibility, keyboard battery/connection, frame rate) to its clients.

Discovery: while running, the service writes `%APPDATA%\\neos75screen\\service.json` with its port, a
random token and its pid; clients send the token as `X-Neos75-Token` (or `?token=` for images).
"""
from __future__ import annotations

API_VERSION = 1
DEFAULT_PORT = 47575
