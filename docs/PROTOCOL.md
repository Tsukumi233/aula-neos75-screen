# Neos75 Screen Realtime Protocol, v1

Host-visible protocol between the PC SDK and the patched screen firmware. The firmware track and the
SDK track both implement exactly this document. Anything marked **TBD (firmware)** is decided by the
firmware track from what the installed build makes practical; the SDK keeps those values in one
constants module so they can be changed in one place.

## 1. Transport

- USB HID, the screen's existing interface: VID `0x5542`, PID `0x0001`, usage page `0xFF60`, usage `0x61`.
- Every report is 64 bytes, report ID 0. On Windows a write is 65 bytes (`00` + 64). Short payloads are
  zero-padded to 64 bytes.
- The screen is a separate USB device behind the keyboard's internal hub. It is reachable only when the
  keyboard is connected by cable (verified for 2.4G, see 1.1; Bluetooth not checked).
- The official Aula Hub may hold the same device open. Concurrent use corrupts multi-packet transfers, so
  the SDK must not stream while the Hub uploads a wallpaper. The SDK opens the device in shared mode.

### 1.1 No screen over 2.4G (checked 2026-11-09)

- **What the PC sees.** In 2.4G mode, with no cable, Windows lists only the dongle, `6D66:8572` "AULA-Neos75 dongle".
  The screen (`5542:0001`) and the wired keyboard (`1EA7:4884`) are gone.
  - Dongle interfaces (report lengths from `HidP_GetCaps`, read only):
    - MI_00: keyboard, with a 65-byte feature report.
    - MI_01: vendor `FF60/0x61`, 33-byte input and output reports (32 bytes plus the report ID).
    - MI_02–04: keyboard, mouse, consumer and system collections.
    - MI_05: vendor `FF31/0x74`, a 65-byte input report only.
  - Nothing was written to any dongle interface.
- **Aula Hub has no wireless screen path** (static analysis of its `app.asar`).
  - It recognises the screen only as `5542:0001` "AULA-Neos75 Screen". Every screen command (wallpaper, GIF,
    time, brightness) is sent there.
  - On the dongle it drops the screen page from its menu (`deviceRoute` without `ybScreenSet` when
    `wireless`).
  - MI_01 carries a VIA-style keyboard protocol in 32-byte reports (keymap, lighting, settings, version),
    with no command that wraps A5 or BA.
  - For the F75 Max the Hub states outright that its screen can only be changed over the cable.
- **The only conceivable route** is PC → dongle → RF → keyboard MCU → UART → screen MCU.
  - The stock screen firmware does parse A5 and BA from that UART (`0x35094`, `0x31E6C`). Our patch takes
    realtime BA from HID source `0x0C` only.
  - The keyboard MCU would have to relay host data to the screen. Nothing shows that it does, and its firmware
    is not ours to change.
  - The link is slow. vTask_Uart (`0x3E8B8`) opens it with baud index 10 (`0x3E8F6`), which is 115200 in the table
    at `0x42E6C`. That is about 11 KB/s raw, against about 58 KB/s over USB. One 480x360 JPEG (20–30 KB)
    would take 2–3 s, so live pages or video over 2.4G are out of reach even with a relay.
- **Knob capture over 2.4G.** The volume knob's media keys would come from the dongle (`6D66:8572`, consumer
  page). It is not in `knobpress.DEFAULT_DEVICES` and is untested.

## 2. Stock commands the SDK may use (unchanged by the patch)

### 2.1 A5 command frame

```
A5 | cmd | 00 | L | 00 | data[L-1] | cs        then zero padding to 64 bytes
cs = (sum of all bytes after A5, up to and including the last data byte) & 0xFF ^ 0xFF
L  = len(data) + 1
```

Read form for queries: `A5 41 00 01 kind cs` (no data bytes; the kind sits where data would be and is
counted in the checksum). Reply: byte 0 `A5`, byte 1 `41`, byte 4 = kind, data from byte 5.

| Command | Meaning | Notes |
|---|---|---|
| `41` kind `14` | combined info: `<HHHH` vid, pid, width, height, then screenType, supportWinUsb, supportJpg | read-only |
| `41` kind `10` / `11` | image / GIF space, `<I` bytes | read-only |
| `39` data `01` / `02` | next / previous page | the firmware saves the page to internal flash about 2 s later |
| `39` data `A1` / `A2` | brightness up / down | also saved to flash |
| `38` data `[1, yearHi, yearLo, month, day, h, m, s, weekday]` | time sync | no flash write found |

