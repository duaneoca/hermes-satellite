"""Shared microphone input stream.

Porcupine wake detection and VAD-gated capture both consume the same 16 kHz
int16 mono frames, so they share a single :class:`MicStream` (opening and
closing the device between pipeline stages would drop the start of speech).

Built on ``sounddevice`` (PortAudio → ALSA on the Pi). The ReSpeaker seeed
cards often refuse mono capture; set ``audio.input_channels: 2`` and channel 0
(the left mic) is extracted.

``sounddevice`` is imported lazily so this module imports on machines without
PortAudio (tests inject a fake mic instead).
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class MicStream:
    def __init__(
        self,
        sample_rate: int = 16000,
        device: Optional[int] = None,
        channels: int = 1,
    ):
        self._sample_rate = sample_rate
        self._device = device
        self._channels = channels
        self._stream = None

    def start(self) -> None:
        """Open (once) and start the input stream. Safe to call repeatedly."""
        if self._stream is None:
            import sounddevice as sd  # lazy: PortAudio not needed for tests

            self._stream = sd.RawInputStream(
                samplerate=self._sample_rate,
                device=self._device,
                channels=self._channels,
                dtype="int16",
            )
            logger.info(
                "mic stream: %d Hz, %d ch, device=%s",
                self._sample_rate, self._channels,
                "default" if self._device is None else self._device,
            )
        if not self._stream.active:
            self._stream.start()

    def read(self, num_frames: int) -> bytes:
        """Blocking read of ``num_frames`` samples; returns mono int16 bytes."""
        data, overflowed = self._stream.read(num_frames)
        if overflowed:
            logger.debug("mic overflow (%d frames)", num_frames)
        buf = bytes(data)
        if self._channels > 1:
            # Keep channel 0 (left mic) only.
            buf = memoryview(buf).cast("h")[0 :: self._channels].tobytes()
        return buf

    def flush(self) -> None:
        """Discard audio buffered while nobody was reading.

        The pipeline stops reading the mic during PROCESS/SPEAK; without a
        flush, the next wake-word wait would process that stale audio —
        including the assistant's own TTS output, a self-trigger risk.
        """
        if self._stream is None or not self._stream.active:
            return
        available = self._stream.read_available
        while available > 0:
            self._stream.read(available)
            available = self._stream.read_available

    def stop(self) -> None:
        if self._stream is not None and self._stream.active:
            self._stream.stop()

    def close(self) -> None:
        if self._stream is not None:
            try:
                self._stream.close()
            finally:
                self._stream = None
