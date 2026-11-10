"""Link/decode rate bench on the patched screen (no config writes: realtime ENTER, frames, LEAVE).

For each ACK window and Huffman mode: full 480x360 frames of real widget pages, a 320x32 region (level bar)
and a 320x64 region (spectrum bars). Prints frames per second and bytes. Needs region-frame firmware.
    sdk/.venv/Scripts/python -B hwtest/bench_link.py --pages <dir with page PNGs>
"""
import argparse
import random
import statistics
import time
from pathlib import Path

from PIL import Image, ImageDraw

from neos75screen import Screen
from neos75screen.imaging import encode_jpeg


def bars(seed: int, w: int = 320, h: int = 64, n: int = 24) -> Image.Image:
    random.seed(seed)
    img = Image.new("RGB", (w, h)); d = ImageDraw.Draw(img); bw = (w - (n - 1) * 3) / n
    for b in range(n):
        v = max(0.05, min(1, .6 - b * .015 + random.uniform(-.25, .25))); x = b * (bw + 3)
        d.rectangle((x, h - v * (h - 2), x + bw, h - 1), fill=(240, 240, 240))
    return img


def level(i: int) -> Image.Image:
    img = Image.new("RGB", (320, 32), (20, 20, 24))
    ImageDraw.Draw(img).rectangle((0, 8, 10 + (i * 37) % 300, 24), fill=(240, 240, 240))
    return img


def run(screen, frames, send) -> tuple[float, float]:  # noqa: ANN001
    t = time.perf_counter()
    for f in frames:
        r = send(f)
        assert r.shown, r
    return len(frames) / (time.perf_counter() - t), statistics.mean(len(f) for f in frames)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pages", type=Path, required=True)
    ap.add_argument("--quality", type=int, default=88)
    args = ap.parse_args()
    pages = [Image.open(p).convert("RGB") for p in sorted(args.pages.glob("*.png"))]
    with Screen.open() as screen:
        assert screen.capabilities.region, "needs region-frame firmware"
        screen.enter()
        for optimize in (True,):
            full = [encode_jpeg(p, args.quality, optimize=optimize) for p in pages] * 2
            lv = [encode_jpeg(level(i), args.quality, optimize=optimize) for i in range(40)]
            sp = [encode_jpeg(bars(i), args.quality, optimize=optimize) for i in range(40)]
            for window in (1, 2, 3):
                screen.ack_window = window
                f_fps, f_b = run(screen, full, screen.show_jpeg)
                l_fps, l_b = run(screen, lv, lambda j: screen.show_region(j, 144, 144))
                s_fps, s_b = run(screen, sp, lambda j: screen.show_region(j, 144, 160))
                t = time.perf_counter(); n = 0
                for k in range(4):   # realistic: a full frame (page switch), then region updates
                    assert screen.show_jpeg(full[k]).shown; n += 1
                    for j in sp[:20]:
                        assert screen.show_region(j, 144, 160).shown; n += 1
                m_fps = n / (time.perf_counter() - t)
                print(f"           mixed (1 full + 20 spectrum regions) x4: {m_fps:5.1f} fps")
                print(f"optimize={optimize!s:5} window={window}: full {f_fps:5.1f} fps ({f_b/1000:4.1f} KB)  "
                      f"level {l_fps:5.1f} fps ({l_b/1000:3.1f} KB)  spectrum {s_fps:5.1f} fps ({s_b/1000:3.1f} KB)",
                      flush=True)
        status = screen.status()
        print("device status", status)
        screen.leave()


if __name__ == "__main__":
    main()