### 2.2 11-class query (read-only)

64-byte report `11 sub 00 00 00 00 crcLo crcHi` + zeros. CRC16-CCITT (poly 0x1021, init 0xFFFF, MSB first)
over all 64 bytes with bytes 6..7 set to zero, stored little-endian in bytes 6..7.
Known vectors: `11 01` -> `F9 2F`; `11 02` -> `FD 1B`; `13 02` -> `78 B7`.
Reply text starts at byte 8: `11 01` -> model `ZG06_2.31_V2.0`, `11 02` -> build string
(installed stock build: `V1.0.3 Sep  6 2026 23:18:16`).

### 2.3 Commands the SDK must never send

`34 xx` (factory reset / bootloader / reset), any `13 xx` (reboot, DFU, clear custom image), `FE`, and
the stock wallpaper upload (`BA 06 AA B0 ...` / `BA 06 AA FC ...`, which erases external flash) unless the
caller explicitly asks for a persistent wallpaper. Enforce this with an allow-list in the transport layer.

## 3. Realtime control: A5 command `3B`

Request: `A5 3B 00 L 00 sub [args] cs`. Response: a report with byte 0 `A5`, byte 1 `3B`, byte 5 = `sub`
echoed, then the response fields (little-endian). Requests not understood return byte 6 = `0xEE`.

| sub | Name | Args | Response fields after `sub` |
|---|---|---|---|
| `01` | HELLO | none | `magic 'N','S'` (2 B), `proto_version` u8 = 1, `patch_version` u16, `canvas_w` u16, `canvas_h` u16, `image_w` u16, `image_h` u16, `formats` u8 (bit0 JPEG baseline, bit1 RGB565), `max_frame_bytes` u32, `buffers` u8, `flags` u8 |
| `02` | ENTER | none | `status` u8 (0 = ok). Switches to the realtime page; stock pages stop using the shared buffers. |
| `03` | LEAVE | none | `status` u8. Returns to the page shown before ENTER. |
| `04` | STATUS | none | `state` u8, `frames_ok` u32, `frames_err` u32, `last_error` u8, `last_decode_ms` u16 |

The patch must not save pages or brightness to flash because of ENTER/LEAVE.

For firmware patch v2, `proto_version = 1`, `patch_version = 2`, and both
`image_w`/`image_h` and `canvas_w`/`canvas_h` are **480 x 360**. Image dimensions report the maximum
accepted frame size. A frame must have nonzero, even width W <= 480 and height H <= 360; dimensions
outside these limits return frame status `03`.

Frames are displayed without scaling at **x = (480 - W) / 2, y = (360 - H) / 2**, with black outside
the image. These coordinates are integers because both the canvas and accepted dimensions are even.
A full-canvas frame starts at (0, 0); a 320 x 320 frame starts at (80, 20). There are no coordinate
fields in the protocol. The firmware may briefly show the black background while updating its single
RGB buffer; frame-done means the complete frame has been handed to the GUI.

`max_frame_bytes` remains **172804**, including the stream header, and `buffers = 1`. A 480 x 360
RGB565 stream requires 345609 bytes and exceeds this limit; use baseline JPEG for full-canvas frames.
JPEG streams have a 13-byte header, leaving at most 172791 bytes for the JPEG itself.

## 4. Realtime frame data: BA transport with new stream types

The stock upload transport is reused unchanged except for the type byte of the first packet:

```
first packet : BA 06 AA <type> len32_BE            type 5A = RAM JPEG frame, 5B = RAM RGB565 frame
data packets : BA n data[n]                         n <= 62, zero-padded to 64 bytes
```

- `len32_BE` = total length of the stream below.
- Stream (same as the stock upload stream, frame count always 1, delay 0):
  - JPEG (`5A`): `55 42 | W_be16 | H_be16 | 00 01 | 00 | jpeg_len_be32 | jpeg`
  - RGB565 (`5B`): `57 51 | W_be16 | H_be16 | 00 01 | 00 | pixels (RGB565 little-endian, row-major)`
