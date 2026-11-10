"""A small Chrome DevTools Protocol client: the headless browser the widget runner and the previews render in.

It replaces Playwright there (Playwright stays for `neos75ctl check`). Playwright runs a Node.js driver
process next to the browser (about 65 MB private memory, all the time); the pages here need only six things:
open a tab, load a file:// page, wait for a condition, run a function, take a PNG screenshot, close the tab.
Those are a few DevTools commands over one WebSocket (`websocket-client`), so this module launches Chrome (or
Edge) itself, with Playwright's switches for automation, and talks to it directly. Thread-safe: any thread may
use any page.

Usage:
    browser = cdp.launch(["--process-per-site"])
    page = browser.new_page()                      # 480x360, scale 1
    page.goto("file:///.../neos_clock.html")
    page.wait_for_function("document.fonts.status === 'loaded'", timeout=5)
    page.evaluate("d => window.neos75Update(d)", {"cpu": 37})
    png = page.screenshot()
    browser.close()
"""
from __future__ import annotations

import base64
import itertools
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Sequence
from concurrent.futures import Future
from pathlib import Path

import websocket

# Playwright's switches for an automated Chrome (chromiumSwitches + headless), minus what only matters to
# its own features: no background throttling of tabs that are not in front, no first-run or update work, no
# network chatter, scrollbars hidden, sound muted.
SWITCHES = [
    "--headless", "--hide-scrollbars", "--mute-audio",
    "--blink-settings=primaryHoverType=2,availableHoverTypes=2,primaryPointerType=4,availablePointerTypes=4",
    "--disable-field-trial-config", "--disable-background-networking", "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows", "--disable-back-forward-cache", "--disable-breakpad",
    "--disable-client-side-phishing-detection", "--disable-component-extensions-with-background-pages",
    "--disable-component-update", "--no-default-browser-check", "--disable-default-apps",
    "--disable-dev-shm-usage", "--disable-edgeupdater", "--disable-extensions",
    "--disable-features=AvoidUnnecessaryBeforeUnloadCheckSync,DestroyProfileOnBrowserClose,DialMediaRouteProvider,"
    "GlobalMediaControls,HttpsUpgrades,LensOverlay,MediaRouter,PaintHolding,ThirdPartyStoragePartitioning,"
    "Translate,AutoDeElevate,OptimizationHints,NetworkTimeServiceQuerying,AimEnabled,msForceBrowserSignIn",
    "--enable-features=CDPScreenshotNewSurface",   # screenshots of tabs that are not in front
    "--allow-pre-commit-input", "--disable-hang-monitor", "--disable-ipc-flooding-protection",
    "--disable-popup-blocking", "--disable-prompt-on-repost", "--disable-renderer-backgrounding",
    "--disable-updater-scheduler", "--force-color-profile=srgb", "--metrics-recording-only", "--no-first-run",
    "--password-store=basic", "--no-service-autorun", "--disable-search-engine-choice-screen",
    "--edge-skip-compat-layer-relaunch", "--disable-infobars", "--disable-sync", "--enable-unsafe-swiftshader",
    "--no-sandbox",
]
CANDIDATES = [  # where Playwright looks for its "chrome" and "msedge" channels on Windows
    r"Google\Chrome\Application\chrome.exe",
    r"Microsoft\Edge\Application\msedge.exe",
]


class CDPError(RuntimeError):
    """A DevTools command failed, the page crashed, or the browser is gone."""


def find_browser() -> Path:
    """The system Chrome, else Edge (always on Windows 10/11), else a Chromium Playwright downloaded."""
    roots = [os.environ.get(k) for k in ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)")]
    for rel in CANDIDATES:
        for root in roots:
            if root and (Path(root) / rel).is_file():
                return Path(root) / rel
    local = os.environ.get("LOCALAPPDATA")
    if local:
        found = sorted((Path(local) / "ms-playwright").glob("chromium-*/chrome-win*/chrome.exe"))
        if found:
            return found[-1]
    raise CDPError("no browser to render pages: install Google Chrome (or Microsoft Edge)")


def launch(args: Sequence[str] = (), executable: str | os.PathLike | None = None, timeout: float = 30) -> Browser:
    return Browser(Path(executable) if executable else find_browser(), args, timeout)


