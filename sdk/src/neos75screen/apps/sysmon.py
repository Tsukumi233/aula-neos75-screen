"""System monitor dashboard: CPU (total, per core, history), RAM, GPU (NVIDIA, when nvidia-ml-py is
installed), disk and network. Two columns on a landscape image, one column on a square one.

Views: "overview" (everything) and "cores" (one labelled bar per logical core). `sysmon_frames` takes a
`view` callable, for example `ViewCycle(VIEWS).current` switched by the knob.
"""
from __future__ import annotations

import math
import os
import time
from collections import deque
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

from PIL import Image, ImageDraw

from ._common import (ACCENTS, BACKGROUND, FOREGROUND, MUTED, PREVIEW_SIZE, TRACK, fitting_font, font, scale,
                      wait_until)

HISTORY = 60
VIEWS = ("overview", "cores")
MAX_CORE_BARS = 32
LANDSCAPE_RATIO = 1.15


@dataclass(frozen=True)
class Sample:
    cpu: float                  # percent
    ram_used: int               # bytes
    ram_total: int
    net_down: float             # bytes per second
    net_up: float
    gpu: float | None           # percent, None when unavailable
    cpu_history: tuple[float, ...]
    per_cpu: tuple[float, ...] = ()
    disk_percent: float | None = None
    uptime_s: float | None = None

    @property
    def ram_percent(self) -> float:
        return 100.0 * self.ram_used / self.ram_total if self.ram_total else 0.0


class _NvidiaGpu:
    """GPU utilisation through NVML; `read()` returns None when NVML is unavailable."""

    def __init__(self) -> None:
        self._nvml: Any = None
        self._handle: Any = None
        try:
            import pynvml
            pynvml.nvmlInit()
            self._handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            self._nvml = pynvml
        except Exception:  # not installed, no driver, or no NVIDIA GPU
            self._nvml = None

    def read(self) -> float | None:
        if self._nvml is None:
            return None
        try:
            return float(self._nvml.nvmlDeviceGetUtilizationRates(self._handle).gpu)
        except Exception:
            return None


class Sampler:
    """Collects samples with psutil (install with `pip install neos75screen[sysmon]`)."""

    def __init__(self, disk_path: str | None = None) -> None:
        try:
            import psutil
        except ImportError as exc:
            raise RuntimeError("the system monitor needs psutil: pip install psutil") from exc
        self._psutil = psutil
        self._gpu = _NvidiaGpu()
        self._disk_path = disk_path or os.path.abspath(os.sep)
        self._history: deque[float] = deque(maxlen=HISTORY)
        psutil.cpu_percent(None)
        psutil.cpu_percent(None, percpu=True)
        self._net = psutil.net_io_counters()
        self._net_time = time.monotonic()

    def sample(self) -> Sample:
        ps = self._psutil
        cpu = ps.cpu_percent(None)
        self._history.append(cpu)
        mem = ps.virtual_memory()
        net, now = ps.net_io_counters(), time.monotonic()
        dt = max(now - self._net_time, 1e-3)
        down = (net.bytes_recv - self._net.bytes_recv) / dt
        up = (net.bytes_sent - self._net.bytes_sent) / dt
        self._net, self._net_time = net, now
        try:
            disk: float | None = ps.disk_usage(self._disk_path).percent
        except OSError:
            disk = None
        return Sample(cpu, mem.used, mem.total, down, up, self._gpu.read(), tuple(self._history),
                      tuple(ps.cpu_percent(None, percpu=True)), disk, time.time() - ps.boot_time())


def _rate(value: float) -> str:
    for unit in ("B/s", "KB/s", "MB/s"):
        if value < 1000:
            return f"{value:.0f} {unit}"
        value /= 1000
    return f"{value:.1f} GB/s"


@dataclass(frozen=True)
class _Gauge:
    label: str
    value: str
    percent: float
    color: tuple[int, int, int]


