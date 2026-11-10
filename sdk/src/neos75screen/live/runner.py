"""Live widget runner: renders HTML widgets in headless Chrome (DevTools, live/cdp.py) and pushes frames to the screen.

Each widget is a 480x360 page that exposes `window.neos75Update(data)` (see design/widgets/data.js). Every
`interval` seconds the runner feeds it fresh data (clock, system, now playing, keyboard status, its page
label), takes a JPEG screenshot and sends it if it changed. All widgets stay loaded in their own browser tabs,
so a switch is immediate.

With firmware that has host pages (`capabilities.host_pages`) every widget is its own page in the screen
knob's cycle, turned to like a stock page (the first is "Page 7"); otherwise the widgets share the slot and a
press switches. With `config` (see `library`) the selection is re-read while running: pages can be added,
removed or reordered at any time, and the runner re-registers the page count with the firmware; no flashing.
While the knob has moved the screen to a stock page (slot hidden) nothing is sent; when the slot is shown
again the current frame is sent.

Rendering and sending overlap: the main thread renders (headless Chrome) at each page's own rate and hands the
newest frame to a sender thread, which brings the screen to it (changed tiles as region frames, a full frame
when needed). If the sender is still busy the waiting frame is replaced, so the screen always gets the newest
picture and never a backlog. Page cache uploads wait until no frame is waiting.

A page that has not changed for IDLE_AFTER_S (a paused tape, silent meters) is rendered at most IDLE_FPS times a
second instead of its own rate; the first change, a knob turn or a page switch brings the full rate back. Its
data still arrives with every render, so a change shows within 1 / IDLE_FPS s. While the screen shows a stock
page (our slot hidden) the shown page is not rendered at all, only the page cache is kept fresh; becoming
visible renders at once. The audio spectrum (loopback capture + FFT, ~6 % of a core) runs only while a page
that declares `<body data-audio>` is shown.

`out_dir` instead of a screen writes the frames to files (offline check, no device).
"""
from __future__ import annotations

import contextlib
import io
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Sequence
from pathlib import Path

from PIL import Image, ImageChops

from .. import constants as c
from ..errors import FrameRejected, Neos75Error
from ..imaging import encode_jpeg
from . import cdp, library
from .regions import dirty_regions
from .providers import MediaProvider, SpectrumProvider, SystemProvider

KEYBOARD_INTERVAL = 5.0
CONFIG_CHECK_S = 1.0
CACHE_REFRESH_S = 15.0     # page cache: re-upload a page's picture after this long
CACHE_SPACING_S = 1.0      # at most one cache upload per second (~10 KB, ~0.2 s of link time)
CACHE_QUALITY = 80         # shown only until the live frame arrives; fitted to the device's slot size
STATS_S = 30.0             # log frame/region/byte rates this often
IDLE_AFTER_S = 2.0         # a page unchanged this long ...
IDLE_FPS = 4.0             # ... is rendered at most this often (measured 2026-11-10: a paused tape at 12 fps cost
                           # ~0.28 of a core in Chrome + Python for frames that were never sent)


