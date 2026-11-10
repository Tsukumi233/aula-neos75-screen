"""Download and unpack the official Neos75 screen UI pack (personal use; vendor files stay out of git).

The Aula Hub lists it for the screen device (vid 0x5542, pid 1) as file 419,
"2915-ZG06_2_31_V20_NEOS_SCREEN_UI_20260615.bin" ("屏幕UI部分固件", V3.03). Layout: a 0x40-byte text banner
("LVGL ALL BIN VER NEOS 480*360 V1.0.1 2026-06-15"), u32 count at 0x40, then `count` entries of 0x4c bytes
(u32 offset, u32 header, u32 size, name[0x40]) for sub-packs whose data starts at 0x174: merged LVGL fonts
(MiSans VF 20/40/50/60), merged LVGL images, merged JPEGs, and a 16-byte signature. The JPEG sub-pack holds
the page backgrounds and animation frames; their names appear in the same order as the images.

Usage: python tools/ui_pack.py [--bin path] [--out design/official]
"""
from __future__ import annotations

import argparse
import re
import struct
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
URL = "https://hubapi.aulacn.com/user/BIN/getFile/419"


def subpacks(b: bytes) -> list[tuple[str, int, int]]:
    count = struct.unpack_from("<I", b, 0x40)[0]
    out = []
    for i in range(count):
        off, _hdr, size = struct.unpack_from("<III", b, 0x44 + i * 0x4C)
        name = b[0x50 + i * 0x4C:0x50 + i * 0x4C + 0x40].split(b"\0")[0].decode()
        out.append((name, 0x174 + off, size))
    return out


def jpegs(b: bytes) -> list[tuple[str, bytes]]:
    names = [m.group(1).decode() for m in re.finditer(rb"([A-Za-z0-9_]{2,40}\.jpg)\x00", b)]
    images = []
    for m in re.finditer(rb"\xff\xd8\xff", b):
        sos = b.find(b"\xff\xda", m.start())
        end = b.find(b"\xff\xd9", sos) + 2
        images.append(b[m.start():end])
    if len(names) != len(images):
        raise SystemExit(f"{len(names)} names but {len(images)} JPEGs: layout changed")
    return list(zip(names, images))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--bin", type=Path)
    p.add_argument("--out", type=Path, default=ROOT / "design" / "official")
    a = p.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    src = a.bin or a.out / "ui419.bin"
    if not src.exists():
        req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
        src.write_bytes(urllib.request.urlopen(req, timeout=120).read())
    b = src.read_bytes()
    print(b[:0x3E].decode(errors="replace").strip())
    for name, off, size in subpacks(b):
        print(f"  {name:36s} @0x{off:06x} {size} bytes")
    (a.out / "jpg").mkdir(exist_ok=True)
    for name, data in jpegs(b):
        (a.out / "jpg" / name).write_bytes(data)
    print(f"wrote {len(jpegs(b))} JPEGs to {a.out / 'jpg'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
