"""LED subsystem: state-driven APA102 or mock LED animation."""

from __future__ import annotations

import logging

from ..config import Config
from .base import Color, LEDBackend, LEDController, LEDState
from .controller import AnimatedLEDController

logger = logging.getLogger(__name__)

__all__ = [
    "Color",
    "LEDBackend",
    "LEDController",
    "LEDState",
    "AnimatedLEDController",
    "build_led_controller",
]


def build_led_controller(config: Config, demo: bool = False) -> LEDController:
    """Construct the LED controller from config (backend chosen by name).

    Demo mode mocks the *pipeline*, not the LEDs: on real hardware,
    ``--demo`` is the documented LED/button smoke test, so the configured
    backend is honored. The mock backend is used when the ``mock`` hardware
    profile is selected (no hardware at all), and as a logged fallback if the
    APA102/SPI backend fails to initialize — LEDs are not worth killing the
    daemon over.
    """
    leds = config.leds
    num = config.profile.led_count
    backend_name = leds.backend.lower()
    if config.profile.name == "mock":
        backend_name = "mock"

    if backend_name == "apa102":
        try:
            from .apa102_backend import APA102Backend

            backend: LEDBackend = APA102Backend(
                num,
                spi_bus=leds.spi_bus,
                spi_device=leds.spi_device,
                brightness=leds.brightness,
            )
            return AnimatedLEDController(backend, brightness=leds.brightness)
        except Exception as exc:
            logger.error(
                "APA102 LED init failed (%s: %s) — falling back to mock LEDs. "
                "Check /dev/spidev%d.%d exists (SPI enabled?) and the user is "
                "in the 'spi' group.",
                type(exc).__name__, exc, leds.spi_bus, leds.spi_device,
            )
            backend_name = "mock"

    if backend_name == "mock":
        from .mock_backend import MockLEDBackend

        return AnimatedLEDController(
            MockLEDBackend(num), brightness=leds.brightness
        )

    raise ValueError(f"Unknown leds.backend: {leds.backend!r} (apa102 | mock)")
