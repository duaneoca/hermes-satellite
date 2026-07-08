"""ALSA (PortAudio) audio capture and playback.

Capture is VAD-gated: after the wake word, we wait up to
``audio.speech_timeout_seconds`` for speech onset, keep a short pre-roll so the
first syllable isn't clipped, then record until ``audio.silence_ms`` of
trailing silence or ``audio.max_record_seconds``.

The mic stream is shared with the wake word detector (see ``audio/mic.py``).
While muted, capture returns immediately with empty bytes.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from typing import Callable, Optional

from ..config import AudioConfig
from .base import AudioSink, AudioSource
from .mic import MicStream
from .vad import VoiceActivityDetector

logger = logging.getLogger(__name__)

# webrtcvad accepts 10/20/30 ms frames; 30 ms keeps the loop cheap.
FRAME_MS = 30
# Audio kept from just before speech onset so the first syllable isn't lost.
PRE_ROLL_MS = 300


class AlsaAudioSource(AudioSource):
    def __init__(
        self,
        config: AudioConfig,
        mic: Optional[MicStream] = None,
        vad: Optional[VoiceActivityDetector] = None,
    ):
        self._config = config
        self._mic = mic or MicStream(
            sample_rate=config.sample_rate,
            device=config.input_device,
            channels=config.input_channels,
        )
        # Constructed lazily so importing this module never requires webrtcvad.
        self._vad = vad

    def _get_vad(self) -> VoiceActivityDetector:
        if self._vad is None:
            self._vad = VoiceActivityDetector(
                self._config.vad_aggressiveness, self._config.sample_rate
            )
        return self._vad

    def capture_utterance(
        self, is_muted: Callable[[], bool], onset_timeout=None
    ) -> bytes:
        cfg = self._config
        vad = self._get_vad()
        samples_per_frame = cfg.sample_rate * FRAME_MS // 1000
        self._mic.start()
        onset = (
            onset_timeout if onset_timeout is not None
            else cfg.speech_timeout_seconds
        )

        # Phase 1: wait for speech onset (keeping a pre-roll).
        pre_roll: deque = deque(maxlen=max(1, PRE_ROLL_MS // FRAME_MS))
        deadline = time.monotonic() + onset
        frame = b""
        while True:
            if is_muted():
                return b""
            if time.monotonic() >= deadline:
                logger.info("no speech within %.1fs", onset)
                return b""
            frame = self._mic.read(samples_per_frame)
            if vad.is_speech(frame):
                break
            pre_roll.append(frame)

        # Phase 2: record until trailing silence or the hard cap.
        voiced = list(pre_roll)
        voiced.append(frame)
        silence_ms = 0
        start = time.monotonic()
        while (
            silence_ms < cfg.silence_ms
            and time.monotonic() - start < cfg.max_record_seconds
        ):
            if is_muted():
                break
            frame = self._mic.read(samples_per_frame)
            voiced.append(frame)
            silence_ms = 0 if vad.is_speech(frame) else silence_ms + FRAME_MS
        audio = b"".join(voiced)
        logger.info("captured %.2fs of audio", len(audio) / 2 / cfg.sample_rate)
        return audio


class AlsaAudioSink(AudioSink):
    def __init__(self, config: AudioConfig):
        self._config = config

    def play(self, pcm: bytes, sample_rate: Optional[int] = None) -> None:
        if not pcm:
            return
        import sounddevice as sd  # lazy

        rate = sample_rate or self._config.sample_rate
        # Context manager starts the stream and, on exit, drains pending
        # buffers before closing — playback is complete when this returns.
        with sd.RawOutputStream(
            samplerate=rate,
            device=self._config.output_device,
            channels=1,
            dtype="int16",
        ) as out:
            out.write(pcm)
