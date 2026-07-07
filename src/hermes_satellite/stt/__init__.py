"""Speech-to-text."""

from .base import STTEngine

__all__ = ["STTEngine", "build_stt"]


def build_stt(config, demo: bool = False) -> STTEngine:
    """Construct the STT engine for the configured backend."""
    backend = "mock" if demo else config.stt.backend.lower()
    if backend == "mock":
        from .mock_backend import MockSTT

        return MockSTT()
    if backend == "moonshine":
        from .moonshine_backend import MoonshineSTT

        return MoonshineSTT(config.stt, sample_rate=config.audio.sample_rate)
    raise ValueError(f"Unknown stt.backend: {config.stt.backend!r} (moonshine | mock)")
