"""`neos75ctl`: control the screen service from a terminal or an agent. Every command takes `--json` for
machine-readable output (one JSON document; `events` prints one JSON object per line).

Pages are numbered as on the screen: the stock pages are 1-6, yours are Page 7..14. Commands that change
the page selection work without the service too (they edit the selection file, which a running service
follows); everything about the live screen needs `neos75d`.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..live import library
from . import catalog
from .client import ApiFailure, Client, ServiceUnavailable
from .paths import Settings, pages_file

EXIT_OK, EXIT_ERROR, EXIT_USAGE, EXIT_NO_SERVICE = 0, 1, 2, 3


class CliError(Exception):
    def __init__(self, message: str, code: int = EXIT_ERROR) -> None:
        super().__init__(message)
        self.code = code


def _client(required: bool = True) -> Client | None:
    try:
        return Client.discover()
    except ServiceUnavailable as exc:
        if required:
            raise CliError(f"{exc}. Start it with `neos75d` (or the desktop app).", EXIT_NO_SERVICE) from exc
        return None


def _out(args: argparse.Namespace, data: object, text: str | None = None) -> None:
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
    elif text is not None:
        print(text)


# --- status --------------------------------------------------------------------------------------------

RUNNER = {"stopped": "已停止", "starting": "启动中", "running": "运行中", "waiting": "等待设备", "stopping": "停止中",
          "error": "出错"}
DEVICE = {"idle": "未占用", "connecting": "连接中", "connected": "已连接", "absent": "未找到", "error": "出错"}
LINK = {"bluetooth": "蓝牙", "2.4g": "2.4G", "wired": "有线", "unknown": "未知"}


def cmd_status(args: argparse.Namespace) -> None:
    api = _client(required=False)
    if api is None:
        data = {"service": None, "pages": catalog.selection(library.load(pages_file()))}
        _out(args, data, "服务未运行（neos75d）。\n" + _pages_text(data["pages"]))
        return
    s = api.get("/api/status")
    r, d, k = s["runner"], s["device"], s["keyboard"]
    lines = [f"服务     pid {s['service']['pid']}  端口 {s['service']['port']}{'  （模拟设备，未连接键盘）' if s['service']['fake'] else ''}",
             f"设备     {DEVICE.get(d['state'], d['state'])}{'  ' + d.get('model', '') if d.get('model') else ''}"
             + (f"  ({d['error']})" if d.get("error") else ""),
             f"运行     {RUNNER.get(r['state'], r['state'])}  {r['fps']} fps  已发送 {r['frames']} 帧"
             + (f"  当前 {r['label']} {r['page']}" if r.get("page") else "")
             + ("" if r.get("visible") is None else ("  屏幕显示中" if r["visible"] else "  屏幕在原厂页"))
             + (f"  ({r['error']})" if r.get("error") else "")]
    if s["service"]["fake"]:
        lines.insert(0, "*** 模拟设备：以下不是你键盘的真实数据 ***")
    sim = "（模拟值）" if k.get("simulated") else ""
    if k.get("reported"):
        lines.append(f"键盘     电量 {k['battery']}%{sim}{'  充电中' if k.get('charging') else ''}  {LINK.get(k.get('connection'), '')}")
    else:
        lines.append("键盘     无状态" + ("（模拟设备没有键盘）" if k.get("simulated") else ""))
    _out(args, s, "\n".join(lines) + "\n" + _pages_text(s["pages"]))


def _pages_text(pages: list[dict]) -> str:
    rows = [f"  {p['label']:8} {p['entry']}  —  {p['title']}" for p in pages]
    return "旋钮页面（Page 1-6 为原厂页）:\n" + "\n".join(rows)


# --- library and selection -------------------------------------------------------------------------------

def cmd_widgets(args: argparse.Namespace) -> None:
    api = _client(required=False)
    items = api.get("/api/widgets") if api else catalog.widgets(Settings.load().dirs)
    if args.group:
        items = [w for w in items if w["group"] == args.group]
    text = "\n".join(f"{w['name']:20} {w['title']}  [{w['group']}]"
                     + (f"  选项: {', '.join(o['key'] for o in w['options'])}" if w["options"] else "")
                     for w in items)
    _out(args, items, text)


def _entries(api: Client | None) -> list[str]:
    if api is not None:
        return [p["entry"] for p in api.get("/api/pages")]
    return library.load(pages_file())


def _save(api: Client | None, entries: list[str]) -> list[dict]:
    if api is not None:
        return api.put("/api/pages", {"pages": entries})
    library.save(pages_file(), entries, Settings.load().dirs)
    return catalog.selection(entries, Settings.load().dirs)


def _slot(value: str, entries: list[str], insert: bool = False) -> int:
    """`Page 9`, `9` (a page number on the screen, 7..14) or an entry name -> list index."""
    text = value.strip().lower().removeprefix("page").strip()
    if text.isdigit():
        index = int(text) - library.STOCK_PAGES - 1
        limit = len(entries) + (1 if insert else 0)
        if not 0 <= index < limit:
            raise CliError(f"page {value}: your pages are Page {library.STOCK_PAGES + 1}"
                           f"..Page {library.STOCK_PAGES + limit}", EXIT_USAGE)
        return index
    if value in entries:
        return entries.index(value)
    matches = [i for i, e in enumerate(entries) if catalog.split(e)[0] == value]
    if len(matches) == 1:
        return matches[0]
    raise CliError(f"{value!r} is not in the selection", EXIT_USAGE)


def cmd_pages(args: argparse.Namespace) -> None:
    api = _client(required=False)
    entries = _entries(api)
    action = args.action
    if action == "show":
        pages = catalog.selection(entries, Settings.load().dirs)
        _out(args, pages, _pages_text(pages))
        return
    if action == "set":
        new = [n for arg in args.items for n in arg.split(",") if n]
    elif action == "add":
        if len(args.items) != 1:
            raise CliError("pages add ENTRY [--at PAGE]", EXIT_USAGE)
        at = _slot(args.at, entries, insert=True) if args.at else len(entries)
        new = entries[:at] + [args.items[0]] + entries[at:]
    elif action == "remove":
        if len(args.items) != 1:
            raise CliError("pages remove PAGE|ENTRY", EXIT_USAGE)
        new = list(entries)
        new.pop(_slot(args.items[0], entries))
    elif action == "move":
        if len(args.items) != 2:
            raise CliError("pages move PAGE TO_PAGE", EXIT_USAGE)
        new = list(entries)
        item = new.pop(_slot(args.items[0], entries))
        new.insert(_slot(args.items[1], entries), item)
    elif action == "options":
        if not args.items:
            raise CliError("pages options PAGE key=value ...", EXIT_USAGE)
        index = _slot(args.items[0], entries)
        name, options = catalog.split(entries[index])
        for pair in args.items[1:]:
            key, sep, value = pair.partition("=")
            if not sep:
                raise CliError(f"option {pair!r}: use key=value (empty value removes it)", EXIT_USAGE)
            options[key] = value
        new = list(entries)
        new[index] = catalog.join(name, options)
    else:
        raise CliError(f"unknown action {action}", EXIT_USAGE)
    try:
        pages = _save(api, new)
    except ApiFailure as exc:
        raise CliError(str(exc), EXIT_USAGE) from exc
    _out(args, pages, _pages_text(pages) + ("" if api else "\n（服务未运行：已写入选择文件）"))


# --- previews and the live screen ------------------------------------------------------------------------

def cmd_preview(args: argparse.Namespace) -> None:
    out = Path(args.output or f"{catalog.split(args.entry)[0]}.png")
    api = None if args.offline else _client(required=False)
    if api is not None:
        png = api.preview(args.entry, label=args.label, fresh=args.fresh)
    else:
        from .preview import Previewer
        previewer = Previewer(None, Settings.load().dirs, log=lambda *_: None)
        try:
            png = previewer.render(args.entry, label=args.label)
        finally:
            previewer.close()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(png)
    _out(args, {"entry": args.entry, "png": str(out.resolve()), "bytes": len(png)}, f"{out.resolve()}")


def cmd_screenshot(args: argparse.Namespace) -> None:
    api = _client()
    try:
        jpeg = api.request("GET", "/api/screen.jpg", raw=True)
    except ApiFailure as exc:
        raise CliError(str(exc)) from exc
    out = Path(args.output or "screen.jpg")
    out.write_bytes(jpeg)
    _out(args, {"jpg": str(out.resolve()), "bytes": len(jpeg)}, str(out.resolve()))


def cmd_runner(args: argparse.Namespace) -> None:
    api = _client()
    result = api.post(f"/api/runner/{args.command}")
    _out(args, result, f"{RUNNER.get(result['runner']['state'], result['runner']['state'])}")


def cmd_settings(args: argparse.Namespace) -> None:
    api = _client(required=False)
    changes = {}
    for pair in args.pairs:
        key, sep, value = pair.partition("=")
        if not sep:
            raise CliError(f"{pair!r}: use key=value", EXIT_USAGE)
        try:
            changes[key] = json.loads(value)
        except ValueError:
            changes[key] = value
    if api is not None:
        settings = api.put("/api/settings", changes) if changes else api.get("/api/settings")
    else:
        current = Settings.load()
        if changes:
            current.update(changes)
            current.save()
        settings = current.to_dict()
    _out(args, settings, "\n".join(f"{k} = {json.dumps(v, ensure_ascii=False)}" for k, v in settings.items()))


def cmd_events(args: argparse.Namespace) -> None:
    api = _client()
    kinds = set(args.type.split(",")) if args.type else None
    seen = 0
    try:
        for event in api.events():
            if kinds and event["type"] not in kinds:
                continue
            print(json.dumps(event, ensure_ascii=False, default=str), flush=True)  # always JSON lines
            seen += 1
            if args.count and seen >= args.count:
                return
    except KeyboardInterrupt:
        pass


def cmd_knob(args: argparse.Namespace) -> None:
    api = _client()
    direction = {"next": 1, "prev": -1, "press": 0}[args.direction]
    try:
        result = api.post("/api/knob", {"direction": direction})
    except ApiFailure as exc:
        raise CliError(str(exc)) from exc
    _out(args, result, "ok")


def cmd_knobs(args: argparse.Namespace) -> None:
    """Show or change what the two knobs do (`knobmap`): through the service if it runs, else the file."""
    from .. import knobmap
    from .paths import knobs_file
    api = _client(required=False)
    config = api.get("/api/knobs")["config"] if api is not None else knobmap.load(knobs_file())
    items = list(args.items)
    if args.action == "set":
        if len(items) < 2 or "." not in items[0]:
            raise CliError("use: knobs set KNOB.GESTURE ACTION [SHORTCUT | TARGET]", EXIT_USAGE)
        knob, _, gesture = items[0].partition(".")
        if knob not in knobmap.KNOBS or gesture not in knobmap.GESTURES:
            raise CliError(f"{items[0]}: knob is screen|volume, gesture is {'|'.join(knobmap.GESTURES)}", EXIT_USAGE)
        action = items[1]
        entry: dict = {"action": action}
        needs = knobmap.ACTIONS.get(action, (None, None, None))[2]
        if needs:
            if len(items) < 3:
                raise CliError(f"{action} needs a {'shortcut' if needs == 'keys' else 'program, file or URL'}",
                               EXIT_USAGE)
            entry[needs] = " ".join(items[2:])
        config[knob][gesture] = entry
    elif args.action == "reset":
        if items:
            for item in items:
                knob, _, gesture = item.partition(".")
                if knob not in knobmap.KNOBS or gesture not in knobmap.GESTURES:
                    raise CliError(f"{item}: not a knob gesture", EXIT_USAGE)
                config[knob][gesture] = {"action": "default"}
        else:
            config = knobmap.default_config()
    elif args.action in ("on", "off"):
        config["enabled"] = args.action == "on"
    if args.action != "show":
        config = knobmap.validate(config)
        config = api.put("/api/knobs", {"config": config})["config"] if api is not None \
            else knobmap.save(knobs_file(), config)
    labels = {a: label for a, (label, _, _) in knobmap.ACTIONS.items()}
    lines = [f"自定义动作：{'开' if config['enabled'] else '关'}" + ("" if api is not None else "（服务未运行，下次启动生效）")]
    for knob in knobmap.KNOBS:
        lines.append(knobmap.KNOB_LABELS[knob])
        for gesture in knobmap.GESTURES:
            entry = config[knob][gesture]
            text = labels[entry["action"]]
            if entry["action"] == "default":
                text += f"（{knobmap.DEFAULT_MEANING.get((knob, gesture), '同普通转动')}）"
            extra = entry.get("keys") or entry.get("target")
            lines.append(f"  {knob}.{gesture:<9} {knobmap.GESTURE_LABELS[gesture]:<8} {text}{'  ' + extra if extra else ''}")
    _out(args, config, "\n".join(lines))


def cmd_log(args: argparse.Namespace) -> None:
    api = _client()
    lines = api.get("/api/log")[-args.lines:]
    import time
    _out(args, lines, "\n".join(f"{time.strftime('%H:%M:%S', time.localtime(l['time']))} {l['message']}" for l in lines))


# --- writing pages ---------------------------------------------------------------------------------------

def cmd_new(args: argparse.Namespace) -> None:
    from .check import new_page
    try:
        path = new_page(args.name, args.title or args.name, args.description or "",
                        Path(args.folder) if args.folder else None, args.force)
    except ValueError as exc:
        raise CliError(str(exc), EXIT_USAGE) from exc
    _out(args, {"name": args.name, "path": str(path)}, str(path))


def cmd_check(args: argparse.Namespace) -> None:
    from .check import check
    try:
        report = check(args.entry, Path(args.output) if args.output else None, dirs=Settings.load().dirs)
    except ValueError as exc:
        raise CliError(str(exc), EXIT_USAGE) from exc
    text = [f"{'OK' if report['ok'] else '有问题'}  {report['path']}"]
    text += [f"  问题: {p}" for p in report["problems"]]
    text += [f"  提示: {w}" for w in report["warnings"]]
    text += [f"  控制台: {c}" for c in report["console"]]
    if report["png"]:
        text.append(f"  截图: {report['png']}")
    _out(args, report, "\n".join(text))
    if not report["ok"]:
        raise SystemExit(EXIT_ERROR)


# --- parser ----------------------------------------------------------------------------------------------

def _parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="machine-readable output")
    parser = argparse.ArgumentParser(prog="neos75ctl", parents=[common],
                                     description="Control the Neos75 screen service (neos75d)")
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name: str, func, help_text: str) -> argparse.ArgumentParser:  # noqa: ANN001
        cmd = sub.add_parser(name, help=help_text, parents=[common])
        cmd.set_defaults(func=func)
        return cmd

    add("status", cmd_status, "service, device, keyboard, runner and selected pages")
    w = add("widgets", cmd_widgets, "the page library (every widget that can be a page)")
    w.add_argument("--group", choices=["neos", "custom", "classic", "extra"])
    p = add("pages", cmd_pages, "show or change the knob pages: show | set A B C | add E [--at 9] | remove 9 | "
                                "move 9 7 | options 9 key=value")
    p.add_argument("action", nargs="?", default="show", choices=["show", "set", "add", "remove", "move", "options"])
    p.add_argument("items", nargs="*")
    p.add_argument("--at", help="add: insert at this page (e.g. 7); default: at the end")
    pv = add("preview", cmd_preview, "render a page to PNG (sample data)")
    pv.add_argument("entry", help="widget name with optional ?options, or a path to an .html file")
    pv.add_argument("-o", "--output")
    pv.add_argument("--label", default="Page 7")
    pv.add_argument("--fresh", action="store_true", help="ignore the cache")
    pv.add_argument("--offline", action="store_true", help="render in this process, not in the service")
    sc = add("screenshot", cmd_screenshot, "the frame the screen shows now (JPEG)")
    sc.add_argument("-o", "--output")
    for name, text in (("start", "start the widget runner"), ("stop", "stop it (releases the device)"),
                       ("restart", "restart it")):
        add(name, cmd_runner, text)
    st = add("settings", cmd_settings, "show or change settings: key=value (JSON values)")
    st.add_argument("pairs", nargs="*")
    ev = add("events", cmd_events, "stream events as JSON lines (page, knob, visibility, keyboard, tick, ...)")
    ev.add_argument("--type", help="comma-separated event types")
    ev.add_argument("--count", type=int, help="stop after this many events")
    kn = add("knob", cmd_knob, "turn the screen knob one step (next/prev; press: fake device only)")
    kn.add_argument("direction", choices=["next", "prev", "press"])
    ko = add("knobs", cmd_knobs, "what the two knobs do: show | set screen.cw scroll_down | set volume.press keys "
                                 "ctrl+z | set screen.hold_cw run C:/app.exe | reset [screen.cw] | on | off")
    ko.add_argument("action", nargs="?", default="show", choices=["show", "set", "reset", "on", "off"])
    ko.add_argument("items", nargs="*")
    lg = add("log", cmd_log, "recent service log")
    lg.add_argument("--lines", type=int, default=40)
    nw = add("new", cmd_new, "create a new page from the template (design/custom/NAME.html)")
    nw.add_argument("name")
    nw.add_argument("--title")
    nw.add_argument("--description")
    nw.add_argument("--folder")
    nw.add_argument("--force", action="store_true")
    ck = add("check", cmd_check, "load a page offline and report problems (+ screenshot with -o)")
    ck.add_argument("entry")
    ck.add_argument("-o", "--output")
    return parser


def main(argv: list[str] | None = None) -> int:
    if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    argv = list(sys.argv[1:] if argv is None else argv)
    args = _parser().parse_args(argv)
    args.json = "--json" in argv     # accepted before or after the command
    try:
        args.func(args)
    except CliError as exc:
        if args.json:
            print(json.dumps({"error": str(exc), "code": exc.code}, ensure_ascii=False))
        else:
            print(f"error: {exc}", file=sys.stderr)
        return exc.code
    except ApiFailure as exc:
        if args.json:
            print(json.dumps({"error": str(exc), "status": exc.status}, ensure_ascii=False))
        else:
            print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except ValueError as exc:
        if args.json:
            print(json.dumps({"error": str(exc), "code": EXIT_USAGE}, ensure_ascii=False))
        else:
            print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except SystemExit as exc:
        return int(exc.code or 0)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
