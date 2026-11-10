from __future__ import annotations

from datetime import datetime
from itertools import islice
from pathlib import Path

import pytest
from PIL import Image

from conftest import gradient
from fake_device import FakeScreen
from neos75screen import MockTransport, apps
from neos75screen import screen as screen_module
from neos75screen.cli import main
from neos75screen.protocol import KnobEvent, VisibilityEvent


SIZES = [(480, 360), (320, 320), (320, 240)]


@pytest.mark.parametrize("size", SIZES)
def test_render_clock(size: tuple[int, int]) -> None:
    for seconds in (True, False):
        img = apps.render_clock(datetime(2026, 11, 6, 23, 59, 30), size, seconds)
        assert img.size == size and img.mode == "RGB"


def test_renderers_default_to_the_panel_size() -> None:
    assert apps.PREVIEW_SIZE == (480, 360)
    assert apps.render_clock(datetime(2026, 1, 1)).size == (480, 360)
    assert apps.render_dashboard(apps.Sample(1.0, 1, 2, 0, 0, None, ())).size == (480, 360)


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("gpu", [None, 77.0])
@pytest.mark.parametrize("cores", [0, 8, 128])
def test_render_dashboard(size: tuple[int, int], gpu: float | None, cores: int) -> None:
    sample = apps.Sample(42.0, 6 * 2**30, 16 * 2**30, 1.5e6, 2e4, gpu, tuple(range(0, 100, 5)),
                         tuple(float(i % 100) for i in range(cores)), 61.0, 3 * 86400 + 4000)
    img = apps.render_dashboard(sample, size)
    assert img.size == size and img.mode == "RGB"


def test_sampler_reads_the_system() -> None:
    sample = apps.Sampler().sample()
    assert 0 <= sample.cpu <= 100 and sample.ram_total > 0 and sample.cpu_history == (sample.cpu,)
    assert len(sample.per_cpu) >= 1 and sample.uptime_s is not None and sample.uptime_s > 0


def test_slideshow(tmp_path: Path) -> None:
    gradient((40, 30)).save(tmp_path / "b.png")
    gradient((20, 10)).save(tmp_path / "a.jpg")
    (tmp_path / "broken.png").write_bytes(b"not an image")
    (tmp_path / "notes.txt").write_text("skip me")
    assert [p.name for p in apps.list_images(tmp_path)] == ["a.jpg", "b.png", "broken.png"]
    sizes = [img.size for img in apps.slideshow_frames(tmp_path, interval=0.0, loop=False)]
    assert sizes == [(20, 10), (40, 30)]
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(FileNotFoundError):
        next(apps.slideshow_frames(empty))


def _gif(path: Path, count: int, duration: int) -> None:
    frames = [Image.new("RGB", (16, 16), (i * 40, 0, 0)) for i in range(count)]
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=duration, loop=0)


def test_gif_frames_keep_order_and_encode(tmp_path: Path) -> None:
    path = tmp_path / "anim.gif"
    _gif(path, 3, 50)
    frames = list(apps.gif_frames(path, loops=2))
    assert len(frames) == 6 and all(isinstance(f, Image.Image) for f in frames)
    encoded = list(islice(apps.gif_frames(path, loops=1, encode=lambda img: b"jpeg"), 3))
    assert encoded == [b"jpeg"] * 3


def test_cli_hello_info_and_show(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                                 capsys: pytest.CaptureFixture[str]) -> None:
    fake = FakeScreen()

    def fresh_transport() -> MockTransport:
        transport = MockTransport(fake)
        fake.attach(transport)
        return transport

    monkeypatch.setattr(screen_module, "open_transport", fresh_transport)
    assert main(["hello"]) == 0
    out = capsys.readouterr().out
    assert "protocol   v1, patch 2" in out and "image      480x360" in out
    assert main(["info"]) == 0
    assert "ZG06_2.31_V2.0" in capsys.readouterr().out
    path = tmp_path / "x.png"
    gradient().save(path)
    assert main(["show", str(path), "--stay"]) == 0
    assert len(fake.shown) == 1 and fake.realtime

    monkeypatch.setattr(screen_module, "open_transport", lambda: MockTransport(FakeScreen(patched=False)))
    assert main(["clock"]) == 1


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("cores", [0, 4, 32, 128])
def test_render_cores_view(size: tuple[int, int], cores: int) -> None:
    sample = apps.Sample(42.0, 1, 2, 0, 0, None, (), tuple(float(i % 100) for i in range(cores)))
    assert apps.render_dashboard(sample, size, "cores").size == size
    with pytest.raises(ValueError):
        apps.render_dashboard(sample, size, "graphs")


def test_view_cycle_follows_the_knob() -> None:
    views = apps.ViewCycle(apps.SYSMON_VIEWS)
    assert views.current == "overview"
    views.on_event(VisibilityEvent(True, 2))   # host ENTER: no change
    views.on_event(KnobEvent(1))               # turning moves pages, not views
    assert views.current == "overview"
    views.on_event(VisibilityEvent(True, 1))   # back onto the slot with the knob
    assert views.current == "cores"
    views.on_event(KnobEvent(0))               # a press, if the firmware reports it
    assert views.current == "overview"
    with pytest.raises(ValueError):
        apps.ViewCycle([])
