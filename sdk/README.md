# neos75screen

Python SDK for the AULA Neos75 keyboard screen (USB HID `5542:0001`, usage page `FF60`, usage `61`).
With the patched screen firmware it pushes images to the screen in real time (RAM only, no flash writes)
using the protocol in [`../docs/PROTOCOL.md`](../docs/PROTOCOL.md). On stock firmware it detects that
realtime display is unavailable and keeps the stock helpers (info, page, brightness, clock, wallpaper)
working.

## Screen size

The physical panel is 480 x 360. The SDK never assumes a size: everything is laid out for `image_w x image_h`
from HELLO (`screen.capabilities.image_size`).

| Firmware | HELLO image size | Where frames appear |
|---|---|---|
| patch v2 | 480 x 360 | whole panel; smaller even-sized frames are centred on black, larger ones are rejected (status 03) |
| patch v1 | 320 x 320 | a 320 x 320 square on the right side of the panel |
| stock | (no HELLO) | realtime unavailable; the stock API and wallpaper are 320 x 320 |

`max_frame_bytes` is 172804, so a full 480 x 360 RGB565 frame (345609 bytes) does not fit: send full-screen
content as JPEG (`show`, `stream`), and RGB565 only for smaller frames (for example 320 x 240).

## Install

Python 3.10 or newer.

```powershell
cd sdk
py -3 -m venv .venv
.venv\Scripts\python -m pip install -e ".[numpy,sysmon,test]"
```

Dependencies: `hidapi` (import name `hid`) and `pillow`. Optional: `numpy` (faster RGB565), `psutil`
(system monitor), `nvidia-ml-py` (GPU load in the monitor, extra `gpu`). Without `hidapi`, Windows falls back
to a built-in ctypes HID transport.

## Quick start

```python
from neos75screen import Screen

with Screen.open() as screen:          # opens the device and sends HELLO
    print(screen.capabilities)         # image size, formats, max frame size, buffers
    if screen.realtime:
        screen.show("photo.jpg")       # fit="contain" letterboxes into image_w x image_h (480 x 360 on v2)
        input("Enter to go back")
# leaving the block sends LEAVE, so the screen returns to the page it showed before
```

Command line (installed as `neos75`):

```
neos75 info                    stock info: model, build, size, image/GIF space (read-only)
neos75 hello                   realtime capabilities, or "stock firmware"
neos75 show PATH [--fit cover] [--quality 85] [--max-bytes N] [--rgb565] [--stay]
neos75 clock [--no-seconds]
neos75 sysmon [--interval 1]
neos75 slideshow DIR [--interval 5] [--once]
neos75 gif PATH [--loops 0]
```

The clock and system monitor lay themselves out for the HELLO size: landscape (480 x 360) puts the time,
weekday, date and a seconds bar on the panel, and the monitor uses two columns (CPU total, per-core bars and
60 s history on the left; RAM, GPU when `nvidia-ml-py` finds one, disk, network and uptime on the right).
Square (320 x 320, v1) gets a single-column version. Slideshow and GIF frames are fitted with `--fit`.

`examples/` has standalone scripts for the same apps. `big_clock.py` and `sysmon_dashboard.py` accept
`--preview` to render a PNG without a device, at 480 x 360 by default or `--size 320x320`
(`sysmon_dashboard.py --view cores` previews the per-core view).

## The knob (protocol v2)

On firmware that reports `proto_version` 2 in HELLO (`screen.capabilities.knob` is True), the keyboard
knob keeps switching screen pages, and the realtime page is one more page in that cycle, between the last
and the first stock page (PROTOCOL.md section 5).

- **Armed slot.** `enter()` (or the first `show`/`stream`) shows the realtime page and arms its slot;
  `leave()` and `close()` disarm it. While armed, the knob can move away from the realtime page and back.
  While disarmed the knob skips it, so nobody lands on an empty page when no PC app runs.
