"""Speech-to-text engine abstraction.

Swapping STT implementations is a config change (``stt.backend``), not a code
change — all engines implement this interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class STTEngine(ABC):
    """Transcribes 16-bit mono PCM audio to text."""

    @abstractmethod
    def transcribe(self, audio: bytes) -> str:
        """Return the transcript of ``audio`` (16-bit mono PCM @ 16 kHz)."""
