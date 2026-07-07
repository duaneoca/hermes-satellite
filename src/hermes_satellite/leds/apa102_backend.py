"""APA102 hardware backend (ReSpeaker 2-Mic HAT v1 & v2).

Wraps the vendored Seeed ``apa102`` SPI driver. Works on both HAT revisions
since the LEDs are identical; only the SPI bus/device (from the hardware
profile / config) differ between Pi 4 and Pi 5.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import List

from .base import Color, LEDBackend

logger = logging.getLogger(__name__)

# The APA102 driver is vendored at the repo root under vendor/.
_VENDOR = Path(__file__).resolve().parents[3] / "vendor"
if str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))


class APA102Backend(LEDBackend):
    def __init__(
        self,
        num_leds: int,
        *,
        spi_bus: int,
        spi_device: int,
        brightness: int = 8,
    ):
        super().__init__(num_leds)
        from apa102 import APA102  # imported lazily so non-Pi hosts can import module

        self._driver = APA102(
            num_led=num_leds,
            global_brightness=brightness,
            order="rgb",
            bus=spi_bus,
            device=spi_device,
        )
        logger.info(
            "APA102 backend: %d LEDs on spidev%d.%d, brightness=%d",
            num_leds, spi_bus, spi_device, brightness,
        )

    def set_frame(self, colors: List[Color]) -> None:
        for i, (r, g, b) in enumerate(colors):
            self._driver.set_pixel(i, r, g, b)
        self._driver.show()

    def set_global_brightness(self, brightness: int) -> None:
        self._driver.global_brightness = max(0, min(int(brightness), 31))

    def clear(self) -> None:
        self._driver.clear_strip()

    def close(self) -> None:
        try:
            self._driver.clear_strip()
            self._driver.cleanup()
        except Exception:  # pragma: no cover - best-effort cleanup
            logger.debug("APA102 cleanup failed", exc_info=True)
