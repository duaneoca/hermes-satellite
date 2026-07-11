"""Moonshine on-device STT backend.

Implemented against ``moonshine-voice`` 0.1.x (API verified by introspection):

* ``get_model_for_language(lang, ModelArch)`` resolves (and downloads to the
  local cache on first use) the model for ``stt.language``.
* ``Transcriber(model_path, model_arch=arch)`` loads it.
* Batch: ``transcribe_without_streaming(List[float], sample_rate)`` returns a
  ``Transcript`` whose ``lines[].text`` we join.
* Streaming (``stt.streaming``): ``create_stream()`` → ``add_audio()`` per
  captured frame → ``update_transcription()`` for the final transcript.

The model download happens on first run — pre-fetch during provisioning on a
headless Pi (see docs/moonshine.md). ``stt.model`` selects the architecture:
``moonshine/tiny`` or ``moonshine/base`` for batch; streaming variants exist
for tiny/small/medium only (no base-streaming).
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..config import STTConfig
from .base import STTEngine, STTSession

logger = logging.getLogger(__name__)

# Streaming model variants exist for these sizes (verified against the
# moonshine-voice download catalog); notably there is NO base-streaming.
_STREAMING_SIZES = ("tiny", "small", "medium")


class MoonshineSTT(STTEngine):
    def __init__(self, config: STTConfig, sample_rate: int = 16000):
        self._config = config
        self._sample_rate = sample_rate
        self._transcriber = None

    def _arch_name(self) -> str:
        size = self._config.model.rsplit("/", 1)[-1].lower()
        if not self._config.streaming:
            return size.upper()
        if size not in _STREAMING_SIZES:
            raise ValueError(
                f"stt.streaming needs a streaming model variant and "
                f"{self._config.model!r} has none — set stt.model to "
                "moonshine/small (closest to base quality), moonshine/tiny "
                "or moonshine/medium"
            )
        return f"{size.upper()}_STREAMING"

    def _get_transcriber(self):
        if self._transcriber is None:
            import moonshine_voice as mv  # lazy

            arch_name = self._arch_name()
            try:
                wanted_arch = getattr(mv.ModelArch, arch_name)
            except AttributeError:
                raise ValueError(
                    f"Unknown Moonshine model {self._config.model!r}; "
                    "use moonshine/tiny or moonshine/base"
                ) from None

            model_name, arch = mv.get_model_for_language(
                self._config.language, wanted_arch
            )
            # get_model_for_language may already return an absolute path.
            path = Path(model_name)
            if not path.exists():
                path = mv.get_model_path(model_name)
            self._transcriber = mv.Transcriber(str(path), model_arch=arch)
            logger.info("moonshine ready: %s (%s)", path.name, arch)
        return self._transcriber

    def start_session(self):
        if not self._config.streaming:
            return None
        return _MoonshineSession(self._get_transcriber(), self._sample_rate)

    def transcribe(self, audio: bytes) -> str:
        # In streaming mode, batch calls run through a session too, so the
        # wizard's Transcription test exercises (and downloads) the same
        # model variant the daemon uses.
        session = self.start_session()
        if session is not None:
            session.feed(audio)
            return session.finish()
        transcript = self._get_transcriber().transcribe_without_streaming(
            _to_float_samples(audio), sample_rate=self._sample_rate
        )
        return " ".join(line.text for line in transcript.lines).strip()


def _to_float_samples(audio: bytes):
    """int16 PCM -> float samples in [-1.0, 1.0]."""
    try:
        import numpy as np  # moonshine-voice already depends on it

        return (
            np.frombuffer(audio, np.int16).astype(np.float32) / 32768.0
        ).tolist()
    except ImportError:  # pragma: no cover - numpy ships with moonshine
        return [s / 32768.0 for s in memoryview(audio).cast("h")]


# Silence fed before the final decode: with a tight audio.silence_ms the
# capture ends with very little trailing audio, and the incremental decoder
# hasn't settled the last words yet. Field symptom: "...like tomorrow?"
# transcribed as "...going on?" — the tail was dropped or garbled.
_FINISH_PAD_MS = 300


class _MoonshineSession(STTSession):
    """One utterance against a streaming-arch Transcriber.

    Verified by loopback (Piper → 30 ms chunks → small-streaming-en):
    ``add_audio`` keeps up well under real time, and the final
    ``update_transcription`` returns synchronously in ~1 ms — so
    :meth:`finish` costs effectively nothing after the last frame.
    """

    def __init__(self, transcriber, sample_rate: int):
        self._rate = sample_rate
        # "Decode everything buffered NOW": without this flag the final
        # transcript can predate the last ~update_interval of audio —
        # loopback-verified to truncate/garble the end of the utterance.
        self._force_flag = getattr(
            transcriber, "MOONSHINE_FLAG_FORCE_UPDATE", 0)
        self._stream = transcriber.create_stream()
        self._stream.start()

    def feed(self, pcm: bytes) -> None:
        if pcm:
            self._stream.add_audio(
                _to_float_samples(pcm), sample_rate=self._rate
            )

    def finish(self) -> str:
        try:
            pad = [0.0] * (self._rate * _FINISH_PAD_MS // 1000)
            self._stream.add_audio(pad, sample_rate=self._rate)
            transcript = self._stream.update_transcription(self._force_flag)
            return " ".join(
                line.text for line in transcript.lines if line.text
            ).strip()
        finally:
            self.abort()

    def abort(self) -> None:
        try:
            self._stream.stop()
            self._stream.close()
        except Exception:  # already closed / native handle gone
            logger.debug("moonshine stream close failed", exc_info=True)
