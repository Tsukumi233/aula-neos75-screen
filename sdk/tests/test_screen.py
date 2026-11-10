"""End-to-end tests of Screen against the fake device (stock and patched behaviour)."""
from __future__ import annotations

import io
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from conftest import Connect, detail, gradient, noise
from fake_device import BUILD, GIF_SPACE, IMAGE_SPACE, MODEL
from neos75screen import (FlashWriteWarning, FrameRejected, ProtocolError, StockFirmware, Timeout)
from neos75screen import constants as c
from neos75screen.imaging import encode_jpeg


def _close(a: Image.Image, b: Image.Image, tolerance: float = 12.0) -> bool:
    diff = np.abs(np.asarray(a, dtype=np.int16) - np.asarray(b.convert("RGB"), dtype=np.int16))
    return float(diff.mean()) < tolerance


# --- stock firmware ---------------------------------------------------------------------------------

def test_stock_firmware_is_detected(connect: Connect) -> None:
    start = time.monotonic()
    screen, _, _ = connect(patched=False)
    assert not screen.realtime
    assert screen.capabilities.image_size == (320, 320)
    assert time.monotonic() - start < 1.0
    for action in (screen.enter, screen.status, lambda: screen.show(gradient())):
        with pytest.raises(StockFirmware):
            action()


def test_stock_helpers(connect: Connect) -> None:
    screen, fake, _ = connect(patched=False)
    info = screen.info()
    assert (info.vid, info.pid, info.width, info.height) == (0x5542, 0x0001, 320, 320)
    assert info.supports_jpeg and info.image_space == IMAGE_SPACE and info.gif_space == GIF_SPACE
    assert screen.version().model == MODEL and screen.version().build == BUILD
    screen.next_page()
    screen.next_page()
    screen.prev_page()
    screen.brightness_up()
    screen.brightness_down()
    screen.brightness_down()
    screen.sync_time(datetime(2026, 11, 6, 12, 30, 45))
    assert (fake.page, fake.brightness) == (1, 2)
    assert fake.clock == bytes([1, 0x07, 0xEA, 11, 6, 12, 30, 45, 5])


def test_stock_wallpaper_upload(connect: Connect) -> None:
    screen, fake, _ = connect(patched=False)
    with pytest.warns(FlashWriteWarning):
        screen.upload_wallpaper(noise(), persistent=True, quality=95)
    assert fake.wallpaper is not None and fake.wallpaper[:2] == b"\x55\x42"
    assert fake.blocks >= 2  # the block CRC / ACK path ran
    img = Image.open(io.BytesIO(fake.wallpaper[13:]))
    assert img.size == (320, 320)


# --- patched firmware -------------------------------------------------------------------------------

def test_hello_reports_capabilities(connect: Connect) -> None:
    screen, _, _ = connect(buffers=2, formats=0x01, image=(240, 240), max_frame_bytes=50000)
    hello = screen.capabilities
    assert hello.realtime and hello.proto_version == 1 and hello.patch_version == 2
    assert (hello.canvas_w, hello.canvas_h, hello.image_w, hello.image_h) == (480, 360, 240, 240)
    assert hello.supports_jpeg and not hello.supports_rgb565
    assert (hello.max_frame_bytes, hello.buffers) == (50000, 2)


def test_newer_protocol_disables_realtime(connect: Connect) -> None:
    with pytest.warns(UserWarning, match="v3"):
        screen, _, _ = connect(proto_version=3)
    assert not screen.realtime


def test_show_enters_and_close_leaves(connect: Connect) -> None:
    screen, fake, _ = connect()
    src = gradient((480, 360))
    result = screen.show(src)
    assert result.shown and result.seq == 1 and result.decode_ms == 3
    assert fake.realtime and len(fake.shown) == 1 and fake.shown[0].size == (480, 360)
    assert _close(src, fake.shown[0])
    screen.close()
    assert not fake.realtime


