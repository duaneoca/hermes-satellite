"""Piper TTS backend.

Supports both published Piper Python APIs (the package changed shape between
releases; which one you get depends on the ``piper-tts`` version that installs
on the Pi's Python):

* classic (<= 1.2): ``PiperVoice.load(path)`` then
  ``voice.synthesize_stream_raw(text)`` yielding raw int16 PCM chunks, with the
  rate at ``voice.config.sample_rate``;
* current (piper1-gpl, >= 1.3): ``voice.synthesize(text)`` yielding
  ``AudioChunk`` objects carrying ``audio_int16_bytes`` and ``sample_rate``.

The voice model (``tts.voice_path`` -> ``voice.onnx`` + ``voice.onnx.json``)
defines the true output rate; :attr:`sample_rate` reports it after loading so
the audio sink opens playback at the right rate (no resampling needed).
"""

from __future__ import annotations

import logging

from ..config import TTSConfig
from .base import TTSEngine

logger = logging.getLogger(__name__)


class PiperTTS(TTSEngine):
    def __init__(self, config: TTSConfig, sample_rate: int = 16000):
        self._config = config
        self._rate = sample_rate  # replaced by the voice's native rate on load
        self._voice = None

    def _get_voice(self):
        if self._voice is None:
            try:
                from piper.voice import PiperVoice  # classic layout
            except ImportError:
                from piper import PiperVoice  # current layout

            self._voice = PiperVoice.load(self._config.voice_path)
            config = getattr(self._voice, "config", None)
            rate = getattr(config, "sample_rate", None)
            if rate:
                self._rate = int(rate)
            logger.info(
                "piper ready: %s (%d Hz)", self._config.voice_path, self._rate
            )
        return self._voice

    @property
    def sample_rate(self) -> int:
        return self._rate

    def synthesize(self, text: str) -> bytes:
        voice = self._get_voice()
        if hasattr(voice, "synthesize_stream_raw"):
            # classic API: yields raw int16 PCM bytes
            return b"".join(voice.synthesize_stream_raw(text))
        # current API: yields AudioChunk objects
        chunks = list(voice.synthesize(text))
        if chunks and hasattr(chunks[0], "audio_int16_bytes"):
            self._rate = int(chunks[0].sample_rate)
            return b"".join(chunk.audio_int16_bytes for chunk in chunks)
        return b"".join(chunks)  # already raw bytes
