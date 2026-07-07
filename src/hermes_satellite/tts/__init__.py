"""Text-to-speech."""

from .base import TTSEngine

__all__ = ["TTSEngine", "build_tts"]


def build_tts(config, demo: bool = False) -> TTSEngine:
    """Construct the TTS engine for the configured backend."""
    backend = "mock" if demo else config.tts.backend.lower()
    if backend == "mock":
        from .mock_backend import MockTTS

        return MockTTS(sample_rate=config.audio.sample_rate)
    if backend == "piper":
        from .piper_backend import PiperTTS

        return PiperTTS(config.tts, sample_rate=config.audio.sample_rate)
    raise ValueError(f"Unknown tts.backend: {config.tts.backend!r} (piper | mock)")
