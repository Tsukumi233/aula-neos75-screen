"""Client of the `neos75d` API (stdlib only): used by `neos75ctl`, tests and Python scripts.

    from neos75screen.service.client import Client
    api = Client.discover()            # reads %APPDATA%\\neos75screen\\service.json
    api.get("/api/status")
    api.put("/api/pages", {"pages": ["neos_clock", "neos_system"]})
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import quote

from .paths import discovery_file


class ServiceUnavailable(Exception):
    """No service is running (or it does not answer)."""


class ApiFailure(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class Client:
    def __init__(self, port: int, token: str, timeout: float = 30.0, info: dict | None = None) -> None:
        self.port, self.token, self.timeout = port, token, timeout
        self.info = info or {}
        self.base = f"http://127.0.0.1:{port}"

    @classmethod
    def discover(cls, path: Path | None = None, timeout: float = 30.0) -> Client:
        path = path or discovery_file()
        try:
            info = json.loads(path.read_text(encoding="utf-8"))
            client = cls(int(info["port"]), str(info["token"]), timeout, info)
        except (OSError, ValueError, KeyError) as exc:
            raise ServiceUnavailable(f"neos75d is not running (no {path})") from exc
        try:
            health = client.request("GET", "/api/health", timeout=min(timeout, 2.0))
        except (ServiceUnavailable, ApiFailure) as exc:
            raise ServiceUnavailable(f"neos75d is not answering on port {client.port}") from exc
        if health.get("pid") != info.get("pid"):
            raise ServiceUnavailable("stale service.json")
        return client

    def request(self, method: str, path: str, body: object = None, timeout: float | None = None,
                raw: bool = False):  # noqa: ANN201
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("X-Neos75-Token", self.token)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:  # noqa: S310 - localhost
                payload = resp.read()
        except urllib.error.HTTPError as exc:
            try:
                message = json.loads(exc.read().decode("utf-8")).get("error", str(exc))
            except ValueError:
                message = str(exc)
            raise ApiFailure(exc.code, message) from exc
        except (urllib.error.URLError, OSError) as exc:
            raise ServiceUnavailable(f"neos75d unreachable: {exc}") from exc
        return payload if raw else json.loads(payload.decode("utf-8"))

    def get(self, path: str, **kw):  # noqa: ANN003, ANN201
        return self.request("GET", path, **kw)

    def post(self, path: str, body: object = None, **kw):  # noqa: ANN003, ANN201
        return self.request("POST", path, body if body is not None else {}, **kw)

    def put(self, path: str, body: object, **kw):  # noqa: ANN003, ANN201
        return self.request("PUT", path, body, **kw)

    def preview(self, entry: str, label: str = "Page 7", width: int = 480, fresh: bool = False) -> bytes:
        path = f"/api/preview?entry={quote(entry, safe='')}&label={quote(label)}&width={width}" + ("&fresh=1" if fresh else "")
        return self.request("GET", path, raw=True, timeout=120)

    def events(self) -> Iterator[dict]:
        """The event stream, one dict per event (blocks; ends when the service stops)."""
        req = urllib.request.Request(self.base + "/api/events")
        req.add_header("X-Neos75-Token", self.token)
        try:
            resp = urllib.request.urlopen(req, timeout=None)  # noqa: S310 - localhost
        except (urllib.error.URLError, OSError) as exc:
            raise ServiceUnavailable(f"neos75d unreachable: {exc}") from exc
        with resp:
            for line in resp:
                if line.startswith(b"data: "):
                    yield json.loads(line[6:].decode("utf-8"))
