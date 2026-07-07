"""Application wiring and lifecycle.

Builds every component from config, connects the state machine to the LED
controller and the mute button, and runs the pipeline on the main thread.
"""

from __future__ import annotations

import logging
import signal
import threading

from .audio import build_audio
from .button import Mute, build_button
from .config import Config
from .core.events import StateMachine
from .core.pipeline import Pipeline
from .core.states import State
from .hermes import build_hermes
from .leds import build_led_controller
from .leds.base import LEDState
from .stt import build_stt
from .tts import build_tts
from .wakeword import build_wakeword

logger = logging.getLogger(__name__)

# Pipeline state -> LED state.
_STATE_LEDS = {
    State.IDLE: LEDState.IDLE,
    State.WAKE: LEDState.WAKE,
    State.RECORD: LEDState.RECORDING,
    State.PROCESS: LEDState.PROCESSING,
    State.SPEAK: LEDState.SPEAKING,
    State.ERROR: LEDState.ERROR,
}


class SatelliteApp:
    def __init__(self, config: Config, demo: bool = False):
        self.config = config
        self.demo = demo
        self._stopping = threading.Event()

        # State + LEDs.
        self.sm = StateMachine(initial=State.IDLE)
        self.leds = build_led_controller(config, demo=demo)

        # Mute (owned here; consulted by wakeword/capture via is_muted).
        self.mute = Mute()
        self.button = build_button(config.profile, self.mute.toggle)

        # LED reacts to both pipeline state and mute changes.
        self.sm.subscribe(self._on_transition)
        self.mute.subscribe(self._on_mute)

        # Pipeline components. Wake detection and capture share one mic stream
        # (opening/closing between stages would drop the start of speech).
        self._mic = None
        if not demo:
            from .audio.mic import MicStream

            self._mic = MicStream(
                sample_rate=config.audio.sample_rate,
                device=config.audio.input_device,
                channels=config.audio.input_channels,
            )
        wakeword = build_wakeword(config, demo=demo, mic=self._mic)
        audio_source, audio_sink = build_audio(config, demo=demo, mic=self._mic)
        stt = build_stt(config, demo=demo)
        tts = build_tts(config, demo=demo)
        hermes = build_hermes(config, demo=demo)

        self.pipeline = Pipeline(
            state_machine=self.sm,
            wakeword=wakeword,
            audio_source=audio_source,
            stt=stt,
            hermes=hermes,
            tts=tts,
            audio_sink=audio_sink,
            session_key=config.hermes.session_key,
            is_muted=self.mute.is_muted,
        )

    @classmethod
    def from_config(cls, config: Config, demo: bool = False) -> "SatelliteApp":
        return cls(config, demo=demo)

    # -- LED glue ------------------------------------------------------------
    def _on_transition(self, _old: State, new: State) -> None:
        if not self.mute.is_muted():
            self.leds.set_state(_STATE_LEDS.get(new, LEDState.IDLE))

    def _on_mute(self, muted: bool) -> None:
        if muted:
            self.leds.set_state(LEDState.MUTED)
        else:
            self.leds.set_state(_STATE_LEDS.get(self.sm.state, LEDState.IDLE))

    # -- Lifecycle -----------------------------------------------------------
    def run(self) -> None:
        logger.info(
            "starting hermes-satellite (profile=%s, demo=%s)",
            self.config.hardware_profile, self.demo,
        )
        self._install_signal_handlers()
        self.leds.start()
        self.leds.set_state(LEDState.IDLE)
        self.button.start()
        try:
            self.pipeline.run_forever()
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        if self._stopping.is_set():
            return
        self._stopping.set()
        logger.info("shutting down")
        self.pipeline.stop()
        self.button.stop()
        if self._mic is not None:
            self._mic.close()
        self.leds.stop()

    def _install_signal_handlers(self) -> None:
        def handler(signum, _frame):
            logger.info("received signal %s", signum)
            self.pipeline.stop()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, handler)
            except ValueError:  # pragma: no cover - not on main thread
                pass
