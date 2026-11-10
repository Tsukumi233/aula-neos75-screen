"""`neos75d`: localhost-only HTTP/JSON API over the engine (see API.md next to this file).

Security: binds 127.0.0.1 only, checks the Host header (no DNS rebinding), and every call except
`/api/health` needs the token from the discovery file (`X-Neos75-Token` header or `?token=`), so a web page
in a browser cannot drive the screen.
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import secrets
import signal
import socket
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import API_VERSION, DEFAULT_PORT
from .engine import Engine, dumps
from .paths import Settings, cache_dir, discovery_file
from .preview import Previewer, thumbnail

SSE_PING_S = 15.0


class ApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class _HTTPServer(ThreadingHTTPServer):
    # http.server sets SO_REUSEADDR, which on Windows lets a second process bind the same port: two services
    # (e.g. with different NEOS75_HOME) then silently shared 47575. Own the port exclusively instead.
    allow_reuse_address = False

    def server_bind(self) -> None:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class Service:
    """The HTTP server plus the engine; `serve_forever` blocks until `shutdown`."""

    def __init__(self, engine: Engine, port: int = DEFAULT_PORT, token: str | None = None,
                 discovery: Path | None = None) -> None:
        self.engine = engine
        self.token = token or secrets.token_urlsafe(24)
        self.discovery = discovery
        try:
            self.httpd = _HTTPServer(("127.0.0.1", port), _handler(self))
        except OSError:
            if port != DEFAULT_PORT:
                raise
            # another program has the default port: any free one (clients find it in the discovery file)
            self.httpd = _HTTPServer(("127.0.0.1", 0), _handler(self))
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.stopping = threading.Event()

    def write_discovery(self) -> None:
        if self.discovery is None:
            return
        self.discovery.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.discovery.with_suffix(".tmp")
        tmp.write_text(json.dumps({"port": self.port, "token": self.token, "pid": os.getpid(),
                                   "fake": self.engine.fake, "api": API_VERSION}), encoding="utf-8")
        tmp.replace(self.discovery)

    def remove_discovery(self) -> None:
        if self.discovery is None:
            return
        try:
            data = json.loads(self.discovery.read_text(encoding="utf-8"))
            if data.get("pid") == os.getpid():
                self.discovery.unlink()
        except (OSError, ValueError):
            pass

    def serve_forever(self) -> None:
        self.write_discovery()
        try:
            self.httpd.serve_forever(poll_interval=0.25)
        finally:
            self.remove_discovery()

    def start_background(self) -> threading.Thread:
        thread = threading.Thread(target=self.serve_forever, name="neos75d-http", daemon=True)
        thread.start()
        return thread

    def shutdown(self) -> None:
        if self.stopping.is_set():
            return
        self.stopping.set()
        self.engine.close()
        threading.Thread(target=self.httpd.shutdown, daemon=True).start()

    # --- routes --------------------------------------------------------------------------------------

    def route(self, method: str, path: str, query: dict, body: object) -> tuple[int, str, bytes] | dict | list:
        e = self.engine
        if (method, path) == ("GET", "/api/status"):
            return e.status(self.port)
        if (method, path) == ("GET", "/api/widgets"):
            return e.widgets()
        if (method, path) == ("GET", "/api/pages"):
            return e.selection()
        if (method, path) == ("PUT", "/api/pages"):
            return e.set_pages(_field(body, "pages", list))
        if (method, path) == ("POST", "/api/pages/add"):
            entries = e.pages()
            entry = _field(body, "entry", str)
            position = body.get("position", len(entries)) if isinstance(body, dict) else len(entries)
            if not isinstance(position, int) or not 0 <= position <= len(entries):
                raise ApiError(400, "position out of range")
            entries.insert(position, entry)
            return e.set_pages(entries)
        if (method, path) == ("POST", "/api/pages/remove"):
            entries = e.pages()
            entries.pop(_index(body, "index", len(entries)))
            return e.set_pages(entries)
        if (method, path) == ("POST", "/api/pages/move"):
            entries = e.pages()
            item = entries.pop(_index(body, "from", len(entries)))
            entries.insert(_index(body, "to", len(entries) + 1), item)
            return e.set_pages(entries)
        if (method, path) == ("POST", "/api/pages/replace"):
            entries = e.pages()
            entries[_index(body, "index", len(entries))] = _field(body, "entry", str)
            return e.set_pages(entries)
        if (method, path) == ("GET", "/api/preview"):
            return self._preview(query)
        if (method, path) == ("GET", "/api/screen.jpg"):
            jpeg = e.mirror_jpeg()
            if jpeg is None:
                raise ApiError(404, "no frame yet (is the runner running?)")
            return 200, "image/jpeg", jpeg
        if method == "POST" and path in ("/api/runner/start", "/api/runner/stop", "/api/runner/restart"):
            action = path.rsplit("/", 1)[1]
            getattr(e, action)()
            return {"ok": True, "runner": e.status(self.port)["runner"]}
        if (method, path) == ("GET", "/api/settings"):
            return e.settings.to_dict()
        if (method, path) == ("PUT", "/api/settings"):
            return e.update_settings(body if isinstance(body, dict) else {})
        if path.startswith("/api/keyboard/via"):
            return self._keyboard(method, path[len("/api/keyboard/via"):], body)
        if (method, path) == ("GET", "/api/knobs"):
            return e.knob_settings()
        if (method, path) == ("PUT", "/api/knobs"):
            if not isinstance(body, dict):
                raise ApiError(400, "body must be the knob settings object")
            try:
                return e.set_knob_settings(body.get("config", body))
            except ValueError as exc:
                raise ApiError(400, str(exc)) from exc
        if (method, path) == ("GET", "/api/log"):
            return list(e.log_lines)
        if (method, path) == ("POST", "/api/knob"):
            direction = _field(body, "direction", int)
            try:
                e.turn_knob(direction)
            except PermissionError as exc:
                raise ApiError(409, str(exc)) from exc
            except RuntimeError as exc:
                raise ApiError(409, str(exc)) from exc
            return {"ok": True}
        if (method, path) == ("POST", "/api/fake/keyboard"):
            try:
                e.set_fake_keyboard(int(body.get("battery", 76)), bool(body.get("charging")), int(body.get("link", 4)))
            except PermissionError as exc:
                raise ApiError(409, str(exc)) from exc
            return {"ok": True}
        if (method, path) == ("POST", "/api/shutdown"):
            threading.Timer(0.2, self.shutdown).start()
            return {"ok": True}
        raise ApiError(404, f"no route {method} {path}")

    def _keyboard(self, method: str, sub: str, body: object) -> dict:
        """The keyboard itself over VIA (keymap, knobs written into the keyboard, lighting, settings)."""
        from .. import via
        kb = self.engine.kb
        b = body if isinstance(body, dict) else {}
        try:
            if (method, sub) == ("GET", ""):
                return kb.state()
            if (method, sub) == ("GET", "/catalog"):
                return kb.catalog()
            if (method, sub) == ("GET", "/battery"):
                return {"battery": kb.battery()}
            if (method, sub) == ("PUT", "/key"):
                return kb.set_key(_field(b, "layer", int), _field(b, "row", int), _field(b, "col", int),
                                  _field(b, "keycode", int))
            if (method, sub) == ("PUT", "/knob"):
                return kb.set_knob(_field(b, "layer", int), _field(b, "encoder", int), _field(b, "gesture", str),
                                   _field(b, "keycode", int))
            if (method, sub) == ("PUT", "/lighting"):
                return kb.set_lighting(b)
            if (method, sub) == ("PUT", "/system"):
                return {"value": kb.set_system(_field(b, "item", int), _field(b, "value", int))}
            if (method, sub) == ("PUT", "/macros"):
                return kb.set_macros(_field(b, "macros", list))
            if (method, sub) == ("PUT", "/tapdance"):
                return kb.set_tap_dance(_field(b, "index", int), b)
            if (method, sub) == ("PUT", "/label"):
                return kb.set_label(_field(b, "kind", str), _field(b, "index", int), str(b.get("name") or ""))
            if (method, sub) == ("POST", "/restore"):
                if not kb.backup_path.exists():
                    raise ApiError(409, "还没有键盘备份")
                return kb.restore()
        except via.KeyboardNotFound as exc:
            raise ApiError(409, "没有找到 Neos75 键盘（有线或 2.4G 接收器）") from exc
        except (via.ForbiddenCommand, ValueError) as exc:
            raise ApiError(400, str(exc)) from exc
        except (via.ViaError, OSError) as exc:
            raise ApiError(502, f"键盘没有响应：{exc}") from exc
        raise ApiError(404, f"no route {method} /api/keyboard/via{sub}")

    def _preview(self, query: dict) -> tuple[int, str, bytes]:
        entry = _q(query, "entry")
        if not entry:
            raise ApiError(400, "entry is required")
        label = _q(query, "label", "Page 7")
        width = int(_q(query, "width", "480") or 480)
        if self.engine.previewer is None:
            raise ApiError(503, "previews are disabled")
        try:
            png = self.engine.previewer.render(entry, label=label or None, data=self.engine.preview_data(),
                                              fresh=_q(query, "fresh") == "1", timeout=180)
        except ValueError as exc:
            raise ApiError(404, str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - render failure
            self.engine.log(f"preview {entry}: {exc!r}")
            raise ApiError(500, str(exc)) from exc
        return 200, "image/png", thumbnail(png, width)


def _q(query: dict, key: str, default: str = "") -> str:
    values = query.get(key)
    return values[0] if values else default


def _field(body: object, key: str, kind: type) -> object:
    if not isinstance(body, dict) or key not in body:
        raise ApiError(400, f"'{key}' is required")
    value = body[key]
    if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
        raise ApiError(400, f"'{key}' must be {kind.__name__}")
    return value


def _index(body: object, key: str, size: int) -> int:
    value = _field(body, key, int)
    if not 0 <= value < size:
        raise ApiError(400, f"'{key}' out of range (0..{size - 1})")
    return value


def _handler(service: Service) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "neos75d"

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - quiet
            pass

        def _headers(self, status: int, ctype: str, length: int | None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store")
            if length is not None:
                self.send_header("Content-Length", str(length))
            self.end_headers()

        def _send(self, status: int, ctype: str, payload: bytes) -> None:
            self._headers(status, ctype, len(payload))
            self.wfile.write(payload)

        def _json(self, status: int, data: object) -> None:
            self._send(status, "application/json; charset=utf-8", dumps(data))

        def do_OPTIONS(self) -> None:  # noqa: N802 - CORS preflight (the token is the protection)
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Neos75-Token")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            self._dispatch("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._dispatch("POST")

        def do_PUT(self) -> None:  # noqa: N802
            self._dispatch("PUT")

        def _dispatch(self, method: str) -> None:
            url = urlparse(self.path)
            query = parse_qs(url.query)
            try:
                host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
                if host not in ("127.0.0.1", "localhost"):
                    raise ApiError(403, "bad host")
                if url.path == "/api/health":
                    self._json(200, {"ok": True, "api": API_VERSION, "pid": os.getpid(), "fake": service.engine.fake})
                    return
                token = self.headers.get("X-Neos75-Token") or _q(query, "token")
                if not secrets.compare_digest(token, service.token):
                    raise ApiError(401, "missing or wrong token (see service.json)")
                if url.path == "/api/events" and method == "GET":
                    self._events()
                    return
                body = None
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    try:
                        body = json.loads(self.rfile.read(length).decode("utf-8"))
                    except ValueError as exc:
                        raise ApiError(400, f"bad JSON: {exc}") from exc
                result = service.route(method, url.path, query, body)
                if isinstance(result, tuple):
                    status, ctype, payload = result
                    self._send(status, ctype, payload)
                else:
                    self._json(200, result)
            except ApiError as exc:
                self._json(exc.status, {"error": str(exc), "status": exc.status})
            except ValueError as exc:
                self._json(400, {"error": str(exc), "status": 400})
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
            except Exception as exc:  # noqa: BLE001
                self._json(500, {"error": repr(exc), "status": 500})

        def _events(self) -> None:
            """Server-sent events: one `data: {json}` per event, a status snapshot first."""
            self.close_connection = True
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Connection", "close")
            self.end_headers()
            sub = service.engine.bus.subscribe()
            try:
                self._event({"type": "status", "time": time.time(), **service.engine.status(service.port)})
                while not service.stopping.is_set():
                    try:
                        event = sub.get(timeout=SSE_PING_S)
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                        continue
                    self._event(event)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                pass
            finally:
                service.engine.bus.unsubscribe(sub)

        def _event(self, event: dict) -> None:
            self.wfile.write(b"data: " + dumps(event) + b"\n\n")
            self.wfile.flush()

    return Handler


def _exit_with_parent(pid: int, stop, log) -> None:  # noqa: ANN001
    """Stop when the process that started the service ends (NEOS75_PARENT_PID, set by the desktop app): a
    killed app (Task Manager, an installer replacing it) must not leave the service holding the keyboard and
    the app's files."""
    if sys.platform != "win32":
        return
    import ctypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    handle = kernel32.OpenProcess(0x00100000, False, pid)        # SYNCHRONIZE
    if not handle:
        log(f"app process {pid} not found: the service keeps running on its own")
        return

    def wait() -> None:
        if kernel32.WaitForSingleObject(handle, 0xFFFFFFFF) == 0:    # WAIT_OBJECT_0: it ended
            log(f"the desktop app (pid {pid}) is gone: stopping")
            stop()

    threading.Thread(target=wait, name="parent-watch", daemon=True).start()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="neos75d", description="Neos75 screen service (localhost API)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"default {DEFAULT_PORT}; 0 picks a free port")
    parser.add_argument("--fake", action="store_true", help="use the protocol-level fake screen (no keyboard needed)")
    parser.add_argument("--no-run", action="store_true", help="do not start the widget runner (settings.run_on_start)")
    parser.add_argument("--no-preview", action="store_true", help="disable page previews (no browser)")
    args = parser.parse_args(argv)
    if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if sys.stderr is not None:
        import faulthandler
        # A native crash (2026-11-08: access violation in _ctypes after 12 min) left no trace in the log; this
        # writes every thread's Python stack to stderr (the app's neos75d.log) before the process dies.
        faulthandler.enable(sys.stderr, all_threads=True)

    from .client import Client, ServiceUnavailable
    try:
        running = Client.discover(timeout=1.0)
        print(f"neos75d is already running (pid {running.info.get('pid')}, port {running.port})", file=sys.stderr)
        return 1
    except ServiceUnavailable:
        pass

    settings = Settings.load()
    previewer = None if args.no_preview else Previewer(cache_dir(), settings.dirs, log=lambda m: engine.log(m))
    engine = Engine(settings, fake=args.fake, previewer=previewer)
    try:
        service = Service(engine, args.port, discovery=discovery_file())
    except OSError as exc:
        print(f"cannot listen on 127.0.0.1:{args.port}: {exc}", file=sys.stderr)
        engine.close()
        return 1

    def stop(*_: object) -> None:
        service.shutdown()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, stop)
    parent = os.environ.get("NEOS75_PARENT_PID", "")
    if parent.isdigit():
        _exit_with_parent(int(parent), stop, engine.log)
    engine.log(f"neos75d listening on http://127.0.0.1:{service.port} ({'fake screen' if args.fake else 'keyboard screen'})")
    # after the keyboard status is known (it is part of the preview key)
    warm = threading.Timer(8.0, lambda: engine.warm_previews())
    warm.daemon = True
    warm.start()
    if settings.run_on_start and not args.no_run:
        engine.start()
    thread = service.start_background()
    while thread.is_alive():
        thread.join(0.5)
    engine.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
