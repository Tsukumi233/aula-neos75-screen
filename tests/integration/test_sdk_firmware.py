"""End-to-end check: the real SDK code talks to the real patched firmware machine code (unicorn emulator).

Every report the SDK writes goes into the patch's HID entry points; the GUI hook then runs and whatever the
firmware sends back is handed to the SDK. Peripherals (JPEG hardware, LVGL) are the emulator's stubs, so this
proves the wire protocol and state machine agree, not the hardware behaviour.

Run from the repository root with the firmware venv (which has unicorn) after `pip install -e sdk`:
    firmware\\.venv\\Scripts\\python -m pytest tests/integration
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "firmware" / "tests"))

from emulator import Machine  # noqa: E402  (firmware test harness)
from neos75screen import Screen  # noqa: E402
from neos75screen.transport import MockTransport  # noqa: E402

GUI_PASSES_PER_REPORT = 6


def connect(variant: str = "dev") -> tuple[Screen, Machine]:
    machine = Machine(variant)

    def responder(report: bytes):
        before = len(machine.sent)
        machine.hid(report)
        for _ in range(GUI_PASSES_PER_REPORT):
            machine.gui()
        return machine.sent[before:]

    # No keepalive thread: it would run the (not thread-safe) emulator while a test pokes it directly.
    return Screen(MockTransport(responder), keepalive=False), machine


def gradient(w: int, h: int) -> Image.Image:
    img = Image.new("RGB", (w, h))
    img.putdata([((x * 255) // max(w - 1, 1), (y * 255) // max(h - 1, 1), (x ^ y) & 255)
                 for y in range(h) for x in range(w)])
    return img


@pytest.mark.parametrize("variant", ["dev", "release"])
def test_hello_matches_firmware(variant: str) -> None:
    screen, _ = connect(variant)
    hello = screen.hello()
    assert hello.realtime
    assert (hello.canvas_w, hello.canvas_h) == (480, 360)
    assert (hello.image_w, hello.image_h) == (480, 360)  # v2: realtime page covers the whole panel
    assert hello.patch_version >= 2
    assert hello.max_frame_bytes == 172804
    assert hello.buffers >= 1


def test_enter_status_leave() -> None:
    screen, machine = connect()
    screen.hello()
    screen.enter()
    assert machine.state().active
    status = screen.status()
    assert status.frames_ok == 0
    screen.leave()
    assert not machine.state().active


@pytest.mark.parametrize("size", [(2, 2), (16, 16), (64, 64), (100, 100)])
def test_rgb565_frames_cross_block_boundaries(size: tuple[int, int]) -> None:
    screen, machine = connect()
    screen.hello()
    w, h = size
    raw = bytes((i * 37) & 255 for i in range(w * h * 2))
    result = screen.show_rgb565(raw, width=w, height=h)
    assert result.status == 1, result
    st = machine.state()
    assert (st.width, st.height) == size
    assert st.ok == 1 and st.errors == 0


def test_full_canvas_jpeg_from_pillow() -> None:
    screen, machine = connect()
    screen.hello()
    result = screen.show(gradient(480, 360), quality=90)
    assert result.status == 1, result
    st = machine.state()
    assert (st.width, st.height) == (480, 360)
    assert st.jpeg_len > 4096  # several BA blocks with CRC and ACK


@pytest.mark.parametrize("size", [(320, 320), (2, 2), (480, 2), (2, 360), (160, 120)])
def test_smaller_jpeg_frames_still_accepted(size: tuple[int, int]) -> None:
    buf = io.BytesIO()
    gradient(*size).save(buf, "JPEG", quality=85)
    screen, machine = connect()
    screen.hello()
    assert screen.show_jpeg(buf.getvalue()).status == 1
    st = machine.state()
    assert (st.width, st.height) == size


@pytest.mark.parametrize("size", [(482, 360), (480, 362)])
def test_frames_larger_than_canvas_rejected(size: tuple[int, int]) -> None:
    from neos75screen import protocol as p

    buf = io.BytesIO()
    gradient(*size).save(buf, "JPEG", quality=50)
    screen, machine = connect()
    screen.hello()
    screen.enter()
    before = machine.state().errors
    # Bypass the SDK's own size check on purpose: the firmware must reject it by itself.
    try:
        result = screen._show_stream(0x5A, p.build_jpeg_stream(buf.getvalue(), *size))
        assert result.status != 1, result
    except Exception:
        pass
    assert machine.state().errors == before + 1
    assert machine.state().ok == 0


@pytest.mark.parametrize("subsampling", ["4:4:4", "4:2:2", "4:2:0"])
def test_jpeg_subsampling_modes(subsampling: str) -> None:
    screen, _ = connect()
    screen.hello()
    result = screen.show(gradient(480, 360), subsampling=subsampling)
    assert result.status == 1, result


def test_square_image_is_letterboxed_into_canvas() -> None:
    screen, machine = connect()
    screen.hello()
    assert screen.show(gradient(320, 320), fit="contain").status == 1
    assert (machine.state().width, machine.state().height) == (480, 360)


def test_stream_of_frames() -> None:
    screen, machine = connect()
    screen.hello()
    frames = [gradient(480, 360).rotate(angle) for angle in (0, 180)] + [gradient(480, 360)]
    stats = screen.stream(frames)
    assert machine.state().ok == len(frames)
    assert stats is not None


def test_decode_failure_is_reported_and_next_frame_works() -> None:
    screen, machine = connect()
    screen.hello()
    machine.decode_success = False
    with pytest.raises(Exception):
        result = screen.show(gradient(64, 64))
        if result.status != 1:
            raise RuntimeError(result)
    machine.decode_success = True
    assert screen.show(gradient(64, 64)).status == 1


def test_jpeg_bytes_round_trip_size() -> None:
    buf = io.BytesIO()
    gradient(320, 320).save(buf, "JPEG", quality=80)
    screen, machine = connect()
    screen.hello()
    assert screen.show_jpeg(buf.getvalue()).status == 1
    assert machine.state().jpeg_len > 0


@pytest.mark.parametrize("variant", ["dev", "release"])
def test_keyboard_status_from_firmware_ram(variant: str) -> None:
    screen, machine = connect(variant)
    raw = bytearray(26)
    raw[13], raw[14], raw[16] = 2, 73, 4
    machine.uc.mem_write(0x20004660, bytes(raw))
    assert screen.capabilities.telemetry
    status = screen.keyboard()
    assert (status.raw, status.battery, status.charging, status.connection) == (bytes(raw), 73, True, "2.4g")


def test_region_frames_from_the_sdk() -> None:
    from neos75screen import FrameRejected
    from neos75screen.imaging import encode_jpeg
    screen, machine = connect("dev")
    assert screen.capabilities.region
    patch = encode_jpeg(Image.new("RGB", (64, 32), "red"), 85)
    with pytest.raises(FrameRejected) as rejected:          # no full frame shown yet
        screen.show_region(patch, 96, 48)
    assert rejected.value.status == 6
    assert screen.show(Image.new("RGB", (480, 360), "black")).shown
    result = screen.show_region(patch, 96, 48)
    assert result.shown
    assert machine.invalidated[-1][1] == (96, 48, 159, 79)
    with pytest.raises(ValueError, match="multiples of 16"):
        screen.show_region(encode_jpeg(Image.new("RGB", (40, 32)), 85), 0, 0)
    with pytest.raises(ValueError):
        screen.show_region(patch, 440, 0)                    # past the right edge