def test_close_without_leave_keeps_realtime(connect: Connect) -> None:
    screen, fake, _ = connect()
    screen.show(gradient())
    screen.close(leave=False)
    assert fake.realtime
    screen.close()


def test_show_sources(connect: Connect, tmp_path: Path) -> None:
    screen, fake, _ = connect()
    path = tmp_path / "pic.png"
    gradient((640, 480)).save(path)
    screen.show(path, fit="cover")
    screen.show(str(path), fit="stretch")
    screen.show(np.zeros((100, 50, 3), dtype=np.uint8))
    assert [img.size for img in fake.shown] == [(480, 360)] * 3


def test_show_large_jpeg_uses_block_acks(connect: Connect) -> None:
    screen, fake, _ = connect()
    jpeg = encode_jpeg(noise(), 60)
    assert len(jpeg) > 3 * 4096
    screen.show_jpeg(jpeg)
    assert fake.blocks == (len(jpeg) + 13) // 4096 and len(fake.shown) == 1


def test_show_respects_max_bytes(connect: Connect) -> None:
    screen, fake, transport = connect(profile="v1", max_frame_bytes=15000)
    screen.show(detail(), quality=95)
    screen.show(detail(), quality=95, max_bytes=11000)
    lengths = [int.from_bytes(r[4:8], "big") for r in transport.written if r[:3] == b"\xba\x06\xaa"]
    assert 11000 < lengths[0] <= 15000 and lengths[1] <= 11000 + 13
    assert len(fake.shown) == 2


def test_show_jpeg_validates_input(connect: Connect) -> None:
    screen, fake, transport = connect(image=(160, 160))
    before = len(transport.written)
    buf = io.BytesIO()
    gradient((64, 64)).save(buf, "JPEG", progressive=True)
    with pytest.raises(ValueError, match="baseline"):
        screen.show_jpeg(buf.getvalue())
    with pytest.raises(ValueError, match="maximum"):
        screen.show_jpeg(encode_jpeg(gradient((200, 100))))
    assert len(transport.written) == before and fake.shown == []


def test_show_jpeg_strips_app_segments(connect: Connect) -> None:
    screen, fake, transport = connect()
    buf = io.BytesIO()
    exif = Image.Exif()
    exif[0x010F] = "camera"
    gradient((64, 64)).save(buf, "JPEG", exif=exif.tobytes())
    screen.show_jpeg(buf.getvalue())
    sent = b"".join(r[2:2 + r[1]] for r in transport.written if r[0] == 0xBA and r[1] <= 62 and r[:3] != b"\xba\x06\xaa")
    assert b"Exif" not in sent and len(fake.shown) == 1


def test_show_rgb565(connect: Connect) -> None:
    screen, fake, _ = connect(profile="v1", max_frame_bytes=210000)
    src = gradient()
    assert screen.show_rgb565(src).shown
    assert _close(src, fake.shown[0], tolerance=4)
    pixels = bytes([0x00, 0xF8]) * (8 * 4)  # pure red
    screen.show_rgb565(pixels, 8, 4)
    assert fake.shown[1].size == (8, 4) and fake.shown[1].getpixel((0, 0)) == (248, 0, 0)
    with pytest.raises(ValueError):
        screen.show_rgb565(pixels)


def test_rgb565_needs_format_and_room(connect: Connect) -> None:
    screen, _, _ = connect(formats=0x01)
    with pytest.raises(ProtocolError):
        screen.show_rgb565(gradient())
    screen, _, _ = connect(max_frame_bytes=100000)
    with pytest.raises(ValueError, match="max_frame_bytes"):
        screen.show_rgb565(gradient())


@pytest.mark.parametrize("status", [c.FRAME_DECODE_ERROR, c.FRAME_TOO_LARGE, c.FRAME_DROPPED_BUSY])
def test_rejected_frames_raise(connect: Connect, status: int) -> None:
    screen, _, _ = connect(force_status=status)
    with pytest.raises(FrameRejected) as info:
        screen.show(gradient())
    assert info.value.status == status