def _gauges(sample: Sample) -> list[_Gauge]:
    """RAM, GPU (if known) and disk (if known); CPU is drawn separately."""
    ram = f"{sample.ram_used / 2**30:.1f}/{sample.ram_total / 2**30:.0f} GB"
    gauges = [_Gauge("RAM", ram, sample.ram_percent, ACCENTS["ram"])]
    if sample.gpu is not None:
        gauges.append(_Gauge("GPU", f"{sample.gpu:.0f}%", sample.gpu, ACCENTS["gpu"]))
    if sample.disk_percent is not None:
        gauges.append(_Gauge("DISK", f"{sample.disk_percent:.0f}%", sample.disk_percent, ACCENTS["disk"]))
    return gauges


def _draw_gauge(draw: ImageDraw.ImageDraw, gauge: _Gauge, x0: float, x1: float, y: float, s: float) -> float:
    """Label, value and bar; returns the height used."""
    label_font = font(round(20 * s), bold=True)
    draw.text((x0, y + 15 * s), gauge.label, font=label_font, fill=MUTED, anchor="lm")
    room = x1 - x0 - draw.textlength(gauge.label, font=label_font) - 16 * s
    value_font = fitting_font(draw, gauge.value, round(27 * s), round(room), bold=True)
    draw.text((x1, y + 15 * s), gauge.value, font=value_font, fill=FOREGROUND, anchor="rm")
    bar_top, bar_h = y + 34 * s, max(6, round(10 * s))
    draw.rounded_rectangle((x0, bar_top, x1, bar_top + bar_h), radius=bar_h / 2, fill=TRACK)
    filled = x0 + (x1 - x0) * max(0.0, min(gauge.percent, 100.0)) / 100
    if filled > x0 + bar_h:
        draw.rounded_rectangle((x0, bar_top, filled, bar_top + bar_h), radius=bar_h / 2, fill=gauge.color)
    return 34 * s + bar_h


def _draw_network(draw: ImageDraw.ImageDraw, sample: Sample, x0: float, x1: float, y: float, s: float) -> float:
    draw.text((x0, y + 15 * s), "NET", font=font(round(20 * s), bold=True), fill=MUTED, anchor="lm")
    value_font = font(round(21 * s))
    draw.text((x1, y + 15 * s), f"↓ {_rate(sample.net_down)}", font=value_font, fill=ACCENTS["net"], anchor="rm")
    draw.text((x1, y + 42 * s), f"↑ {_rate(sample.net_up)}", font=value_font, fill=FOREGROUND, anchor="rm")
    return 56 * s


def _uptime(seconds: float) -> str:
    minutes = int(seconds) // 60
    days, hours, minutes = minutes // 1440, minutes // 60 % 24, minutes % 60
    return f"{days}d {hours:02d}:{minutes:02d}" if days else f"{hours:02d}:{minutes:02d}"


