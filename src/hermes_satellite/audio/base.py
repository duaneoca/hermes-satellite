"""Audio source/sink abstractions.

Audio is exchanged as raw 16-bit little-endian mono PCM at the configured
sample rate (16 kHz for the Porcupine/Moonshine pipeline).
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from typing import Callable, Optional


class AudioSource(ABC):
    """Captures a single spoken utterance, VAD-gated."""

    @abstractmethod
    def capture_utterance(
        self,
        is_muted: Callable[[], bool],
        onset_timeout: Optional[float] = None,
        on_frame: Optional[Callable[[bytes], None]] = None,
    ) -> bytes:
        """Capture until end-of-speech and return 16-bit PCM.

        ``onset_timeout`` overrides how long to wait for speech to *begin*
        before giving up (``None`` = the configured ``speech_timeout_seconds``);
        follow-up mode passes a short window. Returns empty bytes if muted
        throughout or if no speech starts within the window.

        ``on_frame`` (streaming STT): called in order with every frame that
        becomes part of the utterance — the pre-roll once onset fires, then
        each subsequent frame — so a transcription session can run while the
        user is still speaking.
        """


class AudioSink(ABC):
    """Plays back 16-bit PCM audio."""

    @abstractmethod
    def play(
        self,
        pcm: bytes,
        sample_rate: Optional[int] = None,
        cancel: Optional[threading.Event] = None,
    ) -> None:
        """Play ``pcm`` (blocking until playback completes).

        ``sample_rate`` is the rate of ``pcm`` when it differs from the
        configured pipeline rate (e.g. a Piper voice's native rate); ``None``
        means the configured ``audio.sample_rate``. If ``cancel`` is given
        and becomes set, playback stops as soon as possible (barge-in) and
        the method returns without the usual drain hold.
        """
