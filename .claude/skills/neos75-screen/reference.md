# Writing Neos75 screen pages: reference

## The data a page gets (`DATA`, from `design/widgets/data.js`)

The runner calls `window.neos75Update(data)` once per frame; `data.js` merges it into `DATA`, derives the
clock fields from `now`, and calls `window.render()`. Before the first update (and in previews) the
fields hold sample values; most can be overridden in a preview URL (`?cpu=90&title=...`).

| field | meaning |
|---|---|
| `now` | ms since epoch (local clock is derived from it) |
| `time` `"10:42:18"`, `hms` `[10,42,18]`, `weekday` `"Saturday"`, `day` `"07"`, `month` `"November"`, `year` | local date/time (English names; `WEEKDAYS`, `MONTHS` arrays exist) |
| `next`, `nextAt` | free line, live: "Week 45" / "Day 312 of 2026" |
| `cpu`, `gpu`, `mem`, `disk` | percent (numbers) |
| `temp` | GPU temperature °C (may be null) |
| `net` | download MB/s |
| `title`, `artist`, `album`, `app`, `playing`, `has_media`, `pos`, `dur` | Windows media session (seconds); `mmss(s)` helper formats |
| `lvl_l`, `lvl_r` | output peak level 0..1 (audio meters); only with `<body data-audio>` |
| `spec` | 24 spectrum bands 0..1; only with `<body data-audio>` |
| `kb_battery` (null = not reported), `kb_charging`, `kb_link` (1-3 Bluetooth, 4 2.4G, 5 wired) | keyboard status |
| `page_label` | "Page 9": this page's place in the knob cycle |

## Skeleton

```html
<!doctype html>
<html><head><meta charset="utf-8">
<meta name="neos75:title" content="天气">
<meta name="neos75:description" content="今天的天气与温度">
<meta name="neos75:options" content='[{"key":"city","type":"text","label":"城市","default":"上海"}]'>
<link rel="stylesheet" href="../neos/neos.css"></head>
<body data-status="off" data-fps="1">
<img class="strip" src="../official/jpg/page5_bg.jpg">            <!-- official left strip -->
<div class="abs big" style="left:140px;top:120px;font-size:96px" id="v"></div>
<script src="../widgets/data.js"></script>
<script>
const OPT = new URLSearchParams(location.search);
window.render = () => { v.textContent = DATA.time.slice(0, 5); };
render();
</script>
<script src="../neos/status.js"></script></body></html>
```

Pages in `design/custom/` resolve `../neos/`, `../widgets/` and `../official/` like the stock Néos pages.
A page may fetch nothing from the network at runtime except fonts (`neos.css` loads MiSans from a CDN);
there is no server: pages are `file://` URLs.

## Layouts on the official artwork

- Full stock page: `<img class="bg" src="../official/jpg/pageN_bg.jpg">` and draw in the space the page
  leaves (see `neos_clock`, `neos_system`, `neos_player`); keeps the stock status bar (B / Battery /
  Bluetooth / wired / 2.4G), filled live by `status.js`.
- Canvas (like stock page 5): the left strip `page5_bg.jpg` at (19,18), content at x 130..460,
  `<body data-status="off">` (see the template, `neos_photo`, `neos_hifi_player`).
- `data-pill="x,y"` on `<body>` moves the page-number pill; `pill=0`-style options can hide it.

## Options schema (`neos75:options`)

`[{key, type, label, default, ...}]` with `type`: `text`, `number`, `file` (`accept`: extensions; the app
shows a file picker), `select` (`choices`: `[[value, label], ...]`), `bool` (`on` / `off`: values written
when checked / unchecked; `""` leaves the key out). The desktop app builds its option form from this.

## Checklist

- `neos75ctl check NAME -o shot.png --json` → `ok: true`, no `problems`, look at the PNG.
- Text fits (Chinese and long titles: `text-overflow: ellipsis`), nothing beyond 480x360.
- Thin weights (200-400), white on black, hairline borders `#3a3a3d`, pill fill `#131419`.
- `data-fps` only as high as the motion needs; `data-quality` (30-95) only if the default 88 is wrong for the page.
