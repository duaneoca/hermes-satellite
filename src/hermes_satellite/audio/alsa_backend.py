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
# Consecutive speech frames required before recording starts. A single loud
# frame is not speech: the WM8960's output-stage pop (~15 ms, full scale) and
# similar clicks read as one "speech" frame to webrtcvad, which used to open
# recording instantly and burn the whole follow-up window on silence.
ONSET_FRAMES = 3  # 90 ms
# Minimum voiced audio for a capture to count as an utterance. If recording
# ends (trailing silence) having heard less speech than this, the onset was a
# false trigger (residual chime tail / click that slipped past the debounce)
# — re-arm and keep waiting instead of returning a click as the utterance.
# Field symptom without this: at silence_ms 200, capture ended before the
# user could start talking. Short real words ("no", "stop") run ~250 ms+.
MIN_SPEECH_MS = 210  # 7 frames


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
        self, is_muted: Callable[[], bool], onset_timeout=None, on_frame=None
    ) -> bytes:
        cfg = self._config
        vad = self._get_vad()
        samples_per_frame = cfg.sample_rate * FRAME_MS // 1000
        self._mic.start()
        onset = (
            onset_timeout if onset_timeout is not None
            else cfg.speech_timeout_seconds
        )

        started = time.monotonic()
        deadline = started + onset

        while True:  # onset attempts; a false onset re-arms within deadline
            # Phase 1: wait for speech onset (keeping a pre-roll). Onset
            # means ONSET_FRAMES consecutive speech frames — the frames
            # leading up to it stay in the pre-roll, so nothing is clipped.
            pre_roll: deque = deque(maxlen=max(1, PRE_ROLL_MS // FRAME_MS))
            frame = b""
            consecutive = 0
            while True:
                if is_muted():
                    return b""
                if time.monotonic() >= deadline:
                    logger.info("no speech within %.1fs", onset)
                    return b""
                frame = self._mic.read(samples_per_frame)
                if vad.is_speech(frame):
                    consecutive += 1
                    if consecutive >= ONSET_FRAMES:
                        break
                else:
                    consecutive = 0
                pre_roll.append(frame)
            logger.debug(
                "speech onset after %.2fs", time.monotonic() - started
            )

            # Phase 2: record until trailing silence or the hard cap.
            voiced = list(pre_roll)
            voiced.append(frame)
            if on_frame is not None:
                for buffered in voiced:  # pre-roll + onset, in order
                    on_frame(buffered)
            speech_ms = ONSET_FRAMES * FRAME_MS
            silence_ms = 0
            muted_out = capped = False
            start = time.monotonic()
            while silence_ms < cfg.silence_ms:
                if time.monotonic() - start >= cfg.max_record_seconds:
                    capped = True
                    break
                if is_muted():
                    muted_out = True
                    break
                frame = self._mic.read(samples_per_frame)
                voiced.append(frame)
                if on_frame is not None:
                    on_frame(frame)
                if vad.is_speech(frame):
                    speech_ms += FRAME_MS
                    silence_ms = 0
                else:
                    silence_ms += FRAME_MS

            # Mute and the hard cap end the utterance unconditionally; the
            # minimum-speech gate only applies to trailing-silence endings.
            if speech_ms < MIN_SPEECH_MS and not (muted_out or capped):
                # A click, not an utterance. Re-arm within the original
                # window so the user still gets their chance to talk. (A
                # streaming STT session already received these frames —
                # harmless: a click transcribes to nothing.)
                logger.debug(
                    "false onset (%d ms of speech) — re-arming", speech_ms
                )
                continue
            audio = b"".join(voiced)
            logger.info(
                "captured %.2fs of audio", len(audio) / 2 / cfg.sample_rate
            )
            return audio


class AlsaAudioSink(AudioSink):
    def __init__(self, config: AudioConfig):
        self._config = config

    def play(
        self,
        pcm: bytes,
        sample_rate: Optional[int] = None,
        cancel=None,
    ) -> None:
        if not pcm:
            return
        import sounddevice as sd  # lazy

        rate = sample_rate or self._config.sample_rate
        duration = len(pcm) / 2 / rate
        # ~100 ms write chunks so a barge-in `cancel` cuts playback off
        # quickly instead of after the whole clip.
        chunk = max(2, (rate // 10) * 2)
        started = time.monotonic()
        with sd.RawOutputStream(
            samplerate=rate,
            device=self._config.output_device,
            channels=1,
            dtype="int16",
        ) as out:
            for offset in range(0, len(pcm), chunk):
                if cancel is not None and cancel.is_set():
                    out.abort()  # drop what's buffered: silence *now*
                    return
                out.write(pcm[offset:offset + chunk])
            # write() returns once frames are *buffered*, and closing an
            # active stream discards whatever hasn't played yet — so without
            # this hold, short sounds (earcons) get cut off and play() returns
            # while audio is still leaving the speaker. The mic is 5 cm away:
            # capture must not arm while we're still audible, or the VAD
            # opens on our own output.
            latency = out.latency if isinstance(out.latency, float) else 0.0
            remaining = started + duration - time.monotonic()
            hold = max(0.0, remaining) + latency
            deadline = time.monotonic() + hold
            while time.monotonic() < deadline:
                if cancel is not None and cancel.is_set():
                    out.abort()
                    return
                time.sleep(min(0.05, hold))
