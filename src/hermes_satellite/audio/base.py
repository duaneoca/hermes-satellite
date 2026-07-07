"""Audio source/sink abstractions.

Audio is exchanged as raw 16-bit little-endian mono PCM at the configured
sample rate (16 kHz for the Porcupine/Moonshine pipeline).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable, Optional


class AudioSource(ABC):
    """Captures a single spoken utterance, VAD-gated."""

    @abstractmethod
    def capture_utterance(self, is_muted: Callable[[], bool]) -> bytes:
        """Capture until end-of-speech and return 16-bit PCM.

        Returns empty bytes if muted throughout or if no speech is detected.
        """


class AudioSink(ABC):
    """Plays back 16-bit PCM audio."""

    @abstractmethod
    def play(self, pcm: bytes, sample_rate: Optional[int] = None) -> None:
        """Play ``pcm`` (blocking until playback completes).

        ``sample_rate`` is the rate of ``pcm`` when it differs from the
        configured pipeline rate (e.g. a Piper voice's native rate); ``None``
        means the configured ``audio.sample_rate``.
        """
