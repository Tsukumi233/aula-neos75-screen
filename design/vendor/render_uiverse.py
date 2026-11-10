"""Render matching Uiverse elements to PNG thumbnails and a labelled contact sheet."""
import re
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).parent
GALAXY = ROOT / "galaxy"
OUT = ROOT / "_uiverse"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
KEYS = re.compile(r"\b(retro|vintage|aero|pixel|crt|terminal|vfd|lcd|cassette|knob|skeuomorph\w*|metal\w*|clock|weather|music|player|radio player|neon|glow|old school|8-bit|8bit|win98|windows|mac os|ios|apple)\b", re.I)
WRAP = """<!doctype html><html><head><meta charset="utf-8"><script src="https://cdn.tailwindcss.com/3.4.1"></script>
<style>html,body{{margin:0;width:480px;height:360px;overflow:hidden;background:#2b2e31}}
.stage{{width:480px;height:360px;display:flex;align-items:center;justify-content:center}}</style></head>
<body><div class="stage">{body}</div></body></html>"""


def main(limit=400):
    OUT.mkdir(exist_ok=True)
    picks = []
    for f in sorted(GALAXY.rglob("*.html")):
        first = f.read_text(encoding="utf-8", errors="ignore").splitlines()[:1]
        if first and KEYS.search(first[0]):
            picks.append(f)
    picks = picks[:limit]
    print(len(picks), "elements")
    for i, f in enumerate(picks):
        png = OUT / f"{i:03d}.png"
        if png.exists():
            continue
        html = OUT / f"{i:03d}.html"
        html.write_text(WRAP.format(body=f.read_text(encoding="utf-8", errors="ignore")), encoding="utf-8")
        subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--user-data-dir={ROOT / '_prof'}",
                        "--window-size=480,360", "--virtual-time-budget=4000", f"--screenshot={png}", html.as_uri()],
                       capture_output=True)
    (OUT / "index.txt").write_text("\n".join(f"{i:03d}\t{f.relative_to(GALAXY)}\t{f.read_text(encoding='utf-8', errors='ignore').splitlines()[0]}"
                                             for i, f in enumerate(picks)), encoding="utf-8")
    # contact sheets: 6 x 5 per sheet, thumbnails at 240x180
    font = ImageFont.truetype(r"C:\Windows\Fonts\consola.ttf", 16)
    per = 30
    for s in range(0, len(picks), per):
        sheet = Image.new("RGB", (6 * 250 + 10, 5 * 210 + 10), "#111")
        d = ImageDraw.Draw(sheet)
        for k, i in enumerate(range(s, min(s + per, len(picks)))):
            x, y = 10 + (k % 6) * 250, 10 + (k // 6) * 210
            try:
                sheet.paste(Image.open(OUT / f"{i:03d}.png").convert("RGB").resize((240, 180)), (x, y))
            except Exception:
                pass
            d.text((x + 4, y + 182), f"{i:03d}", fill="#ddd", font=font)
        sheet.save(OUT / f"sheet_{s // per:02d}.png")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 400)
