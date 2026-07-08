"""Piper TTS backend.

Voice selection is **name-first**: set ``tts.voice`` to a catalog name (e.g.
``en_GB-northern_english_male-medium``) and the voice is auto-downloaded into
``tts.voices_dir`` on first use via piper's own downloader. An explicit
``tts.voice_path`` overrides the name (for custom/fine-tuned voices).

Synthesis knobs (``speaker_id`` for multi-speaker voices like vctk/aru,
``length_scale`` for pacing, ``volume``) are passed through piper's
``SynthesisConfig`` on the current API and as best-effort kwargs on the
classic API.

Supports both published Piper Python APIs (the package changed shape between
releases; which one you get depends on the ``piper-tts`` version that installs
on the Pi's Python):

* classic (<= 1.2): ``PiperVoice.load(path)`` then
  ``voice.synthesize_stream_raw(text)`` yielding raw int16 PCM chunks, with the
  rate at ``voice.config.sample_rate``;
* current (piper1-gpl, >= 1.3): ``voice.synthesize(text)`` yielding
  ``AudioChunk`` objects carrying ``audio_int16_bytes`` and ``sample_rate``.

The voice model defines the true output rate; :attr:`sample_rate` reports it
after loading so the audio sink opens playback at the right rate.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..config import TTSConfig
from .base import TTSEngine

logger = logging.getLogger(__name__)


def resolve_voice_path(config: TTSConfig) -> str:
    """Resolve the .onnx path for the configured voice, downloading if needed."""
    if config.voice_path:
        return config.voice_path
    if not config.voice:
        raise RuntimeError(
            "No TTS voice configured: set tts.voice to a catalog name "
            "(browse with 'hermes-satellite voices list') or tts.voice_path "
            "to a .onnx file"
        )
    path = Path(config.voices_dir) / f"{config.voice}.onnx"
    if not path.exists():
        try:
            from piper.download_voices import download_voice  # piper >= 1.3
        except ImportError as exc:
            raise RuntimeError(
                f"Voice {config.voice!r} is not at {path} and this piper-tts "
                "version cannot auto-download; fetch the .onnx and .onnx.json "
                "manually (see docs/piper.md) or upgrade piper-tts"
            ) from exc
        path.parent.mkdir(parents=True, exist_ok=True)
        logger.info("downloading piper voice %s -> %s", config.voice, path.parent)
        download_voice(config.voice, path.parent)
    return str(path)


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

            voice_path = resolve_voice_path(self._config)
            self._voice = PiperVoice.load(voice_path)
            config = getattr(self._voice, "config", None)
            rate = getattr(config, "sample_rate", None)
            if rate:
                self._rate = int(rate)
            logger.info("piper ready: %s (%d Hz)", voice_path, self._rate)
        return self._voice

    @property
    def sample_rate(self) -> int:
        return self._rate

    def reload(self) -> None:
        """Drop the loaded voice; the next synthesize picks up config changes
        (e.g. a new tts.voice set at runtime)."""
        self._voice = None

    def _knob_kwargs(self) -> dict:
        cfg = self._config
        if cfg.speaker_id is None and cfg.length_scale is None and cfg.volume == 1.0:
            return {}
        try:
            from piper import SynthesisConfig  # current API only
        except ImportError:
            return {}
        return {
            "syn_config": SynthesisConfig(
                speaker_id=cfg.speaker_id,
                length_scale=cfg.length_scale,
                volume=cfg.volume,
            )
        }

    def synthesize(self, text: str) -> bytes:
        voice = self._get_voice()
        cfg = self._config
        if hasattr(voice, "synthesize_stream_raw"):
            # classic API: yields raw int16 PCM bytes; knobs are kwargs here
            kwargs = {}
            if cfg.speaker_id is not None:
                kwargs["speaker_id"] = cfg.speaker_id
            if cfg.length_scale is not None:
                kwargs["length_scale"] = cfg.length_scale
            try:
                return b"".join(voice.synthesize_stream_raw(text, **kwargs))
            except TypeError:  # very old signature without these kwargs
                return b"".join(voice.synthesize_stream_raw(text))
        # current API: yields AudioChunk objects
        chunks = list(voice.synthesize(text, **self._knob_kwargs()))
        if chunks and hasattr(chunks[0], "audio_int16_bytes"):
            self._rate = int(chunks[0].sample_rate)
            return b"".join(chunk.audio_int16_bytes for chunk in chunks)
        return b"".join(chunks)  # already raw bytes
