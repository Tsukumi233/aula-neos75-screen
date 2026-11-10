"""Image preparation: load, fit to the screen, encode baseline JPEG or RGB565 (Pillow, numpy optional)."""
from __future__ import annotations

import io
import os
import struct
from collections.abc import Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Union

from PIL import Image

if TYPE_CHECKING:
    import numpy as np

Fit = Literal["contain", "cover", "stretch"]
Subsampling = Literal["4:4:4", "4:2:2", "4:2:0"]
# A PIL image, a numpy uint8 array (H x W, H x W x 3 or H x W x 4), or a file path.
ImageSource = Union[Image.Image, "np.ndarray", str, "os.PathLike[str]"]

MIN_QUALITY = 10
_SAMPLING_NAMES: dict[tuple[int, int], Subsampling] = {(1, 1): "4:4:4", (2, 1): "4:2:2", (2, 2): "4:2:0"}
_SOF_MARKERS = frozenset(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}
_STANDALONE_MARKERS = frozenset({0x01, *range(0xD0, 0xD8)})
_APP0, _APP15, _SOS, _EOI = 0xE0, 0xEF, 0xDA, 0xD9


def load_image(source: ImageSource) -> Image.Image:
    """Return a PIL image from an image, a numpy array or a path."""
    if isinstance(source, Image.Image):
        return source
    if isinstance(source, (str, os.PathLike)):
        with Image.open(source) as img:
            return img.copy()
    if hasattr(source, "__array_interface__"):
        return Image.fromarray(source)
    raise TypeError(f"unsupported image source: {type(source).__name__}")


def even_size(width: int, height: int) -> tuple[int, int]:
    """Round both dimensions down to even numbers (the JPEG decoder requires even sizes)."""
    return width - width % 2, height - height % 2


