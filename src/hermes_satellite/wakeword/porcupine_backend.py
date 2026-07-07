"""Porcupine wake word backend.

Reads ``frame_length`` int16 samples at a time from the shared
:class:`~hermes_satellite.audio.mic.MicStream` and feeds them to
``pvporcupine``. While muted, frames are still drained from the device (so the
stream doesn't back up) but never processed — no wake can occur.

Either a custom ``.ppn`` (``wakeword.model_path``) or one of Porcupine's
built-in keywords (``wakeword.builtin_keyword``, e.g. "computer", "jarvis") is
used; the built-in path is handy for testing before a custom wake word is
trained. Requires a Picovoice AccessKey (``PORCUPINE_ACCESS_KEY``).
"""

from __future__ import annotations

import logging
import struct
import threading
from typing import Callable, Optional

from ..config import WakeWordConfig
from ..audio.mic import MicStream
from .base import WakeWordDetector

logger = logging.getLogger(__name__)


class PorcupineWakeWord(WakeWordDetector):
    def __init__(
        self,
        config: WakeWordConfig,
        mic: Optional[MicStream] = None,
        sample_rate: int = 16000,
    ):
        if mic is None:
            raise ValueError("PorcupineWakeWord requires a shared MicStream")
        self._config = config
        self._mic = mic
        self._sample_rate = sample_rate
        self._stop_event = threading.Event()
        self._handle = None
        self._unpack = None

    def _get_handle(self):
        if self._handle is None:
            import pvporcupine  # lazy

            if not self._config.access_key:
                raise RuntimeError(
                    "Porcupine needs an AccessKey: set wakeword.access_key or "
                    "the PORCUPINE_ACCESS_KEY environment variable "
                    "(see docs/porcupine.md)"
                )
            kwargs = dict(
                access_key=self._config.access_key,
                sensitivities=[self._config.sensitivity],
            )
            if self._config.model_path:
                kwargs["keyword_paths"] = [self._config.model_path]
            else:
                kwargs["keywords"] = [self._config.builtin_keyword]
            self._handle = pvporcupine.create(**kwargs)
            if self._handle.sample_rate != self._sample_rate:
                raise RuntimeError(
                    f"Porcupine wants {self._handle.sample_rate} Hz but "
                    f"audio.sample_rate is {self._sample_rate}"
                )
            self._unpack = struct.Struct(
                "<%dh" % self._handle.frame_length
            ).unpack_from
            logger.info(
                "porcupine ready: %s (sensitivity %.2f, frame %d)",
                self._config.model_path or self._config.builtin_keyword,
                self._config.sensitivity,
                self._handle.frame_length,
            )
        return self._handle

    def wait_for_wake(self, is_muted: Callable[[], bool]) -> bool:
        handle = self._get_handle()
        frame_length = handle.frame_length
        self._mic.start()
        self._mic.flush()  # never process stale audio (e.g. our own TTS)
        while not self._stop_event.is_set():
            pcm = self._mic.read(frame_length)
            if is_muted():
                continue  # drain the device but ignore audio entirely
            if handle.process(self._unpack(pcm)) >= 0:
                logger.info("wake word detected")
                return True
        return False

    def stop(self) -> None:
        self._stop_event.set()

    def close(self) -> None:
        """Release the Porcupine handle (call after the wait loop has exited)."""
        if self._handle is not None:
            self._handle.delete()
            self._handle = None
