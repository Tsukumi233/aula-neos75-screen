"""Page previews: render a selection entry (`neos_photo?src=...`) to a 480x360 PNG in headless Chrome.

Pages show their built-in sample values (design/widgets/data.js) with the current time and the page label
they would get in the knob cycle. Renders are cached on disk by entry, label, data and the modification time
of the page and the shared files it uses. A cached render is always served at once; one older than
`REFRESH_S` (the clock on it is stale) is rendered again in the background for the next request
(stale-while-revalidate: the cache used to expire every minute, so every app start re-rendered every
thumbnail at 1.3-2.2 s each, one after another). `warm()` renders what the app will ask for ahead of time.
One worker thread owns the browser (live/cdp.py) and
serves a priority queue (requests waited on first, then background refreshes, then warm-up).
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import itertools
import queue
import threading
import time
from collections.abc import Iterable, Sequence
from concurrent.futures import Future
from pathlib import Path

from PIL import Image

from ..live import cdp, library

SHARED = [library.ROOT / "design" / "widgets" / "data.js", library.ROOT / "design" / "neos" / "status.js",
          library.ROOT / "design" / "neos" / "neos.css"]
SETTLE_S = {"hifi_player": 2.5, "neos_hifi_player": 1.2, "neos_cassette": 1.2}
REFRESH_S = 120            # a cached render older than this is served, then rendered again in the background
IDLE_S = 60                # the preview browser (about 0.4 GB) closes after this long without work
KEEP_S = 3 * 24 * 3600     # cached renders not touched for this long are deleted at start
WAIT, REFRESH, WARM = 0, 1, 2  # queue priorities


class Previewer:
    def __init__(self, cache: Path | None = None, dirs: Sequence[Path] = (), log=print) -> None:  # noqa: ANN001
        self.cache, self.dirs, self.log = cache, list(dirs), log
        self._queue: queue.PriorityQueue = queue.PriorityQueue()
        self._seq = itertools.count()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._inflight: dict[str, Future] = {}
        self._prune()

    # --- public -------------------------------------------------------------------------------------

    def render(self, entry: str, label: str | None = "Page 7", data: dict | None = None,
               timeout: float = 60.0, fresh: bool = False) -> bytes:
        """PNG bytes of `entry`; raises ValueError for an unknown widget, RuntimeError if rendering failed."""
        widget = library.resolve([entry], self.dirs)[0]
        key = self._key(widget, label, data)
        path = self.cache / f"{key}.png" if self.cache else None
        if path is not None and not fresh:
            try:
                png, age = path.read_bytes(), time.time() - path.stat().st_mtime
            except OSError:
                png = None
            if png:
                if age > REFRESH_S:
                    self._submit(widget, label, data, key, REFRESH)   # the next request gets a current one
                return png
        return self._submit(widget, label, data, key, WAIT).result(timeout)

    def warm(self, items: Iterable[tuple[str, str | None]], data: dict | None = None) -> int:
        """Render (entry, label) pairs that have no cached render yet, after anything waited on.
        Returns how many were queued."""
        queued = 0
        for entry, label in items:
            try:
                widget = library.resolve([entry], self.dirs)[0]
            except ValueError:
                continue
            key = self._key(widget, label, data)
            if self.cache is None or not (self.cache / f"{key}.png").is_file():
                self._submit(widget, label, data, key, WARM)
                queued += 1
        return queued

    def close(self) -> None:
        if self._thread is not None:
            self._queue.put((-1, next(self._seq), None))
            self._thread.join(10)
            self._thread = None

    # --- internals ----------------------------------------------------------------------------------

    def _submit(self, widget: library.Widget, label: str | None, data: dict | None, key: str,
                priority: int) -> Future:
        """The render of `key`, queued once however many ask; the result is written to the cache."""
        with self._lock:
            future = self._inflight.get(key)
            if future is not None:
                return future
            future = self._inflight[key] = Future()
        path = self.cache / f"{key}.png" if self.cache else None

        def store(done: Future) -> None:
            with self._lock:
                self._inflight.pop(key, None)
            if path is None or done.cancelled() or done.exception() is not None:
                return
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_name(f"{path.stem}.{threading.get_ident()}.tmp")
                tmp.write_bytes(done.result())
                tmp.replace(path)             # readers never see a half-written PNG
            except OSError as exc:
                self.log(f"preview cache: {exc}")

        future.add_done_callback(store)
        # queue first, then make sure a worker runs: an idle worker only exits while the queue is empty
        self._queue.put((priority, next(self._seq), (widget, label, data, future)))
        self._ensure_thread()
        return future

    def _prune(self) -> None:
        if self.cache is None or not self.cache.is_dir():
            return
        old = time.time() - KEEP_S
        for f in self.cache.glob("*.png"):
            try:
                if f.stat().st_mtime < old:
                    f.unlink()
            except OSError:
                pass

    def _key(self, widget: library.Widget, label: str | None, data: dict | None) -> str:
        stamps = [f"{p}:{p.stat().st_mtime_ns}" for p in [widget.path, *SHARED] if p.exists()]
        raw = "|".join([widget.name, str(widget.path), label or "", repr(sorted((data or {}).items())), *stamps])
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]

    def _ensure_thread(self) -> None:
        with self._lock:  # RLock: also taken by render()
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._work, name="neos75-preview", daemon=True)
                self._thread.start()

    def _work(self) -> None:
        try:
            with contextlib.closing(cdp.launch(["--allow-file-access-from-files",
                                                "--autoplay-policy=no-user-gesture-required"])) as browser:
                while True:
                    try:
                        entry = self._queue.get(timeout=IDLE_S)
                    except queue.Empty:
                        with self._lock:      # _ensure_thread starts a new worker for work queued after this
                            if self._queue.empty():
                                self._thread = None
                                break
                        continue
                    item = entry[2]
                    if item is None:
                        break
                    widget, label, data, future = item
                    if future.set_running_or_notify_cancel():
                        for attempt in (1, 2):    # a page that times out once usually loads the second time
                            try:
                                future.set_result(self._shot(browser, widget, label, data))
                                break
                            except Exception as exc:  # noqa: BLE001 - reported to the caller
                                self.log(f"preview {widget.name} (attempt {attempt}): {exc}")
                                if attempt == 2:
                                    future.set_exception(RuntimeError(f"render {widget.name}: {exc}"))
        except Exception as exc:  # noqa: BLE001 - e.g. Chrome missing
            self.log(f"preview browser: {exc}")
            self._fail_all(RuntimeError(f"preview browser: {exc}"))

    def _fail_all(self, exc: Exception) -> None:
        while True:
            try:
                item = self._queue.get_nowait()[2]
            except queue.Empty:
                return
            if item is not None and item[3].set_running_or_notify_cancel():
                item[3].set_exception(exc)

    @staticmethod
    def _shot(browser: cdp.Browser, widget: library.Widget, label: str | None, data: dict | None) -> bytes:
        page = browser.new_page()
        try:
            page.goto(widget.url, timeout=20)
            try:
                page.wait_for_function("document.fonts && document.fonts.status === 'loaded'", timeout=5)
            except Exception:  # noqa: BLE001 - a slow font is not worth failing the preview
                pass
            update = {"now": time.time() * 1000, **(data or {})}
            if label:
                update["page_label"] = label
            page.evaluate("d => window.neos75Update && window.neos75Update(d)", update)
            time.sleep(SETTLE_S.get(widget.stem, 0.6))
            png = page.screenshot()
        finally:
            page.close()
        Image.open(io.BytesIO(png)).verify()
        return png


def thumbnail(png: bytes, width: int) -> bytes:
    """A smaller PNG (the library grid shows 240x180)."""
    img = Image.open(io.BytesIO(png)).convert("RGB")
    if width >= img.width:
        return png
    img = img.resize((width, round(img.height * width / img.width)), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()
