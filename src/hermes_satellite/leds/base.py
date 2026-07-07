"""LED abstractions.

Two abstractions keep hardware swappable by config alone:

* :class:`LEDBackend` — writes raw pixel frames to a device (APA102 or a mock).
* :class:`LEDController` — accepts high-level :class:`LEDState` changes and
  animates them.

The animation logic is shared (see ``controller.py``); only the backend differs
between real hardware and development.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from typing import List, Tuple

# An (r, g, b) tuple, each channel 0-255.
Color = Tuple[int, int, int]


class LEDState(Enum):
    """Visual states the LEDs can display."""

    IDLE = "idle"            # slow breathing
    WAKE = "wake"            # solid, wake acknowledged
    RECORDING = "recording"  # solid, capturing speech
    PROCESSING = "processing"  # spinner while STT/agent work
    SPEAKING = "speaking"    # pulsing while TTS plays
    MUTED = "muted"          # dim solid red — mic input ignored
    ERROR = "error"          # pulsing red
    OFF = "off"              # all LEDs off

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


class LEDBackend(ABC):
    """Writes pixel frames to a physical or virtual LED strip."""

    def __init__(self, num_leds: int):
        self.num_leds = num_leds

    @abstractmethod
    def set_frame(self, colors: List[Color]) -> None:
        """Set and display a full frame of ``num_leds`` colours."""

    @abstractmethod
    def set_global_brightness(self, brightness: int) -> None:
        """Set the hardware global brightness (0-31 for APA102)."""

    @abstractmethod
    def clear(self) -> None:
        """Turn all LEDs off."""

    @abstractmethod
    def close(self) -> None:
        """Release any hardware resources."""


class LEDController(ABC):
    """High-level LED control driven by pipeline state."""

    @abstractmethod
    def start(self) -> None:
        """Begin animating."""

    @abstractmethod
    def stop(self) -> None:
        """Stop animating and turn the LEDs off."""

    @abstractmethod
    def set_state(self, state: LEDState) -> None:
        """Change the displayed state."""

    @abstractmethod
    def set_brightness(self, brightness: int) -> None:
        """Adjust global brightness (0-31)."""
