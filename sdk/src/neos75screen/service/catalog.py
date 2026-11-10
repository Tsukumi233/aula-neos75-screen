"""The page library as clients see it: every widget with a title, a description, its group and the options
it takes (`?key=value` in a selection entry).

A page describes itself with meta tags in its <head> (the skill teaches agents to add them):

    <meta name="neos75:title" content="天气">
    <meta name="neos75:description" content="今天的天气和气温">
    <meta name="neos75:options" content='[{"key": "city", "type": "text", "label": "城市", "default": "上海"}]'>

Option types: `text`, `number`, `file` (a local file path; `accept` lists extensions), `select` (`choices`:
[[value, label], ...]), `bool` (`on` / `off`: the values written for checked / unchecked; "" leaves the key
out). Pages written before these tags get their description from `BUILTIN` below.
"""
from __future__ import annotations

import json
import re
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlencode

from ..live import library

_PHOTO_EXT = ["jpg", "jpeg", "png", "webp", "gif", "svg", "bmp"]

BUILTIN: dict[str, dict] = {
    "neos_clock": {"title": "时钟", "description": "星期、日期、周数，下方大字时间（原厂 Page 3 计时器底图）"},
    "neos_system": {"title": "系统监控", "description": "CPU、GPU、内存、GPU 温度、磁盘与下载速度（原厂 Page 1 底图）"},
    "neos_player": {"title": "正在播放", "description": "播放状态、曲名、歌手和进度（Windows 媒体会话，原厂 Page 4 底图）"},
    "neos_hifi_clock": {"title": "Hi-Fi 表盘", "description": "镀铬表圈指针时钟，红色秒针与日期窗"},
    "neos_hifi_meters": {"title": "Hi-Fi 电平表", "description": "CPU/GPU 指针 VU 表，内存、温度、磁盘、网络推子"},
    "neos_hifi_player": {"title": "Hi-Fi 播放器", "description": "曲目信息、真实左右声道电平与 24 段频谱"},
    "neos_nixie": {"title": "数码管时钟", "description": "玻璃舱里的段码时钟，银蓝色辉光"},
    "neos_cassette": {"title": "磁带", "description": "经典 CSS 磁带（Tom Hazledine 原作）铺满屏幕：标签手写曲名和歌手，带轴随播放转动，磁带卷随进度变化，壳上印播放状态和时间"},
    "neos_photo": {
        "title": "图片", "description": "你的图片，像原厂自定义图片页；可选全屏",
        "options": [
            {"key": "src", "type": "file", "label": "图片", "accept": _PHOTO_EXT},
            {"key": "full", "type": "bool", "label": "全屏（不显示左侧装饰条）", "on": "1", "off": "", "default": False},
            {"key": "fit", "type": "select", "label": "缩放", "default": "cover",
             "choices": [["cover", "填满裁切"], ["contain", "完整显示"]]},
            {"key": "pill", "type": "bool", "label": "显示页码", "on": "", "off": "0", "default": True},
        ],
    },
    "hifi_clock": {"title": "经典 · Hi-Fi 时钟", "description": "Braun 风格表盘"},
    "hifi_system": {"title": "经典 · Hi-Fi 系统", "description": "VU 表与推子"},
    "hifi_player": {"title": "经典 · Webamp", "description": "Webamp 播放器与皮肤",
                    "options": [{"key": "skin", "type": "text", "label": "皮肤（.wsz 路径或 URL）"}]},
    "retro_clock": {"title": "经典 · 辉光管座钟", "description": "复古辉光管时钟"},
    "retro_player": {"title": "经典 · 磁带播放", "description": "复古磁带播放器"},
}

_META = re.compile(r"<meta\s+name=[\"']neos75:(title|description|options)[\"']\s+content=(\"[^\"]*\"|'[^']*')", re.I)
_FPS = re.compile(r"<body[^>]*\bdata-fps=[\"']?([0-9.]+)", re.I)
_QUALITY = re.compile(r"<body[^>]*data-quality=[\"']?([0-9]+)", re.I)
_STATUS_OFF = re.compile(r"<body[^>]*\bdata-status=[\"']?off", re.I)
_TITLE = re.compile(r"<title>([^<]+)</title>", re.I)


def _group(path: Path, dirs: Sequence[Path]) -> str:
    parent = path.parent.resolve()
    for key, folder in (("neos", library.ROOT / "design" / "neos"), ("classic", library.ROOT / "design" / "widgets"),
                        ("custom", library.CUSTOM_DIR)):
        if parent == folder.resolve():
            return key
    return "extra"


def describe(name: str, path: Path, dirs: Sequence[Path] = ()) -> dict:
    """Library entry of one widget: name, title, description, group, options, fps, status bar, file."""
    info = dict(BUILTIN.get(name, {}))
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:16384]
    except OSError:
        head = ""
    for key, raw in _META.findall(head):
        value = raw[1:-1]
        if key.lower() == "options":
            try:
                options = json.loads(value.replace("&quot;", '"'))
            except ValueError:
                continue
            if isinstance(options, list):
                info["options"] = [o for o in options if isinstance(o, dict) and isinstance(o.get("key"), str)]
        else:
            info[key.lower()] = value
    if "title" not in info:
        match = _TITLE.search(head)
        info["title"] = match.group(1).strip() if match else name
    fps = _FPS.search(head)
    return {
        "name": name,
        "title": info["title"],
        "description": info.get("description", ""),
        "group": _group(path, dirs),
        "options": info.get("options", []),
        "fps": float(fps.group(1)) if fps else 1.0,
        "status_bar": not _STATUS_OFF.search(head),
        "quality": int(q.group(1)) if (q := _QUALITY.search(head)) else None,
        "path": str(path),
        "mtime": path.stat().st_mtime if path.exists() else 0,
    }


def widgets(dirs: Sequence[Path] = ()) -> list[dict]:
    order = {"neos": 0, "custom": 1, "classic": 2, "extra": 3}
    entries = [describe(name, path, dirs) for name, path in library.widgets(dirs).items()]
    return sorted(entries, key=lambda e: (order[e["group"]], e["name"]))


def split(entry: str) -> tuple[str, dict[str, str]]:
    """`neos_photo?src=a.jpg&full=1` -> ("neos_photo", {"src": "a.jpg", "full": "1"})."""
    name, _, query = entry.partition("?")
    return name, dict(parse_qsl(query, keep_blank_values=True))


def join(name: str, options: dict[str, str] | None = None) -> str:
    """The inverse of `split`; empty values are left out. Paths keep their slashes and colons readable;
    spaces become %20, not '+' (the runner hands the query to the page as it is, and a '+' would reach the
    page as a literal plus once the runner escapes it)."""
    options = {k: v for k, v in (options or {}).items() if v not in ("", None)}
    return name + ("?" + urlencode(options, safe=":/\\,", quote_via=quote) if options else "")


def selection(entries: Sequence[str], dirs: Sequence[Path] = ()) -> list[dict]:
    """The selected pages with their knob position label and title."""
    library_ = library.widgets(dirs)
    out = []
    for i, entry in enumerate(entries):
        name, options = split(entry)
        path = library_.get(name)
        title = describe(name, path, dirs)["title"] if path else name
        out.append({"entry": entry, "name": name, "options": options, "label": library.page_label(i),
                    "position": library.STOCK_PAGES + 1 + i, "title": title, "known": path is not None})
    return out
