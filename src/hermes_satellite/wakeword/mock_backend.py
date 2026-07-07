"""Mock wake word detector for demo/testing.

Fires a wake every few seconds of unmuted time, so the full pipeline can be
exercised without Porcupine or a microphone. Honours mute and stop.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable

logger = logging.getLogger(__name__)

from .base import WakeWordDetector


class MockWakeWord(WakeWordDetector):
    def __init__(self, interval: float = 3.0):
        self._interval = interval
        self._stop = threading.Event()

    def wait_for_wake(self, is_muted: Callable[[], bool]) -> bool:
        waited = 0.0
        step = 0.1
        while not self._stop.is_set():
            self._stop.wait(step)
            if self._stop.is_set():
                return False
            if is_muted():
                waited = 0.0  # muted time doesn't count toward a wake
                continue
            waited += step
            if waited >= self._interval:
                logger.info("mock wake word detected")
                return True
        return False

    def stop(self) -> None:
        self._stop.set()
