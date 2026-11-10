# Néos theme

Live widgets drawn on the official Neos75 page artwork, in MiSans like the stock firmware. The artwork is not
in git: run `python tools/ui_pack.py` once to download the official screen UI pack (Aula Hub file 419) and
unpack its 96 JPEGs into `design/official/jpg` (page backgrounds, the page 1 / page 6 animations, the boot
ring sequence, the default wallpaper). Personal use only.

| widget | stock page used | live content |
|---|---|---|
| `neos_clock.html` | Page 3 (timer) | weekday, date, ISO week; HH:MM:SS in the lower panel; "Timer Mode" pill replaced by "Clock" |
| `neos_system.html` | Page 1 | CPU, GPU, memory with chrome hairlines; GPU temperature, disk, download |
| `neos_player.html` | Page 4 | play state pill, title, artist, progress (Windows media session) |

Run: `sdk/.venv/Scripts/python -m neos75screen.cli widgets` (theme `neos` is the default). With firmware that
has host pages each widget is its own page of the screen knob's cycle (stock page 6 → clock → system → player →
stock page 1), turned to like a stock page; on older firmware they share one slot and a press switches.

All three keep the stock status bar alive (`status.js`): keyboard battery percent, a green dot while charging
and a red dot under the active connection (Bluetooth / wired / 2.4G), at the stock positions and colours. The
values come from `Screen.keyboard()` (firmware with HELLO flags bit 1, PROTOCOL.md 5.3b), polled every 5 s;
previews take `?kb_battery=57&kb_charging=1&kb_link=5` (`kb_battery=none`: not reported, all dots grey).

## Pages are web pages

Any 480x360 HTML page is a page of the screen knob's cycle: put it in `design/neos` (or `design/widgets`,
or `--folder`) and select it. Pages read live data from `../widgets/data.js` (`window.neos75Update`), may use
`neos.css`, the official artwork, and `status.js` (page number pill; keyboard status where the background
has the status bar; `<body data-status="off">` for pages without it), or none of them.

- `neos75 pages --list`: the library; `neos75 pages a b c`: select (at most 8, Page 7 onwards); a running
  `neos75 widgets` follows the change within a second, no restart, no flashing.
- Options per entry as a query string, e.g. `neos_photo?src=D:/pictures/a.jpg&full=1`.
- `<body data-fps="20">`: frames per second the page wants (default 1). With region-frame firmware only the
  changed 16x16 tiles are sent, so small moving parts (level meters, reels) run at 10-20 fps. A page whose
  picture stays the same for 2 s is rendered only 4 times a second until it changes again.
- `<body data-audio>`: the page reads `spec` / `lvl_l` / `lvl_r`; the audio loopback capture runs only while
  such a page is on the screen.

| page | layout | content |
|---|---|---|
| `neos_hifi_clock` | left strip (stock page 5) | chrome-bezel dial, red second hand, date window |
| `neos_hifi_meters` | stock page 6 | CPU/GPU VU meters, memory/temp/disk/net faders |
| `neos_hifi_player` | left strip | title, real L/R output level meters (dBFS), progress, transport |
| `neos_nixie` | stock page 2 | millennium segment clock in the glass capsule, silver-blue glow |
| `neos_cassette` | whole screen, no pill | the cassette fills the screen: handwritten title and artist, packs follow progress, transport and time printed on the shell |
| `neos_photo` | left strip or `full=1` | your picture (`src=`), like the stock custom-image page |
