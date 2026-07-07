"""Wake word detection."""

from .base import WakeWordDetector

__all__ = ["WakeWordDetector", "build_wakeword"]


def build_wakeword(config, demo: bool = False, mic=None) -> WakeWordDetector:
    """Construct the configured wake word detector.

    Demo mode uses a mock. Otherwise ``wakeword.backend`` selects the engine:
    ``openwakeword`` (default, free) or ``porcupine`` (paid Picovoice key).
    Both read from the shared ``mic`` stream (also used by capture).
    """
    if demo:
        from .mock_backend import MockWakeWord

        return MockWakeWord()
    backend = config.wakeword.backend.lower()
    if backend == "openwakeword":
        from .openwakeword_backend import OpenWakeWord

        return OpenWakeWord(
            config.wakeword, mic=mic, sample_rate=config.audio.sample_rate
        )
    if backend == "porcupine":
        from .porcupine_backend import PorcupineWakeWord

        return PorcupineWakeWord(
            config.wakeword, mic=mic, sample_rate=config.audio.sample_rate
        )
    raise ValueError(
        f"Unknown wakeword.backend: {config.wakeword.backend!r} "
        "(openwakeword | porcupine)"
    )
