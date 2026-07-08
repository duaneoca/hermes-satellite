"""Earcons: short synthesized audio cues for pipeline events.

Users can't see the LEDs from across the room, so a brief chime confirms
"I heard you" the moment the wake word fires, and a lower tone signals an
error. Tones are generated in pure Python (no bundled assets, works at any
sample rate) and played through the same :class:`AudioSink` as speech.

Playback is best-effort: an earcon failure must never break the pipeline.
"""

from __future__ import annotations

import logging
import math
from typing import Dict, List, Tuple

from ..config import EarconsConfig

logger = logging.getLogger(__name__)

# Each cue is a sequence of (frequency_hz, duration_ms) notes.
_CUES: Dict[str, List[Tuple[float, int]]] = {
    "wake": [(660, 90), (990, 130)],       # ascending "boo-beep" — listening
    "listening": [(880, 70)],              # soft single blip — follow-up open
    "error": [(400, 130), (300, 190)],     # descending — something went wrong
    "done": [(720, 70)],                   # short confirmation
}

_FADE_MS = 6  # attack/release to avoid clicks


def _render(cue: str, sample_rate: int, volume: float) -> bytes:
    notes = _CUES[cue]
    amp = max(0.0, min(volume, 1.0)) * 32767
    fade = max(1, sample_rate * _FADE_MS // 1000)
    out = bytearray()
    for freq, ms in notes:
        n = sample_rate * ms // 1000
        for i in range(n):
            env = 1.0
            if i < fade:
                env = i / fade
            elif i > n - fade:
                env = max(0.0, (n - i) / fade)
            sample = int(amp * env * math.sin(2 * math.pi * freq * i / sample_rate))
            out += int(sample).to_bytes(2, "little", signed=True)
    return bytes(out)


class Earcons:
    def __init__(self, config: EarconsConfig, sink, sample_rate: int = 16000):
        self._enabled = config.enabled
        self._volume = config.volume
        self._sink = sink
        self._rate = sample_rate
        self._cache: Dict[str, bytes] = {}

    def play(self, cue: str) -> None:
        if not self._enabled or cue not in _CUES:
            return
        pcm = self._cache.get(cue)
        if pcm is None:
            pcm = _render(cue, self._rate, self._volume)
            self._cache[cue] = pcm
        try:
            self._sink.play(pcm, self._rate)
        except Exception:  # cosmetic: never break the pipeline over a chime
            logger.debug("earcon %s playback failed", cue, exc_info=True)
