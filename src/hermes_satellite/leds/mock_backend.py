"""Mock LED backend for development off-Pi.

Renders frames as throttled log lines instead of driving SPI, so the full
animation/state machine can be exercised on a laptop. Also records the most
recent frame for tests.
"""

from __future__ import annotations

import logging
import time
from typing import List, Optional

from .base import Color, LEDBackend

logger = logging.getLogger("hermes_satellite.leds.mock")


class MockLEDBackend(LEDBackend):
    def __init__(self, num_leds: int, log_interval: float = 0.5):
        super().__init__(num_leds)
        self._log_interval = log_interval
        self._last_log = 0.0
        self._brightness = 8
        # Exposed for tests/inspection.
        self.last_frame: Optional[List[Color]] = None

    def set_frame(self, colors: List[Color]) -> None:
        self.last_frame = list(colors)
        now = time.monotonic()
        if now - self._last_log >= self._log_interval:
            self._last_log = now
            swatch = " ".join(f"#{r:02x}{g:02x}{b:02x}" for r, g, b in colors)
            logger.info("LED[%d @ b%d]: %s", self.num_leds, self._brightness, swatch)

    def set_global_brightness(self, brightness: int) -> None:
        self._brightness = max(0, min(int(brightness), 31))

    def clear(self) -> None:
        self.last_frame = [(0, 0, 0)] * self.num_leds
        logger.info("LED cleared")

    def close(self) -> None:
        logger.debug("mock LED backend closed")