class Browser:
    def __init__(self, exe: Path, args: Sequence[str], timeout: float) -> None:
        _remove_stale_profiles()
        self._profile = tempfile.mkdtemp(prefix="neos75-chrome-")
        self.process = subprocess.Popen(
            [str(exe), *SWITCHES, *args, f"--user-data-dir={self._profile}", "--remote-debugging-port=0",
             "about:blank"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self._job = _end_with_this_process(self.process)
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        self._pending: dict[int, Future] = {}
        self._sessions: dict[str, Page] = {}
        self._closed = False
        try:
            port, path = self._endpoint(timeout)
            self._ws = _connect(port, path)
        except BaseException:
            self._kill()
            raise
        self._reader = threading.Thread(target=self._read, name="cdp-reader", daemon=True)
        self._reader.start()
        # the blank tab Chrome starts with would hold a renderer process of its own
        for info in self.send("Target.getTargets")["targetInfos"]:
            if info["type"] == "page":
                self.send("Target.closeTarget", {"targetId": info["targetId"]})

    # --- pages ------------------------------------------------------------------------------------------

    def new_page(self, width: int = 480, height: int = 360, scale: float = 1, **_: object) -> Page:
        target = self.send("Target.createTarget", {"url": "about:blank"})["targetId"]
        session = self.send("Target.attachToTarget", {"targetId": target, "flatten": True})["sessionId"]
        page = Page(self, target, session)
        with self._lock:
            self._sessions[session] = page
        page.send("Page.enable")
        page.send("Emulation.setDeviceMetricsOverride",
                  {"width": width, "height": height, "deviceScaleFactor": scale, "mobile": False})
        page.send("Emulation.setFocusEmulationEnabled", {"enabled": True})  # every tab acts as the front one
        return page

    def close(self) -> None:
        if self._closed:
            return
        try:
            self.send("Browser.close", timeout=5)
        except CDPError:
            pass
        self._kill()

    # --- protocol ---------------------------------------------------------------------------------------

    def send(self, method: str, params: dict | None = None, session: str | None = None,
             timeout: float = 30) -> dict:
        if self._closed:
            raise CDPError("browser closed")
        msg_id = next(self._ids)
        future: Future = Future()
        with self._lock:
            self._pending[msg_id] = future
        message = {"id": msg_id, "method": method, "params": params or {}}
        if session:
            message["sessionId"] = session
        try:
            self._ws.send(json.dumps(message))
            return future.result(timeout)
        except TimeoutError as exc:
            raise CDPError(f"{method}: no answer in {timeout} s") from exc
        except (OSError, websocket.WebSocketException) as exc:
            raise CDPError(f"{method}: {exc}") from exc
        finally:
            with self._lock:
                self._pending.pop(msg_id, None)

    def post(self, method: str, params: dict | None = None, session: str | None = None) -> None:
        """Send without waiting for the answer (it is dropped): safe on the reader thread, e.g. to acknowledge
        screencast frames from an event handler."""
        if self._closed:
            return
        message = {"id": next(self._ids), "method": method, "params": params or {}}
        if session:
            message["sessionId"] = session
        try:
            self._ws.send(json.dumps(message))
        except (OSError, websocket.WebSocketException):
            pass

    def _read(self) -> None:
        error: Exception = CDPError("browser connection closed")
        try:
            while True:
                message = json.loads(self._ws.recv())
                if "id" in message:
                    with self._lock:
                        future = self._pending.get(message["id"])
                    if future is not None and not future.done():
                        if "error" in message:
                            future.set_exception(CDPError(message["error"].get("message", str(message["error"]))))
                        else:
                            future.set_result(message.get("result", {}))
                else:
                    with self._lock:
                        page = self._sessions.get(message.get("sessionId", ""))
                    if page is not None:
                        page._event(message["method"], message.get("params", {}))
        except Exception as exc:  # noqa: BLE001 - the browser went away: fail everything waiting
            if not self._closed:
                error = CDPError(f"browser connection lost: {exc}")
        self._closed = True
        with self._lock:
            pending, self._pending = list(self._pending.values()), {}
            pages = list(self._sessions.values())
        for future in pending:
            if not future.done():
                future.set_exception(error)
        for page in pages:
            page._fail(error)

    def _endpoint(self, timeout: float) -> tuple[int, str]:
        """Chrome writes the port it picked and the browser's WebSocket path to DevToolsActivePort."""
        marker = Path(self._profile) / "DevToolsActivePort"
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self.process.poll() is not None:
                raise CDPError(f"the browser exited at start (code {self.process.returncode})")
            try:
                lines = marker.read_text().split()
                if len(lines) >= 2:
                    return int(lines[0]), lines[1]
            except (OSError, ValueError):
                pass
            time.sleep(0.05)
        raise CDPError(f"the browser did not open its DevTools port in {timeout} s")

    def _kill(self) -> None:
        self._closed = True
        ws = getattr(self, "_ws", None)
        if ws is not None:
            try:
                ws.close()
            except (OSError, websocket.WebSocketException):
                pass
        try:
            self.process.wait(5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(5)
        if self._job:
            _close_handle(self._job)   # ends anything of the browser's still running
            self._job = None
        for _ in range(20):            # Chrome's child processes let go of the profile a moment later
            shutil.rmtree(self._profile, ignore_errors=True)
            if not os.path.exists(self._profile):
                break
            time.sleep(0.1)


def _remove_stale_profiles(max_age_s: float = 3600) -> None:
    """Profiles of browsers that ended with their service (a crash, a kill) stay in %TEMP%: remove old ones
    (a profile in use is locked and stays)."""
    old = time.time() - max_age_s
    for path in Path(tempfile.gettempdir()).glob("neos75-chrome-*"):
        try:
            if path.is_dir() and path.stat().st_mtime < old:
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            pass


def _end_with_this_process(process: subprocess.Popen) -> int | None:
    """Windows: put the browser in a job object that ends it when this process ends, however it ends (a
    crash, Task Manager). Its renderer and GPU processes end with it. Playwright's Node.js driver used to do
    this; without it a killed service left Chrome running. Returns the job handle (closing it ends the job)."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class Basic(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class Extended(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", ctypes.c_ulonglong * 6),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return None
    info = Extended()
    info.BasicLimitInformation.LimitFlags = 0x2000          # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    ok = kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)) and \
        kernel32.AssignProcessToJobObject(job, int(process._handle))  # 9: JobObjectExtendedLimitInformation
    if not ok:
        _close_handle(job)
        return None
    return job


def _close_handle(handle: int) -> None:
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle(handle)


class Page:
    def __init__(self, browser: Browser, target: str, session: str) -> None:
        self.browser, self.target, self.session = browser, target, session
        self._loaded = threading.Event()
        self._crash: CDPError | None = None

    def send(self, method: str, params: dict | None = None, timeout: float = 30) -> dict:
        if self._crash is not None:
            raise self._crash
        return self.browser.send(method, params, self.session, timeout)

    def goto(self, url: str, timeout: float = 20) -> None:
        """Load `url` and wait for its load event."""
        self._loaded.clear()
        result = self.send("Page.navigate", {"url": url}, timeout)
        if result.get("errorText"):
            raise CDPError(f"{url}: {result['errorText']}")
        if not self._loaded.wait(timeout):
            raise CDPError(f"{url}: not loaded in {timeout} s")
        if self._crash is not None:
            raise self._crash

    def evaluate(self, function: str, arg: object = None) -> object:
        """Call a JavaScript function (source text, e.g. "d => d.x + 1") with one JSON argument; returns its
        JSON result (promises are awaited)."""
        expression = f"({function})({json.dumps(arg) if arg is not None else ''})"
        result = self.send("Runtime.evaluate", {"expression": expression, "returnByValue": True,
                                                "awaitPromise": True})
        if "exceptionDetails" in result:
            details = result["exceptionDetails"]
            text = details.get("exception", {}).get("description") or details.get("text", "error")
            raise CDPError(f"script error: {text}")
        return result.get("result", {}).get("value")

    def wait_for_function(self, expression: str, timeout: float = 5, poll: float = 0.05) -> None:
        """Wait until the JavaScript expression is truthy; raises CDPError after `timeout` seconds."""
        end = time.monotonic() + timeout
        while True:
            if self.evaluate(f"() => !!({expression})"):
                return
            if time.monotonic() >= end:
                raise CDPError(f"waiting for {expression!r}: still false after {timeout} s")
            time.sleep(poll)

    def screenshot(self) -> bytes:
        """The viewport as PNG (still lossless). optimizeForSpeed: Chrome compresses less and encodes about twice as
        fast; measured 2026-11-10 at 12 fps, Chrome's CPU for capturing went from 0.29 to 0.12 of a core."""
        data = self.send("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False,
                                                    "optimizeForSpeed": True})["data"]
        return base64.b64decode(data)

    def close(self) -> None:
        try:
            self.browser.send("Target.closeTarget", {"targetId": self.target}, timeout=5)
        except CDPError:
            pass
        with self.browser._lock:
            self.browser._sessions.pop(self.session, None)

    def _event(self, method: str, params: dict) -> None:
        if method == "Page.loadEventFired":
            self._loaded.set()
        elif method == "Inspector.targetCrashed":
            self._fail(CDPError("Target crashed"))

    def _fail(self, error: CDPError) -> None:
        self._crash = error
        self._loaded.set()


def _connect(port: int, path: str) -> websocket.WebSocket:
    """The browser's DevTools WebSocket. No Origin header: Chrome refuses origins it was not started to allow."""
    try:
        ws = websocket.create_connection(f"ws://127.0.0.1:{port}{path}", timeout=10, suppress_origin=True,
                                         enable_multithread=True)
    except (OSError, websocket.WebSocketException) as exc:
        raise CDPError(f"DevTools WebSocket: {exc}") from exc
    ws.settimeout(None)
    return ws
