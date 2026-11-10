"""Render the adapted widgets to 480x360 PNGs and a contact sheet (headless Chrome)."""
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).parent
OUT = HERE / "out"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
WIDGETS = [("hifi_clock", "Hi-Fi · 时钟（Paul Noble 的 Braun BC17 表盘）"),
           ("hifi_system", "Hi-Fi · 系统监控（J Lopes 的 VU 表与推子）"),
           ("hifi_player", "Hi-Fi · 正在播放（Webamp + Hitachi 皮肤）"),
           ("retro_clock", "复古未来 · 时钟（rajtilak 的辉光管座钟）"),
           ("retro_player", "复古未来 · 正在播放（Tom Hazledine 的磁带）")]


def render(name, wait=8000):
    OUT.mkdir(exist_ok=True)
    png = OUT / f"{name}.png"
    subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--allow-file-access-from-files",
                    f"--user-data-dir={HERE / '_prof'}", "--window-size=480,360", f"--virtual-time-budget={wait}",
                    f"--screenshot={png}", (HERE / f"{name}.html").as_uri()], capture_output=True)
    return png


def sheet():
    font = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 18)
    W, H, G, cols = 480, 360, 14, 3
    rows = (len(WIDGETS) + cols - 1) // cols
    img = Image.new("RGB", (G + cols * (W + G), G + rows * (H + 34 + G)), "#151617")
    d = ImageDraw.Draw(img)
    for k, (name, title) in enumerate(WIDGETS):
        x, y = G + (k % cols) * (W + G), G + (k // cols) * (H + 34 + G)
        img.paste(Image.open(OUT / f"{name}.png").convert("RGB"), (x, y))
        d.text((x, y + H + 6), title, fill="#e8e6e1", font=font)
    img.save(HERE.parent / "widgets_preview.png")


if __name__ == "__main__":
    names = sys.argv[1:] or [n for n, _ in WIDGETS]
    for n in names:
        print(render(n, 14000 if "player" in n else 8000))
    sheet()
