# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Personal project: drive the 480×360 screen of an AULA Neos75 keyboard from the PC (live HTML pages in the screen
knob's cycle, after the 6 stock pages), customize both knobs, and replace the vendor's Aula Hub for the keyboard
itself. Docs and UI text are Chinese; reply to the user in Chinese.

## Commands (Windows, from the repo root)

```bash
# SDK / service tests (sdk/.venv; setup.ps1 creates it, or: pip install -e "sdk[widgets,check,test]")
sdk/.venv/Scripts/python -m pytest -q sdk/tests
sdk/.venv/Scripts/python -m pytest -q sdk/tests/test_via.py::test_reads      # one test

# Firmware: build the patch, then the Unicorn-emulator tests (firmware/.venv; slow, xdist on all cores)
firmware/.venv/Scripts/python -B firmware/make_patch.py
firmware/.venv/Scripts/python -B -m pytest -c firmware/pytest.ini firmware/tests -q
firmware/.venv/Scripts/python -B -m pytest -c firmware/pytest.ini firmware/tests/test_knob_runtime.py -n 0   # debug one file

# SDK against the real patched firmware code (emulated); needs firmware/.venv with the SDK installed in it
firmware/.venv/Scripts/python -m pytest -q tests/integration

# Desktop app (Electron, app/)
cd app && npm start           # real device
cd app && npm run fake        # fake screen, no keyboard
cd app && npm run shots       # GUI screenshots into app/screenshots

# Installer -> app/dist/Neos75Screen-Setup-<version>.exe (bundles Python, the SDK and the pages)
sdk/.venv/Scripts/python tools/build_app.py

# The running service (always --json for agents; exit 3 = service not running)
sdk/.venv/Scripts/neos75ctl status --json
```

`firmware/make_patch.py` needs the stock-firmware backup (`NEOS75_BACKUP`, default `~/Tools/aula_neos75_backup`)
and the Arm GCC in `firmware/toolchain/`. See `firmware/README.md`. There is no linter configured.

## Architecture

Two chips, two protocols:

- **Screen MCU** (USB HID `5542:0001`, usage page FF60, 64-byte reports). We run a **patched stock firmware**:
  `firmware/make_patch.py` hooks 7 stock functions with trampolines into `firmware/runtime.c`, a freestanding
  Thumb-2 runtime placed after the stock code. It runs in RAM only and doesn't write config flash. The runtime
  speaks the realtime protocol (`A5 3B …`: HELLO/capabilities, JPEG/RGB565 frames, region frames of changed
  16×16 tiles, a page cache, host pages 7–14 in the knob cycle, knob events and knob takeover). Spec:
  `docs/PROTOCOL.md`, `docs/DESIGN.md`, `docs/FIRMWARE.md`. `firmware/tests/emulator.py` runs the built
  machine code in Unicorn (GUI/JPEG/USB are stubs).
- **Keyboard MCU** (wired `1EA7:4884`, 2.4G dongle `6D66:8572`; raw HID FF60/61, 32 bytes). A vendor QMK fork
  speaking VIA protocol 12 and Vial 6. `sdk/src/neos75screen/via.py` builds on `qmk-via-api`. Our part is
  `SafeApi`, an allow-list proxy, plus the vendor-specific bits (system settings on custom channel 0, battery via
  `0x51`, macros written as a whole buffer with `0x0F`, advanced keys = Vial tap dance via raw `FE 0D` packets).
  The layout comes from the official VIA definition (`design/official/AULA-Neos75_via_v3.json`), parsed with
  `pykle-serial`.

Python package `sdk/src/neos75screen`:
- `transport.py` → `guard.py` → device. **Every** outgoing screen report passes the allow-list in `guard.py`.
  `protocol.py` handles encoding and decoding. `screen.py` (`Screen`) is the high-level API: HELLO, frames,
  streaming, events.
- `service/`: `neos75d` is the single owner of the screen's HID device. `engine.py` runs the widget runner
  thread, the knob mapper and `KeyboardControl` (`service/keyboard.py`). `server.py` is a localhost HTTP API
  (`service/API.md`); its port and token are in `%APPDATA%\neos75screen\service.json`, sent in the
  `X-Neos75-Token` header. `cli.py` is `neos75ctl`.
- `live/`: `runner.py` renders each page in headless Chrome/Edge through `cdp.py`, its own small DevTools
  client (deliberately not Playwright). It feeds data via `window.neos75Update(data)` and pushes changed regions
  on a separate sender thread. `library.py` resolves the page folders.
- `knobmap.py` and `knobpress.py` remap the knobs on the PC. Screen-knob turns arrive as firmware takeover
  events. Presses and the volume knob are caught by a `WH_KEYBOARD_LL` hook that is filtered by device via
  Raw Input. Actions are sent with `SendInput`.

The app (`app/main.js`) finds or spawns the service (`python -m neos75screen.service`; when installed, the bundled
`resources/python`) and talks to it over the HTTP API. `app/renderer/` is plain JS: a sidebar of views; `keyboard.js`
holds the keyboard views (keys, knobs incl. PC-side knob actions, macros, advanced keys, lighting, performance,
device), `app.js` the router, screen page, settings and log. `--fake` also simulates the keyboard
(`FakeVia` in `sdk/tests/fake_device.py`). `keyboard.js` must load before
`app.js`. Pages are 480×360 HTML in `design/neos` (official Néos style), `design/widgets`, and user pages in
`design/custom`. They're written against the official artwork and MiSans. See `design/neos/README.md` and the
skill `.claude/skills/neos75-screen/`.

Tests: `sdk/tests/fake_device.py` is a protocol-level fake screen (also the app's `--fake` mode). `test_via.py`
fakes the qmk-via-api object.

## Rules that are not obvious

- Never send to the screen: `34 xx`, `13 xx`, `FE`, the stock wallpaper upload, or anything `guard.py`
  rejects. Avoid stock `A5 39` in tests. After a screen reboot, wait about 20 s before any HID traffic.
- Keyboard: only what `via.SafeApi` allows. Never send bootloader jump, EEPROM/keymap/macro reset, keymap
  buffer writes, other Vial commands (unlock, QMK settings; only `FE 0D` tap dance count/get/set), vendor `0x53`,
  or `0x1D`. Never write reboot/erase keycodes (`keycodes.FORBIDDEN`: QK_BOOT
  `0x7C00–0x7C03`, LCD reset `0x7E22`), except when restoring the keyboard's own backup. Aula Hub's
  post-keycode `15 01 00…` is a bug: it writes encoder 0 ccw on layer 1. Don't copy it.
- Only one process may hold the screen device. Go through the service (`neos75ctl`) instead of opening HID
  directly while it runs.
- Flashing goes only through the `hwtest/run_v3*.py` scripts, which check, back up, write, verify and restore.
  Batch firmware changes so the screen is flashed rarely; each flash erases flash once.
- Never commit vendor firmware, dumps, decompiled output (`firmware/analysis`, `*.bin`), the official assets
  (`design/official`, from `tools/ui_pack.py`) or `app/dist`. All of these are git-ignored. The GitHub repo is
  public. Release installers include the official artwork; the owner accepts that.
- Prefer maintained open-source libraries over hand-rolled code: qmk-via-api, pykle-serial, websocket-client,
  `binascii.crc_hqx`. Keep our own front end and visual style.
- Line endings are mixed (some files CRLF, most LF). Keep each file's existing endings.