def test_not_in_realtime_mode_status(connect: Connect) -> None:
    screen, fake, _ = connect()
    screen.enter()
    fake.realtime = False  # e.g. the device left realtime mode on its own
    with pytest.raises(FrameRejected) as info:
        screen.show(gradient())
    assert info.value.status == c.FRAME_NOT_REALTIME


def test_missing_event_falls_back_to_status(connect: Connect) -> None:
    screen, fake, _ = connect(send_events=False)
    result = screen.show(gradient())
    assert result.timed_out and not result.shown
    assert result.device_status is not None and result.device_status.frames_ok == 1
    assert len(fake.shown) == 1


def test_status_counters(connect: Connect) -> None:
    screen, _, _ = connect()
    screen.show(gradient())
    status = screen.status()
    assert (status.frames_ok, status.frames_err, status.state) == (1, 0, 1)


def test_nack_raises(connect: Connect) -> None:
    screen, _, _ = connect(nack_block=1)
    with pytest.raises(ProtocolError, match="rejected"):
        screen.show_jpeg(encode_jpeg(noise(), 60))


def test_unknown_request_raises(connect: Connect) -> None:
    screen, _, _ = connect(unknown_subs=frozenset({c.RT_STATUS}))
    with pytest.raises(ProtocolError, match="understand"):
        screen.status()


def test_reply_timeout(connect: Connect) -> None:
    screen, fake, _ = connect()
    fake.patched = False  # stops answering realtime requests
    with pytest.raises(Timeout):
        screen.enter()


# --- image size: v2 (480 x 360 panel) and v1 (320 x 320 square) profiles ------------------------------

def _black(pixel: tuple[int, ...]) -> bool:
    return max(pixel[:3]) < 16


def _white(pixel: tuple[int, ...]) -> bool:
    return min(pixel[:3]) > 239


def test_v2_hello_reports_the_panel(connect: Connect) -> None:
    screen, _, _ = connect()
    hello = screen.capabilities
    assert (hello.image_w, hello.image_h, hello.canvas_w, hello.canvas_h) == (480, 360, 480, 360)
    assert hello.patch_version == 2 and hello.max_frame_bytes == 172804


def test_v2_contain_letterboxes_a_square_image(connect: Connect) -> None:
    screen, fake, _ = connect()
    screen.show(Image.new("RGB", (500, 500), "white"), fit="contain")
    assert fake.shown[0].size == (480, 360)
    assert _black(fake.panel.getpixel((20, 180))) and _black(fake.panel.getpixel((460, 180)))
    assert _white(fake.panel.getpixel((240, 180))) and _white(fake.panel.getpixel((70, 10)))
    screen.show(Image.new("RGB", (500, 500), "white"), fit="cover")
    assert _white(fake.panel.getpixel((20, 180)))


def test_v2_centres_smaller_frames(connect: Connect) -> None:
    screen, fake, _ = connect()
    screen.show(Image.new("RGB", (480, 360), "white"))
    screen.show_jpeg(encode_jpeg(Image.new("RGB", (320, 240), "white")))
    panel = fake.panel
    assert _white(panel.getpixel((85, 65))) and _white(panel.getpixel((394, 294)))
    assert _black(panel.getpixel((75, 55))) and _black(panel.getpixel((405, 305)))


def test_v2_oversize_frames(connect: Connect) -> None:
    screen, fake, transport = connect()
    before = len(transport.written)
    with pytest.raises(ValueError, match="maximum"):
        screen.show_jpeg(encode_jpeg(gradient((640, 480))))
    assert len(transport.written) == before
    # A device that advertises more than it accepts rejects the frame with status 03.
    screen, fake, _ = connect(hello_image=(640, 480))
    with pytest.raises(FrameRejected) as info:
        screen.show_jpeg(encode_jpeg(gradient((640, 480))))
    assert info.value.status == c.FRAME_TOO_LARGE and fake.shown == []