class _Sender(threading.Thread):
    """Brings the screen to the newest posted frame; uploads page cache pictures when no frame waits."""

    def __init__(self, runner: WidgetRunner) -> None:
        super().__init__(name="neos75-sender", daemon=True)
        self.runner = runner
        self.cv = threading.Condition()
        self.frame: Image.Image | None = None
        self.force = False
        self.page: int | None = None                         # the host page the waiting frame shows
        self.jobs: OrderedDict[int, bytes] = OrderedDict()   # page -> cache JPEG (newest per page)
        self.stopping = False
        self.shown: Image.Image | None = None
        self.failures = 0

    def post(self, frame: Image.Image, force: bool, page: int | None = None) -> None:
        with self.cv:
            self.frame = frame            # replaces a frame that was not sent yet: newest wins
            self.force = self.force or force
            self.page = page
            self.cv.notify()

    def cache(self, page: int, jpeg: bytes) -> None:
        with self.cv:
            self.jobs.pop(page, None)
            self.jobs[page] = jpeg
            self.cv.notify()

    def stop(self) -> None:
        with self.cv:
            self.stopping = True
            self.cv.notify()
        self.join(timeout=5)

    def run(self) -> None:
        r = self.runner
        while True:
            with self.cv:
                while not (self.stopping or self.frame is not None or self.jobs):
                    self.cv.wait()
                if self.stopping:
                    return
                frame, force, page, job = self.frame, self.force, self.page, None
                self.frame, self.force = None, False
                if frame is None:
                    job = self.jobs.popitem(last=False)
            try:
                if frame is not None:
                    if not r._still_on(page):
                        self.shown = None   # the knob moved on: this picture is of the page it left
                        continue
                    r._sending_page = page   # _show checks it between region frames (subclasses keep _show(frame, shown))
                    if getattr(r, "screen", None) is not None:
                        r.screen.frame_page = page   # page-tag firmware drops it if the knob has left the page
                    shown = r._show(frame, None if force else self.shown)
                    if not r._still_on(page):
                        # Sent in the few ms between the knob step and its page event reaching us: the
                        # device may show this picture of the previous page until the next full frame.
                        r.late_frames += 1
                        r.log(f"a frame of {library.page_label(page)} reached the screen after the knob moved")
                        shown = False
                    self.shown = frame if shown else None
                else:
                    r._upload_cache(*job)
                self.failures = 0
            except (Neos75Error, OSError) as exc:
                # A rejected or timed-out frame: show the next one in full. Keep going; back off if it repeats.
                self.failures += 1
                self.shown = None
                r.log(f"send failed ({self.failures}): {exc}")
                time.sleep(min(2.0, 0.1 * self.failures))