- **Keepalive.** The device disarms the slot after `host_timeout_ms` (HELLO, default 10000) without
  realtime traffic. While a session is open, a background thread sends STATUS when nothing realtime was
  sent for `host_timeout_ms / 3`. It never writes during a BA frame transfer or after `close()`, and it
  stops on `leave()`. `Screen.open(keepalive=False)` turns it off.
- **Visibility.** `screen.visible` and `screen.armed` follow the device's events (and every STATUS
  reply). While the slot is hidden, `stream()` sends nothing and pauses its frame source; when the user
  turns back, it sends the newest frame at once (the device does not keep the old one). A frame answered
  05 because the slot was hidden meanwhile counts as `StreamStats.hidden` and is resent, not raised.
  `show()` on a hidden slot sends nothing and returns `FrameResult(hidden=True)`. If the device disarmed
  the slot after a timeout, the next new frame enters realtime mode again.
- **Events.** `screen.on_knob(callback)` gets `KnobEvent(direction)` (+1 next, -1 previous, 0 press); `screen.on_visibility(callback)` gets `VisibilityEvent(visible, reason)`
  (reason 1 knob, 2 host ENTER/LEAVE, 3 host timeout). Callbacks run on a background thread: keep them
  short and set flags rather than calling `Screen` methods. `for event in screen.events(timeout=None)`
  is the blocking alternative.
- **The two knobs (Windows).** The Neos75 has a screen knob and a volume knob. Screen-knob turns go to the
  screen only (the events above, `knob="screen"`); its press reaches the PC as the Calculator key. The
  volume knob reaches the PC as Volume Up/Down and Mute. `screen.capture_knob()` picks these keys up on the
  PC and reports them as `KnobEvent(direction, knob, source="pc")`: by default the screen knob's press (and
  Calculator no longer opens); with `volume_knob=True` also the volume knob (its turns no longer change the
  volume and its press no longer mutes). Only keys from the Neos75 keyboard are handled (matched by USB
  VID/PID; a mouse's or headset's media keys are untouched), the keyboard's Fn volume shortcuts keep working
  (they take effect on release), and everything ends with `close()`. `neos75 sysmon --knob-press` uses the
  screen knob's press to switch views.
- **Apps.** Turning the knob while the realtime page is visible always moves to a stock page, so apps
  cannot use rotation for themselves. `neos75 sysmon` switches between the overview and a per-core view
  each time you come back onto the realtime page with the knob (or on a knob press with `--knob-press`). The clock, slideshow
  and GIF player simply pause while hidden and resume when you come back.
- **Protocol v1 devices** (`proto_version` 1) have none of this: no events, no keepalive, `visible` and
  `armed` simply mean "entered", and behaviour is unchanged.

```python
from neos75screen import Screen

with Screen.open() as screen:
    screen.on_knob(lambda event: print("knob", event.direction))
    screen.on_visibility(lambda event: print("visible" if event.visible else "hidden", event.reason))
    screen.stream(frames())        # pauses while the knob shows a stock page
```

## API overview

`Screen.open(transport=None, timeouts=None, keepalive=True)` opens the screen (or wraps a given transport)
and probes it.

