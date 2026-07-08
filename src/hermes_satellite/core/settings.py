"""Runtime-tunable settings ("the knobs").

``config.yaml`` is admin-owned and read-only for the service. The small set
of knobs that make sense to adjust at runtime (from Home Assistant via MQTT,
or the setup wizard) live here: thread-safe get/set with validation and
clamping, applied by mutating the live config dataclasses (backends read
their config at use-time, so most changes take effect on the next
utterance), and persisted to an overlay file in the service's writable data
directory (``{data_dir}/runtime.yaml``) that is re-applied at startup.

Changes are announced to subscribers; the app wires the ones that need an
explicit apply step (``voice`` → TTS reload, ``led_brightness`` → LED
controller).
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, List, Optional

import yaml

logger = logging.getLogger(__name__)

SettingsListener = Callable[[str, Any], None]


@dataclass(frozen=True)
class Knob:
    key: str
    section: str  # attribute on Config ("tts", "wakeword", "leds")
    field: str
    cast: type
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    # get() value when the underlying field is None (unset optional knob).
    none_default: Any = None


KNOBS = (
    Knob("volume", "tts", "volume", float, 0.0, 1.0),
    Knob("length_scale", "tts", "length_scale", float, 0.5, 2.0, none_default=1.0),
    Knob("voice", "tts", "voice", str),
    Knob("wake_threshold", "wakeword", "threshold", float, 0.05, 1.0),
    Knob("led_brightness", "leds", "brightness", int, 0, 31),
)


class RuntimeSettings:
    def __init__(self, config, path: Optional[Path]):
        self._config = config
        self._path = path
        self._lock = threading.Lock()
        self._listeners: List[SettingsListener] = []
        self._knobs = {k.key: k for k in KNOBS}

    # -- introspection --------------------------------------------------------
    def keys(self):
        return list(self._knobs)

    def knob(self, key: str) -> Knob:
        return self._knobs[key]

    def get(self, key: str) -> Any:
        knob = self._knobs[key]
        value = getattr(getattr(self._config, knob.section), knob.field)
        return knob.none_default if value is None else value

    # -- mutation --------------------------------------------------------------
    def set(self, key: str, value: Any) -> Any:
        """Validate, apply to live config, persist, notify. Returns the value."""
        applied = self._apply(key, value)
        self._save()
        for listener in list(self._listeners):
            try:
                listener(key, applied)
            except Exception:  # pragma: no cover - listener isolation
                logger.exception("settings listener failed for %s", key)
        return applied

    def subscribe(self, listener: SettingsListener) -> None:
        self._listeners.append(listener)

    def _apply(self, key: str, value: Any) -> Any:
        if key not in self._knobs:
            raise KeyError(f"Unknown setting: {key!r} (known: {sorted(self._knobs)})")
        knob = self._knobs[key]
        value = knob.cast(value)
        if knob.minimum is not None:
            value = max(knob.cast(knob.minimum), value)
        if knob.maximum is not None:
            value = min(knob.cast(knob.maximum), value)
        with self._lock:
            setattr(getattr(self._config, knob.section), knob.field, value)
        logger.info("setting %s = %s", key, value)
        return value

    # -- persistence -----------------------------------------------------------
    def load(self) -> None:
        """Apply the persisted overlay (called once at startup)."""
        if self._path is None or not self._path.exists():
            return
        try:
            data = yaml.safe_load(self._path.read_text()) or {}
        except Exception as exc:
            logger.warning("could not read %s: %s", self._path, exc)
            return
        for key, value in data.items():
            if key not in self._knobs:
                logger.warning("ignoring unknown persisted setting %r", key)
                continue
            try:
                self._apply(key, value)
            except Exception as exc:
                logger.warning("could not apply persisted %s=%r: %s", key, value, exc)

    def _save(self) -> None:
        if self._path is None:
            return
        data = {key: self.get(key) for key in self._knobs}
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(yaml.safe_dump(data, sort_keys=True))
        except OSError as exc:
            logger.warning("could not persist settings to %s: %s", self._path, exc)
