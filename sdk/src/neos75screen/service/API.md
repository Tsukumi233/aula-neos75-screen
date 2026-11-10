# neos75d HTTP API

`neos75d` (`python -m neos75screen.service`) is the single owner of the keyboard screen. It listens on
`127.0.0.1:47575` (`--port`), never on other interfaces, and writes `%APPDATA%\neos75screen\service.json`:

```json
{"port": 47575, "token": "…", "pid": 1234, "fake": false, "api": 1}
```

Every call except `GET /api/health` needs the token: header `X-Neos75-Token: <token>` or `?token=<token>`
(for `<img>` and `EventSource`). Requests with a `Host` other than `127.0.0.1`/`localhost` are refused.
JSON in and out (UTF-8). Errors: `{"error": "...", "status": 400}` with that HTTP status (400 bad input,
401 token, 404 unknown route/widget/no frame, 409 needs the fake device or a running runner, 500).

The screen is opened only while the runner runs; `stop` releases it for other tools.

| method & path | body | returns |
|---|---|---|
| `GET /api/health` | | `{ok, api, pid, fake}` (no token) |
| `GET /api/status` | | `{api, service, device, keyboard, runner, pages, settings}` (below) |
| `GET /api/widgets` | | library: `[{name, title, description, group, options, fps, status_bar, path, mtime}]` |
| `GET /api/pages` | | selection: `[{entry, name, options, label, position, title, known}]` |
| `PUT /api/pages` | `{pages: [entry, ...]}` | new selection (1..8 entries, validated, saved atomically) |
| `POST /api/pages/add` | `{entry, position?}` | new selection (`position` 0-based, default end) |
| `POST /api/pages/remove` | `{index}` | new selection |
| `POST /api/pages/move` | `{from, to}` | new selection (`to` is the index after removal) |
| `POST /api/pages/replace` | `{index, entry}` | new selection (e.g. changed options) |
| `GET /api/preview?entry=&label=&width=&fresh=1` | | PNG of the entry with sample data (cached on disk) |
| `GET /api/screen.jpg` | | the frame the screen shows now (as rendered) |
| `POST /api/runner/start` · `/stop` · `/restart` | | `{ok, runner}` |
| `GET /api/settings` · `PUT /api/settings` | changes | `{run_on_start, interval, quality, extra_dirs}` |
| `GET /api/log` | | `[{time, message}]` (last 300) |
| `GET /api/events` | | server-sent events, see below |
| `GET /api/keyboard/via` | | the keyboard itself over VIA (not the screen): `connection` (wired / 2.4g), `protocol`, `layers`, `rows`, `cols`, `keymap[layer][row][col]`, `encoders[layer][encoder]` = `{cw, ccw, press}` (encoder 0 volume knob, 1 screen knob), `lighting`, `system` (item → value), `battery` `{percent, charging}`, `names` (code → label/name), `layout` (key rectangles with matrix positions, from the official definition; null without it), `macros` (16 action lists, see PUT below), `macro_space` `{count, size, used}`, `tap_dances` (`[{index, tap, hold, double_tap, tap_hold, term}]`), `labels` `{macros, tap_dances}` (index → name), `backup`, `backup_time`, `simulated` (the `--fake` keyboard). `macros`/`tap_dances` are null when the keyboard does not answer them. 409 when no keyboard is connected. The first read saves `keyboard_backup.json` |
| `GET /api/keyboard/via/catalog` | | keycode groups for a picker, lighting effects, system items and options, knobs |
| `PUT /api/keyboard/via/key` | `{layer, row, col, keycode}` | write a key into the keyboard (reboot/erase keycodes are refused, 400) |
| `PUT /api/keyboard/via/knob` | `{layer, encoder, gesture: cw/ccw/press, keycode}` | write a knob turn or press into the keyboard |
| `PUT /api/keyboard/via/lighting` | `{brightness?, effect?, speed?, hue?, saturation?}` | lighting, saved in the keyboard |
| `PUT /api/keyboard/via/system` | `{item, value}` | a system setting (see catalog) |
| `PUT /api/keyboard/via/macros` | `{macros: [[action…] ×≤16]}` | write all macros (whole buffer; actions `{type: tap/down/up, code ≤ 0xFF}`, `{type: delay, ms}`, `{type: text, text}` ASCII) → `{macros, macro_space}` |
| `PUT /api/keyboard/via/tapdance` | `{index, tap, hold, double_tap, tap_hold, term}` | write an advanced key (Vial tap dance; keycode 0x5700 + index) → the entry read back, plus `names` |
| `PUT /api/keyboard/via/label` | `{kind: macros/tap_dances, index, name}` | our own name for a macro or advanced key (kept on this PC, `keyboard_names.json`); empty name removes it |
| `POST /api/keyboard/via/restore` | | write the backup back (keys, knobs, lighting, settings, macros, advanced keys) |
| `GET /api/keyboard/via/battery` | | `{battery: {percent, charging} \| null}` from the keyboard |
| `GET /api/knobs` | | what the two knobs do: `config` (`enabled`, `screen`/`volume` → gesture → `{action, keys?, target?}`), `catalog` (knobs, gestures, actions, what `default` does), `status` (`hook`, `hook_error`, `takeover`, `firmware_takeover`, `connected`, `held`) |
| `PUT /api/knobs` | `{config}` | change it (validated, 400 for an unknown action or key; saved in `knobs.json`; applies at once) |
| `POST /api/knob` | `{direction: -1/0/1}` | turn the screen knob one step, as a person would (the keyboard needs `device.capabilities.test_knob`); press (0) only on the fake device |
| `POST /api/fake/keyboard` | `{battery, charging, link}` | fake device only |
| `POST /api/shutdown` | | stops the runner (releases the device) and the service |

