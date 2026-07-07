"""Voice activity detection via webrtcvad.

Wraps ``webrtcvad.Vad`` behind a one-method interface so the capture logic in
``alsa_backend.py`` can be tested with a fake. webrtcvad accepts only
10/20/30 ms frames of 16-bit mono PCM at 8/16/32/48 kHz — the capture loop
feeds 30 ms frames at the pipeline's 16 kHz.
"""

from __future__ import annotations


class VoiceActivityDetector:
    def __init__(self, aggressiveness: int = 2, sample_rate: int = 16000):
        import webrtcvad  # lazy: not needed when tests inject a fake

        if not 0 <= aggressiveness <= 3:
            raise ValueError("vad_aggressiveness must be 0-3")
        self._vad = webrtcvad.Vad(aggressiveness)
        self._sample_rate = sample_rate

    def is_speech(self, frame: bytes) -> bool:
        """True if ``frame`` (10/20/30 ms of int16 mono PCM) contains speech."""
        return self._vad.is_speech(frame, self._sample_rate)
