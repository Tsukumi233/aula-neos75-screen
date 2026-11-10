"""Build the Windows installer of the desktop app: app/dist/Neos75Screen-Setup-<version>.exe.

The installed app carries everything it needs, so it runs without this repository, a Python install or
Node.js:

  resources/python/        Python (the official embeddable build, same version as sdk/.venv) with the SDK and
                           the widget dependencies (psutil, media info, audio capture, numpy)
  resources/design/        the pages: design/neos, design/widgets, design/custom, the official artwork
                           (design/official/jpg) and the MiSans fonts (design/vendor/misans/Normal)
  resources/sdk/tests/     fake_device.py (the demo mode's simulated screen)
  resources/skill/         the agent skill (.claude/skills/neos75-screen), for the app's "复制提示词"
  resources/build-info.json  version, the repository it was built from (its design/custom is used when it
                           still exists), build time

Pages are rendered in the system Chrome or Edge, driven over DevTools (sdk live/cdp.py; no Playwright).
The installer is per-user (no administrator rights), into %LOCALAPPDATA%\\Programs, with Start menu and
desktop shortcuts and an uninstaller; settings in %APPDATA%\\neos75screen are kept on uninstall.

The installer contains the official artwork: personal use, never commit or publish it (app/dist is ignored).

Usage: sdk\\.venv\\Scripts\\python tools\\build_app.py [--stage-only]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
BUILD = APP / "build"
RES = BUILD / "resources"
CACHE = BUILD / "cache"
VENV_PY = ROOT / "sdk" / ".venv" / "Scripts" / "python.exe"

DESIGN = [  # (source under design/, ignored names)
    ("neos", []),
    ("widgets", ["_prof", "out", "src", "render.py"]),
    ("custom", []),
    ("official/jpg", []),
    ("vendor/misans/Normal", []),
]
IMPORTS = ("neos75screen.service.server, neos75screen.live.runner, neos75screen.live.cdp, hid, PIL, psutil, numpy, "
           "soundcard, winrt.windows.media.control")


def step(text: str) -> None:
    print(f"\n== {text}", flush=True)


def run(cmd: list, **kw) -> None:
    print("   $", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], check=True, **kw)


def venv_version() -> str:
    out = subprocess.run([str(VENV_PY), "-c", "import platform; print(platform.python_version())"],
                         check=True, capture_output=True, text=True)
    return out.stdout.strip()


def python_runtime(version: str) -> None:
    step(f"Python {version} (embeddable)")
    name = f"python-{version}-embed-amd64.zip"
    archive = CACHE / name
    if not archive.is_file():
        CACHE.mkdir(parents=True, exist_ok=True)
        url = f"https://www.python.org/ftp/python/{version}/{name}"
        print("   download", url, flush=True)
        tmp = archive.with_suffix(".part")
        with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f)
        tmp.replace(archive)
    target = RES / "python"
    with zipfile.ZipFile(archive) as z:
        z.extractall(target)
    # the ._pth file fixes sys.path: add site-packages and let `site` run
    pth = next(target.glob("python3*._pth"))
    lines = [ln for ln in pth.read_text().splitlines() if ln.strip() and ln.strip() != "#import site"]
    pth.write_text("\n".join([*lines, "Lib\\site-packages", "import site", ""]))


def site_packages() -> None:
    step("SDK and widget dependencies")
    target = RES / "python" / "Lib" / "site-packages"
    run([VENV_PY, "-m", "pip", "install", "--disable-pip-version-check", "--no-warn-script-location", "-q",
         "--target", target, f"{ROOT / 'sdk'}[widgets]"])
    shutil.rmtree(target / "bin", ignore_errors=True)
    # setuptools leaves these in the source tree when it builds the wheel
    shutil.rmtree(ROOT / "sdk" / "build", ignore_errors=True)
    for egg in (ROOT / "sdk" / "src").glob("*.egg-info"):
        shutil.rmtree(egg, ignore_errors=True)


def design() -> None:
    step("Pages and artwork")
    for sub, ignore in DESIGN:
        src = ROOT / "design" / sub
        if not src.is_dir():
            if sub.startswith("official"):
                sys.exit("design/official/jpg is missing: run setup.ps1 (or tools/ui_pack.py) first")
            if sub.startswith("vendor"):
                sys.exit("design/vendor/misans is missing: run setup.ps1 first")
            continue
        shutil.copytree(src, RES / "design" / sub,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", *ignore), dirs_exist_ok=True)
    shutil.copy2(ROOT / "design" / "SOURCES.md", RES / "design" / "SOURCES.md")
    definition = ROOT / "design" / "official" / "AULA-Neos75_via_v3.json"   # keyboard layout (from Aula Hub)
    if definition.is_file():
        shutil.copy2(definition, RES / "design" / "official" / definition.name)
    else:
        print("   (no design/official/AULA-Neos75_via_v3.json: the keyboard page shows the key matrix instead)")
    fake = RES / "sdk" / "tests"
    fake.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "sdk" / "tests" / "fake_device.py", fake / "fake_device.py")
    shutil.copytree(ROOT / ".claude" / "skills" / "neos75-screen", RES / "skill", dirs_exist_ok=True)


def build_info(version: str) -> None:
    info = {"version": version, "repo": str(ROOT), "built": dt.datetime.now().isoformat(timespec="seconds")}
    (RES / "build-info.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")


def icon() -> None:
    step("Icon")
    from PIL import Image
    official = ROOT / "design" / "official" / "jpg" / "sg25.jpg"
    if official.is_file():   # the chrome ring of the official artwork, as the window and tray use
        img = Image.open(official).convert("RGBA").crop((53, 3, 53 + 134, 3 + 134))
    else:
        img = Image.open(APP / "assets" / "ring.png").convert("RGBA")
    img = img.resize((256, 256), Image.LANCZOS)
    img.save(BUILD / "icon.ico", sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])


def smoke() -> None:
    step("Check the bundled Python")
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV")}
    env["NEOS75_ROOT"] = str(RES)
    run([RES / "python" / "python.exe", "-B", "-c",
         f"import {IMPORTS}; from neos75screen.live import library; "
         "n = len(library.widgets()); assert n >= 10, n; print('   ok:', n, 'pages')"], env=env)


def installer(version: str) -> Path:
    step("Installer (electron-builder)")
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if not npm:
        sys.exit("Node.js / npm not found")
    run([npm, "run", "dist"], cwd=APP)
    out = APP / "dist" / f"Neos75Screen-Setup-{version}.exe"
    if not out.is_file():
        sys.exit(f"no installer at {out}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--stage-only", action="store_true", help="prepare app/build/resources, no installer")
    args = ap.parse_args()
    if not VENV_PY.is_file():
        sys.exit("sdk/.venv is missing: run setup.ps1 first")
    app_version = json.loads((APP / "package.json").read_text(encoding="utf-8"))["version"]
    if RES.exists():
        shutil.rmtree(RES)   # fails loudly when a file in it is in use
    RES.mkdir(parents=True)
    python_runtime(venv_version())
    site_packages()
    design()
    build_info(app_version)
    icon()
    smoke()
    if args.stage_only:
        print(f"\nstaged: {RES}")
        return
    out = installer(app_version)
    print(f"\ninstaller: {out} ({out.stat().st_size / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
