"""Live data for the screen widgets: system load (psutil, nvidia-smi) and now playing (Windows SMTC).

Each provider samples on its own background thread and keeps the latest values, so the render loop never
waits on a slow source. `snapshot()` returns plain JSON-able dicts.
"""
from __future__ import annotations

import asyncio
import math
import shutil
import subprocess
import threading
import time


class SystemProvider:
    """CPU, memory, system-disk usage, network throughput (psutil) and NVIDIA GPU load/temperature."""

    def __init__(self, interval: float = 1.0) -> None:
        import psutil
        self._psutil = psutil
        self._interval = interval
        self._data: dict = {"cpu": 0, "mem": 0, "disk": 0, "net": 0.0, "gpu": 0, "temp": None}
        self._nvidia = shutil.which("nvidia-smi")
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="neos75-system", daemon=True)

    def start(self) -> SystemProvider:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def snapshot(self) -> dict:
        return dict(self._data)

    def _gpu(self) -> tuple[int, int | None]:
        if not self._nvidia:
            return 0, None
        try:
            out = subprocess.run([self._nvidia, "--query-gpu=utilization.gpu,temperature.gpu", "--format=csv,noheader,nounits"],
                                 capture_output=True, text=True, timeout=3, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            util, temp = (int(x) for x in out.stdout.strip().splitlines()[0].split(","))
            return util, temp
        except Exception:  # noqa: BLE001 - a missing or busy driver just leaves the last value
            return self._data.get("gpu", 0), self._data.get("temp")

    def _run(self) -> None:
        ps = self._psutil
        ps.cpu_percent(None)
        last_net, last_t = ps.net_io_counters(), time.monotonic()
        tick = 0
        while not self._stop.wait(self._interval):
            now = time.monotonic()
            net = ps.net_io_counters()
            rate = (net.bytes_recv - last_net.bytes_recv) / max(1e-3, now - last_t) / 1e6
            last_net, last_t = net, now
            data = {"cpu": round(ps.cpu_percent(None)), "mem": round(ps.virtual_memory().percent),
                    "disk": round(ps.disk_usage(ps.disk_partitions()[0].mountpoint).percent), "net": round(rate, 1)}
            if tick % 2 == 0:
                data["gpu"], data["temp"] = self._gpu()
            tick += 1
            self._data.update(data)


class MediaProvider:
    """Now playing from Windows' System Media Transport Controls (what the volume flyout shows).

    Some players (e.g. NetEase Cloud Music) report title and artist but no timeline; then the position is
    counted locally while the session reports Playing, and the duration is unknown (0)."""

    PLAYING = 4

    def __init__(self, interval: float = 1.0) -> None:
        self._interval = interval
        self._data: dict = {"title": "", "artist": "", "album": "", "playing": False, "pos": 0.0, "dur": 0.0,
                            "app": "", "has_media": False}
        self._key = None
        self._local_pos = 0.0
        self._last = time.monotonic()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=lambda: asyncio.run(self._run()), name="neos75-media", daemon=True)

    def start(self) -> MediaProvider:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def snapshot(self) -> dict:
        data = dict(self._data)
        if data["playing"] and not data["dur"]:
            data["pos"] = self._local_pos + (time.monotonic() - self._last)
        return data

    async def _run(self) -> None:
        from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as Manager
        manager = await Manager.request_async()
        while not self._stop.is_set():
            try:
                await self._sample(manager)
            except Exception:  # noqa: BLE001 - players come and go; keep the last good values
                pass
            await asyncio.sleep(self._interval)

    async def _sample(self, manager) -> None:  # noqa: ANN001
        session = manager.get_current_session()
        now = time.monotonic()
        if session is None:
            self._data.update(has_media=False, playing=False)
            return
        props = await session.try_get_media_properties_async()
        timeline = session.get_timeline_properties()
        playing = session.get_playback_info().playback_status == self.PLAYING
        key = (props.title, props.artist)
        if key != self._key:  # new track: restart the local clock
            self._key, self._local_pos = key, 0.0
        elif self._data["playing"]:
            self._local_pos += now - self._last
        self._last = now
        dur = timeline.end_time.total_seconds() if timeline.end_time else 0.0
        pos = timeline.position.total_seconds() if dur else self._local_pos
        self._data.update(title=props.title or "", artist=props.artist or "", album=props.album_title or "",
                          playing=playing, pos=pos, dur=dur, app=session.source_app_user_model_id or "", has_media=True)


