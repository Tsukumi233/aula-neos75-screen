"""Where the service keeps its files, and its settings."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from ..live import library


def data_dir() -> Path:
    """`%APPDATA%\\neos75screen` (the selection file lives here too). `NEOS75_HOME` overrides (tests)."""
    override = os.environ.get("NEOS75_HOME")
    return Path(override) if override else library.default_config().parent


def pages_file() -> Path:
    return data_dir() / "pages.json"


def settings_file() -> Path:
    return data_dir() / "settings.json"


def knobs_file() -> Path:
    """What the two knobs do (`knobmap`)."""
    return data_dir() / "knobs.json"


def discovery_file() -> Path:
    """Written by a running service: {"port", "token", "pid", "fake"}."""
    return data_dir() / "service.json"


def cache_dir() -> Path:
    base = os.environ.get("NEOS75_HOME") or os.environ.get("LOCALAPPDATA") or str(Path.home() / ".cache")
    return Path(base) / "neos75screen" / "previews" if not os.environ.get("NEOS75_HOME") else Path(base) / "previews"


@dataclass
class Settings:
    """Service settings (`settings.json`); unknown keys are ignored, missing keys take the defaults."""

    run_on_start: bool = True          # start the widget runner when the service starts
    interval: float = 1.0              # seconds between frames for pages without `data-fps`
    quality: int = 88                  # JPEG quality of the frames sent to the screen
    extra_dirs: list[str] = field(default_factory=list)   # more widget folders (shown in the library)

    @classmethod
    def load(cls, path: Path | None = None) -> Settings:
        path = path or settings_file()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        return cls().update(raw if isinstance(raw, dict) else {})

    def update(self, changes: dict) -> Settings:
        """Apply known keys with type checks; raises ValueError for a bad value."""
        known = {f.name: f for f in fields(self)}
        for key, value in changes.items():
            if key not in known:
                continue
            current = getattr(self, key)
            if isinstance(current, bool):
                if not isinstance(value, bool):
                    raise ValueError(f"{key} must be true or false")
            elif isinstance(current, (int, float)):
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ValueError(f"{key} must be a number")
                value = type(current)(value)
            elif isinstance(current, list):
                if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                    raise ValueError(f"{key} must be a list of strings")
            setattr(self, key, value)
        if not 0.05 <= self.interval <= 60:
            raise ValueError("interval must be 0.05..60 seconds")
        if not 30 <= self.quality <= 95:
            raise ValueError("quality must be 30..95")
        return self

    def save(self, path: Path | None = None) -> None:
        path = path or settings_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def dirs(self) -> list[Path]:
        return [Path(d) for d in self.extra_dirs]
