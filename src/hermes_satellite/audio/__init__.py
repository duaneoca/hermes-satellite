"""Audio capture and playback."""

from .base import AudioSink, AudioSource

__all__ = ["AudioSource", "AudioSink", "build_audio"]


def build_audio(config, demo: bool = False, mic=None):
    """Return ``(AudioSource, AudioSink)`` for the configured backend.

    ``mic`` is the shared :class:`~hermes_satellite.audio.mic.MicStream` also
    used by wake detection. The ``mock`` backend (or demo mode) needs no ALSA
    hardware.
    """
    backend = "mock" if demo else config.audio.backend.lower()
    if backend == "mock":
        from .mock_backend import MockAudioSink, MockAudioSource

        return MockAudioSource(config.audio), MockAudioSink(config.audio)
    if backend == "alsa":
        from .alsa_backend import AlsaAudioSink, AlsaAudioSource

        return AlsaAudioSource(config.audio, mic=mic), AlsaAudioSink(config.audio)
    raise ValueError(f"Unknown audio.backend: {config.audio.backend!r} (alsa | mock)")
