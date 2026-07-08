"""HAT user button -> microphone mute toggle.

The ReSpeaker 2-Mic HAT has a user button on GPIO17 (BCM), active-low with a
pull-up. Each press toggles a thread-safe mute state owned by :class:`Mute`.
While muted, the wakeword detector and audio capture ignore all microphone
input.

GPIO access differs by platform, so the button backend is selected from the
hardware profile:

* Pi 4 (and earlier): ``RPi.GPIO``
* Pi 5 (RP1): ``lgpio`` — ``RPi.GPIO`` is incompatible
* off-Pi development: a mock backend that toggles on Enter from stdin

All GPIO libraries are imported lazily so this module imports anywhere.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, List

from .platform import GPIO_LGPIO, GPIO_MOCK, GPIO_RPI, HardwareProfile

logger = logging.getLogger(__name__)

MuteListener = Callable[[bool], None]


class Mute:
    """Thread-safe mute flag with change notifications."""

    def __init__(self) -> None:
        self._muted = False
        self._lock = threading.Lock()
        self._listeners: List[MuteListener] = []

    def is_muted(self) -> bool:
        with self._lock:
            return self._muted

    def subscribe(self, listener: MuteListener) -> None:
        self._listeners.append(listener)

    def toggle(self) -> bool:
        with self._lock:
            self._muted = not self._muted
            muted = self._muted
        self._notify(muted)
        return muted

    def set(self, muted: bool) -> None:
        """Set an absolute mute state (e.g. from MQTT); no-op if unchanged."""
        with self._lock:
            if self._muted == muted:
                return
            self._muted = muted
        self._notify(muted)

    def _notify(self, muted: bool) -> None:
        logger.info("microphone %s", "MUTED" if muted else "unmuted")
        for listener in list(self._listeners):
            try:
                listener(muted)
            except Exception:  # pragma: no cover - listener isolation
                logger.exception("mute listener failed")


class Button:
    """Base class: a watcher that calls ``on_press`` when the button is pressed."""

    def __init__(self, gpio: int, on_press: Callable[[], None]):
        self._gpio = gpio
        self._on_press = on_press

    def start(self) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    def stop(self) -> None:  # pragma: no cover - overridden
        pass


class RpiGpioButton(Button):
    """Pi 4 / RPi.GPIO backend."""

    _DEBOUNCE_MS = 300

    def start(self) -> None:
        import RPi.GPIO as GPIO  # lazy

        self._GPIO = GPIO
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(self._gpio, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        GPIO.add_event_detect(
            self._gpio,
            GPIO.FALLING,
            callback=lambda _ch: self._on_press(),
            bouncetime=self._DEBOUNCE_MS,
        )
        logger.info("button on GPIO%d via RPi.GPIO", self._gpio)

    def stop(self) -> None:
        try:
            self._GPIO.remove_event_detect(self._gpio)
            self._GPIO.cleanup(self._gpio)
        except Exception:  # pragma: no cover
            logger.debug("RPi.GPIO cleanup failed", exc_info=True)


class LgpioButton(Button):
    """Pi 5 / lgpio backend (RP1)."""

    _DEBOUNCE_US = 300_000
    # The RP1 header enumerates as gpiochip0 on current Raspberry Pi OS; some
    # earlier kernels used gpiochip4. Try both.
    _CHIPS = (0, 4)

    def start(self) -> None:
        import lgpio  # lazy

        self._lgpio = lgpio
        last_err = None
        for chip in self._CHIPS:
            try:
                self._handle = lgpio.gpiochip_open(chip)
                break
            except Exception as exc:  # pragma: no cover - hardware dependent
                last_err = exc
        else:  # pragma: no cover
            raise RuntimeError(f"could not open a gpiochip {self._CHIPS}: {last_err}")

        lgpio.gpio_claim_alert(
            self._handle, self._gpio, lgpio.FALLING_EDGE, lgpio.SET_PULL_UP
        )
        lgpio.gpio_set_debounce_micros(self._handle, self._gpio, self._DEBOUNCE_US)
        self._cb = lgpio.callback(
            self._handle, self._gpio, lgpio.FALLING_EDGE, lambda *_: self._on_press()
        )
        logger.info("button on GPIO%d via lgpio", self._gpio)

    def stop(self) -> None:
        try:
            self._cb.cancel()
            self._lgpio.gpiochip_close(self._handle)
        except Exception:  # pragma: no cover
            logger.debug("lgpio cleanup failed", exc_info=True)


class MockButton(Button):
    """Development backend: press Enter on stdin to toggle mute."""

    def __init__(self, gpio: int, on_press: Callable[[], None]):
        super().__init__(gpio, on_press)
        self._stop = threading.Event()
        self._thread: threading.Thread = None  # type: ignore[assignment]

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._watch_stdin, name="mock-button", daemon=True
        )
        self._thread.start()
        logger.info("mock button: press Enter to toggle mute")

    def _watch_stdin(self) -> None:
        import sys

        while not self._stop.is_set():
            line = sys.stdin.readline()
            if not line:  # EOF (no interactive stdin)
                return
            self._on_press()

    def stop(self) -> None:
        self._stop.set()


def build_button(profile: HardwareProfile, on_press: Callable[[], None]) -> Button:
    """Construct the button watcher for the hardware profile."""
    backend = profile.gpio_backend
    if backend == GPIO_RPI:
        return RpiGpioButton(profile.button_gpio, on_press)
    if backend == GPIO_LGPIO:
        return LgpioButton(profile.button_gpio, on_press)
    if backend == GPIO_MOCK:
        return MockButton(profile.button_gpio, on_press)
    raise ValueError(f"Unknown gpio_backend: {backend!r}")  # pragma: no cover