class AudioLevelProvider:
    """Real output level of the default playback device (Windows peak meter, per channel): `lvl_l`, `lvl_r`
    0..1. Sampled every 30 ms; a snapshot gives the highest peak since the previous snapshot, falling back
    smoothly (0.25 s time constant) like a peak meter (no fake animation: silence reads 0). Missing pycaw/COM: always 0.
    The widget runner no longer uses it (it polls a COM interface through comtypes every 30 ms; the service
    once died of an access violation in _ctypes): SpectrumProvider gives the same `lvl_l`/`lvl_r`."""

    SAMPLE_S = 0.03
    FALL_S = 0.25        # the shown level falls with this time constant (peak meter ballistics)

    def __init__(self) -> None:
        self._peak = [0.0, 0.0]
        self._shown = [0.0, 0.0]
        self._at = time.monotonic()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="neos75-audio", daemon=True)

    def start(self) -> AudioLevelProvider:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def snapshot(self) -> dict:
        with self._lock:
            peak, self._peak = self._peak, [0.0, 0.0]
        now = time.monotonic()
        fall = math.exp(-(now - self._at) / self.FALL_S)
        self._at = now
        self._shown = [max(p, s * fall) for p, s in zip(peak, self._shown)]
        return {"lvl_l": round(self._shown[0], 3), "lvl_r": round(self._shown[1], 3)}

    def _meter(self):  # noqa: ANN202
        from ctypes import HRESULT, POINTER, c_float, c_uint
        from comtypes import CLSCTX_ALL, COMMETHOD, GUID, IUnknown, cast
        from pycaw.pycaw import AudioUtilities

        class IAudioMeterInformation(IUnknown):  # pycaw declares only GetPeakValue
            _iid_ = GUID("{C02216F6-8C67-4B5B-9D00-D008E73E0064}")
            _methods_ = (COMMETHOD([], HRESULT, "GetPeakValue", (["out"], POINTER(c_float), "peak")),
                         COMMETHOD([], HRESULT, "GetMeteringChannelCount", (["out"], POINTER(c_uint), "count")),
                         COMMETHOD([], HRESULT, "GetChannelsPeakValues", (["in"], c_uint, "count"),
                                   (["in"], POINTER(c_float), "peaks")))

        device = AudioUtilities.GetDeviceEnumerator().GetDefaultAudioEndpoint(0, 1)  # render, multimedia
        meter = cast(device.Activate(IAudioMeterInformation._iid_, CLSCTX_ALL, None), POINTER(IAudioMeterInformation))
        return meter, c_float

    def _run(self) -> None:
        try:
            import comtypes
            comtypes.CoInitialize()
            meter, c_float = self._meter()
            count = meter.GetMeteringChannelCount()
        except Exception:  # noqa: BLE001 - no audio device or no pycaw: levels stay 0
            return
        values = (c_float * max(1, count))()
        reopen = 0.0
        while not self._stop.wait(self.SAMPLE_S):
            try:
                meter.GetChannelsPeakValues(count, values)
            except Exception:  # noqa: BLE001 - default device changed: reopen once a second
                if time.monotonic() - reopen > 1.0:
                    reopen = time.monotonic()
                    try:
                        meter, c_float = self._meter()
                        count = meter.GetMeteringChannelCount()
                        values = (c_float * max(1, count))()
                    except Exception:  # noqa: BLE001
                        pass
                continue
            left, right = values[0], values[1] if count > 1 else values[0]
            with self._lock:
                self._peak = [max(self._peak[0], left), max(self._peak[1], right)]


