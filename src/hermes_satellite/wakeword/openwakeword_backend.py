"""openWakeWord wake word backend (default engine).

Reads 80 ms frames (1280 samples @ 16 kHz) from the shared MicStream and feeds
them to an ``openwakeword.model.Model``. Detection fires when the model score
reaches ``wakeword.threshold``; every anti-false-trigger lever is config:

* ``patience_frames`` — N consecutive frames above threshold (passed to
  ``predict()``; note upstream forbids combining it with ``debounce_time``, so
  the refractory guard below is implemented here instead).
* ``refractory_seconds`` — ignore detections for a window after one fires.
* ``vad_threshold`` — Silero VAD gate (constructor arg; 0 disables).
* ``noise_suppression`` — SpeexDSP pre-processing (Linux only).
* ``verifier_model_path``/``verifier_threshold`` — second-stage verifier
  trained on the household's own voices (``train_custom_verifier``).

Extra hygiene: the mic is flushed on entry (never process stale audio, e.g.
our own TTS), the model state is reset after mute and after each detection,
and pretrained model files are auto-downloaded on first use.

``on_score`` (callable taking the predictions dict) can be attached for the
``--ww-monitor`` tuning mode.

API verified by introspection of openwakeword 0.6.x: ``Model(wakeword_models,
enable_speex_noise_suppression, vad_threshold, custom_verifier_models,
custom_verifier_threshold, inference_framework)``;
``predict(int16_ndarray, patience={name: n}, threshold={name: t})``.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from ..config import WakeWordConfig
from ..audio.mic import MicStream
from .base import WakeWordDetector

logger = logging.getLogger(__name__)

# openWakeWord operates on 80 ms frames at 16 kHz.
FRAME_SAMPLES = 1280


class OpenWakeWord(WakeWordDetector):
    def __init__(
        self,
        config: WakeWordConfig,
        mic: Optional[MicStream] = None,
        sample_rate: int = 16000,
    ):
        if mic is None:
            raise ValueError("OpenWakeWord requires a shared MicStream")
        if sample_rate != 16000:
            raise RuntimeError(
                f"openWakeWord requires 16000 Hz audio, got {sample_rate}"
            )
        self._config = config
        self._mic = mic
        self._stop_event = threading.Event()
        self._handle = None
        self._last_detection = 0.0
        # Hook for --ww-monitor: called with the raw predictions dict per frame.
        self.on_score: Optional[Callable[[dict], None]] = None

    def _create_model(self):
        from openwakeword.model import Model

        cfg = self._config
        kwargs = dict(
            wakeword_models=[cfg.model_path],
            inference_framework=cfg.inference_framework,
            vad_threshold=cfg.vad_threshold,
            enable_speex_noise_suppression=cfg.noise_suppression,
        )
        if cfg.verifier_model_path:
            # Verifier dict is keyed by the parent wakeword model's name.
            parent = Path(cfg.model_path).stem
            kwargs["custom_verifier_models"] = {parent: cfg.verifier_model_path}
            kwargs["custom_verifier_threshold"] = cfg.verifier_threshold
        return Model(**kwargs)

    def _get_handle(self):
        if self._handle is None:
            try:
                self._handle = self._create_model()
            except Exception:
                # First run: pretrained + shared feature models not yet on
                # disk. Fetch and retry once.
                import openwakeword.utils

                logger.info("downloading openWakeWord model files (first run)")
                names = (
                    [self._config.model_path]
                    if not os.path.exists(self._config.model_path)
                    else []
                )
                openwakeword.utils.download_models(model_names=names)
                self._handle = self._create_model()
            logger.info(
                "openwakeword ready: %s (threshold %.2f, patience %d, "
                "vad %.2f, verifier %s)",
                self._config.model_path,
                self._config.threshold,
                self._config.patience_frames,
                self._config.vad_threshold,
                "yes" if self._config.verifier_model_path else "no",
            )
        return self._handle

    def wait_for_wake(self, is_muted: Callable[[], bool]) -> bool:
        import numpy as np

        handle = self._get_handle()
        cfg = self._config
        predict_kwargs = {}
        if cfg.patience_frames > 1:
            # Keyed by model name; apply to every loaded model (we load one).
            names = list(handle.models.keys())
            predict_kwargs = dict(
                patience={n: cfg.patience_frames for n in names},
                threshold={n: cfg.threshold for n in names},
            )

        self._mic.start()
        self._mic.flush()  # never process stale audio (e.g. our own TTS)
        handle.reset()
        was_muted = False
        while not self._stop_event.is_set():
            pcm = self._mic.read(FRAME_SAMPLES)
            if is_muted():
                was_muted = True
                continue  # drain the device but ignore audio entirely
            if was_muted:
                handle.reset()  # don't fire on stale pre-mute state
                was_muted = False
            predictions = handle.predict(
                np.frombuffer(pcm, dtype=np.int16), **predict_kwargs
            )
            if self.on_score is not None:
                self.on_score(predictions)
            score = max(predictions.values())
            if score >= cfg.threshold:
                if (
                    time.monotonic() - self._last_detection
                    < cfg.refractory_seconds
                ):
                    continue
                self._last_detection = time.monotonic()
                handle.reset()
                logger.info("wake word detected (score %.3f)", score)
                return True
        return False

    def stop(self) -> None:
        self._stop_event.set()
