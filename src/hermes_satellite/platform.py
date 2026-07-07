"""Hardware profiles and GPIO backend selection.

This is the single place the Raspberry Pi 4 (ReSpeaker 2-Mic HAT v1) and
Raspberry Pi 5 (ReSpeaker 2-Mic HAT v2) differences live. Everything the rest
of the codebase needs to differ by hardware — SPI numbering for the APA102
LEDs, which GPIO library drives the HAT button, the number of on-board LEDs —
is resolved from a named :class:`HardwareProfile`.

The LEDs are identical across HAT v1/v2 (3x APA102 over SPI), so only SPI
bus/device and brightness change. The audio codec differs (WM8960 vs
TLV320AIC3104) but that is an OS-level driver/overlay concern handled in the
hardware setup guides, not here.
"""

from __future__ import annotations

from dataclasses import dataclass

# ReSpeaker 2-Mic HAT (both revisions) exposes 3 APA102 RGB LEDs and a user
# button on GPIO17 (BCM).
RESPEAKER_LED_COUNT = 3
RESPEAKER_BUTTON_GPIO = 17

# GPIO backend identifiers.
# NB: RPi.GPIO edge detection is broken on kernels >= ~6.6 (it needed the
# removed sysfs GPIO interface; symptom: "RuntimeError: Failed to add edge
# detection"), so lgpio is the default on every profile. The rpi backend
# remains only for legacy kernels.
GPIO_RPI = "rpi"      # RPi.GPIO — legacy kernels (< 6.6) only
GPIO_LGPIO = "lgpio"  # lgpio — works on Pi 4 and Pi 5 (RP1) with current kernels
GPIO_MOCK = "mock"    # keyboard/no-op — development off-Pi


@dataclass(frozen=True)
class HardwareProfile:
    """Static hardware defaults for a supported device combination."""

    name: str
    led_count: int
    spi_bus: int
    spi_device: int
    gpio_backend: str
    button_gpio: int
    # Human-readable hint used only in logs / diagnostics.
    audio_hint: str


PROFILES: dict[str, HardwareProfile] = {
    "pi4-respeaker-v1": HardwareProfile(
        name="pi4-respeaker-v1",
        led_count=RESPEAKER_LED_COUNT,
        spi_bus=0,
        spi_device=1,
        gpio_backend=GPIO_LGPIO,
        button_gpio=RESPEAKER_BUTTON_GPIO,
        audio_hint="WM8960 via wm8960-soundcard overlay (ALSA card 'seeed2micvoicec')",
    ),
    "pi5-respeaker-v2": HardwareProfile(
        name="pi5-respeaker-v2",
        led_count=RESPEAKER_LED_COUNT,
        # The HAT wires the APA102 to SPI0 CE1. On some Pi 5 kernels this
        # enumerates as /dev/spidev10.1 instead of /dev/spidev0.1 — override
        # leds.spi_bus: 10 in config if so (see docs/hardware/pi5-respeaker-v2.md).
        spi_bus=0,
        spi_device=1,
        gpio_backend=GPIO_LGPIO,
        button_gpio=RESPEAKER_BUTTON_GPIO,
        audio_hint="TLV320AIC3104 via device-tree overlay",
    ),
    "mock": HardwareProfile(
        name="mock",
        led_count=RESPEAKER_LED_COUNT,
        spi_bus=0,
        spi_device=1,
        gpio_backend=GPIO_MOCK,
        button_gpio=RESPEAKER_BUTTON_GPIO,
        audio_hint="no hardware (development)",
    ),
}

DEFAULT_PROFILE = "pi5-respeaker-v2"


def get_profile(name: str) -> HardwareProfile:
    """Return the profile for ``name`` or raise a clear error."""
    try:
        return PROFILES[name]
    except KeyError:
        supported = ", ".join(sorted(PROFILES))
        raise ValueError(
            f"Unknown hardware_profile {name!r}. Supported: {supported}"
        ) from None
