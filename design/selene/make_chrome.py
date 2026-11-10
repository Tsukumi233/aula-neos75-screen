"""Turn CC0 museum sculpture photos into liquid-chrome cut-outs for the Selene theme.

Pipeline: cut the sculpture from its studio background (rembg), luminance -> S-curve -> a non-monotonic
"chrome" gradient map (dark troughs, bright bands, a second dark band, a hot highlight: what makes polished
metal read as metal), then a faint iridescent sheen on the brightest areas, as on the official screen art.
Output: PNG with alpha in assets/.
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter
from rembg import remove

HERE = Path(__file__).parent
OUT = HERE / "assets"

# luminance stops (0..1) -> grey; a cool neutral chrome, matching the gunmetal body
CHROME = [(0.00, 10), (0.20, 40), (0.36, 150), (0.46, 222), (0.56, 120), (0.66, 70), (0.78, 170), (0.90, 240), (1.00, 255)]


def gradient_map(lum):
    xs = np.array([p for p, _ in CHROME]); ys = np.array([v for _, v in CHROME], dtype=float)
    return np.interp(lum, xs, ys)


def chrome(src: Path, name: str, size: int = 900) -> Path:
    img = Image.open(src).convert("RGB")
    img.thumbnail((size, size), Image.LANCZOS)
    cut = remove(img)                                   # RGBA, background removed
    a = np.asarray(cut).astype(float) / 255.0
    rgb, alpha = a[..., :3], a[..., 3]
    lum = rgb @ np.array([0.299, 0.587, 0.114])
    # smooth marble grain so the chrome bands follow the form, not the stone texture
    lum = np.asarray(Image.fromarray((lum * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.6))).astype(float) / 255.0
    # local contrast first so the gradient map follows the carving, not the overall lighting
    lo, hi = np.percentile(lum[alpha > 0.5], [2, 98])
    lum = np.clip((lum - lo) / max(1e-3, hi - lo), 0, 1)
    lum = 0.5 - 0.5 * np.cos(np.pi * lum)              # S-curve
    grey = gradient_map(lum) / 255.0
    # cool tint: steel blue in the darks, neutral in the lights
    tint = np.stack([grey * 0.93 + 0.02, grey * 0.96 + 0.02, grey * 1.0 + 0.03], -1)
    # iridescent sheen on highlights (hue varies across the image), very faint
    h, w = grey.shape
    yy, xx = np.mgrid[0:h, 0:w]
    hue = (xx / w * 0.9 + yy / h * 0.6) % 1.0
    irid = np.stack([0.5 + 0.5 * np.cos(2 * np.pi * (hue + k)) for k in (0.0, 0.33, 0.66)], -1)
    hl = np.clip((grey - 0.9) / 0.1, 0, 1)[..., None] * 0.14
    out = np.clip(tint * (1 - hl) + irid * hl, 0, 1)
    rgba = np.dstack([out, alpha])
    res = Image.fromarray((rgba * 255).astype(np.uint8), "RGBA")
    res = res.filter(ImageFilter.UnsharpMask(radius=2, percent=60, threshold=2))
    OUT.mkdir(exist_ok=True)
    path = OUT / f"{name}.png"
    res.save(path)
    return path


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        src, name = arg.split("=")
        print(chrome(Path(src), name))
