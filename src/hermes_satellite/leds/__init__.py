"""LED subsystem: state-driven APA102 or mock LED animation."""

from __future__ import annotations

from ..config import Config
from .base import Color, LEDBackend, LEDController, LEDState
from .controller import AnimatedLEDController

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

    Demo mode forces the mock backend so it runs without SPI hardware.
    """
    leds = config.leds
    num = config.profile.led_count
    backend_name = "mock" if demo else leds.backend.lower()

    if backend_name == "apa102":
        from .apa102_backend import APA102Backend

        backend: LEDBackend = APA102Backend(
            num,
            spi_bus=leds.spi_bus,
            spi_device=leds.spi_device,
            brightness=leds.brightness,
        )
    elif backend_name == "mock":
        from .mock_backend import MockLEDBackend

        backend = MockLEDBackend(num)
    else:
        raise ValueError(f"Unknown leds.backend: {leds.backend!r} (apa102 | mock)")

    return AnimatedLEDController(backend, brightness=leds.brightness)
