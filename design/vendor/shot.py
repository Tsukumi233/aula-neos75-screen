"""shot.py <html-or-url> <out.png> [WxH] [wait_ms]: headless Chrome screenshot."""
import subprocess
import sys
from pathlib import Path

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
src, out = sys.argv[1], Path(sys.argv[2]).resolve()
size = sys.argv[3] if len(sys.argv) > 3 else "480,360"
wait = sys.argv[4] if len(sys.argv) > 4 else "5000"
url = src if "://" in src else Path(src).resolve().as_uri()
subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                f"--user-data-dir={Path(__file__).parent / '_prof3'}", f"--window-size={size}",
                f"--virtual-time-budget={wait}", f"--screenshot={out}", url], capture_output=True)
print(out, out.exists())
