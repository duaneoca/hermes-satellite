"""Animated LED controller shared by all backends.

Runs a daemon thread that renders the current :class:`LEDState` as an animation
frame at a fixed rate, delegating the actual pixel writes to an
:class:`LEDBackend`. Patterns are computed here so real hardware and the mock
backend show identical behaviour.
"""

from __future__ import annotations

import math
import threading
import time
from typing import List

from .base import Color, LEDBackend, LEDController, LEDState

# Base colours per state (before brightness/animation scaling).
_STATE_COLORS = {
    LEDState.IDLE: (0, 80, 160),        # calm blue
    LEDState.WAKE: (0, 160, 120),       # teal
    LEDState.RECORDING: (0, 200, 0),    # green
    LEDState.PROCESSING: (200, 140, 0), # amber spinner
    LEDState.SPEAKING: (60, 120, 220),  # blue
    LEDState.MUTED: (120, 0, 0),        # dim red
    LEDState.ERROR: (220, 0, 0),        # red
    LEDState.OFF: (0, 0, 0),
}

_FPS = 30
_FRAME_DT = 1.0 / _FPS


class AnimatedLEDController(LEDController):
    def __init__(self, backend: LEDBackend, brightness: int = 8):
        self._backend = backend
        self._num = backend.num_leds
        self._state = LEDState.OFF
        self._state_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread = None  # type: ignore[assignment]
        self._t0 = time.monotonic()
        self._backend.set_global_brightness(brightness)

    # -- LEDController API ---------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="led-animation", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)
        self._backend.clear()
        self._backend.close()

    def set_state(self, state: LEDState) -> None:
        with self._state_lock:
            if state != self._state:
                self._state = state
                self._t0 = time.monotonic()

    def set_brightness(self, brightness: int) -> None:
        self._backend.set_global_brightness(brightness)

    # -- Animation -----------------------------------------------------------
    def _run(self) -> None:
        while not self._stop.is_set():
            with self._state_lock:
                state = self._state
                elapsed = time.monotonic() - self._t0
            frame = self._render(state, elapsed)
            self._backend.set_frame(frame)
            self._stop.wait(_FRAME_DT)

    def _render(self, state: LEDState, t: float) -> List[Color]:
        base = _STATE_COLORS[state]
        if state == LEDState.OFF:
            return [(0, 0, 0)] * self._num
        if state == LEDState.IDLE:
            # Breathing: sine 0.1 .. 1.0 over ~4s.
            scale = 0.1 + 0.9 * (0.5 + 0.5 * math.sin(t * math.pi / 2.0))
            return [_scale(base, scale)] * self._num
        if state in (LEDState.SPEAKING, LEDState.ERROR):
            # Pulsing: faster sine 0.2 .. 1.0.
            scale = 0.2 + 0.8 * (0.5 + 0.5 * math.sin(t * math.pi * 2.0))
            return [_scale(base, scale)] * self._num
        if state == LEDState.PROCESSING:
            # Spinner: one bright LED walks the ring, others dim.
            active = int(t * 6) % self._num
            return [
                base if i == active else _scale(base, 0.15)
                for i in range(self._num)
            ]
        # WAKE, RECORDING, MUTED: solid.
        return [base] * self._num


def _scale(color: Color, factor: float) -> Color:
    return (
        int(color[0] * factor),
        int(color[1] * factor),
        int(color[2] * factor),
    )
