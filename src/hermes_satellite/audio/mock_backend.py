"""Mock audio source/sink for demo/testing (no ALSA hardware)."""

from __future__ import annotations

import logging
import time
from typing import Callable, Optional

from ..config import AudioConfig
from .base import AudioSink, AudioSource

logger = logging.getLogger(__name__)


class MockAudioSource(AudioSource):
    def __init__(self, config: AudioConfig, capture_seconds: float = 1.0):
        self._config = config
        self._seconds = capture_seconds

    def capture_utterance(self, is_muted: Callable[[], bool]) -> bytes:
        if is_muted():
            return b""
        logger.info("mock capture: %.1fs of silence", self._seconds)
        time.sleep(self._seconds)
        # 16-bit mono PCM of silence for the requested duration.
        n_samples = int(self._config.sample_rate * self._seconds)
        return b"\x00\x00" * n_samples


class MockAudioSink(AudioSink):
    def __init__(self, config: AudioConfig):
        self._config = config

    def play(self, pcm: bytes, sample_rate: Optional[int] = None) -> None:
        rate = sample_rate or self._config.sample_rate
        seconds = len(pcm) / 2 / rate
        logger.info("mock playback: %.1fs @ %d Hz", seconds, rate)
        time.sleep(min(seconds, 2.0))