Entries: a widget name plus options as a query string, `neos_photo?src=D:/a.jpg&full=1` (spaces as `%20`).
Labels: index 0 is "Page 7" (the stock pages are 1-6).

## Status

```json
{
  "service":  {"pid": 1, "port": 47575, "fake": false, "uptime": 12, "clients": 1},
  "device":   {"state": "idle|connecting|connected|absent|error", "error": null, "fake": false,
               "model": "ZG06_2.31_V2.0", "capabilities": {"proto_version": 2, "host_pages": true, ...}},
  "keyboard": {"reported": true, "live": true, "battery": 76, "charging": false, "link": 4, "connection": "2.4g"},
  "runner":   {"state": "stopped|starting|running|waiting|stopping|error", "error": null, "fps": 1.0,
               "frames": 31, "regions": 0, "paged": true, "page_index": 1, "page": "neos_system",
               "label": "Page 8", "visible": true, "since": 1762560000.0},
  "pages": [...], "settings": {...}
}
```

`runner.fps`: screen updates per second (full or region frames) over the last 3 s. `visible` false: the
knob is on a stock page (nothing is sent then).

## Events (`GET /api/events`)

`data: {json}` lines; the first event is `{"type": "status", ...status}`. Then:

| type | fields |
|---|---|
| `page` | `index`, `count`, `label`: the knob moved to another of our pages |
| `visibility` | `visible`, `reason`: our slot shown / the knob left for a stock page |
| `knob` | `direction` (1, -1, 0 = press), `knob`, `taken` (the firmware took the turn over: no page moved) |
| `knob_action` | `knob`, `gesture` (cw, ccw, press, hold_cw, hold_ccw), `action`: a mapped knob action ran |
| `knobs` | `config`: the knob actions changed |
| `keyboard` | as `status.keyboard` |
| `tick` | `fps`, `frames`, `page_index`, `visible`, `frame_version` (sent when one changes, ≤ 1/s) |
| `selection` | `pages` (also when the selection file was edited by someone else) |
| `runner`, `device` | `state`, `error` (+ `info` for device) |
| `settings` | `settings` |
| `log` | `message` |

A `: ping` comment every 15 s keeps idle connections open.
