"""WidgetRunner's keyboard status polling (no browser: only the data it feeds the widgets)."""
from __future__ import annotations

from conftest import Connect
from neos75screen.live.runner import WidgetRunner


def _raw(mode: int, battery: int, link: int) -> bytes:
    raw = bytearray(26)
    raw[13], raw[14], raw[16] = mode, battery, link
    return bytes(raw)


def _polls(fake) -> int:  # noqa: ANN001
    return sum(1 for r in fake.log if r[:2] == b"\xA5\x3B" and r[5] == 0x05)


def test_keyboard_status_is_polled_at_the_interval(connect: Connect) -> None:
    screen, fake, _ = connect(profile="v2-knob", keyboard_status=_raw(2, 64, 5))
    runner = WidgetRunner(["x.html"], screen=screen, log=lambda *_: None)
    assert runner._keyboard_status() == {"kb_battery": 64, "kb_charging": True, "kb_link": 5}
    fake.keyboard_status = _raw(0, 63, 4)
    assert runner._keyboard_status()["kb_battery"] == 64 and _polls(fake) == 1   # within the interval
    runner._keyboard_at -= runner.keyboard_interval
    assert runner._keyboard_status() == {"kb_battery": 63, "kb_charging": False, "kb_link": 4}


def test_unreported_and_unsupported(connect: Connect) -> None:
    screen, _, _ = connect(profile="v2-knob", keyboard_status=bytes(26))
    assert WidgetRunner(["x.html"], screen=screen)._keyboard_status()["kb_battery"] is None
    screen, fake, _ = connect(profile="v2-knob")
    assert WidgetRunner(["x.html"], screen=screen)._keyboard_status() == {} and _polls(fake) == 0


def test_paged_runner_follows_page_events(connect: Connect) -> None:
    from neos75screen.protocol import KnobEvent, PageEvent
    screen, _, _ = connect(profile="v2-knob", host_pages=True)
    runner = WidgetRunner(["a.html", "b.html", "c.html"], screen=screen)
    assert runner.paged
    runner._on_knob(KnobEvent(0))                 # a press does not switch in page mode
    assert runner._next_index() is None
    runner._on_page(PageEvent(2, 3))
    assert runner._wake.is_set() and runner._next_index() == 2
    runner.index = 2
    runner._on_page(PageEvent(2, 3))
    assert runner._next_index() is None


def test_unpaged_runner_switches_on_press(connect: Connect) -> None:
    from neos75screen.protocol import KnobEvent
    screen, _, _ = connect(profile="v2-knob")
    runner = WidgetRunner(["a.html", "b.html", "c.html"], screen=screen)
    assert not runner.paged
    runner._on_knob(KnobEvent(0)); runner._on_knob(KnobEvent(0))
    assert runner._next_index() == 2
    runner._on_knob(KnobEvent(1))                 # turns are the firmware's business
    assert runner._next_index() is None


def test_library_selection_roundtrip(tmp_path) -> None:  # noqa: ANN001
    import pytest
    from neos75screen.live import library
    config = tmp_path / "pages.json"
    assert library.load(config) == library.DEFAULT_PAGES          # no file yet
    library.save(config, ["neos_system", "neos_clock"])
    assert library.load(config) == ["neos_system", "neos_clock"]
    assert [p.stem for p in library.resolve(library.load(config))] == ["neos_system", "neos_clock"]
    assert library.page_label(0) == "Page 7" and library.page_label(7) == "Page 14"
    with pytest.raises(ValueError, match="unknown widget"):
        library.save(config, ["nope"])
    with pytest.raises(ValueError, match="at most 8"):
        library.resolve(["neos_clock"] * 9)
    assert library.load(config) == ["neos_system", "neos_clock"]   # a refused save leaves the file alone


def test_runner_follows_the_selection_file(connect: Connect, tmp_path) -> None:  # noqa: ANN001
    import os
    from neos75screen.live import library, runner as runner_module
    config = tmp_path / "pages.json"
    library.save(config, ["neos_clock", "neos_system"])
    screen, _, _ = connect(profile="v2-knob", host_pages=True)
    runner = WidgetRunner(screen=screen, config=config, log=lambda *_: None)
    assert [p.stem for p in runner.pages] == ["neos_clock", "neos_system"]
    runner._config_at -= runner_module.CONFIG_CHECK_S
    assert runner._config_changed() is None                        # unchanged file
    config.write_text('{"pages": ["neos_player"', encoding="utf-8")  # half-written: ignored
    os.utime(config, ns=(1, 1))
    runner._config_at -= runner_module.CONFIG_CHECK_S
    assert runner._config_changed() is None
    library.save(config, ["neos_player", "neos_clock", "neos_system"])
    runner._config_at -= runner_module.CONFIG_CHECK_S
    changed = runner._config_changed()
    assert [p.stem for p in changed] == ["neos_player", "neos_clock", "neos_system"]
    runner.pages = changed
    runner._register()
    assert screen.status().pages == 3


