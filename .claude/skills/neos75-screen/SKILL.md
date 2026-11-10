---
name: neos75-screen
description: Control the AULA Neos75 keyboard screen and write pages for it. Use when the user wants to see what the keyboard screen shows, list/add/remove/reorder the screen knob's pages (Page 7-14), set page options (e.g. a picture for neos_photo), preview a page, or create a new 480x360 widget page (clock, stats, weather, anything) and put it on the screen, or change what the two knobs do (turn, press, turn while held). Also for "键盘屏幕", "旋钮页面", "Neos75 屏幕", "做一个屏幕页面", "旋钮动作", "自定义旋钮".
---

# Neos75 keyboard screen

The keyboard has a 480x360 screen whose knob turns through 6 stock pages (Page 1-6) and up to 8 of our own
pages (Page 7-14). A local service, `neos75d`, owns the screen (only one process may hold the USB device):
it renders our pages (HTML in headless Chrome) and streams frames to the screen. Everything goes through
the service or the selection file; **never open the HID device yourself, never flash firmware**.

Run commands from the repository root. The CLI is `sdk/.venv/Scripts/neos75ctl` (Windows). Always pass
`--json` and parse the output; exit codes: 0 ok, 1 error, 2 bad usage/unknown page, 3 service not running.

## Look first

```bash
sdk/.venv/Scripts/neos75ctl status --json        # service, device, keyboard battery, runner, fps, pages
sdk/.venv/Scripts/neos75ctl widgets --json       # the page library: name, title, description, options
sdk/.venv/Scripts/neos75ctl screenshot -o screen.jpg --json   # what the screen shows right now
```

Then Read the JPEG to see the screen. If the status says the service is not running (exit 3), page
selection commands still work (they edit `%APPDATA%\neos75screen\pages.json`, which the service follows
within a second once it runs); for the live screen ask the user to open the desktop app (`app/`, see
`app/README.md`) or start `sdk/.venv/Scripts/neos75d` themselves. Do not start `neos75d` against the real
keyboard on your own if another program might be using the device; `neos75d --fake` is always safe
(protocol-level fake screen, no keyboard).

## Change the knob pages

Pages are addressed by their number on the screen (`7`..`14`, or `"Page 9"`) or by entry name.

```bash
neos75ctl pages --json                                   # current Page 7.. list
neos75ctl pages set neos_clock neos_system neos_player --json   # replace the whole list (max 8)
neos75ctl pages add neos_nixie --at 8 --json            # insert as Page 8 (default: at the end)
neos75ctl pages remove 9 --json                         # or: remove neos_nixie
neos75ctl pages move 10 7 --json                        # Page 10 becomes Page 7
neos75ctl pages options 9 src=D:/pictures/a.jpg full=1 --json   # set options; key= (empty) removes one
```

An entry is a widget name plus optional options as a query string: `neos_photo?src=D:/a.jpg&full=1`. The
same widget may appear several times with different options. Options a widget takes are listed by
`widgets --json` (`options`: key, type, label, default, choices). Changes reach the screen within ~1 s;
no restart, no flashing. Confirm by `status --json` (and a `screenshot` if the user wants to see it).

## Preview without touching the screen

```bash
neos75ctl preview "neos_photo?src=D:/a.jpg&full=1" -o preview.png --json   # via the service (cached)
neos75ctl preview neos_hifi_meters -o p.png --offline --json             # no service needed
```

Read the PNG to judge it. Previews use sample data (see reference.md) and the current time.

## Write a new page

1. `neos75ctl new my_page --title "我的页面" --description "一句话说明" --json` creates
   `design/custom/my_page.html` from the template (Néos layout: official left strip + content area). Pages in
   `design/custom` appear in the library automatically.
2. Edit the HTML. Rules (details and the data fields in [reference.md](reference.md)):
   - The page is exactly 480x360; nothing scrolls. Black background.
   - Live data: include `../widgets/data.js`, define `window.render = () => {...}` reading `DATA.*`, call
     `render()` once. The runner calls `window.neos75Update(data)` every frame, which re-runs `render`.
   - Page number pill and keyboard status: include `../neos/status.js` **after** your script. On layouts
     without the stock status bar use `<body data-status="off">`.
   - Frame rate: `<body data-fps="N">` (default 1, max 30). Only changed 16x16 tiles are sent, so small
     moving parts can run at 10-20 fps; full-screen motion should stay low. `<body data-quality="N">`
     (30-95) sets the page's JPEG quality (photos higher, flat text pages can go lower). A page that reads
     `spec` / `lvl_l` / `lvl_r` must say `<body data-audio>`: the audio capture only runs while such a page
     is shown. A page whose picture does not change is rendered only 4 times a second (any change brings
     its own rate back).
   - Describe it for the app: `<meta name="neos75:title">`, `neos75:description`, and `neos75:options`
     (JSON list) for anything configurable; read options with `new URLSearchParams(location.search)`.
   - Style: follow the official Néos look (the user rejects self-invented "AI-flavoured" styling): build on
     the official artwork in `../official/jpg/` (page backgrounds `page1_bg.jpg`..`page6_bg.jpg`, the left
     strip `page5_bg.jpg`, chrome ring), MiSans via `../neos/neos.css` (classes `pill`, `track`, `big`,
     `lbl`, `dim`, `strip`, `chrome-text`), thin white type, hairline pills, polished-metal hairlines. Look
     at `design/neos/*.html` before inventing anything.
3. Check it: `neos75ctl check my_page -o my_page.png --json` loads it like the runner (script errors,
   failed loads, missing `neos75Update`, overflow beyond 480x360) and screenshots it. Read the PNG. Fix
   until `ok` is true and it looks right.
4. Put it on the screen: `neos75ctl pages add my_page --json`, then `knob next|prev` until
   `status --json` → `runner.page` is it, and `screenshot`. Tell the user which Page number it is.

## Change what the knobs do

Two knobs (`screen`, `volume`), five gestures each (`cw`, `ccw`, `press`, `hold_cw`, `hold_ccw` = turn while held
down). `neos75ctl knobs --json` shows the mapping; `knobs set screen.cw scroll_down`, `knobs set volume.press keys
ctrl+shift+t`, `knobs set screen.hold_cw run C:/Program Files/App/app.exe` (or a URL), `knobs reset [screen.cw]`,
`knobs off|on`. Actions: `default` (stock: pages / Calculator / volume / mute), `none`, `page_next`, `page_prev`,
`volume_up`, `volume_down`, `mute`, `play_pause`, `media_next`, `media_prev`, `scroll_up|down|left|right`,
`zoom_in|out`, `keys` (a shortcut), `run` (program, file or URL). Screen-knob turns are only remapped while the
service shows the screen (firmware takeover); presses and the volume knob work whenever the service runs.

## Other commands

`start` / `stop` / `restart` (stop releases the USB device), `settings --json` (`interval`, `quality`,
`run_on_start`, `extra_dirs`; set with `key=value`), `events --json --count N --type page,knob` (JSON
lines: page, knob, visibility, keyboard, tick, selection, runner, device, log), `log --json`,
`knob next|prev` (turns the real knob one step, so the screen shows the next/previous page: use it to show
the user a page or to screenshot one; `press` only on the fake device). HTTP API for scripts: `sdk/src/neos75screen/service/API.md`.