- Within the stream, the last data packet of every full 4096-byte block carries the zlib CRC-32 of that
  block (little-endian, 4 bytes, after the data) and the host waits for an ACK report: byte 0 `BA`,
  byte 6 `FE`. The tail shorter than 4096 bytes has no CRC and no ACK. (Identical to stock.)
- JPEG constraints: baseline, 8-bit, YCbCr 4:4:4, 4:2:2 or 4:2:0 (the hardware decoder's modes), even
  width and height, at most `image_w` x `image_h`, total stream at most `max_frame_bytes`. Strip APPn
  segments other than APP0.
- Frames are not written to flash. The device does not wait 3 s before showing them.

### 4.1 Frame-done event

When a realtime frame has been decoded and handed to the display (or failed), the device sends one report:

```
A5 3B 00 L 00 10 status seq_lo seq_hi decode_ms_lo decode_ms_hi cs
status: 01 shown, 02 decode error, 03 too large / bad header, 04 dropped (busy), 05 not in realtime mode
seq   : device frame counter (u16, wraps)
```

### 4.2 Flow control

- v1: the host sends the next frame only after the frame-done event of the previous one, or after a
  timeout (default 2 s), then calls STATUS.
- `buffers` in HELLO tells how many frames the device can hold (1 or 2). With 2, the host may send frame
  N+1 while frame N is decoding (pipelining). A frame arriving when no buffer is free gets status `04`.
- A new stream start while the previous stream is still being received replaces it without an event (the
  host abandoned it). Every frame-done after a start therefore belongs to that start. If reports are lost
  because the device's input queue overflowed, a stream being received ends with `04`; with nothing being
  received there is no event (patch 3, build after `c7d2a4c1`).

## 5. Knob and visibility (proto_version 2)

The keyboard's knob switches the screen's stock pages; the keyboard MCU tells the screen MCU over the
internal link and the screen firmware switches pages. The realtime page is part of that cycle (user
decision, 2026-11-07).

### 5.1 Behaviour

- **The realtime page is a virtual page in the knob cycle**, placed after the last stock page and before
  the first: `... -> last stock page -> REALTIME -> first stock page -> ...`, and the reverse for the other
  direction. **TBD (firmware):** the exact stock page order, read from the installed build.
- **Armed / disarmed.** The slot is *armed* while a host session is alive: after ENTER, until LEAVE or
  until no host activity (any `A5 3B` request or BA realtime packet) for `host_timeout_ms` (HELLO field,
  default 10000). When disarmed the knob skips the slot, so a user without the PC app never lands on an
  empty page. If the slot is disarmed while visible (timeout), the screen returns to the stock page that
  was shown before the realtime page.
- **Knob while realtime is visible:** the screen switches to the neighbouring stock page exactly as stock
  would; the session stays armed. Frames that arrive while the slot is hidden get status `05` and are
  dropped (the firmware must not touch shared buffers then).
- **Knob back onto the slot:** realtime becomes visible again. The previous frame is not guaranteed to
  survive (its buffer may have been reused by stock pages), so the screen shows black until the host
  sends a frame; the host is told through the visibility event and should resend at once.
- ENTER from the host always shows the realtime page (as before). LEAVE disarms; if the realtime page is
  visible the screen returns to the stock page shown before it, and if it is hidden the current stock page
  stays.
- **Flash:** moving onto or off the realtime slot must not write flash because of the realtime slot. The
  realtime slot is never saved as the current page; after a reboot the screen starts on a stock page.
  Stock pages selected with the knob keep their stock saving behaviour (stock writes the page about 2 s
  after a knob switch); that is the user's own action and is allowed.
- Keeping the stock knob behaviour outside realtime mode unchanged is a hard requirement.

### 5.2 Device-to-host events (unsolicited reports)

```
A5 3B 00 L 00 20 visible reason cs        visibility changed
    visible: 01 shown, 00 hidden
    reason : 01 knob, 02 host ENTER/LEAVE, 03 host timeout
A5 3B 00 L 00 21 dir [taken] cs           knob turned (sent whenever the knob is turned while armed)
    dir    : 01 next (clockwise), FF previous (counter-clockwise)
    taken  : only with takeover (5.3g): 01, the turn moved no page. Absent (L = 03) for an ordinary turn.
```

A knob press does **not** reach the screen MCU (verified on hardware 2026-11-07: the keyboard sends it to
the PC as its own key, Calculator by default), so the firmware never sends `21 00`. Aula Hub cannot remap it.
On Windows the SDK can pick the press up on the PC instead: `Screen.capture_knob_press()` installs a low-level
keyboard hook for VK_LAUNCH_APP2 (verified: the press arrives as VK 0xB7) and reports `KnobEvent(0)`; by
default it swallows the key, so Calculator does not open while the app runs.

### 5.3 HELLO and STATUS additions

- HELLO gains, after `flags`: `host_timeout_ms` u16. `proto_version` becomes **2**; a v1 host still
  parses the leading fields.
- STATUS gains, after `last_decode_ms`: `visible` u8, `armed` u8.

### 5.3a Knob step from the PC

Firmware with HELLO `flags` bit 0 (patch 3 dev builds; release builds from `2710bdd4`) accepts `A5 3B 00 03 00 30 dir cs` (`dir` 01 next, FF previous). The
step enters the screen's stock key queue exactly as the keyboard's UART `A5 39 01/02` does, so everything
after the queue (GUI read callback, slot logic, stock page code, events, the stock page save about 2 s after a
step that settles on a stock page) behaves as for a physical turn. Reply: byte 5 `30`, byte 6 `00` (queued) or
`EE` (bad `dir`). Older release builds clear the flag and answer `EE`. Used by the desktop app, `neos75ctl knob
next|prev` and unattended hardware tests (`hwtest/knob_auto.py`); the SDK exposes it as `Screen.inject_knob(+1/-1)`.

### 5.3b Keyboard status (HELLO `flags` bit 1)

`A5 3B 00 02 00 05 cs` returns, after the echo `05`, the 26 bytes the stock `A5 35` handler (0x35220) last
copied from the keyboard to RAM 0x20004660; all zero until the keyboard reports after a screen boot. The
stock status bar draws three of them (updaters 0x3370c, 0x337b0):

| Offset | Meaning | Stock drawing |
|---|---|---|
| 13 | mode; 2 = charging | dot under "B" (195,55): grey 0x808080, green 0x80FF2B while charging |
| 14 | battery percent | label `%d%%` centred in 56×19 at (238,50) |
| 16 | connection: 1–3 Bluetooth channel, 4 2.4G, 5 wired | dots under Bluetooth (350,55), wired (385,55), 2.4G (432,55): red 0xFF0000 on the active one |

Read-only: nothing is written and the stock path is not involved. Both builds. SDK: `Screen.keyboard()`.

### 5.3c Host pages (HELLO `flags` bit 2)

`A5 3B 00 03 00 06 n cs` (`n` 1..8) makes the slot `n` pages of the screen knob's cycle. Reply: `06`, status
`00` (or `EE` for another `n`), current page u8, `n`. Default (and after boot) 1: the previous behaviour.

- Inside the visible slot a knob step moves to the next/previous page and the slot stays visible: event
  `A5 3B … 22 page count` (0-based page). The last frame stays up until the host sends the new page's frame.
- A step past the last page (clockwise) or before page 0 (counter-clockwise) leaves for the stock neighbour
  exactly as before (`20 00 01`).
- Entering by knob: clockwise from stock page 6 lands on page 0, counter-clockwise from page 1 on page `n-1`;
  the `22` event comes before `20 01 01`, so the host can send the right page's frame first. Host ENTER keeps
  the current page.
- STATUS appends `page` u8, `pages` u8 after `armed`.

Both builds. SDK: `Screen.set_pages(n)`, `Screen.on_page`, `PageEvent`, `Screen.page`.

### 5.3d Region frames (HELLO `flags` bit 3)

A BA stream of type `5A` whose data starts with `'U','R'` replaces part of the shown frame:
`'U' 'R' w u16 h u16 x u16 y u16 jpeg_len u32` (big-endian, 14 bytes), then a baseline JPEG of w x h.
w and h are multiples of 16 (whole JPEG MCUs), x and y even, `w*h <= 86400`, the rectangle inside 480x360.
The device decodes beside the canvas (the borrowed pool's last 172800 bytes), copies the rows into the shown
frame and has LVGL redraw only that rectangle (`lv_obj_invalidate_area`). Frame-done as usual; status `06`
when no full 480x360 frame is shown (after ENTER, after the slot was hidden, after a smaller frame): send a
full frame first. Status `03` for bad geometry, `02` for a failed decode (the canvas is left as it was).

Only the change crosses the 64 KB/s HID link: a 320x32 meter is ~3 KB instead of a ~22 KB full frame.
SDK: `Screen.show_region(jpeg, x, y)`; the widget runner sends changed 16x16 tiles as regions by itself.

### 5.3e Smoothness (HELLO `flags` bit 4)

- **5 ms tick while shown.** The firmware's work (input queue, ACKs, frame-done, events) runs in the stock
  10 ms GUI timer; while the slot is shown its period is set to 5 ms (the GUI task loops every ~5 ms), and
  back to 10 ms when it is hidden. Halves the wait for each ACK and frame-done.
- **Pipelined starts.** A start that arrives while the previous frame is complete but not yet shown no longer
  gets `04` busy: the device shows that frame first, then takes the new start (an RGB565 copy that spans
  ticks keeps the start queued). The host may send the next frame without waiting for frame-done.
- **Page cache.** `5A` stream `'U','C', page u8, 0, jpeg_len u32 BE`, then a 480x360 baseline JPEG of at most
  the slot size: stored for host page `page`, not shown (frame-done `01`). Sub `06` replies with the slot size
  (u32 LE after the count): the pool area behind the region scratch (90880 bytes) divided by the page count;
  a new count forgets the cache. When the knob turns to a host page, or enters the slot on one, the device
  decodes that page's cached JPEG at once (CRC-checked: a slot the stock code overwrote while hidden is
  dropped, never shown). A stream still arriving for the previous page ends with frame-done `07`
  (superseded). Region frames are limited to 81920 bytes decoded (40960 pixels) to make room.

Hardware (2026-11-08, before this feature): per frame ~10 ms waiting for the start ACK, 1 ms per report,
~8 ms from the last report to frame-done. Host-only changes (ACK window 2 between region frames, Huffman-
optimized JPEG) took a 0.7 KB region from 23 to 38 fps and a 2.8 KB spectrum from 11 to 17 fps.

### 5.3f Page-tagged frames (HELLO `flags` bit 5)

Byte 8 of a `5A`/`5B` BA start (zero padding before) may name the host page the frame was drawn for:
`1 + page` (1..8; 0 = untagged, as before). When the frame is complete and its tag does not match the page the
device shows, it is dropped with frame-done `07` instead of being shown. This closes the gap between a knob
step on the device and its page event reaching the host (up to ~20 ms): a frame of the page the knob just left,
started in that gap, used to be shown after the cached new page ("the page jumps back, then forward";
hardware 2026-11-08). Cache uploads (`UC`) ignore the tag. SDK: `Screen.frame_page`; the widget runner sets it.

### 5.3g Knob takeover (HELLO `flags` bit 6)

`A5 3B 00 03 00 07 on cs` with `on` 01 takes the screen knob's turns over for the current session: a physical
turn moves no page (stock, host page or slot) and is only reported as `21 dir 01`. `on` 00 returns to stock
paging. Reply `07 status on` (status 00 ok, EE refused: no armed session or `on` > 1). Takeover ends with the
session (LEAVE, host timeout), so a host that dies leaves a knob that pages again within 10 s; a new session
starts without it. A knob step from the PC (5.3a, `30`) still pages under takeover and is then not reported,
so the host can implement "next page" as one of its actions without seeing it as a turn. RAM only, no flash
write. SDK: `Screen.set_knob_takeover(on)` (sent again after every ENTER while wanted), `KnobEvent.taken`;
the knob actions of the service (`knobmap`) switch it on while screen-knob turns are mapped, or while the knob
is held down when only "turn while held" is mapped.

### 5.4 Host behaviour (SDK)

- While a realtime session is open, keep it alive: send STATUS when no frame has been sent for
  `host_timeout_ms / 3`.
- On a hidden event, pause sending frames; on a visible event, send the current frame immediately.
- Expose knob events to applications (callback or iterator), so an app can, for example, switch
  dashboards; the knob still moves between pages, so apps must not assume they keep the screen.

## 6. Versioning

- `proto_version` changes only for host-visible changes. `patch_version` is the firmware patch build.
- An unpatched (stock) screen does not answer `A5 3B 01` with the magic: the SDK must report
  "stock firmware, realtime unavailable" and keep the stock helpers working.
