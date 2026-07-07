"""Mock TTS for demo/testing: returns silence sized to the text."""

from __future__ import annotations

from .base import TTSEngine


class MockTTS(TTSEngine):
    def __init__(self, sample_rate: int = 16000, chars_per_second: float = 15.0):
        self._sample_rate = sample_rate
        self._cps = chars_per_second

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    def synthesize(self, text: str) -> bytes:
        seconds = max(0.5, len(text) / self._cps)
        n_samples = int(self._sample_rate * seconds)
        return b"\x00\x00" * n_samples
