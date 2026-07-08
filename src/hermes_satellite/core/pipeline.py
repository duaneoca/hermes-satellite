"""Pipeline orchestration: wire the components to the state machine.

The pipeline runs on the main thread. Each cycle it waits for a wake word,
captures an utterance, transcribes it, sends the text to Hermes, synthesizes the
reply and plays it back — dispatching the corresponding :class:`Event` at each
step so the LED controller (subscribed to state transitions) reflects progress.

Microphone input is gated by a ``is_muted`` callable owned by the app: while
muted, wake detection and capture ignore all audio.
"""

from __future__ import annotations

import logging
import time
from typing import Callable

from ..audio.base import AudioSink, AudioSource
from ..hermes.base import AgentClient
from ..stt.base import STTEngine
from ..tts.base import TTSEngine
from ..wakeword.base import WakeWordDetector
from .events import Event, StateMachine
from .speech_text import make_speakable

logger = logging.getLogger(__name__)


class Pipeline:
    def __init__(
        self,
        *,
        state_machine: StateMachine,
        wakeword: WakeWordDetector,
        audio_source: AudioSource,
        stt: STTEngine,
        hermes: AgentClient,
        tts: TTSEngine,
        audio_sink: AudioSink,
        session_key: str,
        is_muted: Callable[[], bool],
    ):
        self.sm = state_machine
        self.wakeword = wakeword
        self.audio_source = audio_source
        self.stt = stt
        self.hermes = hermes
        self.tts = tts
        self.audio_sink = audio_sink
        self.session_key = session_key
        self.is_muted = is_muted
        self._running = False

    def run_cycle(self) -> None:
        """Run a single wake -> speak cycle."""
        if not self.wakeword.wait_for_wake(self.is_muted):
            return  # stopped or interrupted before a wake word
        self.sm.dispatch(Event.WAKE_DETECTED)

        self.sm.dispatch(Event.RECORDING_STARTED)
        audio = self.audio_source.capture_utterance(self.is_muted)
        if not audio:
            logger.info("no speech captured; returning to idle")
            self.sm.dispatch(Event.RESET)
            return
        self.sm.dispatch(Event.SPEECH_CAPTURED)

        text = self.stt.transcribe(audio)
        logger.info("transcript: %s", text)
        if not text.strip():
            self.sm.dispatch(Event.RESET)
            return
        reply = self.hermes.send(text, self.session_key)
        logger.info("hermes reply: %s", reply)
        self.sm.dispatch(Event.RESPONSE_READY)

        # Defense-in-depth against markdown reaching the speaker: the system
        # prompt asks for plain prose; this flattens whatever slipped through.
        speakable = make_speakable(reply)
        if speakable != reply:
            logger.debug("sanitized reply for speech: %s", speakable)

        pcm = self.tts.synthesize(speakable)
        self.audio_sink.play(pcm, self.tts.sample_rate)
        self.sm.dispatch(Event.PLAYBACK_DONE)

    def run_forever(self) -> None:
        """Loop cycles until :meth:`stop` is called.

        Unexpected errors route the state machine through ERROR and reset back to
        IDLE. A :class:`NotImplementedError` from a stubbed backend stops the loop
        cleanly rather than spinning.
        """
        self._running = True
        while self._running:
            try:
                self.run_cycle()
            except NotImplementedError as exc:
                logger.error("stubbed component not implemented: %s", exc)
                self.sm.dispatch(Event.ERROR)
                break
            except Exception:
                logger.exception("pipeline cycle failed")
                self.sm.dispatch(Event.ERROR)
                time.sleep(1.0)
                self.sm.dispatch(Event.RESET)

    def stop(self) -> None:
        self._running = False
        self.wakeword.stop()
