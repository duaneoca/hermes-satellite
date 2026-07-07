"""Mock STT for demo/testing: returns a canned transcript."""

from __future__ import annotations

from .base import STTEngine


class MockSTT(STTEngine):
    def __init__(self, transcript: str = "what time is it"):
        self._transcript = transcript

    def transcribe(self, audio: bytes) -> str:
        return self._transcript