class _RegionScreen:
    """Records what WidgetRunner._show sends; `canvas` False answers regions with FRAME_NO_CANVAS."""

    def __init__(self, region: bool = True) -> None:
        from types import SimpleNamespace
        self.capabilities = SimpleNamespace(region=region, host_pages=False, telemetry=False)
        self.sent: list = []
        self.canvas = True

    def show_region(self, jpeg, x, y):  # noqa: ANN001, ANN201
        from types import SimpleNamespace
        from neos75screen import FrameRejected
        if not self.canvas:
            raise FrameRejected(6)
        self.sent.append(("region", x, y))
        return SimpleNamespace(hidden=False, shown=True, status=1)

    def show_jpeg(self, jpeg):  # noqa: ANN001, ANN201
        from types import SimpleNamespace
        self.sent.append(("full",))
        self.canvas = True
        return SimpleNamespace(hidden=False, shown=True)


def test_runner_sends_regions_full_frames_or_nothing() -> None:
    from PIL import Image, ImageDraw
    screen = _RegionScreen()
    runner = WidgetRunner(["x.html"], screen=screen, log=lambda *_: None)
    base = Image.new("RGB", (480, 360), (5, 5, 5))
    meter = base.copy(); ImageDraw.Draw(meter).rectangle((150, 150, 300, 160), fill="white")
    assert runner._show(base, None) and screen.sent == [("full",)]          # nothing shown yet
    assert runner._show(base, base) and screen.sent == [("full",)]          # unchanged: nothing sent
    assert runner._show(meter, base) and screen.sent[-1] == ("region", 144, 144)
    screen.canvas = False                                                    # e.g. slot was hidden
    assert runner._show(base, meter) and screen.sent[-1] == ("full",)
    old = _RegionScreen(region=False)
    runner = WidgetRunner(["x.html"], screen=old, log=lambda *_: None)
    assert runner._show(meter, base) and old.sent == [("full",)]            # older firmware: full frames


def test_page_cache_warmup_then_one_stale_page_at_a_time() -> None:
    from types import SimpleNamespace
    from PIL import Image
    import io
    from neos75screen.live import runner as runner_module

    class Tab:
        def evaluate(self, *_): pass  # noqa: ANN001, ANN201
        def screenshot(self, type="png"):  # noqa: ANN001, ANN201, A002
            buf = io.BytesIO(); Image.new("RGB", (480, 360), (9, 9, 9)).save(buf, "PNG"); return buf.getvalue()

    class Cache:
        capabilities = SimpleNamespace(page_cache=True, host_pages=True, region=True, telemetry=False)
        cache_slot_bytes = 11360
        def __init__(self): self.ack_window = 1; self.uploads = []
        def cache_page(self, page, jpeg):  # noqa: ANN001, ANN201
            assert len(jpeg) <= self.cache_slot_bytes
            self.uploads.append(page); return SimpleNamespace(hidden=False)

    screen = Cache()
    r = WidgetRunner(["a.html", "b.html", "c.html", "d.html"], screen=screen, log=lambda *_: None)
    r.index = 2
    tabs = {w: Tab() for w in r.pages}
    r._refresh_cache(tabs, {}, 1.0)
    assert sorted(screen.uploads) == [0, 1, 2, 3] and screen.uploads[0] == 2      # warm-up, shown page first
    r._refresh_cache(tabs, {}, 1.0)
    assert len(screen.uploads) == 4                                               # all fresh, spacing
    r._cached[0] -= runner_module.CACHE_REFRESH_S; r._cached[1] -= runner_module.CACHE_REFRESH_S
    r._cache_at -= runner_module.CACHE_SPACING_S
    r._refresh_cache(tabs, {}, 1.0)
    assert screen.uploads[4:] == [1]                                              # one, nearest stale page
    r._register  # re-registering (new pages) warms all again



def test_sender_sends_the_newest_frame_and_survives_errors() -> None:
    import threading
    from types import SimpleNamespace
    from PIL import Image
    from neos75screen import FrameRejected
    from neos75screen.live.runner import _Sender

    shown = []
    gate = threading.Event()

    class Runner:
        log = staticmethod(lambda *_: None)
        _still_on = staticmethod(lambda page: True)
        def _show(self, frame, previous):  # noqa: ANN001, ANN201
            gate.wait(2)
            if frame.getpixel((0, 0)) == (1, 1, 1):
                raise FrameRejected(3)
            shown.append((frame.getpixel((0, 0))[0], previous is None))
            return True
        def _upload_cache(self, page, jpeg):  # noqa: ANN001, ANN201
            shown.append(("cache", page))

    sender = _Sender(Runner()); sender.start()
    frames = [Image.new("RGB", (4, 4), (v, v, v)) for v in (10, 20, 30)]
    sender.post(frames[0], True)
    import time; time.sleep(0.1)          # the sender is now busy with frame 10 (gate closed)
    sender.post(frames[1], False); sender.post(frames[2], False)   # 20 is replaced by 30
    sender.cache(3, b"x")
    gate.set(); time.sleep(0.3)
    assert shown == [(10, True), (30, False), ("cache", 3)]
    sender.post(Image.new("RGB", (4, 4), (1, 1, 1)), False); time.sleep(0.3)   # rejected: logged, goes on
    sender.post(frames[0], False); time.sleep(0.5)
    assert shown[-1] == (10, True)        # after a failure the next frame is sent in full
    sender.stop()


