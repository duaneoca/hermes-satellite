"""Moonshine on-device STT backend.

Implemented against ``moonshine-voice`` 0.1.x (API verified by introspection):

* ``get_model_for_language(lang, ModelArch)`` resolves (and downloads to the
  local cache on first use) the model for ``stt.language``.
* ``Transcriber(model_path, model_arch=arch)`` loads it.
* ``transcribe_without_streaming(List[float], sample_rate)`` returns a
  ``Transcript`` whose ``lines[].text`` we join.

The model download happens on first run — pre-fetch during provisioning on a
headless Pi (see docs/moonshine.md). ``stt.model`` selects the architecture:
``moonshine/tiny`` or ``moonshine/base``.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..config import STTConfig
from .base import STTEngine

logger = logging.getLogger(__name__)


class MoonshineSTT(STTEngine):
    def __init__(self, config: STTConfig, sample_rate: int = 16000):
        self._config = config
        self._sample_rate = sample_rate
        self._transcriber = None

    def _get_transcriber(self):
        if self._transcriber is None:
            import moonshine_voice as mv  # lazy

            arch_name = self._config.model.rsplit("/", 1)[-1].upper()
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

    def transcribe(self, audio: bytes) -> str:
        transcriber = self._get_transcriber()
        # int16 PCM -> float samples in [-1.0, 1.0].
        try:
            import numpy as np  # moonshine-voice already depends on it

            samples = (
                np.frombuffer(audio, np.int16).astype(np.float32) / 32768.0
            ).tolist()
        except ImportError:  # pragma: no cover - numpy ships with moonshine
            samples = [s / 32768.0 for s in memoryview(audio).cast("h")]
        transcript = transcriber.transcribe_without_streaming(
            samples, sample_rate=self._sample_rate
        )
        return " ".join(line.text for line in transcript.lines).strip()
