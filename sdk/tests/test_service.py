"""neos75d: the HTTP API, the engine with the fake screen, the catalog and the neos75ctl CLI.

Everything runs against the protocol-level fake device and a temporary NEOS75_HOME, so the real
selection file and the real keyboard are never touched. Tests marked `browser` start headless Chrome.
"""
from __future__ import annotations

import json
import shutil
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

from neos75screen.live import library
from neos75screen.service import catalog
from neos75screen.service import cli as ctl
from neos75screen.service.client import ApiFailure, Client, ServiceUnavailable
from neos75screen.service.engine import Engine
from neos75screen.service.paths import Settings
from neos75screen.service.server import Service

HAS_BROWSER = shutil.which("chrome") is not None or Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe").exists()
try:
    import playwright  # noqa: F401
except ImportError:
    HAS_BROWSER = False
browser = pytest.mark.skipif(not HAS_BROWSER, reason="needs Playwright and Chrome")


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("NEOS75_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def service(home: Path) -> Iterator[tuple[Service, Client]]:
    engine = Engine(Settings(run_on_start=False), fake=True, config=home / "pages.json",
                    settings_path=home / "settings.json", log_stdout=False)
    svc = Service(engine, port=0, discovery=home / "service.json")
    thread = svc.start_background()
    for _ in range(100):
        if (home / "service.json").exists():
            break
        time.sleep(0.02)
    client = Client.discover(home / "service.json", timeout=10)
    yield svc, client
    svc.shutdown()
    thread.join(10)


def wait_for(predicate, timeout: float = 30.0, step: float = 0.1):  # noqa: ANN001, ANN201
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = predicate()
        if value:
            return value
        time.sleep(step)
    raise AssertionError("timed out")


# --- catalog ---------------------------------------------------------------------------------------------

def test_entry_split_and_join_roundtrip() -> None:
    name, options = catalog.split("neos_photo?src=D:/pictures/a b.jpg&full=1")
    assert name == "neos_photo" and options == {"src": "D:/pictures/a b.jpg", "full": "1"}
    entry = catalog.join(name, {**options, "fit": ""})          # empty values are left out
    assert catalog.split(entry) == (name, options)
    assert "D:/pictures/a%20b.jpg" in entry                       # paths stay readable, spaces as %20
    from neos75screen.live.library import Widget
    assert Widget(Path("x.html").resolve(), entry.partition("?")[2]).url.endswith("src=D:/pictures/a%20b.jpg&full=1")
    assert catalog.join("neos_clock", {}) == "neos_clock"


def test_widgets_have_titles_groups_and_options() -> None:
    items = {w["name"]: w for w in catalog.widgets()}
    assert items["neos_clock"]["title"] == "时钟" and items["neos_clock"]["group"] == "neos"
    assert items["hifi_clock"]["group"] == "classic"
    keys = [o["key"] for o in items["neos_photo"]["options"]]
    assert keys == ["src", "full", "fit", "pill"]
    assert items["neos_photo"]["status_bar"] is False and items["neos_hifi_player"]["fps"] == 20.0


def test_page_meta_tags_describe_a_page(tmp_path: Path) -> None:
    page = tmp_path / "weather.html"
    page.write_text('<!doctype html><html><head><meta name="neos75:title" content="天气">'
                    '<meta name="neos75:description" content="今天的天气">'
                    """<meta name="neos75:options" content='[{"key": "city", "type": "text"}, {"bad": 1}]'>"""
                    '</head><body data-fps="5" data-status="off"></body></html>', encoding="utf-8")
    info = catalog.describe("weather", page, [tmp_path])
    assert info["title"] == "天气" and info["description"] == "今天的天气" and info["group"] == "extra"
    assert info["options"] == [{"key": "city", "type": "text"}] and info["fps"] == 5.0 and not info["status_bar"]
    assert "weather" in [w["name"] for w in catalog.widgets([tmp_path])]


def test_selection_labels_follow_the_stock_pages() -> None:
    pages = catalog.selection(["neos_clock", "neos_photo?full=1", "nope"])
    assert [p["label"] for p in pages] == ["Page 7", "Page 8", "Page 9"]
    assert pages[1]["options"] == {"full": "1"} and pages[1]["title"] == "图片"
    assert pages[2]["known"] is False


def test_settings_validate_and_persist(home: Path) -> None:
    settings = Settings.load()
    assert settings.run_on_start is True and settings.quality == 88
    with pytest.raises(ValueError):
        Settings().update({"quality": 5})
    with pytest.raises(ValueError):
        Settings().update({"run_on_start": "yes"})
    settings.update({"interval": 2, "unknown": 1}).save()
    assert Settings.load().interval == 2.0
    (home / "settings.json").write_text("{broken", encoding="utf-8")
    assert Settings.load().interval == 1.0                      # a broken file falls back to defaults


# --- HTTP API --------------------------------------------------------------------------------------------

def _raw(port: int, path: str, headers: dict | None = None) -> int:
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code


def test_api_needs_the_token_and_a_local_host(service) -> None:  # noqa: ANN001
    svc, api = service
    assert _raw(svc.port, "/api/health") == 200
    assert _raw(svc.port, "/api/status") == 401
    assert _raw(svc.port, "/api/status", {"X-Neos75-Token": "wrong"}) == 401
    assert _raw(svc.port, f"/api/status?token={svc.token}") == 200
    assert _raw(svc.port, "/api/health", {"Host": "evil.example:80"}) == 403
    assert api.get("/api/status")["service"]["fake"] is True


def test_status_before_the_runner_starts(service) -> None:  # noqa: ANN001
    _, api = service
    status = api.get("/api/status")
    assert status["runner"]["state"] == "stopped" and status["device"]["state"] == "idle"
    assert [p["entry"] for p in status["pages"]] == library.DEFAULT_PAGES
    assert status["keyboard"] == {"reported": False, "simulated": True}


def test_pages_api_edits_the_selection_file(service, home: Path) -> None:  # noqa: ANN001
    _, api = service
    api.put("/api/pages", {"pages": ["neos_clock", "neos_system"]})
    assert json.loads((home / "pages.json").read_text(encoding="utf-8"))["pages"] == ["neos_clock", "neos_system"]
    api.post("/api/pages/add", {"entry": "neos_photo?full=1", "position": 0})
    api.post("/api/pages/move", {"from": 0, "to": 2})
    pages = api.post("/api/pages/remove", {"index": 0})
    assert [p["entry"] for p in pages] == ["neos_system", "neos_photo?full=1"]
    pages = api.post("/api/pages/replace", {"index": 1, "entry": "neos_photo?full=1&fit=contain"})
    assert pages[1]["options"] == {"full": "1", "fit": "contain"} and pages[1]["label"] == "Page 8"
    with pytest.raises(ApiFailure) as err:
        api.put("/api/pages", {"pages": ["nope"]})
    assert err.value.status == 400 and "unknown widget" in str(err.value)
    with pytest.raises(ApiFailure):
        api.put("/api/pages", {"pages": ["neos_clock"] * 9})
    with pytest.raises(ApiFailure):
        api.post("/api/pages/remove", {"index": 7})
    with pytest.raises(ApiFailure):
        api.put("/api/pages", {"pages": []})
    assert [p["entry"] for p in api.get("/api/pages")] == ["neos_system", "neos_photo?full=1&fit=contain"]


def test_settings_api(service, home: Path) -> None:  # noqa: ANN001
    _, api = service
    assert api.put("/api/settings", {"quality": 80})["quality"] == 80
    assert json.loads((home / "settings.json").read_text(encoding="utf-8"))["quality"] == 80
    with pytest.raises(ApiFailure) as err:
        api.put("/api/settings", {"interval": 0})
    assert err.value.status == 400


def test_knob_and_screen_need_a_running_fake(service) -> None:  # noqa: ANN001
    _, api = service
    with pytest.raises(ApiFailure) as err:
        api.post("/api/knob", {"direction": 1})
    assert err.value.status == 409
    with pytest.raises(ApiFailure) as err:
        api.get("/api/screen.jpg", raw=True)
    assert err.value.status == 404
    with pytest.raises(ApiFailure) as err:
        api.get("/api/nothing")
    assert err.value.status == 404


def test_event_stream_starts_with_status_and_reports_selection_changes(service) -> None:  # noqa: ANN001
    _, api = service
    events: list[dict] = []

    def listen() -> None:
        for event in api.events():
            events.append(event)
            if event["type"] == "selection":
                return

    thread = threading.Thread(target=listen, daemon=True)
    thread.start()
    wait_for(lambda: events)
    assert events[0]["type"] == "status" and "runner" in events[0]
    api.put("/api/pages", {"pages": ["neos_nixie"]})
    thread.join(10)
    assert events[-1]["type"] == "selection" and events[-1]["pages"][0]["entry"] == "neos_nixie"


def test_selection_file_edited_elsewhere_is_announced(service, home: Path) -> None:  # noqa: ANN001
    svc, _ = service
    sub = svc.engine.bus.subscribe()
    library.save(home / "pages.json", ["neos_cassette"])         # e.g. `neos75 pages` in another terminal
    event = wait_for(lambda: next((e for e in _drain(sub) if e["type"] == "selection"), None), timeout=5)
    assert event["pages"][0]["entry"] == "neos_cassette"


def _drain(q) -> list[dict]:  # noqa: ANN001
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


def test_discovery_file_is_removed_on_shutdown(home: Path) -> None:
    engine = Engine(Settings(run_on_start=False), fake=True, config=home / "pages.json", log_stdout=False)
    svc = Service(engine, port=0, discovery=home / "service.json")
    thread = svc.start_background()
    wait_for(lambda: (home / "service.json").exists(), timeout=5)
    Client.discover(home / "service.json").post("/api/shutdown")
    thread.join(10)
    assert not (home / "service.json").exists()
    with pytest.raises(ServiceUnavailable):
        Client.discover(home / "service.json")


@browser
def test_runner_on_the_fake_screen(service) -> None:  # noqa: ANN001
    """Start the real runner (headless Chrome) on the fake screen: frames, keyboard, knob, mirror, stop."""
    svc, api = service
    api.put("/api/pages", {"pages": ["neos_clock", "neos_system"]})
    api.post("/api/runner/start")
    status = wait_for(lambda: (s := api.get("/api/status"))["runner"]["frames"] > 0 and s, timeout=60)
    assert status["device"]["state"] == "connected" and status["runner"]["paged"] is True
    assert status["runner"]["page"] == "neos_clock" and status["runner"]["label"] == "Page 7"
    assert api.get("/api/screen.jpg", raw=True)[:2] == b"\xff\xd8"
    keyboard = api.get("/api/status")["keyboard"]
    assert keyboard["simulated"] is True and keyboard["reported"] is False   # a demo never invents a battery
    api.post("/api/fake/keyboard", {"battery": 55, "link": 5})
    svc.engine._runner._keyboard_at -= 10                        # next poll now, not in 5 s
    keyboard = wait_for(lambda: (k := api.get("/api/status")["keyboard"]).get("battery") == 55 and k, timeout=10)
    assert keyboard["simulated"] is True and keyboard["connection"] == "wired"
    api.post("/api/knob", {"direction": 1})                      # turn to Page 8
    wait_for(lambda: api.get("/api/status")["runner"]["page"] == "neos_system", timeout=10)
    api.post("/api/knob", {"direction": 1})                      # past the last host page: a stock page
    wait_for(lambda: api.get("/api/status")["runner"]["visible"] is False, timeout=10)
    api.post("/api/pages/add", {"entry": "neos_nixie"})          # hot reload: 3 pages registered
    wait_for(lambda: svc.engine._fake_device is not None and svc.engine._fake_device.pages == 3, timeout=20)
    api.post("/api/runner/stop")
    status = api.get("/api/status")
    assert status["runner"]["state"] == "stopped" and status["device"]["state"] == "idle"


@browser
def test_preview_endpoint_renders_and_caches(home: Path) -> None:
    from neos75screen.service.preview import Previewer
    previewer = Previewer(home / "previews", log=lambda *_: None)
    engine = Engine(Settings(run_on_start=False), fake=True, config=home / "pages.json", previewer=previewer,
                    log_stdout=False)
    svc = Service(engine, port=0, discovery=home / "service.json")
    thread = svc.start_background()
    try:
        api = wait_for(lambda: (home / "service.json").exists() and Client.discover(home / "service.json"), timeout=5)
        png = api.preview("neos_clock", width=240)
        assert png[:4] == b"\x89PNG"
        from PIL import Image
        import io
        assert Image.open(io.BytesIO(png)).size == (240, 180)
        assert len(list((home / "previews").glob("*.png"))) == 1
        with pytest.raises(ApiFailure) as err:
            api.preview("nope")
        assert err.value.status == 404
    finally:
        svc.shutdown()
        thread.join(15)


# --- neos75ctl -------------------------------------------------------------------------------------------

def run_cli(capsys: pytest.CaptureFixture, *argv: str) -> tuple[int, object]:
    code = ctl.main(["--json", *argv] if argv[0] != "--text" else list(argv[1:]))
    out = capsys.readouterr().out
    try:
        return code, json.loads(out)
    except ValueError:
        return code, out


def test_cli_pages_without_a_service_edit_the_file(home: Path, capsys: pytest.CaptureFixture) -> None:
    code, status = run_cli(capsys, "status")
    assert code == 0 and status["service"] is None
    code, pages = run_cli(capsys, "pages", "set", "neos_clock,neos_system", "neos_player")
    assert code == 0 and [p["entry"] for p in pages] == ["neos_clock", "neos_system", "neos_player"]
    code, pages = run_cli(capsys, "pages", "add", "neos_nixie", "--at", "8")
    assert [p["entry"] for p in pages] == ["neos_clock", "neos_nixie", "neos_system", "neos_player"]
    code, pages = run_cli(capsys, "pages", "move", "Page 10", "7")
    assert [p["entry"] for p in pages][0] == "neos_player"
    code, pages = run_cli(capsys, "pages", "remove", "neos_nixie")
    assert "neos_nixie" not in [p["entry"] for p in pages]
    code, pages = run_cli(capsys, "pages", "add", "neos_photo")
    code, pages = run_cli(capsys, "pages", "options", "10", "src=D:/a.jpg", "full=1")
    assert pages[3]["entry"] == "neos_photo?src=D:/a.jpg&full=1"
    code, pages = run_cli(capsys, "pages", "options", "10", "full=")
    assert pages[3]["entry"] == "neos_photo?src=D:/a.jpg"
    assert library.load(home / "pages.json")[3] == "neos_photo?src=D:/a.jpg"


def test_cli_errors_are_json_with_exit_codes(home: Path, capsys: pytest.CaptureFixture) -> None:
    code, err = run_cli(capsys, "pages", "set", "nope")
    assert code == ctl.EXIT_USAGE and "unknown widget" in err["error"]
    code, err = run_cli(capsys, "pages", "remove", "20")
    assert code == ctl.EXIT_USAGE and "Page 7" in err["error"]
    code, err = run_cli(capsys, "start")
    assert code == ctl.EXIT_NO_SERVICE and "neos75d" in err["error"]
    code, err = run_cli(capsys, "knob", "next")
    assert code == ctl.EXIT_NO_SERVICE


def test_cli_through_the_service(service, capsys: pytest.CaptureFixture) -> None:  # noqa: ANN001
    _, api = service
    code, status = run_cli(capsys, "status")
    assert code == 0 and status["service"]["fake"] is True and status["runner"]["state"] == "stopped"
    code, widgets = run_cli(capsys, "widgets", "--group", "neos")
    assert code == 0 and {w["group"] for w in widgets} == {"neos"} and len(widgets) >= 9
    code, pages = run_cli(capsys, "pages", "set", "neos_cassette", "neos_clock")
    assert [p["entry"] for p in api.get("/api/pages")] == ["neos_cassette", "neos_clock"]
    code, settings = run_cli(capsys, "settings", "interval=0.5", "run_on_start=false")
    assert settings["interval"] == 0.5 and settings["run_on_start"] is False
    code, err = run_cli(capsys, "knob", "next")
    assert code == ctl.EXIT_ERROR and "start the runner" in err["error"]
    code, text = run_cli(capsys, "--text", "status")
    assert code == 0 and "Page 7" in text and "neos_cassette" in text


def test_cli_new_page_from_the_template(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    folder = tmp_path / "custom"
    code, made = run_cli(capsys, "new", "my_cpu", "--title", "我的 CPU", "--folder", str(folder))
    assert code == 0 and Path(made["path"]).is_file()
    info = catalog.describe("my_cpu", Path(made["path"]))
    assert info["title"] == "我的 CPU" and info["options"][0]["key"] == "label"
    code, err = run_cli(capsys, "new", "my_cpu", "--folder", str(folder))
    assert code == ctl.EXIT_USAGE and "exists" in err["error"]
    code, err = run_cli(capsys, "new", "neos_clock", "--folder", str(folder))
    assert code == ctl.EXIT_USAGE and "already exists" in err["error"]
    code, err = run_cli(capsys, "new", "9bad name")
    assert code == ctl.EXIT_USAGE


@browser
def test_cli_check_reports_a_good_page(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    code, report = run_cli(capsys, "check", "neos_clock", "-o", str(tmp_path / "clock.png"))
    assert code == 0 and report["ok"] and report["has_update"] and (tmp_path / "clock.png").stat().st_size > 1000


@browser
def test_cli_check_reports_script_errors(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    bad = tmp_path / "bad.html"
    bad.write_text("<html><body><script>window.neos75Update = d => { throw new Error('boom') }</script></body></html>",
                   encoding="utf-8")
    code, report = run_cli(capsys, "check", str(bad))
    assert code == ctl.EXIT_ERROR and not report["ok"] and any("boom" in p for p in report["problems"])


def test_knob_from_the_pc_on_the_keyboard(home: Path) -> None:
    """Without --fake the knob turns through Screen.inject_knob when the firmware can (HELLO test_knob)."""
    from types import SimpleNamespace
    engine = Engine(Settings(run_on_start=False), fake=False, config=home / "pages.json", log_stdout=False)
    try:
        with pytest.raises(RuntimeError):
            engine.turn_knob(1)                       # no screen yet
        steps: list[int] = []
        screen = SimpleNamespace(capabilities=SimpleNamespace(test_knob=True), inject_knob=steps.append)
        engine._screen = screen
        engine.turn_knob(1); engine.turn_knob(-1)
        assert steps == [1, -1]
        with pytest.raises(PermissionError):
            engine.turn_knob(0)                       # a press is fake-device only
        with pytest.raises(ValueError):
            engine.turn_knob(2)
        screen.capabilities.test_knob = False
        with pytest.raises(PermissionError):
            engine.turn_knob(1)                       # older firmware
        assert steps == [1, -1]
    finally:
        engine._screen = None
        engine.close()


def test_previews_serve_the_cache_and_refresh_stale_ones_in_the_background(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    """A cached preview is returned at once; an old one too, and it is rendered again behind the scenes."""
    import io as _io
    import os as _os
    import time as _time

    from PIL import Image as _Image

    from neos75screen.service import preview as pv

    renders: list[tuple[str, int]] = []

    def fake_work(self) -> None:  # noqa: ANN001 - stands in for the browser worker
        while True:
            priority, _, item = self._queue.get()
            if item is None:
                return
            widget, label, data, future = item
            if future.set_running_or_notify_cancel():
                renders.append((widget.name, priority))
                out = _io.BytesIO(); _Image.new("RGB", (480, 360), (len(renders), 0, 0)).save(out, "PNG")
                future.set_result(out.getvalue())

    monkeypatch.setattr(pv.Previewer, "_work", fake_work)
    p = pv.Previewer(tmp_path)
    try:
        first = p.render("neos_clock")
        assert renders == [("neos_clock", pv.WAIT)]
        wait_for(lambda: list(tmp_path.glob("*.png")), timeout=5)
        assert p.render("neos_clock") == first and len(renders) == 1          # cached: no render
        cached = next(tmp_path.glob("*.png"))
        old = _time.time() - pv.REFRESH_S - 10
        _os.utime(cached, (old, old))
        assert p.render("neos_clock") == first                                 # stale: served at once...
        wait_for(lambda: cached.stat().st_mtime > old + 5, timeout=5)          # ...and refreshed behind
        assert renders[-1] == ("neos_clock", pv.REFRESH) and cached.read_bytes() != first
        assert p.warm([("neos_clock", "Page 7"), ("neos_system", "Page 8"), ("no_such_page", "x")]) == 1
        wait_for(lambda: renders[-1] == ("neos_system", pv.WARM), timeout=5)
    finally:
        p.close()


def test_knob_settings_api(service, home: Path) -> None:  # noqa: ANN001
    svc, api = service
    data = api.get("/api/knobs")
    assert data["config"]["screen"]["cw"] == {"action": "default"} and data["config"]["enabled"] is True
    assert {"keys", "run", "page_next"} <= {a["id"] for a in data["catalog"]["actions"]}
    assert data["status"]["hook"] is False and data["status"]["takeover"] is False
    saved = api.put("/api/knobs", {"config": {"screen": {"cw": "page_prev", "ccw": "page_next"}}})
    assert saved["config"]["screen"]["cw"] == {"action": "page_prev"}
    assert json.loads((home / "knobs.json").read_text(encoding="utf-8"))["screen"]["ccw"] == {"action": "page_next"}
    with pytest.raises(ApiFailure) as err:
        api.put("/api/knobs", {"config": {"volume": {"cw": {"action": "keys", "keys": "ctrl+nope"}}}})
    assert err.value.status == 400
    assert svc.engine.knobs.config["screen"]["cw"] == {"action": "page_prev"}   # the bad one changed nothing


def test_screen_knob_turns_mapped_on_the_fake_screen(home: Path) -> None:
    """Without the runner: the engine's knob mapper takes the fake screen's knob over and pages backwards."""
    from neos75screen import knobmap
    from neos75screen.screen import Screen
    from neos75screen.transport import MockTransport
    engine = Engine(Settings(run_on_start=False), fake=True, config=home / "pages.json", log_stdout=False)
    try:
        engine.set_knob_settings({"screen": {"cw": "page_prev", "ccw": "page_next"}})
        fake_module = engine._fake_module or __import__("neos75screen.service.engine", fromlist=["x"])._load_fake_module()
        fake = fake_module.FakeScreen(profile="v2-knob", host_pages=True, knob_takeover=True)
        transport = MockTransport(fake)
        fake.attach(transport)
        screen = Screen.open(transport)
        screen.on_knob(engine._on_knob)
        screen.enter()
        screen.set_pages(3)
        engine._screen, engine._fake_device = screen, fake
        engine.knobs.screen_session()
        wait_for(lambda: fake.takeover, timeout=5)
        fake.knob(-1)                                    # counter-clockwise: mapped to "next page"
        wait_for(lambda: fake.host_page == 1, timeout=5)
        fake.knob(+1)
        wait_for(lambda: fake.host_page == 0, timeout=5)
        engine.set_knob_settings(knobmap.default_config())
        wait_for(lambda: not fake.takeover, timeout=5)
        fake.knob(+1)                                    # stock paging again
        wait_for(lambda: fake.host_page == 1, timeout=5)
        screen.close()
        fake.stop()
    finally:
        engine._screen = engine._fake_device = None
        engine.close()
