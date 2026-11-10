"""What the installed desktop app (tools/build_app.py) relies on: page folders from the environment, a
browser fallback, and a service port no second service can share."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler

import pytest

from neos75screen.live import library
from neos75screen.service.server import _HTTPServer


def test_page_folders_come_from_the_environment(tmp_path):
    root, custom = tmp_path / "res", tmp_path / "repo_custom"
    code = ("import json; from neos75screen.live import library as l; "
            "print(json.dumps([str(l.ROOT), str(l.CUSTOM_DIR), [str(d) for d in l.LIBRARY_DIRS]]))")
    env = {**os.environ, "NEOS75_ROOT": str(root), "NEOS75_CUSTOM": str(custom)}
    out = subprocess.run([sys.executable, "-c", code], env=env, check=True, capture_output=True, text=True)
    got_root, got_custom, dirs = json.loads(out.stdout)
    assert got_root == str(root) and got_custom == str(custom)
    assert dirs == [str(root / "design" / "neos"), str(root / "design" / "widgets"), str(custom)]


class _Chromium:
    def __init__(self, installed):
        self.installed, self.tried = installed, []

    def launch(self, channel=None, headless=True, args=()):
        self.tried.append(channel)
        if channel not in self.installed:
            raise RuntimeError(f"{channel} is not installed")
        return f"browser:{channel}"


class _PW:
    def __init__(self, installed):
        self.chromium = _Chromium(installed)


def test_pages_render_in_edge_without_chrome():
    pw = _PW({"msedge"})
    assert library.launch_browser(pw, ["--x"]) == "browser:msedge"
    assert pw.chromium.tried == ["chrome", "msedge"]


def test_no_browser_at_all_says_so():
    with pytest.raises(RuntimeError, match="no browser"):
        library.launch_browser(_PW(set()), [])


def test_a_second_service_cannot_take_the_same_port():
    first = _HTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    try:
        with pytest.raises(OSError):
            _HTTPServer(("127.0.0.1", first.server_address[1]), BaseHTTPRequestHandler)
    finally:
        first.server_close()


def test_the_preview_browser_closes_when_idle_and_comes_back(tmp_path, monkeypatch):
    """The preview browser (about 0.4 GB) is not kept open between renders; the next request starts it."""
    import io
    import time

    from PIL import Image

    from neos75screen.live import cdp
    from neos75screen.service import preview as pv

    png = io.BytesIO()
    Image.new("RGB", (480, 360)).save(png, "PNG")
    events: list[str] = []

    class Page:
        def goto(self, *a, **k): pass
        def wait_for_function(self, *a, **k): pass
        def evaluate(self, *a, **k): pass
        def screenshot(self, **k): return png.getvalue()
        def close(self): pass

    class Browser:
        def __init__(self): events.append("open")
        def new_page(self, **k): return Page()
        def close(self): events.append("close")

    monkeypatch.setattr(cdp, "launch", lambda args: Browser())
    monkeypatch.setattr(pv, "IDLE_S", 0.3)
    monkeypatch.setattr(pv, "SETTLE_S", {"neos_clock": 0})
    monkeypatch.setattr(pv.time, "sleep", lambda s: None)
    p = pv.Previewer(None)
    try:
        assert p.render("neos_clock", fresh=True) == png.getvalue()
        deadline = time.monotonic() + 5
        while events != ["open", "close"] and time.monotonic() < deadline:
            time.sleep(0.05)
        assert events == ["open", "close"]          # idle: closed
        assert p.render("neos_clock", fresh=True) == png.getvalue()
        assert events[:3] == ["open", "close", "open"]   # started again for the next request
    finally:
        p.close()


def test_pages_render_over_devtools_without_playwright():
    """live/cdp.py: load a page, run a function with an argument, take a 480x360 screenshot, report errors."""
    import io

    from PIL import Image

    from neos75screen.live import cdp

    try:
        cdp.find_browser()
    except cdp.CDPError:
        pytest.skip("no Chrome or Edge")
    browser = cdp.launch(["--allow-file-access-from-files", "--process-per-site"])
    try:
        page = browser.new_page()
        page.goto(library.resolve(["neos_clock"])[0].url)
        page.wait_for_function("document.readyState === 'complete'", timeout=5)
        assert page.evaluate("d => d.a + d.b", {"a": 2, "b": 3}) == 5
        assert page.evaluate("() => [innerWidth, innerHeight]") == [480, 360]
        with pytest.raises(cdp.CDPError, match="boom"):
            page.evaluate("() => { throw new Error('boom') }")
        other = browser.new_page()            # a tab that is not in front can still be captured
        other.goto(library.resolve(["neos_system"])[0].url)
        for tab in (page, other):
            assert Image.open(io.BytesIO(tab.screenshot())).size == (480, 360)
        other.close()
    finally:
        browser.close()
    assert browser.process.poll() is not None


def test_the_browser_ends_with_a_killed_service():
    """No Chrome left running when the service is killed (Task Manager, a crash): a job object ends it."""
    import subprocess
    import sys
    import time

    from neos75screen.live import cdp

    try:
        cdp.find_browser()
    except cdp.CDPError:
        pytest.skip("no Chrome or Edge")
    child = subprocess.Popen([sys.executable, "-c", "import time; from neos75screen.live import cdp; "
                              "b = cdp.launch([]); print(b.process.pid, flush=True); time.sleep(60)"],
                             stdout=subprocess.PIPE, text=True)
    try:
        pid = int(child.stdout.readline())
    finally:
        child.kill()
        child.wait()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        tasks = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        if str(pid) not in tasks:
            return
        time.sleep(0.2)
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    pytest.fail("the browser outlived the killed service")
