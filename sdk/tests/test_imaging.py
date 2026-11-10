from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from conftest import gradient, noise
from neos75screen import imaging


def _jpeg(img: Image.Image, **options: object) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "JPEG", **options)
    return buf.getvalue()


def _markers(data: bytes) -> list[int]:
    return [marker for marker, _, _ in imaging._segments(data)]


@pytest.mark.parametrize("fit", ["contain", "cover", "stretch"])
def test_fit_produces_exact_size(fit: imaging.Fit) -> None:
    out = imaging.fit_image(gradient((400, 200)), (320, 320), fit)
    assert out.size == (320, 320) and out.mode == "RGB"


def test_contain_letterboxes_and_cover_fills() -> None:
    white = Image.new("RGB", (400, 200), "white")
    contained = imaging.fit_image(white, (320, 320), "contain")
    assert contained.getpixel((160, 5)) == (0, 0, 0) and contained.getpixel((160, 160)) == (255, 255, 255)
    covered = imaging.fit_image(white, (320, 320), "cover")
    assert covered.getpixel((160, 5)) == (255, 255, 255)
    with pytest.raises(ValueError):
        imaging.fit_image(white, (320, 320), "zoom")  # type: ignore[arg-type]


def test_transparency_is_flattened_on_black() -> None:
    out = imaging.fit_image(Image.new("RGBA", (10, 10), (255, 0, 0, 0)), (10, 10), "stretch")
    assert out.getpixel((5, 5)) == (0, 0, 0)


def test_load_image_sources(tmp_path: Path) -> None:
    array = np.zeros((20, 30, 3), dtype=np.uint8)
    assert imaging.load_image(array).size == (30, 20)
    path = tmp_path / "x.png"
    gradient((16, 8)).save(path)
    assert imaging.load_image(path).size == (16, 8)
    assert imaging.load_image(str(path)).size == (16, 8)
    with pytest.raises(TypeError):
        imaging.load_image(42)


def test_prepare_image_rounds_to_even() -> None:
    assert imaging.prepare_image(gradient((50, 50)), (321, 319)).size == (320, 318)


@pytest.mark.parametrize("subsampling", ["4:4:4", "4:2:2", "4:2:0"])
def test_encode_jpeg_is_baseline_with_requested_subsampling(subsampling: imaging.Subsampling) -> None:
    data = imaging.encode_jpeg(gradient(), 80, subsampling)
    info = imaging.inspect_jpeg(data)
    assert (info.width, info.height, info.subsampling) == (320, 320, subsampling)
    assert 0xE0 in _markers(data)


def test_encode_jpeg_rejects_odd_size() -> None:
    with pytest.raises(ValueError):
        imaging.encode_jpeg(gradient((321, 320)))


def test_quality_search_fits_max_bytes() -> None:
    img = noise()
    full = imaging.encode_jpeg(img, 90)
    limited = imaging.encode_jpeg(img, 90, max_bytes=len(full) // 2)
    assert len(limited) <= len(full) // 2
    assert len(imaging.encode_jpeg(img, 90, max_bytes=len(limited))) == len(limited)
    with pytest.raises(ValueError):
        imaging.encode_jpeg(img, 90, max_bytes=500)


def test_strip_app_segments_keeps_app0_only() -> None:
    exif = Image.Exif()
    exif[0x010F] = "camera"
    data = _jpeg(gradient((32, 32)), exif=exif.tobytes(), icc_profile=b"\x00" * 64)
    assert 0xE1 in _markers(data) and 0xE2 in _markers(data)
    stripped = imaging.strip_app_segments(data)
    assert [m for m in _markers(stripped) if 0xE0 <= m <= 0xEF] == [0xE0]
    assert Image.open(io.BytesIO(stripped)).size == (32, 32)


def test_inspect_rejects_unsupported_jpegs() -> None:
    with pytest.raises(ValueError, match="baseline"):
        imaging.inspect_jpeg(_jpeg(gradient((32, 32)), progressive=True))
    with pytest.raises(ValueError, match="components"):
        imaging.inspect_jpeg(_jpeg(gradient((32, 32)).convert("L")))
    with pytest.raises(ValueError):
        imaging.inspect_jpeg(b"not a jpeg")
    with pytest.raises(ValueError):
        imaging.inspect_jpeg(b"\xff\xd8\xff\xd9")


def test_rgb565_known_values_and_paths_agree() -> None:
    img = Image.frombytes("RGB", (3, 1), bytes([255, 0, 0, 0, 255, 0, 0, 0, 255]))
    assert imaging.to_rgb565(img) == bytes([0x00, 0xF8, 0xE0, 0x07, 0x1F, 0x00])
    big = noise((64, 32))
    assert imaging.to_rgb565(big) == imaging._rgb565_pure(big.tobytes())