class WidgetRunner:
    def __init__(self, pages: Sequence[library.Widget | Path] = (), screen=None, out_dir: Path | None = None,  # noqa: ANN001
                 interval: float = 1.0, quality: int = 88, log=print,  # noqa: ANN001
                 config: Path | None = None) -> None:
        if (screen is None) == (out_dir is None):
            raise ValueError("give either a screen or an out_dir")
        self.config = config
        if config is not None and not pages:
            pages = library.resolve(library.load(config))
        if not pages:
            raise ValueError("no pages")
        self.pages = [p if isinstance(p, library.Widget) else library.Widget(Path(p).resolve()) for p in pages]
        self.screen, self.out_dir = screen, out_dir
        if screen is not None:
            # hardware 2026-11-08: region frames 23 -> 38 fps (0.7 KB), spectrum 11 -> 17 fps with window 2
            screen.ack_window = 2
        self.interval, self.quality, self.log = interval, quality, log
        self.index = 0
        self._wake = threading.Event()          # a knob/page/visibility event: do not wait out the interval
        self._force = threading.Event()
        self._hidden = False                          # the screen shows a stock page, not our slot
        self._audio: set[library.Widget] = set()      # pages that read the spectrum (`<body data-audio>`)
        self._last_frame: Image.Image | None = None   # the last render of the shown page and when it changed
        self._changed_at = 0.0
        self._step = 0                          # press mode: widgets to advance
        self._target: int | None = None         # page mode: the page the knob moved to
        self.frames = 0
        self.keyboard_interval = KEYBOARD_INTERVAL
        self._keyboard: dict = {}
        self._keyboard_at = float("-inf")
        self._fps: dict[library.Widget, float] = {}   # a page's own rate (`<body data-fps>`)
        self._quality: dict[library.Widget, int] = {} # a page's own JPEG quality (`<body data-quality>`)
        self.regions = 0                               # region frames sent
        self.bytes = 0                                 # JPEG bytes sent
        self.late_frames = 0                           # frames that crossed a knob step (see _Sender)
        self._sending_page: int | None = None          # the host page of the frame being sent
        self._sender: _Sender | None = None
        self._cached: dict[int, float] = {}            # page cache: page index -> upload time
        self._cache_at = float("-inf")
        self._warm = True                              # upload every page's picture on the next pass
        self._config_stamp = self._stamp()
        self._config_at = time.monotonic()

    def _keyboard_status(self) -> dict:
        """kb_battery (None until the keyboard reports), kb_charging, kb_link; {} without support (the
        widgets then keep their preview values) and the last good values when a read fails."""
        if self.screen is None or not self.screen.capabilities.telemetry:
            return {}
        now = time.monotonic()
        if now - self._keyboard_at >= self.keyboard_interval:
            self._keyboard_at = now
            try:
                k = self.screen.keyboard()
            except Exception as exc:  # noqa: BLE001 - a missed poll only delays the status bar
                self.log(f"keyboard status: {exc}")
            else:
                self._keyboard = {"kb_battery": k.battery if k.reported else None,
                                  "kb_charging": k.charging, "kb_link": k.link}
        return self._keyboard

    @property
    def paged(self) -> bool:
        """Each widget is a page of the screen knob's cycle (firmware with host pages)."""
        return self.screen is not None and bool(self.screen.capabilities.host_pages)

    # press mode: a press of the screen knob moves to the next widget
    def _on_knob(self, event) -> None:  # noqa: ANN001
        if event.direction == 0 and event.knob == "screen" and not self.paged:
            self._step += 1
            self._wake.set()

    def _on_page(self, event) -> None:  # noqa: ANN001
        self._target = event.page
        self._wake.set()

    def _on_visibility(self, event) -> None:  # noqa: ANN001
        self._hidden = not event.visible
        if event.visible:
            self._force.set()
            self._wake.set()

    def _still_on(self, page: int | None) -> bool:
        """The screen still shows host page `page` (always true without host pages). Screen.page follows the
        device's page events as they arrive, independent of the render loop."""
        return page is None or not self.paged or self.screen.page == page

    def _next_index(self) -> int | None:
        """The widget to show after a knob event, or None if it stays."""
        target, self._target = self._target, None
        step, self._step = self._step, 0
        if target is not None and target < len(self.pages):
            return target if target != self.index else None
        return (self.index + step) % len(self.pages) if step else None

    # --- selection ---------------------------------------------------------------------------------

    def _stamp(self) -> tuple[int, int] | None:
        if self.config is None:
            return None
        try:
            st = self.config.stat()
        except OSError:
            return None
        return st.st_mtime_ns, st.st_size

    def _config_changed(self) -> list[library.Widget] | None:
        """The new page files if the selection file changed (checked once a second); None otherwise. A bad
        file is reported and ignored, so a typo never takes the screen down."""
        if self.config is None or time.monotonic() - self._config_at < CONFIG_CHECK_S:
            return None
        self._config_at = time.monotonic()
        stamp = self._stamp()
        if stamp == self._config_stamp:
            return None
        self._config_stamp = stamp
        try:
            pages = library.resolve(library.load(self.config))
        except (ValueError, OSError) as exc:
            self.log(f"pages not changed: {exc}")
            return None
        return pages if pages != self.pages else None

    def _register(self) -> None:
        """Tell the firmware how many pages there are; it answers with the page it shows."""
        self._cached.clear()   # a new count forgets the device's cache; a new order makes it wrong
        self._warm = True
        if self.paged:
            self.index = min(self.screen.set_pages(len(self.pages)), len(self.pages) - 1)
            self.log(f"{len(self.pages)} page(s) in the knob cycle: "
                     + ", ".join(f"{library.page_label(i)} {p.name}" for i, p in enumerate(self.pages)))

    # --- main loop ---------------------------------------------------------------------------------

    def run(self, seconds: float | None = None) -> None:
        # lvl_l/lvl_r come from the spectrum's loopback capture: no COM peak meter (pycaw/comtypes) polled
        # every 30 ms in the service any more
        system, media = SystemProvider().start(), MediaProvider().start()
        spectrum: SpectrumProvider | None = None      # started only while a page that uses it is shown
        if self.screen is not None:
            self.screen.on_knob(self._on_knob)
            self.screen.on_visibility(self._on_visibility)
            if self.paged:
                self.screen.on_page(self._on_page)
        end = None if seconds is None else time.monotonic() + seconds
        self._sender = _Sender(self)
        self._sender.start()
        stats_at, stats = time.monotonic(), (0, 0, 0)
        try:
            # --process-per-site: the pages (all file://) share one renderer process. Measured with five Néos
            # pages: 1.2 GB with a renderer per tab, 0.55 GB shared; capturing the shown page stays at 17-25 ms.
            # The browser is driven over DevTools directly (live/cdp.py), without Playwright's Node.js driver.
            args = ["--allow-file-access-from-files", "--autoplay-policy=no-user-gesture-required",
                    "--process-per-site"]
            with contextlib.closing(cdp.launch(args)) as browser:
                # Every widget is loaded once, in its own tab, so a switch only changes which tab is captured:
                # no reload, no font wait, and a knob event wakes the loop instead of waiting it out.
                tabs: dict[library.Widget, object] = {}
                self._sync_tabs(browser, tabs)
                self._register()
                while end is None or time.monotonic() < end:
                    started = time.monotonic()
                    self._wake.clear()
                    changed = self._config_changed()
                    if changed is not None:
                        current = self.pages[self.index]
                        self.pages = changed
                        self._sync_tabs(browser, tabs)
                        self._register()
                        if not self.paged:
                            self.index = changed.index(current) if current in changed else 0
                        self._target = None
                        self._force.set()
                    index = self._next_index()
                    if index is not None:
                        self.index = index
                        self._force.set()
                        self.log(f"widget -> {self.pages[self.index].name}")
                    page = tabs[self.pages[self.index]]
                    hidden = self._hidden and self.screen is not None
                    audio = self.pages[self.index] in self._audio and not hidden
                    if audio and spectrum is None:
                        spectrum = SpectrumProvider().start()
                    elif not audio and spectrum is not None:
                        spectrum.stop()
                        spectrum = None
                    data = {"now": time.time() * 1000, **system.snapshot(), **media.snapshot(),
                            **(spectrum.snapshot() if spectrum is not None else {}),
                            **self._keyboard_status(), "page_label": library.page_label(self.index)}
                    if hidden:
                        # a stock page is on the screen: nothing to render; keep the page cache fresh so a knob
                        # turn back still shows the page at once, and wait for the visibility event
                        self._refresh_cache(tabs, data, 1.0)
                        self._wake.wait(max(0.0, 1.0 - (time.monotonic() - started)))
                        continue
                    page.evaluate("d => window.neos75Update && window.neos75Update(d)", data)
                    frame = Image.open(io.BytesIO(page.screenshot())).convert("RGB")
                    force = self._force.is_set()
                    self._force.clear()
                    if force or self._last_frame is None or self._last_frame.size != frame.size \
                            or ImageChops.difference(self._last_frame, frame).getbbox() is not None:
                        self._changed_at = time.monotonic()
                    self._last_frame = frame
                    if self._target is not None:
                        continue   # the knob moved while this was rendered: it shows the page it left
                    self._sender.post(frame, force, self.index if self.paged else None)
                    if time.monotonic() - stats_at >= STATS_S:
                        now = time.monotonic(); dt = now - stats_at
                        f, g, b = self.frames - stats[0], self.regions - stats[1], self.bytes - stats[2]
                        self.log(f"{self.pages[self.index].name}: {f / dt:.1f} full + {g / dt:.1f} region frames/s, "
                                 f"{b / dt / 1000:.1f} KB/s")
                        stats_at, stats = now, (self.frames, self.regions, self.bytes)
                    period = 1.0 / self._fps.get(self.pages[self.index], 1.0 / self.interval)
                    if time.monotonic() - self._changed_at >= IDLE_AFTER_S:
                        period = max(period, 1.0 / IDLE_FPS)      # nothing moves: render less often
                    self._refresh_cache(tabs, data, period)
                    self._wake.wait(max(0.0, period - (time.monotonic() - started)))
        finally:
            if self._sender is not None:
                self._sender.stop()
                self._sender = None
            system.stop()
            media.stop()
            if spectrum is not None:
                spectrum.stop()
            if self.paged:
                try:
                    self.screen.set_pages(1)  # leave the slot as other apps expect it
                except Exception as exc:  # noqa: BLE001
                    self.log(f"reset pages: {exc}")

    def _upload_cache(self, page: int, jpeg: bytes) -> None:
        result = self.screen.cache_page(page, jpeg)
        if result.hidden:
            self._cached.pop(page, None)   # not stored: try again later
        else:
            self.bytes += len(jpeg)

    def _refresh_cache(self, tabs: dict, data: dict, period: float,
                       upload: Callable[[int, bytes], None] | None = None) -> None:
        """Keep the device's page cache (page cache firmware): every page's current picture, so a knob turn
        shows it at once. All pages after (re)registering; then the stalest page, nearest the shown one
        first, at most one per second, or one per CACHE_REFRESH_S while a fast page (meters) is shown."""
        screen = self.screen
        if not (self.paged and screen.capabilities.page_cache and screen.cache_slot_bytes):
            return
        now = time.monotonic()
        spacing = CACHE_SPACING_S if period >= 0.5 else CACHE_REFRESH_S
        if not self._warm and now - self._cache_at < spacing:
            return
        if upload is None:
            upload = self._sender.cache if self._sender is not None else self._upload_cache
        n = len(self.pages)
        due = [i for i in range(n) if now - self._cached.get(i, float("-inf")) >= CACHE_REFRESH_S]
        due.sort(key=lambda i: min((i - self.index) % n, (self.index - i) % n))
        for i in due if self._warm else due[:1]:
            tab = tabs[self.pages[i]]
            tab.evaluate("d => window.neos75Update && window.neos75Update(d)", {**data, "page_label": library.page_label(i)})
            img = Image.open(io.BytesIO(tab.screenshot())).convert("RGB")
            try:
                jpeg = encode_jpeg(img, CACHE_QUALITY, optimize=True, max_bytes=screen.cache_slot_bytes)
                self._cached[i] = now
                upload(i, jpeg)
            except (ValueError, FrameRejected) as exc:
                self._cached.pop(i, None)
                self.log(f"page cache {library.page_label(i)}: {exc}")
        self._warm = False
        self._cache_at = now

    def _sync_tabs(self, browser: cdp.Browser, tabs: dict) -> None:
        """Open a tab for every selected widget that has none, close tabs of widgets no longer selected."""
        for path in [p for p in tabs if p not in self.pages]:
            tabs.pop(path).close()
        for path in self.pages:
            if path not in tabs:
                tabs[path] = browser.new_page()
                self._open(tabs[path], path)
                fps = tabs[path].evaluate("() => parseFloat(document.body.dataset.fps || '0')")
                if fps > 0:
                    self._fps[path] = min(30.0, fps)
                if tabs[path].evaluate("() => 'audio' in document.body.dataset"):
                    self._audio.add(path)
                quality = tabs[path].evaluate("() => parseInt(document.body.dataset.quality || '0')")
                if 30 <= quality <= 95:
                    self._quality[path] = quality

    def _open(self, page, widget: library.Widget) -> None:  # noqa: ANN001
        """Load a widget (once). Some pages keep the network busy (skins, font CSS), so wait for the load
        event and then give web fonts and Webamp a moment instead of waiting for network idle."""
        page.goto(widget.url, timeout=20)
        try:
            page.wait_for_function("document.fonts && document.fonts.status === 'loaded'", timeout=5)
        except Exception:  # noqa: BLE001 - a slow font is not worth blocking the screen
            pass
        time.sleep(2.5 if widget.stem == "hifi_player" else 0.8)  # Webamp needs longer

    def _show(self, frame: Image.Image, shown: Image.Image | None) -> bool:
        """Bring the screen from `shown` to `frame`: nothing if equal, region frames for the changed parts
        when the firmware has them (`capabilities.region`) and they are small, a full frame otherwise. True
        if the screen now shows `frame`."""
        if shown is not None and ImageChops.difference(shown, frame).getbbox() is None:
            return True
        region_ok = self.screen is not None and self.screen.capabilities.region
        rects = dirty_regions(shown, frame) if region_ok else None
        if rects:
            try:
                quality = self._quality.get(self.pages[self.index], self.quality)
                for x, y, w, h in rects:
                    if not self._still_on(self._sending_page):
                        return False
                    jpeg = encode_jpeg(frame.crop((x, y, x + w, y + h)), quality, optimize=True)
                    result = self.screen.show_region(jpeg, x, y)
                    if result.hidden or result.status == c.FRAME_SUPERSEDED:
                        return False
                    self.regions += 1
                    self.bytes += len(jpeg)
                return True
            except FrameRejected as exc:
                if exc.status != c.FRAME_NO_CANVAS:
                    raise
        return self._send(encode_jpeg(frame, self.quality, optimize=True))

    def _send(self, jpeg: bytes) -> bool:
        self.frames += 1
        self.bytes += len(jpeg)
        if self.out_dir is not None:
            self.out_dir.mkdir(parents=True, exist_ok=True)
            (self.out_dir / f"{self.frames:04d}_{self.pages[self.index].stem}.jpg").write_bytes(jpeg)
            return True
        result = self.screen.show_jpeg(jpeg)
        return bool(result.shown)