def test_v2_rgb565_limits(connect: Connect) -> None:
    screen, fake, _ = connect()
    with pytest.raises(ValueError, match="max_frame_bytes"):
        screen.show_rgb565(gradient((480, 360)))  # 345609 bytes > 172804
    screen.show_rgb565(bytes([0x1F, 0x00]) * (320 * 240), 320, 240)  # 153609 bytes fits
    assert fake.shown[0].size == (320, 240) and fake.panel.getpixel((240, 180)) == (0, 0, 248)


def test_v1_profile_square_on_the_right(connect: Connect) -> None:
    screen, fake, _ = connect(profile="v1")
    hello = screen.capabilities
    assert (hello.image_w, hello.image_h, hello.patch_version) == (320, 320, 1)
    screen.show(Image.new("RGB", (480, 360), "white"), fit="contain")
    assert fake.shown[0].size == (320, 320)
    assert _black(fake.panel.getpixel((80, 180)))      # left of the square: not used by v1
    assert _black(fake.panel.getpixel((320, 30)))      # letterbox bar inside the square
    assert _white(fake.panel.getpixel((320, 180)))


# --- streaming --------------------------------------------------------------------------------------

def test_stream_single_buffer_waits_for_each_frame(connect: Connect) -> None:
    screen, fake, _ = connect(decode_delay=0.02)
    frames = [gradient(), noise(), gradient()]
    stats = screen.stream(frames)
    assert (stats.sent, stats.shown, stats.dropped, stats.timeouts) == (3, 3, 0, 0)
    assert fake.max_pending == 1 and fake.statuses == [1, 1, 1]


def test_stream_pipelines_with_two_buffers(connect: Connect) -> None:
    screen, fake, _ = connect(buffers=2, decode_delay=0.05)
    stats = screen.stream([gradient()] * 6)
    assert stats.shown == 6 and stats.dropped == 0
    assert fake.max_pending == 2


def test_stream_counts_busy_drops(connect: Connect) -> None:
    # The device claims two buffers but has one, so pipelined frames get "dropped (busy)".
    screen, fake, _ = connect(buffers=1, hello_buffers=2, decode_delay=0.1)
    stats = screen.stream([gradient()] * 4)
    assert stats.dropped >= 1 and stats.shown + stats.dropped == 4
    assert c.FRAME_DROPPED_BUSY in fake.statuses


def test_stream_accepts_jpeg_bytes_and_limits_fps(connect: Connect) -> None:
    screen, fake, _ = connect()
    jpeg = encode_jpeg(gradient())
    start = time.monotonic()
    stats = screen.stream([jpeg] * 4, fps=20)
    assert stats.shown == 4 and time.monotonic() - start >= 0.14
    assert len(fake.shown) == 4


def test_stream_timeouts_are_counted(connect: Connect) -> None:
    screen, _, _ = connect(send_events=False)
    stats = screen.stream([gradient()] * 2)
    assert stats.timeouts == 2 and stats.shown == 0


def test_stream_raises_on_decode_error(connect: Connect) -> None:
    screen, _, _ = connect(force_status=c.FRAME_DECODE_ERROR)
    with pytest.raises(FrameRejected):
        screen.stream([gradient()])


def test_wallpaper_on_patched_firmware_leaves_realtime(connect: Connect) -> None:
    screen, fake, _ = connect()
    screen.show(gradient())
    with pytest.warns(FlashWriteWarning):
        screen.upload_wallpaper(gradient(), persistent=True)
    assert not fake.realtime and fake.wallpaper is not None


@pytest.mark.parametrize("window", [1, 2, 8])
def test_ack_window_sends_the_same_frames(connect: Connect, window: int) -> None:
    screen, fake, _ = connect()
    screen.ack_window = window
    for size in ((480, 360), (64, 64)):
        assert screen.show(noise(size)).shown
    assert len(fake.shown) == 2 and screen.status().frames_err == 0
