"""Speech-to-text engine abstraction.

Swapping STT implementations is a config change (``stt.backend``), not a code
change — all engines implement this interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class STTEngine(ABC):
    """Transcribes 16-bit mono PCM audio to text."""

    def start_session(self):
        """Return an :class:`STTSession` for capture-time streaming, or None
        when this engine (or its configuration) doesn't support it — callers
        then use batch :meth:`transcribe` after capture."""
        return None

    @abstractmethod
    def transcribe(self, audio: bytes) -> str:
        """Return the transcript of ``audio`` (16-bit mono PCM @ 16 kHz)."""


class STTSession(ABC):
    """A single-utterance streaming transcription session.

    ``feed()`` receives 16-bit PCM frames while the user is still speaking;
    ``finish()`` returns the final transcript (near-instant — the audio has
    already been processed). ``abort()`` discards a session whose capture
    produced no speech.
    """

    @abstractmethod
    def feed(self, pcm: bytes) -> None: ...

    @abstractmethod
    def finish(self) -> str: ...

    @abstractmethod
    def abort(self) -> None: ...