class SpectrumProvider:
    """Real spectrum of what the default playback device plays: WASAPI loopback capture (soundcard), Hann
    window, 2048-point FFT, `BANDS` log-spaced bands from 60 Hz to 16 kHz in dBFS, mapped from
    `FLOOR_DB`..`TOP_DB` to 0..1 (`spec`). Bars rise at once and fall with a 0.2 s time constant, like a
    hi-fi analyser. From the same capture, the output peak per channel `lvl_l`, `lvl_r` (0..1, falling with a
    0.25 s time constant like a peak meter). Nothing is stored; silence (or no soundcard/numpy) reads zeros."""

    BANDS = 24
    RATE = 48000
    SIZE = 2048
    FLOOR_DB, TOP_DB = -78.0, -18.0
    FALL_S = 0.2
    LVL_FALL_S = 0.25

    def __init__(self) -> None:
        self._bands = [0.0] * self.BANDS
        self._shown = [0.0] * self.BANDS
        self._peak = [0.0, 0.0]
        self._lvl = [0.0, 0.0]
        self._at = time.monotonic()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="neos75-spectrum", daemon=True)

    def start(self) -> SpectrumProvider:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def snapshot(self) -> dict:
        with self._lock:
            bands, self._bands = self._bands, [0.0] * self.BANDS
            peak, self._peak = self._peak, [0.0, 0.0]
        now = time.monotonic()
        fall = math.exp(-(now - self._at) / self.FALL_S)
        lvl_fall = math.exp(-(now - self._at) / self.LVL_FALL_S)
        self._at = now
        self._shown = [max(b, s * fall) for b, s in zip(bands, self._shown)]
        self._lvl = [max(p, s * lvl_fall) for p, s in zip(peak, self._lvl)]
        return {"spec": [round(v, 3) for v in self._shown],
                "lvl_l": round(self._lvl[0], 3), "lvl_r": round(self._lvl[1], 3)}

    def _edges(self, np):  # noqa: ANN001, ANN202
        freqs = np.fft.rfftfreq(self.SIZE, 1 / self.RATE)
        edges = np.geomspace(60, 16000, self.BANDS + 1)   # from 60 Hz every band holds its own bin
        bins = []
        for lo, hi in zip(edges[:-1], edges[1:]):
            idx = np.nonzero((freqs >= lo) & (freqs < hi))[0]
            if not len(idx):   # low bands narrower than one bin: the nearest bin
                idx = np.array([int(np.argmin(np.abs(freqs - math.sqrt(lo * hi))))])
            bins.append(idx)
        return bins

    def _run(self) -> None:
        try:
            import warnings
            import numpy as np
            import soundcard as sc
            speaker = sc.default_speaker()
            mic = sc.get_microphone(id=str(speaker.name), include_loopback=True)
        except Exception:  # noqa: BLE001 - no soundcard/numpy or no device: the spectrum stays at zero
            return
        window = np.hanning(self.SIZE).astype(np.float32)
        scale = 2.0 / window.sum()
        bins = self._edges(np)
        ring = np.zeros(self.SIZE, dtype=np.float32)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")   # soundcard warns on every gap ("data discontinuity")
            try:
                with mic.recorder(samplerate=self.RATE, channels=2, blocksize=self.SIZE // 2) as rec:
                    while not self._stop.is_set():
                        raw = rec.record(numframes=self.SIZE // 2)
                        peaks = np.abs(raw).max(axis=0) if len(raw) else np.zeros(2)
                        left, right = min(1.0, float(peaks[0])), min(1.0, float(peaks[-1]))
                        block = raw.mean(axis=1).astype(np.float32)
                        ring = np.concatenate((ring[len(block):], block))[-self.SIZE:]
                        mag = np.abs(np.fft.rfft(ring * window)) * scale
                        db = 20 * np.log10(np.maximum(1e-9, np.array([mag[i].max() for i in bins])))
                        level = np.clip((db - self.FLOOR_DB) / (self.TOP_DB - self.FLOOR_DB), 0, 1)
                        with self._lock:
                            self._bands = [max(a, float(b)) for a, b in zip(self._bands, level)]
                            self._peak = [max(self._peak[0], left), max(self._peak[1], right)]
            except Exception:  # noqa: BLE001 - device changed or vanished: stop quietly (zeros)
                return