def test_sender_drops_frames_of_a_page_the_knob_left() -> None:
    import time
    from PIL import Image
    from neos75screen.live.runner import _Sender

    sent = []

    class Runner:
        log = staticmethod(lambda *_: None)
        late_frames = 0
        page = 1
        _sending_page = None
        def _still_on(self, page):  # noqa: ANN001, ANN201
            return page is None or page == self.page
        def _show(self, frame, previous):  # noqa: ANN001, ANN201
            sent.append(frame.getpixel((0, 0))[0])
            return True

    r = Runner(); sender = _Sender(r); sender.start()
    sender.post(Image.new("RGB", (4, 4), (10, 10, 10)), True, 1); time.sleep(0.2)
    r.page = 2                                                      # the knob moved to page 2
    sender.post(Image.new("RGB", (4, 4), (20, 20, 20)), False, 1); time.sleep(0.2)   # rendered before: dropped
    sender.post(Image.new("RGB", (4, 4), (30, 30, 30)), True, 2); time.sleep(0.2)
    sender.stop()
    assert sent == [10, 30] and sender.shown is not None



class _Tab:
    """A browser tab with a picture that only changes when told to; counts renders."""

    def __init__(self, audio: bool) -> None:
        import io
        from PIL import Image
        self.audio, self.shots, self.color = audio, 0, (10, 10, 10)
        self._io, self._image = io, Image

    def evaluate(self, function: str, arg: object = None) -> object:
        if "dataset.fps" in function:
            return 12
        if "'audio' in" in function:
            return self.audio
        return 0

    def screenshot(self) -> bytes:
        self.shots += 1
        buf = self._io.BytesIO()
        self._image.new("RGB", (480, 360), self.color).save(buf, "PNG")
        return buf.getvalue()

    def close(self) -> None:
        pass


def _run_with_stubs(monkeypatch, runner, tabs, seconds):  # noqa: ANN001, ANN202
    """Run the render loop for `seconds` with stub tabs and data sources; returns the spectrum starts."""
    import threading
    from contextlib import nullcontext

    from neos75screen.live import runner as R

    class Stub:
        starts = 0

        def __init__(self) -> None:
            type(self).starts += 1

        def start(self):  # noqa: ANN202
            return self

        def stop(self) -> None:
            pass

        def snapshot(self) -> dict:
            return {}

    class Spectrum(Stub):
        starts = 0

    class Browser:
        def new_page(self) -> _Tab:
            return tabs.pop(0)

        def close(self) -> None:
            pass

    monkeypatch.setattr(R, "SystemProvider", Stub)
    monkeypatch.setattr(R, "MediaProvider", Stub)
    monkeypatch.setattr(R, "SpectrumProvider", Spectrum)
    monkeypatch.setattr(R.cdp, "launch", lambda args: Browser())
    monkeypatch.setattr(R.contextlib, "closing", lambda b: nullcontext(b))
    monkeypatch.setattr(R.WidgetRunner, "_open", lambda self, page, widget: None)
    monkeypatch.setattr(R, "IDLE_AFTER_S", 0.3)
    t = threading.Thread(target=runner.run, kwargs={"seconds": seconds}, daemon=True)
    t.start()
    t.join(seconds + 5)
    return Spectrum.starts


def test_unchanged_page_is_rendered_less_often(connect: Connect, monkeypatch) -> None:  # noqa: ANN001
    screen, _, _ = connect(profile="v2-knob")
    runner = WidgetRunner(["a.html"], screen=screen, log=lambda *_: None)
    tab = _Tab(audio=False)
    _run_with_stubs(monkeypatch, runner, [tab], 2.0)
    # 12 fps for the first 0.3 s, then 4 fps: far fewer than 2 s x 12
    assert 5 <= tab.shots <= 14


def test_hidden_slot_is_not_rendered_and_audio_only_when_asked(connect: Connect, monkeypatch) -> None:  # noqa: ANN001
    from neos75screen.protocol import VisibilityEvent
    screen, _, _ = connect(profile="v2-knob")
    runner = WidgetRunner(["a.html"], screen=screen, log=lambda *_: None)
    runner._on_visibility(VisibilityEvent(False, 0))
    tab = _Tab(audio=True)
    assert _run_with_stubs(monkeypatch, runner, [tab], 1.0) == 0 and tab.shots == 0
    runner = WidgetRunner(["a.html"], screen=screen, log=lambda *_: None)
    tab = _Tab(audio=True)
    assert _run_with_stubs(monkeypatch, runner, [tab], 0.5) == 1 and tab.shots > 0
    runner = WidgetRunner(["a.html"], screen=screen, log=lambda *_: None)
    tab = _Tab(audio=False)
    assert _run_with_stubs(monkeypatch, runner, [tab], 0.5) == 0 and tab.shots > 0
