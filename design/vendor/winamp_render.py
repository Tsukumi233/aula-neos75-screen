"""Render Webamp with museum skins at 480x360 (main window + equalizer, scaled to fit)."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
OUT = ROOT / "_webamp" / "render"
OUT.mkdir(parents=True, exist_ok=True)
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PAGE = """<!doctype html><html><head><meta charset="utf-8"><style>
html,body{margin:0;width:480px;height:360px;overflow:hidden;background:#2B2E31}</style></head><body>
<script src="https://unpkg.com/webamp@1.5.0/built/webamp.bundle.min.js"></script>
<script>
const w = new Webamp({
  initialSkin: {url: "https://r2.webampskins.org/skins/%(md5)s.wsz"},
  initialTracks: [{metaData: {artist: "Low Tide Orchestra", title: "Aerial Cartography"}, url: "about:blank", duration: 338}],
});
const host = document.createElement('div'); document.body.appendChild(host);
w.renderWhenReady(host).then(() => setTimeout(() => {
  const root = document.getElementById('webamp');
  const wins = [...root.querySelectorAll('#main-window, #equalizer-window')];
  const pl = root.querySelector('#playlist-window'); if (pl) pl.style.display = 'none';
  // stack main above equalizer at the origin, then scale to fit 480x360
  wins.forEach((el, i) => { const p = el.closest('div[style*="translate"]') || el.parentElement;
    p.style.transform = `translate(0px, ${i * 116}px)`; p.style.left = '0px'; p.style.top = '0px'; });
  const s = Math.min(480 / 275, 360 / 232);
  root.style.position = 'absolute'; root.style.left = ((480 - 275 * s) / 2) + 'px'; root.style.top = ((360 - 232 * s) / 2) + 'px';
  root.style.transformOrigin = '0 0'; root.style.transform = `scale(${s})`; root.style.imageRendering = 'pixelated';
}, 1500));
</script></body></html>"""


def render(md5, name):
    html = OUT / f"{name}.html"
    html.write_text(PAGE % {"md5": md5}, encoding="utf-8")
    png = OUT / f"{name}.png"
    subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--user-data-dir={ROOT / '_prof4'}",
                    "--window-size=480,360", "--virtual-time-budget=12000", f"--screenshot={png}", html.as_uri()],
                   capture_output=True)
    return png


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        md5, name = arg.split(":")
        print(render(md5, name))
