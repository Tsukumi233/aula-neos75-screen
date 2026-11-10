# Design: realtime display for the AULA Neos75 screen

## Goal

A PC program pushes images to the keyboard's 320 x 320 screen in real time (system monitor, clock,
media, simple animation), alongside the official Aula Hub, which keeps managing keys, lighting and
performance on the separate keyboard MCU. Only the screen MCU's application firmware is patched; all stock
screen functions keep working.

Expected performance (estimates, not measured): about 62 KB/s over 64-byte HID, so a 10 KiB JPEG frame
reaches about 5 fps and a 20 KiB frame about 3 fps, minus decode and redraw time.

## Hardware and firmware facts (verified)

- Screen MCU: UB32F341 (Cortex-M, Thumb-2). Internal flash 512 KB: bootloader 0x0000-0x7FFF (read-only via
  DFU, refuses its own erase), app 0x8000-0x7EFFF, config 0x7F000. External SPI NOR XIP at 0x70000000,
  external RAM at 0x80000000.
- Installed app build: `V1.0.3 Sep  6 2026 23:18:16`; backup `app_8000_7F000.bin`, SHA-256
  `66679ff2cb06ab5a5e47673f679e14e68bf2082fb6401b3808ca58d7f2e1a40c`, content up to offset 0x42ACC (the
  last 16 bytes are a DFU suffix), then 0xFF. It is a different build from the cloud V3.0.3 image, so
  cloud-image addresses are only hints.
- Bootloader (decompiled from the dump): stays in DFU when GPIO 0x40041800 pin 6 is low, when the app's
  initial SP top byte is not 0x20, when RAM 0x20000000 == 0xA5A5A5A5 (app request), or when the
  "unconfirmed" flag at 0x7F00 == 0x55AAAA55 and RAM 0x20000004 != 0x5A5A5A5A. Every DFU erase sets the
  flag; leaving DFU sets RAM 0x20000004 = 0x5A5A5A5A, so a new app gets one trial boot. The app's init step
  `mcu_run_success` erases page 0x7000 and clears the flag. DFU idles 180 s, then jumps to the app.
- Write path verified on the device: ERASE + SET_ADDRESS + DNLOAD per 4 KB sector, read-back identical,
  flag FFFFFFFF -> 55AAAA55 -> FFFFFFFF.
- Stock RTOS tasks (cloud build): main_thread, vTask_Uart, vtask_gdb (shell), vtask_hid (64-byte HID
  receive loop, stack 0x500, calls the BA upload handler directly), vtast_appdog, Platform1ms, plus an LVGL
  task. Display path: hardware JPEG decoder (`isr_jpeg`, semaphore-guarded) -> RGB565 buffer -> LVGL
  image object (`lv_img_set_src`-like) -> RGB scan-out of a 480 x 360 canvas on an ST7701-class panel.

## Firmware patch architecture

1. **Receive (vtask_hid context).** New BA stream types `5A`/`5B` follow the stock BA state machine, but
   the 4 KB block commit copies into a RAM receive buffer instead of erasing external flash, and the
   stream finish marks the buffer READY instead of writing the flash header. No LVGL calls, no heap
   allocation and no JPEG decoding in this context.
2. **Display (LVGL task context).** A hook that already runs periodically in the GUI task checks for a
   READY buffer, validates the stream header, runs the hardware JPEG decode (or copies RGB565) into a
   persistent RGB565 image buffer, updates the image descriptor, invalidates the image object, and sends
   the frame-done event. The hook does nothing when realtime mode is off.
3. **Buffers.** Compressed input buffers come from RAM the stock firmware already reserves for JPEG input
   (cloud build: 0x800D4200, 0x2A304 bytes), split into one or two buffers; stock decoders that share it
   are kept off while realtime mode is active. The RGB output buffer is allocated once with the GUI
   allocator at ENTER (or reused from the stock static-image page) and never freed while active.
4. **Control (A5 `3B`).** HELLO/ENTER/LEAVE/STATUS handled in the A5 dispatcher (an unused TBH slot);
   ENTER/LEAVE only set flags that the GUI hook acts on, so page switching also happens in the GUI task.
   Nothing here writes flash.
5. **No 3-second gate, no flash writes.** The realtime path never calls the external-flash erase/program
   routines, the config save routines, or the stock upload progress page.
6. **Code placement.** New code is appended after the installed image's end in the free app area; existing
   addresses do not move. Hooks are minimal branch rewrites, each checked against the expected original
   bytes. The patch generator refuses an input whose SHA-256 differs from the backup above.

## Trial-boot flag handling

- **dev build:** the patch skips the `mcu_run_success` flag clear. The flag stays set after flashing, so
  every power-up enters DFU for up to 180 s and then starts the app; any crash or hang that leads to a
  reset lands in DFU, ready to re-flash. Used while testing on the device.
- **release build:** the flag is cleared only after the app has run stably (for example 60 s of uptime
  after init), not at init, so a crash in the first minute still lands in DFU.
- The flag lives in internal flash page 0x7000, inside the bootloader's region; only the stock erase
  routine the app already uses may touch it.

## Flashing and recovery

- `tools/flash.py` writes an app image to 0x8000.. (4 KB sectors, only the sectors the image covers,
  never below 0x8000, never the 0x7F000 config page, never external flash), reads it back, and only then
  leaves DFU. `--restore` writes the original backup the same way. It refuses images over 0x77000 bytes or
  with an initial SP outside 0x20xxxxxx.
- Recovery when a patched app misbehaves: the device lands in DFU (dev build, or release within the first
  minute) or the stock `13 02` still works; flash the backup. Last resort: GPIO pin 6 low at power-up
  (board location unknown).
- Every first flash of a new build needs the user's explicit approval.

## Repository layout

```
docs/      PROTOCOL.md (host-visible protocol), DESIGN.md (this file), FIRMWARE.md (patch internals)
firmware/  patch generator sources; build/ and analysis/ are git-ignored (vendor-derived)
sdk/       Python package `neos75screen`, examples, tests
tools/     enter_dfu.py, dfu_probe.py, flash.py, ghidra scripts
```

Vendor firmware, dumps and anything decompiled from them are never committed.