def fit_image(img: Image.Image, size: tuple[int, int], fit: Fit = "contain",
              background: tuple[int, int, int] = (0, 0, 0)) -> Image.Image:
    """Resize to exactly `size` (RGB). contain: letterbox; cover: centre crop; stretch: ignore aspect."""
    img = _to_rgb(img, background)
    width, height = size
    if fit not in ("contain", "cover", "stretch"):
        raise ValueError(f"fit must be contain, cover or stretch, not {fit!r}")
    if fit == "stretch":
        return img.resize(size, Image.Resampling.LANCZOS)
    ratios = (width / img.width, height / img.height)
    scale = min(ratios) if fit == "contain" else max(ratios)
    scaled = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))),
                        Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", size, background)
    canvas.paste(scaled, ((width - scaled.width) // 2, (height - scaled.height) // 2))
    return canvas


def _to_rgb(img: Image.Image, background: tuple[int, int, int]) -> Image.Image:
    if img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        flat = Image.new("RGB", rgba.size, background)
        flat.paste(rgba, mask=rgba.getchannel("A"))
        return flat
    return img.convert("RGB")


def prepare_image(source: ImageSource, size: tuple[int, int], fit: Fit = "contain") -> Image.Image:
    """Load and fit an image to an even-sized frame."""
    return fit_image(load_image(source), even_size(*size), fit)


# --- JPEG -------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class JpegInfo:
    width: int
    height: int
    subsampling: Subsampling


def _segments(data: bytes) -> Iterator[tuple[int, int, int]]:
    """Yield (marker, start, end) for each marker up to SOS; the SOS entry extends to the end of data."""
    if data[:2] != b"\xff\xd8":
        raise ValueError("not a JPEG (no SOI)")
    yield 0xD8, 0, 2
    pos = 2
    while pos + 1 < len(data):
        if data[pos] != 0xFF:
            raise ValueError(f"bad JPEG marker at offset {pos}")
        marker = data[pos + 1]
        if marker == 0xFF:  # fill byte
            pos += 1
            continue
        if marker in _STANDALONE_MARKERS:
            yield marker, pos, pos + 2
            pos += 2
            continue
        if marker == _EOI:
            break
        if pos + 4 > len(data):
            break
        end = pos + 2 + struct.unpack_from(">H", data, pos + 2)[0]
        if marker == _SOS:
            yield marker, pos, len(data)
            return
        if end > len(data):
            raise ValueError("truncated JPEG segment")
        yield marker, pos, end
        pos = end
    raise ValueError("JPEG has no scan (SOS)")


def inspect_jpeg(data: bytes) -> JpegInfo:
    """Check the hardware decoder's constraints: baseline, 8-bit, 3-component 4:4:4 / 4:2:2 / 4:2:0."""
    info: JpegInfo | None = None
    for marker, start, _ in _segments(data):
        if marker not in _SOF_MARKERS:
            continue
        if marker != 0xC0:
            raise ValueError(f"not a baseline JPEG (SOF{marker - 0xC0})")
        precision, height, width, count = struct.unpack_from(">BHHB", data, start + 4)
        if precision != 8:
            raise ValueError(f"{precision}-bit JPEG; only 8-bit is supported")
        if count != 3:
            raise ValueError(f"{count} components; only YCbCr (3) is supported")
        factors = [data[start + 11 + 3 * i] for i in range(3)]
        luma = (factors[0] >> 4, factors[0] & 0x0F)
        if factors[1] != 0x11 or factors[2] != 0x11 or luma not in _SAMPLING_NAMES:
            raise ValueError("unsupported chroma subsampling")
        info = JpegInfo(width, height, _SAMPLING_NAMES[luma])
    if info is None:
        raise ValueError("no baseline SOF0 segment")
    return info


def strip_app_segments(data: bytes) -> bytes:
    """Remove APP1..APP15 segments (EXIF, ICC, ...); keep APP0 and everything else."""
    return b"".join(data[start:end] for marker, start, end in _segments(data)
                    if not _APP0 < marker <= _APP15)


def _encode(img: Image.Image, quality: int, subsampling: Subsampling, optimize: bool = False) -> bytes:
    buf = io.BytesIO()
    # optimize: Huffman tables fitted to the image (~7% smaller UI frames); still baseline, tables 0/1
    img.save(buf, "JPEG", quality=quality, subsampling=subsampling, progressive=False, optimize=optimize)
    return strip_app_segments(buf.getvalue())


def encode_jpeg(img: Image.Image, quality: int = 85, subsampling: Subsampling = "4:2:0",
                max_bytes: int | None = None, optimize: bool = False) -> bytes:
    """Baseline JPEG of an even-sized RGB image. With `max_bytes`, the highest quality in
    [MIN_QUALITY, quality] that fits is chosen (binary search); ValueError if none fits."""
    if img.width % 2 or img.height % 2:
        raise ValueError(f"image size {img.width}x{img.height} must be even")
    img = img.convert("RGB")
    best = _encode(img, quality, subsampling, optimize)
    if max_bytes is None or len(best) <= max_bytes:
        return best
    low, high, best = MIN_QUALITY, quality - 1, b""
    while low <= high:
        mid = (low + high) // 2
        candidate = _encode(img, mid, subsampling, optimize)
        if len(candidate) <= max_bytes:
            best, low = candidate, mid + 1
        else:
            high = mid - 1
    if not best:
        raise ValueError(f"cannot fit the image in {max_bytes} bytes even at quality {MIN_QUALITY}")
    return best


# --- RGB565 -----------------------------------------------------------------------------------------

def to_rgb565(img: Image.Image) -> bytes:
    """Row-major RGB565, little-endian per pixel."""
    rgb = img.convert("RGB")
    try:
        import numpy as np
    except ImportError:
        return _rgb565_pure(rgb.tobytes())
    a = np.asarray(rgb, dtype=np.uint16)
    packed = ((a[..., 0] >> 3) << 11) | ((a[..., 1] >> 2) << 5) | (a[..., 2] >> 3)
    return packed.astype("<u2").tobytes()


def _rgb565_pure(raw: bytes) -> bytes:
    out = bytearray(len(raw) // 3 * 2)
    for j, i in enumerate(range(0, len(raw), 3)):
        value = ((raw[i] >> 3) << 11) | ((raw[i + 1] >> 2) << 5) | (raw[i + 2] >> 3)
        out[2 * j] = value & 0xFF
        out[2 * j + 1] = value >> 8
    return bytes(out)