| Method | What it does |
|---|---|
| `hello()` / `capabilities` / `realtime` | HELLO; `realtime` is False on stock firmware (no magic within 0.5 s) |
| `enter()`, `leave()`, `status()` | realtime page on/off and slot armed/disarmed (no flash write); device counters (v2: plus `visible`, `armed`) |
| `visible`, `armed` | realtime page on screen; slot in the knob cycle (v1: both mean "entered") |
| `on_knob(cb)`, `on_visibility(cb)`, `events(timeout=None)` | knob and visibility events (v2) |
| `show(image, fit, quality, max_bytes, subsampling)` | PIL image, numpy array or path; fitted to `image_w x image_h` (contain: letterbox, cover: crop, stretch), baseline JPEG |
| `show_jpeg(data)` | ready JPEG: baseline, 8-bit, 4:4:4/4:2:2/4:2:0, even size; APP1..APP15 stripped |
| `show_rgb565(image or bytes, width, height)` | raw RGB565 little-endian frame (if the device lists the format) |
| `encode(image, ...)` | the JPEG that `show` would send, for pre-encoding |
| `stream(frames, fps=None, ...)` | frames (images or JPEG bytes) with flow control, paused while hidden; returns `StreamStats` |
| `info()`, `version()` | stock read-only queries (`A5 41` kinds 14/10/11, `11 01`/`11 02`) |
| `next_page()`, `prev_page()`, `brightness_up()`, `brightness_down()` | stock controls; **these save to flash** |
| `sync_time(datetime=None)` | stock clock sync (`A5 38`) |
| `upload_wallpaper(image, persistent=True)` | stock 320 x 320 wallpaper upload; **erases external flash** |
| `close(leave=True)` | LEAVE (unless `leave=False`, which keeps the last frame up) and release the device |

`show` and `show_*` return a `FrameResult` (`status`, `seq`, `decode_ms`; on a missing frame-done event
`timed_out` is True and `device_status` holds STATUS; `hidden` is True when the knob hid the slot). Frames
rejected by the device raise `FrameRejected(status)`.

Flow control (`stream`): each frame waits for the previous frame-done event, or 2 s, then STATUS. When HELLO
reports `buffers >= 2`, one extra frame is sent while the previous one decodes. Frames the device drops as
busy are counted in `StreamStats.dropped`; other rejections raise.

Exceptions (all subclass `Neos75Error`): `DeviceNotFound`, `StockFirmware`, `FrameRejected`, `Timeout`,
`ProtocolError` (NACK, error status, unknown request), `ForbiddenPacket` (allow-list).

Every protocol value, including the ones PROTOCOL.md marks TBD, lives in `src/neos75screen/constants.py`.

## Safety

- **Allow-list.** Every write passes `guard.py` before reaching the device: `A5 41` (kinds 14/10/11),
  `A5 39` (01/02/A1/A2 only), `A5 38`, `A5 3B` (HELLO/ENTER/LEAVE/STATUS), `11 01`/`11 02`, and BA packets
  only inside a stream the SDK started (type 5A/5B, or B0 only with `persistent=True`, exactly the
  announced length). `34 xx`, `13 xx`, `FE` and every other command raise `ForbiddenPacket` and are never
  sent.
- **Stock-only mode.** On stock firmware the realtime methods raise `StockFirmware`; nothing realtime is
  sent apart from the HELLO probe (`A5 3B 01`), which stock firmware does not answer with the magic.
- **Aula Hub.** The SDK opens the device shared, so the official Hub can keep running, but concurrent
  multi-packet transfers corrupt each other: do not stream while the Hub uploads a wallpaper.
- **Flash-writing helpers.** `next_page`/`prev_page` (page saved about 2 s later) and
  `brightness_up`/`brightness_down` write the screen's internal flash config. `upload_wallpaper` erases and
  rewrites external flash (one fixed slot, no read-back), so it requires `persistent=True` and issues a
  `FlashWriteWarning`. Realtime frames never touch flash.

## Tests

```powershell
cd sdk
.venv\Scripts\python -m pytest
```

The tests never open a real device. They run the SDK against `tests/fake_device.py` behind
`MockTransport`, which emulates stock firmware (ignores `A5 3B`) and the patched firmware in two profiles,
v2 (480 x 360, centring, oversize frames rejected), v1 (320 x 320 square on the right) and v2-knob
(protocol v2: knob cycle with three stock pages, armed/disarmed slot, host timeout, events, 05 while
hidden): HELLO,
ENTER/LEAVE, STATUS, BA 5A/5B reception with block CRC checks and ACKs, frame-done events, busy and
decode-error statuses. They also check that forbidden packets never reach the transport.
