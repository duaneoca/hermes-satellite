"""Text-to-speech engine abstraction."""

from __future__ import annotations

from abc import ABC, abstractmethod


class TTSEngine(ABC):
    """Synthesizes text into 16-bit mono PCM audio."""

    @abstractmethod
    def synthesize(self, text: str) -> bytes:
        """Return 16-bit mono PCM for ``text`` at :attr:`sample_rate`."""

    @property
    def sample_rate(self) -> int:
        """Sample rate of the PCM returned by :meth:`synthesize`.

        Engines with a fixed native rate (e.g. a Piper voice) override this;
        the value is only reliable after the first :meth:`synthesize` call.
        """
        return 16000