def _draw_cores(draw: ImageDraw.ImageDraw, cores: tuple[float, ...], box: tuple[float, float, float, float]) -> None:
    """One vertical bar per logical core (averaged into MAX_CORE_BARS groups on very large CPUs)."""
    if not cores:
        return
    groups = min(len(cores), MAX_CORE_BARS)
    bounds = [i * len(cores) // groups for i in range(groups + 1)]
    values = [sum(cores[a:b]) / (b - a) for a, b in zip(bounds, bounds[1:])]
    x0, y0, x1, y1 = box
    pitch = (x1 - x0) / groups
    gap = max(1.0, pitch * 0.25)
    for i, value in enumerate(values):
        left = x0 + i * pitch
        draw.rectangle((left, y0, left + pitch - gap, y1), fill=TRACK)
        top = y1 - (y1 - y0) * max(0.0, min(value, 100.0)) / 100
        if top < y1:
            draw.rectangle((left, top, left + pitch - gap, y1), fill=ACCENTS["cpu"])


def _draw_history(draw: ImageDraw.ImageDraw, history: tuple[float, ...], box: tuple[float, float, float, float]) -> None:
    x0, y0, x1, y1 = box
    draw.rectangle(box, outline=TRACK)
    if len(history) < 2:
        return
    step = (x1 - x0 - 2) / (HISTORY - 1)
    start = HISTORY - len(history)
    points = [(x0 + 1 + (start + i) * step, y1 - 1 - (y1 - y0 - 2) * v / 100) for i, v in enumerate(history)]
    draw.polygon([(points[0][0], y1 - 1), *points, (points[-1][0], y1 - 1)], fill=(20, 50, 90))
    draw.line(points, fill=ACCENTS["cpu"], width=2)


def _render_cores(sample: Sample, size: tuple[int, int]) -> Image.Image:
    """Detail view: CPU total, then one bar per logical core with its number and load."""
    img = Image.new("RGB", size, BACKGROUND)
    draw = ImageDraw.Draw(img)
    width, height = size
    s = scale(size)
    pad = 14 * s
    y = pad + _draw_gauge(draw, _Gauge("CPU", f"{sample.cpu:.0f}%", sample.cpu, ACCENTS["cpu"]),
                          pad, width - pad, pad, s) + 16 * s
    cores = sample.per_cpu[:MAX_CORE_BARS]
    if not cores:
        return img
    label_font = font(round(13 * s))
    label_h = 18 * s
    _draw_cores(draw, cores, (pad, y + label_h, width - pad, height - pad - label_h))
    pitch = (width - 2 * pad) / len(cores)
    stride = math.ceil(22 * s / pitch)  # label every core, or every n-th one when the bars are narrow
    for i, value in enumerate(cores):
        if i % stride == 0:
            x = pad + i * pitch + pitch * 0.375
            draw.text((x, y + label_h / 2), f"{value:.0f}", font=label_font, fill=FOREGROUND, anchor="mm")
            draw.text((x, height - pad - label_h / 2), str(i), font=label_font, fill=MUTED, anchor="mm")
    return img


def render_dashboard(sample: Sample, size: tuple[int, int] = PREVIEW_SIZE, view: str = "overview") -> Image.Image:
    if view == "cores":
        return _render_cores(sample, size)
    if view != "overview":
        raise ValueError(f"unknown view {view!r}; expected one of {VIEWS}")
    img = Image.new("RGB", size, BACKGROUND)
    draw = ImageDraw.Draw(img)
    width, height = size
    s = scale(size)
    pad = 14 * s
    cpu = _Gauge("CPU", f"{sample.cpu:.0f}%", sample.cpu, ACCENTS["cpu"])
    gauges = _gauges(sample)

    if width >= height * LANDSCAPE_RATIO:
        # Left column: CPU total, per-core bars, history. Right column: RAM, GPU, disk, network.
        mid = width / 2
        y = pad + _draw_gauge(draw, cpu, pad, mid - pad, pad, s) + 12 * s
        cores_bottom = y + 70 * s
        _draw_cores(draw, sample.per_cpu, (pad, y, mid - pad, cores_bottom))
        _draw_history(draw, sample.cpu_history, (pad, cores_bottom + 14 * s, mid - pad, height - pad))
        y = pad
        for gauge in gauges:
            y += _draw_gauge(draw, gauge, mid + pad, width - pad, y, s) + 20 * s
        y += _draw_network(draw, sample, mid + pad, width - pad, y, s) + 16 * s
        if sample.uptime_s is not None and y + 30 * s <= height - pad:
            draw.text((mid + pad, y + 15 * s), "UP", font=font(round(20 * s), bold=True), fill=MUTED, anchor="lm")
            draw.text((width - pad, y + 15 * s), _uptime(sample.uptime_s), font=font(round(21 * s)),
                      fill=FOREGROUND, anchor="rm")
        return img

    # Square or portrait: one column, history in the remaining space.
    y = pad
    for gauge in [cpu, *gauges[:2]]:
        y += _draw_gauge(draw, gauge, pad, width - pad, y, s) + 14 * s
    y += _draw_network(draw, sample, pad, width - pad, y, s) + 8 * s
    if height - pad - y > 24 * s:
        _draw_history(draw, sample.cpu_history, (pad, y, width - pad, height - pad))
    return img


def sysmon_frames(interval: float = 1.0, size: tuple[int, int] = PREVIEW_SIZE,
                  view: Callable[[], str] = lambda: "overview") -> Iterator[Image.Image]:
    """One dashboard frame every `interval` seconds, in the view `view()` names at that moment."""
    sampler = Sampler()
    next_at = time.monotonic() + min(interval, 0.5)  # let the first CPU reading cover a real interval
    while True:
        wait_until(next_at)
        next_at = max(next_at + interval, time.monotonic())
        yield render_dashboard(sampler.sample(), size, view())
